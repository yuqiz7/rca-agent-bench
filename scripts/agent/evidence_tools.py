#!/usr/bin/env python3
"""evidence_tools.py -- the agent's six read-only views onto one evidence pack.

Narrow on purpose. The pack is ~10 MB per card (9k log lines, 27k spans, 205
metric series); a tool that could return "the file" would put the whole pack in
context on the first call and make every later step cost more than the answer is
worth. So every tool filters first and summarises second, and every return size is
capped by config.yaml. traces_query in particular never returns spans in bulk --
it returns per-(service, operation) aggregates plus a handful of samples.

Reads are confined to evidence/<card_id>/ (plus the shared topology graph that the
card's topology.json points at by hash). Nothing here can see scenarios/.

Validation is a first-class result, not an exception: an unknown service name or
an out-of-window timestamp comes back as {"error": ...} so the caller can feed the
message to the model and count the intercept. Only genuine bugs raise.
"""
import bisect
import json
import os
import re
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EVIDENCE_ROOT = os.path.join(REPO, "evidence")

# fault_schema §2 target_enum: the 16 services a card may be built on, and hence
# the only legal answers to submit(). Query tools use the *observed* service set
# instead (see EvidencePack.known_services) -- load-generator and frontend-web are
# not legal answers but are perfectly legal things to look at.
TARGET_ENUM = (
    "ad", "astronomy-db", "cart", "checkout", "currency", "email", "flagd",
    "frontend", "frontend-proxy", "image-provider", "payment", "product-catalog",
    "quote", "recommendation", "shipping", "valkey-cart",
)

# fault_schema §3.
FAULT_TYPES = ("crash", "latency", "blackhole", "misconfig", "mem_leak")

SEVERITIES = ("trace", "debug", "info", "warn", "error", "fatal")


class ToolError(Exception):
    """A rejected call: bad parameters, not a broken tool."""


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s, field):
    if not isinstance(s, str):
        raise ToolError(f"{field} must be an ISO8601 string like 2026-08-27T19:24:03Z")
    t = s.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        raise ToolError(f"{field}={s!r} is not ISO8601; expected e.g. 2026-08-27T19:24:03Z")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _pct(sorted_vals, q):
    if not sorted_vals:
        return None
    i = min(len(sorted_vals) - 1, int(round(q * (len(sorted_vals) - 1))))
    return sorted_vals[i]


def _round(x, n=3):
    return None if x is None else round(x, n)


