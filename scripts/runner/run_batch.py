#!/usr/bin/env python3
"""run_batch.py —— 注入周期批次 runner（docs/workflow.md §5、§7）

串行执行一份周期清单：每个周期走 workflow.md §2 的五段流程，按
docs/fault_schema.md §5 判三探针，落盘每周期产物与批次汇总。

只用标准库。运行在宿主机，不进容器；本身不 daemon 化 —— 无人值守用
scripts/runner/run_batch.sh 包 nohup。

判定只在 runner 侧发生：agent 永远看不到本文件产出的 probes.json 与
anchors.json（泄漏隔离，见 fault_schema §4 与决策 015）。
"""
import argparse, json, math, os, subprocess, sys, time
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # scripts/
REPO = os.path.dirname(ROOT)
PRIMITIVES = os.path.join(ROOT, "primitives")
PROBE = os.path.join(ROOT, "probes", "three_signals.py")
STATE_DIR = os.path.join(ROOT, "state")
LOCK = os.path.join(STATE_DIR, "runner.lock")

# 档位时间表（秒）。来源：docs/workflow.md §3「周期双档」。
TIERS = {
    "debug": {"pre": 30, "inject": 60, "post": 30},
    "full":  {"pre": 60, "inject": 120, "post": 60},
}

# recovered 判定窗自 t_revert + RECOVER_SKIP_S 起算。来源：fault_schema §5。
RECOVER_SKIP_S = 30

# 三探针的观测点 = t_end + SETTLE_S，三个窗口统一在该时刻查询（决策 016）。
# span 只在结束时才导出，注入窗内拨出的报错调用多在窗口结束后才结束 ——
# 窗口终点即刻查询会看到 0 条（实测：即刻 0/0，5 分钟后重查同一窗口 55/55），
# 把「终将吵」的 crash 误判成「哑」的 blackhole。
#
# 150s 的来源：
#   - Linux tcp_syn_retries=6 → 建连重试预算 1+2+4+8+16+32+64 = 127s；
#   - 实测报错 span p95 131.9s、max 134.9s（2026-08-24 入库档 crash 周期）；
#   - 加导出落库余量约 20s。
SETTLE_S_DEFAULT = 150

# symptom 阈值，逐条抄自 fault_schema §5，改阈值请先改 §5。
# §5 crash：N 由固定 20 改为按靶子基线流量算的相对阈值（决策 018）。
# N = max(5, ceil(0.25 x baseline_rate_per_s x inject_s))
# 固定 20 只对 cart（高流量）成立：实测 email / payment / checkout 的被调速率
# 约 4.4/min，120 秒注入窗内总共才约 9 次调用，永远达不到 20。
CRASH_N_FLOOR = 5
CRASH_N_FRAC = 0.25
BLACKHOLE_SPAN_FRAC = 0.10        # §5: caller_spans_total 低于基线 10%
LATENCY_SHIFT_FRAC = 0.80         # §5: 耗时分布右移 ≥ delay_ms × 0.8
# §5 未给各类 recovered 的统一数值判据，此处按"symptom 判定为假 + caller span
# 数回到基线 50% 以上"实现，待 ⑤ 定稿后回填 §5。
RECOVER_SPAN_FRAC = 0.50

PRIMS = {"kill_container", "drop_inbound", "delay_outbound", "set_flag"}
CLASS_OF = {"kill_container": "crash", "drop_inbound": "blackhole",
            "delay_outbound": "latency"}
# set_flag 的类别取决于 flag：走 flagd 通道，同一原语可注 misconfig 或 mem_leak。
# 归属来源：2026-08-24 对各服务代码中 flag 判断处的逐个复核（决策 018）。
FLAG_CLASS = {
    "emailMemoryLeak": "mem_leak",
    "recommendationCacheFailure": "mem_leak",   # 复核为泄漏而非配置错：见 018
    "cartFailure": "misconfig", "adFailure": "misconfig",
    "paymentFailure": "misconfig", "productCatalogFailure": "misconfig",
    "paymentUnreachable": "misconfig", "failedReadinessProbe": "misconfig",
    "adHighCpu": "misconfig", "adManualGc": "misconfig",
    "imageSlowLoad": "misconfig", "intlShippingSlowdown": "misconfig",
    "kafkaQueueProblems": "misconfig",
}


