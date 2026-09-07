#!/usr/bin/env python3
"""db.py -- connections to the service's Postgres.

One connection per unit of work, no pool. Design §4 caps concurrency at a single
worker and §2 puts the whole consumer set at "curl and a static page", so the
request rate a pool would amortise does not exist yet; adding psycopg_pool now
would be a dependency and a lifecycle bought with nothing. When step 3 adds the
worker loop it gets its own long-lived connection, which is the case a pool would
not have helped with either.
"""
import contextlib

from . import config


@contextlib.contextmanager
def connect(*, autocommit: bool = False):
    """A psycopg connection to RCA_DB_URL, closed on the way out.

    psycopg is imported HERE, not at module level, so that building the FastAPI
    app requires no database driver at all. CI gate 4 is an offline contract test
    (design §6): it constructs the app, reads app.openapi() and asks the Pydantic
    models what fields they carry, and none of that should oblige the CI runner to
    install a Postgres driver -- for the same reason harness.py imports the arm
    modules lazily so the app builds with no anthropic client. A process that
    actually talks to the database fails here, loudly, on the first connect.
    """
    import psycopg                                   # noqa: PLC0415

    conn = psycopg.connect(config.database_url(), autocommit=autocommit)
    try:
        yield conn
    finally:
        conn.close()
