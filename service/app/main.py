#!/usr/bin/env python3
"""main.py -- the FastAPI app: migrations on startup, GET /healthz.

Step 1 of docs/design/service_v1.md §7. The run submission, the queue worker and
the query endpoints land in steps 3 and 4; what is here is the skeleton they
attach to and the one endpoint that answers "is this thing up".

WHY /healthz TOUCHES THE DATABASE AND WHY IT COUNTS MONEY
---------------------------------------------------------
A liveness probe that only proves the web process is alive would be green on a
service that cannot do a single useful thing. So the check is the real budget
query -- `SUM(cost_usd)` over today's runs -- which fails unless the connection
works AND the schema is actually applied. A container whose migrations silently
did not run reports 503 rather than pretending.

The budget number itself is service-state (§4): `max_usd_per_card` in
scripts/agent/config.yaml stops one runaway card, and stops nothing at all about
someone POSTing a thousand times. The daily budget is the breaker denominated in
TIME, which is the one an HTTP entry point needs. Note the asymmetry §4 asks
for: at zero remaining, /healthz stays **200** -- the service is healthy, it just
will not take work. 503 is reserved for "the database is not there".
"""
import contextlib
import json
import sys
import time

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from . import cards_sync, config, db, migrate, reimport
from .models import ApiError, Health, ReimportResult, install_error_handlers

# §5: structured JSON on stdout, picked up by docker's json-file driver. No file
# sink, no shipper. Every line will carry run_id once there are runs to carry --
# the same value as the trace attribute rca.run_id, so logs and traces join.
def log(event: str, **fields) -> None:
    print(json.dumps({"ts": time.time(), "event": event, **fields}, default=str), file=sys.stdout, flush=True)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Retry rather than crash. compose already orders us behind the db's
    # healthcheck, but `restart: unless-stopped` plus a crash loop turns a slow
    # database into a container that is never up long enough to ask why -- and a
    # started-but-unmigrated process still answers /healthz with a 503 that says
    # so, which is more diagnostic than exit code 1.
    for attempt in range(10):
        try:
            with db.connect(autocommit=True) as conn:
                applied = migrate.apply_pending(conn)
                log("migrations.applied", applied=applied)
                # The cards snapshot is refreshed on every boot rather than by a
                # command someone has to remember. scenarios/*.yaml is the
                # authority (§3) and it moves whenever a batch lands, so a
                # snapshot that is only as fresh as the last manual sync is a
                # snapshot nobody can trust. The upsert is a no-op when nothing
                # changed, which is what makes running it unconditionally cheap.
                log("cards.synced", **cards_sync.sync(conn))
            break
        except Exception as exc:                      # noqa: BLE001 -- reported, not swallowed
            log("startup.failed", attempt=attempt + 1, error=str(exc))
            time.sleep(1)
    yield


app = FastAPI(
    title="rca-agent-bench evaluation service",
    version="1.0.0",
    lifespan=lifespan,
)
install_error_handlers(app)


@app.get("/healthz", response_model=Health, responses={503: {"model": Health}})
def healthz():
    budget = config.daily_budget_usd()
    try:
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM runs "
                "WHERE created_at > date_trunc('day', now())"
            )
            spent = float(cur.fetchone()[0])
    except Exception as exc:                          # noqa: BLE001
        log("healthz.db_error", error=str(exc))
        # Remaining budget is unknown, and "unknown" must not read as "plenty":
        # report 0, which is already the vocabulary for "not accepting work".
        return JSONResponse(
            status_code=503,
            content=Health(status="degraded", db="error", budget_remaining_usd=0.0).model_dump(),
        )
    # cost_usd is numeric(10,6); round to the same resolution so the JSON does not
    # carry float noise the column cannot hold.
    remaining = round(max(budget - spent, 0.0), 6)
    return Health(status="ok", db="ok", budget_remaining_usd=remaining)


@app.post("/admin/reimport", response_model=ReimportResult)
def admin_reimport():
    """Backfill artifacts/agent_runs/ into Postgres. Local, idempotent, re-runnable.

    404 rather than 403 when disabled: see config.admin_enabled(). Runs
    synchronously -- it is a few hundred file reads against a local mount, the
    caller is a human on a loopback port, and putting it on the queue would make
    the one endpoint that repairs the database depend on the worker that reads it.
    """
    if not config.admin_enabled():
        raise ApiError(404, "not_found", "admin endpoints are disabled")
    with db.connect(autocommit=True) as conn:
        result = reimport.reimport(conn)
    log("admin.reimport", **{k: v for k, v in result.items() if k != "failed"},
        failed=len(result["failed"]))
    return result
