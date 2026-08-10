"""Policy rule storage.

Replaces the hardcoded ``DEFAULT_POLICY_RULES`` list. A rule is **versioned and
effective-dated**: publishing a change inserts a new row with ``version + 1`` and deactivates the
previous one, so a historical claim can always be explained by the rule text that was live on its
submission date. Rows are never edited in place and never deleted.

``conditions`` / ``actions`` are JSONB so later phases can express richer rules
(``{"amountUsd": {"gt": 500}}`` → ``{"route": "FINANCE_REVIEW"}``) without another migration.
**Phase 1 stores them only** — no evaluation logic reads them yet; the existing category engine in
``app/services/policy_engine.py`` continues to consume the typed columns. See
``DECISIONS/ADR-004-phase1-durable-domain-model.md``.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

MONEY = Numeric(14, 2)


class PolicyRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "policy_rules"
    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_policy_rules_code_version"),
        CheckConstraint(
            "expiration_date IS NULL OR expiration_date >= effective_date",
            name="ck_policy_rules_effective_window",
        ),
        CheckConstraint(
            "expense_limit IS NULL OR expense_limit >= 0",
            name="ck_policy_rules_expense_limit_non_negative",
        ),
        CheckConstraint(
            "auto_approve_limit IS NULL OR auto_approve_limit >= 0",
            name="ck_policy_rules_auto_approve_limit_non_negative",
        ),
        CheckConstraint(
            "receipt_required_above IS NULL OR receipt_required_above >= 0",
            name="ck_policy_rules_receipt_threshold_non_negative",
        ),
        CheckConstraint("version > 0", name="ck_policy_rules_version_positive"),
        CheckConstraint("char_length(currency) = 3", name="ck_policy_rules_currency_iso4217"),
        # The hot read path: active rules for a category, highest priority first.
        Index("ix_policy_rules_category_active_priority", "category", "is_active", "priority"),
        Index("ix_policy_rules_effective_window", "effective_date", "expiration_date"),
        Index("ix_policy_rules_is_active", "is_active"),
        Index("ix_policy_rules_country", "country"),
    )

    # Stable identity of the rule across versions (e.g. "MEALS_DAILY_CAP").
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    category: Mapped[str] = mapped_column(String(64), nullable=False)
    # NULL country / grade_tier = applies everywhere / to all staff.
    country: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="USD")
    grade_tier: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    expense_limit: Mapped[Optional[Decimal]] = mapped_column(MONEY, nullable=True)
    auto_approve_limit: Mapped[Optional[Decimal]] = mapped_column(MONEY, nullable=True)
    receipt_required_above: Mapped[Optional[Decimal]] = mapped_column(MONEY, nullable=True)
    requires_pre_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Free-text limit for rules money cannot express ("Per signed agreement"). Unbounded: a
    # reference/formula-based limit must be stored verbatim, not truncated.
    limit_expression: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    expiration_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Lower number = evaluated first when several rules match.
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100, server_default="100")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    # Declarative payloads for the Phase 2+ rule engine. Stored, not yet evaluated.
    conditions: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    actions: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    # Human-readable clauses shown in the UI (legacy ``specialRules``).
    special_rules: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    created_by_sub: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # --- AI extraction provenance (set when a rule originated from an AI candidate) ---
    source_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_documents.id", ondelete="SET NULL",
                   name="fk_policy_rules_source_document"),
        nullable=True,
    )
    source_page_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_chunk_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_chunks.id", ondelete="SET NULL",
                   name="fk_policy_rules_source_chunk"),
        nullable=True,
    )
    # Provider/model provenance, e.g. "BEDROCK-<configured-model>" or "GEMINI-2.5-FLASH";
    # "MANUAL_ENTRY" for human-authored rules.
    extracted_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    def is_effective_on(self, on_date: date) -> bool:
        """Whether this rule governs an expense dated ``on_date``."""
        if not self.is_active:
            return False
        if on_date < self.effective_date:
            return False
        return self.expiration_date is None or on_date <= self.expiration_date

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<PolicyRule {self.code} v{self.version} {self.category!r} active={self.is_active}>"
