"""Claim lifecycle service — the only orchestrator of claim business logic.

Routes call this; it calls repositories. Nothing here builds HTTP responses and nothing in the
routes touches the ORM, which is the layering Phase 1 introduces:

    route  ->  service  ->  repository  ->  database

The submission pipeline reproduces the behaviour the in-memory implementation had (policy
evaluation, then fraud screening, then routing), with two differences that matter:

* every step is a *guarded* lifecycle transition, so the claim's own history is a complete and
  gap-free record of how it reached its current state, and
* the whole pipeline is one transaction: a claim, its history, its fraud result, its workflow, and
  its audit records either all commit or none do. A partially processed claim cannot exist.

Routing decisions (which state a processed claim lands in) match the previous thresholds exactly —
this phase moves state into the database, it does not change policy.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional, Sequence

from app.core.logging import get_logger
from app.domain import validators
from app.domain.actor import Actor
from app.domain.claim_state_machine import SYSTEM_ROLE
from app.domain.errors import ForbiddenError, NotFoundError, ValidationError
from app.models.claim import Claim
from app.models.enums import (
    ApprovalStepStatus,
    AttachmentKind,
    AuditAction,
    AuditEntity,
    ClaimStatus,
    FraudRiskLevel,
)
from app.models.organization import Employee
from app.repositories.claim_repository import ClaimQuery, ClaimRepository
from app.repositories.fraud_repository import FraudResultRepository
from app.repositories.receipt_repository import ReceiptRepository
from app.repositories.workflow_repository import ApprovalWorkflowRepository
from app.services.audit_service import AuditService
from app.services.employee_service import EmployeeService
from app.services.fraud_engine import screen_for_anomalies
from app.services.mappers import claim_to_engine_input, claims_to_engine_corpus
from app.services.policy_engine import evaluate_expense_policy
from app.services.policy_rule_service import PolicyRuleService

logger = get_logger(__name__)

#: Risk score at or above which a claim is routed to fraud investigation instead of review.
FRAUD_ROUTING_THRESHOLD = 40

#: Categories whose claims require an attendee listing.
ATTENDEE_REQUIRED_CATEGORIES = frozenset({"Client Entertainment"})

#: The four actions the existing API exposes on ``POST /claims/{id}/action``.
CLAIM_ACTIONS = frozenset({"APPROVE", "REJECT", "DISBURSE", "FLAG_FRAUD"})

#: Only these roles may move money.
DISBURSE_ROLES = frozenset({"finance", "admin"})

#: How many peer claims to compare against during fraud screening.
FRAUD_CORPUS_LIMIT = 200


class ClaimService:
    """Business operations on claims. One instance per request (see ``app.core.deps``)."""

    def __init__(
        self,
        *,
        claim_repository: ClaimRepository,
        fraud_repository: FraudResultRepository,
        workflow_repository: ApprovalWorkflowRepository,
        receipt_repository: ReceiptRepository,
        employee_service: EmployeeService,
        policy_rule_service: PolicyRuleService,
        audit_service: AuditService,
    ) -> None:
        self._claims = claim_repository
        self._fraud = fraud_repository
        self._workflows = workflow_repository
        self._receipts = receipt_repository
        self._employees = employee_service
        self._policies = policy_rule_service
        self._audit = audit_service

    # --- reads ---------------------------------------------------------------

    def list_claims(
        self,
        *,
        actor: Actor,
        category: Optional[str] = None,
        status: Optional[str] = None,
        employee_code: Optional[str] = None,
        risk_level: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> Sequence[Claim]:
        """Claims visible to ``actor``, filtered.

        An employee is always scoped to their own claims regardless of the filters they send; a
        non-existent ``employee_code`` filter yields an empty list rather than an error, so a
        dashboard filter cannot 404.
        """
        query_employee_id: Optional[uuid.UUID] = None

        if actor.role == "employee":
            own = self._employees.find_actor_employee(actor)
            if own is None:
                return []
            query_employee_id = own.id
        elif employee_code:
            requested = self._employees.find_by_code(employee_code)
            if requested is None:
                return []
            query_employee_id = requested.id

        return self._claims.search(
            ClaimQuery(
                employee_id=query_employee_id,
                category=category or None,
                status=self._coerce_status(status),
                risk_level=self._coerce_risk_level(risk_level),
                limit=limit,
                offset=offset,
            )
        )

    def get_claim_for_actor(self, identifier: str, *, actor: Actor) -> Claim:
        """One claim, enforcing ownership for employees.

        A non-owner employee gets 404 rather than 403 so the endpoint does not confirm that a
        claim id exists.
        """
        claim = self._claims.get_by_id_or_number(identifier)
        if claim is None:
            raise NotFoundError("Claim", identifier)

        if actor.role == "employee":
            own = self._employees.find_actor_employee(actor)
            if own is None or claim.employee_id != own.id:
                raise NotFoundError("Claim", identifier)
        return claim

    def list_history(self, claim: Claim):
        return self._claims.list_history(claim.id)

    # --- submission ----------------------------------------------------------

    def submit_claim(self, payload: dict[str, Any], *, actor: Actor) -> Claim:
        """Create and fully process a claim: Draft → Submitted → Processing → routed.

        Mirrors ``POST /claims``, which has always been a create-and-submit call. All of it runs in
        the caller's single transaction.
        """
        employee = self._employees.resolve_actor_employee(actor)
        actor = actor.with_name(employee.full_name)

        claim = self._build_draft(payload, employee=employee, actor=actor)

        # Draft → Submitted (the employee's own act)
        self._claims.transition_status(
            claim,
            ClaimStatus.SUBMITTED,
            actor_role="employee" if actor.role == "employee" else actor.role,
            actor_sub=actor.sub,
            actor_name=actor.name,
            step_name="Submit Claim",
            action=f"Submitted expense claim {claim.claim_number}",
        )

        # Submitted → Processing → routed (machine-driven)
        self._process(claim, actor=actor)

        self._audit.record(
            actor=actor,
            action=AuditAction.SUBMIT_CLAIM,
            entity_type=AuditEntity.CLAIM,
            entity_id=claim.claim_number,
            details=(
                f"Submitted claim for {claim.currency} {claim.amount_usd:.2f} "
                f"({claim.category}) -> Route: {claim.status.value}"
            ),
            after={
                "claimId": str(claim.id),
                "status": claim.status.value,
                "amountUsd": str(claim.amount_usd),
                "category": claim.category,
            },
        )
        # History, comments, fraud result and workflow were inserted during this transaction;
        # expire so the serialized aggregate reflects all of them.
        return self._claims.refresh(claim)

    def _build_draft(
        self, payload: dict[str, Any], *, employee: Employee, actor: Actor
    ) -> Claim:
        """Validate the request and insert the ``Draft`` row it describes."""
        validators.require_fields(payload, ("amount", "merchantVendor"))

        amount = validators.validate_amount(payload.get("amount"), field="amount")
        amount_usd = (
            validators.validate_amount(payload["amountUSD"], field="amountUSD")
            if payload.get("amountUSD") is not None
            else amount
        )
        currency = validators.validate_currency(payload.get("currency"))
        expense_date = validators.validate_expense_date(
            self._coerce_date(payload.get("expenseDate")) or date.today()
        )
        category = (payload.get("category") or "Misc / Other").strip()
        attendees = validators.validate_attendees(
            payload.get("attendees"), required=category in ATTENDEE_REQUIRED_CATEGORIES
        )
        merchant_vendor = str(payload["merchantVendor"]).strip()

        # Hard-block an exact resubmission before writing anything.
        validators.require_no_duplicate(
            self._claims.find_duplicate_claims(
                employee_id=employee.id,
                merchant_vendor=merchant_vendor,
                expense_date=expense_date,
                amount_usd=amount_usd,
            )
        )

        receipt_id = self._resolve_receipt(payload.get("receiptId"), employee=employee)

        claim = Claim(
            claim_number=self._claims.next_claim_number(),
            employee_id=employee.id,
            # Snapshot: a later promotion or transfer must not re-judge a historical claim.
            employee_grade=employee.grade,
            department_id=employee.department_id,
            expense_date=expense_date,
            category=category,
            sub_category=(payload.get("subCategory") or "General Expense").strip(),
            amount=amount,
            currency=currency,
            amount_usd=amount_usd,
            fx_rate=self._coerce_decimal(payload.get("fxRate")),
            merchant_vendor=merchant_vendor,
            purpose_description=(payload.get("purposeDescription") or "").strip(),
            attendees=attendees,
            trip_log=payload.get("tripLog"),
            has_pre_approval=bool(payload.get("hasPreApproval", False)),
            pre_approval_doc_ref=payload.get("preApprovalDocRef"),
            receipt_attached=bool(payload.get("receiptAttached", False)),
            receipt_url=payload.get("receiptUrl"),
            receipt_id=receipt_id,
            extracted_receipt=payload.get("extractedReceipt"),
        )

        self._claims.create_claim(
            claim, actor_sub=actor.sub, actor_name=actor.name, actor_role=actor.role
        )

        if receipt_id is not None:
            receipt = self._receipts.get(receipt_id)
            if receipt is not None:
                self._claims.add_attachment(
                    claim,
                    file_name=receipt.file_name,
                    kind=AttachmentKind.RECEIPT,
                    receipt_id=receipt.id,
                    content_type=receipt.content_type,
                    file_size_bytes=receipt.file_size_bytes,
                    s3_bucket=receipt.s3_bucket,
                    s3_key=receipt.s3_key,
                    s3_region=receipt.s3_region,
                    uploaded_by_sub=actor.sub,
                )

        self._audit.record(
            actor=actor,
            action=AuditAction.CLAIM_CREATE,
            entity_type=AuditEntity.CLAIM,
            entity_id=claim.claim_number,
            details=f"Created draft claim {claim.claim_number} for {employee.employee_code}.",
            after={"claimId": str(claim.id), "status": claim.status.value},
        )
        return claim

    def _resolve_receipt(
        self, receipt_id: Optional[str], *, employee: Employee
    ) -> Optional[uuid.UUID]:
        """Validate an attached receipt: it must exist, be the employee's, and be unclaimed."""
        if not receipt_id:
            return None

        try:
            parsed = uuid.UUID(str(receipt_id))
        except (ValueError, TypeError):
            raise ValidationError(
                "'receiptId' must be a valid UUID.", details={"field": "receiptId"}
            )

        receipt = validators.require_receipt(self._receipts.get(parsed), str(receipt_id))
        validators.require_receipt_ownership(receipt, employee)
        validators.require_receipt_unclaimed(parsed, self._receipts.claim_using(parsed))
        return parsed

    def _process(self, claim: Claim, *, actor: Actor) -> Claim:
        """Submitted → Processing → routed. Evaluation and screening happen here."""
        system = Actor.system()

        self._claims.transition_status(
            claim,
            ClaimStatus.PROCESSING,
            actor_role=SYSTEM_ROLE,
            actor_sub=actor.sub,
            actor_name=system.name,
            step_name="Processing",
            action="Started automated policy and fraud evaluation",
        )

        policy_report = self._evaluate_policy(claim)
        fraud_report = self._screen_fraud(claim)

        target = self._route(policy_report, fraud_report)
        self._claims.transition_status(
            claim,
            target,
            actor_role=SYSTEM_ROLE,
            actor_sub=actor.sub,
            actor_name=system.name,
            step_name="Routing Decision",
            action=f"Routed claim {claim.claim_number} to {target.value}",
            notes=policy_report.get("reasoningSummary"),
            outcome="SUCCESS" if target != ClaimStatus.FLAGGED_FRAUD else "WARNING",
        )

        self._start_workflow(claim, target)

        self._claims.add_comment(
            claim,
            body=f"Claim processed. Route status set to: {target.value.replace('_', ' ')}.",
            author_name=system.name,
            author_role="admin",
            author_sub=None,
        )
        return claim

    def _evaluate_policy(self, claim: Claim) -> dict[str, Any]:
        """Run the category policy engine against the effective ruleset and snapshot the report."""
        report = evaluate_expense_policy(
            claim_to_engine_input(claim),
            self._policies.rules_for_engine(claim.expense_date),
        )
        claim.policy_validation = report

        self._claims.record_step(
            claim,
            actor_name="ExpenseFlow Policy Engine",
            actor_role="admin",
            step_name="Policy Validation",
            action=(
                "Passed policy constraints"
                if report.get("overallPassed")
                else "Flagged policy violations"
            ),
            notes=report.get("reasoningSummary"),
            outcome="SUCCESS" if report.get("overallPassed") else "WARNING",
        )
        return report

    def _screen_fraud(self, claim: Claim) -> dict[str, Any]:
        """Screen against the employee's claim history and persist the verdict."""
        corpus = claims_to_engine_corpus(
            self._claims.list_for_employee(
                claim.employee_id, exclude_claim_id=claim.id, limit=FRAUD_CORPUS_LIMIT
            )
        )
        report = screen_for_anomalies(claim_to_engine_input(claim), corpus)

        self._fraud.record(
            claim_id=claim.id,
            risk_score=report["riskScore"],
            risk_level=report["riskLevel"],
            is_flagged=report["isFlagged"],
            recommended_action=report["recommendedAction"],
            rationale=report["rationale"],
            flags=report["flags"],
        )

        self._claims.record_step(
            claim,
            actor_name="ExpenseFlow Anomaly Engine",
            actor_role="admin",
            step_name="Fraud Screening",
            action=f"Risk Score: {report['riskScore']}/100 ({report['riskLevel']})",
            notes=report["rationale"],
            outcome="FAILED" if report["isFlagged"] else "SUCCESS",
        )
        return report

    @staticmethod
    def _route(policy_report: dict[str, Any], fraud_report: dict[str, Any]) -> ClaimStatus:
        """Destination for a processed claim. Thresholds unchanged from the previous behaviour."""
        if fraud_report["isFlagged"] and fraud_report["riskScore"] >= FRAUD_ROUTING_THRESHOLD:
            return ClaimStatus.FLAGGED_FRAUD
        if not policy_report.get("overallPassed"):
            return ClaimStatus.MANAGER_REVIEW
        if policy_report.get("requiresManualReview"):
            return ClaimStatus.MANAGER_REVIEW
        return ClaimStatus.AUTO_APPROVED

    def _start_workflow(self, claim: Claim, routed_to: ClaimStatus) -> None:
        """Materialise the approval workflow that matches the routing outcome."""
        manager = self._employees.manager_of(claim.employee)
        workflow = self._workflows.create_standard_workflow(
            claim.id,
            assignee_by_role={"manager": manager.id} if manager else None,
        )

        if routed_to == ClaimStatus.AUTO_APPROVED:
            # Policy cleared it without a human: manager step is skipped, finance still pays out.
            self._workflows.complete_step(
                workflow,
                required_role="manager",
                status=ApprovalStepStatus.SKIPPED,
                decision_notes="Auto-approved by policy engine.",
            )
            self._workflows.start_step(workflow, 2)
        elif routed_to == ClaimStatus.FINANCE_REVIEW:
            self._workflows.complete_step(
                workflow, required_role="manager", status=ApprovalStepStatus.SKIPPED
            )
            self._workflows.start_step(workflow, 2)
        else:
            self._workflows.start_step(workflow, 1)

        if manager is not None and routed_to in (
            ClaimStatus.MANAGER_REVIEW,
            ClaimStatus.FLAGGED_FRAUD,
        ):
            self._claims.assign_reviewer(
                claim,
                manager.id,
                actor_name=Actor.system().name,
                actor_role=SYSTEM_ROLE,
                notes=f"Auto-assigned to reporting manager {manager.full_name}.",
            )

    # --- decisions -----------------------------------------------------------

    def execute_action(
        self,
        identifier: str,
        *,
        action: str,
        actor: Actor,
        notes: Optional[str] = None,
        expected_version: Optional[int] = None,
    ) -> Claim:
        """Apply a reviewer decision (``APPROVE`` / ``REJECT`` / ``DISBURSE`` / ``FLAG_FRAUD``).

        The claim's current state decides the target: approving a claim in manager review escalates
        it to finance review, exactly as before. An action that is not legal from the current state
        raises ``409`` naming the states that are.
        """
        normalized = (action or "").strip().upper()
        if normalized not in CLAIM_ACTIONS:
            raise ValidationError(
                f"Unsupported action '{action}'.",
                details={"action": action, "supported": sorted(CLAIM_ACTIONS)},
            )

        claim = self._claims.get_by_id_or_number(identifier)
        if claim is None:
            raise NotFoundError("Claim", identifier)

        # Client-supplied version turns a lost race into 409 instead of a silent overwrite.
        if expected_version is not None and claim.version != expected_version:
            from app.domain.errors import ConcurrentUpdateError

            raise ConcurrentUpdateError("Claim", claim.claim_number)

        validators.require_not_terminal(claim)

        if normalized == "DISBURSE" and actor.role not in DISBURSE_ROLES:
            raise ForbiddenError(
                "Only finance or admin can disburse.",
                details={"permittedRoles": sorted(DISBURSE_ROLES)},
            )

        before_status = claim.status
        workflow = self._workflows.get_active_for_claim(claim.id)

        if normalized == "APPROVE":
            claim = self._approve(claim, actor=actor, notes=notes, workflow=workflow)
        elif normalized == "REJECT":
            claim = self._claims.reject_claim(
                claim, actor_role=actor.role, actor_sub=actor.sub,
                actor_name=actor.name, reason=notes,
            )
            if workflow is not None:
                self._workflows.reject_open_step(
                    workflow, required_role=actor.role, decided_by_sub=actor.sub, reason=notes
                )
        elif normalized == "DISBURSE":
            claim = self._claims.mark_reimbursed(
                claim, actor_role=actor.role, actor_sub=actor.sub,
                actor_name=actor.name, notes=notes,
            )
            if workflow is not None:
                self._workflows.complete_step(
                    workflow, required_role="finance",
                    status=ApprovalStepStatus.APPROVED, decided_by_sub=actor.sub,
                    decision_notes=notes,
                )
                self._workflows.cancel_open_steps(workflow, decided_by_sub=actor.sub)
        else:  # FLAG_FRAUD
            claim = self._claims.flag_fraud(
                claim, actor_role=actor.role, actor_sub=actor.sub,
                actor_name=actor.name, notes=notes,
            )
            self._record_manual_fraud_flag(claim, actor=actor, notes=notes)

        if notes:
            self._claims.add_comment(
                claim,
                body=notes,
                author_name=actor.name,
                author_role=actor.role,
                author_sub=actor.sub,
            )

        self._audit.record(
            actor=actor,
            action=AuditAction.APPROVAL_ACTION,
            entity_type=AuditEntity.CLAIM,
            entity_id=claim.claim_number,
            details=f"Action {normalized} executed. Status updated to {claim.status.value}",
            before={"status": before_status.value},
            after={"status": claim.status.value, "claimId": str(claim.id)},
        )
        logger.info(
            "claim.action_executed",
            extra={
                "claimNumber": claim.claim_number,
                "claimAction": normalized,
                "fromStatus": before_status.value,
                "toStatus": claim.status.value,
                "actorRole": actor.role,
            },
        )
        return self._claims.refresh(claim)

    def _approve(
        self, claim: Claim, *, actor: Actor, notes: Optional[str], workflow
    ) -> Claim:
        """Approval semantics: manager review escalates to finance; anything else approves."""
        if claim.status == ClaimStatus.MANAGER_REVIEW:
            if workflow is not None:
                self._workflows.complete_step(
                    workflow,
                    required_role="manager",
                    status=ApprovalStepStatus.APPROVED,
                    decided_by_sub=actor.sub,
                    decision_notes=notes,
                )
                self._workflows.advance_or_complete(workflow)
            return self._claims.transition_status(
                claim,
                ClaimStatus.FINANCE_REVIEW,
                actor_role=actor.role,
                actor_sub=actor.sub,
                actor_name=actor.name,
                step_name="Manager Approval",
                action=f"Manager approved claim {claim.claim_number}; escalated to finance review",
                notes=notes,
            )

        if workflow is not None:
            self._workflows.complete_step(
                workflow,
                required_role=actor.role if actor.role != "admin" else None,
                status=ApprovalStepStatus.APPROVED,
                decided_by_sub=actor.sub,
                decision_notes=notes,
            )
        return self._claims.approve_claim(
            claim, actor_role=actor.role, actor_sub=actor.sub,
            actor_name=actor.name, notes=notes,
        )

    def _record_manual_fraud_flag(
        self, claim: Claim, *, actor: Actor, notes: Optional[str]
    ) -> None:
        """Persist a human fraud flag as a screening result, so the verdict trail is complete."""
        previous = self._fraud.latest_for_claim(claim.id)
        flags = list(previous.flag_list) if previous else []
        flags.append(
            {
                "code": "MANUAL_FRAUD_FLAG",
                "title": "Manually Flagged for Investigation",
                "severity": "HIGH",
                "description": notes or f"Flagged by {actor.role} for fraud audit.",
                "evidence": f"Raised by {actor.name} ({actor.role}).",
            }
        )
        self._fraud.record(
            claim_id=claim.id,
            risk_score=max(previous.risk_score if previous else 0, FRAUD_ROUTING_THRESHOLD),
            risk_level=FraudRiskLevel.HIGH,
            is_flagged=True,
            recommended_action="FINANCE_AUDIT",
            rationale=notes or f"Manually flagged for fraud investigation by {actor.name}.",
            flags=flags,
        )
        self._audit.record(
            actor=actor,
            action=AuditAction.FRAUD_FLAG,
            entity_type=AuditEntity.CLAIM,
            entity_id=claim.claim_number,
            details=notes or "Claim manually flagged for fraud investigation.",
        )

    # --- reviewer assignment -------------------------------------------------

    def assign_reviewer(
        self, identifier: str, *, reviewer_code: Optional[str], actor: Actor,
        notes: Optional[str] = None,
    ) -> Claim:
        """Assign (or clear) the reviewer responsible for the claim's next decision."""
        claim = self._claims.get_by_id_or_number(identifier)
        if claim is None:
            raise NotFoundError("Claim", identifier)
        validators.require_not_terminal(claim)

        reviewer = self._employees.get_by_code(reviewer_code) if reviewer_code else None
        before = (
            claim.assigned_reviewer.employee_code if claim.assigned_reviewer else None
        )

        self._claims.assign_reviewer(
            claim,
            reviewer.id if reviewer else None,
            actor_sub=actor.sub,
            actor_name=actor.name,
            actor_role=actor.role,
            notes=notes,
        )
        self._audit.record(
            actor=actor,
            action=AuditAction.REVIEWER_ASSIGNED,
            entity_type=AuditEntity.CLAIM,
            entity_id=claim.claim_number,
            details=(
                f"Assigned reviewer {reviewer.employee_code}"
                if reviewer
                else "Cleared reviewer assignment"
            ),
            before={"assignedReviewerId": before},
            after={"assignedReviewerId": reviewer.employee_code if reviewer else None},
        )
        return self._claims.refresh(claim)

    # --- comments ------------------------------------------------------------

    def add_comment(
        self, identifier: str, *, body: str, actor: Actor, is_internal: bool = False
    ) -> Claim:
        """Attach a comment. Employees may only comment on their own claims."""
        text = (body or "").strip()
        if not text:
            raise ValidationError("Comment text is required.", details={"field": "text"})
        if is_internal and actor.role == "employee":
            raise ForbiddenError("Employees cannot create internal comments.")

        claim = self.get_claim_for_actor(identifier, actor=actor)

        self._claims.add_comment(
            claim,
            body=text,
            author_name=actor.name,
            author_role=actor.role,
            author_sub=actor.sub,
            is_internal=is_internal,
        )
        self._audit.record(
            actor=actor,
            action=AuditAction.COMMENT_ADDED,
            entity_type=AuditEntity.CLAIM,
            entity_id=claim.claim_number,
            details=f"Comment added to claim {claim.claim_number}.",
        )
        return self._claims.refresh(claim)

    # --- draft editing -------------------------------------------------------

    def update_draft(
        self, identifier: str, *, changes: dict[str, Any], actor: Actor
    ) -> Claim:
        """Edit an unsubmitted claim. Rejected once the claim has left ``Draft``."""
        claim = self.get_claim_for_actor(identifier, actor=actor)
        employee = self._employees.resolve_actor_employee(actor)
        if actor.role == "employee":
            validators.require_claim_ownership(claim, employee)
        validators.require_editable(claim)

        updates: dict[str, Any] = {}
        before: dict[str, Any] = {}

        if "amount" in changes:
            updates["amount"] = validators.validate_amount(changes["amount"], field="amount")
            before["amount"] = str(claim.amount)
        if "amountUSD" in changes:
            updates["amount_usd"] = validators.validate_amount(
                changes["amountUSD"], field="amountUSD"
            )
            before["amountUSD"] = str(claim.amount_usd)
        if "currency" in changes:
            updates["currency"] = validators.validate_currency(changes["currency"])
            before["currency"] = claim.currency
        if "expenseDate" in changes:
            updates["expense_date"] = validators.validate_expense_date(
                self._coerce_date(changes["expenseDate"])
            )
            before["expenseDate"] = claim.expense_date.isoformat()
        for wire_name, column in (
            ("category", "category"),
            ("subCategory", "sub_category"),
            ("merchantVendor", "merchant_vendor"),
            ("purposeDescription", "purpose_description"),
            ("attendees", "attendees"),
            ("receiptUrl", "receipt_url"),
            ("preApprovalDocRef", "pre_approval_doc_ref"),
        ):
            if wire_name in changes:
                before[wire_name] = getattr(claim, column)
                updates[column] = changes[wire_name]

        if not updates:
            raise ValidationError("No editable fields supplied.")

        self._claims.update_fields(claim, actor_sub=actor.sub, **updates)
        self._audit.record(
            actor=actor,
            action=AuditAction.CLAIM_UPDATE,
            entity_type=AuditEntity.CLAIM,
            entity_id=claim.claim_number,
            details=f"Updated draft claim {claim.claim_number}: {', '.join(sorted(before))}.",
            before={key: str(value) for key, value in before.items()},
            after={key: str(value) for key, value in updates.items()},
        )
        return self._claims.refresh(claim)

    # --- coercion helpers ----------------------------------------------------

    @staticmethod
    def _coerce_status(value: Optional[str]) -> Optional[ClaimStatus]:
        if not value:
            return None
        try:
            return ClaimStatus.coerce(value)
        except ValueError as exc:
            raise ValidationError(str(exc), details={"field": "status"})

    @staticmethod
    def _coerce_risk_level(value: Optional[str]) -> Optional[FraudRiskLevel]:
        if not value:
            return None
        try:
            return FraudRiskLevel.coerce(value)
        except ValueError as exc:
            raise ValidationError(str(exc), details={"field": "riskLevel"})

    @staticmethod
    def _coerce_date(value: Any) -> Optional[date]:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        try:
            return datetime.fromisoformat(str(value)[:10]).date()
        except ValueError:
            raise ValidationError(
                "Date must be an ISO-8601 value (YYYY-MM-DD).",
                details={"value": str(value)},
            )

    @staticmethod
    def _coerce_decimal(value: Any) -> Optional[Decimal]:
        if value is None or value == "":
            return None
        try:
            return Decimal(str(value))
        except (ArithmeticError, ValueError, TypeError):
            return None
