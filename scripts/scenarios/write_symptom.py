#!/usr/bin/env python3
"""write_symptom.py -- copy detect.py's alerts into a card's agent_visible_symptom.

detect.py is forbidden from touching scenarios/ (see its docstring), so the
write-back lives here: one direction, alerts.json -> card yaml, and nothing about
the card flows back into detection. run_batch.py calls the same function.

Usage: write_symptom.py --card-id crash-cart-01 [--evidence-root evidence]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cards  # noqa: E402

EVIDENCE_ROOT = os.path.join(cards.REPO, "evidence")


def symptom_from_alerts(alerts_path):
    with open(alerts_path) as f:
        res = json.load(f)
    return {
        "window": res["window"],
        "detected_at": res["detected_at"],
        "no_alert": res["no_alert"],
        "alerts": [{"rule": a["rule"], "message": a["message"], "service": a["service"],
                    "baseline": a["baseline"], "observed": a["observed"],
                    "deviation": a["deviation"]} for a in res["alerts"]],
    }


def write_back(card_id, evidence_root=EVIDENCE_ROOT):
    card = cards.load(card_id)
    card["agent_visible_symptom"] = symptom_from_alerts(
        os.path.join(evidence_root, card_id, "alerts.json"))
    return cards.save(card)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--card-id", required=True)
    ap.add_argument("--evidence-root", default=EVIDENCE_ROOT)
    a = ap.parse_args()
    print(os.path.relpath(write_back(a.card_id, a.evidence_root), cards.REPO))
    return 0


if __name__ == "__main__":
    sys.exit(main())