def now(): return datetime.now(timezone.utc)
def iso(dt): return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
def log(m): print(f"[{iso(now())}] {m}", flush=True)


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def sh(cmd):
    """执行并返回 (rc, stdout, stderr)，不抛异常。"""
    p = run(cmd)
    return p.returncode, p.stdout, p.stderr


def parse_kv(stdout):
    """原语的 key=value 输出解析成 dict（t_inject=… service=… injected=…）。"""
    kv = {}
    for line in stdout.splitlines():
        for tok in line.split():
            if "=" in tok and not tok.startswith("-"):
                k, v = tok.split("=", 1)
                kv.setdefault(k, v)
    return kv


def probe_signals(svc, t0, t1, out_dir, suffix=""):
    """suffix 只用于把同一窗口的两次快照的 raw 转储分开落盘（决策 016 双快照）。"""
    cmd = [sys.executable, PROBE, svc, iso(t0), iso(t1), "--out-dir", out_dir]
    if suffix:
        cmd += ["--name-suffix", suffix]
    rc, so, se = sh(cmd)
    if rc != 0:
        return None, f"three_signals rc={rc}: {se.strip()[:300]}"
    try:
        return json.loads(so), None
    except json.JSONDecodeError as e:
        return None, f"three_signals bad json: {e}"


# ── 残留核对：每类各自的方式，runner 不复用单一手法 ────────────────────────
def residue_clean(prim, svc, param=None):
    if prim == "set_flag":
        demo = os.path.join(os.path.dirname(REPO), "opentelemetry-demo")
        rc, so, _ = sh(["git", "-C", demo, "status", "--short", "src/flagd/"])
        dirty = so.strip()
        left = [f for f in os.listdir(STATE_DIR) if f.startswith(f"{svc}.flag")]
        return (not dirty and not left), f"flagd json dirty={bool(dirty)} state_left={left}"
    if prim == "kill_container":
        rc, so, _ = sh(["docker", "inspect", svc, "--format", "{{.State.Status}}"])
        return (rc == 0 and so.strip() == "running"), f"container status={so.strip()}"
    rc, so, _ = sh(["docker", "inspect", svc, "--format", "{{.State.Pid}}"])
    pid = so.strip()
    if rc != 0 or pid in ("", "0"):
        return False, f"cannot get pid ({so.strip()})"
    if prim == "drop_inbound":
        rc, so, _ = sh(["sudo", "-n", "nsenter", "-t", pid, "-n", "iptables", "-S", "INPUT"])
        bad = [l for l in so.splitlines() if "DROP" in l]
        return (not bad), f"iptables INPUT DROP rules={len(bad)}"
    rc, so, _ = sh(["sudo", "-n", "nsenter", "-t", pid, "-n", "tc", "qdisc", "show"])
    bad = [l for l in so.splitlines() if "netem" in l]
    return (not bad), f"tc netem qdiscs={len(bad)}"


# ── crash 证据钩子（只记录不判定，决策 016 附注 / O-P2-5）────────────────
def _pid_of(svc):
    rc, so, _ = sh(["docker", "inspect", svc, "--format", "{{.State.Pid}}"])
    return so.strip() if rc == 0 else ""


def _ip_of(svc):
    rc, so, _ = sh(["docker", "inspect", svc, "--format",
                    "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"])
    return so.strip() if rc == 0 else ""


def neigh_snapshot(caller_svc, target_ip):
    """在 caller 的 netns 内看 target 旧 IP 的邻居缓存状态。"""
    pid = _pid_of(caller_svc)
    if not pid or pid == "0":
        return {"error": f"no pid for {caller_svc}"}
    rc, so, se = sh(["sudo", "-n", "nsenter", "-t", pid, "-n", "ip", "neigh", "show"])
    if rc != 0:
        return {"error": se.strip()[:200]}
    line = next((l for l in so.splitlines() if l.split()[:1] == [target_ip]), None)
    return {"ts": iso(now()), "target_ip": target_ip,
            "entry": line, "state": (line.split()[-1] if line else "ABSENT")}


def tcp_syn_retries(caller_svc):
    pid = _pid_of(caller_svc)
    if not pid or pid == "0":
        return None
    rc, so, _ = sh(["sudo", "-n", "nsenter", "-t", pid, "-n",
                    "sysctl", "-n", "net.ipv4.tcp_syn_retries"])
    return so.strip() if rc == 0 else None


