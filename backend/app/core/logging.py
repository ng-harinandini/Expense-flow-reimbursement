"""Structured (JSON) logging.

One JSON object per line so CloudWatch Logs Insights / any log pipeline can query fields
directly. Every record automatically carries the active ``requestId`` / ``correlationId`` from
:mod:`app.core.context`, so application code never has to pass them.

Usage::

    logger = get_logger(__name__)
    logger.info("claim.submitted", extra={"claimId": str(claim.id), "amountUsd": 42.0})

Anything under ``extra`` is merged into the JSON object. Never log secrets, full tokens, or
database URLs — see ``LOG_SENSITIVE_KEYS`` for the keys that are redacted defensively.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Iterable

from app.core.context import get_context

# Attributes present on every LogRecord — anything else is treated as caller-supplied `extra`.
_RESERVED = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
        "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
        "pathname", "process", "processName", "relativeCreated", "stack_info", "thread",
        "threadName", "taskName",
    }
)

# Defensive redaction: if a caller ever passes one of these, the value never reaches the log.
LOG_SENSITIVE_KEYS = frozenset(
    {
        "password", "newpassword", "secret", "token", "idtoken", "accesstoken",
        "refreshtoken", "authorization", "clientsecret", "databaseurl", "dburl",
        "apikey", "session",
    }
)

_REDACTED = "***redacted***"


def _scrub(key: str, value: Any) -> Any:
    return _REDACTED if key.lower().replace("_", "") in LOG_SENSITIVE_KEYS else value


class JsonFormatter(logging.Formatter):
    """Render a ``LogRecord`` as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        ctx = get_context()
        if ctx is not None:
            payload.update(ctx.as_log_fields())

        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = _scrub(key, value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO", *, quiet_loggers: Iterable[str] = ()) -> None:
    """Install the JSON formatter on the root logger. Idempotent."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn installs its own handlers; route them through ours instead of double-printing.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        target = logging.getLogger(name)
        target.handlers = []
        target.propagate = True

    for name in quiet_loggers:
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Module-level logger accessor (kept so call sites never import ``logging`` directly)."""
    return logging.getLogger(name)
