#!/usr/bin/env python3
"""detect.py -- the agent-visible symptom: four rules over evidence/<card>/metrics.json.

This is what the agent is *told* ("monitoring says X"), so it must be derivable
from the evidence pack alone, the same way an on-call engineer's pager is. It
reads two files -- metrics.json and traces.json -- and nothing else. traces.json
was added with rule 6 (O-P2-17): a targeting fault never moves a rate, so the
only view it is visible in is per-entity, and the entity ids live on spans.

**It never touches scenarios/.** No import of the card modules, no open() of any
scenarios/ path, anywhere in this file. A detector that could see ground_truth
would eventually start agreeing with it, and the alert would stop being evidence
(fault_schema §4 wall 1, decision 005). The write-back into the card's
`agent_visible_symptom` is done by the caller -- run_batch.py, or
scripts/scenarios/write_symptom.py for a standalone run -- from alerts.json.

Four rules, scanned for every service / container present in the metrics:

  1. elevated error rate    inject errors >= N = max(5, ceil(0.25 x baseline
                            request rate x inject_s)) AND >= 2x the baseline
                            error count scaled to the inject window
  2. traffic dropped to zero  baseline request rate > 0 AND >= 2 consecutive
                            zero samples inside the inject window
  3. elevated p95 latency   inject p95 >= 2x baseline p95
  4. memory rising          container memory >= baseline + 30 MiB AND rising
                            monotonically across the inject window
  5. elevated error rate    same as rule 1 but per (service, operation): a flag
     (method level)         touching one method vanishes in a service counter
  6. errors concentrated    >= 5 error spans on one demo.<entity>.id AND >= 50%
     (entity level)         of that entity's spans in the inject window

Alerts are sorted by deviation multiple, descending. An empty list is recorded
as no_alert -- an explicit "the pager stayed quiet", not a missing field.

Usage:
  detect.py --card-id crash-cart-01 [--evidence-root evidence] [--stdout]
"""
import argparse
import json
import math
import os
import re
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EVIDENCE_ROOT = os.path.join(REPO, "evidence")

# ── rule constants ────────────────────────────────────────────────────────
# N floor and fraction are decision 018's crash threshold, reused: a fixed count
# is only meaningful for a high-traffic target (email/payment/checkout are called
# ~4.4/min, so 120s can never reach a fixed 20).
ERR_N_FLOOR = 5
ERR_N_FRAC = 0.25
ERR_BASELINE_MULT = 2.0
ZERO_RUN = 2                 # consecutive zero samples that count as "traffic gone"
# Rule 2 guards, added 2026-08-27 (O-P2-15). At a 15s step a service called ~3/min
# has empty samples as its *normal* state, so "2 consecutive zero samples" alone
# fired on payment / checkout / email in a clean, uninjected window -- with their
# inject-window rate essentially unchanged from baseline. Two conditions now have
# to hold on top of the zero run:
#   (a) the inject window's mean rate actually collapsed, same 10% cut decision
#       016 uses for the blackhole symptom (caller spans < baseline x 0.10);
#   (b) the zero span is long enough that the baseline rate predicts at least
#       ZERO_MIN_EXPECTED calls should have landed in it -- i.e. the silence is
#       statistically meaningful for *this* service's traffic level, not just
#       longer than a fixed number of samples.
ZERO_RATE_FRAC = 0.10
ZERO_MIN_EXPECTED = 5
P95_MULT = 2.0
# Absolute p95 arm (O-P2-17). latency-checkout-800 passed the production gate and
# alerted on nothing: 800ms injected on checkout's egress moved its own p95 by far
# less than 2x, because checkout's baseline p95 is already large. A ratio alone
# cannot see a fixed-size delay added to a slow service; 500ms is well above the
# sampling noise on every service measured here and below the smallest injected
# delay (800ms).
P95_ABS_MS = 500.0

# Rule 5, method-level error rate (O-P2-17). Same shape as rule 1 but per
# (service, operation): a flag that only touches one method disappears into the
# service-level counter.
METHOD_ERR_N_FLOOR = 5
METHOD_ERR_N_FRAC = 0.25
METHOD_ERR_BASELINE_MULT = 2.0

