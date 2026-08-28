#!/usr/bin/env python3
"""three_signals.py <service> <window_start_iso> <window_end_iso> [--out-dir DIR] [--name-suffix S]

从 Jaeger / Prometheus / OpenSearch 三个后端采集针对单个服务的信号，输出一个 JSON。
只用标准库（urllib/json），不装任何包。运行在宿主机，不进容器。

每个查询的原始请求与原始返回落盘到 <out-dir>/<ts>_<service>_<start>.json 备查，
<out-dir> 默认 scripts/out，文件名格式不随 --out-dir 改变。
--name-suffix 在文件名末尾追加一段（同一窗口查两次时区分快照，见决策 016），
默认空、即文件名格式不变。
"""
import json, os, re, subprocess, sys, time, urllib.parse, urllib.request

_IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def _looks_like_ip(v):
    return bool(_IP_RE.match(str(v)))
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
# 前三个是 OTel semconv 的 peer 命名，语言 SDK 都发这几个。
# 后两个是 Envoy 的命名（决策 023 / O-P2-19）：frontend-proxy 是 Envoy，不发
# semconv peer 标签，只发 upstream_cluster —— 而它是**唯一**打到 frontend 的
# 上游。少了这两个键，frontend 的 caller_edges 恒为空、caller_all_dur.p50 恒为
# null，latency-frontend-800 的边判据分子取不到值（批次 2 实测即如此失败）。
# 实测 frontend-proxy 的 client span：upstream_cluster 与 upstream_cluster.name
# 取值都精确等于服务名（frontend / image-provider / flagservice），
# 900s 内 router frontend egress 1662 条 = 1.85/s，p50 5.63ms、p95 28.03ms，
# 800ms 注入是约 140 倍台阶，判据分辨率绰绰有余。
# upstream_address 未纳入：它是 IP:port，不是服务名，配不上 `== svc` 的匹配。
#
# 为何不改成「靶子自身 SERVER span 侧臂」：delay_outbound 按设计只延迟**从服务
# 端口发出的响应包**（原语注释 §3 / 决策 007），netem 排队发生在应用写完响应
# 之后，靶子自己的 server span 量不到这段。四张已通过的 latency 卡实测靶子自身
# p95 位移分别为 +0.3 / +0.0 / +0.0 / +0.0 ms，而调用方 p95 位移 1922~4727ms
# —— server 侧臂在任何门槛下都不会命中，故不设。
PEER_KEYS = ("net.peer.name", "server.address", "peer.service",
             "upstream_cluster.name", "upstream_cluster")

# 但 PEER_KEYS 的取值不保证是服务名 —— 实测 `checkout` 的 **gRPC** client span 把
# peer 解析成了容器 IP，而它的 HTTP 出口给的是名字：
#
#   oteldemo.PaymentService/Charge             server.address=172.18.0.16
#   oteldemo.CartService/GetCart               server.address=172.18.0.23
#   oteldemo.CurrencyService/Convert           server.address=172.18.0.13
#   oteldemo.ProductCatalogService/GetProduct  server.address=172.18.0.17
#   POST（→ shipping / email，HTTP）           server.address=shipping / email
#
# `peer == svc` 的字符串匹配因此对 checkout 的四条 gRPC 边**恒不成立**，这些边在
# 探针里永远是空的 —— `latency-payment-800` 的 caller_all_dur.p50 恒为 null 正是
# 这么来的（F-6 / 决策 027）。已过门的卡走的是别的臂（frontend 的边、靶子侧臂），
# 所以这个洞一直没露出来。
#
# 修法：把容器 IP 反解成服务名后再比。只**新增**匹配、不移除既有匹配，
# 对既有卡是单调的（空边可能变成有边，有边不会变没）。
_IP_TO_SVC = None


def _service_ip_map():
    """{ip: service}，来自 docker inspect，尽力而为。

    docker 本来就是每个原语的硬依赖，这里 shell 出去不引入新依赖。
    失败返回空表，匹配退回今天的「只比名字」行为。
    """
    out = {}
    try:
        names = subprocess.run(["docker", "ps", "--format", "{{.Names}}"],
                               capture_output=True, text=True, timeout=30)
        for name in (names.stdout or "").split():
            r = subprocess.run(
                ["docker", "inspect", name, "--format",
                 "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}"],
                capture_output=True, text=True, timeout=30)
            for ip in (r.stdout or "").split():
                if ip:
                    out[ip] = name
    except Exception:
        return {}
    return out


