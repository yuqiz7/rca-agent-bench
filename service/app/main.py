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
import binascii
import contextlib
import hashlib
import json
import time
import uuid
from uuid import UUID

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

import psycopg

from . import cards_sync, config, db, harness, migrate, reimport, repo, summary as summary_mod
from .logs import log
from .models import (Answer, ApiError, Card, Grade, Health, Page, ReimportResult,
                     Run, RunCreate, RunRef, RunTrace, Summary, TraceModelCall,
                     TraceStep, TraceToolCall, install_error_handlers)


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


def _replay_or_conflict(existing: dict, digest: str, key: str) -> RunRef:
    """Same key: hand back the original run, or refuse if the body differs."""
    if ((existing.get("run_config") or {}).get("request_digest")) != digest:
        raise ApiError(409, "idempotency_conflict",
                       "idempotency key was already used with a different body",
                       {"idempotency_key": key, "run_id": str(existing["run_id"])})
    return RunRef(run_id=existing["run_id"], status=existing["status"],
                  poll=f"/runs/{existing['run_id']}")


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
      3. the idempotency key -- looked up BEFORE the budget gate. A replay does
         not spend anything: it hands back a run that already exists. Making it
         pass the budget check would mean that the moment the daily budget runs
         out, a retrying client stops being able to find out its own run_id --
         the breaker would start withholding information instead of withholding
         money. The gate belongs in front of work that will newly charge.
      4. budget_exceeded (429) -- the daily breaker, on new runs only. The rules
         arm passes through it too even though it costs nothing: one rule that is
         always true beats two rules that are usually true, and an arm-dependent
         breaker is exactly the sort of thing that is correct until someone adds a
         fourth arm.
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

        digest = _request_digest(body)

        # The idempotency lookup, ahead of the budget gate. This is the read half;
        # the write half below still resolves a genuine race through the unique
        # index, because two retries can arrive between this SELECT and that
        # INSERT and only the database can order them.
        if body.idempotency_key is not None:
            with conn.cursor() as cur:
                existing = repo.get_run_by_idempotency_key(cur, body.idempotency_key)
            if existing is not None:
                replay = _replay_or_conflict(existing, digest, body.idempotency_key)
                log("runs.idempotent_replay", run_id=str(existing["run_id"]),
                    idempotency_key=body.idempotency_key, status=existing["status"])
                return replay

        budget = config.daily_budget_usd()
        with conn.cursor() as cur:
            spent = repo.spent_today(cur)
        if spent >= budget:
            raise ApiError(429, "budget_exceeded",
                           "daily budget is spent; no new runs today",
                           {"budget_usd": budget, "spent_usd": round(spent, 6),
                            "remaining_usd": 0.0})

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
            replay = _replay_or_conflict(existing, digest, body.idempotency_key)
            log("runs.idempotent_replay", run_id=str(existing["run_id"]),
                idempotency_key=body.idempotency_key, status=existing["status"], raced=True)
            return replay

    log("runs.queued", run_id=str(run_id), card_id=body.card_id, arm=body.arm, model=model)
    return RunRef(run_id=run_id, status="queued", poll=f"/runs/{run_id}")


# --------------------------------------------------------------------------- #
# read side (§2 endpoint table)
# --------------------------------------------------------------------------- #

def _to_run(row: dict, grade: dict | None) -> Run:
    """A runs row (+ its grade) as the §2 Run model.

    answer is None rather than {"service": null} when the run never answered:
    §2 types it `Answer | None`, and a half-empty object would make "no answer"
    and "answered with nothing" indistinguishable to a client.
    """
    answer = None
    if row.get("answer_service") or row.get("answer_fault"):
        answer = Answer(service=row.get("answer_service") or "",
                        fault_type=row.get("answer_fault") or "")
    return Run(
        run_id=row["run_id"], card_id=row["card_id"], arm=row["arm"], model=row["model"],
        status=row["status"], created_at=row["created_at"], started_at=row["started_at"],
        finished_at=row["finished_at"], answer=answer,
        grade=Grade(**grade) if grade else None,
        terminated=row["terminated"], steps=row["steps"],
        cost_usd=float(row["cost_usd"]) if row["cost_usd"] is not None else None,
        wall_s=float(row["wall_s"]) if row["wall_s"] is not None else None,
        counters=row["counters"], error=row["error"],
    )


@app.get("/runs", response_model=Page[Run])
def list_runs(card: str | None = None, arm: str | None = None, status: str | None = None,
              model: str | None = None,
              limit: int = Query(50, ge=1, le=200), cursor: str | None = None):
    with db.connect(autocommit=True) as conn, conn.cursor() as cur:
        try:
            rows, next_cursor = repo.list_runs(cur, card_id=card, arm=arm, status=status,
                                               model=model, limit=limit, cursor=cursor)
        except (ValueError, TypeError, binascii.Error) as exc:
            # A cursor is opaque to the client, so a bad one is a client error with
            # nothing to fix in the query -- 400 with a code, not a 500.
            raise ApiError(400, "bad_cursor", "cursor is not a cursor this API issued",
                           {"cursor": cursor, "detail": str(exc)[:200]}) from None
        grades = repo.grades_for(cur, [r["run_id"] for r in rows])
    return Page[Run](items=[_to_run(r, grades.get(r["run_id"])) for r in rows],
                     next_cursor=next_cursor)


