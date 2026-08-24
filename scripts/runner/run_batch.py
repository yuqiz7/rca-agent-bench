#!/usr/bin/env python3
"""run_batch.py —— 注入周期批次 runner（docs/workflow.md §5、§7）

串行执行一份周期清单：每个周期走 workflow.md §2 的五段流程，按
docs/fault_schema.md §5 判三探针，落盘每周期产物与批次汇总。

只用标准库。运行在宿主机，不进容器；本身不 daemon 化 —— 无人值守用
scripts/runner/run_batch.sh 包 nohup。

判定只在 runner 侧发生：agent 永远看不到本文件产出的 probes.json 与
anchors.json（泄漏隔离，见 fault_schema §4 与决策 015）。
"""
import argparse, json, os, subprocess, sys, time
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

# symptom 阈值，逐条抄自 fault_schema §5，改阈值请先改 §5。
CRASH_ERROR_SPANS_MIN = 20        # §5: 调用方对 B 的错误 span 数 > N（cart 实测 N = 20）
BLACKHOLE_SPAN_FRAC = 0.10        # §5: caller_spans_total 低于基线 10%
LATENCY_SHIFT_FRAC = 0.80         # §5: 耗时分布右移 ≥ delay_ms × 0.8
# §5 未给各类 recovered 的统一数值判据，此处按"symptom 判定为假 + caller span
# 数回到基线 50% 以上"实现，待 ⑤ 定稿后回填 §5。
RECOVER_SPAN_FRAC = 0.50

PRIMS = {"kill_container", "drop_inbound", "delay_outbound"}
CLASS_OF = {"kill_container": "crash", "drop_inbound": "blackhole",
            "delay_outbound": "latency"}


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


def probe_signals(svc, t0, t1, out_dir):
    rc, so, se = sh([sys.executable, PROBE, svc, iso(t0), iso(t1), "--out-dir", out_dir])
    if rc != 0:
        return None, f"three_signals rc={rc}: {se.strip()[:300]}"
    try:
        return json.loads(so), None
    except json.JSONDecodeError as e:
        return None, f"three_signals bad json: {e}"


# ── 残留核对：每类各自的方式，runner 不复用单一手法 ────────────────────────
def residue_clean(prim, svc):
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


# ── 三探针判定，逐类按 fault_schema §5 ────────────────────────────────────
def judge_symptom(cls, base, during, param):
    """返回 (pass: bool, detail: dict)。base/during 是 three_signals 的 summary。"""
    bt, dt_ = base["traces"], during["traces"]
    if cls == "crash":
        got = dt_.get("caller_error_spans") or 0
        return got > CRASH_ERROR_SPANS_MIN, {
            "rule": f"caller_error_spans > {CRASH_ERROR_SPANS_MIN} (§5)",
            "during_error_spans": got, "baseline_error_spans": bt.get("caller_error_spans")}
    if cls == "blackhole":
        b = bt.get("caller_spans_total") or 0
        d = dt_.get("caller_spans_total") or 0
        thr = b * BLACKHOLE_SPAN_FRAC
        return (b > 0 and d < thr), {
            "rule": f"caller_spans_total < baseline x {BLACKHOLE_SPAN_FRAC} (§5)",
            "baseline_spans": b, "during_spans": d, "threshold": round(thr, 2)}
    # latency
    bp = (bt.get("caller_all_dur") or {}).get("p50_ms")
    dp = (dt_.get("caller_all_dur") or {}).get("p50_ms")
    delay = int(param or 800)
    need = delay * LATENCY_SHIFT_FRAC
    shift = (dp - bp) if (bp is not None and dp is not None) else None
    err_ok = (dt_.get("caller_error_spans") or 0) <= (bt.get("caller_error_spans") or 0)
    ok = shift is not None and shift >= need and err_ok
    return ok, {"rule": f"p50 shift >= delay x {LATENCY_SHIFT_FRAC} and errors not up (§5)",
                "baseline_p50_ms": bp, "during_p50_ms": dp,
                "shift_ms": round(shift, 2) if shift is not None else None,
                "required_shift_ms": need, "errors_not_up": err_ok,
                "downstream_edges_during": (dt_.get("downstream_edges") or {})}