def peer_names(tags):
    """这个 span 的 peer 标签指向的全部服务名（IP 已反解）。"""
    global _IP_TO_SVC
    if _IP_TO_SVC is None:
        _IP_TO_SVC = _service_ip_map()
    out = set()
    for k in PEER_KEYS:
        v = tags.get(k)
        if not v:
            continue
        v = str(v)
        out.add(v)
        if v in _IP_TO_SVC:
            out.add(_IP_TO_SVC[v])
    return out

# 这些靶子是第三方镜像（PostgreSQL / Valkey），没有 SDK、不产生 server span，
# 因此在 Jaeger 的 /api/services 里也不存在。它们的「调用方边」只能从调用方的
# client span 的 peer 标签认出来 —— 而 PEER_KEYS 匹配本来就是这么做的，
# 唯一要绕开的是 callers 列表里没有它们自己（本来也不该有）。
# 记在这里是为了让「为什么这两个靶子照样有数」这件事有出处（决策 018）。
NO_SERVER_SPAN_TARGETS = ("valkey-cart", "astronomy-db")

# targeting 型开关（productCatalogFailure）只让**一个业务实体**的请求失败，
# 按方法分组看不出来：GetProduct 整体报错率就是那个商品的流量份额（实测约 11%），
# 与「注入没生效」在数字上难以区分。按业务 id 分组后，命中的那一支报错率接近 100%、
# 其余支 0%，判据才有区分度（决策 021 修订 / O-P2-13）。
# 键名形如 demo.<entity>.id，实测有 demo.product.id / demo.order.id /
# demo.shipping.tracking.id；用正则而不是清单，新的实体自动纳入。
BUSINESS_ID_RE = re.compile(r"^demo\..+\.id$")