@app.get("/runs/{run_id}", response_model=Run)
def get_run(run_id: UUID):
    with db.connect(autocommit=True) as conn, conn.cursor() as cur:
        row = repo.get_run(cur, run_id)
        if row is None:
            raise ApiError(404, "run_not_found", f"no run {run_id}", {"run_id": str(run_id)})
        grade = repo.get_grade(cur, run_id)
    return _to_run(row, {"top1_ok": grade["top1_ok"], "service_ok": grade["service_ok"]}
                   if grade else None)


@app.get("/runs/{run_id}/trace", response_model=RunTrace)
def get_run_trace(run_id: UUID):
    with db.connect(autocommit=True) as conn, conn.cursor() as cur:
        row = repo.get_run(cur, run_id)
        if row is None:
            raise ApiError(404, "run_not_found", f"no run {run_id}", {"run_id": str(run_id)})
        steps = repo.get_steps(cur, run_id)
        model_calls = repo.get_model_calls(cur, run_id)
        tool_calls = repo.get_tool_calls(cur, run_id)

    # Three queries and a group-by in Python, not one query per step: the tree is
    # small and bounded by max_steps (20), and this keeps /runs/{id}/trace at a
    # fixed query count no matter how long the run was.
    by_step_models, by_step_tools = {}, {}
    for call in model_calls:
        by_step_models.setdefault(call["step_no"], []).append(TraceModelCall(
            model=call["model"], input_tokens=call["input_tokens"],
            output_tokens=call["output_tokens"], cache_write_tokens=call["cache_write_tokens"],
            cache_read_tokens=call["cache_read_tokens"],
            step_cost_usd=float(call["step_cost_usd"]) if call["step_cost_usd"] is not None else None,
            stop_reason=call["stop_reason"],
            latency_s=float(call["latency_s"]) if call["latency_s"] is not None else None))
    for call in tool_calls:
        by_step_tools.setdefault(call["step_no"], []).append(TraceToolCall(
            tool=call["tool"], args_digest=call["args_digest"],
            result_bytes=call["result_bytes"], ok=call["ok"],
            validation_reject=call["validation_reject"], retried=call["retried"],
            error=call["error"]))

    return RunTrace(
        run_id=row["run_id"], card_id=row["card_id"], arm=row["arm"],
        model=row["model"], status=row["status"],
        steps=[TraceStep(
            step_no=s["step_no"],
            duration_ms=float(s["duration_ms"]) if s["duration_ms"] is not None else None,
            model_calls=by_step_models.get(s["step_no"], []),
            tool_calls=by_step_tools.get(s["step_no"], []),
        ) for s in steps])


@app.get("/cards", response_model=list[Card])
def list_cards(cardset: str | None = None, in_stock: bool | None = None):
    wanted = None
    if cardset is not None:
        wanted = summary_mod.load_cardset(cardset)
        if wanted is None:
            raise ApiError(404, "cardset_not_found", f"no cardset {cardset!r}",
                           {"cardset": cardset,
                            "known": sorted(summary_mod.available_cardsets())})
        wanted = set(wanted)
    with db.connect(autocommit=True) as conn, conn.cursor() as cur:
        rows = repo.list_cards(cur, in_stock=in_stock)
    return [_to_card(r) for r in rows if wanted is None or r["card_id"] in wanted]


@app.get("/cards/{card_id}", response_model=Card)
def get_card(card_id: str):
    with db.connect(autocommit=True) as conn, conn.cursor() as cur:
        row = repo.get_card(cur, card_id)
    if row is None:
        raise ApiError(404, "card_not_found", f"no card {card_id!r}", {"card_id": card_id})
    return _to_card(row)


def _to_card(row: dict) -> Card:
    # Named fields only. Never `Card(**row)`: that would forward whatever the
    # table happens to have, which is the mechanism by which an answer column
    # would one day reach a client (§1).
    return Card(card_id=row["card_id"], **{"class": row["class"]}, target=row["target"],
                primitive=row["primitive"], difficulty=row["difficulty"],
                in_stock=row["in_stock"], evidence_ok=row["evidence_ok"],
                snapshot_at=row["snapshot_at"])


@app.get("/summary", response_model=Summary)
def get_summary(cardset: str, arms: str,
                pick: str = "latest", runs: str | None = None):
    """The four-metric table, aggregated by compare_arms.metrics() (决策 037)."""
    cards = summary_mod.load_cardset(cardset)
    if cards is None:
        raise ApiError(404, "cardset_not_found", f"no cardset {cardset!r}",
                       {"cardset": cardset, "known": sorted(summary_mod.available_cardsets())})
    if pick not in ("latest", "run_ids"):
        raise ApiError(400, "bad_pick", "pick must be 'latest' or 'run_ids'", {"pick": pick})
    parsed = summary_mod.parse_arms(arms)
    if not parsed:
        raise ApiError(400, "arm_unknown", "arms is empty", {"arms": arms})

    run_ids = None
    if pick == "run_ids":
        try:
            run_ids = [UUID(u.strip()) for u in (runs or "").split(",") if u.strip()]
        except ValueError as exc:
            raise ApiError(400, "bad_request", "runs must be a comma-separated uuid list",
                           {"detail": str(exc)[:200]}) from None
        if not run_ids:
            raise ApiError(400, "bad_request", "pick=run_ids needs &runs=<uuid,...>", {})

    with db.connect(autocommit=True) as conn, conn.cursor() as cur:
        result = summary_mod.compute(cur, cardset, cards, parsed, pick=pick, run_ids=run_ids)
    return result
