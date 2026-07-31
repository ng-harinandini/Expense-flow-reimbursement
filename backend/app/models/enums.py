"""Domain enumerations and their PostgreSQL enum types.

**Wire-value compatibility is deliberate.** Every member's *value* is the exact string the
existing API already returns and the frontend already understands (``frontend/src/types.ts``),
so introducing the database enum does not change a single response payload. Member *names* use
the canonical Phase 1 lifecycle vocabulary. Where the two differ, the mapping is:

    canonical (spec)   member name         wire value / DB value
    ----------------   -----------------   ---------------------
    Processing         PROCESSING          "Processing_AI"
    Pending Review     MANAGER_REVIEW      "Manager_Review"
    Pending Review     FINANCE_REVIEW      "Finance_Review"
    Reimbursed         REIMBURSED          "Disbursed"

:meth:`ClaimStatus.coerce` additionally accepts the canonical spec spellings
(``"Processing"``, ``"Pending_Review"``, ``"Reimbursed"``) and case/separator variants as input
aliases, so future callers can speak either vocabulary. See
``DECISIONS/ADR-005-claim-lifecycle-state-machine.md``.
"""

from __future__ import annotations

import enum
from typing import Optional

from sqlalchemy import Enum as SAEnum


class _WireEnum(str, enum.Enum):
    """Base for enums whose values are stable API/database strings."""

    @classmethod
    def values(cls) -> list[str]:
        return [member.value for member in cls]

    @classmethod
    def coerce(cls, value: "str | _WireEnum | None"):
        """Resolve a member from its value, name, or a registered alias.

        Raises ``ValueError`` for anything unrecognized so callers can convert it into a
        domain ``ValidationError`` with a useful message.
        """
        if value is None:
            raise ValueError(f"{cls.__name__} value is required.")
        if isinstance(value, cls):
            return value

        raw = str(value).strip()
        for member in cls:
            if raw == member.value or raw == member.name:
                return member

        normalized = raw.upper().replace(" ", "_").replace("-", "_")
        for member in cls:
            if normalized in (member.name, member.value.upper()):
                return member

        alias = getattr(cls, "_aliases", {}).get(normalized)
        if alias is not None:
            return cls(alias)

        raise ValueError(
            f"'{value}' is not a valid {cls.__name__}. Expected one of: {', '.join(cls.values())}."
        )


class ClaimStatus(_WireEnum):
    """Lifecycle state of an expense claim. Transitions are governed by
    :mod:`app.domain.claim_state_machine` and additionally enforced by a database trigger."""

    DRAFT = "Draft"
    SUBMITTED = "Submitted"
    PROCESSING = "Processing_AI"
    AUTO_APPROVED = "Auto_Approved"
    MANAGER_REVIEW = "Manager_Review"
    FINANCE_REVIEW = "Finance_Review"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    REIMBURSED = "Disbursed"
    FLAGGED_FRAUD = "Flagged_Fraud"


# Canonical spec spellings accepted as input (normalized: upper + underscores).
ClaimStatus._aliases = {  # type: ignore[attr-defined]
    "PROCESSING_AI": ClaimStatus.PROCESSING.value,
    "PENDING_REVIEW": ClaimStatus.MANAGER_REVIEW.value,
    "DISBURSED": ClaimStatus.REIMBURSED.value,
    "REIMBURSED": ClaimStatus.REIMBURSED.value,
    "FLAGGED": ClaimStatus.FLAGGED_FRAUD.value,
}


class ExpenseItemStatus(_WireEnum):
    """Evaluation outcome of a single expense item.

    Each item of a claim is judged independently (policy validation + fraud screening), and the
    claim's own :class:`ClaimStatus` is then *rolled up* from the set of item statuses — see
    ``ClaimService._roll_up_status``. Fraud outranks a policy hold, deliberately: a claim
    containing one fraud-flagged item goes to fraud review regardless of how clean its siblings are.

    There is no ``Draft`` member (an item is only ever written as part of a submitted claim) and no
    ``Processing`` member (items are evaluated inside one transaction, so the intermediate state is
    never observable).
    """

    SUBMITTED = "Submitted"          # awaiting automated checks
    AUTO_APPROVED = "Auto_Approved"  # cleared both engines
    POLICY_HOLD = "Policy_Hold"      # failed policy, or policy asked for a human
    FRAUD_FLAG = "Fraud_Flag"        # risk score at or above the routing threshold
    MANAGER_APPROVED = "Manager_Approved"  # a human cleared the hold or the flag
    REJECTED = "Rejected"


class EmployeeGrade(_WireEnum):
    """Seniority band; drives grade-dependent policy limits."""

    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    L5 = "L5"
    DIRECTOR = "Director"
    VP = "VP"


