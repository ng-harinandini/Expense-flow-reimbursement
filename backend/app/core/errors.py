"""Translation of domain / infrastructure errors into HTTP responses.

Routes raise (or let bubble) :class:`~app.domain.errors.DomainError`; these handlers assign the
status code and a stable JSON body::

    {"detail": "...", "code": "duplicate_claim", "context": {...}, "requestId": "..."}

``requestId`` lets a caller quote one id that ties the failure to the server logs. Unexpected
exceptions are logged with a stack trace and answered with an opaque 500 — internal messages are
never leaked to clients.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.core.context import current_request_id
from app.core.logging import get_logger
from app.domain.errors import (
    ConflictError,
    DomainError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)

logger = get_logger(__name__)

# Spelled numerically: Starlette renamed HTTP_422_UNPROCESSABLE_ENTITY to
# HTTP_422_UNPROCESSABLE_CONTENT, and the literal works across both versions without a warning.
HTTP_422_UNPROCESSABLE = 422

# Most specific first — the first matching class wins.
STATUS_BY_ERROR: tuple[tuple[type[DomainError], int], ...] = (
    (NotFoundError, status.HTTP_404_NOT_FOUND),
    (ForbiddenError, status.HTTP_403_FORBIDDEN),
    (ConflictError, status.HTTP_409_CONFLICT),
    (ValidationError, HTTP_422_UNPROCESSABLE),
)


def status_for(error: DomainError) -> int:
    for error_type, http_status in STATUS_BY_ERROR:
        if isinstance(error, error_type):
            return http_status
    return status.HTTP_400_BAD_REQUEST


def _json(status_code: int, payload: dict) -> JSONResponse:
    request_id = current_request_id()
    if request_id:
        payload = {**payload, "requestId": request_id}
    return JSONResponse(status_code=status_code, content=payload)


async def domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
    http_status = status_for(exc)
    logger.warning(
        "domain.error",
        extra={
            "errorCode": exc.code,
            "errorType": type(exc).__name__,
            "status": http_status,
            "errorContext": exc.details or None,
        },
    )
    return _json(http_status, exc.to_payload())


async def stale_data_handler(_: Request, exc: StaleDataError) -> JSONResponse:
    """SQLAlchemy raises this when an optimistic-lock (``version_id_col``) check fails."""
    logger.warning("db.concurrent_update", extra={"errorType": type(exc).__name__})
    return _json(
        status.HTTP_409_CONFLICT,
        {
            "detail": "The record was modified by another request. Reload and retry.",
            "code": "concurrent_update",
        },
    )


async def integrity_error_handler(_: Request, exc: IntegrityError) -> JSONResponse:
    """A database constraint rejected the write (unique, FK, check, or a guard trigger).

    The constraint name is diagnostic detail, so it is logged but not returned.
    """
    logger.warning(
        "db.integrity_error",
        extra={"dbError": str(getattr(exc, "orig", exc))[:500]},
    )
    return _json(
        status.HTTP_409_CONFLICT,
        {
            "detail": "The request violates a data-integrity constraint.",
            "code": "integrity_violation",
        },
    )


async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled.error", extra={"errorType": type(exc).__name__})
    return _json(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        {"detail": "Internal server error.", "code": "internal_error"},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Wire every handler above onto ``app``. Call once during startup."""
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(StaleDataError, stale_data_handler)
    app.add_exception_handler(IntegrityError, integrity_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