# Rule 6, entity-level concentration (O-P2-17). Reads traces.json, which since
# O-P2-16 carries whitelisted demo.<entity>.id tags. A targeting-style fault fails
# one entity completely and leaves the rest untouched -- invisible in any rate,
# obvious the moment you group by the id.
ENTITY_ERR_FLOOR = 5
ENTITY_ERR_FRAC = 0.5
# Same pattern pack.py whitelists into traces.json (fault_schema §9).
BUSINESS_ID_RE = re.compile(r"^demo\..+\.id$")
SERIES_SEP = "|"          # matches queries.SERIES_SEP; detect.py imports nothing
MEM_GROWTH_MIB = 30.0
MEM_MONOTONIC_TOL_MIB = 0.5  # sampling jitter allowance for "monotonically rising"

RULES = {
    "error_rate_jump": "elevated error rate on {name}",
    "traffic_zero": "traffic dropped to zero on {name}",
    "p95_jump": "elevated p95 latency on {name}",
    "memory_over_line": "memory rising on {name}",
    "method_error_rate_jump": "elevated error rate on {name}",
    "entity_error_concentration": "errors concentrated on {name}",
}


def _dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _in(series, t0, t1):
    return [(ts, v) for ts, v in series if t0 <= ts <= t1 and v is not None]


def counter_delta(points):
    """Sum of positive increments -- counter resets (a restarted container zeroes
    its spanmetrics counter) must not turn into a negative or a huge delta."""
    total = 0.0
    for (_, a), (_, b) in zip(points, points[1:]):
        if b >= a:
            total += b - a
        else:
            total += b            # reset: everything after the reset is new
    return total


def median(vals):
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def load_metrics(path):
    with open(path) as f:
        m = json.load(f)
    by_name = {q["name"]: q for q in m["queries"]}
    return m, by_name


def detect_entity_concentration(traces_path, i0, i1, w):
    """Rule 6: group the inject window's spans by their demo.<entity>.id tags.

    A targeting fault fails one entity outright and leaves every other entity
    untouched, so it never moves a rate: product-catalog's 32 failing GetProduct
    calls sat under a service-level threshold of 102 while being 100% of that one
    product's traffic. Grouping by the id is the only view where it is loud.

    Returns [] when traces.json is missing or carries no id tags -- packs made
    before O-P2-16 have no tags at all, and a missing input is not an alert.
    """
    if not os.path.exists(traces_path):
        return []
    with open(traces_path) as f:
        tj = json.load(f)
    # {(tag_key, tag_value, service): [total, errors]}
    buckets = {}
    for sp in tj.get("spans") or []:
        st = (sp.get("start") or 0) / 1e6
        if not (i0 <= st <= i1):
            continue
        tags = sp.get("tags") or {}
        for k, v in tags.items():
            if not BUSINESS_ID_RE.match(k):
                continue
            b = buckets.setdefault((k, str(v), sp.get("service")), [0, 0])
            b[0] += 1
            if sp.get("status") == "ERROR":
                b[1] += 1
    out = []
    for (k, v, svc), (total, errs) in sorted(buckets.items()):
        frac = errs / total if total else 0.0
        if errs >= ENTITY_ERR_FLOOR and frac >= ENTITY_ERR_FRAC:
            out.append({
                "rule": "entity_error_concentration",
                "message": RULES["entity_error_concentration"].format(
                    name=f"{k}={v} ({svc})"),
                "service": svc, "tag": k, "value": v,
                "baseline": {"note": "entity grouping is computed on the inject "
                                     "window only; the baseline arm is the other "
                                     "entities, reported as total/errors below",
                             "window": w["baseline"]},
                "observed": {"spans": total, "error_spans": errs,
                             "error_frac": round(frac, 4),
                             "threshold_frac": ENTITY_ERR_FRAC,
                             "threshold_abs": ENTITY_ERR_FLOOR,
                             "window": w["inject"]},
                "window": w["inject"],
                "deviation": float(errs),
                "deviation_unit": "error spans on the entity",
            })
    return out


