"""ORM models for AI governance: the model/provider/embedding registry and persisted feature-flag
overrides.

**One versioned, auditable table for all three of Task 11's "model/provider/embedding registry"
atomics.** ``RegistryEntry`` is discriminated by ``kind`` (an
:class:`~app.ai.core.enums.ProviderKind` value) rather than three near-identical tables, on the
same reasoning M9 applied to decision
memory — a table with no distinct schema need of its own is speculative and would only need
altering in lockstep with its siblings. ``app/ai/governance/{model_registry,provider_registry,
embedding_registry}.py`` are thin, kind-scoped convenience wrappers over one shared
:class:`~app.ai.repositories.governance_repository.RegistryEntryRepository`. Versioning mirrors
``app.models.policy.PolicyRule`` (see ``PromptTemplate``'s docstring for the one deliberate
rollback difference, which applies here too).

This is a distinct concern from :class:`~app.ai.registry.registry.ComponentRegistry` (M1): that is
an in-process, name-keyed *factory* for swappable Python provider classes, reset on every process
restart and never audited. ``RegistryEntry`` is the opposite shape — a durable, versioned row
recording which model/provider/embedding spec is *currently policy*, who set it, and what it
costs — surviving restarts and audited through the existing ``AuditService``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class RegistryEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One version of one governed model/provider/embedding configuration."""

    __tablename__ = "ai_registry_entries"
    __table_args__ = (
        UniqueConstraint(
            "kind", "name", "version", name="uq_ai_registry_entries_kind_name_version"
        ),
        CheckConstraint("version > 0", name="ck_ai_registry_entries_version_positive"),
        CheckConstraint(
            "cost_per_unit_usd IS NULL OR cost_per_unit_usd >= 0",
            name="ck_ai_registry_entries_cost_non_negative",
        ),
        Index("ix_ai_registry_entries_kind_active", "kind", "is_active"),
    )

    # ProviderKind value, stored VARCHAR: open vocabulary, matching every other open-ended AI enum
    # (see app/ai/core/enums.py's module docstring on the closed-vs-open PG-enum split).
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # Stable identity within a kind across versions (e.g. "bge_m3_onnx").
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    # Free-form config (model id, endpoint, dimensions, ...) — the shape genuinely varies by kind,
    # the same reasoning KnowledgeDocument.doc_metadata already applies to unfiltered AI metadata.
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    # Priced per one ``cost_unit`` (e.g. "token", "request", "second") — deliberately not a
    # "per-1M-tokens" style rate, so estimating a cost is a plain multiplication
    # (app.ai.governance.cost_registry.estimate_cost) with no unit-prefix parsing to get wrong.
    cost_per_unit_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 10), nullable=True)
    cost_unit: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    created_by_sub: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<RegistryEntry {self.kind}/{self.name} v{self.version} active={self.is_active}>"


class FlagOverride(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A durable override for one feature flag, surviving a process restart.

    ``app.ai.registry.flags.FeatureFlags.set_override`` is deliberately in-process only — its own
    docstring: "an emergency AI kill switch must take effect without a database write succeeding
    first." This table is the audited, durable layer *above* that fast path: a write here is also
    applied in-process immediately (see ``app.ai.governance.feature_flags``), and every override is
    reloaded into the in-process store at startup, so an override survives a restart without ever
    making the emergency path depend on the database being reachable.
    """

    __tablename__ = "ai_flag_overrides"
    __table_args__ = (
        UniqueConstraint("flag_name", name="uq_ai_flag_overrides_flag_name"),
    )

    flag_name: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_by_sub: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<FlagOverride {self.flag_name}={self.enabled}>"


__all__ = ["FlagOverride", "RegistryEntry"]
