#!/usr/bin/env python3
"""three_signals.py <service> <window_start_iso> <window_end_iso>

从 Jaeger / Prometheus / OpenSearch 三个后端采集针对单个服务的信号，输出一个 JSON。
只用标准库（urllib/json），不装任何包。运行在宿主机，不进容器。

每个查询的原始请求与原始返回落盘到 scripts/out/<ts>_<service>_<start>.json 备查。
"""
import json, os, sys, time, urllib.parse, urllib.request
from collections import Counter
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_DIR = os.path.join(ROOT, "out")

# Jaeger 每个 caller 最多取多少条 trace。命中上限会记进输出，不静默截断。
TRACE_LIMIT_PER_CALLER = 2000
HTTP_TIMEOUT = 30

_raw = []   # 原始请求/返回记录


def load_env():
    env = {}
    with open(os.path.join(ROOT, "backends.env")) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def iso_to_dt(s):
    s = s.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fetch(url, data=None, headers=None, label=""):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    if data is not None:
        req.get_method = lambda: "POST"
    rec = {"label": label, "url": url, "body": data.decode() if data else None}
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            payload = r.read().decode()
        rec["status"] = 200
        rec["response"] = json.loads(payload)
    except Exception as e:                       # noqa: BLE001 - 探针不能因单个后端挂掉整体
        rec["status"] = "error"
        rec["response"] = None
        rec["error"] = f"{type(e).__name__}: {e}"
    _raw.append(rec)
    return rec["response"], rec.get("error")


# ── traces ────────────────────────────────────────────────────────────────
PEER_KEYS = ("net.peer.name", "server.address", "peer.service")


def collect_traces(base, svc, t0, t1):
    """调用方打给 svc 的报错 span。

    crash 类下被注入服务自己不产生任何 span，所以必须从 caller 侧查：
    取每个其它服务的 trace，筛出「owner != svc 且 peer == svc」的 span。
    """
    us0, us1 = int(t0.timestamp() * 1e6), int(t1.timestamp() * 1e6)
    services, err = fetch(f"{base}/api/services", label="jaeger:services")
    if not services:
        return {"error": err or "no services", "caller_error_spans": None}
    callers = [s for s in services.get("data") or [] if s != svc]

    spans, hit_limit = {}, []
    for caller in callers:
        q = urllib.parse.urlencode(
            {"service": caller, "start": us0, "end": us1, "limit": TRACE_LIMIT_PER_CALLER}
        )
        data, _ = fetch(f"{base}/api/traces?{q}", label=f"jaeger:traces:{caller}")
        traces = (data or {}).get("data") or []
        if len(traces) >= TRACE_LIMIT_PER_CALLER:
            hit_limit.append(caller)
        for tr in traces:
            procs = {k: v.get("serviceName") for k, v in (tr.get("processes") or {}).items()}
            for sp in tr.get("spans") or []:
                owner = procs.get(sp.get("processID"))
                if owner == svc or owner is None:
                    continue
                # span 必须自身起始于窗口内。Jaeger 返回的是与窗口相交的整条
                # trace，不过滤会把注入期起始的长 span 算进 after 窗口
                # （实测：一条 71.3s 的 GetCart span 同时出现在 during 和 after）。
                st = sp.get("startTime")
                if st is None or not (us0 <= st <= us1):
                    continue
                tags = {t["key"]: t.get("value") for t in sp.get("tags") or []}
                if not any(tags.get(k) == svc for k in PEER_KEYS):
                    continue
                spans[sp["spanID"]] = (sp, tags, owner)

    errs = []
    for sp, tags, owner in spans.values():
        is_err = tags.get("error") is True or str(tags.get("otel.status_code", "")).upper() == "ERROR"
        if is_err:
            errs.append((sp, tags, owner))

    durs = sorted(sp["duration"] / 1000.0 for sp, _, _ in errs)     # us -> ms

    def pct(p):
        if not durs:
            return None
        i = min(int(round(p / 100.0 * (len(durs) - 1))), len(durs) - 1)
        return round(durs[i], 2)

    msgs = Counter()
    for sp, tags, _ in errs:
        m = (tags.get("otel.status_description") or tags.get("grpc.error_message")
             or tags.get("error.message") or tags.get("exception.message")
             or tags.get("grpc.error_name"))
        if not m:
            for log in sp.get("logs") or []:
                lf = {f["key"]: f.get("value") for f in log.get("fields") or []}
                m = lf.get("exception.message") or lf.get("message") or lf.get("event")
                if m:
                    break
        if m:
            msgs[str(m)[:200]] += 1

    first_err_ts = min((sp["startTime"] for sp, _, _ in errs), default=None)
    return {
        "caller_error_spans": len(errs),
        "caller_spans_total": len(spans),
        "caller_error_p50_ms": pct(50),
        "caller_error_p95_ms": pct(95),
        "error_messages_top3": [{"message": m, "count": c} for m, c in msgs.most_common(3)],
        "first_error_span_unix": first_err_ts / 1e6 if first_err_ts else None,
        "callers_queried": len(callers),
        "trace_limit_per_caller": TRACE_LIMIT_PER_CALLER,
        "callers_hitting_limit": hit_limit,
    }


