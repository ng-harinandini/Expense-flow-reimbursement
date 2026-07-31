"""Domain validation.

Two tiers of checking, deliberately separated:

* **Shape** — types, required fields, non-negative amounts, ISO currency: enforced by the Pydantic
  v2 request models in ``app/schemas/schemas.py``, which reject bad input before any query runs.
* **State** — the rules in this module, which need the database or the caller's identity to decide:
  does the employee exist, does the claim belong to them, is the receipt already claimed, is this a
  duplicate submission, is the claim still editable.

Each function raises a :mod:`app.domain.errors` type and returns ``None``, so a service reads as a
list of preconditions followed by the action. Every check is independent — no ordering coupling.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable, Optional, Sequence

from app.domain import claim_state_machine as fsm
from app.domain.errors import (
    DuplicateClaimError,
    ForbiddenError,
    ImmutableEntityError,
    NotFoundError,
    ValidationError,
)
from app.models.claim import Claim
from app.models.expense_item import ExpenseItem
from app.models.organization import Employee
from app.services import receipt_extraction

#: Claims older than this need a written justification and Finance Director approval. The claim is
#: still accepted — the policy engine flags it — because refusing the submission would leave the
#: employee with no way to file a late expense at all.
CLAIM_AGE_MAX_DAYS = 90

#: How far ahead an expense date may be. Non-zero to tolerate timezone skew between a client's
#: local date and the server's UTC date.
FUTURE_DATE_GRACE_DAYS = 1

MAX_ATTENDEE_TEXT_LENGTH = 4000
MIN_ATTENDEE_TEXT_LENGTH = 5


# --- existence -----------------------------------------------------------------

def require_employee(employee: Optional[Employee], identifier: str = "") -> Employee:
    """The submitting employee must exist in the directory."""
    if employee is None:
        raise NotFoundError("Employee", identifier or "unknown")
    if not employee.is_active:
        raise ValidationError(
            f"Employee {employee.employee_code} is deactivated and cannot file claims.",
            details={"employeeId": employee.employee_code},
        )
    return employee


def require_claim(claim: Optional[Claim], identifier: str) -> Claim:
    if claim is None:
        raise NotFoundError("Claim", identifier)
    return claim


def require_expense_item(item: Optional[ExpenseItem], identifier: str) -> ExpenseItem:
    if item is None:
        raise NotFoundError("ExpenseItem", identifier)
    return item


# --- ownership -----------------------------------------------------------------

def require_claim_ownership(claim: Claim, employee: Employee) -> None:
    """The claim must belong to ``employee``.

    Callers that must not leak a claim's existence to a non-owner should catch this and answer
    404 instead; services use it where the caller is already known to be the owner-or-nothing.
    """
    if claim.employee_id != employee.id:
        raise ForbiddenError(
            "This claim belongs to another employee.",
            details={"claimNumber": claim.claim_number},
        )


def require_receipt_ownership(file_url: Optional[str], employee: Employee) -> None:
    """The receipt object must have been uploaded by ``employee``.

    The client holds the upload response and echoes ``file_url`` back at submit time, so without
    this check a caller could attach a document uploaded by somebody else. ``expense_items`` has no
    ``s3_key`` column to compare against — the employee segment embedded in the object key by
    ``receipt_extraction.build_object_key`` is the only server-side evidence of ownership.
    """
    if file_url is None:
        return
    if not receipt_extraction.owns_object(file_url, str(employee.id)):
        raise ForbiddenError(
            "This receipt was uploaded by another employee.",
            details={"fileUrl": file_url},
        )


# --- mutability ----------------------------------------------------------------

def require_editable(claim: Claim) -> None:
    """A claim may only be edited while in an editable state (i.e. never after approval)."""
    if claim.status not in fsm.EDITABLE_STATUSES:
        raise ImmutableEntityError(
            f"Claim {claim.claim_number} is '{claim.status.value}' and can no longer be modified.",
            details={
                "claimNumber": claim.claim_number,
                "currentStatus": claim.status.value,
                "editableStatuses": sorted(s.value for s in fsm.EDITABLE_STATUSES),
            },
        )


def require_not_terminal(claim: Claim) -> None:
    """Reject any action on a closed claim (rejected, or already reimbursed)."""
    if fsm.is_terminal(claim.status):
        raise ImmutableEntityError(
            f"Claim {claim.claim_number} is closed ('{claim.status.value}'); no further action is possible.",
            details={"claimNumber": claim.claim_number, "currentStatus": claim.status.value},
        )


# --- expense facts -------------------------------------------------------------

def validate_amount(amount: Decimal | float | int, *, field: str = "amount") -> Decimal:
    """Amount must be a positive number. Rejects zero and negatives."""
    try:
        value = Decimal(str(amount))
    except (ArithmeticError, ValueError, TypeError):
        raise ValidationError(f"'{field}' must be a number.", details={"field": field})

    if value <= 0:
        raise ValidationError(
            f"'{field}' must be greater than zero.",
            details={"field": field, "value": str(value)},
        )
    return value


def validate_expense_date(expense_date: Optional[date], *, today: Optional[date] = None) -> date:
    """Expense date is mandatory and may not be in the future."""
    if expense_date is None:
        raise ValidationError("'expenseDate' is required.", details={"field": "expenseDate"})

    reference = today or date.today()
    if expense_date > reference + timedelta(days=FUTURE_DATE_GRACE_DAYS):
        raise ValidationError(
            "'expenseDate' cannot be in the future.",
            details={"field": "expenseDate", "value": expense_date.isoformat()},
        )
    return expense_date


def validate_currency(currency: Optional[str]) -> str:
    """Currency must be a 3-letter ISO 4217 alphabetic code (matches the DB check constraint)."""
    code = (currency or "USD").strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise ValidationError(
            "'currency' must be a 3-letter ISO 4217 code.",
            details={"field": "currency", "value": currency},
        )
    return code


def require_fields(payload: dict, fields: Iterable[str]) -> None:
    """Every name in ``fields`` must be present and non-blank in ``payload``."""
    missing = [
        name
        for name in fields
        if payload.get(name) is None
        or (isinstance(payload.get(name), str) and not payload[name].strip())
    ]
    if missing:
        raise ValidationError(
            f"Missing mandatory field(s): {', '.join(missing)}.",
            details={"fields": missing},
        )


def days_since_expense(expense_date: date, *, on_date: Optional[date] = None) -> int:
    """Age of the expense in days (never negative)."""
    return max((on_date or date.today()) - expense_date, timedelta(0)).days


def is_stale_claim(expense_date: date, *, on_date: Optional[date] = None) -> bool:
    """Whether the claim breaches the 90-day submission window."""
    return days_since_expense(expense_date, on_date=on_date) > CLAIM_AGE_MAX_DAYS


# --- duplicates & receipts -----------------------------------------------------

def require_no_duplicate(duplicates: Sequence[ExpenseItem]) -> None:
    """Reject a resubmission of an expense already on file.

    Distinct from the fraud engine's ``DUPLICATE_SUBMISSION`` flag: this blocks the write outright
    (409) so an exact double-submit — a double-tapped button, a retried request — never creates a
    second item. The fraud flag remains for the softer near-duplicate signals.
    """
    if not duplicates:
        return
    existing = duplicates[0]
    raise DuplicateClaimError(
        f"An equivalent expense already exists on claim {existing.claim.claim_number}.",
        details={
            "existingClaimId": str(existing.claim_id),
            "existingClaimNumber": existing.claim.claim_number,
            "existingItemId": str(existing.id),
            "lineNumber": existing.line_number,
            "existingStatus": existing.status.value,
            "merchantVendor": existing.merchant_vendor,
            "expenseDate": existing.expense_date.isoformat(),
            "amountUsd": str(existing.amount_usd),
        },
    )


def validate_attendees(attendees: Optional[str], *, required: bool) -> Optional[str]:
    """Attendee listing for categories that require it (e.g. Client Entertainment)."""
    text = (attendees or "").strip()
    if required and len(text) < MIN_ATTENDEE_TEXT_LENGTH:
        raise ValidationError(
            "'attendees' must list all internal and external attendees for this category.",
            details={"field": "attendees"},
        )
    if len(text) > MAX_ATTENDEE_TEXT_LENGTH:
        raise ValidationError(
            f"'attendees' exceeds {MAX_ATTENDEE_TEXT_LENGTH} characters.",
            details={"field": "attendees"},
        )
    return text or None
