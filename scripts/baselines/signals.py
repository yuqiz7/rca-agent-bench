#!/usr/bin/env python3
"""signals.py -- window-scoped features both baselines read, from one evidence pack.

Same data surface as the agent's six tools: everything here comes out of
EvidencePack, which can only see evidence/<card>/. scenarios/ (and therefore the
ground truth) is unreachable from this module by construction, exactly as it is
for scripts/agent/. card_id is used to address the pack and never as a feature --
see assert_no_card_id_in_features.

The windows come from the pack's own manifest (baseline / inject / recover), which
is the same partition the detector and the agent-visible alerts were computed on.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent"))

from evidence_tools import EvidencePack, TARGET_ENUM  # noqa: E402

# Services that sit at the entrance and therefore observe every fault downstream
# of them. They are legal answers, so they are never excluded -- only discounted
# when the evidence naming them is observer-side (decision 024's prompt makes the
# same point in prose).
OBSERVER_SERVICES = ("frontend", "frontend-proxy")

# Reused verbatim from the harness so the baseline is judged on the same lines the
# cards were built on, rather than on constants tuned to this card set.
SILENCE_CEILING_FRAC = 0.1      # run_batch: during rate <= baseline x 0.1
MEMLEAK_GROWTH_FLOOR_MIB = 10.0  # run_batch: MEMLEAK_GROWTH_FLOOR_MIB
MEMLEAK_GROWTH_FRAC = 0.15       # run_batch: MEMLEAK_GROWTH_FRAC
LATENCY_RATIO = 2.0              # detector rule 7 ratio arm
LATENCY_MIN_DELTA_MS = 100.0     # detector rule 7 absolute floor


def _ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _span_start_s(sp):
    return sp["start"] / 1e6


def _pct(vals, q):
    if not vals:
        return None
    v = sorted(vals)
    i = min(len(v) - 1, max(0, int(round(q * (len(v) - 1)))))
    return v[i]


class Signals:
    """Per-service features over the pack's baseline / inject / recover windows."""

    def __init__(self, card_id, evidence_root=None):
        self.pack = EvidencePack(card_id, **({"evidence_root": evidence_root} if evidence_root else {}))
        w = self.pack.manifest["windows"]
        self.win = {k: (_ts(w[k]["start"]), _ts(w[k]["end"])) for k in ("baseline", "inject", "recover")
                    if k in w}
        self._metrics = {q["name"]: q.get("series", {}) for q in self.pack.metrics["queries"]}
        self._parent = {s["spanID"]: s for s in self.pack.traces["spans"]}
        self.services = sorted(self.pack.known_services)

    # ---- metrics ----------------------------------------------------------

    def series(self, metric, key):
        return self._metrics.get(metric, {}).get(key, [])

    def mean_in(self, metric, key, window):
        a, b = self.win[window]
        vals = [v for t, v in self.series(metric, key) if a <= t <= b and v is not None]
        return (sum(vals) / len(vals)) if vals else None

    def memory_growth(self, svc):
        """Leak test over the inject window: sustained climb, not a GC sawtooth.

        The harness's own mem_leak judge is first-to-last growth against
        max(10 MiB, 0.15 x first). That is sound where it runs -- it only ever
        judges the one target the leak is injected into -- but it is not safe to
        point at every service in the pack: a garbage-collected runtime that sits
        flat and then allocates once shows the same first-to-last delta as a leak.
        Observed: a Node service going 83, 83, 83, 83, 83, 83, 83, 114 MiB clears
        the harness threshold on the strength of its last sample alone.

        So the harness threshold is kept and two shape conditions are added, both
        of which a leak passes and a sawtooth fails:

          ends_high   the series finishes within 10% of its own peak -- it climbed
                      and stayed, rather than spiking and coming back down;
          sustained   at least half the samples already sit above first + thr/2 --
                      the rise is spread across the window, not carried by one
                      sample at the edge.
        """
        a, b = self.win["inject"]
        pts = [(t, v) for t, v in self.series("container_memory_mib", svc)
               if a <= t <= b and v is not None]
        if len(pts) < 4:
            return None
        vals = [v for _, v in pts]
        first, last, peak = vals[0], vals[-1], max(vals)
        thr = max(MEMLEAK_GROWTH_FLOOR_MIB, MEMLEAK_GROWTH_FRAC * first)
        over = sum(1 for v in vals if v >= first + thr / 2.0)
        sustained_frac = over / len(vals)
        ends_high = last >= 0.9 * peak
        return {"first": first, "last": last, "max": peak,
                "growth": last - first, "threshold": thr,
                "sustained_frac": round(sustained_frac, 3), "ends_high": ends_high,
                "leaking": (last - first) >= thr and ends_high and sustained_frac >= 0.5}

    # ---- traces -----------------------------------------------------------

    def _spans_in(self, window):
        a, b = self.win[window]
        return [s for s in self.pack.traces["spans"] if a <= _span_start_s(s) <= b]

    def edge_stats(self, window):
        """Per-callee: span count, error count, p50 ms, split by caller service."""
        out = {}
        for s in self._spans_in(window):
            callee = s["service"]
            d = out.setdefault(callee, {"spans": 0, "errors": 0, "durs": [],
                                        "callers": {}, "caller_errors": 0,
                                        "caller_spans": 0, "caller_durs": []})
            d["spans"] += 1
            d["durs"].append(s["duration"] / 1000.0)
            if s.get("status") == "ERROR":
                d["errors"] += 1
        # caller side: a span whose parent belongs to a different service is the
        # callee end of an edge; the parent span is what the caller measured.
        for s in self._spans_in(window):
            par = self._parent.get(s.get("parentSpanID"))
            if not par or par["service"] == s["service"]:
                continue
            d = out.setdefault(s["service"], {"spans": 0, "errors": 0, "durs": [],
                                              "callers": {}, "caller_errors": 0,
                                              "caller_spans": 0, "caller_durs": []})
            d["callers"][par["service"]] = d["callers"].get(par["service"], 0) + 1
            d["caller_spans"] += 1
            d["caller_durs"].append(par["duration"] / 1000.0)
            if par.get("status") == "ERROR":
                d["caller_errors"] += 1
        for d in out.values():
            d["p50_ms"] = _pct(d["durs"], 0.5)
            d["caller_p50_ms"] = _pct(d["caller_durs"], 0.5)
            d.pop("durs"), d.pop("caller_durs")
        return out

    # ---- logs -------------------------------------------------------------

    def log_error_counts(self, window):
        a, b = self.win[window]
        out = {}
        for r in self.pack.logs:
            t = r["_t"]
            if not (a <= t <= b):
                continue
            if str(r.get("severity", "")).lower() in ("error", "fatal"):
                out[r.get("service")] = out.get(r.get("service"), 0) + 1
        return out

    # ---- alerts -----------------------------------------------------------

    def alerts(self):
        """The alert list exactly as the agent sees it -- card_id never read."""
        return list(self.pack.alerts.get("alerts") or [])


def assert_no_card_id_in_features(card_id, features):
    """Fail closed: a feature dict that embeds the card id would leak the answer."""
    blob = repr(features)
    if card_id in blob:
        raise AssertionError(f"{card_id}: card id leaked into the feature set")
    return True
