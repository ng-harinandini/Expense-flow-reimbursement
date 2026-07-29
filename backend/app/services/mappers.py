"""ORM entity → wire/engine dictionary mappers.

Two audiences, one place, so they cannot drift apart:

* **API responses** — the exact JSON shapes the frontend already consumes
  (``frontend/src/types.ts``: ``ExpenseClaim``, ``AuditLogEntry``, ``PolicyRuleDefinition``).
  Persisting the domain properly must not change a single field name, so this module is where the
  camelCase wire contract is honoured.
* **The existing rule engines** — ``policy_engine`` / ``fraud_engine`` were written against the old
  in-memory claim dictionaries. Rather than rewrite (and risk changing) their behaviour in a
  persistence phase, they keep receiving the same dictionary shape, now produced from real rows.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional, Sequence

from app.models.audit import AuditLog
from app.models.claim import Attachment, Claim, ClaimStatusHistory, Comment
from app.models.fraud import FraudResult
from app.models.policy import PolicyRule
from app.models.workflow import ApprovalStep, ApprovalWorkflow


# --- scalars -----------------------------------------------------------------

def _float(value: Optional[Decimal | float | int]) -> Optional[float]:
    """Money for JSON. Kept as ``Decimal`` in the database; only the edge sees a float."""
    return float(value) if value is not None else None


def _iso(value: Optional[datetime | date]) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        # The frontend expects a trailing Z for UTC instants.
        return value.astimezone().isoformat().replace("+00:00", "Z")
    return value.isoformat()


def _iso_date(value: Optional[date]) -> Optional[str]:
    return value.isoformat() if value else None


# --- claim children -----------------------------------------------------------

def history_to_workflow_step(entry: ClaimStatusHistory) -> dict[str, Any]:
    """One ``claim_status_history`` row as the legacy ``WorkflowStepLog``."""
    return {
        "timestamp": _iso(entry.occurred_at),
        "actorName": entry.actor_name or entry.actor_role or "system",
        "actorRole": entry.actor_role or "admin",
        "stepName": entry.step_name,
        "action": entry.action,
        "status": entry.outcome,
        "notes": entry.notes or "",
        "traceId": entry.request_id,
    }


def comment_to_dict(comment: Comment) -> dict[str, Any]:
    """One comment row as the legacy ``ClaimComment``."""
    return {
        "id": str(comment.id),
        "authorName": comment.author_name,
        "authorRole": comment.author_role,
        "timestamp": _iso(comment.created_at),
        "text": comment.body,
        "isInternal": comment.is_internal,
    }


def attachment_to_dict(attachment: Attachment) -> dict[str, Any]:
    return {
        "id": str(attachment.id),
        "kind": attachment.kind.value,
        "fileName": attachment.file_name,
        "contentType": attachment.content_type,
        "fileSizeBytes": attachment.file_size_bytes,
        "receiptId": str(attachment.receipt_id) if attachment.receipt_id else None,
        "s3": (
            {
                "bucket": attachment.s3_bucket,
                "key": attachment.s3_key,
                "region": attachment.s3_region,
            }
            if attachment.s3_bucket
            else None
        ),
        "createdAt": _iso(attachment.created_at),
    }


def fraud_result_to_dict(result: Optional[FraudResult]) -> Optional[dict[str, Any]]:
    """A screening row as the legacy ``FraudScreeningReport``."""
    if result is None:
        return None
    return {
        "riskScore": result.risk_score,
        "isFlagged": result.is_flagged,
        "riskLevel": result.risk_level.value,
        "flags": result.flag_list,
        "rationale": result.rationale,
        "recommendedAction": result.recommended_action,
        "engineVersion": result.engine_version,
        "evaluatedAt": _iso(result.evaluated_at),
    }


def approval_step_to_dict(step: ApprovalStep) -> dict[str, Any]:
    return {
        "id": str(step.id),
        "stepOrder": step.step_order,
        "name": step.name,
        "requiredRole": step.required_role,
        "status": step.status.value,
        "assigneeId": str(step.assignee_id) if step.assignee_id else None,
        "assigneeName": step.assignee.full_name if step.assignee else None,
        "decisionNotes": step.decision_notes,
        "startedAt": _iso(step.started_at),
        "completedAt": _iso(step.completed_at),
    }


def workflow_to_dict(workflow: Optional[ApprovalWorkflow]) -> Optional[dict[str, Any]]:
    if workflow is None:
        return None
    return {
        "id": str(workflow.id),
        "definitionCode": workflow.definition_code,
        "name": workflow.name,
        "status": workflow.status.value,
        "currentStepOrder": workflow.current_step_order,
        "completedAt": _iso(workflow.completed_at),
        "steps": [approval_step_to_dict(step) for step in workflow.steps],
    }


# --- claim -------------------------------------------------------------------

def claim_to_dict(claim: Claim, *, include_internal_comments: bool = True) -> dict[str, Any]:
    """Full claim in the legacy ``ExpenseClaim`` wire shape.

    Field-for-field compatible with what the in-memory API returned. New, additive keys
    (``approvalWorkflow``, ``attachments``, ``version``, …) are safe: the frontend reads by name.

    ``include_internal_comments=False`` hides reviewer-only notes — used when the claim owner is
    the audience.
    """
    employee = claim.employee
    fraud = claim.latest_fraud_result
    comments = [
        c for c in claim.comments if include_internal_comments or not c.is_internal
    ]

    return {
        "id": str(claim.id),
        "claimNumber": claim.claim_number,
        # Employees are addressed by their external code on the wire, as before.
        "employeeId": employee.employee_code if employee else None,
        "employeeName": employee.full_name if employee else None,
        "employeeGrade": claim.employee_grade.value,
        "department": claim.department.name if claim.department else None,
        "expenseDate": _iso_date(claim.expense_date),
        # Historically a date-only string; submission instant is exposed separately.
        "submissionDate": _iso_date(claim.submitted_at.date()) if claim.submitted_at else None,
        "submittedAt": _iso(claim.submitted_at),
        "category": claim.category,
        "subCategory": claim.sub_category,
        "amount": _float(claim.amount),
        "currency": claim.currency,
        "amountUSD": _float(claim.amount_usd),
        "fxRate": _float(claim.fx_rate),
        "merchantVendor": claim.merchant_vendor,
        "purposeDescription": claim.purpose_description or "",
        "attendees": claim.attendees,
        "tripLog": claim.trip_log,
        "hasPreApproval": claim.has_pre_approval,
        "preApprovalDocRef": claim.pre_approval_doc_ref,
        "receiptAttached": claim.receipt_attached,
        "receiptUrl": claim.receipt_url,
        "receiptId": str(claim.receipt_id) if claim.receipt_id else None,
        "extractedReceipt": claim.extracted_receipt,
        "policyValidation": claim.policy_validation,
        "fraudScreening": fraud_result_to_dict(fraud),
        "status": claim.status.value,
        "workflowHistory": [history_to_workflow_step(h) for h in claim.status_history],
        "comments": [comment_to_dict(c) for c in comments],
        "attachments": [attachment_to_dict(a) for a in claim.attachments],
        "approvalWorkflow": workflow_to_dict(claim.active_workflow),
        "assignedReviewerId": (
            claim.assigned_reviewer.employee_code if claim.assigned_reviewer else None
        ),
        "assignedReviewerName": (
            claim.assigned_reviewer.full_name if claim.assigned_reviewer else None
        ),
        "decisionNotes": claim.decision_notes,
        "rejectionReason": claim.rejection_reason,
        "reimbursementReference": claim.reimbursement_reference,
        "approvedAt": _iso(claim.approved_at),
        "rejectedAt": _iso(claim.rejected_at),
        "reimbursedAt": _iso(claim.reimbursed_at),
        "createdAt": _iso(claim.created_at),
        "updatedAt": _iso(claim.updated_at),
        # Clients echo this back on writes to get optimistic-concurrency protection.
        "version": claim.version,
    }


def claim_to_engine_input(claim: Claim) -> dict[str, Any]:
    """The subset ``policy_engine`` / ``fraud_engine`` read, in their historical key names."""
    employee = claim.employee
    return {
        "id": str(claim.id),
        "claimNumber": claim.claim_number,
        "employeeId": employee.employee_code if employee else None,
        "employeeGrade": claim.employee_grade.value,
        "department": claim.department.name if claim.department else None,
        "expenseDate": _iso_date(claim.expense_date),
        "submissionDate": _iso_date(
            claim.submitted_at.date() if claim.submitted_at else date.today()
        ),
        "category": claim.category,
        "subCategory": claim.sub_category,
        "amount": _float(claim.amount),
        "currency": claim.currency,
        "amountUSD": _float(claim.amount_usd),
        "merchantVendor": claim.merchant_vendor,
        "purposeDescription": claim.purpose_description or "",
        "attendees": claim.attendees,
        "hasPreApproval": claim.has_pre_approval,
        "receiptAttached": claim.receipt_attached,
        "extractedReceipt": claim.extracted_receipt,
        "status": claim.status.value,
    }


def claims_to_engine_corpus(claims: Sequence[Claim]) -> list[dict[str, Any]]:
    """Peer claims for anomaly comparison, in the engine's dictionary shape."""
    return [claim_to_engine_input(claim) for claim in claims]


