#!/usr/bin/env python3
"""run_agent.py -- diagnose one card from its packed evidence, via function calling.

The loop is written by hand rather than handed to the SDK's tool runner because
the four safety rails are the point of the exercise and each one needs a hook the
runner does not expose: reject a bad parameter without executing anything and feed
the message back, retry a tool that threw, stop at a step ceiling, and stop at a
dollar ceiling. Each rail keeps its own counter and every counter lands in the
result, so a bad accuracy number can be read as "the model was wrong" or "the model
never got to answer" rather than being ambiguous between them.

This module never imports scripts/scenarios/cards.py. It cannot see the answer, so
it also cannot grade -- run_eval.py does that, from the transcript this writes.
"""
import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yaml                                   # noqa: E402
import anthropic                              # noqa: E402

import leak_check                             # noqa: E402
import prompts                                # noqa: E402
from evidence_tools import (                  # noqa: E402
    EvidencePack, ToolError, call_read_tool, tool_schemas, validate_submit)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
CONFIG_PATH = os.path.join(HERE, "config.yaml")
PRICES_PATH = os.path.join(HERE, "prices.yaml")
RUNS_ROOT = os.path.join(REPO, "artifacts", "agent_runs")

# How much of a tool return is kept in the transcript. The model sees the whole
# thing; the transcript keeps a prefix so a 19-card run stays readable and small.
RESULT_SUMMARY_CHARS = 1200
# A model that stops without calling submit gets this many reminders before the run
# is written off. Two is enough to cover a stray end_turn; more would just burn
# budget on a model that has decided it is finished.
MAX_NUDGES = 2
NUDGE = ("You have not called submit yet. Call submit now with your best current "
         "hypothesis for the single service and fault type.")


def load_config(path=CONFIG_PATH):
    with open(path) as f:
        return yaml.safe_load(f)


def run_config_for(model, config):
    """config["run"], with any per-model exception from config["model_api"] applied.

    Not every model accepts every request parameter, and the ones that do not
    reject the whole request with a 400 rather than ignoring the field. Measured
    on 2026-09-06: claude-haiku-4-5 returns "adaptive thinking is not supported on
    this model" for thinking={"type": "adaptive"} and "This model does not support
    the effort parameter." for output_config.effort; claude-sonnet-5 accepts both.

    The exceptions live in config.yaml rather than in an `if model ==` here so
    that adding an arm is a config edit, and so the primary model's settings are
    never reached by this code path at all -- an unlisted model gets config["run"]
    back unchanged, byte for byte.
    """
    over = (config.get("model_api") or {}).get(model)
    if not over:
        return config["run"]
    rc = dict(config["run"])
    rc.update(over)
    return rc


def load_prices(path=PRICES_PATH):
    with open(path) as f:
        return yaml.safe_load(f)


def price_usd(prices, model, usage):
    """Dollars for one response's usage, at the rates in prices.yaml.

    Cache writes and reads are billed at their own rates, not at base input, and
    the API reports them as separate counters -- folding them into input_tokens
    would overstate a cached run by roughly the cache read discount.
    """
    p = prices["models"].get(model)
    if p is None:
        raise KeyError(f"{model} is not priced in prices.yaml; re-fetch {prices['source_url']}")
    return (usage["input"] * p["input"]
            + usage["output"] * p["output"]
            + usage["cache_write"] * p["cache_write_5m"]
            + usage["cache_read"] * p["cache_read"]) / 1e6


def _zero_usage():
    return {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}


