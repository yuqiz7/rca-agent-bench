#!/usr/bin/env python3
"""repo.py -- hand-written SQL for the six tables of docs/design/service_v1.md §3.

NO ORM, and the reason is the same one that rejected Alembic (决策 037): what an
ORM buys is mapping a large, churning schema onto objects, and this is six tables
that will grow columns and not shapes. What it would cost is a lazy-loading model
where `GET /runs/{id}/trace` silently becomes N+1 queries, plus a second dialect
to read when the SQL misbehaves. Every statement below is the statement that
runs.

Every value is bound as a parameter -- there is no f-string anywhere near a
query. The only interpolation is of column and ORDER BY fragments this module
itself owns, never of anything a caller supplies.

CURSOR PAGINATION (§2). cursor = base64("<created_at ISO>|<run_id>"), ordering by
(created_at DESC, run_id DESC). Offset pagination on a table that is being
inserted into repeats and skips rows; the tuple comparison below cannot, because
it names a position in the ordering rather than a count from the start. run_id is
in the key only to break ties on identical timestamps -- without it a page
boundary landing between two runs created in the same microsecond drops one.
"""
import base64

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

RUN_COLUMNS = (
    "run_id", "card_id", "arm", "model", "status", "idempotency_key",
    "created_at", "started_at", "finished_at", "answer_service", "answer_fault",
    "terminated", "steps", "cost_usd", "wall_s", "counters", "error",
    "artifact_path", "run_config",
)
_JSONB_RUN_COLUMNS = {"counters", "run_config"}


# --------------------------------------------------------------------------- #
# cards
# --------------------------------------------------------------------------- #

def upsert_card(cur, card: dict) -> bool:
    """Insert or update one card snapshot. True when the row actually changed.

    The `IS DISTINCT FROM` guard is what makes a re-sync report changed=0 instead
    of touching 68 rows: without it every sync would bump snapshot_at and the
    column would record when sync last ran rather than when the card last moved.
    """
    cur.execute(
        """
        INSERT INTO cards (card_id, class, target, primitive, difficulty,
                           in_stock, evidence_ok, card_sha256)
        VALUES (%(card_id)s, %(class)s, %(target)s, %(primitive)s, %(difficulty)s,
                %(in_stock)s, %(evidence_ok)s, %(card_sha256)s)
        ON CONFLICT (card_id) DO UPDATE SET
            class       = EXCLUDED.class,
            target      = EXCLUDED.target,
            primitive   = EXCLUDED.primitive,
            difficulty  = EXCLUDED.difficulty,
            in_stock    = EXCLUDED.in_stock,
            evidence_ok = EXCLUDED.evidence_ok,
            card_sha256 = EXCLUDED.card_sha256,
            snapshot_at = now()
        WHERE (cards.class, cards.target, cards.primitive, cards.difficulty,
               cards.in_stock, cards.evidence_ok, cards.card_sha256)
              IS DISTINCT FROM
              (EXCLUDED.class, EXCLUDED.target, EXCLUDED.primitive, EXCLUDED.difficulty,
               EXCLUDED.in_stock, EXCLUDED.evidence_ok, EXCLUDED.card_sha256)
        RETURNING card_id
        """,
        card,
    )
    return cur.fetchone() is not None


def get_card(cur, card_id: str) -> dict | None:
    cur.row_factory = dict_row
    cur.execute("SELECT * FROM cards WHERE card_id = %s", (card_id,))
    return cur.fetchone()


def list_cards(cur, *, in_stock: bool | None = None) -> list[dict]:
    cur.row_factory = dict_row
    if in_stock is None:
        cur.execute("SELECT * FROM cards ORDER BY card_id")
    else:
        cur.execute("SELECT * FROM cards WHERE in_stock = %s ORDER BY card_id", (in_stock,))
    return cur.fetchall()


def count_cards(cur) -> tuple[int, int, int]:
    """(total, in_stock, evidence_ok) -- the three numbers a sync is checked by."""
    cur.execute(
        "SELECT count(*), count(*) FILTER (WHERE in_stock), "
        "count(*) FILTER (WHERE evidence_ok) FROM cards"
    )
    return tuple(cur.fetchone())


# --------------------------------------------------------------------------- #
# runs
# --------------------------------------------------------------------------- #

def insert_run(cur, run: dict) -> None:
    values = dict(run)
    for col in _JSONB_RUN_COLUMNS:
        if values.get(col) is not None:
            values[col] = Jsonb(values[col])
    cols = [c for c in RUN_COLUMNS if c in values]
    cur.execute(
        f"INSERT INTO runs ({', '.join(cols)}) "
        f"VALUES ({', '.join('%(' + c + ')s' for c in cols)})",
        values,
    )


def get_run(cur, run_id) -> dict | None:
    cur.row_factory = dict_row
    cur.execute("SELECT * FROM runs WHERE run_id = %s", (run_id,))
    return cur.fetchone()


def encode_cursor(created_at, run_id) -> str:
    raw = f"{created_at.isoformat()}|{run_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> tuple[str, str]:
    raw = base64.urlsafe_b64decode(cursor.encode()).decode()
    created_at, _, run_id = raw.partition("|")
    if not created_at or not run_id:
        raise ValueError("malformed cursor")
    return created_at, run_id


