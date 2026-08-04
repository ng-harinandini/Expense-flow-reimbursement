"""Approval workflow / step persistence.

Phase 1 instantiates one standard workflow per submitted claim and advances its steps alongside
the claim's lifecycle transitions. Routing stays deliberately simple here — the value now is that
the durable structure exists, so later phases can add amount-banded or multi-approver routing
without a schema change or a claim-table migration.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import select

from app.models.enums import ApprovalStepStatus, ApprovalWorkflowStatus
from app.models.workflow import STANDARD_WORKFLOW_CODE, ApprovalStep, ApprovalWorkflow
from app.repositories.base import BaseRepository

#: The Phase 1 standard route: manager review, then finance sign-off before payout.
STANDARD_STEPS: tuple[tuple[int, str, str], ...] = (
    (1, "Manager Review", "manager"),
    (2, "Finance Review", "finance"),
)


class ApprovalWorkflowRepository(BaseRepository[ApprovalWorkflow]):
    model = ApprovalWorkflow

    # --- workflow ------------------------------------------------------------

    def create_standard_workflow(
        self,
        claim_id: uuid.UUID,
        *,
        name: str = "Standard Expense Approval",
        definition_code: str = STANDARD_WORKFLOW_CODE,
        assignee_by_role: Optional[dict[str, uuid.UUID]] = None,
    ) -> ApprovalWorkflow:
        """Create a workflow with the standard steps, all ``PENDING``."""
        workflow = ApprovalWorkflow(
            claim_id=claim_id,
            definition_code=definition_code,
            name=name,
            status=ApprovalWorkflowStatus.PENDING,
            current_step_order=0,
        )
        self.session.add(workflow)
        self.session.flush()

        assignees = assignee_by_role or {}
        for step_order, step_name, required_role in STANDARD_STEPS:
            self.session.add(
                ApprovalStep(
                    workflow_id=workflow.id,
                    step_order=step_order,
                    name=step_name,
                    required_role=required_role,
                    assignee_id=assignees.get(required_role),
                    status=ApprovalStepStatus.PENDING,
                )
            )
        self.session.flush()
        return workflow

    def get_active_for_claim(self, claim_id: uuid.UUID) -> Optional[ApprovalWorkflow]:
        return self._one_or_none(
            select(ApprovalWorkflow)
            .where(
                ApprovalWorkflow.claim_id == claim_id,
                ApprovalWorkflow.status.in_(
                    [ApprovalWorkflowStatus.PENDING, ApprovalWorkflowStatus.IN_PROGRESS]
                ),
            )
            .order_by(ApprovalWorkflow.created_at.desc())
        )

    def list_for_claim(self, claim_id: uuid.UUID) -> Sequence[ApprovalWorkflow]:
        return self._all(
            select(ApprovalWorkflow)
            .where(ApprovalWorkflow.claim_id == claim_id)
            .order_by(ApprovalWorkflow.created_at)
        )

    def start_step(self, workflow: ApprovalWorkflow, step_order: int) -> Optional[ApprovalStep]:
        """Mark ``step_order`` in progress and make it the workflow's current step."""
        step = next((s for s in workflow.steps if s.step_order == step_order), None)
        if step is None:
            return None

        now = datetime.now(timezone.utc)
        if step.status == ApprovalStepStatus.PENDING:
            step.status = ApprovalStepStatus.IN_PROGRESS
            step.started_at = step.started_at or now

        workflow.current_step_order = step_order
        if workflow.status == ApprovalWorkflowStatus.PENDING:
            workflow.status = ApprovalWorkflowStatus.IN_PROGRESS
        self.session.flush()
        return step

    def complete_step(
        self,
        workflow: ApprovalWorkflow,
        *,
        step_order: Optional[int] = None,
        required_role: Optional[str] = None,
        status: ApprovalStepStatus = ApprovalStepStatus.APPROVED,
        decided_by_sub: Optional[str] = None,
        decision_notes: Optional[str] = None,
    ) -> Optional[ApprovalStep]:
        """Close one step.

        Identified by ``step_order``, or by the first open step for ``required_role``. Returns
        ``None`` when there is no matching open step, which the caller treats as "nothing to do"
        rather than an error — the claim's own status remains the authority.
        """
        step: Optional[ApprovalStep]
        if step_order is not None:
            step = next((s for s in workflow.steps if s.step_order == step_order), None)
        elif required_role is not None:
            step = workflow.step_for_role(required_role)
        else:
            step = workflow.current_step

        if step is None or not step.is_open:
            return None

        step.status = status
        step.decided_by_sub = decided_by_sub
        step.decision_notes = decision_notes
        step.completed_at = datetime.now(timezone.utc)
        step.started_at = step.started_at or step.completed_at
        self.session.flush()
        return step

    def advance_or_complete(self, workflow: ApprovalWorkflow) -> ApprovalWorkflow:
        """Move to the next open step, or finish the workflow when none remain."""
        next_step = next(
            (s for s in sorted(workflow.steps, key=lambda s: s.step_order) if s.is_open), None
        )
        if next_step is not None:
            self.start_step(workflow, next_step.step_order)
            return workflow
        return self.complete_workflow(workflow)

    def complete_workflow(
        self, workflow: ApprovalWorkflow, *, status: ApprovalWorkflowStatus = ApprovalWorkflowStatus.COMPLETED
    ) -> ApprovalWorkflow:
        workflow.status = status
        workflow.completed_at = datetime.now(timezone.utc)
        self.session.flush()
        return workflow

    def cancel_open_steps(
        self, workflow: ApprovalWorkflow, *, decided_by_sub: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> ApprovalWorkflow:
        """Skip every remaining step and close the workflow (terminal claim outcome)."""
        for step in workflow.steps:
            if step.is_open:
                step.status = ApprovalStepStatus.SKIPPED
                step.decided_by_sub = decided_by_sub
                step.decision_notes = reason
                step.completed_at = datetime.now(timezone.utc)
        return self.complete_workflow(workflow)

    def reject_open_step(
        self,
        workflow: ApprovalWorkflow,
        *,
        required_role: Optional[str] = None,
        decided_by_sub: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> ApprovalWorkflow:
        """Record a rejection on the relevant step and close the workflow."""
        self.complete_step(
            workflow,
            required_role=required_role,
            status=ApprovalStepStatus.REJECTED,
            decided_by_sub=decided_by_sub,
            decision_notes=reason,
        )
        return self.cancel_open_steps(workflow, decided_by_sub=decided_by_sub, reason=reason)
