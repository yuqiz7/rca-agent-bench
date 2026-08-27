"""No card internals may reach the agent's task view (fault_schema §4, decision 005).

Runs over *every* card in scenarios/, not a sample: a leak on card 61 is as fatal
to the dataset as a leak on card 1. Checks key names rather than values, because a
value can coincide with ordinary English ("cart" is a word) while a key name in
the serialized JSON is unambiguous evidence that a whole field got copied through.
"""
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts", "scenarios"))
sys.path.insert(0, os.path.join(REPO, "scripts", "harness"))

import cards          # noqa: E402
import task_view      # noqa: E402

FORBIDDEN_KEYS = ["ground_truth", "target", "primitive", "class", "difficulty", "params"]


def _all_keys(obj, out=None):
    out = set() if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            _all_keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _all_keys(v, out)
    return out


def test_scenarios_present():
    assert cards.all_card_ids(), "no cards in scenarios/ -- run scripts/scenarios/generate.py"


def test_task_view_has_no_forbidden_keys(tmp_path):
    offenders = []
    for cid in cards.all_card_ids():
        path = task_view.write(cid, out_root=str(tmp_path))
        text = open(path).read()
        view = json.loads(text)
        for key in FORBIDDEN_KEYS:
            # the serialized text, as required: a key can only appear as `"key":`
            if f'"{key}"' in text:
                offenders.append((cid, key, "serialized text"))
            if key in _all_keys(view):
                offenders.append((cid, key, "parsed keys"))
    assert not offenders, f"forbidden keys leaked into task.json: {offenders[:10]}"


def test_task_view_fields_are_exactly_the_whitelist(tmp_path):
    allowed = set(task_view.WHITELIST) | {"trigger"}
    for cid in cards.all_card_ids():
        view = json.loads(open(task_view.write(cid, out_root=str(tmp_path))).read())
        extra = set(view) - allowed
        assert not extra, f"{cid}: unexpected top-level fields {extra}"


def test_trigger_names_no_service():
    """The trigger is fixed for every card and must not name a service."""
    services = {c["target"] for c in (cards.load(i) for i in cards.all_card_ids())}
    low = task_view.TRIGGER.lower()
    hits = [s for s in services if s.lower() in low]
    assert not hits, f"trigger text names a service: {hits}"