# ── metrics ───────────────────────────────────────────────────────────────
# 选 traces_span_metrics_calls_total：由 collector 的 spanmetrics connector 从 span
# 派生，对全部 16 个 target 一致存在，不依赖各服务自己埋的语言相关指标
# （cart 是 .NET，没有 rpc_server_duration_*；选它才能一套查询覆盖全部靶子）。
METRIC = "traces_span_metrics_calls_total"


def collect_metrics(base, svc, t0, t1):
    """请求速率用计数器差分算，不用 rate()。

    Prometheus 的 scrape_interval 是 1m，rate(...[60s]) 在 60 秒窗口里通常只
    拿得到 1 个样本，直接返回空向量（实测 baseline/after 两个窗口都是 None）。
    改成在窗口两端取计数器值做差分，得到窗口内真实平均速率，并把窗口内实际
    样本数一并输出，让分辨率不足这件事可见而不是变成一个 null。
    """
    q = f'sum({METRIC}{{service_name="{svc}",span_kind="SPAN_KIND_SERVER"}})'
    url = f"{base}/api/v1/query_range?" + urllib.parse.urlencode(
        {"query": q, "start": t0.timestamp(), "end": t1.timestamp(), "step": 15}
    )
    data, err = fetch(url, label="prom:req_rate_range")
    rate, n_samples, note, reset = None, 0, None, False
    if data and data.get("status") == "success" and data["data"]["result"]:
        vals = [(float(ts), float(v)) for ts, v in data["data"]["result"][0]["values"]]
        uniq = []
        for ts, v in vals:                       # 折叠掉同一次 scrape 的重复点
            if not uniq or v != uniq[-1][1]:
                uniq.append((ts, v))
        n_samples = len(uniq)
        if n_samples >= 2:
            (ta, va), (tb, vb) = uniq[0], uniq[-1]
            if vb < va:                          # 容器重启导致计数器归零
                reset, rate = True, round(vb / (tb - ta), 4)
                note = "counter reset detected (target restarted); rate is post-reset approximation"
            else:
                rate = round((vb - va) / (tb - ta), 4)
        else:
            note = (f"insufficient samples ({n_samples}) in a "
                    f"{int((t1 - t0).total_seconds())}s window at scrape_interval=60s")
    elif not err:
        note = "no series in window"

    url2 = f"{base}/api/v1/query?" + urllib.parse.urlencode(
        {"query": f'target_info{{service_name="{svc}"}}', "time": t1.timestamp()}
    )
    d2, _ = fetch(url2, label="prom:target_info")
    present = bool(d2 and d2.get("status") == "success" and d2["data"]["result"])
    return {
        "req_rate_per_s": rate,
        "metric_used": METRIC,
        "metric_note": "spanmetrics connector 派生，server-kind span；对全部靶子一致可用",
        "rate_method": "counter delta over window (rate() unusable at scrape_interval=60s)",
        "samples_in_window": n_samples,
        "counter_reset": reset,
        "resolution_note": note,
        "promql": q,
        "target_info_present": present,
        "error": err,
    }


# ── logs ──────────────────────────────────────────────────────────────────
def collect_logs(base, svc, t0, t1):
    body = json.dumps({
        "query": {"bool": {"filter": [
            {"term": {"resource.service.name": svc}},
            {"range": {"observedTimestamp": {
                "gte": t0.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "lte": t1.strftime("%Y-%m-%dT%H:%M:%S.999Z"),
            }}},
        ]}}
    }).encode()
    data, err = fetch(f"{base}/otel-logs-*/_count", data=body,
                      headers={"Content-Type": "application/json"}, label="opensearch:count")
    return {
        "log_lines": (data or {}).get("count"),
        "field_used": "resource.service.name",
        "error": err,
    }


def main():
    if len(sys.argv) != 4:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    svc, s0, s1 = sys.argv[1], sys.argv[2], sys.argv[3]
    t0, t1 = iso_to_dt(s0), iso_to_dt(s1)
    env = load_env()

    out = {
        "service": svc,
        "window": {"start": s0, "end": s1, "seconds": (t1 - t0).total_seconds()},
        "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "traces": collect_traces(env["JAEGER_BASE"], svc, t0, t1),
        "metrics": collect_metrics(env["PROM_BASE"], svc, t0, t1),
        "logs": collect_logs(env["OPENSEARCH_BASE"], svc, t0, t1),
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    safe = s0.replace(":", "").replace("-", "")
    path = os.path.join(OUT_DIR, f"{stamp}_{svc}_{safe}.json")
    with open(path, "w") as f:
        json.dump({"summary": out, "raw": _raw}, f, indent=1)
    out["raw_dump"] = os.path.relpath(path, ROOT)

    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