def detect(metrics_path, traces_path=None):
    if traces_path is None:
        traces_path = os.path.join(os.path.dirname(metrics_path), "traces.json")
    m, q = load_metrics(metrics_path)
    w = m["windows"]
    b0, b1 = _dt(w["baseline"]["start"]), _dt(w["baseline"]["end"])
    i0, i1 = _dt(w["inject"]["start"]), _dt(w["inject"]["end"])
    base_s = max(b1 - b0, 1.0)
    inject_s = max(i1 - i0, 1.0)
    step = m.get("step_s") or 15

    calls = q["calls_total"]["series"]
    errors = q["errors_total"]["series"]
    p95 = q["p95_latency_ms"]["series"]
    mem = q["container_memory_mib"]["series"]

    alerts = []

    # ── rules 1-2: per service, off the raw counters ──
    for svc in sorted(set(calls) | set(errors)):
        b_calls = counter_delta(_in(calls.get(svc, []), b0, b1))
        i_calls_pts = _in(calls.get(svc, []), i0, i1)
        b_rate = b_calls / base_s
        b_errs = counter_delta(_in(errors.get(svc, []), b0, b1))
        i_errs = counter_delta(_in(errors.get(svc, []), i0, i1))
        scaled_b_errs = b_errs * (inject_s / base_s)

        n = max(ERR_N_FLOOR, math.ceil(ERR_N_FRAC * b_rate * inject_s))
        if i_errs >= n and i_errs >= ERR_BASELINE_MULT * scaled_b_errs:
            alerts.append({
                "rule": "error_rate_jump",
                "message": RULES["error_rate_jump"].format(name=svc),
                "service": svc,
                "baseline": {"error_count": round(b_errs, 2),
                             "error_count_scaled_to_inject": round(scaled_b_errs, 2),
                             "request_rate_per_s": round(b_rate, 4),
                             "window": w["baseline"]},
                "observed": {"error_count": round(i_errs, 2), "threshold_N": n,
                             "window": w["inject"]},
                "window": w["inject"],
                "deviation": round(i_errs / max(scaled_b_errs, 1.0), 3),
            })

        # traffic disappearing: consecutive zero deltas inside the inject window.
        # A series that vanishes entirely counts as zero too -- Prometheus keeps a
        # dead target's last sample for 5 min of staleness, so a killed container
        # normally shows as a flat counter (delta 0) rather than a gap; both read
        # the same here on purpose.
        if b_rate > 0:
            expected = int(inject_s // step)
            if len(i_calls_pts) < 2:
                zero_run, deltas = max(expected, ZERO_RUN), []
            else:
                deltas = [b - a for (_, a), (_, b) in zip(i_calls_pts, i_calls_pts[1:])]
                zero_run, run = 0, 0
                for d in deltas:
                    run = run + 1 if d <= 0 else 0
                    zero_run = max(zero_run, run)
                missing = expected - len(deltas)
                if missing > 0:
                    zero_run = max(zero_run, missing)
            i_rate = counter_delta(i_calls_pts) / inject_s
            zero_span_s = zero_run * step
            expected_missing = b_rate * zero_span_s
            rate_collapsed = i_rate <= b_rate * ZERO_RATE_FRAC
            enough_expected = expected_missing >= ZERO_MIN_EXPECTED
            if zero_run >= ZERO_RUN and rate_collapsed and enough_expected:
                alerts.append({
                    "rule": "traffic_zero",
                    "message": RULES["traffic_zero"].format(name=svc),
                    "service": svc,
                    "baseline": {"request_rate_per_s": round(b_rate, 4),
                                 "window": w["baseline"]},
                    "observed": {"request_rate_per_s": round(i_rate, 4),
                                 "rate_ceiling_per_s": round(b_rate * ZERO_RATE_FRAC, 4),
                                 "consecutive_zero_samples": zero_run,
                                 "zero_span_s": zero_span_s,
                                 "expected_calls_in_zero_span": round(expected_missing, 2),
                                 "min_expected_calls": ZERO_MIN_EXPECTED,
                                 "samples_in_window": len(i_calls_pts),
                                 "window": w["inject"]},
                    "window": w["inject"],
                    # Rule 2's deviation is a *count* -- calls the baseline rate
                    # says should have landed in the silence -- not a multiple.
                    # Dividing baseline by an inject rate of exactly 0 produced
                    # six-figure ratios that swamped every other rule in the sort
                    # order and said nothing about how much traffic was actually
                    # lost. Rules 1/3/4 keep their multiples.
                    "deviation": round(expected_missing, 2),
                    "deviation_unit": "expected calls missed",
                })

    # ── rule 3: p95 latency ──
    for svc in sorted(p95):
        bp = median([v for _, v in _in(p95[svc], b0, b1)])
        ip = median([v for _, v in _in(p95[svc], i0, i1)])
        if bp is None or ip is None or bp <= 0:
            continue
        ratio = ip / bp
        delta = ip - bp
        if ratio >= P95_MULT or delta >= P95_ABS_MS:
            alerts.append({
                "rule": "p95_jump",
                "message": RULES["p95_jump"].format(name=svc),
                "service": svc,
                "baseline": {"p95_ms": round(bp, 2), "window": w["baseline"]},
                "observed": {"p95_ms": round(ip, 2),
                             "delta_ms": round(delta, 2),
                             "ratio": round(ratio, 3),
                             "threshold_ratio": P95_MULT,
                             "threshold_delta_ms": P95_ABS_MS,
                             "arm": "ratio" if ratio >= P95_MULT else "absolute",
                             "window": w["inject"]},
                "window": w["inject"],
                # whichever arm fired harder; both are unitless multiples of their
                # own threshold so they stay comparable in the sort
                "deviation": round(max(ratio, delta / P95_ABS_MS), 3),
            })

    # ── rule 4: container memory ──
    for cont in sorted(mem):
        bpts = [v for _, v in _in(mem[cont], b0, b1)]
        ipts = [v for _, v in _in(mem[cont], i0, i1)]
        if not bpts or len(ipts) < 2:
            continue
        base_mib = bpts[-1]
        growth = ipts[-1] - base_mib
        rising = all(b >= a - MEM_MONOTONIC_TOL_MIB for a, b in zip(ipts, ipts[1:]))
        if growth >= MEM_GROWTH_MIB and rising:
            alerts.append({
                "rule": "memory_over_line",
                "message": RULES["memory_over_line"].format(name=cont),
                "service": cont,
                "baseline": {"memory_mib": round(base_mib, 1), "window": w["baseline"]},
                "observed": {"memory_mib": round(ipts[-1], 1),
                             "growth_mib": round(growth, 1),
                             "threshold_growth_mib": MEM_GROWTH_MIB,
                             "monotonic": rising, "window": w["inject"]},
                "window": w["inject"],
                "deviation": round(growth / MEM_GROWTH_MIB, 3),
            })

    # ── rule 5: per (service, operation) error rate ──
    m_calls = (q.get("calls_total_by_operation") or {}).get("series") or {}
    m_errs = (q.get("errors_total_by_operation") or {}).get("series") or {}
    for key in sorted(set(m_calls) | set(m_errs)):
        svc, _, op = key.partition(SERIES_SEP)
        b_calls = counter_delta(_in(m_calls.get(key, []), b0, b1))
        b_rate = b_calls / base_s
        b_errs = counter_delta(_in(m_errs.get(key, []), b0, b1))
        i_errs = counter_delta(_in(m_errs.get(key, []), i0, i1))
        scaled_b = b_errs * (inject_s / base_s)
        n = max(METHOD_ERR_N_FLOOR, math.ceil(METHOD_ERR_N_FRAC * b_rate * inject_s))
        if i_errs >= n and i_errs >= METHOD_ERR_BASELINE_MULT * scaled_b:
            alerts.append({
                "rule": "method_error_rate_jump",
                "message": RULES["method_error_rate_jump"].format(name=f"{svc}/{op}"),
                "service": svc, "operation": op,
                "baseline": {"error_count": round(b_errs, 2),
                             "error_count_scaled_to_inject": round(scaled_b, 2),
                             "request_rate_per_s": round(b_rate, 4),
                             "window": w["baseline"]},
                "observed": {"error_count": round(i_errs, 2), "threshold_N": n,
                             "window": w["inject"]},
                "window": w["inject"],
                "deviation": round(i_errs / max(scaled_b, 1.0), 3),
            })

    # ── rule 6: entity-level concentration (needs traces.json) ──
    alerts.extend(detect_entity_concentration(traces_path, i0, i1, w))

    alerts.sort(key=lambda a: (-a["deviation"], a["rule"], a["service"]))
    return {
        "detected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window": w["inject"],
        "windows": w,
        "rules": {k: v.replace("{name}", "<service>") for k, v in RULES.items()},
        "rules_version": "2026-08-27 (O-P2-17: absolute p95 arm, rules 5 and 6)",
        "alerts": alerts,
        "no_alert": not alerts,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--card-id", required=True)
    ap.add_argument("--evidence-root", default=EVIDENCE_ROOT)
    ap.add_argument("--stdout", action="store_true", help="also print the result")
    a = ap.parse_args()
    cdir = os.path.join(a.evidence_root, a.card_id)
    res = detect(os.path.join(cdir, "metrics.json"),
                 os.path.join(cdir, "traces.json"))
    res["card_id"] = a.card_id
    out = os.path.join(cdir, "alerts.json")
    with open(out, "w") as f:
        json.dump(res, f, indent=1)
    if a.stdout:
        print(json.dumps(res, indent=1, ensure_ascii=False))
    else:
        print(f"{a.card_id}: {len(res['alerts'])} alerts -> {os.path.relpath(out, REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
