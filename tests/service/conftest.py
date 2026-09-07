"""Local-gate fixtures: a real Postgres, in a schema of its own.

WHY A REAL POSTGRES AND NOT SQLITE (design §6). The schema this exercises uses
`FOR UPDATE SKIP LOCKED`, `jsonb`, a partial UNIQUE index and `timestamptz`.
SQLite supports none of them, so a green suite on SQLite would be green exactly
where this service is most likely to be wrong -- the queue under concurrency.
And not testcontainers either: it pulls an image, and CI's first hard rule is
that no gate touches the network. docker-compose.service.yml already runs a
Postgres; these tests connect to it.

ISOLATION IS A THROWAWAY SCHEMA, NOT A TRANSACTION. The concurrency test needs
two connections to see each other's committed rows, which a
wrap-each-test-in-a-rollback fixture makes impossible by construction. So the
session creates `svc_test_<pid>_<n>`, applies the real migrations into it, points
every connection's search_path at it, and drops it CASCADE at the end. The 400+
rows in `public` are never read and never written -- and because the migrations
run for real, the tests also assert that migrations apply to an empty database.
"""
import os
import uuid

import pytest

TEST_DB_URL = os.environ.get("RCA_TEST_DB_URL")
SKIP_REASON = ("RCA_TEST_DB_URL is not set -- local gate; see docs/workflow.md "
               "(design §6: DB tests are a local gate, never a CI one)")



@pytest.fixture(scope="session")
def schema():
    psycopg = pytest.importorskip("psycopg", reason=SKIP_REASON)
    if not TEST_DB_URL:
        pytest.skip(SKIP_REASON)
    name = f"svc_test_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    with psycopg.connect(TEST_DB_URL, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{name}"')
    try:
        yield name
    finally:
        with psycopg.connect(TEST_DB_URL, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')


@pytest.fixture(scope="session")
def connect(schema):
    """Open a connection whose search_path is the throwaway schema."""
    psycopg = pytest.importorskip("psycopg", reason=SKIP_REASON)

    def _connect(autocommit=True):
        return psycopg.connect(TEST_DB_URL, autocommit=autocommit,
                               options=f"-c search_path={schema}")
    return _connect


@pytest.fixture(scope="session", autouse=True)
def migrated(connect):
    from service.app import migrate
    with connect(autocommit=True) as conn:
        applied = migrate.apply_pending(conn)
    # Applying to an empty schema must apply everything; this is also the only
    # place the migration runner is exercised against a database that has never
    # seen it before.
    assert applied == ["001_init.sql", "002_reimport_dedup.sql"], applied
    return applied


@pytest.fixture(scope="session", autouse=True)
def cards(connect, migrated):
    """runs.card_id is a foreign key; the tests need cards to point at."""
    rows = [("test-card-a", "crash", "cart", "kill_container"),
            ("test-card-b", "latency", "quote", "delay_outbound")]
    with connect(autocommit=True) as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO cards (card_id, class, target, primitive, in_stock, evidence_ok) "
            "VALUES (%s, %s, %s, %s, true, true)", rows)
    return [r[0] for r in rows]


@pytest.fixture
def empty_queue(connect):
    """Start a queue test from an empty `runs` table.

    Function-scoped and explicit rather than autouse: the tests that do NOT ask
    for it are asserting things about rows they inserted themselves, and having
    every test silently truncate the table would hide an accidental dependency
    between them instead of preventing one. `runs` cascades to the child tables.
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM runs")
