"""``ExpenseItem`` — one line of a multi-item claim, with its own receipt and its own verdict.

Replaces the "one claim = one expense = one receipt" shape. Everything the policy and fraud
engines read now lives here; ``claims`` keeps only ownership, lifecycle, and roll-up totals.

**Two money columns, deliberately.** ``amount``/``currency`` are what the employee actually spent;
``amount_usd`` is what the engines judge. Every threshold in :mod:`app.services.policy_engine` and
:mod:`app.services.fraud_engine` is a USD constant, so a EUR item without a converted total would
be silently compared against the wrong ceiling.

**``category`` is denormalized on purpose.** ``category_id`` is the referential link;
``category`` is the string the policy engine literally compares, frozen as of submission so that
renaming a category cannot retroactively re-judge a filed item. See :mod:`app.models.category`.

**Three layers of receipt truth**, which must not be collapsed into one column:

    ocr_extracted_json       what the AI said            immutable once written
    employee_corrected_data  what the human changed      only the edited keys
    merchant_vendor, amount, the value the engines judge  correction > extraction > manual entry
    expense_date, attendees…

Storing only the OCR JSON would lose the correction (or destroy the extraction audit trail), leave
a manually-entered item with nowhere to put its vendor, and force the fraud engine's
vendor/date/amount predicates onto unindexed JSONB paths.

``version`` is present for the same reason it is on ``Claim``: two reviewers deciding two items of
the same claim concurrently must not silently overwrite each other.

Schema is owned by Alembic — this declaration is the source migration ``0008`` was authored from.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import (
    OptimisticLockMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from app.models.claim import MONEY
from app.models.enums import (
    ExpenseDuration,
    ExpenseItemStatus,
    FraudRiskLevel,
    TravelType,
    expense_duration_enum,
    expense_item_status_enum,
    fraud_risk_level_enum,
    travel_type_enum,
)


class ExpenseItem(UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin, Base):
    __tablename__ = "expense_items"
    __table_args__ = (
        # Stable, gap-free ordering within a claim, independent of insert timing.
        UniqueConstraint(
            "claim_id", "line_number", name="uq_expense_items_claim_line_number"
        ),
        CheckConstraint("line_number > 0", name="ck_expense_items_line_number_positive"),
        CheckConstraint("amount > 0", name="ck_expense_items_amount_positive"),
        CheckConstraint("amount_usd > 0", name="ck_expense_items_amount_usd_positive"),
        CheckConstraint(
            "char_length(currency) = 3", name="ck_expense_items_currency_iso4217"
        ),
        CheckConstraint(
            "fx_rate IS NULL OR fx_rate > 0", name="ck_expense_items_fx_rate_positive"
        ),
        CheckConstraint(
            "file_size_bytes IS NULL OR file_size_bytes >= 0",
            name="ck_expense_items_file_size_non_negative",
        ),
        CheckConstraint(
            "fraud_risk_score IS NULL OR (fraud_risk_score >= 0 AND fraud_risk_score <= 100)",
            name="ck_expense_items_fraud_risk_score_range",
        ),
        CheckConstraint(
            "ai_classification_confidence IS NULL OR "
            "(ai_classification_confidence >= 0 AND ai_classification_confidence <= 1)",
            name="ck_expense_items_ai_classification_confidence_range",
        ),
        Index("ix_expense_items_claim_id", "claim_id"),
        Index("ix_expense_items_status", "status"),
        Index("ix_expense_items_expense_date", "expense_date"),
        Index("ix_expense_items_category", "category"),
        Index("ix_expense_items_category_id", "category_id"),
        Index("ix_expense_items_category_review_required", "category_review_required"),
        # Exact-byte duplicate receipt detection — the index is what makes it usable.
        Index("ix_expense_items_file_hash", "file_hash"),
        # Duplicate probe: the employee side is reached by joining ``claims``, which is already
        # covered by ix_claims_employee_id_status.
        Index("ix_expense_items_duplicate_probe", "expense_date", "amount_usd"),
    )

    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("claims.id", ondelete="CASCADE", name="fk_expense_items_claim_id"),
        nullable=False,
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # --- classification ---
    # Nullable so an unrecognized category still saves; ``category`` below is the NOT NULL one.
    category_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "expense_categories.id",
            ondelete="RESTRICT",
            name="fk_expense_items_category_id",
        ),
        nullable=True,
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    sub_category: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    # --- local travel policy scope (app.services.policy_engine.evaluate_travel_policy) ---
    # Nullable: most categories have no travel dimension, and a NULL value simply never matches a
    # travel-scoped ClaimPolicyRule. See doc/travel-policy-rules.md.
    travel_type: Mapped[Optional[TravelType]] = mapped_column(travel_type_enum, nullable=True)
    duration: Mapped[Optional[ExpenseDuration]] = mapped_column(expense_duration_enum, nullable=True)

    # --- expense facts (the resolved values both engines read) ---
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    merchant_vendor: Mapped[str] = mapped_column(String(200), nullable=False)
    purpose_description: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=""
    )
    attendees: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    trip_log: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="USD")
    amount_usd: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    fx_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8), nullable=True)

    has_pre_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    pre_approval_doc_ref: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # --- receipt, inline (the receipts/receipt_fields/receipt_line_items tables are gone) ---
    # Asserted independently of ``file_url``: an item may legitimately claim a paper receipt.
    receipt_attached: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # The object URL is the only record of the S3 key — bucket and region come from settings.
    # ``app.services.receipt_extraction`` owns building and parsing it.
    file_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # SHA-256 hex is exactly 64 chars — same width as ClaimFingerprint.checksum_sha256.
    file_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Verbatim extractor output. Never rewritten — corrections go to the column below.
    ocr_extracted_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    # Only the keys the employee actually changed, for "did a human edit this?" review.
    employee_corrected_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    ocr_source: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # Per-field confidences ({"vendor": 98.2, "total": 99.1}), normalized to a 0-100 scale at the
    # boundary: Textract reports 0-100 per summary field, Gemini reports one 0-1 score.
    ocr_confidence: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # --- evaluation snapshots (the evidence the claim's roll-up was computed from) ---
    policy_validation: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    # Kept separate from policy_validation above (a different report shape, written by a different
    # engine — app.services.policy_engine.evaluate_travel_policy) rather than merged into it.
    travel_policy_validation: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    # One human-readable sentence explaining why this item landed on Policy_Hold/Fraud_Flag —
    # ClaimService._build_hold_reason combines whichever of the reports above actually drove the
    # routing decision. NULL for an item that was never held. Distinct from decision_notes/
    # rejection_reason below, which are human-authored at review time, not system-generated.
    hold_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fraud_risk_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    fraud_risk_level: Mapped[Optional[FraudRiskLevel]] = mapped_column(
        fraud_risk_level_enum, nullable=True
    )
    fraud_flags: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    is_fraud_flagged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # --- AI document classification (a verdict about the category, not a value the engines
    # resolve into — kept separate from the three-layers-of-receipt-truth columns above) ---
    # Null when classification never ran, or when the model's answer wasn't a recognized category
    # — an unrecognized category is never persisted here (``category`` above stays authoritative).
    ai_suggested_category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # Set whenever a response was parsed at all, including the invalid-category case — this is the
    # "classification actually ran" signal that mappers.item_to_dict gates on.
    ai_document_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    ai_classification_confidence: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(4, 3), nullable=True
    )
    category_mismatch: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Computed once by the classification service (mismatch, low confidence, invalid category, or
    # provider failure) — stored rather than derived live, so it stays stable even if the
    # confidence threshold setting changes later.
    category_review_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Category-specific extraction output. Not the same column as ``ocr_extracted_json`` above,
    # which is Textract-verbatim and never rewritten.
    ai_category_fields: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    ai_classification_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- lifecycle + human decision ---
    status: Mapped[ExpenseItemStatus] = mapped_column(
        expense_item_status_enum,
        nullable=False,
        default=ExpenseItemStatus.SUBMITTED,
        server_default=ExpenseItemStatus.SUBMITTED.value,
    )
    decided_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Cognito ``sub``, matching claims.decided_by_sub / approval_steps.decided_by_sub. There is no
    # ``users`` table in this schema; the employees FK below is the hard link.
    decided_by_sub: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    decided_by_employee_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "employees.id",
            ondelete="SET NULL",
            name="fk_expense_items_decided_by_employee_id",
        ),
        nullable=True,
    )
    decision_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- provenance ---
    created_by_sub: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    updated_by_sub: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # --- relationships ---
    claim: Mapped["Claim"] = relationship(  # noqa: F821
        back_populates="items"
    )
    category_ref: Mapped[Optional["ExpenseCategory"]] = relationship(  # noqa: F821
        "ExpenseCategory", lazy="selectin"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<ExpenseItem #{self.line_number} {self.category} "
            f"{self.amount_usd} {self.status}>"
        )
