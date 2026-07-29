"""Approval workflow instances and their ordered steps.

A workflow is the *plan* for getting a claim decided (manager, then finance, …); the claim's
``status`` is the *result*. Keeping them separate is what will let Phase 3 introduce
multi-approver or amount-banded routing without touching the claim table: the steps change, the
lifecycle vocabulary does not.

Phase 1 materialises a single standard workflow per submitted claim and advances its steps in
lockstep with the claim transitions, so the durable record already exists when richer routing
arrives.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    ApprovalStepStatus,
    ApprovalWorkflowStatus,
    approval_step_status_enum,
    approval_workflow_status_enum,
)

# The workflow definition Phase 1 instantiates.
STANDARD_WORKFLOW_CODE = "STANDARD_TWO_STAGE"


class ApprovalWorkflow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "approval_workflows"
    __table_args__ = (
        CheckConstraint(
            "current_step_order >= 0", name="ck_approval_workflows_current_step_non_negative"
        ),
        Index("ix_approval_workflows_claim_id", "claim_id"),
        Index("ix_approval_workflows_status", "status"),
    )

    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("claims.id", ondelete="CASCADE", name="fk_approval_workflows_claim_id"),
        nullable=False,
    )

    definition_code: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=STANDARD_WORKFLOW_CODE
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[ApprovalWorkflowStatus] = mapped_column(
        approval_workflow_status_enum,
        nullable=False,
        default=ApprovalWorkflowStatus.PENDING,
        server_default=ApprovalWorkflowStatus.PENDING.value,
    )
    # 0 = not started; otherwise the ``step_order`` currently in progress.
    current_step_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    claim: Mapped["Claim"] = relationship("Claim", back_populates="workflows")  # noqa: F821
    steps: Mapped[List["ApprovalStep"]] = relationship(
        back_populates="workflow",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ApprovalStep.step_order",
        lazy="selectin",
    )

    @property
    def current_step(self) -> Optional["ApprovalStep"]:
        return next((s for s in self.steps if s.step_order == self.current_step_order), None)

    def step_for_role(self, role: str) -> Optional["ApprovalStep"]:
        """First not-yet-decided step whose required role matches ``role``."""
        return next(
            (
                s
                for s in self.steps
                if s.required_role == role
                and s.status in (ApprovalStepStatus.PENDING, ApprovalStepStatus.IN_PROGRESS)
            ),
            None,
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ApprovalWorkflow {self.definition_code} {self.status} step={self.current_step_order}>"


class ApprovalStep(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "approval_steps"
    __table_args__ = (
        UniqueConstraint("workflow_id", "step_order", name="uq_approval_steps_workflow_order"),
        CheckConstraint("step_order > 0", name="ck_approval_steps_order_positive"),
        Index("ix_approval_steps_workflow_id", "workflow_id"),
        Index("ix_approval_steps_assignee_id", "assignee_id"),
        Index("ix_approval_steps_status", "status"),
    )

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "approval_workflows.id", ondelete="CASCADE", name="fk_approval_steps_workflow_id"
        ),
        nullable=False,
    )

    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Application role that may decide this step (employee/manager/finance/admin/auditor).
    required_role: Mapped[str] = mapped_column(String(32), nullable=False)

    assignee_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="SET NULL", name="fk_approval_steps_assignee_id"),
        nullable=True,
    )

    status: Mapped[ApprovalStepStatus] = mapped_column(
        approval_step_status_enum,
        nullable=False,
        default=ApprovalStepStatus.PENDING,
        server_default=ApprovalStepStatus.PENDING.value,
    )
    decided_by_sub: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    decision_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    workflow: Mapped["ApprovalWorkflow"] = relationship(back_populates="steps")
    assignee: Mapped[Optional["Employee"]] = relationship("Employee", lazy="selectin")  # noqa: F821

    @property
    def is_open(self) -> bool:
        return self.status in (ApprovalStepStatus.PENDING, ApprovalStepStatus.IN_PROGRESS)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ApprovalStep {self.step_order} {self.name!r} {self.status}>"