def _add_usage(total, u):
    total["input"] += u.input_tokens or 0
    total["output"] += u.output_tokens or 0
    total["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
    total["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
    return total


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _summarise(obj):
    text = json.dumps(obj, ensure_ascii=False, default=str)
    return {"bytes": len(text), "preview": text[:RESULT_SUMMARY_CHARS],
            "truncated": len(text) > RESULT_SUMMARY_CHARS}


def run_card(card_id, model=None, config=None, prices=None, evidence_root=None,
             client=None):
    """Run one card. Returns the result dict; never raises on a model mistake."""
    config = config or load_config()
    prices = prices or load_prices()
    model = model or config["models"]["primary"]
    rc = run_config_for(model, config)

    pack = EvidencePack(card_id,
                        evidence_root=evidence_root or os.path.join(REPO, "evidence"),
                        limits=config.get("tools"))
    with open(os.path.join(pack.dir, "task.json")) as f:
        task_view = json.load(f)

    tools = tool_schemas()
    payload = prompts.build_request_payload(task_view, tools)
    # Fail closed, before a single token is spent: a leaked answer makes the whole
    # run worthless, and finding out afterwards costs the same money plus the time.
    leak_passed = leak_check.check_payload(payload, card_id, task_view)

    client = client or anthropic.Anthropic(
        max_retries=rc["api_max_retries"], timeout=rc["request_timeout_s"])

    messages = [{"role": "user", "content": payload["user"]}]
    counters = {"api_calls": 0, "tool_calls": 0, "validation_rejects": 0,
                "tool_retries": 0, "nudges": 0}
    usage = _zero_usage()
    transcript = []
    answer, terminated, error = None, None, None
    steps, nudges = 0, 0
    t0 = time.time()

    create_kwargs = {
        "model": model,
        "max_tokens": rc["max_tokens"],
        "system": payload["system"],
        "tools": tools,
    }
    # effort / thinking are omitted entirely when the model does not take them:
    # sending either to a model that rejects it fails the whole request, and
    # there is no equivalent to substitute that would keep the arms comparable.
    if rc.get("effort"):
        create_kwargs["output_config"] = {"effort": rc["effort"]}
    if rc.get("thinking") == "adaptive":
        create_kwargs["thinking"] = {"type": "adaptive"}
    if rc.get("prompt_caching"):
        # Top-level auto-caching: the system prompt and tool schemas are identical
        # on every step and the message history only grows at the end, so the whole
        # prefix is a cache hit from step two onward.
        create_kwargs["cache_control"] = {"type": "ephemeral"}

    while True:
        if steps >= rc["max_steps"]:
            terminated = "max_steps"
            break
        try:
            resp = client.messages.create(messages=messages, **create_kwargs)
        except anthropic.APIError as exc:
            terminated, error = "api_error", f"{type(exc).__name__}: {exc}"
            break
        counters["api_calls"] += 1
        steps += 1
        _add_usage(usage, resp.usage)
        cost = price_usd(prices, model, usage)

        entry = {
            "step": steps,
            "stop_reason": resp.stop_reason,
            "usage": {"input": resp.usage.input_tokens,
                      "output": resp.usage.output_tokens,
                      "cache_write": getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
                      "cache_read": getattr(resp.usage, "cache_read_input_tokens", 0) or 0},
            "cumulative_cost_usd": round(cost, 6),
            "text": "".join(b.text for b in resp.content if b.type == "text")[:1000],
            "tool_calls": [],
        }
        messages.append({"role": "assistant", "content": resp.content})

        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        results = []
        for block in tool_uses:
            counters["tool_calls"] += 1
            call = {"name": block.name, "input": block.input}
            if block.name == "submit":
                try:
                    svc, ft = validate_submit(block.input)
                except ToolError as exc:
                    counters["validation_rejects"] += 1
                    call.update(ok=False, error=str(exc))
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": f"rejected: {exc}", "is_error": True})
                    entry["tool_calls"].append(call)
                    continue
                answer = {"service": svc, "fault_type": ft}
                call.update(ok=True, result={"accepted": True})
                entry["tool_calls"].append(call)
                terminated = "submit"
                break

            out, err = None, None
            for attempt in range(rc["tool_retries"] + 1):
                try:
                    out = call_read_tool(pack, block.name, block.input)
                    break
                except ToolError as exc:
                    # A rejected parameter is the model's mistake, not a flaky tool:
                    # retrying the same call would fail identically.
                    err = str(exc)
                    counters["validation_rejects"] += 1
                    break
                except Exception as exc:                      # noqa: BLE001
                    err = f"{type(exc).__name__}: {exc}"
                    if attempt < rc["tool_retries"]:
                        counters["tool_retries"] += 1
                        continue
                    break
            if out is None:
                call.update(ok=False, error=err)
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": f"error: {err}", "is_error": True})
            else:
                call.update(ok=True, result=_summarise(out))
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": json.dumps(out, ensure_ascii=False, default=str)})
            entry["tool_calls"].append(call)

        transcript.append(entry)
        if terminated:
            break

        if cost >= rc["max_usd_per_card"]:
            terminated = "cost_cap"
            break

        if results:
            messages.append({"role": "user", "content": results})
            continue

        # No tool call this step: the model answered in prose instead of submitting.
        if nudges >= MAX_NUDGES:
            terminated = "no_submit"
            break
        nudges += 1
        counters["nudges"] += 1
        messages.append({"role": "user", "content": NUDGE})

    wall = time.time() - t0
    return {
        "card_id": card_id,
        "model": model,
        "started_at": _now(),
        "wall_s": round(wall, 3),
        "terminated": terminated or "max_steps",
        "error": error,
        "answer": answer,
        "steps": steps,
        "counters": counters,
        "usage": usage,
        "cost_usd": round(price_usd(prices, model, usage), 6),
        "prices": {"source_url": prices["source_url"], "fetched_at": prices["fetched_at"]},
        "run_config": {k: rc[k] for k in
                       ("max_steps", "max_usd_per_card", "tool_retries",
                        "api_max_retries", "max_tokens", "thinking", "effort",
                        "prompt_caching")},
        "leak_check": leak_passed,
        "input": payload,
        "transcript": transcript,
    }


def write_result(result, run_id, runs_root=RUNS_ROOT):
    d = os.path.join(runs_root, run_id)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{result['card_id']}.json")
    with open(path, "w") as f:
        json.dump(result, f, indent=1, ensure_ascii=False, default=str)
    return path


def new_run_id(prefix="run"):
    return f"{prefix}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex[:6]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--card-id", required=True)
    ap.add_argument("--model")
    ap.add_argument("--run-id")
    ap.add_argument("--runs-root", default=RUNS_ROOT)
    a = ap.parse_args()
    run_id = a.run_id or new_run_id()
    result = run_card(a.card_id, model=a.model)
    path = write_result(result, run_id, a.runs_root)
    print(f"{result['card_id']}: {result['terminated']} "
          f"answer={result['answer']} steps={result['steps']} "
          f"cost=${result['cost_usd']:.4f} wall={result['wall_s']:.1f}s")
    print(os.path.relpath(path, REPO))
    return 0


if __name__ == "__main__":
    sys.exit(main())
