"""Candidate policy rules extracted by an AI provider, pending human approval.

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
# Provider-level fallback provenance. The extractor appends the configured model dynamically.
EXTRACTED_BY_BEDROCK = "BEDROCK"
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
        CheckConstraint(
            "overall_confidence IS NULL OR (overall_confidence >= 0 AND overall_confidence <= 1)",
            name="ck_candidate_rules_confidence_range",
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
    # Nullable: unlike the published PolicyRule, a draft may not yet have a confirmed currency
    # (e.g. the source document uses a generic symbol like "$" without naming one).
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    grade_tier: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    expense_limit: Mapped[Optional[Decimal]] = mapped_column(MONEY, nullable=True)
    # Unbounded: a reference/formula-based limit ("published quarterly by Finance in the Global
    # Travel Portal, matching the IRS standard mileage rate") must survive verbatim, not truncated.
    limit_expression: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
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

    # --- review aids (candidate-only; not part of the published PolicyRule shape) ---
    # Everything below exists so a Finance reviewer can approve or edit a candidate without
    # reopening the source PDF.
    #
    # Verbatim policy sentence(s) this rule was derived from — the citation the reviewer reads.
    source_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Section/heading the sentence sits under, e.g. "4.2 International Travel".
    source_section: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # Explicitly non-reimbursable items called out by this rule.
    exclusions: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    # Supporting documents the rule demands (receipt, trip log, attendee list, ...).
    required_documents: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    # {field_name: 0.0-1.0} for each populated field, so reviewers know where to look first.
    field_confidence: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    overall_confidence: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(4, 3), nullable=True
    )
    # Model-authored uncertainty plus extractor-authored completeness warnings.
    review_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

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

    @property
    def needs_review(self) -> bool:
        """True when a reviewer should read the citation before approving.

        Derived, never stored: the extractor writes ``review_notes`` whenever it was unsure,
        and low confidence or a missing citation both mean "do not rubber-stamp this one".
        """
        if self.review_notes:
            return True
        if not self.source_text:
            return True
        return self.overall_confidence is not None and self.overall_confidence < Decimal("0.7")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CandidatePolicyRule {self.category!r} {self.status}>"