class FraudRiskLevel(_WireEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AttachmentKind(_WireEnum):
    RECEIPT = "RECEIPT"
    PRE_APPROVAL = "PRE_APPROVAL"
    SUPPORTING = "SUPPORTING"


class ApprovalWorkflowStatus(_WireEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class ApprovalStepStatus(_WireEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SKIPPED = "SKIPPED"


class AIInferenceStatus(_WireEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    FALLBACK = "FALLBACK"


class AuditAction(_WireEnum):
    """Well-known audit action names.

    ``audit_logs.action`` is a ``VARCHAR``, not a database enum: later phases must be able to
    record new actions without a migration. This enum is the curated vocabulary for the actions
    Phase 1 emits, and keeps spelling consistent across call sites. The four values the frontend
    already renders (``SUBMIT_CLAIM``, ``APPROVAL_ACTION``, ``POLICY_UPDATE``, ``IAM_REFINED``)
    must not be renamed.
    """

    CLAIM_CREATE = "CLAIM_CREATE"
    SUBMIT_CLAIM = "SUBMIT_CLAIM"
    CLAIM_UPDATE = "CLAIM_UPDATE"
    CLAIM_STATUS_CHANGE = "CLAIM_STATUS_CHANGE"
    EXPENSE_ITEM_DECISION = "EXPENSE_ITEM_DECISION"
    POLICY_EVALUATION = "POLICY_EVALUATION"
    FRAUD_SCREENING = "FRAUD_SCREENING"
    FRAUD_FLAG = "FRAUD_FLAG"
    APPROVAL_ACTION = "APPROVAL_ACTION"
    REVIEWER_ASSIGNED = "REVIEWER_ASSIGNED"
    COMMENT_ADDED = "COMMENT_ADDED"
    ATTACHMENT_ADDED = "ATTACHMENT_ADDED"
    RECEIPT_UPLOAD = "RECEIPT_UPLOAD"
    POLICY_UPDATE = "POLICY_UPDATE"
    IAM_REFINED = "IAM_REFINED"
    USER_CREATE = "USER_CREATE"
    USER_UPDATE = "USER_UPDATE"
    USER_ROLE_CHANGE = "USER_ROLE_CHANGE"
    USER_ENABLE = "USER_ENABLE"
    USER_DISABLE = "USER_DISABLE"


class AuditEntity(_WireEnum):
    """Well-known ``audit_logs.entity_type`` values (also a ``VARCHAR`` for the same reason)."""

    CLAIM = "Claim"
    EXPENSE_ITEM = "ExpenseItem"
    # Retained for historical rows: the ``receipts`` tables were dropped in 0008, but
    # ``audit_logs.entity_type`` is a VARCHAR and older entries still carry this value.
    RECEIPT = "Receipt"
    EMPLOYEE = "Employee"
    POLICY_RULE = "PolicyRule"
    APPROVAL_WORKFLOW = "ApprovalWorkflow"
    APPROVAL_STEP = "ApprovalStep"
    FRAUD_RESULT = "FraudResult"
    COMMENT = "Comment"
    ATTACHMENT = "Attachment"
    USER = "User"
    IAM_POLICY = "IamPolicy"


def _pg_enum(python_enum: type[_WireEnum], name: str, *, create_type: bool = True) -> SAEnum:
    """Build a native PostgreSQL enum type that stores member *values*.

    ``create_type=False`` is used inside migrations where the type is created explicitly.
    """
    return SAEnum(
        python_enum,
        name=name,
        values_callable=lambda e: [member.value for member in e],
        create_type=create_type,
        validate_strings=True,
    )


# Named PG enum types. Created and dropped exclusively by Alembic.
CLAIM_STATUS_ENUM_NAME = "claim_status"
EXPENSE_ITEM_STATUS_ENUM_NAME = "expense_item_status"
EMPLOYEE_GRADE_ENUM_NAME = "employee_grade"
FRAUD_RISK_LEVEL_ENUM_NAME = "fraud_risk_level"
ATTACHMENT_KIND_ENUM_NAME = "attachment_kind"
APPROVAL_WORKFLOW_STATUS_ENUM_NAME = "approval_workflow_status"
APPROVAL_STEP_STATUS_ENUM_NAME = "approval_step_status"
AI_INFERENCE_STATUS_ENUM_NAME = "ai_inference_status"

claim_status_enum = _pg_enum(ClaimStatus, CLAIM_STATUS_ENUM_NAME)
expense_item_status_enum = _pg_enum(ExpenseItemStatus, EXPENSE_ITEM_STATUS_ENUM_NAME)
employee_grade_enum = _pg_enum(EmployeeGrade, EMPLOYEE_GRADE_ENUM_NAME)
fraud_risk_level_enum = _pg_enum(FraudRiskLevel, FRAUD_RISK_LEVEL_ENUM_NAME)
attachment_kind_enum = _pg_enum(AttachmentKind, ATTACHMENT_KIND_ENUM_NAME)
approval_workflow_status_enum = _pg_enum(
    ApprovalWorkflowStatus, APPROVAL_WORKFLOW_STATUS_ENUM_NAME
)
approval_step_status_enum = _pg_enum(ApprovalStepStatus, APPROVAL_STEP_STATUS_ENUM_NAME)
ai_inference_status_enum = _pg_enum(AIInferenceStatus, AI_INFERENCE_STATUS_ENUM_NAME)


def status_value(status: "ClaimStatus | str | None") -> Optional[str]:
    """Wire string for a status that may already be a plain string (defensive serializer helper)."""
    if status is None:
        return None
    if isinstance(status, ClaimStatus):
        return status.value
    return str(status)
