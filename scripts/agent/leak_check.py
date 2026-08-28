#!/usr/bin/env python3
"""leak_check.py -- no card internals may reach the agent, fail-closed at run time.

tests/test_no_leak.py holds the line at task.json. This holds it one layer further
in, at the bytes actually sent to the model, and it is called by run_agent.py
*before* the first API request -- a leak that only shows up in an eval report has
already spent money on a worthless number.

Same reasoning as the task.json test: check key names rather than values, because
"cart" is an ordinary English word and appears legitimately in evidence, while a
serialized `"ground_truth":` is unambiguous.

Two checks the task.json test does not make, both about *provenance*:

  - card_id must not appear. It is whitelisted into task.json (the pipeline
    addresses packs by it) and it spells out the answer: "crash-cart-01" is
    (cart, crash) written down. This is the one field where the value, not the key,
    is the leak, so it is checked by value.
  - the user turn must be a subset of task.json. Not "looks clean" -- literally
    reconstructible from task.json's own fields, so no third source can slip in.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import prompts            # noqa: E402
from evidence_tools import tool_schemas  # noqa: E402

# Same list as tests/test_no_leak.py: the card fields that decide the answer.
FORBIDDEN_KEYS = ["ground_truth", "target", "primitive", "class", "difficulty", "params"]


class LeakError(AssertionError):
    pass


def check_payload(payload, card_id, task_view):
    """Raise LeakError on the first violation. Returns the checks that passed."""
    system, user, tools = payload["system"], payload["user"], payload["tools"]
    blob = system + "\n" + user + "\n" + json.dumps(tools, ensure_ascii=False)
    done = []

    # 1. No card field name was copied through wholesale.
    for key in FORBIDDEN_KEYS:
        if f'"{key}"' in blob:
            raise LeakError(f"{card_id}: forbidden key {key!r} appears in the agent input")
    done.append("no_forbidden_keys")

    # 2. The card id stays out. This is the one field checked by value rather than
    #    by key: it is whitelisted into task.json and it spells out the answer.
    if card_id in blob:
        raise LeakError(f"{card_id}: the card id appears in the agent input")
    #    Its dash-separated components are deliberately *not* scanned. They are
    #    drawn from the answer vocabulary the system prompt states openly (naming
    #    the answer space is not naming the answer) plus a numeric discriminator
    #    that collides with ordinary prose -- "01" is a substring of "ISO8601".
    #    Check 3 covers the same ground exactly instead of approximately: the two
    #    card-independent halves of the request are byte-compared against what the
    #    modules produce with no card in hand, so nothing card-specific can be in
    #    them at all.
    done.append("no_card_id")

    # 3. The system prompt and the tool schemas are card-independent: byte-identical
    #    to what the module produces with no card in hand.
    if system != prompts.SYSTEM_PROMPT:
        raise LeakError(f"{card_id}: system prompt is not the fixed constant")
    if tools != tool_schemas():
        raise LeakError(f"{card_id}: tool schemas differ from the card-independent set")
    done.append("fixed_prompt_and_tools")

    # 4. The user turn is a projection of task.json and nothing else.
    doc = json.loads(user)
    extra = set(doc) - set(prompts.USER_FIELDS)
    if extra:
        raise LeakError(f"{card_id}: user turn has fields outside the projection: {extra}")
    for k, v in doc.items():
        if k not in task_view:
            raise LeakError(f"{card_id}: user turn field {k!r} is not in task.json")
        if v != task_view[k]:
            raise LeakError(f"{card_id}: user turn field {k!r} does not match task.json")
    done.append("user_turn_subset_of_task_json")

    return done


def check_card(card_id, evidence_root=None):
    root = evidence_root or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "evidence")
    with open(os.path.join(root, card_id, "task.json")) as f:
        task_view = json.load(f)
    payload = prompts.build_request_payload(task_view, tool_schemas())
    return check_payload(payload, card_id, task_view)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--card-id")
    ap.add_argument("--all", action="store_true",
                    help="check every card that has a packed task.json")
    a = ap.parse_args()
    root = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "evidence")
    if a.all:
        ids = sorted(d for d in os.listdir(root)
                     if not d.startswith("_")
                     and os.path.exists(os.path.join(root, d, "task.json")))
    elif a.card_id:
        ids = [a.card_id]
    else:
        ap.error("need --card-id or --all")
    for cid in ids:
        done = check_card(cid)
        print(f"ok {cid}: {', '.join(done)}")
    print(f"{len(ids)} card(s) clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
