#!/usr/bin/env python3
"""cards_sync.py -- scenarios/*.yaml -> the `cards` table, one direction only.

The table is a SNAPSHOT, not the authority (§3). scenarios/*.yaml stays the
source of truth; this exists so `GET /cards` and the joins behind `/summary` do
not have to parse 68 YAML files per request. Nothing here ever writes back.

WHERE EACH COLUMN'S RULE COMES FROM -- every one is an existing repo definition
reused, not a fresh judgement:

  in_stock     scripts/tools/readme_check.py:instock() --
               `production.probe.verdict == "passed"`. That function is what
               produces README's "N in stock", so a disagreement here would be a
               disagreement with the number the repo publishes about itself.
  evidence_ok  the eight files readme_check counts for "N files per card": the
               five sha256-manifested ones in scripts/evidence/pack.py FIVE, plus
               manifest.json (pack.py writes it as "the 6th file: the index"),
               alerts.json (scripts/evidence/detect.py writes it) and task.json
               (the agent's whole input; scripts/agent/leak_check.py reads it).
               Derived from the writers rather than by listing one card's
               directory, so a half-packed reference card cannot redefine "eight".
  difficulty   `difficulty_measured` when a card has one, else
               `difficulty_predicted.tier` (generate.py:62 computes A/B/C into a
               tier). Measured is null on every card today; the fallback is what
               makes the column non-empty, and the preference order is what makes
               it right the day a batch backfills a measurement.
  card_sha256  sha256 of the yaml file's bytes. Compared against a later sync,
               it is how drift between the snapshot and the authority shows up.

GROUND TRUTH IS NEVER READ HERE. The loader takes the six fields it needs by
name; `ground_truth` is not among them, and assert_no_truth_columns() re-checks
the table's shape after every sync -- §3's promise is that no SQL, no misconfigured
account and no GET /cards can reach the answer, and a promise that is only true
of the code that exists today is worth checking against the database itself.
"""
import hashlib

import yaml

from . import harness, repo

# scripts/evidence/pack.py FIVE, sha256-manifested.
PACK_FIVE = ("logs.jsonl", "metrics.json", "traces.json", "config_diff.txt", "topology.json")
# The three that complete the eight readme_check counts per card.
PACK_INDEX_AND_VIEWS = ("manifest.json", "alerts.json", "task.json")
EVIDENCE_EIGHT = PACK_FIVE + PACK_INDEX_AND_VIEWS

# The card fields that decide the answer. Same list as scripts/agent/leak_check.py
# FORBIDDEN_KEYS, minus the ones `cards` legitimately holds (class/target/primitive
# describe the card, and §3 puts them in the table on purpose); what is left is the
# set that must never become a column.
FORBIDDEN_COLUMNS = ("ground_truth", "params", "answer", "aliases", "note")


def _difficulty(card: dict) -> str | None:
    measured = card.get("difficulty_measured")
    if isinstance(measured, dict):
        measured = measured.get("tier")
    if measured:
        return str(measured)
    predicted = card.get("difficulty_predicted") or {}
    tier = predicted.get("tier") if isinstance(predicted, dict) else predicted
    return str(tier) if tier else None


def _evidence_ok(card_id: str) -> bool:
    d = harness.EVIDENCE_DIR / card_id
    return all((d / name).is_file() and (d / name).stat().st_size > 0 for name in EVIDENCE_EIGHT)


def load_cards() -> list[dict]:
    """Every scenarios/*.yaml that is a card, as `cards` rows. No truth fields."""
    rows = []
    for path in sorted(harness.SCENARIOS_DIR.glob("*.yaml")):
        raw = path.read_bytes()
        card = yaml.safe_load(raw)
        # _template.yaml has no card_id; readme_check's scenarios() skips on the
        # same condition, so the two agree on what counts as a card.
        if not isinstance(card, dict) or "card_id" not in card:
            continue
        probe = ((card.get("production") or {}).get("probe") or {})
        rows.append({
            "card_id": card["card_id"],
            "class": card["class"],
            "target": card["target"],
            "primitive": card["primitive"],
            "difficulty": _difficulty(card),
            "in_stock": probe.get("verdict") == "passed",
            "evidence_ok": _evidence_ok(card["card_id"]),
            "card_sha256": hashlib.sha256(raw).hexdigest(),
        })
    return rows


def assert_no_truth_columns(cur) -> None:
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'cards' "
        "AND column_name = ANY(%s)",
        (list(FORBIDDEN_COLUMNS),),
    )
    found = [r[0] for r in cur.fetchall()]
    if found:
        raise AssertionError(f"cards table carries ground-truth columns {found}")


def sync(conn) -> dict:
    """Upsert every card. Idempotent: a second run reports changed=0."""
    cards = load_cards()
    with conn.transaction(), conn.cursor() as cur:
        changed = sum(repo.upsert_card(cur, c) for c in cards)
        assert_no_truth_columns(cur)
        total, in_stock, evidence_ok = repo.count_cards(cur)
    return {"scanned": len(cards), "changed": changed, "total": total,
            "in_stock": in_stock, "evidence_ok": evidence_ok}
