#!/usr/bin/env python3
"""cards.py -- canonical read/write for scenarios/<card_id>.yaml (decision 021).

Every writer in the pipeline (generate.py, detect.py, run_batch.py) goes through
render() so the on-disk field order never depends on who wrote last. Rendering is
hand-rolled rather than yaml.dump() because pyyaml's key order and flow style are
not stable across versions, and the generator must be byte-identical on a rerun.

Reading uses pyyaml: cards are plain YAML, any loader can consume them.
"""
import csv
import os
import sys

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCENARIOS_DIR = os.path.join(REPO, "scenarios")

# Cycle timings default the same for every card: decision 012 (60/120/60) +
# decision 016 (settle 150s). Kept here so a card file never carries a hand-edited
# timing -- a per-card deviation goes in recipe.csv's cycle_override column, not
# into the yaml by hand (决策 023).
CYCLE = {"baseline_s": 60, "inject_s": 120, "recover_s": 60, "settle_s": 150}


def cycle_for(override_json):
    """Merge recipe.csv's cycle_override onto CYCLE.

    Decision 012 fixed one cycle for the whole deck and that stays the default; the
    override column is the *exception* mechanism 决策 023 needed for two cases the
    uniform cycle cannot serve:

      - R1 low-traffic targets (checkout / email / payment, ~0.057/s): a 120s inject
        window is expected to carry ~7 calls, and both judgment floors (N >= 5,
        baseline_rate x inject_s >= 5) sit right on top of that. Batch 2 measured
        0.04/s -> 4.8 expected and the cards failed on sample size, not on the
        fault. inject_s=300 puts them at ~17.
      - O-P2-9 cartFailure: the erroring EmptyCart spans hang for p50 150s, past the
        evidence window's close, so spanmetrics sees 1 call / 0 errors while the gate
        (Jaeger, queried at harvest) sees 3 / 2. settle_s=300 lets them land.

    Raises on an unknown key: a typo'd override would otherwise be silently dropped
    and the card would run the default window while the recipe claimed otherwise.
    """
    import json as _json
    cycle = dict(CYCLE)
    if not override_json or not override_json.strip():
        return cycle
    ov = _json.loads(override_json)
    unknown = sorted(set(ov) - set(CYCLE))
    if unknown:
        raise ValueError(f"cycle_override has unknown keys {unknown}; "
                         f"allowed: {sorted(CYCLE)}")
    for k, v in ov.items():
        if not isinstance(v, int) or v <= 0:
            raise ValueError(f"cycle_override[{k}] must be a positive int, got {v!r}")
    cycle.update(ov)
    return cycle

# Difficulty tiers, decision 021: 0-1 easy, 2-3 medium, >=4 hard; axis C == 2
# promotes one tier on top of the total.
TIERS = ["易", "中", "难"]


def tier_of(a, b, c):
    total = a + b + c
    idx = 0 if total <= 1 else (1 if total <= 3 else 2)
    if c == 2:
        idx = min(idx + 1, 2)
    return total, TIERS[idx]


def card_path(card_id):
    return os.path.join(SCENARIOS_DIR, f"{card_id}.yaml")


def load(card_id):
    with open(card_path(card_id)) as f:
        return yaml.safe_load(f)


def load_path(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _quote(s):
    return '"%s"' % s.replace("\\", "\\\\").replace('"', '\\"')


def _needs_quote(s):
    """Exact test: would YAML read this bare scalar back as the same string?

    Guessing at the rules is how `t_start: 2026-08-27T17:57:23Z` silently became a
    datetime on the next load and then a `2026-08-27 17:57:23+00:00` string on the
    save after that -- and a datetime is not JSON-serialisable, so task.json died
    three steps downstream. Round-tripping through the loader is cheap and cannot
    drift from whatever pyyaml actually does.
    """
    if s == "" or s.strip() != s:
        return True
    if s[0] in "&*!%@`>|{}[]#,\"'-?:":
        return True
    try:
        v = yaml.safe_load(s)
    except Exception:                     # noqa: BLE001 - anything unparseable gets quoted
        return True
    return not isinstance(v, str) or v != s


def _scalar(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v)
    return _quote(s) if _needs_quote(s) else s


def _emit(val, indent, lines):
    pad = " " * indent
    if isinstance(val, dict):
        if not val:
            lines[-1] += " {}"
            return
        for k, v in val.items():
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{pad}{k}:")
                _emit(v, indent + 2, lines)
            elif isinstance(v, dict):
                lines.append(f"{pad}{k}: {{}}")
            elif isinstance(v, list):
                lines.append(f"{pad}{k}: []")
            else:
                lines.append(f"{pad}{k}: {_scalar(v)}")
    elif isinstance(val, list):
        for item in val:
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}-")
                _emit(item, indent + 2, lines)
            else:
                lines.append(f"{pad}- {_scalar(item)}")


# Canonical top-level field order (decision 021 / recipe v1.0 §4).
FIELD_ORDER = ["card_id", "class", "target", "primitive", "params", "ground_truth",
               "difficulty_predicted", "difficulty_measured", "cycle",
               "param_validated", "batch", "production", "agent_visible_symptom"]

HEADER = ("# 故障场景卡（decision 021 / docs/recipe.md v1.0）\n"
          "# 由 scripts/scenarios/generate.py 从 scripts/scenarios/recipe.csv 生成。\n"
          "# production / agent_visible_symptom / difficulty_measured 由 runner 回填。\n")


def render(card):
    lines = []
    for k in FIELD_ORDER:
        if k not in card:
            continue
        v = card[k]
        if isinstance(v, (dict, list)) and v:
            lines.append(f"{k}:")
            _emit(v, 2, lines)
        elif isinstance(v, dict):
            lines.append(f"{k}: {{}}")
        elif isinstance(v, list):
            lines.append(f"{k}: []")
        else:
            lines.append(f"{k}: {_scalar(v)}")
    extra = [k for k in card if k not in FIELD_ORDER]
    if extra:
        raise ValueError(f"unknown card fields (not in FIELD_ORDER): {extra}")
    return HEADER + "\n".join(lines) + "\n"


ADHOC_DIR = os.path.join(REPO, "scripts", "out", "_adhoc")
_RECIPE_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recipe.csv")


def recipe_card_ids():
    """card_id set from recipe.csv. Empty set if the file is missing -- the guard
    below then lets everything through rather than blocking all writes."""
    if not os.path.exists(_RECIPE_CSV):
        return set()
    with open(_RECIPE_CSV) as f:
        return {r["card_id"] for r in csv.DictReader(f)}


def save(card):
    """Write scenarios/<card_id>.yaml -- but only for cards the recipe knows.

    Anything else lands in scripts/out/_adhoc/ instead. `--scenarios <path>` takes
    an arbitrary yaml while save() addresses by card_id, so a one-off card run from
    outside the tree used to materialise a phantom file in scenarios/ that --batch
    would then pick up and the leak test would iterate.
    """
    cid = card["card_id"]
    known = recipe_card_ids()
    if known and cid not in known:
        os.makedirs(ADHOC_DIR, exist_ok=True)
        path = os.path.join(ADHOC_DIR, f"{cid}.yaml")
        print(f"warning: card_id {cid!r} is not in recipe.csv; "
              f"writing to {os.path.relpath(path, REPO)} instead of scenarios/",
              file=sys.stderr)
    else:
        path = card_path(cid)
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(render(card))
    return path


def all_card_ids():
    if not os.path.isdir(SCENARIOS_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(SCENARIOS_DIR)
                  if f.endswith(".yaml") and not f.startswith("_"))