def collect_traces(base, svc, t0, t1):
    """调用方打给 svc 的报错 span。

    crash 类下被注入服务自己不产生任何 span，所以必须从 caller 侧查：
    取每个其它服务的 trace，筛出「owner != svc 且 peer == svc」的 span。
    """
    us0, us1 = int(t0.timestamp() * 1e6), int(t1.timestamp() * 1e6)
    services, err = fetch(f"{base}/api/services", label="jaeger:services")
    if not services:
        return {"error": err or "no services", "caller_error_spans": None}
    all_svcs = services.get("data") or []
    callers = [s for s in all_svcs if s != svc]
    # 也查 svc 自己：self_edges 需要它的 server span，只靠调用方的 trace 顺带
    # 带出来不可靠（调用方 trace 未被采样时就漏了）。
    query_list = callers + ([svc] if svc in all_svcs else [])

    spans, hit_limit = {}, []
    down = {}                    # 下游边：svc 自己发出的 client span，按 peer 分组
    self_srv = {}                # svc 自己的 server span，按方法分组
    self_by_id = {}              # 同上，但按业务 id 标签分组：{tag_key: {value: {spanID: (sp, tags)}}}
    for caller in query_list:
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
                if owner is None:
                    continue
                if owner == svc:
                    st_o = sp.get("startTime")
                    if st_o is None or not (us0 <= st_o <= us1):
                        continue
                    tg = {t["key"]: t.get("value") for t in sp.get("tags") or []}
                    if tg.get("span.kind") == "server":
                        # svc 自己的 server span：misconfig 类的症状落在这里，
                        # 调用方侧可能一条报错都没有（实测 cartFailure=50% 时
                        # 调用方 0 报错、cart 自身 123 条里 2 条报错，决策 018）。
                        # 分组键按实际标签取：ad 用 rpc.method，cart/email 用
                        # http.route，都没有则退回 operationName。
                        mk = (tg.get("rpc.method") or tg.get("http.route")
                              or sp.get("operationName") or "?")
                        self_srv.setdefault(mk, {})[sp["spanID"]] = (sp, tg)
                        for bk, bv in tg.items():
                            if BUSINESS_ID_RE.match(bk) and bv is not None:
                                (self_by_id.setdefault(bk, {}).setdefault(str(bv), {})
                                 [sp["spanID"]]) = (sp, tg)
                        continue
                    # svc 自己发出的下游调用：latency 类要靠它证明 sport 过滤
                    # 生效（下游没被误伤），见决策 014。
                    if tg.get("span.kind") != "client":
                        continue
                    names = peer_names(tg)
                    # 有服务名就用服务名，没有才退回原始取值（可能是 IP），
                    # 这样下游边按服务分组而不是按地址分组。
                    named = sorted(n for n in names if not _looks_like_ip(n))
                    pr = named[0] if named else next(iter(sorted(names)), None)
                    if not pr or pr == svc:
                        continue
                    down.setdefault(pr, {})[sp["spanID"]] = (sp, tg)
                    continue
                # span 必须自身起始于窗口内。Jaeger 返回的是与窗口相交的整条
                # trace，不过滤会把注入期起始的长 span 算进 after 窗口
                # （实测：一条 71.3s 的 GetCart span 同时出现在 during 和 after）。
                st = sp.get("startTime")
                if st is None or not (us0 <= st <= us1):
                    continue
                tags = {t["key"]: t.get("value") for t in sp.get("tags") or []}
                if svc not in peer_names(tags):
                    continue
                spans[sp["spanID"]] = (sp, tags, owner)

    errs = []
    for sp, tags, owner in spans.values():
        is_err = tags.get("error") is True or str(tags.get("otel.status_code", "")).upper() == "ERROR"
        if is_err:
            errs.append((sp, tags, owner))

    durs = sorted(sp["duration"] / 1000.0 for sp, _, _ in errs)     # us -> ms

    def pct(p):
        """线性插值分位数。

        原来用 round(p/100*(n-1)) 取整下标：n=62 时 round(30.5) 因 Python 的
        banker's rounding 落到 30，得 4589.8ms；而 n//2 落到 31，得 11693.2ms。
        crash 的时延分布是双峰的，两个中位下标恰好一个在低峰尾、一个在高峰头，
        整数下标会静默地二选一。插值后得真中位数 8141.5ms —— 但它落在两峰之间
        的空谷里，本身没有代表性，看分布桶（pct_under_100ms / pct_over_10s）。
        """
        if not durs:
            return None
        k = p / 100.0 * (len(durs) - 1)
        lo, hi = int(k), min(int(k) + 1, len(durs) - 1)
        return round(durs[lo] + (durs[hi] - durs[lo]) * (k - lo), 2)

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

    def edge_stats(items):
        """一条边的 span 数 / 报错数 / 耗时分位。items: [(sp, tags), ...]"""
        ds = sorted(x[0]["duration"] / 1000.0 for x in items)
        ne = sum(1 for _, tg in items
                 if tg.get("error") is True
                 or str(tg.get("otel.status_code", "")).upper() == "ERROR")
        m = len(ds)
        def q(pp):
            if not ds:
                return None
            k = pp / 100.0 * (m - 1)
            lo, hi = int(k), min(int(k) + 1, m - 1)
            return round(ds[lo] + (ds[hi] - ds[lo]) * (k - lo), 2)
        return {"spans": m, "error_spans": ne, "min_ms": round(ds[0], 2) if ds else None,
                "p50_ms": q(50), "p90_ms": q(90), "p95_ms": q(95),
                "max_ms": round(ds[-1], 2) if ds else None}

    caller_items = [(sp, tg) for sp, tg, _ in spans.values()]
    caller_edges = {}
    for sp, tg, own in spans.values():
        caller_edges.setdefault(own, []).append((sp, tg))

    n = len(durs)
    buckets = {
        "pct_under_100ms": round(sum(1 for x in durs if x < 100) / n * 100, 1) if n else None,
        "pct_over_10s": round(sum(1 for x in durs if x > 10000) / n * 100, 1) if n else None,
    }
    first_err_ts = min((sp["startTime"] for sp, _, _ in errs), default=None)
    last_err_ts = max((sp["startTime"] for sp, _, _ in errs), default=None)
    return {
        "caller_error_spans": len(errs),
        "caller_spans_total": len(spans),
        "caller_error_p50_ms": pct(50),
        "caller_error_p95_ms": pct(95),
        "caller_error_dur_buckets": buckets,
        "error_messages_top3": [{"message": m, "count": c} for m, c in msgs.most_common(3)],
        "first_error_span_unix": first_err_ts / 1e6 if first_err_ts else None,
        "last_error_span_unix": last_err_ts / 1e6 if last_err_ts else None,
        "callers_queried": len(callers),
        "trace_limit_per_caller": TRACE_LIMIT_PER_CALLER,
        "callers_hitting_limit": hit_limit,
        # ── 以下为 2026-08-24 新增，供 runner 按 fault_schema §5 判三探针 ──
        # caller_all_dur 是「调用方 → svc 全部 span」的耗时分布（含成功的），
        # 与只统计报错 span 的 caller_error_p* 不同：latency 类全程无报错，
        # 只能靠全部 span 的分位数看右移。
        "caller_all_dur": edge_stats(caller_items),
        "caller_edges": {k: edge_stats(v) for k, v in sorted(caller_edges.items())},
        "downstream_edges": {k: edge_stats(list(v.values()))
                             for k, v in sorted(down.items())},
        # self_edges（2026-08-24 决策 018 第二部分新增）：misconfig 的判据落在
        # server_by_method 上，与 caller_edges 是两回事。
        "self_edges": {
            "server_by_method": {k: edge_stats(list(v.values()))
                                 for k, v in sorted(self_srv.items())},
            "client_by_peer": {k: edge_stats(list(v.values()))
                               for k, v in sorted(down.items())},
            "server_spans_total": sum(len(v) for v in self_srv.values()),
            "server_error_spans": sum(
                1 for v in self_srv.values() for sp, tg in v.values()
                if tg.get("error") is True
                or str(tg.get("otel.status_code", "")).upper() == "ERROR"),
            # targeting 型开关的判据落在这里：按业务 id 分组的自有 server span
            # （决策 021 修订 / O-P2-13）。分组键是标签名，二级键是标签值。
            "server_by_business_id": {
                bk: {bv: edge_stats(list(items.values()))
                     for bv, items in sorted(vals.items())}
                for bk, vals in sorted(self_by_id.items())},
        },
    }


