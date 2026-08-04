"""Shared column mixins for every ORM model.

Conventions enforced here (Phase 1 database design rules):
  * UUID primary keys, generated application-side so an aggregate can be wired up before flush.
  * ``created_at`` / ``updated_at`` are ``timestamptz`` with database-side defaults, so rows
    written by a migration or by raw SQL are stamped identically to rows written by the ORM.
  * Optimistic locking via an integer ``version`` column that SQLAlchemy increments and checks
    on every UPDATE (``version_id_col``) — applied to concurrently-mutated aggregates only.

Alembic remains the sole owner of the schema; these declarations are what migrations are
authored from (see ``alembic/README.md``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column


class UUIDPrimaryKeyMixin:
    """``id UUID PRIMARY KEY`` defaulted client-side to ``uuid4``."""

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class TimestampMixin:
    """``created_at`` / ``updated_at`` with server-side defaults."""

    @declared_attr
    def created_at(cls) -> Mapped[datetime]:  # noqa: N805
        return mapped_column(
            DateTime(timezone=True), server_default=func.now(), nullable=False
        )

    @declared_attr
    def updated_at(cls) -> Mapped[datetime]:  # noqa: N805
        return mapped_column(
            DateTime(timezone=True),
            server_default=func.now(),
            onupdate=func.now(),
            nullable=False,
        )


class CreatedAtMixin:
    """``created_at`` only — for append-only tables that are never updated."""

    @declared_attr
    def created_at(cls) -> Mapped[datetime]:  # noqa: N805
        return mapped_column(
            DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
        )


class OptimisticLockMixin:
    """Adds ``version`` and configures it as SQLAlchemy's version counter.

    Any UPDATE carries ``WHERE version = <loaded value>``; a lost race raises
    ``StaleDataError``, which ``app.core.errors`` turns into ``409 concurrent_update``.
    Subclasses that need their own ``__mapper_args__`` must merge this dict in.
    """

    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    @declared_attr.directive
    def __mapper_args__(cls) -> dict:  # noqa: N805
        return {"version_id_col": cls.__table__.c.version}
