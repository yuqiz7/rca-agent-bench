#!/usr/bin/env python3
"""models.py -- the wire contract of docs/design/service_v1.md §2.

Request/response models are the design's, field for field. The error envelope is
here too rather than in main.py because it is a contract, not plumbing: §2 says
every 4xx on every endpoint carries the same three keys and that clients read
`code`, never the prose and never the HTTP status. Keeping the shape, the stable
code vocabulary, and the handlers that produce them in one file is what makes
that promise checkable in one place -- and tests/test_api_contract.py (step 5)
will check exactly this file against the OpenAPI snapshot.

`Page[T]` uses PEP 695 generic syntax, which needs Python 3.12; the image and CI
are both on 3.12 (.github/workflows/ci.yml pins it).
"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException


# --------------------------------------------------------------------------- #
# §2 request / response schema
# --------------------------------------------------------------------------- #

class RunCreate(BaseModel):
    card_id: str
    arm: Literal["agent", "single_shot", "rules"]
    model: str | None = None          # None -> config.yaml 的 models.primary
    idempotency_key: str | None = None


class RunRef(BaseModel):              # 202 的响应体
    run_id: UUID
    # §2 writes this as Literal["queued"], which is true of a fresh submission and
    # false of the one case the key exists for: a retry whose original has already
    # started or finished. Returning "queued" for a run that is `succeeded` would
    # make the idempotent reply lie about the thing the caller is about to poll
    # for, so the field carries the run's actual status.
    status: Literal["queued", "running", "succeeded", "failed"]
    poll: str                         # "/runs/{run_id}"


class Grade(BaseModel):
    top1_ok: bool
    service_ok: bool


class Answer(BaseModel):
    service: str
    fault_type: str


class Run(BaseModel):
    run_id: UUID
    card_id: str
    arm: str
    model: str
    status: Literal["queued", "running", "succeeded", "failed"]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    answer: Answer | None
    grade: Grade | None
    terminated: str | None            # submit / max_steps / cost_cap / no_submit / api_error
    steps: int | None
    cost_usd: float | None
    wall_s: float | None
    counters: dict[str, int] | None   # 五道保险
    error: str | None


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None


# --------------------------------------------------------------------------- #
# cards / summary / trace -- the read side (§2 endpoint table)
# --------------------------------------------------------------------------- #

class Card(BaseModel):
    """The snapshot fields, and ONLY the snapshot fields.

    There is no ground_truth here and there is nowhere for one to appear: the
    model is closed over eight named fields, so even if a future query started
    selecting `*` from a table that had grown an answer column, this would refuse
    to carry it. §1 puts the reason plainly -- leak_check guards the line that a
    card's answer must not be reachable, and an endpoint anyone with the port can
    call is exactly where that line would break.
    """
    model_config = ConfigDict(populate_by_name=True)

    card_id: str
    # `class` is a Python keyword; the wire name is still "class" (§3's column).
    class_: str = Field(alias="class")
    target: str
    primitive: str
    difficulty: str | None
    in_stock: bool
    evidence_ok: bool
    snapshot_at: datetime


class ArmMetrics(BaseModel):
    """compare_arms.metrics() output, field for field, plus what /summary adds.

    The names are not re-chosen here: n / missing / top1 / svc / top1_pct /
    svc_pct / steps / cost / total / p95 / per_hit are the keys that function
    returns, and 决策 037 item 3 is explicit that the endpoint reuses it rather
    than re-deriving the same table. `selected` and `arm` / `model` are the only
    additions, and they describe WHICH runs were fed in, not what was computed.
    """
    arm: str
    model: str | None
    selected: int                     # runs actually chosen for this arm
    n: int
    top1: int
    svc: int
    top1_pct: float
    svc_pct: float
    steps: float
    cost: float
    total: float
    p95: float
    per_hit: float | None             # None at zero hits -- never rendered as 0
    missing: list[str]                # cards with no succeeded run; counted as misses


class Summary(BaseModel):
    cardset: str
    n_cards: int
    pick: Literal["latest", "run_ids"]
    arms: list[ArmMetrics]


class TraceToolCall(BaseModel):
    tool: str
    args_digest: str | None
    result_bytes: int | None
    ok: bool | None
    validation_reject: bool
    retried: bool
    error: str | None


class TraceModelCall(BaseModel):
    model: str
    input_tokens: int | None
    output_tokens: int | None
    cache_write_tokens: int | None
    cache_read_tokens: int | None
    step_cost_usd: float | None
    stop_reason: str | None
    latency_s: float | None


class TraceStep(BaseModel):
    step_no: int
    duration_ms: float | None
    model_calls: list[TraceModelCall]
    tool_calls: list[TraceToolCall]


class RunTrace(BaseModel):
    """card -> step -> model.call / tool.* , the tree §1 promises.

    NULLs are returned as null. The backfill could not measure duration_ms or
    latency_s (the evaluator records neither), and filling them by dividing wall_s
    would produce numbers indistinguishable from measured ones.
    """
    run_id: UUID
    card_id: str
    arm: str
    model: str
    status: str
    steps: list[TraceStep]


class ReimportFailure(BaseModel):
    artifact_path: str
    card_id: str | None
    error: str


class ReimportResult(BaseModel):
    scanned: int
    imported: int
    skipped: int
    failed: list[ReimportFailure]


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    db: Literal["ok", "error"]
    budget_remaining_usd: float


# --------------------------------------------------------------------------- #
# §2 error envelope -- one shape for every 4xx, on every endpoint
# --------------------------------------------------------------------------- #

# THE ERROR CODE TABLE. `code` is the stable machine-readable string §2 promises;
# clients branch on it and never on the prose or the HTTP status. Settled here
# rather than left to each raise site, because §2 and §4 of the design disagreed:
# §2's example list says `evidence_missing`, §4's prose says `evidence_leak`. They
# are now BOTH codes and they mean different things --
#
#   evidence_leak     the pack exists and would put the answer in front of the
#                     model. leak_check.LeakError. A card that must never run.
#   evidence_missing  the pack is absent, truncated or unreadable. A card that
#                     cannot run yet.
#
# Collapsing them was wrong in a specific way: the first is a correctness
# emergency and the second is an ops chore, and a client that cannot tell them
# apart will retry both or neither.
ERROR_CODES = (
    "card_not_found",       # 404  no such card, or not in stock
    "run_not_found",        # 404  no such run
    "cardset_not_found",    # 404  no such cardset under scripts/baselines/
    "not_found",            # 404  unrouted path, or /admin/* while disabled
    "arm_unknown",          # 400  arm spec empty or unrecognised
    "bad_cursor",           # 400  cursor was not issued by this API
    "bad_pick",             # 400  pick is neither latest nor run_ids
    "bad_request",          # 400  otherwise-malformed query
    "validation_error",     # 422  body/query does not match the schema
    "evidence_leak",        # 422  leak_check.LeakError -- the pack leaks the answer
    "evidence_missing",     # 422  the pack is absent or unreadable
    "idempotency_conflict", # 409  same key, different body (or in flight)
    "budget_exceeded",      # 429  daily budget spent
    "method_not_allowed",   # 405
    "internal_error",       # 5xx
)


class ErrorDetail(BaseModel):
    code: str                         # stable, machine-readable; clients branch on this
    message: str                      # for humans; may be reworded at any time
    detail: dict | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class ApiError(Exception):
    """Raise this instead of HTTPException when the code matters -- it always does.

    The status code stays deliberately dumb: §2 says HTTP status carries no
    semantic detail, so 404 means "not here" and `code` says which "not here".
    """

    def __init__(self, status_code: int, code: str, message: str, detail: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail = detail


# Fallback codes for 4xx that no handler of ours raised -- an unrouted path, a
# wrong verb, a body FastAPI rejected before any endpoint ran. Without these,
# those responses would be the one family of 4xx that does not carry a `code`,
# and "read code, not status" would be true of the endpoints but not of the API.
_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
}


def error_response(status_code: int, code: str, message: str, detail: dict | None = None) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(code=code, message=message, detail=detail))
    return JSONResponse(status_code=status_code, content=jsonable_encoder(body))


def install_error_handlers(app: FastAPI) -> None:
    """Make every 4xx (and any 5xx we raise as an HTTPException) use the envelope."""

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return error_response(exc.status_code, exc.code, exc.message, exc.detail)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        # An HTTPException raised with a dict detail may name its own code.
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            return error_response(
                exc.status_code,
                exc.detail["code"],
                exc.detail.get("message", ""),
                exc.detail.get("detail"),
            )
        code = _STATUS_CODES.get(exc.status_code, "internal_error" if exc.status_code >= 500 else "error")
        return error_response(exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        return error_response(
            422,
            "validation_error",
            "request body or query does not match the schema",
            {"errors": jsonable_encoder(exc.errors())},
        )