# ── 三探针判定，逐类按 fault_schema §5 ────────────────────────────────────
# 各类 symptom 读哪一份注入窗快照（决策 016）。
# blackhole 的静音是**机制性**的：DROP 生效期间已建连接卡在重传、不发新 SYN，
# 所以「哑」只在 immediate（t_revert 即刻）成立；撤除后积压请求同一秒全部完成
# 并回放，harvest 反而看到比基线还多的 span。
# crash 的报错要等 127s 建连预算耗尽才集中出现，immediate 看到 0 条。
SYMPTOM_SNAPSHOT = {"crash": "harvest", "blackhole": "immediate", "latency": "harvest",
                    "misconfig": "harvest", "mem_leak": "harvest"}


def judge_symptom(cls, base, during, param):
    """返回 (pass: bool, detail: dict)。base/during 是 three_signals 的 summary。

    调用方需按 SYMPTOM_SNAPSHOT[cls] 传入对应的注入窗快照。
    """
    bt, dt_ = base["traces"], during["traces"]
    if cls == "crash":
        got = dt_.get("caller_error_spans") or 0
        bsec = base["window"]["seconds"] or 1
        brate = (bt.get("caller_spans_total") or 0) / bsec
        inject_s = dt_["window"]["seconds"] if "window" in dt_ else during["window"]["seconds"]
        n = max(CRASH_N_FLOOR, math.ceil(CRASH_N_FRAC * brate * inject_s))
        return got > n, {
            "snapshot": SYMPTOM_SNAPSHOT[cls],
            "rule": f"caller_error_spans > N, N = max({CRASH_N_FLOOR}, "
                    f"ceil({CRASH_N_FRAC} x baseline_rate x inject_s)) (§5 / 决策 018)",
            "N": n, "baseline_rate_per_s": round(brate, 4), "inject_s": inject_s,
            "during_error_spans": got, "baseline_error_spans": bt.get("caller_error_spans")}
    if cls == "blackhole":
        b = bt.get("caller_spans_total") or 0
        d = dt_.get("caller_spans_total") or 0
        thr = b * BLACKHOLE_SPAN_FRAC
        return (b > 0 and d < thr), {
            "snapshot": SYMPTOM_SNAPSHOT[cls],
            "rule": f"caller_spans_total < baseline x {BLACKHOLE_SPAN_FRAC} (§5)",
            "baseline_spans": b, "during_spans": d, "threshold": round(thr, 2)}
    if cls in ("misconfig", "mem_leak"):
        # 判据待决策 018 后半（本轮只落原始数字，不裁决）
        return None, {"snapshot": SYMPTOM_SNAPSHOT.get(cls, "harvest"),
                      "rule": "待定：misconfig / mem_leak 的 symptom 规则待决策 018 后半",
                      "baseline": {"spans": bt.get("caller_spans_total"),
                                   "error_spans": bt.get("caller_error_spans"),
                                   "p50_ms": (bt.get("caller_all_dur") or {}).get("p50_ms")},
                      "during": {"spans": dt_.get("caller_spans_total"),
                                 "error_spans": dt_.get("caller_error_spans"),
                                 "p50_ms": (dt_.get("caller_all_dur") or {}).get("p50_ms")},
                      "baseline_memory": base.get("memory"),
                      "during_memory": during.get("memory")}

    # latency
    bp = (bt.get("caller_all_dur") or {}).get("p50_ms")
    dp = (dt_.get("caller_all_dur") or {}).get("p50_ms")
    delay = int(param or 800)
    need = delay * LATENCY_SHIFT_FRAC
    shift = (dp - bp) if (bp is not None and dp is not None) else None
    err_ok = (dt_.get("caller_error_spans") or 0) <= (bt.get("caller_error_spans") or 0)
    ok = shift is not None and shift >= need and err_ok
    return ok, {"snapshot": SYMPTOM_SNAPSHOT[cls],
                "rule": f"p50 shift >= delay x {LATENCY_SHIFT_FRAC} and errors not up (§5)",
                "baseline_p50_ms": bp, "during_p50_ms": dp,
                "shift_ms": round(shift, 2) if shift is not None else None,
                "required_shift_ms": need, "errors_not_up": err_ok,
                "downstream_edges_during": (dt_.get("downstream_edges") or {})}


