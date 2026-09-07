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

from . import config, db, migrate
from .models import Health, install_error_handlers

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
            with db.connect() as conn:
                applied = migrate.apply_pending(conn)
                conn.commit()
            log("migrations.applied", applied=applied)
            break
        except Exception as exc:                      # noqa: BLE001 -- reported, not swallowed
            log("migrations.failed", attempt=attempt + 1, error=str(exc))
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