def list_runs(cur, *, card_id=None, arm=None, status=None, limit=50, cursor=None):
    """One page of runs, newest first. Returns (rows, next_cursor).

    Fetches limit+1 rows and keeps limit: that extra row is how the caller learns
    there is a next page without a second COUNT query over a growing table.
    """
    where, params = [], {}
    if card_id is not None:
        where.append("card_id = %(card_id)s")
        params["card_id"] = card_id
    if arm is not None:
        where.append("arm = %(arm)s")
        params["arm"] = arm
    if status is not None:
        where.append("status = %(status)s")
        params["status"] = status
    if cursor is not None:
        created_at, run_id = decode_cursor(cursor)
        # Row-value comparison, not "created_at < x OR (= x AND run_id < y)":
        # Postgres can drive runs_created straight off this form.
        where.append("(created_at, run_id) < (%(cur_created)s::timestamptz, %(cur_run)s::uuid)")
        params["cur_created"] = created_at
        params["cur_run"] = run_id

    params["limit"] = limit + 1
    cur.row_factory = dict_row
    cur.execute(
        "SELECT * FROM runs"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY created_at DESC, run_id DESC LIMIT %(limit)s",
        params,
    )
    rows = cur.fetchall()
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1]["created_at"], rows[-1]["run_id"])
    return rows, next_cursor


def artifact_paths(cur) -> set[tuple[str, str]]:
    """(artifact_path, card_id) already imported -- the reimport dedup key."""
    cur.execute("SELECT artifact_path, card_id FROM runs WHERE artifact_path IS NOT NULL")
    return {(r[0], r[1]) for r in cur.fetchall()}


# --------------------------------------------------------------------------- #
# grades / steps / model_calls / tool_calls
# --------------------------------------------------------------------------- #

def insert_grade(cur, run_id, grade: dict) -> None:
    cur.execute(
        "INSERT INTO grades (run_id, top1_ok, service_ok) VALUES (%s, %s, %s)",
        (run_id, grade["top1_ok"], grade["service_ok"]),
    )


def get_grade(cur, run_id) -> dict | None:
    cur.row_factory = dict_row
    cur.execute("SELECT * FROM grades WHERE run_id = %s", (run_id,))
    return cur.fetchone()


def insert_steps(cur, run_id, steps: list[dict]) -> None:
    if not steps:
        return
    cur.executemany(
        "INSERT INTO steps (run_id, step_no, duration_ms) VALUES (%s, %s, %s)",
        [(run_id, s["step_no"], s.get("duration_ms")) for s in steps],
    )


def get_steps(cur, run_id) -> list[dict]:
    cur.row_factory = dict_row
    cur.execute("SELECT * FROM steps WHERE run_id = %s ORDER BY step_no", (run_id,))
    return cur.fetchall()


def insert_model_calls(cur, run_id, calls: list[dict]) -> None:
    if not calls:
        return
    cur.executemany(
        "INSERT INTO model_calls (run_id, step_no, model, input_tokens, output_tokens, "
        "cache_write_tokens, cache_read_tokens, step_cost_usd, stop_reason, latency_s) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        [(run_id, c["step_no"], c["model"], c.get("input_tokens"), c.get("output_tokens"),
          c.get("cache_write_tokens"), c.get("cache_read_tokens"), c.get("step_cost_usd"),
          c.get("stop_reason"), c.get("latency_s")) for c in calls],
    )


def get_model_calls(cur, run_id) -> list[dict]:
    cur.row_factory = dict_row
    cur.execute("SELECT * FROM model_calls WHERE run_id = %s ORDER BY step_no, id", (run_id,))
    return cur.fetchall()


def insert_tool_calls(cur, run_id, calls: list[dict]) -> None:
    if not calls:
        return
    cur.executemany(
        "INSERT INTO tool_calls (run_id, step_no, tool, args_digest, result_bytes, ok, "
        "validation_reject, retried, error) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        [(run_id, c["step_no"], c["tool"], c.get("args_digest"), c.get("result_bytes"),
          c.get("ok"), bool(c.get("validation_reject", False)),
          bool(c.get("retried", False)), c.get("error")) for c in calls],
    )


def get_tool_calls(cur, run_id) -> list[dict]:
    cur.row_factory = dict_row
    cur.execute("SELECT * FROM tool_calls WHERE run_id = %s ORDER BY step_no, id", (run_id,))
    return cur.fetchall()


# --------------------------------------------------------------------------- #
# the whole bundle, one transaction
# --------------------------------------------------------------------------- #

def insert_run_bundle(conn, run: dict, *, grade=None, steps=None,
                      model_calls=None, tool_calls=None) -> None:
    """A run and every child row it owns, or none of them.

    §3 says the JSON evidence artefact may exist without a database row and never
    the other way round. A half-written run -- the row present, its steps missing
    -- would be a third state that satisfies neither side of that rule and that
    nothing downstream knows how to read, so the whole bundle is one transaction.
    """
    with conn.transaction(), conn.cursor() as cur:
        insert_run(cur, run)
        if grade is not None:
            insert_grade(cur, run["run_id"], grade)
        insert_steps(cur, run["run_id"], steps or [])
        insert_model_calls(cur, run["run_id"], model_calls or [])
        insert_tool_calls(cur, run["run_id"], tool_calls or [])