def judge_recovered(cls, base, after, param):
    """按**每秒速率**比较，不比原始条数。

    基线窗是 pre 秒（入库档 60s），恢复窗是 [t_revert+30s, t_end] 只有 30s ——
    直接比条数等于拿 60 秒的量和 30 秒的量对撞，恢复正常也会判失败
    （实测 crash 18 vs 53、blackhole 32 vs 66，两次都是窗长差造成的假失败）。
    """
    sym_still, sd = judge_symptom(cls, base, after, param)
    if sym_still is None:
        return None, {"rule": "待定：该类 symptom 规则未定，recovered 同样待定",
                      "symptom_detail": sd}
    bs = (base["traces"].get("caller_spans_total") or 0)
    as_ = (after["traces"].get("caller_spans_total") or 0)
    bsec = base["window"]["seconds"] or 1
    asec = after["window"]["seconds"] or 1
    brate, arate = bs / bsec, as_ / asec
    span_back = brate > 0 and arate >= brate * RECOVER_SPAN_FRAC
    return (not sym_still) and span_back, {
        "rule": f"symptom false in recover window AND caller span RATE >= baseline rate x {RECOVER_SPAN_FRAC}"
                " (§5 未给数值判据，此处为 runner 实现，待 ⑤ 定稿回填)",
        "symptom_still_true": sym_still, "symptom_detail": sd,
        "baseline_window_s": bsec, "baseline_spans": bs, "baseline_rate_per_s": round(brate, 4),
        "after_window_s": asec, "after_spans": as_, "after_rate_per_s": round(arate, 4),
        "required_rate_per_s": round(brate * RECOVER_SPAN_FRAC, 4)}


def sleep_until(target):
    while True:
        left = (target - now()).total_seconds()
        if left <= 0:
            return
        time.sleep(min(left, 5))


def git_head(path):
    rc, so, _ = sh(["git", "-C", path, "rev-parse", "--short", "HEAD"])
    return so.strip() if rc == 0 else "unknown"