class EvidencePack:
    """One card's pack, parsed lazily and kept in memory for the whole run.

    Parsing traces.json costs ~1 s and 7 MB; an agent makes several traces_query
    calls per card, so it is loaded once and reused rather than re-read per call.
    """

    def __init__(self, card_id, evidence_root=EVIDENCE_ROOT, limits=None):
        self.card_id = card_id
        self.dir = os.path.join(evidence_root, card_id)
        self.root = evidence_root
        if not os.path.isdir(self.dir):
            raise FileNotFoundError(f"no evidence pack at {self.dir}")
        self.limits = dict(DEFAULT_LIMITS, **(limits or {}))
        self._logs = None
        self._metrics = None
        self._traces = None
        self._span_by_id = None
        self._topology = None
        self._alerts = None
        self._known = None
        with open(os.path.join(self.dir, "manifest.json")) as f:
            self.manifest = json.load(f)
        w = self.manifest["window"]
        self.window_start = _parse_iso(w["start"], "manifest.window.start")
        self.window_end = _parse_iso(w["end"], "manifest.window.end")

    # ---- lazy loaders -----------------------------------------------------

    @property
    def logs(self):
        if self._logs is None:
            rows = []
            with open(os.path.join(self.dir, "logs.jsonl")) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    r = json.loads(line)
                    r["_t"] = _parse_iso(r["ts"], "log.ts")
                    rows.append(r)
            rows.sort(key=lambda r: r["_t"])
            self._logs = rows
        return self._logs

    @property
    def metrics(self):
        if self._metrics is None:
            with open(os.path.join(self.dir, "metrics.json")) as f:
                self._metrics = json.load(f)
        return self._metrics

    @property
    def traces(self):
        if self._traces is None:
            with open(os.path.join(self.dir, "traces.json")) as f:
                self._traces = json.load(f)
            # Parent lookup, so a caller->callee edge filter is possible at all:
            # a span records its own service, never the caller's.
            self._span_by_id = {s["spanID"]: s for s in self._traces["spans"]}
        return self._traces

    @property
    def topology(self):
        if self._topology is None:
            with open(os.path.join(self.dir, "topology.json")) as f:
                doc = json.load(f)
            ref = doc.get("ref")
            if ref:
                with open(os.path.join(self.root, ref)) as f:
                    doc = json.load(f)
            self._topology = doc
        return self._topology

    @property
    def alerts(self):
        if self._alerts is None:
            with open(os.path.join(self.dir, "alerts.json")) as f:
                self._alerts = json.load(f)
        return self._alerts

    @property
    def known_services(self):
        """Every service name that appears anywhere in this pack.

        The validation set for query tools. Built from the evidence rather than
        from target_enum because the pack legitimately contains non-target
        services (load-generator, frontend-web, otelcol-contrib) and rejecting a
        query for one of those would be a false intercept.
        """
        if self._known is None:
            names = set()
            for q in self.metrics["queries"]:
                for key in q.get("series", {}):
                    names.add(key.split("|", 1)[0])
            names.update(self.traces["traces_per_service"])
            names.update(s["service"] for s in self.traces["spans"])
            for e in self.topology.get("edges", []):
                names.add(e["from"])
                names.add(e["to"])
            names.update(r.get("service") for r in self.logs)
            names.discard(None)
            self._known = names
        return self._known

    # ---- shared validation ------------------------------------------------

    def _check_service(self, name, field="service"):
        if name is None:
            return None
        if not isinstance(name, str):
            raise ToolError(f"{field} must be a string")
        if name not in self.known_services:
            near = sorted(n for n in self.known_services if name.lower() in n.lower())
            hint = f" did you mean {near}?" if near else ""
            raise ToolError(
                f"unknown {field} {name!r}; not present in this evidence pack."
                f"{hint} Call topology to see the service graph.")
        return name

    def _check_window(self, start, end):
        s = self.window_start if start is None else _parse_iso(start, "start")
        e = self.window_end if end is None else _parse_iso(end, "end")
        if e < s:
            raise ToolError("end is before start")
        if e < self.window_start or s > self.window_end:
            raise ToolError(
                f"requested window is outside the evidence pack, which covers "
                f"{_iso(self.window_start)} .. {_iso(self.window_end)}")
        return max(s, self.window_start), min(e, self.window_end)

    def _check_limit(self, limit, default, cap, field="limit"):
        if limit is None:
            return default
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise ToolError(f"{field} must be an integer")
        if not 1 <= limit <= cap:
            raise ToolError(f"{field} must be between 1 and {cap}")
        return limit

    # ---- the six read tools ----------------------------------------------

    def logs_search(self, keyword=None, service=None, severity=None,
                    start=None, end=None, limit=None):
        service = self._check_service(service)
        s, e = self._check_window(start, end)
        limit = self._check_limit(limit, self.limits["logs_default_limit"],
                                  self.limits["logs_max_limit"])
        if severity is not None:
            if not isinstance(severity, str) or severity.lower() not in SEVERITIES:
                raise ToolError(f"severity must be one of {list(SEVERITIES)}")
            severity = severity.lower()
        pat = None
        if keyword is not None:
            if not isinstance(keyword, str) or not keyword.strip():
                raise ToolError("keyword must be a non-empty string")
            try:
                pat = re.compile(re.escape(keyword.strip()), re.IGNORECASE)
            except re.error as exc:
                raise ToolError(f"bad keyword: {exc}")

        hits, by_service, by_severity = [], {}, {}
        for r in self.logs:
            if not (s <= r["_t"] <= e):
                continue
            if service and r.get("service") != service:
                continue
            if severity and (r.get("severity") or "").lower() != severity:
                continue
            body = r.get("body") or ""
            if pat and not pat.search(body):
                continue
            by_service[r.get("service")] = by_service.get(r.get("service"), 0) + 1
            sev = (r.get("severity") or "?").lower()
            by_severity[sev] = by_severity.get(sev, 0) + 1
            hits.append(r)

        cap = self.limits["logs_body_chars"]
        # Return the tail: for a fault the interesting lines are the ones after
        # onset, and a head-truncated result would show only warmup chatter.
        shown = hits[-limit:]
        return {
            "window": {"start": _iso(s), "end": _iso(e)},
            "matched": len(hits),
            "returned": len(shown),
            "counts_by_service": dict(sorted(by_service.items(), key=lambda kv: -kv[1])[:15]),
            "counts_by_severity": by_severity,
            "records": [{
                "ts": r["ts"],
                "service": r.get("service"),
                "severity": r.get("severity"),
                "body": (r.get("body") or "")[:cap],
            } for r in shown],
        }

    def metrics_query(self, metric=None, service=None, start=None, end=None,
                      aggregate="summary", max_points=None):
        names = [q["name"] for q in self.metrics["queries"]]
        if metric not in names:
            raise ToolError(f"unknown metric {metric!r}; available metrics: {names}")
        if aggregate not in ("summary", "series"):
            raise ToolError("aggregate must be 'summary' or 'series'")
        s, e = self._check_window(start, end)
        max_points = self._check_limit(max_points, self.limits["metrics_max_points"],
                                       self.limits["metrics_max_points"], "max_points")
        q = next(x for x in self.metrics["queries"] if x["name"] == metric)
        if service is not None:
            self._check_service(service)

        out = []
        for key, points in q.get("series", {}).items():
            head = key.split("|", 1)[0]
            if service and head != service:
                continue
            pts = [(t, v) for t, v in points if s <= t <= e and v is not None]
            if not pts:
                continue
            vals = [v for _, v in pts]
            row = {
                "key": key,
                "points": len(pts),
                "min": _round(min(vals)),
                "max": _round(max(vals)),
                "mean": _round(sum(vals) / len(vals)),
                "first": _round(vals[0]),
                "last": _round(vals[-1]),
            }
            if aggregate == "series":
                step = max(1, len(pts) // max_points)
                row["series"] = [[_iso(t), _round(v)] for t, v in pts[::step]][:max_points]
            out.append(row)
        if service and not out:
            raise ToolError(
                f"metric {metric!r} has no series for service {service!r}; "
                f"it groups by {q.get('group_by')}")
        out.sort(key=lambda r: r["key"])
        return {
            "metric": metric,
            "unit": q.get("unit"),
            "promql": q.get("promql"),
            "group_by": q.get("group_by"),
            "window": {"start": _iso(s), "end": _iso(e)},
            "step_s": self.metrics.get("step_s"),
            "series_count": len(out),
            "series": out[:60],
        }

    def traces_query(self, service=None, caller=None, operation_contains=None,
                     status=None, start=None, end=None, samples=None):
        service = self._check_service(service, "service")
        caller = self._check_service(caller, "caller")
        s, e = self._check_window(start, end)
        samples = self._check_limit(samples, self.limits["traces_default_samples"],
                                    self.limits["traces_max_samples"], "samples")
        if status is not None:
            if not isinstance(status, str) or status.upper() not in ("ERROR", "OK", "UNSET", "ANY"):
                raise ToolError("status must be one of ERROR, OK, UNSET, ANY")
            status = status.upper()
        if operation_contains is not None and not isinstance(operation_contains, str):
            raise ToolError("operation_contains must be a string")

        self.traces  # noqa: B018 - force the parent index to build
        groups, matched = {}, []
        for sp in self._traces["spans"]:
            t = sp["start"] / 1e6
            if not (s <= t <= e):
                continue
            if service and sp["service"] != service:
                continue
            if caller:
                parent = self._span_by_id.get(sp.get("parentSpanID"))
                if parent is None or parent["service"] != caller:
                    continue
            op = sp.get("operation") or ""
            if operation_contains and operation_contains.lower() not in op.lower():
                continue
            st = sp.get("status") or "UNSET"
            if status and status != "ANY" and st != status:
                continue
            g = groups.setdefault((sp["service"], op), {"n": 0, "err": 0, "d": []})
            g["n"] += 1
            g["err"] += 1 if st == "ERROR" else 0
            g["d"].append(sp["duration"] / 1000.0)
            matched.append(sp)

        rows = []
        for (svc, op), g in groups.items():
            d = sorted(g["d"])
            rows.append({
                "service": svc, "operation": op, "count": g["n"], "errors": g["err"],
                "p50_ms": _round(_pct(d, 0.5)), "p95_ms": _round(_pct(d, 0.95)),
                "max_ms": _round(d[-1]),
            })
        rows.sort(key=lambda r: (-r["errors"], -r["count"]))
        matched.sort(key=lambda sp: sp["start"])
        step = max(1, len(matched) // samples) if matched else 1
        return {
            "window": {"start": _iso(s), "end": _iso(e)},
            "matched_spans": len(matched),
            "error_spans": sum(r["errors"] for r in rows),
            "group_count": len(rows),
            "groups": rows[:self.limits["traces_top_groups"]],
            "samples": [{
                "ts": _iso(sp["start"] / 1e6),
                "service": sp["service"],
                "operation": sp.get("operation"),
                "status": sp.get("status"),
                "duration_ms": _round(sp["duration"] / 1000.0),
                "caller": (self._span_by_id.get(sp.get("parentSpanID")) or {}).get("service"),
            } for sp in matched[::step][:samples]],
        }

    def config_diff(self):
        with open(os.path.join(self.dir, "config_diff.txt")) as f:
            return {"text": f.read()}

    def topology_view(self):
        t = self.topology
        return {
            "generated_at": t.get("generated_at"),
            "method": t.get("method"),
            "edge_count": t.get("edge_count"),
            "edges": [{"from": e["from"], "to": e["to"]} for e in t.get("edges", [])],
            "unresolved_client_calls": t.get("spanmetrics_unresolved_client_calls", {}),
        }

    def alerts_view(self):
        a = self.alerts
        return {
            "window": a.get("window"),
            "rules": a.get("rules"),
            "count": len(a.get("alerts", [])),
            "alerts": a.get("alerts", []),
        }


DEFAULT_LIMITS = {
    "logs_default_limit": 30,
    "logs_max_limit": 200,
    "logs_body_chars": 300,
    "traces_default_samples": 5,
    "traces_max_samples": 20,
    "traces_top_groups": 10,
    "metrics_max_points": 40,
}

# Metric names are pack-independent (the collector runs the same 10 queries every
# time), so the enum can go straight into the schema the model sees.
METRIC_NAMES = [
    "req_rate_per_s", "error_rate_per_s", "p95_latency_ms", "calls_total",
    "errors_total", "calls_total_by_operation", "errors_total_by_operation",
    "p95_latency_ms_by_operation", "container_memory_mib", "container_cpu_ratio",
]


def tool_schemas():
    """The seven tool definitions, exactly as sent to the API.

    Card-independent by construction: nothing here is formatted with a card id, a
    service name from a card, or anything else drawn from scenarios/.
    """
    return [
        {
            "name": "logs_search",
            "description": (
                "Search the collected application logs for this incident. Filter by "
                "keyword (case-insensitive substring), service, severity, and time "
                "window. Returns match counts broken down by service and severity "
                "plus the most recent matching records."),
            "input_schema": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "case-insensitive substring to match in the log body"},
                    "service": {"type": "string", "description": "restrict to one service; must be a service that appears in this incident's telemetry"},
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                    "start": {"type": "string", "description": "ISO8601, e.g. 2026-08-27T19:24:03Z; defaults to the start of the collection window"},
                    "end": {"type": "string", "description": "ISO8601; defaults to the end of the collection window"},
                    "limit": {"type": "integer", "description": "max records to return (1-200, default 30)"},
                },
                "required": [],
            },
        },
        {
            "name": "metrics_query",
            "description": (
                "Query one collected metric over a time window. 'summary' returns "
                "min/max/mean/first/last per series; 'series' additionally returns a "
                "downsampled time series. Metrics ending in _by_operation are keyed "
                "'<service>|<operation>'."),
            "input_schema": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string", "enum": METRIC_NAMES},
                    "service": {"type": "string", "description": "restrict to one service"},
                    "start": {"type": "string", "description": "ISO8601"},
                    "end": {"type": "string", "description": "ISO8601"},
                    "aggregate": {"type": "string", "enum": ["summary", "series"], "description": "default summary"},
                    "max_points": {"type": "integer", "description": "cap on returned series points (default 40)"},
                },
                "required": ["metric"],
            },
        },
        {
            "name": "traces_query",
            "description": (
                "Aggregate distributed-tracing spans. Filter by the span's own "
                "service, by caller (the service that owns the parent span, i.e. the "
                "calling side of an edge), by operation substring, by status, and by "
                "time. Returns per-(service, operation) counts, error counts and "
                "latency percentiles, plus a few sample spans -- never the full span "
                "list."),
            "input_schema": {
                "type": "object",
                "properties": {
                    "service": {"type": "string", "description": "the service the span belongs to (callee side)"},
                    "caller": {"type": "string", "description": "the service that owns the parent span (caller side of an edge)"},
                    "operation_contains": {"type": "string"},
                    "status": {"type": "string", "enum": ["ERROR", "OK", "UNSET", "ANY"]},
                    "start": {"type": "string", "description": "ISO8601"},
                    "end": {"type": "string", "description": "ISO8601"},
                    "samples": {"type": "integer", "description": "sample spans to return (1-20, default 5)"},
                },
                "required": [],
            },
        },
        {
            "name": "config_diff",
            "description": (
                "Return the unified diff of the deployment configuration (compose "
                "environment and feature-flag file) against the pre-incident "
                "baseline snapshot, verbatim."),
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "topology",
            "description": (
                "Return the service dependency graph as a list of caller -> callee "
                "edges, derived from a healthy baseline window."),
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "alerts",
            "description": (
                "Return the full list of alerts the monitoring rules fired for this "
                "incident, with each alert's rule, baseline and observed values."),
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "submit",
            "description": (
                "Submit your final root-cause diagnosis. Exactly one service is the "
                "root cause and exactly one fault type describes it. Calling this "
                "ends the investigation -- you get no further tool calls, so make "
                "sure the evidence supports the answer before calling it."),
            "input_schema": {
                "type": "object",
                "properties": {
                    "service": {"type": "string", "enum": list(TARGET_ENUM),
                                "description": "the single service where the fault was introduced"},
                    "fault_type": {"type": "string", "enum": list(FAULT_TYPES),
                                   "description": "crash: the service's container is not running. "
                                                  "latency: the service is up and reachable but responds slowly. "
                                                  "blackhole: the service is up but its port silently drops packets, so callers see neither success nor error. "
                                                  "misconfig: the service's configuration or feature flag was changed and it returns errors. "
                                                  "mem_leak: the service's memory grows without bound."},
                },
                "required": ["service", "fault_type"],
            },
        },
    ]


