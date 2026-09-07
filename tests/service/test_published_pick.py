#!/usr/bin/env python3
"""repo.runs_in_artifact_dirs -- the selector behind pick=published_all43. LOCAL GATE.

Skipped without RCA_TEST_DB_URL, like the rest of tests/service/.

WHAT IS WORTH TESTING HERE. The published pick reproduces a frozen table, so the
failure that matters is not "it errored" but "it returned a number, and the
number moved". Three ways that can happen quietly:

  * the selector reaching outside the named batches, which would let a later run
    of the same card -- the service's own runs included -- drift into a published
    aggregate;
  * the selector inheriting the `status = 'succeeded'` filter from
    latest_succeeded_per_card, which drops the nine agent-haiku runs that hit
    max_steps and takes the arm's cost down with them (see the docstring on
    runs_in_artifact_dirs: $3.30 -> $2.24, an arm made cheaper by discarding its
    expensive runs);
  * a batch re-imported twice giving two rows for one card, where "which one" is
    then whatever the planner returned first.

Each test below is one of those. The other half of the claim -- that the batches
named in PUBLISHED_ALL43 hold one run per card and aggregate to the report's
numbers -- is checked against the real database, since the throwaway schema this
fixture builds has no batch data in it by construction.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

if not os.environ.get("RCA_TEST_DB_URL"):
    pytest.skip("RCA_TEST_DB_URL is not set -- local gate; see docs/workflow.md",
                allow_module_level=True)

from service.app import repo, summary   # noqa: E402

CARD = "test-card-a"
T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _run(dirname, *, status="succeeded", card_id=CARD, created_at=T0, **over):
    row = {"run_id": uuid.uuid4(), "card_id": card_id, "arm": "agent",
           "model": "claude-sonnet-5", "status": status, "created_at": created_at,
           "artifact_path": f"artifacts/agent_runs/{dirname}/{card_id}.json",
           "steps": 5, "cost_usd": 0.05, "wall_s": 10.0}
    row.update(over)
    return row


def _select(connect, dirs, cards=(CARD,)):
    with connect() as conn, conn.cursor() as cur:
        return repo.runs_in_artifact_dirs(cur, list(cards), "agent",
                                          "claude-sonnet-5", list(dirs))


def test_only_runs_inside_the_named_batches_are_selected(connect, empty_queue):
    with connect() as conn, conn.cursor() as cur:
        repo.insert_run(cur, _run("batch_named"))
        repo.insert_run(cur, _run("batch_other", created_at=T0 + timedelta(days=30)))
    chosen = _select(connect, ["batch_named"])
    assert set(chosen) == {CARD}
    assert chosen[CARD]["artifact_path"].split("/")[2] == "batch_named"


def test_a_failed_run_inside_the_batch_is_kept(connect, empty_queue):
    """status='failed' is this service's word for terminated != submit/answered.

    The published table counted those runs; dropping them here would make an
    arm's cost and step count better by deleting its worst runs.
    """
    with connect() as conn, conn.cursor() as cur:
        repo.insert_run(cur, _run("batch_named", status="failed",
                                  terminated="max_steps", steps=20, cost_usd=0.2))
    chosen = _select(connect, ["batch_named"])
    assert set(chosen) == {CARD}
    assert chosen[CARD]["status"] == "failed"
    assert chosen[CARD]["steps"] == 20


def test_an_unfinished_run_is_not_selected(connect, empty_queue):
    with connect() as conn, conn.cursor() as cur:
        repo.insert_run(cur, _run("batch_named", status="queued",
                                  artifact_path=None))
        repo.insert_run(cur, _run("batch_named", status="running",
                                  card_id="test-card-b",
                                  artifact_path="artifacts/agent_runs/batch_named/x.json"))
    assert _select(connect, ["batch_named"], cards=(CARD, "test-card-b")) == {}


def test_a_batch_imported_twice_resolves_to_the_newest_row(connect, empty_queue):
    older = _run("batch_named", created_at=T0, steps=5)
    newer = _run("batch_named", created_at=T0 + timedelta(hours=1), steps=7,
                 artifact_path=f"artifacts/agent_runs/batch_named/{CARD}.dup.json")
    with connect() as conn, conn.cursor() as cur:
        repo.insert_run(cur, older)
        repo.insert_run(cur, newer)
    chosen = _select(connect, ["batch_named"])
    assert chosen[CARD]["run_id"] == newer["run_id"]
    assert chosen[CARD]["steps"] == 7


def test_no_batches_selects_nothing_rather_than_everything(connect, empty_queue):
    """An arm PUBLISHED_ALL43 does not name must not quietly aggregate the table."""
    with connect() as conn, conn.cursor() as cur:
        repo.insert_run(cur, _run("batch_named"))
    assert _select(connect, []) == {}
    assert summary.PUBLISHED_ALL43.get(("agent", "no-such-model")) is None
