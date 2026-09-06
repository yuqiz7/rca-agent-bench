#!/usr/bin/env python3
"""tracing.py -- OpenTelemetry spans for the agent loop, off by default.

WHERE THE SPANS GO, AND WHY NOT THE TESTBED
-------------------------------------------
Never to the demo's collector (4317/4318) or its Jaeger (16686). A batch snapshots
that Jaeger into every evidence pack; a service called `rca-agent` appearing in
/api/services would land in topology.json and traces.json and become part of the
ground truth the agent is later asked to diagnose. The evaluator would be inside
the thing being evaluated.

Two sinks instead, both outside the testbed:

  file    artifacts/agent_runs/<run-id>/traces.jsonl, one JSON object per span.
          This is the evidence artefact -- diffable, reviewable, no daemon needed,
          and it survives after any container is gone.
  otlp    a SEPARATE Jaeger all-in-one from docker-compose.agent-obs.yml, on
          16687/4327/4328 so nothing collides with the testbed. This is the demo
          artefact. It is optional and its absence is not an error.

DEFAULT OFF
-----------
Tracing is opt-in (`--trace`, or tracing.enabled in config.yaml). With it off this
module imports no OpenTelemetry package and every span call is a no-op context
manager, so an eval run costs exactly what it did before. If the packages are
missing when it is switched ON, that is reported once and the run continues
untraced -- a missing observability dependency must never fail an evaluation.

THE GRADE PROBLEM
-----------------
run_agent must not be able to grade: it never imports cards.py and never sees the
answer (see leak_check). So top-1 / service-only cannot be attributes on the card
span, which has already ended by the time run_eval grades. Instead the card span's
context is kept in _CARD_CTX and run_eval calls grade_span(), which opens a short
child span under the finished parent. The parent-child link is real, and the
answer still never reaches the module that could act on it.
"""
import json
import os
import sys
import time
from contextlib import contextmanager

SERVICE_NAME = "rca-agent"

_state = {
    "on": False,          # tracing actually active (enabled AND imports worked)
    "tracer": None,
    "provider": None,
    "file": None,         # path to traces.jsonl, or None
    "warned": False,
}
_CARD_CTX = {}            # card_id -> opentelemetry Context of that card's span


@contextmanager
def _noop(*_a, **_kw):
    yield None


class _JsonlSpanExporter:
    """Writes one span per line. Deliberately not a Jaeger/OTLP file format:
    the point is that a human and a diff can both read it."""

    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._path = path
        self._fh = open(path, "a", encoding="utf-8")

    def export(self, spans):
        from opentelemetry.sdk.trace.export import SpanExportResult
        for s in spans:
            ctx = s.get_span_context()
            rec = {
                "name": s.name,
                "trace_id": f"{ctx.trace_id:032x}",
                "span_id": f"{ctx.span_id:016x}",
                "parent_span_id": (f"{s.parent.span_id:016x}" if s.parent else None),
                "start_unix_nano": s.start_time,
                "end_unix_nano": s.end_time,
                "duration_ms": round((s.end_time - s.start_time) / 1e6, 3),
                "status": s.status.status_code.name if s.status else None,
                "attributes": dict(s.attributes or {}),
                "events": [{"name": e.name, "attributes": dict(e.attributes or {})}
                           for e in (s.events or [])],
            }
            self._fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        self._fh.flush()
        return SpanExportResult.SUCCESS

    def shutdown(self):
        try:
            self._fh.close()
        except Exception:                                      # noqa: BLE001
            pass

    def force_flush(self, timeout_millis=30000):               # noqa: ARG002
        self._fh.flush()
        return True


def start(run_id, enabled, cfg=None, out_dir=None):
    """Turn tracing on for this process. Safe to call when enabled is False."""
    if not enabled:
        return False
    cfg = cfg or {}
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    except ImportError as exc:
        # An evaluation must not fail because an observability package is absent.
        print(f"tracing: disabled ({exc}); install opentelemetry-sdk to enable",
              file=sys.stderr)
        return False

    resource = Resource.create({
        "service.name": cfg.get("service_name") or SERVICE_NAME,
        "service.version": cfg.get("service_version") or "v1.1",
        "rca.run_id": run_id,
    })
    provider = TracerProvider(resource=resource)

    if cfg.get("file", True) and out_dir:
        path = os.path.join(out_dir, "traces.jsonl")
        # Simple, not Batch: a crashed run should still leave the spans it finished.
        provider.add_span_processor(SimpleSpanProcessor(_JsonlSpanExporter(path)))
        _state["file"] = path

    endpoint = cfg.get("otlp_endpoint")
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter)
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        except ImportError as exc:
            print(f"tracing: OTLP exporter unavailable ({exc}); file sink only",
                  file=sys.stderr)

    _state.update(on=True, provider=provider,
                  tracer=provider.get_tracer("rca.agent"))
    return True


