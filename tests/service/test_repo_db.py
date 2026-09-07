#!/usr/bin/env python3
"""service/app/repo.py against a real Postgres (design §6). LOCAL GATE.

Skipped without RCA_TEST_DB_URL. The four things worth testing here are the four
things SQLite could not have told us: a partial unique index that ignores NULLs,
a second partial unique index over a column PAIR, `FOR UPDATE SKIP LOCKED` under
two genuinely concurrent connections, and cursor pagination over a row order that
only exists because of an index.
"""
import uuid
from datetime import datetime, timedelta, timezone

import os

import pytest

# The database gate, checked before importing anything that needs psycopg.
# Spelled out in each file rather than imported from conftest: `pytestmark` in a
# conftest does not apply to test modules, and making tests/ a package to allow
# the import would change how pytest resolves every other test file in the repo.
if not os.environ.get("RCA_TEST_DB_URL"):
    pytest.skip("RCA_TEST_DB_URL is not set -- local gate; see docs/workflow.md",
                allow_module_level=True)

from service.app import repo   # noqa: E402


def _run(card_id="test-card-a", **over):
    row = {"run_id": uuid.uuid4(), "card_id": card_id, "arm": "rules",
           "model": "none", "status": "queued"}
    row.update(over)
    return row


# --------------------------------------------------------------------------- #
# round trip
# --------------------------------------------------------------------------- #

def test_insert_and_read_back_a_run(connect):
    run = _run(status="succeeded", cost_usd=0.0, wall_s=1.5, steps=1,
               answer_service="cart", answer_fault="crash",
               counters={"api_calls": 0}, run_config={"request": {"arm": "rules"}},
               terminated="submit")
    with connect() as conn:
        repo.insert_run_bundle(conn, run, grade={"top1_ok": True, "service_ok": True},
                               steps=[{"step_no": 1, "duration_ms": None}])
        with conn.cursor() as cur:
            got = repo.get_run(cur, run["run_id"])
            grade = repo.get_grade(cur, run["run_id"])
            steps = repo.get_steps(cur, run["run_id"])
    assert got["card_id"] == "test-card-a" and got["status"] == "succeeded"
    # jsonb survives the round trip as a dict, not as a string.
    assert got["counters"] == {"api_calls": 0}
    assert got["run_config"]["request"]["arm"] == "rules"
    assert float(got["cost_usd"]) == 0.0 and float(got["wall_s"]) == 1.5
    assert (grade["top1_ok"], grade["service_ok"]) == (True, True)
    assert [s["step_no"] for s in steps] == [1]


def test_bundle_is_all_or_nothing(connect):
    """A child row that violates a constraint must take the run row with it."""
    run = _run()
    with connect() as conn:
        with pytest.raises(Exception):
            repo.insert_run_bundle(
                conn, run,
                # step_no is part of the primary key; the duplicate fails the insert.
                steps=[{"step_no": 1, "duration_ms": None}, {"step_no": 1, "duration_ms": None}])
        with conn.cursor() as cur:
            assert repo.get_run(cur, run["run_id"]) is None


# --------------------------------------------------------------------------- #
# the two partial unique indexes
# --------------------------------------------------------------------------- #

def test_idempotency_key_is_unique_but_nulls_are_free(connect):
    import psycopg
    key = f"k-{uuid.uuid4().hex[:8]}"
    with connect() as conn:
        repo.insert_run_bundle(conn, _run(idempotency_key=key))
        with pytest.raises(psycopg.errors.UniqueViolation):
            repo.insert_run_bundle(conn, _run(idempotency_key=key))
        # NULL keys are the common case and must not collide with each other --
        # this is the whole reason runs_idem is a PARTIAL index.
        for _ in range(3):
            repo.insert_run_bundle(conn, _run(idempotency_key=None))
        with conn.cursor() as cur:
            found = repo.get_run_by_idempotency_key(cur, key)
    assert found is not None and found["idempotency_key"] == key


def test_artifact_path_is_unique_per_card_not_per_file(connect):
    """The baseline arms put many cards in one file; the key is the pair."""
    import psycopg
    path = f"artifacts/agent_runs/batch_{uuid.uuid4().hex[:6]}/baseline1.json"
    with connect() as conn:
        repo.insert_run_bundle(conn, _run("test-card-a", artifact_path=path))
        # Same file, different card: legitimate, and must be allowed.
        repo.insert_run_bundle(conn, _run("test-card-b", artifact_path=path))
        with pytest.raises(psycopg.errors.UniqueViolation):
            repo.insert_run_bundle(conn, _run("test-card-a", artifact_path=path))
        # A service-created run has no artefact until it finishes; NULLs are free.
        for _ in range(2):
            repo.insert_run_bundle(conn, _run(artifact_path=None))


# --------------------------------------------------------------------------- #
# the queue
# --------------------------------------------------------------------------- #

