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
from app.models.expense_item import ExpenseItem
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
        "expenseItemId": (
            str(attachment.expense_item_id) if attachment.expense_item_id else None
        ),
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


def item_to_dict(item: ExpenseItem) -> dict[str, Any]:
    """One expense item on the wire.

    The per-expense key names are deliberately the ones ``claim_to_dict`` used to expose
    (``expenseDate``, ``amountUSD``, ``merchantVendor``, ``policyValidation``, …) so a client moves
    its existing expense component down a level rather than rewriting it.

    ``fraudScreening`` is assembled from the item's four ``fraud_*`` columns into the same shape
    :func:`fraud_result_to_dict` produces for the claim, so both levels read identically.
    """
    return {
        "id": str(item.id),
        "lineNumber": item.line_number,
        "categoryId": str(item.category_id) if item.category_id else None,
        "category": item.category,
        "subCategory": item.sub_category,
        "expenseDate": _iso_date(item.expense_date),
        "merchantVendor": item.merchant_vendor,
        "purposeDescription": item.purpose_description or "",
        "attendees": item.attendees,
        "tripLog": item.trip_log,
        "amount": _float(item.amount),
        "currency": item.currency,
        "amountUSD": _float(item.amount_usd),
        "fxRate": _float(item.fx_rate),
        "hasPreApproval": item.has_pre_approval,
        "preApprovalDocRef": item.pre_approval_doc_ref,
        # --- receipt (inline) ---
        "receiptAttached": item.receipt_attached,
        "fileUrl": item.file_url,
        "fileName": item.file_name,
        "mimeType": item.mime_type,
        "fileSizeBytes": item.file_size_bytes,
        "fileHash": item.file_hash,
        # ``extractedReceipt`` keeps its historical name; the correction layer is exposed beside it
        # so a reviewer can see what the AI said *and* what the employee changed.
        "extractedReceipt": item.ocr_extracted_json,
        "employeeCorrectedData": item.employee_corrected_data,
        "ocrSource": item.ocr_source,
        "ocrConfidence": item.ocr_confidence,
        # --- verdicts ---
        "policyValidation": item.policy_validation,
        "fraudScreening": (
            {
                "riskScore": item.fraud_risk_score,
                "isFlagged": item.is_fraud_flagged,
                "riskLevel": item.fraud_risk_level.value if item.fraud_risk_level else None,
                "flags": item.fraud_flags or [],
            }
            if item.fraud_risk_score is not None
            else None
        ),
        "status": item.status.value,
        "decidedAt": _iso(item.decided_at),
        "decisionNotes": item.decision_notes,
        "rejectionReason": item.rejection_reason,
        "createdAt": _iso(item.created_at),
        "updatedAt": _iso(item.updated_at),
        # Clients echo this back on item decisions for optimistic-concurrency protection.
        "version": item.version,
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
        # Historically a date-only string; submission instant is exposed separately.
        "submissionDate": _iso_date(claim.submitted_at.date()) if claim.submitted_at else None,
        "submittedAt": _iso(claim.submitted_at),
        # --- report header ---
        "title": claim.title,
        "purpose": claim.purpose,
        "fromDate": _iso_date(claim.from_date),
        "toDate": _iso_date(claim.to_date),
        "currency": claim.currency,
        # Trigger-maintained roll-ups over ``items``.
        "totalAmount": _float(claim.total_amount),
        "totalAmountUSD": _float(claim.total_amount_usd),
        "itemCount": claim.item_count,
        # The per-expense fields moved here from the claim; the key names are unchanged so a
        # client renders an item with the same component it used to render a claim.
        "items": [item_to_dict(i) for i in claim.items],
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
        "withdrawalReason": claim.withdrawal_reason,
        "reimbursementReference": claim.reimbursement_reference,
        "approvedAt": _iso(claim.approved_at),
        "rejectedAt": _iso(claim.rejected_at),
        "reimbursedAt": _iso(claim.reimbursed_at),
        "withdrawnAt": _iso(claim.withdrawn_at),
        "createdAt": _iso(claim.created_at),
        "updatedAt": _iso(claim.updated_at),
        # Clients echo this back on writes to get optimistic-concurrency protection.
        "version": claim.version,
    }


def item_to_engine_input(
    item: ExpenseItem, *, claim: Optional[Claim] = None
) -> dict[str, Any]:
    """The subset ``policy_engine`` / ``fraud_engine`` read, in their historical key names.

    The engines are unchanged and still judge one expense at a time — what changed is that the
    expense is now an item rather than the whole claim. Two details matter:

      * ``id`` is the **item's** id. The fraud engine excludes ``c["id"] == current["id"]`` from
        the comparison corpus; keyed on the claim, an item would exclude its own siblings and the
        same-claim split-transaction check could never fire.
      * ``status`` is the **item's** status, so the duplicate check's ``!= "Rejected"`` filter
        means "this line was rejected", not "the whole report was".

    ``employeeGrade`` and ``submissionDate`` still come from the parent claim: grade is a property
    of the submission, not of the line.
    """
    parent = claim if claim is not None else item.claim
    employee = parent.employee if parent else None
    return {
        "id": str(item.id),
        "claimId": str(item.claim_id),
        "claimNumber": parent.claim_number if parent else None,
        "lineNumber": item.line_number,
        "employeeId": employee.employee_code if employee else None,
        "employeeGrade": parent.employee_grade.value if parent else None,
        "expenseDate": _iso_date(item.expense_date),
        "submissionDate": _iso_date(
            parent.submitted_at.date() if parent and parent.submitted_at else date.today()
        ),
        "category": item.category,
        "subCategory": item.sub_category,
        "amount": _float(item.amount),
        "currency": item.currency,
        "amountUSD": _float(item.amount_usd),
        "merchantVendor": item.merchant_vendor,
        "purposeDescription": item.purpose_description or "",
        "attendees": item.attendees,
        "hasPreApproval": item.has_pre_approval,
        "receiptAttached": item.receipt_attached,
        "extractedReceipt": item.ocr_extracted_json,
        "fileHash": item.file_hash,
        "status": item.status.value,
    }


def items_to_engine_corpus(items: Sequence[ExpenseItem]) -> list[dict[str, Any]]:
    """Peer expense items for anomaly comparison, in the engine's dictionary shape."""
    return [item_to_engine_input(item) for item in items]


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
        # AI extraction provenance — present only for rules that originated from Gemini.
        "sourceDocumentId": str(rule.source_document_id) if rule.source_document_id else None,
        "sourcePageNumber": rule.source_page_number,
        "sourceChunkId": str(rule.source_chunk_id) if rule.source_chunk_id else None,
        "extractedBy": rule.extracted_by,
    }


def policy_rules_to_engine_input(rules: Sequence[PolicyRule]) -> list[dict[str, Any]]:
    """Rules in the shape ``policy_engine.evaluate_expense_policy`` expects."""
    return [policy_rule_to_dict(rule) for rule in rules]
