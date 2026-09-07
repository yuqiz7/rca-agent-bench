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
import hashlib
import json
import time
import uuid

from fastapi import FastAPI
from fastapi.responses import JSONResponse

import psycopg

from . import cards_sync, config, db, harness, migrate, reimport, repo
from .logs import log
from .models import (ApiError, Health, ReimportResult, RunCreate, RunRef,
                     install_error_handlers)


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


# --------------------------------------------------------------------------- #
# POST /runs (§2)
# --------------------------------------------------------------------------- #

def _request_digest(body: RunCreate) -> str:
    """What "same body" means for the idempotency key.

    Canonical JSON over the request minus the key itself, so field order and
    whitespace cannot make two identical submissions look different. The key is
    excluded because it is the name of the comparison, not part of what is being
    compared.
    """
    payload = {"card_id": body.card_id, "arm": body.arm, "model": body.model}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@app.post("/runs", status_code=202, response_model=RunRef)
def create_run(body: RunCreate):
    """Accept a run, or refuse it before it can cost anything.

    The four gates run in this order because each is cheaper and more certain than
    the next, and because §4 is explicit that the expensive checks must not be
    what protects the budget:

      1. card_not_found (404) -- a card not in the snapshot, or not in stock.
      2. evidence_leak (422)  -- leak_check.check_card on the pack. run_card runs
         this again internally before the first token; this outer one exists
         because the service adds "automatic, batched, unattended" to the picture,
         and in that setting a broken pack should never enter a system that will
         retry it (§4).
      3. budget_exceeded (429) -- the daily breaker. The rules arm passes through
         it too even though it costs nothing: one rule that is always true beats
         two rules that are usually true, and an arm-dependent breaker is exactly
         the sort of thing that is correct until someone adds a fourth arm.
      4. the idempotency key.
    """
    with db.connect(autocommit=True) as conn:
        with conn.cursor() as cur:
            card = repo.get_card(cur, body.card_id)
        if card is None:
            raise ApiError(404, "card_not_found",
                           f"no card {body.card_id!r} in the in-stock set",
                           {"card_id": body.card_id})
        if not card["in_stock"]:
            raise ApiError(404, "card_not_found",
                           f"card {body.card_id!r} is not in stock",
                           {"card_id": body.card_id, "in_stock": False})

        try:
            harness.check_card(body.card_id)
        except Exception as exc:                  # noqa: BLE001 -- LeakError, or a pack that will not open
            log("runs.evidence_leak", card_id=body.card_id, error=str(exc))
            raise ApiError(422, "evidence_leak",
                           f"card {body.card_id!r} failed the entry leak check",
                           {"card_id": body.card_id, "detail": str(exc)[:400]}) from None

        budget = config.daily_budget_usd()
        with conn.cursor() as cur:
            spent = repo.spent_today(cur)
        if spent >= budget:
            raise ApiError(429, "budget_exceeded",
                           "daily budget is spent; no new runs today",
                           {"budget_usd": budget, "spent_usd": round(spent, 6),
                            "remaining_usd": 0.0})

        digest = _request_digest(body)
        # rules calls no model; every other arm resolves through config.yaml
        # rather than through a constant here (§4: the service does not hold a
        # second opinion about models.primary).
        model = "none" if body.arm == "rules" else (body.model or harness.primary_model())
        run_id = uuid.uuid4()
        run = {
            "run_id": run_id,
            "card_id": body.card_id,
            "arm": body.arm,
            "model": model,
            "status": "queued",
            "idempotency_key": body.idempotency_key,
            "run_config": {"request": {"card_id": body.card_id, "arm": body.arm,
                                       "model": body.model},
                           "request_digest": digest,
                           "resolved_model": model},
        }

        # INSERT FIRST, then resolve the conflict. A read-then-write would leave a
        # window between "no run with this key" and the insert, and two retries
        # landing in that window is exactly the double charge the key exists to
        # prevent. The runs_idem partial unique index from step 1 is what actually
        # decides; this code only reports what it decided.
        try:
            with conn.transaction(), conn.cursor() as cur:
                repo.insert_run(cur, run)
        except psycopg.errors.UniqueViolation:
            with conn.cursor() as cur:
                existing = repo.get_run_by_idempotency_key(cur, body.idempotency_key)
            if existing is None:                  # the other writer rolled back
                raise ApiError(409, "idempotency_conflict",
                               "idempotency key is in flight; retry",
                               {"idempotency_key": body.idempotency_key}) from None
            if ((existing.get("run_config") or {}).get("request_digest")) != digest:
                raise ApiError(409, "idempotency_conflict",
                               "idempotency key was already used with a different body",
                               {"idempotency_key": body.idempotency_key,
                                "run_id": str(existing["run_id"])}) from None
            log("runs.idempotent_replay", run_id=str(existing["run_id"]),
                idempotency_key=body.idempotency_key)
            return RunRef(run_id=existing["run_id"], status=existing["status"],
                          poll=f"/runs/{existing['run_id']}")

    log("runs.queued", run_id=str(run_id), card_id=body.card_id, arm=body.arm, model=model)
    return RunRef(run_id=run_id, status="queued", poll=f"/runs/{run_id}")
