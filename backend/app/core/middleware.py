"""HTTP middleware.

``RequestContextMiddleware`` binds a :class:`~app.core.context.RequestContext` for the lifetime
of each request, echoes both ids back as response headers so a client can quote them in a support
ticket, and emits one structured access log line per request with its latency.
"""

from __future__ import annotations

import time
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.context import (
    CORRELATION_ID_HEADER,
    REQUEST_ID_HEADER,
    build_context,
    reset_context,
    set_context,
)
from app.core.logging import get_logger

logger = get_logger("app.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        context = build_context(
            request_id=request.headers.get(REQUEST_ID_HEADER),
            correlation_id=request.headers.get(CORRELATION_ID_HEADER),
            method=request.method,
            path=request.url.path,
            client_ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        token = set_context(context)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Log with correlation ids still bound, then let the exception handlers respond.
            logger.exception(
                "request.failed",
                extra={"durationMs": round((time.perf_counter() - started) * 1000, 2)},
            )
            reset_context(token)
            raise

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = context.request_id
        response.headers[CORRELATION_ID_HEADER] = context.correlation_id
        logger.info(
            "request.completed",
            extra={"status": response.status_code, "durationMs": duration_ms},
        )
        reset_context(token)
        return response


def _client_ip(request: Request) -> str | None:
    """Caller IP, preferring the left-most ``X-Forwarded-For`` entry behind a load balancer."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