# ── metrics ───────────────────────────────────────────────────────────────
# 选 traces_span_metrics_calls_total：由 collector 的 spanmetrics connector 从 span
# 派生，对全部 16 个 target 一致存在，不依赖各服务自己埋的语言相关指标
# （cart 是 .NET，没有 rpc_server_duration_*；选它才能一套查询覆盖全部靶子）。
METRIC = "traces_span_metrics_calls_total"


def collect_metrics(base, svc, t0, t1):
    """请求速率用计数器差分算，不用 rate()。

    指标粒度由 SDK 导出间隔决定（经 OTLP 推送写入 Prometheus，无 scrape）。
    该间隔原为 60s 时，rate(...[60s]) 在 60 秒窗口里通常只拿得到 1 个样本、
    直接返回空向量（实测 baseline/after 两个窗口都是 None），故改用计数器差分。
    metric_export_interval=15s (SDK, OTLP push; no scrape) 后样本已足，但仍保留
    差分法：它把窗口内实际样本数一并输出，让分辨率不足这件事可见而不是变成 null。
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
        for ts, v in vals:                       # 折叠掉取值未变化的重复点
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
                    f"{int((t1 - t0).total_seconds())}s window at metric_export_interval=15s (SDK, OTLP push; no scrape)")
    elif not err:
        note = "no series in window"

    # 心跳：target_info 由 SDK 的 resource 派生，与请求量无关 —— 服务只要还在
    # 上报遥测它就在，服务一死它就停。对全部靶子一致存在（Go/Java/.NET/Node/
    # Python 都有），不像各语言自己的 runtime 指标那样挑语言。
    # 回看窗口起点前 600s，才能在服务已死的情况下找到"最后一次心跳"。
    # 用 timestamp() 取底层样本的真实时刻，不能用 query_range 的求值时刻：
    # query_range 会把最后一个已知值按 5 分钟 staleness 重复铺到每个 step 点上，
    # 服务已死也照样每步都有值，age 恒为 0，测不出"心跳停了"。
    hb_q = f'timestamp(target_info{{service_name="{svc}"}})'
    url2 = f"{base}/api/v1/query?" + urllib.parse.urlencode(
        {"query": hb_q, "time": t1.timestamp()}
    )
    d2, _ = fetch(url2, label="prom:heartbeat_age")
    hb_age = None
    if d2 and d2.get("status") == "success" and d2["data"]["result"]:
        last_sample = max(float(r["value"][1]) for r in d2["data"]["result"])
        hb_age = round(t1.timestamp() - last_sample, 1)

    # 窗口内真实样本数：对 timestamp() 做 range 查询后数不同取值的个数
    url3 = f"{base}/api/v1/query_range?" + urllib.parse.urlencode(
        {"query": hb_q, "start": t0.timestamp(), "end": t1.timestamp(), "step": 15}
    )
    d3, _ = fetch(url3, label="prom:heartbeat_samples")
    hb_n = 0
    if d3 and d3.get("status") == "success" and d3["data"]["result"]:
        hb_n = len({v for series in d3["data"]["result"] for _, v in series["values"]})
    return {
        "req_rate_per_s": rate,
        "metric_used": METRIC,
        "metric_note": "spanmetrics connector 派生，server-kind span；对全部靶子一致可用",
        "rate_method": "counter delta over window; metric_export_interval=15s (SDK, OTLP push; no scrape)",
        "samples_in_window": n_samples,
        "counter_reset": reset,
        "resolution_note": note,
        "promql": q,
        "heartbeat_metric": "target_info",
        "heartbeat_age_s": hb_age,
        "heartbeat_samples_in_window": hb_n,
        "error": err,
    }


# ── 容器内存（决策 018：mem_leak 类需要，misconfig 类作旁证）──────────────
# 指标名 container_memory_usage_total_bytes，由 collector 的 docker_stats
# receiver 产出（collection_interval 未设，实测 10s 一个点），标签 container_name。
# 选它而不是 container_memory_percent_ratio / _file_bytes：前者是比例、后者只是
# page cache，都不是「用了多少」。_usage_limit_bytes 是上限不是用量。
MEM_METRIC = "container_memory_usage_total_bytes"


def collect_memory(base, svc, t0, t1):
    q = f'{MEM_METRIC}{{container_name="{svc}"}}'
    url = f"{base}/api/v1/query_range?" + urllib.parse.urlencode(
        {"query": q, "start": t0.timestamp(), "end": t1.timestamp(), "step": 10}
    )
    data, err = fetch(url, label="prom:container_memory")
    out = {"metric_used": MEM_METRIC, "first_mib": None, "last_mib": None,
           "max_mib": None, "samples": 0, "error": err}
    if data and data.get("status") == "success" and data["data"]["result"]:
        vals = [float(v) / 1048576.0 for _, v in data["data"]["result"][0]["values"]]
        if vals:
            out.update(first_mib=round(vals[0], 1), last_mib=round(vals[-1], 1),
                       max_mib=round(max(vals), 1), samples=len(vals),
                       growth_mib=round(vals[-1] - vals[0], 1))
    return out


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
    argv = sys.argv[1:]
    out_dir = OUT_DIR
    if "--out-dir" in argv:
        i = argv.index("--out-dir")
        if i + 1 >= len(argv):
            print("error: --out-dir needs a value", file=sys.stderr)
            return 2
        out_dir = argv[i + 1]
        del argv[i:i + 2]
    name_suffix = ""
    if "--name-suffix" in argv:
        i = argv.index("--name-suffix")
        if i + 1 >= len(argv):
            print("error: --name-suffix needs a value", file=sys.stderr)
            return 2
        name_suffix = argv[i + 1]
        del argv[i:i + 2]
    if len(argv) != 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    svc, s0, s1 = argv[0], argv[1], argv[2]
    t0, t1 = iso_to_dt(s0), iso_to_dt(s1)
    env = load_env()

    out = {
        "service": svc,
        "window": {"start": s0, "end": s1, "seconds": (t1 - t0).total_seconds()},
        "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "traces": collect_traces(env["JAEGER_BASE"], svc, t0, t1),
        "metrics": collect_metrics(env["PROM_BASE"], svc, t0, t1),
        "logs": collect_logs(env["OPENSEARCH_BASE"], svc, t0, t1),
        "memory": collect_memory(env["PROM_BASE"], svc, t0, t1),
    }

    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    safe = s0.replace(":", "").replace("-", "")
    path = os.path.join(out_dir, f"{stamp}_{svc}_{safe}{name_suffix}.json")
    with open(path, "w") as f:
        json.dump({"summary": out, "raw": _raw}, f, indent=1)
    out["raw_dump"] = os.path.relpath(path, ROOT)

    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