# --- audit -------------------------------------------------------------------

def audit_to_dict(entry: AuditLog) -> dict[str, Any]:
    """One audit row as the legacy ``AuditLogEntry``."""
    return {
        "id": str(entry.id),
        "timestamp": _iso(entry.occurred_at),
        "actor": entry.actor_name,
        "role": entry.actor_role,
        "eventType": entry.action,
        "targetId": entry.entity_id,
        "details": entry.details,
        "ipAddress": entry.ip_address or "unknown",
        # Additive provenance fields — absent from the old shape, ignored by older clients.
        "entityType": entry.entity_type,
        "requestId": entry.request_id,
        "correlationId": entry.correlation_id,
        "before": entry.before,
        "after": entry.after,
    }


# --- policy rules ------------------------------------------------------------

def policy_rule_to_dict(rule: PolicyRule) -> dict[str, Any]:
    """One rule as the legacy ``PolicyRuleDefinition``.

    ``maxAmountUSD`` stays a number-or-string union: some rules are expressed in prose
    ("Per signed agreement"), which ``limit_expression`` carries.
    """
    return {
        "category": rule.category,
        "maxAmountUSD": rule.limit_expression or _float(rule.expense_limit),
        "autoApproveLimitUSD": _float(rule.auto_approve_limit),
        "receiptRequiredAboveUSD": _float(rule.receipt_required_above) or 0.0,
        "requiresPreApproval": rule.requires_pre_approval,
        "gradeTier": rule.grade_tier or "All Staff",
        "specialRules": rule.special_rules or [],
        # Additive: the durable identity/versioning the in-memory list could not express.
        "code": rule.code,
        "version": rule.version,
        "name": rule.name,
        "description": rule.description,
        "country": rule.country,
        "currency": rule.currency,
        "priority": rule.priority,
        "isActive": rule.is_active,
        "effectiveDate": _iso_date(rule.effective_date),
        "expirationDate": _iso_date(rule.expiration_date),
        "conditions": rule.conditions,
        "actions": rule.actions,
    }


def policy_rules_to_engine_input(rules: Sequence[PolicyRule]) -> list[dict[str, Any]]:
    """Rules in the shape ``policy_engine.evaluate_expense_policy`` expects."""
    return [policy_rule_to_dict(rule) for rule in rules]
