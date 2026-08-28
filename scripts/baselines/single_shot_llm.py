#!/usr/bin/env python3
"""single_shot_llm.py -- baseline 2: one model turn, no tools, fixed digest.

Between baseline 1 (rules, no model) and the agent (model plus six tools and up
to 20 steps) sits the question this arm answers: how much of the agent's score
comes from being able to *investigate*, and how much from simply being a capable
model looking at the evidence? Same model, same answer space, same card set --
the only thing removed is the loop. Whatever the agent scores above this is what
the tool-using loop is worth.

The digest is built by summarise.py and its shape is identical for every card, so
this arm cannot quietly become "a summariser that knows the answer". The alerts
come from task.json's agent_visible_symptom, exactly what the agent is handed,
and never from alerts.json -- that file carries card_id, which spells out the
answer.

Leak discipline is the agent's, reused rather than reimplemented: leak_check's
FORBIDDEN_KEYS and its by-value card_id assertion run over the assembled prompt
before any request goes out. leak_check.check_payload itself does not apply here
-- two of its four checks pin the request to the agent's fixed system prompt and
tool schemas, which this arm deliberately does not have.

A reply that is not parseable JSON is retried once; a second failure is recorded
as unparsed and scored wrong, never silently retried into a pass.
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "scripts", "agent"))

import anthropic  # noqa: E402

import leak_check  # noqa: E402
import run_agent  # noqa: E402
from evidence_tools import FAULT_TYPES, TARGET_ENUM  # noqa: E402
from summarise import summarise  # noqa: E402

SYSTEM_PROMPT = """\
You are a site reliability engineer diagnosing a single fault in a running \
microservice deployment. Exactly one fault was introduced, into exactly one \
service. Downstream services reporting errors is expected cascade, not a second \
root cause -- your job is to name where the fault was introduced, not where it \
was felt.

You are given a fixed digest of the evidence: the incident windows, the service \
dependency graph, per-window metric means, per-window trace aggregates, the \
error log lines, the configuration diff, and the alerts that fired. There are no \
tools and there is no second turn -- answer from what is in front of you.

The candidate services are:
""" + ", ".join(TARGET_ENUM) + """

The fault types are:
- crash: the service's container is not running. Callers see address-unreachable \
errors in bulk; the service's own logs and spans stop.
- latency: the service is up and reachable but responds slowly. Caller-side spans \
get much longer; errors stay rare.
- blackhole: the service is up but its port silently drops packets. Callers see \
neither success nor error -- the edge simply goes quiet.
- misconfig: the service's configuration or feature flag was changed. The service \
itself returns errors on its own spans and logs.
- mem_leak: the service's memory grows without bound, and it eventually slows \
down or restarts.

