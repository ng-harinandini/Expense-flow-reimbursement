"""Database connection layer (PostgreSQL only).

Alembic is the SOLE owner of the schema — this module never calls
``Base.metadata.create_all()`` and never mutates schema at import or startup.

The engine is created lazily so the application can boot even when no ``DATABASE_URL`` is
configured yet (e.g. before the owner supplies credentials). Attempting to open a session
without a configured database raises a clear error.
"""

from __future__ import annotations

import logging
from typing import Generator, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Declarative base for all ORM models. Alembic reads ``Base.metadata``."""


_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None


def _build_engine() -> Engine:
    url = settings.database_url
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not configured. Set DATABASE_URL, or DB_USER/DB_HOST/DB_NAME "
            "(and DB_PASSWORD), before using the database."
        )
    # Never log the URL — it may contain the password.
    logger.info("Initializing PostgreSQL engine (pool_pre_ping=on, recycle=%ss).",
                settings.DB_POOL_RECYCLE_SECONDS)
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=settings.DB_POOL_RECYCLE_SECONDS,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        future=True,
    )


def get_engine() -> Engine:
    """Return the process-wide engine, building it on first use."""
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_session_factory() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(), autoflush=False, autocommit=False, expire_on_commit=False
        )
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yields the request-scoped session.

    Rolls back on an unhandled exception so a failed request can never leave a half-applied
    transaction behind, and always closes. It deliberately does **not** commit: committing is the
    route's explicit act (see :class:`app.core.unit_of_work.UnitOfWork`), so a commit failure is
    reported inside the response rather than after it has been sent.

    Every repository and service in one request receives this same session, which is what makes a
    business change and its audit record atomic.
    """
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# Backwards/ergonomic aliases some call sites may expect.
def SessionLocal() -> Session:  # noqa: N802 (kept capitalized to mirror common convention)
    """Create a new Session. Prefer ``get_db`` as a FastAPI dependency."""
    return get_session_factory()()
