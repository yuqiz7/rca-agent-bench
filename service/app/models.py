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
from pydantic import BaseModel
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
