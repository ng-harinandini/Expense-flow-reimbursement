"""Explicit transaction boundary for a request.

A route's handler *is* the unit of work: it calls one or more service methods, then commits once.
Passing this instead of the raw ``Session`` keeps SQLAlchemy out of the routes while still making
the commit point obvious in the code (and therefore reviewable) rather than an invisible side
effect of dependency teardown.

Committing inside the handler also means a constraint violation or a lost optimistic lock is
translated into the HTTP response by the exception handlers, instead of failing after the response
has already been sent to the client.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logging import get_logger

logger = get_logger(__name__)


class UnitOfWork:
    """Commit/rollback control over the request-scoped session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def commit(self) -> None:
        """Persist everything staged during this request."""
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def flush(self) -> None:
        """Send pending SQL without ending the transaction (constraint checks run now)."""
        self._session.flush()

    def __enter__(self) -> "UnitOfWork":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        """Commit on a clean exit, roll back on any exception."""
        if exc_type is None:
            self.commit()
        else:
            logger.warning("unit_of_work.rolled_back", extra={"errorType": exc_type.__name__})
            self.rollback()
