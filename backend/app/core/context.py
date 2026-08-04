"""Per-request correlation context.

Every inbound request gets a ``request_id`` (unique to this hop) and a ``correlation_id``
(carried across hops via the ``X-Correlation-Id`` header, so a client or upstream service can
tie many requests together). Both are stored in ``contextvars`` so that any layer — repository,
service, log formatter — can read them without threading arguments through every signature.

Audit records persist both ids (`AuditLog.request_id` / `AuditLog.correlation_id`), which is what
makes a database row traceable back to the exact HTTP call that produced it.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Optional

REQUEST_ID_HEADER = "X-Request-Id"
CORRELATION_ID_HEADER = "X-Correlation-Id"


@dataclass(frozen=True)
class RequestContext:
    """Immutable correlation data for the current request."""

    request_id: str
    correlation_id: str
    method: Optional[str] = None
    path: Optional[str] = None
    client_ip: Optional[str] = None
    user_agent: Optional[str] = None
    extra: dict[str, str] = field(default_factory=dict)

    def as_log_fields(self) -> dict[str, str]:
        fields = {"requestId": self.request_id, "correlationId": self.correlation_id}
        if self.method:
            fields["method"] = self.method
        if self.path:
            fields["path"] = self.path
        return fields


_current: ContextVar[Optional[RequestContext]] = ContextVar("request_context", default=None)


def new_id() -> str:
    """Generate an id for a request/correlation pair."""
    return str(uuid.uuid4())


def build_context(
    *,
    request_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    method: Optional[str] = None,
    path: Optional[str] = None,
    client_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> RequestContext:
    """Create a context, generating any id the caller did not supply."""
    rid = (request_id or "").strip() or new_id()
    return RequestContext(
        request_id=rid,
        correlation_id=(correlation_id or "").strip() or rid,
        method=method,
        path=path,
        client_ip=client_ip,
        user_agent=user_agent,
    )


def set_context(context: RequestContext) -> Token:
    """Bind ``context`` to the current task. Returns a token for :func:`reset_context`."""
    return _current.set(context)


def reset_context(token: Token) -> None:
    _current.reset(token)


def get_context() -> Optional[RequestContext]:
    """The active context, or ``None`` outside a request (CLI, worker, unit test)."""
    return _current.get()


def current_request_id() -> Optional[str]:
    ctx = _current.get()
    return ctx.request_id if ctx else None


def current_correlation_id() -> Optional[str]:
    ctx = _current.get()
    return ctx.correlation_id if ctx else None


def current_client_ip() -> Optional[str]:
    ctx = _current.get()
    return ctx.client_ip if ctx else None
