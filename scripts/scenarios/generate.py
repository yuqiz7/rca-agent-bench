#!/usr/bin/env python3
"""generate.py -- build scenarios/<card_id>.yaml for every card in the recipe.

Single input: scripts/scenarios/recipe.csv (decision 021). The markdown table in
docs/recipe.md §4 is a *render* of that csv, not a second source -- parsing the
prose "params" column back out of markdown was rejected as too brittle, so the
csv is authoritative and --write-docs regenerates the table between the markers.

Deterministic by construction: rows are emitted in csv order, fields in the fixed
order of cards.FIELD_ORDER, and the three runner-owned fields
(difficulty_measured / production / agent_visible_symptom) are carried over from
an existing card instead of being reset. A rerun therefore produces no diff --
neither on a fresh tree nor after the runner has filled a card in.

Usage:
  generate.py                 # write all cards + refresh the docs table
  generate.py --check         # exit 1 if any file would change (CI use)
  generate.py --reset         # also clear runner-owned fields back to null
"""
import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cards  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = cards.REPO
RECIPE_CSV = os.path.join(HERE, "recipe.csv")
RECIPE_MD = os.path.join(REPO, "docs", "recipe.md")

BEGIN = "<!-- BEGIN GENERATED: recipe-table (scripts/scenarios/generate.py) -->"
END = "<!-- END GENERATED: recipe-table -->"

# Fields the runner writes back; generate.py must not stomp on them.
CARRIED = ("difficulty_measured", "production", "agent_visible_symptom")


def read_recipe(path=RECIPE_CSV):
    with open(path) as f:
        rows = list(csv.DictReader(f))
    seen = set()
    for r in rows:
        if r["card_id"] in seen:
            raise SystemExit(f"duplicate card_id in recipe.csv: {r['card_id']}")
        seen.add(r["card_id"])
    return rows


def build_card(row, existing=None):
    a, b, c = int(row["axis_a"]), int(row["axis_b"]), int(row["axis_c"])
    total, tier = cards.tier_of(a, b, c)
    card = {
        "card_id": row["card_id"],
        "class": row["class"],
        "target": row["target"],
        "primitive": row["primitive"],
        "params": json.loads(row["params"]),
        "ground_truth": {"service": row["gt_service"], "class": row["gt_class"]},
        "difficulty_predicted": {"A": a, "B": b, "C": c, "total": total, "tier": tier},
        "difficulty_measured": None,
        "cycle": cards.cycle_for(row.get("cycle_override")),
        "param_validated": row["param_validated"] == "true",
        "batch": int(row["batch"]) if row["batch"] else None,
        "production": None,
        "agent_visible_symptom": None,
    }
    if existing:
        for k in CARRIED:
            if existing.get(k) is not None:
                card[k] = existing[k]
    return card


def params_str(params):
    if not params:
        return "—"
    return " ".join(f"{k}={params[k]}" for k in sorted(params))


def docs_table(rows):
    L = ["| card_id | class | target | primitive | params | A | B | C | total | 档位 | param_validated | batch | 周期覆盖 | note |",
         "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | ---: | --- | --- |"]
    for r in rows:
        a, b, c = int(r["axis_a"]), int(r["axis_b"]), int(r["axis_c"])
        total, tier = cards.tier_of(a, b, c)
        ov = (r.get("cycle_override") or "").strip()
        ov_s = " ".join(f"`{k}={v}`" for k, v in sorted(json.loads(ov).items())) if ov else "—"
        L.append("| `{cid}` | {cls} | `{tgt}` | `{prim}` | {par} | {a} | {b} | {c} | {t} | {tier} | {pv} | {batch} | {ov} | {note} |".format(
            cid=r["card_id"], cls=r["class"], tgt=r["target"], prim=r["primitive"],
            par=params_str(json.loads(r["params"])), a=a, b=b, c=c, t=total, tier=tier,
            pv="yes" if r["param_validated"] == "true" else "no",
            batch=r["batch"] or "—", ov=ov_s, note=r["note"] or ""))
    return "\n".join(L)


def splice_docs(rows, path=RECIPE_MD):
    """Replace the generated table between the markers. Returns (changed, text)."""
    with open(path) as f:
        text = f.read()
    if BEGIN not in text or END not in text:
        raise SystemExit(f"{path}: missing generated-table markers ({BEGIN})")
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    new = head + BEGIN + "\n\n" + docs_table(rows) + "\n\n" + END + tail
    return new != text, new


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="do not write; exit 1 if anything would change")
    ap.add_argument("--reset", action="store_true",
                    help="also clear difficulty_measured / production / agent_visible_symptom")
    ap.add_argument("--no-docs", action="store_true", help="skip the docs table refresh")
    a = ap.parse_args()

    rows = read_recipe()
    changed = []

    # 配方里没有的卡由生成器删除，不手删 —— 手删迟早漏一张，而 scenarios/ 里
    # 多出来的幽灵卡会被 --batch 选中、被泄漏测试遍历，静默进入量产。
    wanted = {r["card_id"] for r in rows}
    stale = [c for c in cards.all_card_ids() if c not in wanted]
    for cid in stale:
        changed.append(os.path.relpath(cards.card_path(cid), REPO) + "  (removed)")
        if not a.check:
            os.remove(cards.card_path(cid))
    for row in rows:
        path = cards.card_path(row["card_id"])
        existing = None
        if os.path.exists(path) and not a.reset:
            existing = cards.load_path(path)
        text = cards.render(build_card(row, existing))
        old = open(path).read() if os.path.exists(path) else None
        if old != text:
            changed.append(os.path.relpath(path, REPO))
            if not a.check:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, "w").write(text)

    if not a.no_docs:
        doc_changed, new_text = splice_docs(rows)
        if doc_changed:
            changed.append(os.path.relpath(RECIPE_MD, REPO))
            if not a.check:
                open(RECIPE_MD, "w").write(new_text)

    print(f"cards={len(rows)} changed={len(changed)}")
    for c in changed[:20]:
        print(f"  {c}")
    if len(changed) > 20:
        print(f"  ... and {len(changed) - 20} more")
    if a.check and changed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