def shutdown():
    p = _state.get("provider")
    if p is not None:
        try:
            p.shutdown()
        except Exception:                                      # noqa: BLE001
            pass
    _state.update(on=False, provider=None, tracer=None)


def is_on():
    return bool(_state["on"])


def file_path():
    return _state["file"]


def _set(span, attrs):
    if span is None:
        return
    for k, v in attrs.items():
        if v is None:
            continue
        span.set_attribute(k, v if isinstance(v, (str, bool, int, float)) else str(v))


@contextmanager
def card(card_id, run_id, model, config_name):
    """Root span for one card. Records the card's own context for grade_span()."""
    if not _state["on"]:
        yield None
        return
    from opentelemetry import trace
    with _state["tracer"].start_as_current_span("card") as span:
        _set(span, {"rca.card_id": card_id, "rca.run_id": run_id,
                    "rca.model": model, "rca.config": config_name})
        _CARD_CTX[card_id] = trace.set_span_in_context(span)
        yield span


def finish_card(span, *, terminated, steps, cost_usd, wall_s, counters):
    _set(span, {
        "rca.terminated": terminated,
        "rca.steps": steps,
        "rca.cost_usd": round(cost_usd, 6),
        "rca.wall_s": round(wall_s, 3),
        "rca.counters.api_calls": counters.get("api_calls"),
        "rca.counters.tool_calls": counters.get("tool_calls"),
        "rca.counters.validation_rejects": counters.get("validation_rejects"),
        "rca.counters.tool_retries": counters.get("tool_retries"),
        "rca.counters.nudges": counters.get("nudges"),
    })


def grade_span(card_id, top1_ok, service_ok):
    """A child of the finished card span, opened by the grader.

    Separate span rather than an attribute because run_agent -- which owns the card
    span -- is structurally forbidden from knowing the answer.
    """
    if not _state["on"]:
        return
    ctx = _CARD_CTX.get(card_id)
    if ctx is None:
        return
    with _state["tracer"].start_as_current_span("grade", context=ctx) as span:
        _set(span, {"rca.card_id": card_id,
                    "rca.grade.top1_ok": bool(top1_ok),
                    "rca.grade.service_ok": bool(service_ok)})


@contextmanager
def step(n):
    if not _state["on"]:
        yield None
        return
    with _state["tracer"].start_as_current_span(f"step {n}") as span:
        _set(span, {"rca.step": n})
        yield span


@contextmanager
def model_call(model):
    if not _state["on"]:
        yield None
        return
    t0 = time.time()
    with _state["tracer"].start_as_current_span("model.call") as span:
        _set(span, {"rca.model": model})
        try:
            yield span
        finally:
            _set(span, {"rca.latency_s": round(time.time() - t0, 3)})


def record_model_result(span, *, usage, step_cost_usd, stop_reason):
    _set(span, {
        "rca.tokens.input": usage.get("input"),
        "rca.tokens.output": usage.get("output"),
        "rca.tokens.cache_write": usage.get("cache_write"),
        "rca.tokens.cache_read": usage.get("cache_read"),
        "rca.step_cost_usd": round(step_cost_usd, 6),
        "rca.stop_reason": stop_reason,
    })


@contextmanager
def tool_call(name, tool_input):
    if not _state["on"]:
        yield None
        return
    with _state["tracer"].start_as_current_span(f"tool.{name}") as span:
        # Argument digest, not the arguments: a tool input can carry a long window
        # or a filter string, and the span file is meant to stay readable.
        try:
            digest = json.dumps(tool_input, sort_keys=True, ensure_ascii=False,
                                default=str)
        except Exception:                                      # noqa: BLE001
            digest = str(tool_input)
        _set(span, {"rca.tool": name, "rca.tool.args": digest[:300],
                    "rca.tool.args_len": len(digest)})
        yield span


def record_tool_result(span, *, ok, result_bytes=None, validation_reject=False,
                       retried=False, error=None):
    _set(span, {
        "rca.tool.ok": bool(ok),
        "rca.tool.result_bytes": result_bytes,
        "rca.tool.validation_reject": bool(validation_reject),
        "rca.tool.retried": bool(retried),
        "rca.tool.error": (error or "")[:200] or None,
    })