def test_two_connections_claim_two_different_runs(connect, empty_queue):
    """SKIP LOCKED, tested with two connections rather than one worker.

    The statement_timeout is the assertion that matters. Without SKIP LOCKED the
    second claim would BLOCK on the row the first one has locked, and a blocking
    test hangs the suite instead of failing it; with the timeout, the wrong
    behaviour shows up as a QueryCanceled in three seconds.
    """
    import psycopg
    from psycopg.rows import dict_row

    a_run, b_run = _run("test-card-a"), _run("test-card-b")
    with connect() as conn:
        repo.insert_run_bundle(conn, a_run)
        repo.insert_run_bundle(conn, b_run)

    first = connect(autocommit=False)
    second = connect(autocommit=False)
    try:
        with first.cursor(row_factory=dict_row) as ca, second.cursor(row_factory=dict_row) as cb:
            ca.execute("SET statement_timeout = '3s'")
            cb.execute("SET statement_timeout = '3s'")
            ca.execute(repo.CLAIM_SQL)
            claimed_a = ca.fetchone()                # first holds the row lock, uncommitted
            try:
                cb.execute(repo.CLAIM_SQL)
            except psycopg.errors.QueryCanceled:
                pytest.fail("the second claim blocked: SKIP LOCKED is not in effect")
            claimed_b = cb.fetchone()
        first.commit()
        second.commit()
    finally:
        first.close()
        second.close()

    assert claimed_a is not None and claimed_b is not None
    assert claimed_a["run_id"] != claimed_b["run_id"], "both workers claimed the same run"
    assert {claimed_a["card_id"], claimed_b["card_id"]} == {"test-card-a", "test-card-b"}
    assert claimed_a["status"] == claimed_b["status"] == "running"
    assert claimed_a["started_at"] is not None

    with connect() as conn:
        assert repo.claim_next(conn) is None, "the queue should be empty now"


def test_claim_prefers_the_oldest_queued_run(connect, empty_queue):
    old = _run(created_at=datetime.now(timezone.utc) - timedelta(hours=2))
    new = _run(created_at=datetime.now(timezone.utc))
    with connect() as conn:
        repo.insert_run_bundle(conn, new)
        repo.insert_run_bundle(conn, old)
        claimed = repo.claim_next(conn)
        assert claimed["run_id"] == old["run_id"]
        assert repo.claim_next(conn)["run_id"] == new["run_id"]


def test_requeue_returns_stale_running_rows_and_leaves_fresh_ones(connect, empty_queue):
    stale = _run(status="running", error="previous attempt note",
                 started_at=datetime.now(timezone.utc) - timedelta(minutes=20))
    fresh = _run(status="running", started_at=datetime.now(timezone.utc))
    with connect() as conn:
        repo.insert_run_bundle(conn, stale)
        repo.insert_run_bundle(conn, fresh)
        requeued = repo.requeue_stale(conn, 600)
        assert stale["run_id"] in requeued and fresh["run_id"] not in requeued
        with conn.cursor() as cur:
            back = repo.get_run(cur, stale["run_id"])
            untouched = repo.get_run(cur, fresh["run_id"])
    assert back["status"] == "queued"
    # started_at is cleared, or the timeout would fire again on the next sweep.
    assert back["started_at"] is None
    # The note is APPENDED: a requeued run may already carry something worth keeping.
    assert back["error"].splitlines() == ["previous attempt note", repo.REQUEUE_NOTE]
    assert untouched["status"] == "running"


# --------------------------------------------------------------------------- #
# cursor pagination
# --------------------------------------------------------------------------- #

def test_cursor_pagination_covers_every_row_exactly_once(connect, empty_queue):
    base = datetime.now(timezone.utc)
    made = []
    with connect() as conn:
        for i in range(17):
            # Two runs share a created_at on purpose: the tie-break on run_id is
            # the part of the cursor that offset pagination has no answer for.
            run = _run(arm="agent", model="pagination-probe",
                       created_at=base - timedelta(seconds=i // 2))
            repo.insert_run_bundle(conn, run)
            made.append(run["run_id"])

        seen, cursor, pages = [], None, 0
        while True:
            with conn.cursor() as cur:
                rows, cursor = repo.list_runs(cur, arm="agent", model="pagination-probe",
                                              limit=5, cursor=cursor)
            seen += [r["run_id"] for r in rows]
            pages += 1
            if not cursor:
                break
        with conn.cursor() as cur:
            cur.execute("SELECT run_id FROM runs WHERE model = 'pagination-probe' "
                        "ORDER BY created_at DESC, run_id DESC")
            expected = [r[0] for r in cur.fetchall()]

    assert pages == 4                       # 5 + 5 + 5 + 2
    assert len(seen) == len(set(seen)) == 17, "a row was repeated or dropped"
    assert seen == expected, "pages did not follow the declared ordering"
    assert set(seen) == set(made)


def test_a_forged_cursor_is_rejected_rather_than_ignored(connect):
    with connect() as conn, conn.cursor() as cur:
        with pytest.raises(Exception):
            repo.list_runs(cur, cursor="not-base64-at-all")