READ_TOOLS = {
    "logs_search": lambda p, kw: p.logs_search(**kw),
    "metrics_query": lambda p, kw: p.metrics_query(**kw),
    "traces_query": lambda p, kw: p.traces_query(**kw),
    "config_diff": lambda p, kw: p.config_diff(),
    "topology": lambda p, kw: p.topology_view(),
    "alerts": lambda p, kw: p.alerts_view(),
}

# Parameter names each read tool accepts. An unexpected key is an intercept, not a
# TypeError: the model gets told the name is wrong and can fix it next step.
ACCEPTED_ARGS = {
    "logs_search": {"keyword", "service", "severity", "start", "end", "limit"},
    "metrics_query": {"metric", "service", "start", "end", "aggregate", "max_points"},
    "traces_query": {"service", "caller", "operation_contains", "status", "start", "end", "samples"},
    "config_diff": set(),
    "topology": set(),
    "alerts": set(),
    "submit": {"service", "fault_type"},
}


def validate_submit(kwargs):
    unexpected = sorted(set(kwargs) - ACCEPTED_ARGS["submit"])
    if unexpected:
        raise ToolError(f"submit got unexpected parameters {unexpected}; "
                        f"it takes exactly service and fault_type")
    svc, ft = kwargs.get("service"), kwargs.get("fault_type")
    if svc not in TARGET_ENUM:
        raise ToolError(f"service must be one of {list(TARGET_ENUM)}")
    if ft not in FAULT_TYPES:
        raise ToolError(f"fault_type must be one of {list(FAULT_TYPES)}")
    return svc, ft


def call_read_tool(pack, name, kwargs):
    if name not in READ_TOOLS:
        raise ToolError(f"no such tool {name!r}")
    unexpected = sorted(set(kwargs) - ACCEPTED_ARGS[name])
    if unexpected:
        raise ToolError(f"{name} got unexpected parameters {unexpected}; "
                        f"it accepts {sorted(ACCEPTED_ARGS[name])}")
    return READ_TOOLS[name](pack, kwargs)