Reply with JSON and nothing else, in exactly this form:
{"service": "<one of the candidate services>", "fault_type": "<one of the fault types>"}
"""

RETRY_NOTE = ('Your previous reply was not valid JSON of the required form. '
              'Reply with only the JSON object, no prose, no code fence: '
              '{"service": "...", "fault_type": "..."}')


def build_user(card_id, evidence_root=None):
    """Digest + the agent-visible symptom. Identical assembly for every card."""
    root = evidence_root or os.path.join(REPO, "evidence")
    with open(os.path.join(root, card_id, "task.json")) as f:
        task_view = json.load(f)
    doc = {"trigger": task_view.get("trigger"),
           "agent_visible_symptom": task_view.get("agent_visible_symptom")}
    return (f"{json.dumps(doc, indent=1, ensure_ascii=False)}\n\n"
            f"# evidence digest\n\n{summarise(card_id, evidence_root=evidence_root)}"), task_view


def assert_no_leak(card_id, system, user):
    """leak_check's own rules, over this arm's prompt."""
    blob = system + "\n" + user
    for key in leak_check.FORBIDDEN_KEYS:
        if f'"{key}"' in blob:
            raise leak_check.LeakError(
                f"{card_id}: forbidden key {key!r} appears in the baseline input")
    if card_id in blob:
        raise leak_check.LeakError(f"{card_id}: the card id appears in the baseline input")
    return ["no_forbidden_keys", "no_card_id"]


def parse_answer(text):
    if not text:
        return None
    m = re.search(r"\{[^{}]*\"service\"[^{}]*\}", text, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    svc, ft = d.get("service"), d.get("fault_type")
    if svc in TARGET_ENUM and ft in FAULT_TYPES:
        return {"service": svc, "fault_type": ft}
    return None


def run_card(card_id, model=None, config=None, prices=None, evidence_root=None, client=None):
    config = config or run_agent.load_config()
    prices = prices or run_agent.load_prices()
    rc = config["run"]
    model = model or config["models"]["primary"]

    user, _ = build_user(card_id, evidence_root=evidence_root)
    leak_passed = assert_no_leak(card_id, SYSTEM_PROMPT, user)

    client = client or anthropic.Anthropic(
        max_retries=rc["api_max_retries"], timeout=rc["request_timeout_s"])

    messages = [{"role": "user", "content": user}]
    usage = run_agent._zero_usage()
    t0 = time.time()
    answer, terminated, error = None, None, None
    parse_failures, attempts = 0, 0
    texts = []

    for attempt in range(2):
        attempts += 1
        try:
            resp = client.messages.create(
                model=model, max_tokens=rc["max_tokens"], system=SYSTEM_PROMPT,
                messages=messages, output_config={"effort": rc["effort"]},
                **({"thinking": {"type": "adaptive"}} if rc.get("thinking") == "adaptive" else {}))
        except anthropic.APIError as exc:
            terminated, error = "api_error", f"{type(exc).__name__}: {exc}"
            break
        run_agent._add_usage(usage, resp.usage)
        text = "".join(b.text for b in resp.content if b.type == "text")
        texts.append(text[:2000])
        answer = parse_answer(text)
        if answer:
            terminated = "answered"
            break
        parse_failures += 1
        if attempt == 0:
            messages = messages + [{"role": "assistant", "content": resp.content},
                                   {"role": "user", "content": RETRY_NOTE}]
        else:
            terminated = "unparsed"

    return {
        "card_id": card_id,
        "model": model,
        "answer": answer,
        # One turn by construction: the retry is error handling, not investigation,
        # so the step count stays 1 and stays comparable with baseline 1.
        "steps": 1,
        "api_calls": attempts,
        "parse_failures": parse_failures,
        "terminated": terminated,
        "error": error,
        "usage": usage,
        "cost_usd": round(run_agent.price_usd(prices, model, usage), 6),
        "wall_s": round(time.time() - t0, 3),
        "leak_check": leak_passed,
        "replies": texts,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--card-id")
    ap.add_argument("--cards")
    ap.add_argument("--model")
    ap.add_argument("--out")
    ap.add_argument("--evidence-root")
    a = ap.parse_args()
    ids = [a.card_id] if a.card_id else (a.cards.split(",") if a.cards else [])
    if not ids:
        ap.error("--card-id or --cards required")
    config, prices = run_agent.load_config(), run_agent.load_prices()
    client = anthropic.Anthropic(max_retries=config["run"]["api_max_retries"],
                                 timeout=config["run"]["request_timeout_s"])
    out = []
    for i, c in enumerate(ids, 1):
        r = run_card(c, model=a.model, config=config, prices=prices,
                     evidence_root=a.evidence_root, client=client)
        out.append(r)
        ans = r["answer"] and f"{r['answer']['service']}/{r['answer']['fault_type']}"
        print(f"[{i}/{len(ids)}] {c}: {ans or r['terminated']} "
              f"${r['cost_usd']:.4f} {r['wall_s']}s")
        if a.out:
            os.makedirs(os.path.dirname(a.out), exist_ok=True)
            with open(a.out, "w") as f:
                json.dump(out, f, indent=1, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
