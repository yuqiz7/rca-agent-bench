#!/usr/bin/env python3
"""keyword_heuristic.py -- baseline 1: rule-based diagnosis, zero API calls.

The floor the LLM arms have to beat. It reads the same evidence surface the agent
does (EvidencePack, so evidence/<card>/ only) and applies a fixed rule set derived
from fault_schema §5 and fingerprints.md. Thresholds are the harness's own
constants, not values fitted to this card set, and no rule mentions a card id or a
service name as an answer -- see the test at the bottom of run().

Rule order matters and encodes the two mistakes decision 024 recorded:

  1. localise    score every service by alert concentration (target-side rules
                 outweigh observer-side ones) plus its own telemetry, and pick the
                 argmax. Entry-point services are discounted when the only thing
                 naming them is latency-shaped, because they observe every fault
                 downstream of them.
  2. mem_leak    checked FIRST among the types, because a leak presents as
                 slowness and would otherwise be swallowed by the latency rule.
  3. crash |     a candidate whose own traffic collapsed to <= 10% of baseline is
     blackhole   one of these two; loud upstream errors mean the process is gone,
                 a quiet edge means packets are being dropped.
  4. misconfig   self errors on the candidate's own server spans while its traffic
                 is NOT zero.
  5. latency     last: caller-edge p50 stepped up and nothing above fired.

Usage:
  keyword_heuristic.py --card-id crash-cart-01
  keyword_heuristic.py --cards a,b,c --out artifacts/.../baseline1.json
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from signals import (  # noqa: E402
    LATENCY_MIN_DELTA_MS, LATENCY_RATIO, OBSERVER_SERVICES, SILENCE_CEILING_FRAC,
    Signals, assert_no_card_id_in_features,
)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent"))
from evidence_tools import TARGET_ENUM  # noqa: E402

# Alert rules split by what they tell you about *where* the fault is. A rule that
# fires on the faulted service itself localises; one that fires wherever the pain
# was felt does not.
TARGET_SIDE_RULES = {
    "traffic_zero": 3.0,
    "entity_error_concentration": 3.0,
    "error_rate_jump": 2.0,
    "method_error_rate_jump": 1.5,
}
OBSERVER_SIDE_RULES = {
    "p95_jump": 1.0,
    "method_latency_jump": 1.0,
}
OBSERVER_DISCOUNT = 0.25

# Median caller time above which the caller was blocked rather than refused.
# decision 023's step arm, absolute half.
CALLER_WAIT_MS = 1000.0



# Below this, a service's callers have themselves collapsed and its silence says
# nothing about the service: it is starved, not broken.
STARVED_CALLER_FRAC = 0.5


def _went_quiet(f):
    """True when the service stopped serving, by spans or by rate.

    Spans are checked first and on their own: the rate series is a 15 s-granularity
    Prometheus rate that smooths across the window edge, so a service with zero
    spans in the inject window can still show a third of its baseline rate. The
    span count is the direct measurement and the rate is the smoothed one, so a
    disagreement is resolved in favour of the spans.
    """
    sr, rr = f.get("span_ratio"), f.get("rate_ratio")
    if sr is not None and f.get("spans_baseline", 0) >= 5:
        return sr <= SILENCE_CEILING_FRAC
    return rr is not None and rr <= SILENCE_CEILING_FRAC


def _ratio(during, base):
    if base in (None, 0) or during is None:
        return None
    return during / base


def localise(sig):
    """Score every candidate service; return (best, scores, per-service features)."""
    eb, ei = sig.edge_stats("baseline"), sig.edge_stats("inject")
    logs_b, logs_i = sig.log_error_counts("baseline"), sig.log_error_counts("inject")
    callers = {}
    for e in sig.pack.topology.get("edges", []):
        callers.setdefault(e["to"], set()).add(e["from"])

    feats, scores = {}, {}
    for svc in TARGET_ENUM:
        b, i = eb.get(svc, {}), ei.get(svc, {})
        rate_b = sig.mean_in("req_rate_per_s", svc, "baseline")
        rate_i = sig.mean_in("req_rate_per_s", svc, "inject")
        span_ratio = _ratio(i.get("spans", 0), b.get("spans"))
        rate_ratio = _ratio(rate_i, rate_b)
        # upstream distress: error spans on the services that call this one
        up_err_b = sum(eb.get(c, {}).get("errors", 0) for c in callers.get(svc, ()))
        up_err_i = sum(ei.get(c, {}).get("errors", 0) for c in callers.get(svc, ()))
        up_log_err_i = sum(logs_i.get(c, 0) for c in callers.get(svc, ()))
        up_log_err_b = sum(logs_b.get(c, 0) for c in callers.get(svc, ()))
        # Are this service's callers still working at all? This is what separates
        # "broken" from "starved". A faulted service goes quiet while the services
        # that call it keep running and start erroring or waiting on it. A service
        # that is merely downstream of the fault goes quiet *because its callers
        # went quiet first* -- nobody is asking it for anything. Both look
        # identical if you only measure the service itself, which is how a cascade
        # sibling ends up outscoring the real root cause.
        # Weight by who actually called this service in the baseline window, not by
        # every edge the topology graph lists. Summing raw span counts over all
        # listed callers lets the entry-point service -- which has orders of
        # magnitude more spans than anyone else -- decide the answer for every
        # candidate, which hides exactly the case this feature exists to catch:
        # the one caller that mattered having gone dark.
        base_callers = (eb.get(svc, {}).get("callers") or {})
        num = den = 0.0
        for c, vol in base_callers.items():
            cr = _ratio(ei.get(c, {}).get("spans", 0), eb.get(c, {}).get("spans"))
            if cr is None:
                continue
            num += vol * cr
            den += vol
        caller_activity = (num / den) if den else None
        # Low caller volume on its own does not mean starvation. A service that
        # died takes its caller's throughput down with it, so the caller looks
        # quiet in exactly the case where its silence is most meaningful. What
        # tells the two apart is whether the caller is in distress while it is
        # quiet: a starved caller is idle and healthy, a caller stuck on a broken
        # dependency is blocked or erroring. Only the idle-and-healthy case is
        # allowed to discount the candidate.
        # Distress is read only off the caller being BLOCKED, not off it erroring.
        # Errors travel: in a cascade every service on the path back to the entry
        # point ends up erroring, so an error-based test makes each starved node
        # downstream of the fault look implicated too, and the walk runs off the
        # end of the chain. Being blocked for a full second does not travel the
        # same way -- it is the signature of waiting on this particular
        # dependency.
        caller_distress = any(
            (ei.get(c, {}).get("p50_ms") or 0) >= CALLER_WAIT_MS for c in base_callers)
        up_spans_b = sum(eb.get(c, {}).get("spans", 0) for c in base_callers)
        up_spans_i = sum(ei.get(c, {}).get("spans", 0) for c in base_callers)
        f = {
            "rate_baseline": rate_b, "rate_inject": rate_i, "rate_ratio": rate_ratio,
            "spans_baseline": b.get("spans", 0), "spans_inject": i.get("spans", 0),
            "span_ratio": span_ratio,
            "self_errors_baseline": b.get("errors", 0), "self_errors_inject": i.get("errors", 0),
            "caller_errors_baseline": b.get("caller_errors", 0),
            "caller_errors_inject": i.get("caller_errors", 0),
            "caller_p50_baseline": b.get("caller_p50_ms"), "caller_p50_inject": i.get("caller_p50_ms"),
            "log_errors_baseline": logs_b.get(svc, 0), "log_errors_inject": logs_i.get(svc, 0),
            "upstream_errors_baseline": up_err_b, "upstream_errors_inject": up_err_i,
            "upstream_spans_baseline": up_spans_b, "upstream_spans_inject": up_spans_i,
            "upstream_log_errors_baseline": up_log_err_b, "upstream_log_errors_inject": up_log_err_i,
            "caller_activity": caller_activity, "caller_distress": caller_distress,
            "max_caller_p50_inject": max(
                [ei.get(c, {}).get("p50_ms") for c in base_callers
                 if ei.get(c, {}).get("p50_ms") is not None] or [None],
                key=lambda x: (x is not None, x)),
            "memory": sig.memory_growth(svc),
        }
        feats[svc] = f

        s = 0.0
        # -- alert evidence, weighted by how much the rule localises
        for a in sig.alerts():
            if a.get("service") != svc:
                continue
            rule = a.get("rule")
            if rule in TARGET_SIDE_RULES:
                s += TARGET_SIDE_RULES[rule]
            elif rule in OBSERVER_SIDE_RULES:
                w = OBSERVER_SIDE_RULES[rule]
                s += w * (OBSERVER_DISCOUNT if svc in OBSERVER_SERVICES else 1.0)
        # -- telemetry evidence, independent of whether a rule happened to fire
        if f["memory"] and f["memory"]["leaking"]:
            s += 4.0
        quiet = _went_quiet(f)
        if quiet:
            # Credit the silence only in proportion to how alive its callers still
            # are: silence with a working caller is evidence, silence with a dead
            # caller is just the far end of someone else's outage.
            if (caller_activity is None or caller_activity >= STARVED_CALLER_FRAC
                    or caller_distress):
                s += 3.0
            else:
                s -= 2.0
        if f["self_errors_inject"] > f["self_errors_baseline"]:
            s += 2.0
        cb, ci = f["caller_p50_baseline"], f["caller_p50_inject"]
        if cb and ci and ci >= LATENCY_RATIO * cb and (ci - cb) >= LATENCY_MIN_DELTA_MS:
            s += 2.5
        scores[svc] = round(s, 3)

    best = max(scores, key=lambda k: (scores[k], -TARGET_ENUM.index(k)))
    best = _walk_downstream(best, scores, feats, sig)
    return best, scores, feats


def _walk_downstream(best, scores, feats, sig, max_hops=4):
    """Follow the anomaly to the deepest service that still shows it.

    Both latency and blackhole propagate *upwards*: a caller blocked on a slow or
    silent dependency looks slow itself, and its own caller looks slow in turn, so
    every service on the path back to the entry point carries the signature. The
    fault is at the far end of that path -- the deepest service that is still
    anomalous and whose own dependencies are not. Walking down the dependency
    graph while the evidence holds is what turns "somewhere on this chain" into a
    single answer.
    """
    out = _downstream_graph(sig)
    seen = {best}
    for _ in range(max_hops):
        f = feats.get(best) or {}
        # Terminal signatures: the chain cannot continue past these. Silence means
        # we have already reached the service that stopped serving, and a leak is
        # a property of the process itself with nothing downstream to blame.
        if f.get("memory") and f["memory"]["leaking"]:
            break
        if _went_quiet(f):
            break
        cands = [d for d in out.get(best, ()) if d in scores and d not in seen
                 and _is_anomalous(feats.get(d))]
        if not cands:
            break
        # Self errors are deliberately NOT terminal. A service returning errors on
        # its own spans looks like the misconfigured one, but a service whose
        # dependency died returns errors on its own spans too -- the entry proxy
        # answering 503 because the service behind it is gone is the clearest
        # case. What separates them is whether anything downstream is also
        # anomalous: if something is, the errors here are the symptom and the
        # cause is deeper; if nothing is, this service is answering wrongly on its
        # own and the chain ends here.
        # Prefer a dependency that has gone silent over one that is merely slow:
        # silence is the end of the chain, slowness is another link in it.
        nxt = max(cands, key=lambda k: (_went_quiet(feats[k]), scores[k]))
        seen.add(nxt)
        best = nxt
    return best


def _downstream_graph(sig):
    """caller -> callees, from the topology graph AND the observed baseline spans.

    The shipped topology.json is derived from spanmetrics and jaeger peer records
    and is not complete: the entry proxy's edge to the service behind it is absent
    from it, while the baseline traces show that call plainly. Taking the union
    keeps the graph's breadth without inheriting its blind spots, and the trace
    side is observed evidence from this pack rather than a second opinion about
    what the architecture is supposed to look like.
    """
    out = {}
    for e in sig.pack.topology.get("edges", []):
        out.setdefault(e["from"], set()).add(e["to"])
    for callee, d in sig.edge_stats("baseline").items():
        for caller in (d.get("callers") or {}):
            out.setdefault(caller, set()).add(callee)
    return out


def _is_anomalous(f):
    if not f:
        return False
    if f.get("memory") and f["memory"]["leaking"]:
        return True
    if f["self_errors_inject"] > f["self_errors_baseline"]:
        return True
    cb, ci = f["caller_p50_baseline"], f["caller_p50_inject"]
    if cb and ci and ci >= LATENCY_RATIO * cb and (ci - cb) >= LATENCY_MIN_DELTA_MS:
        return True
    if _went_quiet(f):
        act = f.get("caller_activity")
        return act is None or act >= STARVED_CALLER_FRAC or f.get("caller_distress", False)
    return False


def classify(sig, svc, feats):
    """Fault type for the localised service, rules applied in the documented order."""
    f = feats[svc]
    why = []

    # 1. resource first -- a leak looks like latency, so it must be ruled out first
    mem = f["memory"]
    if mem and mem["leaking"]:
        why.append(f"container_memory_mib {mem['first']:.1f} -> {mem['last']:.1f} MiB "
                   f"(+{mem['growth']:.1f}, threshold {mem['threshold']:.1f})")
        return "mem_leak", why

    rr, sr = f["rate_ratio"], f["span_ratio"]
    quiet = _went_quiet(f)

    # 2. traffic gone -> crash or blackhole, split on loud vs quiet
    if quiet:
        # Loud-vs-quiet on error counts alone does not separate these two here.
        # The pack is the harvest view: once the fault is lifted the caller's
        # backlog drains and its timeouts land inside the window, so a blackhole
        # ends up with plenty of caller errors too. What does separate them is
        # what the caller was DOING while the fault was on -- a dropped packet
        # leaves it blocked, a dead process refuses it immediately:
        #
        #   caller blocked for seconds  -> packets are being swallowed  -> blackhole
        #   caller returns straight away -> nothing is listening        -> crash
        #
        # The gate is the harness's own step-arm floor (decision 023): 1000 ms of
        # median caller time is a wait no healthy call makes. Only the absolute
        # arm is used, not the step arm's 100x relative one -- these callers have
        # millisecond baselines, so 100x is a few hundred ms, which a crash's
        # own error path can reach without anybody ever being blocked.
        waited = f["max_caller_p50_inject"] is not None and \
            f["max_caller_p50_inject"] >= CALLER_WAIT_MS
        # backlog replay: a blackhole's retransmits flush after the rule is
        # dropped, so the recover window overshoots baseline; a restarting
        # container does not (fingerprints.md, "撤除后的积压回放").
        er = sig.edge_stats("recover").get(svc, {})
        eb = sig.edge_stats("baseline").get(svc, {})
        rebound = None
        if eb.get("spans"):
            rebound = er.get("spans", 0) / eb["spans"]
        why.append(f"own traffic {f['rate_baseline']} -> {f['rate_inject']} /s "
                   f"(ratio {rr if rr is not None else sr}), upstream errors "
                   f"{f['upstream_errors_baseline']} -> {f['upstream_errors_inject']}")
        why.append(f"max caller p50 during fault {f['max_caller_p50_inject']} ms "
                   f"(wait gate {CALLER_WAIT_MS} ms)")
        if waited:
            if rebound is not None and rebound >= 1.0:
                why.append(f"recover-window rebound x{rebound:.2f} >= 1: backlog replay")
            why.append("callers sat blocked on it: packets dropped")
            return "blackhole", why
        why.append("callers were refused immediately: process gone")
        return "crash", why

    # 3. self errors while still serving -> misconfig
    if f["self_errors_inject"] > f["self_errors_baseline"]:
        why.append(f"own server error spans {f['self_errors_baseline']} -> {f['self_errors_inject']} "
                   f"while traffic held ({f['rate_baseline']} -> {f['rate_inject']} /s)")
        return "misconfig", why

    # 4. caller-side step with no errors -> latency
    cb, ci = f["caller_p50_baseline"], f["caller_p50_inject"]
    if cb and ci and ci >= LATENCY_RATIO * cb and (ci - cb) >= LATENCY_MIN_DELTA_MS:
        why.append(f"caller-edge p50 {cb:.2f} -> {ci:.2f} ms with errors flat")
        return "latency", why

    # 5. nothing matched cleanly: fall back on the loudest alert shape
    rules = [a.get("rule") for a in sig.alerts() if a.get("service") == svc]
    if any(r in ("error_rate_jump", "method_error_rate_jump") for r in rules):
        why.append(f"fallback: error-shaped alerts on itself ({rules})")
        return "misconfig", why
    why.append(f"fallback: latency-shaped or no alerts ({rules})")
    return "latency", why


def run(card_id, evidence_root=None):
    sig = Signals(card_id, evidence_root=evidence_root)
    svc, scores, feats = localise(sig)
    assert_no_card_id_in_features(card_id, (scores, feats))
    ft, why = classify(sig, svc, feats)
    return {
        "card_id": card_id,
        "answer": {"service": svc, "fault_type": ft},
        "steps": 1,
        "cost_usd": 0.0,
        "terminated": "submit",
        "rationale": why,
        "scores": scores,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--card-id")
    ap.add_argument("--cards", help="comma-separated card ids")
    ap.add_argument("--out")
    ap.add_argument("--evidence-root")
    a = ap.parse_args()
    ids = [a.card_id] if a.card_id else (a.cards.split(",") if a.cards else [])
    if not ids:
        ap.error("--card-id or --cards required")
    out = []
    for c in ids:
        r = run(c, evidence_root=a.evidence_root)
        out.append(r)
        print(f"{c}: {r['answer']['service']}/{r['answer']['fault_type']}")
    if a.out:
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        with open(a.out, "w") as f:
            json.dump(out, f, indent=1, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
