"""ClaimRepository: identity, queries, duplicate detection, and guarded transitions."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.domain.errors import (
    ForbiddenError,
    ImmutableEntityError,
    InvalidStateTransitionError,
    NotFoundError,
)
from app.models.enums import AttachmentKind, ClaimStatus, FraudRiskLevel
from app.repositories.claim_repository import ClaimQuery


@pytest.fixture
def claims(repositories):
    return repositories["claims"]


# --- identity ----------------------------------------------------------------


def test_claim_numbers_are_unique_and_sequential(claims):
    first = claims.next_claim_number()
    second = claims.next_claim_number()
    assert first != second
    assert first.startswith("EXP-")
    assert int(second.rsplit("-", 1)[1]) > int(first.rsplit("-", 1)[1])


def test_claim_number_uses_the_requested_year(claims):
    assert claims.next_claim_number(year=2030).startswith("EXP-2030-")


def test_get_by_id_or_number_accepts_both(claims, make_claim):
    claim = make_claim()
    assert claims.get_by_id_or_number(str(claim.id)).id == claim.id
    assert claims.get_by_id_or_number(claim.claim_number).id == claim.id


def test_get_by_id_or_number_returns_none_for_unknown(claims):
    assert claims.get_by_id_or_number(str(uuid.uuid4())) is None
    assert claims.get_by_id_or_number("not-a-uuid-or-number") is None


def test_get_or_raise_raises_not_found(claims):
    with pytest.raises(NotFoundError):
        claims.get_or_raise(uuid.uuid4())


def test_create_claim_opens_the_history(claims, make_claim):
    claim = make_claim()
    history = claims.list_history(claim.id)
    assert len(history) == 1
    assert history[0].sequence == 1
    assert history[0].from_status is None
    assert history[0].to_status is ClaimStatus.DRAFT
    assert history[0].step_name == "Create Claim"


# --- queries -----------------------------------------------------------------


def test_search_filters_by_employee(claims, make_claim, employee, other_employee):
    mine = make_claim(owner=employee)
    make_claim(owner=other_employee)

    found = claims.search(ClaimQuery(employee_id=employee.id))
    assert mine.id in {c.id for c in found}
    assert all(c.employee_id == employee.id for c in found)


def test_search_filters_by_status_and_category(claims, make_claim):
    reviewed = make_claim(ClaimStatus.MANAGER_REVIEW, category="Hotel / Lodging")
    make_claim(ClaimStatus.DRAFT, category="Meals")

    by_status = claims.search(ClaimQuery(status=ClaimStatus.MANAGER_REVIEW))
    assert reviewed.id in {c.id for c in by_status}
    assert all(c.status is ClaimStatus.MANAGER_REVIEW for c in by_status)

    by_category = claims.search(ClaimQuery(category="Hotel / Lodging"))
    assert all(c.category == "Hotel / Lodging" for c in by_category)


def test_search_filters_by_expense_date_window(claims, make_claim):
    old = make_claim(expense_date=date.today() - timedelta(days=60))
    recent = make_claim(expense_date=date.today() - timedelta(days=2))

    found = {
        c.id
        for c in claims.search(
            ClaimQuery(expense_date_from=date.today() - timedelta(days=10))
        )
    }
    assert recent.id in found
    assert old.id not in found


def test_search_filters_by_latest_risk_level(claims, repositories, make_claim):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    # An earlier LOW verdict must not match once a later HIGH verdict exists.
    repositories["fraud"].record(
        claim_id=claim.id, risk_score=5, risk_level=FraudRiskLevel.LOW,
        is_flagged=False, recommended_action="AUTO_APPROVE",
    )
    repositories["fraud"].record(
        claim_id=claim.id, risk_score=80, risk_level=FraudRiskLevel.CRITICAL,
        is_flagged=True, recommended_action="INVESTIGATE_FRAUD",
    )

    critical = {c.id for c in claims.search(ClaimQuery(risk_level=FraudRiskLevel.CRITICAL))}
    low = {c.id for c in claims.search(ClaimQuery(risk_level=FraudRiskLevel.LOW))}
    assert claim.id in critical
    assert claim.id not in low


def test_search_paginates(claims, make_claim, employee):
    for _ in range(3):
        make_claim(owner=employee)

    page = claims.search(ClaimQuery(employee_id=employee.id, limit=2))
    assert len(page) == 2
    second_page = claims.search(ClaimQuery(employee_id=employee.id, limit=2, offset=2))
    assert {c.id for c in page}.isdisjoint({c.id for c in second_page})


def test_review_queue_contains_only_open_review_states(claims, make_claim):
    pending = make_claim(ClaimStatus.MANAGER_REVIEW)
    make_claim(ClaimStatus.DRAFT)

    queue = claims.list_review_queue()
    assert pending.id in {c.id for c in queue}
    assert all(
        c.status
        in (ClaimStatus.MANAGER_REVIEW, ClaimStatus.FINANCE_REVIEW, ClaimStatus.FLAGGED_FRAUD)
        for c in queue
    )


# --- duplicate detection -----------------------------------------------------


def test_finds_exact_duplicate(claims, make_claim, employee):
    when = date.today() - timedelta(days=3)
    original = make_claim(
        owner=employee, vendor="Sweetgreen SF", expense_date=when, amount=Decimal("22.50")
    )

    found = claims.find_duplicate_claims(
        employee_id=employee.id, merchant_vendor="Sweetgreen SF",
        expense_date=when, amount_usd=Decimal("22.50"),
    )
    assert original.id in {c.id for c in found}


def test_duplicate_match_ignores_vendor_case_and_padding(claims, make_claim, employee):
    when = date.today() - timedelta(days=3)
    original = make_claim(owner=employee, vendor="Sweetgreen SF", expense_date=when,
                          amount=Decimal("22.50"))

    found = claims.find_duplicate_claims(
        employee_id=employee.id, merchant_vendor="  sweetgreen sf  ",
        expense_date=when, amount_usd=Decimal("22.50"),
    )
    assert original.id in {c.id for c in found}


@pytest.mark.parametrize(
    "vendor, amount, day_offset",
    [
        ("Different Vendor", Decimal("22.50"), 3),   # other vendor
        ("Sweetgreen SF", Decimal("25.00"), 3),      # other amount
        ("Sweetgreen SF", Decimal("22.50"), 4),      # other date
    ],
)
def test_non_duplicates_are_not_matched(claims, make_claim, employee, vendor, amount, day_offset):
    make_claim(owner=employee, vendor="Sweetgreen SF",
               expense_date=date.today() - timedelta(days=3), amount=Decimal("22.50"))

    found = claims.find_duplicate_claims(
        employee_id=employee.id, merchant_vendor=vendor,
        expense_date=date.today() - timedelta(days=day_offset), amount_usd=amount,
    )
    assert found == []


def test_other_employees_identical_claim_is_not_a_duplicate(
    claims, make_claim, employee, other_employee
):
    when = date.today() - timedelta(days=3)
    make_claim(owner=other_employee, vendor="Shared Cafe", expense_date=when,
               amount=Decimal("30.00"))

    found = claims.find_duplicate_claims(
        employee_id=employee.id, merchant_vendor="Shared Cafe",
        expense_date=when, amount_usd=Decimal("30.00"),
    )
    assert found == []


def test_rejected_claims_excluded_from_duplicates_by_default(claims, make_claim, employee):
    """Re-filing a corrected version of a rejected claim is legitimate."""
    when = date.today() - timedelta(days=3)
    rejected = make_claim(ClaimStatus.REJECTED, owner=employee, vendor="Fix Me",
                          expense_date=when, amount=Decimal("15.00"))

    assert claims.find_duplicate_claims(
        employee_id=employee.id, merchant_vendor="Fix Me",
        expense_date=when, amount_usd=Decimal("15.00"),
    ) == []
    assert rejected.id in {
        c.id
        for c in claims.find_duplicate_claims(
            employee_id=employee.id, merchant_vendor="Fix Me", expense_date=when,
            amount_usd=Decimal("15.00"), include_rejected=True,
        )
    }


def test_duplicate_search_excludes_the_claim_itself(claims, make_claim, employee):
    when = date.today() - timedelta(days=3)
    claim = make_claim(owner=employee, vendor="Solo", expense_date=when, amount=Decimal("9.00"))

    found = claims.find_duplicate_claims(
        employee_id=employee.id, merchant_vendor="Solo", expense_date=when,
        amount_usd=Decimal("9.00"), exclude_claim_id=claim.id,
    )
    assert found == []


def test_same_day_vendor_claims_for_split_detection(claims, make_claim, employee):
    when = date.today() - timedelta(days=1)
    first = make_claim(owner=employee, vendor="Uber SF", expense_date=when,
                       amount=Decimal("24.00"))
    second = make_claim(owner=employee, vendor="Uber SF", expense_date=when,
                        amount=Decimal("26.00"))

    found = claims.find_same_day_vendor_claims(
        employee_id=employee.id, merchant_vendor="Uber SF", expense_date=when,
        exclude_claim_id=second.id,
    )
    assert {c.id for c in found} == {first.id}


def test_find_by_receipt_id(claims, db_session, make_claim, employee):
    from app.models.receipt import ExtractionStatus, Receipt

    receipt = Receipt(file_name="r.png", extraction_status=ExtractionStatus.COMPLETED)
    db_session.add(receipt)
    db_session.flush()

    claim = make_claim(receipt_id=receipt.id)
    assert claims.find_by_receipt_id(receipt.id).id == claim.id
    assert claims.find_by_receipt_id(uuid.uuid4()) is None


# --- transitions -------------------------------------------------------------


def test_transition_records_history_timestamps_and_bumps_version(claims, make_claim):
    claim = make_claim()
    version_before = claim.version

    claims.transition_status(
        claim, ClaimStatus.SUBMITTED, actor_role="employee",
        actor_sub="sub-1", actor_name="Sarah Jenkins",
    )

    assert claim.status is ClaimStatus.SUBMITTED
    assert claim.submitted_at is not None
    assert claim.version > version_before

    history = claims.list_history(claim.id)
    assert [entry.sequence for entry in history] == [1, 2]
    latest = history[-1]
    assert latest.from_status is ClaimStatus.DRAFT
    assert latest.to_status is ClaimStatus.SUBMITTED
    assert latest.actor_name == "Sarah Jenkins"
    assert latest.actor_role == "employee"


def test_transition_rejects_illegal_edge(claims, make_claim):
    claim = make_claim()
    with pytest.raises(InvalidStateTransitionError):
        claims.transition_status(claim, ClaimStatus.REIMBURSED, actor_role="finance")


def test_transition_rejects_unauthorized_role(claims, make_claim):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    with pytest.raises(ForbiddenError):
        # Only manager/finance/admin (or the system) may move a claim into review.
        claims.transition_status(claim, ClaimStatus.FINANCE_REVIEW, actor_role="employee")


def test_illegal_transition_leaves_the_claim_untouched(claims, make_claim):
    claim = make_claim()
    with pytest.raises(InvalidStateTransitionError):
        claims.transition_status(claim, ClaimStatus.APPROVED, actor_role="manager")
    assert claim.status is ClaimStatus.DRAFT
    assert len(claims.list_history(claim.id)) == 1  # no history row written


def test_first_timestamp_wins_when_a_state_is_re_entered(claims, make_claim):
    """Re-opening review must not rewrite when review first started."""
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    first_review_at = claim.review_started_at
    assert first_review_at is not None

    claims.transition_status(claim, ClaimStatus.FLAGGED_FRAUD, actor_role="manager")
    claims.transition_status(claim, ClaimStatus.MANAGER_REVIEW, actor_role="manager")
    assert claim.review_started_at == first_review_at


def test_decision_metadata_recorded_on_approve_and_reject(claims, make_claim):
    approved = make_claim(ClaimStatus.FINANCE_REVIEW)
    claims.approve_claim(approved, actor_role="finance", actor_sub="sub-fin",
                         actor_name="Fin", notes="Checked receipts")
    assert approved.status is ClaimStatus.APPROVED
    assert approved.approved_at is not None
    assert approved.decided_at is not None
    assert approved.decided_by_sub == "sub-fin"
    assert approved.decision_notes == "Checked receipts"

    rejected = make_claim(ClaimStatus.MANAGER_REVIEW)
    claims.reject_claim(rejected, actor_role="manager", reason="Missing itemisation")
    assert rejected.status is ClaimStatus.REJECTED
    assert rejected.rejected_at is not None
    assert rejected.rejection_reason == "Missing itemisation"


def test_flag_fraud_marks_the_history_entry_as_warning(claims, make_claim):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    claims.flag_fraud(claim, actor_role="finance", notes="Vendor mismatch")
    assert claim.status is ClaimStatus.FLAGGED_FRAUD
    assert claims.list_history(claim.id)[-1].outcome == "WARNING"


def test_history_sequence_stays_gap_free_across_many_transitions(claims, make_claim):
    claim = make_claim(ClaimStatus.REIMBURSED)
    sequences = [entry.sequence for entry in claims.list_history(claim.id)]
    assert sequences == list(range(1, len(sequences) + 1))


# --- assignment, editing, children -------------------------------------------


def test_assign_and_clear_reviewer(claims, make_claim, other_employee):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)

    claims.assign_reviewer(claim, other_employee.id, actor_role="manager", actor_name="Mgr")
    assert claim.assigned_reviewer_id == other_employee.id
    assert claim.assigned_at is not None

    claims.assign_reviewer(claim, None, actor_role="manager", actor_name="Mgr")
    assert claim.assigned_reviewer_id is None
    assert claim.assigned_at is None


def test_assignment_is_recorded_without_changing_status(claims, make_claim, other_employee):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    claims.assign_reviewer(claim, other_employee.id, actor_role="manager")

    latest = claims.list_history(claim.id)[-1]
    assert latest.step_name == "Assign Reviewer"
    assert latest.from_status == latest.to_status == ClaimStatus.MANAGER_REVIEW


def test_update_fields_allowed_on_draft(claims, make_claim):
    claim = make_claim()
    claims.update_fields(claim, actor_sub="sub-1", purpose_description="Corrected purpose")
    assert claim.purpose_description == "Corrected purpose"
    assert claim.updated_by_sub == "sub-1"


def test_update_fields_blocked_after_submission(claims, make_claim):
    claim = make_claim(ClaimStatus.SUBMITTED)
    with pytest.raises(ImmutableEntityError):
        claims.update_fields(claim, purpose_description="Too late")


def test_update_fields_cannot_smuggle_a_status_change(claims, make_claim):
    claim = make_claim()
    claims.update_fields(claim, status=ClaimStatus.APPROVED, purpose_description="ok")
    assert claim.status is ClaimStatus.DRAFT


def test_update_rejects_unknown_attribute(claims, make_claim):
    claim = make_claim()
    with pytest.raises(AttributeError):
        claims.update_fields(claim, nonexistent_column="x")


def test_add_comment_and_attachment(claims, make_claim):
    claim = make_claim()

    comment = claims.add_comment(
        claim, body="Please attach the itemised receipt.",
        author_name="Marcus Vance", author_role="manager", is_internal=True,
    )
    assert comment.claim_id == claim.id
    assert comment.is_internal is True

    attachment = claims.add_attachment(
        claim, file_name="folio.pdf", kind=AttachmentKind.SUPPORTING,
        content_type="application/pdf", file_size_bytes=1024,
    )
    assert attachment.claim_id == claim.id
    assert attachment.kind is AttachmentKind.SUPPORTING


def test_record_step_adds_history_without_transitioning(claims, make_claim):
    claim = make_claim(ClaimStatus.PROCESSING)
    before = len(claims.list_history(claim.id))

    claims.record_step(
        claim, step_name="Policy Validation", action="Passed policy constraints",
        actor_name="Policy Engine", actor_role="admin",
    )
    history = claims.list_history(claim.id)
    assert len(history) == before + 1
    assert history[-1].from_status == history[-1].to_status == ClaimStatus.PROCESSING


def test_list_for_employee_excludes_the_current_claim(claims, make_claim, employee):
    first = make_claim(owner=employee)
    second = make_claim(owner=employee)

    peers = claims.list_for_employee(employee.id, exclude_claim_id=second.id)
    assert second.id not in {c.id for c in peers}
    assert first.id in {c.id for c in peers}


def test_count_for_employee(claims, make_claim, employee):
    before = claims.count_for_employee(employee.id)
    make_claim(owner=employee)
    assert claims.count_for_employee(employee.id) == before + 1