def run_cycle(idx, prim, svc, tier, param, batch_dir, settle_s):
    cls = (FLAG_CLASS[param.split("=", 1)[0]] if prim == "set_flag" else CLASS_OF[prim])
    tm = TIERS[tier]
    cdir = os.path.join(batch_dir, f"{idx:02d}_{prim}_{svc}")
    os.makedirs(cdir, exist_ok=True)
    script = os.path.join(PRIMITIVES, f"{prim}.sh")
    if prim == "set_flag":
        pargs = [svc, str(param)]
    elif prim == "delay_outbound" and param:
        pargs = [svc, str(param)]
    else:
        pargs = [svc]

    res = {"idx": idx, "primitive": prim, "service": svc, "class": cls,
           "tier": tier, "param": param,
           "injected": None, "symptom": None, "recovered": None,
           "residue_clean": None, "aborted": None, "notes": []}

    t0 = now()
    log(f"cycle {idx} {prim}/{svc} tier={tier} param={param} t0={iso(t0)}")

    # 证据钩子（只 kill_container，只记录不判定）
    evidence = None
    if prim == "kill_container":
        evidence = {"caller": "frontend", "target": svc,
                    "target_ip_before": _ip_of(svc),
                    "caller_tcp_syn_retries": tcp_syn_retries("frontend"),
                    "neigh": []}

    # ── 稳定期（窗口起止照记，查询推迟到 t_end+settle）──
    sleep_until(t0 + timedelta(seconds=tm["pre"]))
    base_win = (t0, now())

    # ── apply ──
    rc, so, se = sh([script, "apply"] + pargs)
    if rc != 0:
        res["aborted"] = f"apply rc={rc}: {se.strip()[:300]}"
        sh([script, "revert"] + pargs)
        return res, None
    kv = parse_kv(so)
    t_apply = now()

    # ── 注入期中段 probe（injected）；沿途抓三次邻居缓存 ──
    for off in (10, 40, 80):
        if off >= tm["inject"]:
            break
        sleep_until(t_apply + timedelta(seconds=off))
        if evidence is not None:
            evidence["neigh"].append(neigh_snapshot("frontend",
                                                    evidence["target_ip_before"]))
    sleep_until(t_apply + timedelta(seconds=max(tm["inject"] // 2, 80)))
    rc, so, se = sh([script, "probe"] + pargs)
    res["injected"] = (parse_kv(so).get("injected") == "true")
    if not res["injected"]:
        # injected 失败 = 无效注入，立刻 revert 并中止批次（§5 失败即停）
        sh([script, "revert"] + pargs)
        res["aborted"] = "injected=false at mid-inject; batch aborted"
        return res, None

    # ── 注入窗（只记窗口起止）──
    sleep_until(t_apply + timedelta(seconds=tm["inject"]))
    during_win = (t_apply, now())

    # ── revert ──
    rc, so, se = sh([script, "revert"] + pargs)
    t_revert = now()
    if rc != 0:
        res["aborted"] = f"revert rc={rc}: {se.strip()[:300]}"
        return res, None

    # ── 注入窗快照①：immediate（t_revert 即刻）──
    # blackhole 的静音只在这一刻可见，撤除后积压请求就会回放填满注入窗。
    t_q_imm = now()
    during_imm, err = probe_signals(svc, during_win[0], during_win[1], cdir,
                                    suffix="_immediate")
    if err:
        res["aborted"] = f"during(immediate) probe failed: {err}"
        return res, None
    json.dump(during_imm,
              open(os.path.join(cdir, "window_during_immediate.json"), "w"), indent=1)

    if evidence is not None:
        evidence["target_ip_after"] = _ip_of(svc)
    rc, so, _ = sh([script, "probe"] + pargs)
    reverted = (parse_kv(so).get("injected") == "false")
    clean, cdetail = residue_clean(prim, svc, param)
    res["residue_clean"] = bool(reverted and clean)
    res["notes"].append(f"probe_after_revert injected={'false' if reverted else 'true'}; {cdetail}")

    # ── 恢复期 → 恢复窗 [t_revert+30s, t_end] ──
    t_end = t_revert + timedelta(seconds=tm["post"])
    rec_start = t_revert + timedelta(seconds=RECOVER_SKIP_S)
    sleep_until(t_end)
    after_win = None if rec_start >= t_end else (rec_start, t_end)
    if after_win is None:
        res["notes"].append(
            f"recover window empty: t_revert+{RECOVER_SKIP_S}s >= t_end (post={tm['post']}s); "
            "workflow.md §3 已记：调试档结构上无法评估 recovered")

    # ── 统一收割：三个窗口一律在 t_end + settle_s 查询（决策 016）──
    t_harvest = t_end + timedelta(seconds=settle_s)
    if settle_s > 0:
        log(f"cycle {idx} settle {settle_s}s -> harvest at {iso(t_harvest)}")
        sleep_until(t_harvest)
    t_harvest = now()

    base, err = probe_signals(svc, base_win[0], base_win[1], cdir)
    if err:
        res["aborted"] = f"baseline probe failed: {err}"
        return res, None
    json.dump(base, open(os.path.join(cdir, "window_baseline.json"), "w"), indent=1)

    # ── 注入窗快照②：harvest（t_end+settle）──
    t_q_harv = now()
    during_harv, err = probe_signals(svc, during_win[0], during_win[1], cdir,
                                     suffix="_harvest")
    if err:
        res["aborted"] = f"during(harvest) probe failed: {err}"
        return res, None
    json.dump(during_harv,
              open(os.path.join(cdir, "window_during_harvest.json"), "w"), indent=1)

    after = None
    if after_win is not None:
        after, err = probe_signals(svc, after_win[0], after_win[1], cdir)
        if err:
            res["aborted"] = f"after probe failed: {err}"
            return res, None
        json.dump(after, open(os.path.join(cdir, "window_after.json"), "w"), indent=1)
    else:
        res["recovered"] = None

    # ── 判定 ──
    # probe_signals 返回的就是 three_signals 打到 stdout 的 summary 本体
    snap = {"immediate": during_imm, "harvest": during_harv}[SYMPTOM_SNAPSHOT[cls]]
    sym_ok, sym_d = judge_symptom(cls, base, snap, param)
    res["symptom"] = sym_ok
    def snap_nums(d):
        t = d["traces"]
        return {"spans": t.get("caller_spans_total"),
                "error_spans": t.get("caller_error_spans"),
                "p50_ms": (t.get("caller_all_dur") or {}).get("p50_ms")}

    imm_n, harv_n = snap_nums(during_imm), snap_nums(during_harv)
    in_flight = (harv_n["spans"] or 0) - (imm_n["spans"] or 0)
    res["in_flight_at_revert"] = in_flight
    probes = {"injected": {"pass": res["injected"], "detail": "primitive probe injected=true"},
              "symptom": {"pass": sym_ok, "detail": sym_d},
              "inject_immediate": imm_n,
              "inject_harvest": harv_n,
              # 撤除时仍在飞行中的调用数：harvest 比 immediate 多出来的 span，
              # 即注入期拨出、撤除后才结束的那些。
              "in_flight_at_revert": in_flight,
              "t_query_immediate": iso(t_q_imm),
              "t_query_harvest": iso(t_q_harv)}
    if after is not None:
        rec_ok, rec_d = judge_recovered(cls, base, after, param)
        res["recovered"] = rec_ok
        probes["recovered"] = {"pass": rec_ok, "detail": rec_d}
    else:
        probes["recovered"] = {"pass": None, "detail": "recover window empty (debug tier)"}

    anchors = {"t0": iso(t0), "t_apply": iso(t_apply), "t_revert": iso(t_revert),
               "t_end": iso(t_end), "settle_s": settle_s, "t_harvest": iso(t_harvest),
               "tier": tier, "primitive": prim, "service": svc,
               "param": param, "class": cls,
               "testbed": {"repo": "opentelemetry-demo", "tag": "3.0.0",
                           "branch": "p2-baseline",
                           "commit": git_head(os.path.join(os.path.dirname(REPO),
                                                           "opentelemetry-demo"))},
               "p2_rca_commit": git_head(REPO)}
    json.dump(anchors, open(os.path.join(cdir, "anchors.json"), "w"), indent=1)
    json.dump(probes, open(os.path.join(cdir, "probes.json"), "w"), indent=1)
    if evidence is not None:
        json.dump(evidence, open(os.path.join(cdir, "evidence.json"), "w"), indent=1)
    res["probes"] = probes
    log(f"cycle {idx} done injected={res['injected']} symptom={res['symptom']} "
        f"recovered={res['recovered']} residue_clean={res['residue_clean']}")
    return res, probes


def parse_cycles(path):
    out = []
    for ln in open(path):
        ln = ln.split("#", 1)[0].strip()
        if not ln:
            continue
        parts = ln.split()
        if len(parts) < 3:
            raise SystemExit(f"bad cycle line: {ln!r}")
        prim, svc, tier = parts[0], parts[1], parts[2]
        if prim not in PRIMS:
            raise SystemExit(f"unknown primitive {prim!r}")
        if tier not in TIERS:
            raise SystemExit(f"unknown tier {tier!r}")
        if prim == "set_flag":
            if len(parts) < 4 or "=" not in parts[3]:
                raise SystemExit(f"set_flag 需要第四列 <flag>=<variant>: {ln!r}")
            param = parts[3]
            fk = param.split("=", 1)[0]
            if fk not in FLAG_CLASS:
                raise SystemExit(f"unknown flag {fk!r}; 不在 FLAG_CLASS 表中")
        else:
            param = parts[3] if len(parts) > 3 else ("800" if prim == "delay_outbound" else None)
        out.append((prim, svc, tier, param))
    return out


def write_summary(batch_dir, batch_id, rows, aborted):
    json.dump({"batch_id": batch_id, "cycles": rows, "aborted": aborted},
              open(os.path.join(batch_dir, "summary.json"), "w"), indent=1)
    L = [f"# 批次汇总 {batch_id}", ""]
    if aborted:
        L += [f"**批次中止**：{aborted}", ""]
    L += ["| # | 原语 | 靶子 | 档 | injected | symptom | recovered | 无残留 | 关键数字 |",
          "| --- | --- | --- | --- | :---: | :---: | :---: | :---: | --- |"]
    def mark(v):
        return "待定" if v is None else ("通过" if v else "**失败**")
    npass = nfail = nred = npend = 0
    for r in rows:
        d = ((r.get("probes") or {}).get("symptom") or {}).get("detail") or {}
        if r["class"] == "crash":
            key = f"报错 span {d.get('during_error_spans')}（阈值 N={d.get('N')}）"
        elif r["class"] == "blackhole":
            key = f"caller span {d.get('during_spans')} / 基线 {d.get('baseline_spans')}"
        elif r["class"] == "latency":
            key = f"p50 右移 {d.get('shift_ms')}ms（需 ≥{d.get('required_shift_ms')}）"
        else:
            dm = d.get("during_memory") or {}
            key = (f"待定｜spans {(d.get('during') or {}).get('spans')} "
                   f"err {(d.get('during') or {}).get('error_spans')} "
                   f"mem {dm.get('first_mib')}→{dm.get('last_mib')} MiB")
        if r.get("aborted"):
            key = r["aborted"]
        L.append(f"| {r['idx']} | `{r['primitive']}` | `{r['service']}` | {r['tier']} | "
                 f"{mark(r['injected'])} | {mark(r['symptom'])} | {mark(r['recovered'])} | "
                 f"{mark(r['residue_clean'])} | {key} |")
        if r["recovered"] is False:
            nred += 1
        if r["symptom"] is None:
            npend += 1            # 判据待定的类别不计入通过/失败
        elif r["injected"] and r["symptom"] and r["recovered"] is not False:
            npass += 1
        else:
            nfail += 1
    L += ["", f"通过 {npass}　失败 {nfail}　判据待定 {npend}　recovered 标红 {nred}　"
              f"共 {len(rows)} 周期"]
    open(os.path.join(batch_dir, "summary.md"), "w").write("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", required=True)
    ap.add_argument("--batch-id", default=None)
    ap.add_argument("--abort-after-recovered-failures", type=int, default=2)
    ap.add_argument("--out-root", default=os.path.join(ROOT, "out"))
    ap.add_argument("--settle-s", type=int, default=SETTLE_S_DEFAULT,
                    help="三个窗口统一推迟到 t_end+settle 查询（决策 016），默认 150")
    a = ap.parse_args()

    cycles = parse_cycles(a.cycles)
    tier0 = cycles[0][2] if cycles else "debug"
    batch_id = a.batch_id or f"{now().strftime('%Y%m%dT%H%M%SZ')}_{tier0}"

    # ── 串行保证（workflow.md §5：同一时刻只允许一个原语处于 apply）──
    os.makedirs(STATE_DIR, exist_ok=True)
    if os.path.exists(LOCK):
        raise SystemExit(f"error: {LOCK} exists; another batch is running")
    stale = [f for f in os.listdir(STATE_DIR)
             if f != ".gitkeep" and not f.endswith(".lock")]
    if stale:
        raise SystemExit(f"error: stale primitive state files present: {stale}")
    open(LOCK, "w").write(f"{batch_id}\npid={os.getpid()}\n")

    batch_dir = os.path.join(a.out_root, batch_id)
    os.makedirs(batch_dir, exist_ok=True)
    log(f"batch {batch_id} start; {len(cycles)} cycles -> {batch_dir}")

    rows, aborted, rec_fail_streak = [], None, 0
    try:
        for i, (prim, svc, tier, param) in enumerate(cycles, 1):
            r, _ = run_cycle(i, prim, svc, tier, param, batch_dir, a.settle_s)
            rows.append(r)
            if r.get("aborted"):
                aborted = f"cycle {i}: {r['aborted']}"
                log(f"ABORT {aborted}")
                break
            if r["recovered"] is False:
                rec_fail_streak += 1
                log(f"recovered FAILED (streak {rec_fail_streak})")
                if rec_fail_streak >= a.abort_after_recovered_failures:
                    aborted = (f"{rec_fail_streak} consecutive recovered failures "
                               f"(limit {a.abort_after_recovered_failures})")
                    log(f"ABORT {aborted}")
                    break
            elif r["recovered"] is True:
                rec_fail_streak = 0
    finally:
        write_summary(batch_dir, batch_id, rows, aborted)
        if os.path.exists(LOCK):
            os.remove(LOCK)
        log(f"batch {batch_id} finished; summary at {batch_dir}/summary.md")
    return 1 if aborted else 0


if __name__ == "__main__":
    sys.exit(main())
