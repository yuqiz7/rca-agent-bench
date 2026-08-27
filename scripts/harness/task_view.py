#!/usr/bin/env python3
"""task_view.py -- the agent's entire input: evidence/<card_id>/task.json.

Whitelist, not blacklist (fault_schema §4, decision 005). Three fields get copied
out of the card and everything else is dropped by construction -- adding a field
to a card can never widen the agent's view, because nothing here enumerates what
to *exclude*. tests/test_no_leak.py holds the line.

Usage:
  task_view.py --card-id crash-cart-01          # write evidence/<card>/task.json
  task_view.py --all                            # every card in scenarios/
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "scenarios"))
import cards  # noqa: E402

EVIDENCE_ROOT = os.path.join(cards.REPO, "evidence")

# The only card fields an agent ever sees. Do not extend without a decision entry.
WHITELIST = ("card_id", "agent_visible_symptom", "evidence_dir")

# Fixed for every card, contains no service name and no class (fault_schema §2).
TRIGGER = "Monitoring detected an anomaly in the system."


def build(card, evidence_root=EVIDENCE_ROOT):
    src = {
        "card_id": card["card_id"],
        "agent_visible_symptom": card.get("agent_visible_symptom"),
        "evidence_dir": os.path.relpath(
            os.path.join(evidence_root, card["card_id"]), cards.REPO),
    }
    view = {k: src[k] for k in WHITELIST}
    view["trigger"] = TRIGGER
    return view


def write(card_id, evidence_root=EVIDENCE_ROOT, out_root=None):
    card = cards.load(card_id)
    view = build(card, evidence_root)
    cdir = os.path.join(out_root or evidence_root, card_id)
    os.makedirs(cdir, exist_ok=True)
    path = os.path.join(cdir, "task.json")
    with open(path, "w") as f:
        json.dump(view, f, indent=1, ensure_ascii=False)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--card-id")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--evidence-root", default=EVIDENCE_ROOT)
    a = ap.parse_args()
    if not a.card_id and not a.all:
        ap.error("need --card-id or --all")
    ids = cards.all_card_ids() if a.all else [a.card_id]
    for cid in ids:
        print(os.path.relpath(write(cid, a.evidence_root), cards.REPO))
    return 0


if __name__ == "__main__":
    sys.exit(main())
