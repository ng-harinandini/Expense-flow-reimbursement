"""The claim aggregate: ``Claim`` plus its owned children.

A claim is an **expense report header**, not an expense: it owns N :class:`~app.models.expense_item.ExpenseItem`
rows, each with its own receipt, category, and verdict. Everything the aggregate carries is a typed
column, a foreign key, or a child row:

    concept             now
    ----------------    ------------------------------------------------------------
    the expenses        ``expense_items`` rows (amount, vendor, receipt, per-item verdict)
    workflowHistory     ``claim_status_history`` rows (+ ``approval_steps``)
    comments            ``comments`` rows
    fraudScreening      ``fraud_results`` rows (latest wins)
    policyValidation    ``expense_items.policy_validation`` JSONB, one snapshot per item
    employeeName/Grade  FK to ``employees`` + a grade snapshot on the claim

**The claim's own status is derived, not decided.** Each item is judged independently and
``ClaimService`` rolls the item statuses up into ``Claim.status`` — fraud outranks a policy hold.
Transitions remain governed by :mod:`app.domain.claim_state_machine` and the database trigger.

**Snapshot columns are intentional.** ``employee_grade`` records the value *as of submission*,
because a later promotion must not retroactively change the policy context a historical claim was
judged under — the policy engine reads it for the Flights, Lodging, and Client Entertainment rules.
The employee's display name is read through the FK instead of copied, so a name correction
propagates and PII is not duplicated.

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
        CheckConstraint("char_length(currency) = 3", name="ck_claims_currency_iso4217"),
        # Zero, not positive: a header exists briefly before its first item is inserted.
        CheckConstraint("total_amount >= 0", name="ck_claims_total_amount_non_negative"),
        CheckConstraint(
            "total_amount_usd >= 0", name="ck_claims_total_amount_usd_non_negative"
        ),
        CheckConstraint("item_count >= 0", name="ck_claims_item_count_non_negative"),
        CheckConstraint(
            "to_date IS NULL OR from_date IS NULL OR to_date >= from_date",
            name="ck_claims_date_range_ordered",
        ),
        # A submitted claim must know when it was submitted, and vice versa.
        CheckConstraint(
            "(status = 'Draft' AND submitted_at IS NULL) "
            "OR (status <> 'Draft' AND submitted_at IS NOT NULL)",
            name="ck_claims_submitted_at_matches_status",
        ),
        Index("ix_claims_employee_id_status", "employee_id", "status"),
        Index("ix_claims_status_created_at", "status", "created_at"),
        Index("ix_claims_assigned_reviewer_id", "assigned_reviewer_id"),
    )

    # Unique human-facing reference ("EXP-2026-1000"), drawn from ``claim_number_seq``. Distinct
    # from ``title``, which is free text and may repeat across claims.
    claim_number: Mapped[str] = mapped_column(String(32), nullable=False)

    # --- report header ---
    title: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    purpose: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    from_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    to_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # --- ownership + snapshot of the organisational context at submission time ---
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="RESTRICT", name="fk_claims_employee_id"),
        nullable=False,
    )
    employee_grade: Mapped[EmployeeGrade] = mapped_column(employee_grade_enum, nullable=False)

    # --- roll-ups, maintained by the ``expense_items_recalculate_claim_totals`` trigger ---
    # The trigger is the sole writer: it recomputes all three from ``expense_items`` on every
    # insert, update, or delete. Never assign them in application code.
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="USD")
    total_amount: Mapped[Decimal] = mapped_column(
        MONEY, nullable=False, default=0, server_default="0"
    )
    # Items may be filed in mixed currencies; only the USD roll-up is meaningfully summable, and
    # it is what the fraud engine and duplicate probe compare against.
    total_amount_usd: Mapped[Decimal] = mapped_column(
        MONEY, nullable=False, default=0, server_default="0"
    )
    item_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

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
    withdrawn_at: Mapped[Optional[datetime]] = mapped_column(
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
    withdrawal_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # System-authored, not human-authored (unlike the three columns above): a roll-up of every
    # held item's own hold_reason, written by ClaimService._process. NULL once nothing is held.
    hold_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reimbursement_reference: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    # --- provenance ---
    created_by_sub: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    updated_by_sub: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # --- relationships ---
    employee: Mapped["Employee"] = relationship(  # noqa: F821
        "Employee", foreign_keys=[employee_id], lazy="joined"
    )
    assigned_reviewer: Mapped[Optional["Employee"]] = relationship(  # noqa: F821
        "Employee", foreign_keys=[assigned_reviewer_id], lazy="selectin"
    )
    items: Mapped[List["ExpenseItem"]] = relationship(  # noqa: F821
        "ExpenseItem",
        back_populates="claim",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ExpenseItem.line_number",
        lazy="selectin",
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
        Index("ix_attachments_expense_item_id", "expense_item_id"),
    )

    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("claims.id", ondelete="CASCADE", name="fk_attachments_claim_id"),
        nullable=False,
    )
    # Set when the document supports one specific item rather than the claim as a whole. The
    # item's own receipt lives inline on ``expense_items``; this is for everything else.
    expense_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "expense_items.id",
            ondelete="SET NULL",
            name="fk_attachments_expense_item_id",
        ),
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
