#!/usr/bin/env python3
"""POST /runs refuses a bad pack, and says WHICH kind of bad. LOCAL GATE.

修正 C, end to end through the real HTTP stack: FastAPI's TestClient over the
real app, against the real Postgres, with the evidence root pointed at the two
fixture packs in tests/fixtures/service/.

  fixture-leak-pack     -> 422 evidence_leak     the answer would reach the model
  fixture-missing-pack  -> 422 evidence_missing  the pack is not there

The distinction is the whole point. §4 puts this check at the entry because the
service adds "automatic, batched, unattended" to the picture, and in that setting
a broken pack must never enter a system that will retry it. But the two failures
want opposite responses from whoever is on the other end: a leak means stop and
fix the generator, a missing pack means go harvest it. One code for both would
have a client retry the emergency and page someone about the chore.

Skipped without RCA_TEST_DB_URL: this one needs a database, so design §6 makes it
a local gate, never a CI one.
"""
import os

import pytest

# The database gate, checked before importing anything that needs psycopg.
# Spelled out in each file rather than imported from conftest: `pytestmark` in a
# conftest does not apply to test modules, and making tests/ a package to allow
# the import would change how pytest resolves every other test file in the repo.
if not os.environ.get("RCA_TEST_DB_URL"):
    pytest.skip("RCA_TEST_DB_URL is not set -- local gate; see docs/workflow.md",
                allow_module_level=True)

pytest.importorskip("fastapi")
pytest.importorskip("httpx", reason="fastapi.testclient needs it; "
                    "see requirements-service-test.txt")

FIXTURE_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "fixtures", "service")
LEAK_CARD = "fixture-leak-pack"
MISSING_CARD = "fixture-missing-pack"
GOOD_CARD = "crash-cart-01"


@pytest.fixture
def client(connect, schema, monkeypatch):
    """The real app, talking to the throwaway schema, reading the fixture packs."""
    from fastapi.testclient import TestClient

    from service.app import harness
    from service.app.main import app

    url = os.environ["RCA_TEST_DB_URL"]
    sep = "&" if "?" in url else "?"
    # search_path in the connection string rather than a monkeypatched connect():
    # it keeps the app's own db.connect() on the code path under test.
    monkeypatch.setenv("RCA_DB_URL", f"{url}{sep}options=-c%20search_path%3D{schema}")
    monkeypatch.setattr(harness, "EVIDENCE_DIR", __import__("pathlib").Path(FIXTURE_ROOT))

    with connect() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO cards (card_id, class, target, primitive, in_stock, evidence_ok) "
            "VALUES (%s, 'crash', 'cart', 'kill_container', true, false) "
            "ON CONFLICT (card_id) DO NOTHING",
            [(LEAK_CARD,), (MISSING_CARD,), (GOOD_CARD,)])
    try:
        # No `with`: the lifespan would re-run migrations and a full cards sync,
        # neither of which this test is about.
        yield TestClient(app)
    finally:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM runs WHERE card_id = ANY(%s)",
                        ([LEAK_CARD, MISSING_CARD, GOOD_CARD],))
            cur.execute("DELETE FROM cards WHERE card_id = ANY(%s)",
                        ([LEAK_CARD, MISSING_CARD, GOOD_CARD],))


def _post(client, card_id):
    return client.post("/runs", json={"card_id": card_id, "arm": "rules"})


def test_a_pack_that_leaks_the_answer_is_evidence_leak(client):
    r = _post(client, LEAK_CARD)
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "evidence_leak"
    assert body["error"]["detail"]["card_id"] == LEAK_CARD
    # The reason names the offending key, and the response does NOT carry the
    # value that was about to leak.
    assert "ground_truth" in body["error"]["detail"]["detail"]
    assert "cart" not in body["error"]["detail"]["detail"]


def test_a_pack_that_is_not_there_is_evidence_missing(client):
    r = _post(client, MISSING_CARD)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "evidence_missing"


def test_neither_bad_card_was_queued(client):
    """The gate is in front of the queue: a refused card costs nothing and leaves nothing."""
    _post(client, LEAK_CARD)
    _post(client, MISSING_CARD)
    with __import__("service.app.db", fromlist=["db"]).connect(autocommit=True) as conn, \
            conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM runs WHERE card_id = ANY(%s)",
                    ([LEAK_CARD, MISSING_CARD],))
        assert cur.fetchone()[0] == 0


def test_the_gate_is_not_simply_rejecting_everything(client):
    """A card whose pack is absent from the FIXTURE root still fails -- correctly.

    The control that matters is the opposite one: the same card passes when the
    evidence root is the real one, so `evidence_missing` is a statement about the
    pack and not about the card id.
    """
    import pathlib

    from service.app import harness
    assert _post(client, GOOD_CARD).json()["error"]["code"] == "evidence_missing"
    real_root = pathlib.Path(__file__).resolve().parents[2] / "evidence"
    harness.EVIDENCE_DIR = real_root
    try:
        r = _post(client, GOOD_CARD)
        # 202 (queued) or 429 (budget), but never a 422: the pack is fine.
        assert r.status_code in (202, 429), r.text
    finally:
        harness.EVIDENCE_DIR = pathlib.Path(FIXTURE_ROOT)
