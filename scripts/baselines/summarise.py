#!/usr/bin/env python3
"""summarise.py -- one fixed, card-independent digest of an evidence pack.

Baseline 2 gets no tools, so it cannot go looking for anything: whatever it is
going to know has to be in the single prompt. That makes the summariser the whole
experiment. The rule is that the SHAPE of the digest is identical for every card
-- same sections, same ordering, same caps -- so the only thing that varies
between cards is the evidence itself. Nothing here branches on what the evidence
turns out to look like; a card whose fault is obvious and a card whose fault is
invisible get the same treatment and the same number of lines.

Caps exist because the packs are ~10 MB. They are stated once, here, and applied
uniformly:

  logs      LOG_ERRORS top error/fatal lines, newest last, body clipped
  metrics   per service, baseline vs inject vs recover for each key series
  traces    per callee: spans, errors, p50, and who called it, in each window
  alerts    verbatim from the agent-visible symptom, never from alerts.json
            (that file carries card_id, which is the answer in a string)
  topology  every edge, one line
  config    the diff verbatim, it is three lines
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from signals import Signals  # noqa: E402

LOG_ERRORS = 20
LOG_BODY_CHARS = 160
TRACE_ROWS = 18
METRIC_SERIES = ("req_rate_per_s", "error_rate_per_s", "p95_latency_ms",
                 "container_memory_mib")
WINDOWS = ("baseline", "inject", "recover")


def _fmt(x, nd=3):
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:.{nd}f}".rstrip("0").rstrip(".") or "0"
    return str(x)


def summarise(card_id, evidence_root=None):
    sig = Signals(card_id, evidence_root=evidence_root)
    out = []

    w = sig.pack.manifest["windows"]
    out.append("## windows")
    for k in WINDOWS:
        if k in w:
            out.append(f"{k}: {w[k]['start']} .. {w[k]['end']}")

    out.append("\n## topology (caller -> callee)")
    for e in sorted(sig.pack.topology.get("edges", []), key=lambda e: (e["from"], e["to"])):
        out.append(f"{e['from']} -> {e['to']}")

    out.append("\n## metrics (mean per window)")
    out.append("service | metric | baseline | inject | recover")
    for metric in METRIC_SERIES:
        keys = sorted(sig._metrics.get(metric, {}))
        for k in keys:
            if "|" in k:
                continue
            vals = [sig.mean_in(metric, k, win) for win in WINDOWS]
            if all(v is None for v in vals):
                continue
            out.append(f"{k} | {metric} | " + " | ".join(_fmt(v) for v in vals))

    out.append("\n## traces (per service, per window)")
    out.append("service | window | spans | errors | p50_ms | callers(spans) | caller_p50_ms")
    stats = {win: sig.edge_stats(win) for win in WINDOWS}
    services = sorted({s for win in WINDOWS for s in stats[win]},
                      key=lambda s: -max(stats[win].get(s, {}).get("spans", 0) for win in WINDOWS))
    for svc in services[:TRACE_ROWS]:
        for win in WINDOWS:
            d = stats[win].get(svc)
            if not d:
                out.append(f"{svc} | {win} | 0 | 0 | - | - | -")
                continue
            callers = ",".join(f"{c}({n})" for c, n in
                               sorted((d.get("callers") or {}).items(), key=lambda x: -x[1]))
            out.append(f"{svc} | {win} | {d['spans']} | {d['errors']} | "
                       f"{_fmt(d.get('p50_ms'))} | {callers or '-'} | {_fmt(d.get('caller_p50_ms'))}")

    out.append(f"\n## logs (up to {LOG_ERRORS} error/fatal lines)")
    errs = [r for r in sig.pack.logs
            if str(r.get("severity", "")).lower() in ("error", "fatal")]
    counts = {}
    for r in errs:
        counts[r.get("service")] = counts.get(r.get("service"), 0) + 1
    out.append("error line counts by service: " +
               (", ".join(f"{k}={v}" for k, v in sorted(counts.items(), key=lambda x: -x[1])) or "none"))
    for r in errs[-LOG_ERRORS:]:
        body = " ".join(str(r.get("body", "")).split())[:LOG_BODY_CHARS]
        out.append(f"{r.get('ts')} {r.get('service')} {r.get('severity')} {body}")

    out.append("\n## config diff")
    with open(os.path.join(sig.pack.dir, "config_diff.txt")) as f:
        out.append(f.read().strip())

    return "\n".join(out)
