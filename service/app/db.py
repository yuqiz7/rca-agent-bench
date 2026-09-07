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

import psycopg

from . import config


@contextlib.contextmanager
def connect(*, autocommit: bool = False):
    """A psycopg connection to RCA_DB_URL, closed on the way out."""
    conn = psycopg.connect(config.database_url(), autocommit=autocommit)
    try:
        yield conn
    finally:
        conn.close()
