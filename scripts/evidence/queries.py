#!/usr/bin/env python3
"""queries.py -- the fixed PromQL set every evidence pack uses (decision 021).

One set for all 76 cards. A card never gets a query tailored to its target: that
would leak the answer into the evidence pack (fault_schema §4, decision 005).
Everything here is per-service or per-container and blind to which card is running.

Why raw counters *and* rate():
  - `rate()` series are what a human reads off a dashboard, so they go in the pack;
  - but detect.py computes counts and zero-traffic from the **raw counter delta**,
    the method decision 013 / three_signals.py settled on. rate()[1m] smears a
    hard stop across a full minute, and a counter that stops advancing is the
    crisper signal for "traffic dropped to zero".

Metric choices follow three_signals.py:
  - traces_span_metrics_* : spanmetrics connector, derived from spans, present for
    every target regardless of language (cart is .NET and has no rpc_server_*).
  - container_memory_usage_total_bytes / container_cpu_utilization_ratio :
    docker_stats receiver, label container_name -- the same read path the
    resource-audit probes use.
"""

STEP_S = 15          # range-query step, decision 013 (SDK export interval 15s)
RATE_WINDOW = "1m"   # 4 samples at a 15s export interval

SERVER = 'span_kind="SPAN_KIND_SERVER"'
ERR = 'status_code="STATUS_CODE_ERROR"'

QUERIES = [
    # ── per service ──────────────────────────────────────────────────────
    {
        "name": "req_rate_per_s",
        "group_by": "service_name",
        "unit": "requests/s",
        "promql": f'sum by (service_name) (rate(traces_span_metrics_calls_total{{{SERVER}}}[{RATE_WINDOW}]))',
        "note": "server-kind span rate; dashboard view of request rate",
    },
    {
        "name": "error_rate_per_s",
        "group_by": "service_name",
        "unit": "errors/s",
        "promql": f'sum by (service_name) (rate(traces_span_metrics_calls_total{{{SERVER},{ERR}}}[{RATE_WINDOW}]))',
        "note": "server-kind span rate restricted to STATUS_CODE_ERROR",
    },
    {
        "name": "p95_latency_ms",
        "group_by": "service_name",
        "unit": "ms",
        "promql": ('histogram_quantile(0.95, sum by (service_name, le) '
                   f'(rate(traces_span_metrics_duration_milliseconds_bucket{{{SERVER}}}[{RATE_WINDOW}])))'),
        "note": "spanmetrics latency histogram, server-kind",
    },
    {
        "name": "calls_total",
        "group_by": "service_name",
        "unit": "count (cumulative)",
        "promql": f'sum by (service_name) (traces_span_metrics_calls_total{{{SERVER}}})',
        "note": "raw counter; detect.py takes request counts from its delta",
    },
    {
        "name": "errors_total",
        "group_by": "service_name",
        "unit": "count (cumulative)",
        "promql": f'sum by (service_name) (traces_span_metrics_calls_total{{{SERVER},{ERR}}})',
        "note": "raw counter; detect.py takes error counts from its delta",
    },
    # ── per container ────────────────────────────────────────────────────
    {
        "name": "container_memory_mib",
        "group_by": "container_name",
        "unit": "MiB",
        "promql": "container_memory_usage_total_bytes / 1048576",
        "note": "docker_stats receiver; same metric as three_signals.collect_memory",
    },
    {
        "name": "container_cpu_ratio",
        "group_by": "container_name",
        "unit": "ratio of one host CPU",
        "promql": "container_cpu_utilization_ratio",
        "note": "docker_stats receiver",
    },
]

BY_NAME = {q["name"]: q for q in QUERIES}
