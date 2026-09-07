#!/usr/bin/env python3
"""config.py -- everything the service reads from its environment, in one place.

THE DATABASE PASSWORD IS NOT IN RCA_DB_URL, ON PURPOSE
------------------------------------------------------
Design §5 writes the URL as `postgresql://rca@db/rca` -- no password -- and puts
the password in a docker secret file mounted at /run/secrets/pg. That split is
the point: the URL is an ordinary environment variable and shows up in
`docker inspect`, `docker compose config`, and any crash dump that prints the
environment; the secret file does not. So the password is read from the file at
connect time and spliced into the URL here, percent-encoded, and the composed
DSN is never logged.

`database_url()` leaves the URL alone if it already carries a password (a local
`RCA_DB_URL=postgresql://rca:pw@localhost/rca` for running the tests outside
compose) and if no secret file is present. Neither is the deployed path.
"""
import os
from urllib.parse import quote, urlsplit, urlunsplit

# Design §4: the daily budget is service-state, not config.yaml state. It is the
# only breaker that counts spend per unit of TIME, which is what an HTTP entry
# point needs -- max_usd_per_card stops one runaway card, not a thousand POSTs.
DEFAULT_DAILY_BUDGET_USD = 5.00

DEFAULT_DB_URL = "postgresql://rca@db/rca"
DEFAULT_PG_PASSWORD_FILE = "/run/secrets/pg"


def daily_budget_usd() -> float:
    raw = os.environ.get("RCA_DAILY_BUDGET_USD", "")
    if not raw.strip():
        return DEFAULT_DAILY_BUDGET_USD
    return float(raw)


def pg_password() -> str | None:
    """The docker secret, or None when there is no secret file to read."""
    path = os.environ.get("RCA_PG_PASSWORD_FILE", DEFAULT_PG_PASSWORD_FILE)
    try:
        with open(path, encoding="utf-8") as fh:
            # Match the postgres entrypoint, which reads the file with `$(< file)`
            # and therefore drops trailing newlines but nothing else. Stripping
            # more than that would silently change a password with real spaces.
            return fh.read().rstrip("\r\n") or None
    except OSError:
        return None


def database_url() -> str:
    """RCA_DB_URL with the docker secret spliced in as the password."""
    url = os.environ.get("RCA_DB_URL", DEFAULT_DB_URL)
    parts = urlsplit(url)
    if parts.password:
        return url                      # caller supplied one; do not second-guess it
    password = pg_password()
    if password is None:
        return url
    host = parts.hostname or ""
    if ":" in host:                     # IPv6 literal
        host = f"[{host}]"
    if parts.port:
        host = f"{host}:{parts.port}"
    userinfo = f"{quote(parts.username or '', safe='')}:{quote(password, safe='')}"
    return urlunsplit((parts.scheme, f"{userinfo}@{host}", parts.path, parts.query, parts.fragment))


def admin_enabled() -> bool:
    """Whether /admin/* exists at all.

    The port is already bound to 127.0.0.1 only (决策 037), so this is the second
    layer, not the first. It earns its place because the two layers fail
    differently: the port binding is a deployment property that an `ssh -L`, a
    reverse proxy or an edited compose file can undo without touching this repo,
    while this one travels with the image. Anything but a truthy value hides the
    endpoint as 404 rather than 403 -- a disabled admin surface should not
    advertise that it exists.
    """
    return os.environ.get("RCA_ADMIN_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
