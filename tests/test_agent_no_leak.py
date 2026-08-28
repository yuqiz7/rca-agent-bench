"""The agent's *request bytes* must carry no card internals (决策 005, extended).

test_no_leak.py holds the line at evidence/<card>/task.json. This holds it one
layer further in, at the system prompt + user turn + tool schemas that actually go
over the wire, and it runs the same fail-closed checker run_agent.py calls before
its first API request -- so a regression is caught by pytest rather than by an eval
report that already cost money.
"""
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts", "agent"))
sys.path.insert(0, os.path.join(REPO, "scripts", "scenarios"))
sys.path.insert(0, os.path.join(REPO, "scripts", "harness"))

import cards          # noqa: E402
import leak_check     # noqa: E402
import prompts        # noqa: E402
from evidence_tools import tool_schemas  # noqa: E402

EVIDENCE = os.path.join(REPO, "evidence")


def packed_card_ids():
    """Cards with a packed task.json -- the only ones the agent can be run on."""
    return [cid for cid in cards.all_card_ids()
            if os.path.exists(os.path.join(EVIDENCE, cid, "task.json"))]


def test_some_card_is_packed():
    assert packed_card_ids(), "no packed task.json -- run scripts/harness/task_view.py"


def test_request_payload_is_clean_for_every_packed_card():
    for cid in packed_card_ids():
        leak_check.check_card(cid)          # raises LeakError on any violation


def test_card_id_never_reaches_the_model():
    """The one field checked by value: a card id spells out both halves of the answer."""
    for cid in packed_card_ids():
        with open(os.path.join(EVIDENCE, cid, "task.json")) as f:
            view = json.load(f)
        assert view["card_id"] == cid, "task.json is expected to carry the card id"
        payload = prompts.build_request_payload(view, tool_schemas())
        blob = payload["system"] + payload["user"] + json.dumps(payload["tools"])
        assert cid not in blob


def test_user_turn_is_narrower_than_task_json():
    """The projection drops fields; it must never add one."""
    import task_view  # noqa: PLC0415
    assert set(prompts.USER_FIELDS) < set(task_view.WHITELIST) | {"trigger"}
