"""Domain validation rules (pure unit tests — no database)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.domain import validators
from app.domain.errors import (
    DuplicateClaimError,
    ForbiddenError,
    ImmutableEntityError,
    NotFoundError,
    ReceiptAlreadyClaimedError,
    ValidationError,
)
from app.models.enums import ClaimStatus

TODAY = date(2026, 7, 28)


def _employee(active: bool = True, code: str = "emp-101", identity=1) -> SimpleNamespace:
    return SimpleNamespace(id=identity, employee_code=code, is_active=active)


def _claim(status: ClaimStatus = ClaimStatus.DRAFT, employee_id=1) -> SimpleNamespace:
    return SimpleNamespace(
        id="c-1",
        claim_number="EXP-2026-1001",
        status=status,
        employee_id=employee_id,
        merchant_vendor="Vendor",
        expense_date=TODAY,
        amount_usd=Decimal("10.00"),
    )


# --- amounts -----------------------------------------------------------------


@pytest.mark.parametrize("value", ["10.50", 10.5, 1, Decimal("0.01")])
def test_valid_amounts(value):
    assert validators.validate_amount(value) > 0


@pytest.mark.parametrize("value", [0, "0", -1, "-0.01", Decimal("-5")])
def test_negative_and_zero_amounts_rejected(value):
    with pytest.raises(ValidationError, match="greater than zero"):
        validators.validate_amount(value)


def test_non_numeric_amount_rejected():
    with pytest.raises(ValidationError, match="must be a number"):
        validators.validate_amount("abc")


def test_amount_error_names_the_field():
    with pytest.raises(ValidationError) as raised:
        validators.validate_amount(-1, field="amountUSD")
    assert raised.value.details["field"] == "amountUSD"


# --- dates -------------------------------------------------------------------


def test_past_expense_date_accepted():
    assert validators.validate_expense_date(TODAY - timedelta(days=30), today=TODAY)


def test_today_accepted():
    assert validators.validate_expense_date(TODAY, today=TODAY) == TODAY


def test_future_expense_date_rejected():
    with pytest.raises(ValidationError, match="cannot be in the future"):
        validators.validate_expense_date(TODAY + timedelta(days=5), today=TODAY)


def test_one_day_ahead_tolerated_for_timezone_skew():
    assert validators.validate_expense_date(TODAY + timedelta(days=1), today=TODAY)


def test_missing_expense_date_rejected():
    with pytest.raises(ValidationError, match="required"):
        validators.validate_expense_date(None)


def test_claim_age_helpers():
    assert validators.days_since_expense(TODAY - timedelta(days=10), on_date=TODAY) == 10
    # A future date must not produce a negative age.
    assert validators.days_since_expense(TODAY + timedelta(days=3), on_date=TODAY) == 0
    assert not validators.is_stale_claim(TODAY - timedelta(days=89), on_date=TODAY)
    assert validators.is_stale_claim(TODAY - timedelta(days=91), on_date=TODAY)


# --- currency ----------------------------------------------------------------


@pytest.mark.parametrize("raw, expected", [("usd", "USD"), (" eur ", "EUR"), (None, "USD")])
def test_currency_normalized(raw, expected):
    assert validators.validate_currency(raw) == expected


@pytest.mark.parametrize("raw", ["US", "USDD", "12X", "$$$"])
def test_invalid_currency_rejected(raw):
    with pytest.raises(ValidationError, match="ISO 4217"):
        validators.validate_currency(raw)


# --- mandatory fields --------------------------------------------------------


def test_required_fields_present():
    validators.require_fields({"amount": 1, "merchantVendor": "X"}, ("amount", "merchantVendor"))


def test_missing_and_blank_fields_reported_together():
    with pytest.raises(ValidationError) as raised:
        validators.require_fields(
            {"amount": None, "merchantVendor": "   ", "category": "Meals"},
            ("amount", "merchantVendor", "category"),
        )
    assert raised.value.details["fields"] == ["amount", "merchantVendor"]


# --- existence ---------------------------------------------------------------


def test_require_employee_rejects_missing():
    with pytest.raises(NotFoundError):
        validators.require_employee(None, "emp-999")


def test_require_employee_rejects_deactivated():
    with pytest.raises(ValidationError, match="deactivated"):
        validators.require_employee(_employee(active=False))


def test_require_claim_and_receipt_missing():
    with pytest.raises(NotFoundError):
        validators.require_claim(None, "EXP-1")
    with pytest.raises(NotFoundError):
        validators.require_receipt(None, "r-1")


# --- ownership ---------------------------------------------------------------


def test_claim_ownership_accepts_owner():
    validators.require_claim_ownership(_claim(employee_id=1), _employee(identity=1))


def test_claim_ownership_rejects_other_employee():
    with pytest.raises(ForbiddenError, match="another employee"):
        validators.require_claim_ownership(_claim(employee_id=2), _employee(identity=1))


def test_receipt_ownership_by_uuid_link():
    receipt = SimpleNamespace(id="r-1", employee_ref_id=1, employee_id=None)
    validators.require_receipt_ownership(receipt, _employee(identity=1))


def test_receipt_ownership_by_external_code():
    """Receipts uploaded before the employees table existed only carry the code."""
    receipt = SimpleNamespace(id="r-1", employee_ref_id=None, employee_id="emp-101")
    validators.require_receipt_ownership(receipt, _employee(identity=1, code="emp-101"))


def test_receipt_ownership_rejects_other_employee():
    receipt = SimpleNamespace(id="r-1", employee_ref_id=2, employee_id="emp-104")
    with pytest.raises(ForbiddenError, match="another employee"):
        validators.require_receipt_ownership(receipt, _employee(identity=1, code="emp-101"))


# --- mutability --------------------------------------------------------------


def test_draft_is_editable():
    validators.require_editable(_claim(ClaimStatus.DRAFT))


@pytest.mark.parametrize(
    "status",
    [
        ClaimStatus.SUBMITTED,
        ClaimStatus.PROCESSING,
        ClaimStatus.MANAGER_REVIEW,
        ClaimStatus.APPROVED,
        ClaimStatus.AUTO_APPROVED,
        ClaimStatus.REIMBURSED,
        ClaimStatus.REJECTED,
    ],
)
def test_non_draft_claims_cannot_be_modified(status):
    with pytest.raises(ImmutableEntityError, match="no longer be modified"):
        validators.require_editable(_claim(status))


@pytest.mark.parametrize("status", [ClaimStatus.REJECTED, ClaimStatus.REIMBURSED])
def test_terminal_claims_reject_further_action(status):
    with pytest.raises(ImmutableEntityError, match="closed"):
        validators.require_not_terminal(_claim(status))


@pytest.mark.parametrize(
    "status", [ClaimStatus.DRAFT, ClaimStatus.MANAGER_REVIEW, ClaimStatus.APPROVED]
)
def test_open_claims_allow_action(status):
    validators.require_not_terminal(_claim(status))


# --- duplicates & receipts ---------------------------------------------------


def test_no_duplicates_passes():
    validators.require_no_duplicate([])


def test_duplicate_reports_the_existing_claim():
    with pytest.raises(DuplicateClaimError) as raised:
        validators.require_no_duplicate([_claim(ClaimStatus.MANAGER_REVIEW)])

    details = raised.value.details
    assert details["existingClaimNumber"] == "EXP-2026-1001"
    assert details["existingStatus"] == "Manager_Review"


def test_unclaimed_receipt_passes():
    validators.require_receipt_unclaimed("r-1", None)


def test_already_claimed_receipt_rejected():
    with pytest.raises(ReceiptAlreadyClaimedError) as raised:
        validators.require_receipt_unclaimed("r-1", _claim())
    assert raised.value.details["claimNumber"] == "EXP-2026-1001"


# --- attendees ---------------------------------------------------------------


def test_attendees_required_and_missing():
    with pytest.raises(ValidationError, match="attendees"):
        validators.validate_attendees("", required=True)


def test_attendees_required_and_present():
    assert validators.validate_attendees("Alice, Bob (ACME)", required=True)


def test_attendees_optional_when_not_required():
    assert validators.validate_attendees(None, required=False) is None


def test_attendees_length_capped():
    with pytest.raises(ValidationError, match="exceeds"):
        validators.validate_attendees("x" * 5000, required=False)
