"""The claim aggregate: ``Claim`` plus its owned children.

``Claim`` replaces the ``claims_store`` Python list. Everything the old dictionary carried is now
either a typed column, a foreign key, or a child row:

    old dict key        now
    ----------------    ------------------------------------------------------------
    workflowHistory     ``claim_status_history`` rows (+ ``approval_steps``)
    comments            ``comments`` rows
    fraudScreening      ``fraud_results`` rows (latest wins)
    policyValidation    ``claims.policy_validation`` JSONB snapshot
    employeeName/Grade  FK to ``employees`` + a grade/department snapshot on the claim

**Snapshot columns are intentional.** ``employee_grade`` and ``department_id`` record the values
*as of submission*, because a later promotion or transfer must not retroactively change the policy
context a historical claim was judged under. The employee's display name is read through the FK
instead of copied, so a name correction propagates and PII is not duplicated.

``Claim`` carries a ``version`` column: two reviewers acting on the same claim cannot silently
overwrite each other (the loser gets ``409 concurrent_update``).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

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
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import (
    CreatedAtMixin,
    OptimisticLockMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from app.models.enums import (
    AttachmentKind,
    ClaimStatus,
    EmployeeGrade,
    attachment_kind_enum,
    claim_status_enum,
    employee_grade_enum,
)

# Money: 14 digits with 2 decimals covers any realistic expense in any currency without the
# rounding error a float would introduce.
MONEY = Numeric(14, 2)


class Claim(UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin, Base):
    __tablename__ = "claims"
    __table_args__ = (
        UniqueConstraint("claim_number", name="uq_claims_claim_number"),
        # One claim per receipt — the database half of the "receipt already claimed" rule.
        UniqueConstraint("receipt_id", name="uq_claims_receipt_id"),
        CheckConstraint("amount > 0", name="ck_claims_amount_positive"),
        CheckConstraint("amount_usd > 0", name="ck_claims_amount_usd_positive"),
        CheckConstraint("char_length(currency) = 3", name="ck_claims_currency_iso4217"),
        CheckConstraint(
            "fx_rate IS NULL OR fx_rate > 0", name="ck_claims_fx_rate_positive"
        ),
        # A submitted claim must know when it was submitted, and vice versa.
        CheckConstraint(
            "(status = 'Draft' AND submitted_at IS NULL) "
            "OR (status <> 'Draft' AND submitted_at IS NOT NULL)",
            name="ck_claims_submitted_at_matches_status",
        ),
        Index("ix_claims_employee_id_status", "employee_id", "status"),
        Index("ix_claims_status_created_at", "status", "created_at"),
        Index("ix_claims_expense_date", "expense_date"),
        Index("ix_claims_category", "category"),
        Index("ix_claims_assigned_reviewer_id", "assigned_reviewer_id"),
        Index("ix_claims_department_id", "department_id"),
        # Supports duplicate detection (employee + vendor + date + amount).
        Index(
            "ix_claims_duplicate_probe",
            "employee_id",
            "expense_date",
            "amount_usd",
        ),
    )

    claim_number: Mapped[str] = mapped_column(String(32), nullable=False)

    # --- ownership + snapshot of the organisational context at submission time ---
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="RESTRICT", name="fk_claims_employee_id"),
        nullable=False,
    )
    employee_grade: Mapped[EmployeeGrade] = mapped_column(employee_grade_enum, nullable=False)
    department_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("departments.id", ondelete="RESTRICT", name="fk_claims_department_id"),
        nullable=False,
    )

    # --- expense facts ---
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    sub_category: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="USD")
    amount_usd: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    fx_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8), nullable=True)

    merchant_vendor: Mapped[str] = mapped_column(String(200), nullable=False)
    purpose_description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    attendees: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    trip_log: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    has_pre_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    pre_approval_doc_ref: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # --- receipt linkage ---
    receipt_attached: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    receipt_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    receipt_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("receipts.id", ondelete="SET NULL", name="fk_claims_receipt_id"),
        nullable=True,
    )
    # Extraction snapshot as the claim was judged (a manually entered claim has no Receipt row).
    extracted_receipt: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # --- evaluation snapshot (the report the decision was based on) ---
    policy_validation: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # --- lifecycle ---
    status: Mapped[ClaimStatus] = mapped_column(
        claim_status_enum,
        nullable=False,
        default=ClaimStatus.DRAFT,
        server_default=ClaimStatus.DRAFT.value,
    )
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reimbursed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- review assignment + decision ---
    assigned_reviewer_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="SET NULL", name="fk_claims_assigned_reviewer_id"),
        nullable=True,
    )
    assigned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by_sub: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    decision_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reimbursement_reference: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    # --- provenance ---
    created_by_sub: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    updated_by_sub: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # --- relationships ---
    employee: Mapped["Employee"] = relationship(  # noqa: F821
        "Employee", foreign_keys=[employee_id], lazy="joined"
    )
    department: Mapped["Department"] = relationship("Department", lazy="joined")  # noqa: F821
    assigned_reviewer: Mapped[Optional["Employee"]] = relationship(  # noqa: F821
        "Employee", foreign_keys=[assigned_reviewer_id], lazy="selectin"
    )
    receipt: Mapped[Optional["Receipt"]] = relationship(  # noqa: F821
        "Receipt", lazy="selectin"
    )

    status_history: Mapped[List["ClaimStatusHistory"]] = relationship(
        back_populates="claim",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ClaimStatusHistory.sequence",
        lazy="selectin",
    )
    comments: Mapped[List["Comment"]] = relationship(
        back_populates="claim",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Comment.created_at",
        lazy="selectin",
    )
    attachments: Mapped[List["Attachment"]] = relationship(
        back_populates="claim",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Attachment.created_at",
        lazy="selectin",
    )
    fraud_results: Mapped[List["FraudResult"]] = relationship(  # noqa: F821
        "FraudResult",
        back_populates="claim",
        cascade="all, delete-orphan",
        passive_deletes=True,
        # Newest first, with deterministic tie-breaks so ``latest_fraud_result`` is unambiguous.
        order_by=(
            "desc(FraudResult.evaluated_at), desc(FraudResult.created_at), desc(FraudResult.id)"
        ),
        lazy="selectin",
    )
    workflows: Mapped[List["ApprovalWorkflow"]] = relationship(  # noqa: F821
        "ApprovalWorkflow",
        back_populates="claim",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ApprovalWorkflow.created_at",
        lazy="selectin",
    )

    # --- derived helpers (no I/O) ---

    @property
    def latest_fraud_result(self) -> Optional["FraudResult"]:  # noqa: F821
        """Most recent screening (the relationship is ordered newest-first)."""
        return self.fraud_results[0] if self.fraud_results else None

    @property
    def active_workflow(self) -> Optional["ApprovalWorkflow"]:  # noqa: F821
        for workflow in reversed(self.workflows):
            if workflow.status.value in ("PENDING", "IN_PROGRESS"):
                return workflow
        return self.workflows[-1] if self.workflows else None

    @property
    def next_history_sequence(self) -> int:
        return len(self.status_history) + 1

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Claim {self.claim_number} {self.status} {self.amount_usd}>"


class ClaimStatusHistory(UUIDPrimaryKeyMixin, Base):
    """Append-only record of one lifecycle transition.

    Written by ``ClaimRepository.transition_status`` on every state change — never by a route.
    ``sequence`` makes the ordering explicit and gap-free per claim, independent of clock skew.
    """

    __tablename__ = "claim_status_history"
    __table_args__ = (
        UniqueConstraint("claim_id", "sequence", name="uq_claim_status_history_claim_sequence"),
        CheckConstraint("sequence > 0", name="ck_claim_status_history_sequence_positive"),
        Index("ix_claim_status_history_claim_id", "claim_id"),
        Index("ix_claim_status_history_occurred_at", "occurred_at"),
    )

    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("claims.id", ondelete="CASCADE", name="fk_claim_status_history_claim_id"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)

    # NULL "from" marks claim creation.
    from_status: Mapped[Optional[ClaimStatus]] = mapped_column(claim_status_enum, nullable=True)
    to_status: Mapped[ClaimStatus] = mapped_column(claim_status_enum, nullable=False)

    actor_sub: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    actor_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    actor_role: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    step_name: Mapped[str] = mapped_column(String(120), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, server_default="SUCCESS")
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    correlation_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    claim: Mapped["Claim"] = relationship(back_populates="status_history")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ClaimStatusHistory {self.sequence} {self.from_status}->{self.to_status}>"


class Comment(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A human or system note attached to a claim."""

    __tablename__ = "comments"
    __table_args__ = (
        CheckConstraint("char_length(btrim(body)) > 0", name="ck_comments_body_not_blank"),
        Index("ix_comments_claim_id", "claim_id"),
    )

    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("claims.id", ondelete="CASCADE", name="fk_comments_claim_id"),
        nullable=False,
    )
    author_sub: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    author_name: Mapped[str] = mapped_column(String(200), nullable=False)
    author_role: Mapped[str] = mapped_column(String(32), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Internal notes are reviewer-only; the claim owner never sees them.
    is_internal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    claim: Mapped["Claim"] = relationship(back_populates="comments")


class Attachment(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A supporting document on a claim (beyond the primary receipt)."""

    __tablename__ = "attachments"
    __table_args__ = (
        CheckConstraint(
            "file_size_bytes IS NULL OR file_size_bytes >= 0",
            name="ck_attachments_file_size_non_negative",
        ),
        Index("ix_attachments_claim_id", "claim_id"),
        Index("ix_attachments_receipt_id", "receipt_id"),
    )

    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("claims.id", ondelete="CASCADE", name="fk_attachments_claim_id"),
        nullable=False,
    )
    # Set when the attachment is an extracted receipt document.
    receipt_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("receipts.id", ondelete="SET NULL", name="fk_attachments_receipt_id"),
        nullable=True,
    )

    kind: Mapped[AttachmentKind] = mapped_column(
        attachment_kind_enum, nullable=False, server_default=AttachmentKind.SUPPORTING.value
    )
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    s3_bucket: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    s3_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    s3_region: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    uploaded_by_sub: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    claim: Mapped["Claim"] = relationship(back_populates="attachments")
