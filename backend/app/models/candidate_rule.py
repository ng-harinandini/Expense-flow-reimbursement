"""Candidate policy rules extracted by Gemini, pending human approval.

Each row is a draft that a Finance or Admin user must explicitly approve before it is
published to the durable ``policy_rules`` table. The candidate schema mirrors
``PolicyRule`` so approval is a straight copy of fields — no translation required.

Lifecycle: PENDING → APPROVED (sets ``published_rule_id``) | DISMISSED.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import UUIDPrimaryKeyMixin

MONEY = Numeric(14, 2)

EXTRACTED_BY_GEMINI = "GEMINI-2.5-FLASH"
EXTRACTED_BY_MANUAL = "MANUAL_ENTRY"


class CandidatePolicyRule(UUIDPrimaryKeyMixin, Base):
    """One AI-extracted rule candidate awaiting human review."""

    __tablename__ = "candidate_policy_rules"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'DISMISSED')",
            name="ck_candidate_rules_status",
        ),
        CheckConstraint(
            "expense_limit IS NULL OR expense_limit >= 0",
            name="ck_candidate_rules_limit_positive",
        ),
        Index("ix_candidate_rules_document", "document_id"),
        Index("ix_candidate_rules_run", "extraction_run_id"),
        Index("ix_candidate_rules_status", "status"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.now(),
    )

    # --- source provenance ---
    document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_documents.id", ondelete="SET NULL",
                   name="fk_candidate_rules_document"),
        nullable=True,
    )
    source_page_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_chunk_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_chunks.id", ondelete="SET NULL",
                   name="fk_candidate_rules_chunk"),
        nullable=True,
    )
    extracted_by: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=EXTRACTED_BY_GEMINI
    )
    # Groups all candidates produced in one extract-rules call.
    extraction_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )

    # --- rule payload (mirrors PolicyRule columns) ---
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="USD")
    grade_tier: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    expense_limit: Mapped[Optional[Decimal]] = mapped_column(MONEY, nullable=True)
    limit_expression: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    auto_approve_limit: Mapped[Optional[Decimal]] = mapped_column(MONEY, nullable=True)
    receipt_required_above: Mapped[Optional[Decimal]] = mapped_column(MONEY, nullable=True)
    requires_pre_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default="100"
    )
    conditions: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    actions: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    special_rules: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    # --- candidate workflow ---
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="PENDING", server_default="PENDING"
    )
    reviewed_by_sub: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Set to the published PolicyRule.id when approved.
    published_rule_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("policy_rules.id", ondelete="SET NULL",
                   name="fk_candidate_rules_published"),
        nullable=True,
    )

    @property
    def is_pending(self) -> bool:
        return self.status == "PENDING"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CandidatePolicyRule {self.category!r} {self.status}>"