def judge_recovered(cls, base, after, param):
    sym_still, sd = judge_symptom(cls, base, after, param)
    b = (base["traces"].get("caller_spans_total") or 0)
    a = (after["traces"].get("caller_spans_total") or 0)
    span_back = b > 0 and a >= b * RECOVER_SPAN_FRAC
    return (not sym_still) and span_back, {
        "rule": f"symptom false in recover window AND caller_spans_total >= baseline x {RECOVER_SPAN_FRAC}"
                " (§5 未给数值判据，此处为 runner 实现，待 ⑤ 定稿回填)",
        "symptom_still_true": sym_still, "symptom_detail": sd,
        "baseline_spans": b, "after_spans": a}


def sleep_until(target):
    while True:
        left = (target - now()).total_seconds()
        if left <= 0:
            return
        time.sleep(min(left, 5))


def git_head(path):
    rc, so, _ = sh(["git", "-C", path, "rev-parse", "--short", "HEAD"])
    return so.strip() if rc == 0 else "unknown"


def run_cycle(idx, prim, svc, tier, param, batch_dir):
    cls = CLASS_OF[prim]
    tm = TIERS[tier]
    cdir = os.path.join(batch_dir, f"{idx:02d}_{prim}_{svc}")
    os.makedirs(cdir, exist_ok=True)
    script = os.path.join(PRIMITIVES, f"{prim}.sh")
    pargs = [svc] + ([str(param)] if prim == "delay_outbound" and param else [])

    res = {"idx": idx, "primitive": prim, "service": svc, "class": cls,
           "tier": tier, "param": param,
           "injected": None, "symptom": None, "recovered": None,
           "residue_clean": None, "aborted": None, "notes": []}

    t0 = now()
    log(f"cycle {idx} {prim}/{svc} tier={tier} param={param} t0={iso(t0)}")

    # ── 稳定期 → 基线窗 ──
    sleep_until(t0 + timedelta(seconds=tm["pre"]))
    base, err = probe_signals(svc, t0, now(), cdir)
    if err:
        res["aborted"] = f"baseline probe failed: {err}"
        return res, None
    json.dump(base, open(os.path.join(cdir, "window_baseline.json"), "w"), indent=1)

    # ── apply ──
    rc, so, se = sh([script, "apply"] + pargs)
    if rc != 0:
        res["aborted"] = f"apply rc={rc}: {se.strip()[:300]}"
        sh([script, "revert"] + pargs)
        return res, None
    kv = parse_kv(so)
    t_apply = now()

    # ── 注入期中段 probe（injected）──
    sleep_until(t_apply + timedelta(seconds=tm["inject"] // 2))
    rc, so, se = sh([script, "probe"] + pargs)
    res["injected"] = (parse_kv(so).get("injected") == "true")
    if not res["injected"]:
        # injected 失败 = 无效注入，立刻 revert 并中止批次（§5 失败即停）
        sh([script, "revert"] + pargs)
        res["aborted"] = "injected=false at mid-inject; batch aborted"
        return res, None

    # ── 注入窗 ──
    sleep_until(t_apply + timedelta(seconds=tm["inject"]))
    during, err = probe_signals(svc, t_apply, now(), cdir)
    if err:
        sh([script, "revert"] + pargs)
        res["aborted"] = f"during probe failed: {err}"
        return res, None
    json.dump(during, open(os.path.join(cdir, "window_during.json"), "w"), indent=1)

    # ── revert ──
    rc, so, se = sh([script, "revert"] + pargs)
    t_revert = now()
    if rc != 0:
        res["aborted"] = f"revert rc={rc}: {se.strip()[:300]}"
        return res, None

    rc, so, _ = sh([script, "probe"] + pargs)
    reverted = (parse_kv(so).get("injected") == "false")
    clean, cdetail = residue_clean(prim, svc)
    res["residue_clean"] = bool(reverted and clean)
    res["notes"].append(f"probe_after_revert injected={'false' if reverted else 'true'}; {cdetail}")

    # ── 恢复期 → 恢复窗 [t_revert+30s, t_end] ──
    t_end = t_revert + timedelta(seconds=tm["post"])
    rec_start = t_revert + timedelta(seconds=RECOVER_SKIP_S)
    if rec_start >= t_end:
        res["recovered"] = None
        res["notes"].append(
            f"recover window empty: t_revert+{RECOVER_SKIP_S}s >= t_end (post={tm['post']}s); "
            "workflow.md §3 已记：调试档结构上无法评估 recovered")
        sleep_until(t_end)
        after = None
    else:
        sleep_until(t_end)
        after, err = probe_signals(svc, rec_start, t_end, cdir)
        if err:
            res["aborted"] = f"after probe failed: {err}"
            return res, None
        json.dump(after, open(os.path.join(cdir, "window_after.json"), "w"), indent=1)

    # ── 判定 ──
    # probe_signals 返回的就是 three_signals 打到 stdout 的 summary 本体
    sym_ok, sym_d = judge_symptom(cls, base, during, param)
    res["symptom"] = sym_ok
    probes = {"injected": {"pass": res["injected"], "detail": "primitive probe injected=true"},
              "symptom": {"pass": sym_ok, "detail": sym_d}}
    if after is not None:
        rec_ok, rec_d = judge_recovered(cls, base, after, param)
        res["recovered"] = rec_ok
        probes["recovered"] = {"pass": rec_ok, "detail": rec_d}
    else:
        probes["recovered"] = {"pass": None, "detail": "recover window empty (debug tier)"}

    anchors = {"t0": iso(t0), "t_apply": iso(t_apply), "t_revert": iso(t_revert),
               "t_end": iso(t_end), "tier": tier, "primitive": prim, "service": svc,
               "param": param, "class": cls,
               "testbed": {"repo": "opentelemetry-demo", "tag": "3.0.0",
                           "branch": "p2-baseline",
                           "commit": git_head(os.path.join(os.path.dirname(REPO),
                                                           "opentelemetry-demo"))},
               "p2_rca_commit": git_head(REPO)}
    json.dump(anchors, open(os.path.join(cdir, "anchors.json"), "w"), indent=1)
    json.dump(probes, open(os.path.join(cdir, "probes.json"), "w"), indent=1)
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
        return "—" if v is None else ("通过" if v else "**失败**")
    npass = nfail = nred = 0
    for r in rows:
        d = ((r.get("probes") or {}).get("symptom") or {}).get("detail") or {}
        if r["class"] == "crash":
            key = f"报错 span {d.get('during_error_spans')}（阈值 >{CRASH_ERROR_SPANS_MIN}）"
        elif r["class"] == "blackhole":
            key = f"caller span {d.get('during_spans')} / 基线 {d.get('baseline_spans')}"
        else:
            key = f"p50 右移 {d.get('shift_ms')}ms（需 ≥{d.get('required_shift_ms')}）"
        if r.get("aborted"):
            key = r["aborted"]
        L.append(f"| {r['idx']} | `{r['primitive']}` | `{r['service']}` | {r['tier']} | "
                 f"{mark(r['injected'])} | {mark(r['symptom'])} | {mark(r['recovered'])} | "
                 f"{mark(r['residue_clean'])} | {key} |")
        if r["recovered"] is False:
            nred += 1
        if r["injected"] and r["symptom"] and r["recovered"] is not False:
            npass += 1
        else:
            nfail += 1
    L += ["", f"通过 {npass}　失败 {nfail}　recovered 标红 {nred}　共 {len(rows)} 周期"]
    open(os.path.join(batch_dir, "summary.md"), "w").write("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", required=True)
    ap.add_argument("--batch-id", default=None)
    ap.add_argument("--abort-after-recovered-failures", type=int, default=2)
    ap.add_argument("--out-root", default=os.path.join(ROOT, "out"))
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
            r, _ = run_cycle(i, prim, svc, tier, param, batch_dir)
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
