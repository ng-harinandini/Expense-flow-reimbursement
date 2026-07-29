"""End-to-end claim lifecycle through ``ClaimService``.

Covers the whole spec'd path — Draft → Submitted → Processing → Pending Review → Approved /
Rejected → Reimbursed — plus the invariants that make it trustworthy: every transition audited and
historied, no bypass, no duplicate, nothing editable after approval, and a failed submission
leaving no partial rows behind.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.domain.errors import (
    ConcurrentUpdateError,
    DuplicateClaimError,
    ForbiddenError,
    ImmutableEntityError,
    InvalidStateTransitionError,
    NotFoundError,
    ReceiptAlreadyClaimedError,
    ValidationError,
)
from app.models.enums import ApprovalStepStatus, ClaimStatus, FraudRiskLevel


def _audit_actions(repositories, claim_number: str) -> list[str]:
    return [
        entry.action
        for entry in repositories["audit"].list_for_entity("Claim", claim_number)
    ]


# --- submission --------------------------------------------------------------


def test_submit_runs_the_full_pipeline(claim_service, claim_payload, employee_actor, employee):
    claim = claim_service.submit_claim(claim_payload(), actor=employee_actor)

    assert claim.claim_number.startswith("EXP-")
    assert claim.employee_id == employee.id
    # Auto-approved: $22.50 Meals is inside the $25 ceiling with a receipt attached.
    assert claim.status is ClaimStatus.AUTO_APPROVED
    assert claim.submitted_at is not None
    assert claim.processing_started_at is not None
    assert claim.policy_validation is not None
    assert claim.latest_fraud_result is not None


def test_submission_history_records_every_step_in_order(claim_service, claim_payload, employee_actor):
    claim = claim_service.submit_claim(claim_payload(), actor=employee_actor)
    steps = [(entry.sequence, entry.step_name, entry.to_status) for entry in claim.status_history]

    assert [s[0] for s in steps] == list(range(1, len(steps) + 1))  # gap-free
    names = [s[1] for s in steps]
    assert names[0] == "Create Claim"
    assert "Submit Claim" in names
    assert "Processing" in names
    assert "Policy Validation" in names
    assert "Fraud Screening" in names
    assert "Routing Decision" in names
    assert steps[-1][2] is ClaimStatus.AUTO_APPROVED


def test_submission_snapshots_the_employees_grade_and_department(
    claim_service, claim_payload, employee_actor, employee
):
    """A later promotion must not retroactively change how a claim was judged."""
    claim = claim_service.submit_claim(claim_payload(), actor=employee_actor)
    assert claim.employee_grade is employee.grade
    assert claim.department_id == employee.department_id


def test_submission_writes_audit_records(
    claim_service, claim_payload, employee_actor, repositories
):
    claim = claim_service.submit_claim(claim_payload(), actor=employee_actor)
    actions = _audit_actions(repositories, claim.claim_number)
    assert "CLAIM_CREATE" in actions
    assert "SUBMIT_CLAIM" in actions


def test_submission_creates_a_workflow(claim_service, claim_payload, employee_actor):
    claim = claim_service.submit_claim(claim_payload(), actor=employee_actor)
    workflow = claim.active_workflow
    assert workflow is not None
    assert [step.step_order for step in workflow.steps] == [1, 2]
    # Auto-approval skips the manager step but still needs finance to pay out.
    manager_step = next(s for s in workflow.steps if s.required_role == "manager")
    assert manager_step.status is ApprovalStepStatus.SKIPPED
    assert workflow.current_step_order == 2


def test_submission_adds_a_system_comment(claim_service, claim_payload, employee_actor):
    claim = claim_service.submit_claim(claim_payload(), actor=employee_actor)
    assert any("Route status set to" in comment.body for comment in claim.comments)


def test_actor_without_an_employee_record_is_rejected(claim_service, claim_payload):
    from tests.conftest import make_actor

    stranger = make_actor("employee", employee_code="emp-does-not-exist", sub="sub-stranger")
    with pytest.raises(NotFoundError):
        claim_service.submit_claim(claim_payload(), actor=stranger)


# --- routing -----------------------------------------------------------------


def test_over_limit_claim_routes_to_manager_review(claim_service, claim_payload, employee_actor):
    claim = claim_service.submit_claim(
        claim_payload(amount=Decimal("38.00"), amountUSD=Decimal("38.00")), actor=employee_actor
    )
    # Above the $25 auto-approve ceiling but under the $40 cap -> human review.
    assert claim.status is ClaimStatus.MANAGER_REVIEW
    assert claim.policy_validation["requiresManualReview"] is True


def test_policy_violation_routes_to_manager_review(claim_service, claim_payload, employee_actor):
    claim = claim_service.submit_claim(
        claim_payload(amount=Decimal("120.00"), amountUSD=Decimal("120.00")), actor=employee_actor
    )
    assert claim.status is ClaimStatus.MANAGER_REVIEW
    assert claim.policy_validation["overallPassed"] is False


def test_flights_never_auto_approve(claim_service, claim_payload, employee_actor):
    claim = claim_service.submit_claim(
        claim_payload(category="Flights", amount=Decimal("640.00"),
                      amountUSD=Decimal("640.00")),
        actor=employee_actor,
    )
    assert claim.status is ClaimStatus.MANAGER_REVIEW


def test_missing_receipt_routes_to_review(claim_service, claim_payload, employee_actor):
    claim = claim_service.submit_claim(
        claim_payload(amount=Decimal("30.00"), amountUSD=Decimal("30.00"),
                      receiptAttached=False),
        actor=employee_actor,
    )
    assert claim.status is ClaimStatus.MANAGER_REVIEW
    assert any(
        check["ruleId"] == "RECEIPT_REQUIRED_MISSING"
        for check in claim.policy_validation["checks"]
    )


def test_high_risk_claim_routes_to_fraud_investigation(
    claim_service, claim_payload, employee_actor
):
    """A near-identical same-day, same-vendor pair trips split-transaction plus threshold flags."""
    when = date.today() - timedelta(days=1)
    vendor = "Uber SF Airport"
    claim_service.submit_claim(
        claim_payload(category="Ground Transport", vendor=vendor, merchantVendor=vendor,
                      amount=Decimal("49.95"), amountUSD=Decimal("49.95"), expenseDate=when),
        actor=employee_actor,
    )
    second = claim_service.submit_claim(
        claim_payload(category="Ground Transport", merchantVendor=vendor,
                      amount=Decimal("48.00"), amountUSD=Decimal("48.00"), expenseDate=when),
        actor=employee_actor,
    )

    result = second.latest_fraud_result
    assert result.risk_score >= 40
    assert result.is_flagged is True
    assert second.status is ClaimStatus.FLAGGED_FRAUD


def test_manager_review_assigns_the_reporting_manager(
    claim_service, claim_payload, employee_actor, employee
):
    claim = claim_service.submit_claim(
        claim_payload(amount=Decimal("38.00"), amountUSD=Decimal("38.00")), actor=employee_actor
    )
    assert claim.assigned_reviewer_id == employee.manager_id
    assert claim.assigned_at is not None


def test_fraud_verdict_is_persisted_with_its_engine_version(
    claim_service, claim_payload, employee_actor, repositories
):
    claim = claim_service.submit_claim(claim_payload(), actor=employee_actor)
    stored = repositories["fraud"].latest_for_claim(claim.id)
    assert stored is not None
    assert stored.engine_version == "rules-1.0"
    assert stored.risk_level in set(FraudRiskLevel)


# --- validation on submission -------------------------------------------------


def test_duplicate_submission_is_blocked(claim_service, claim_payload, employee_actor):
    payload = claim_payload(merchantVendor="Sweetgreen SF")
    claim_service.submit_claim(payload, actor=employee_actor)

    with pytest.raises(DuplicateClaimError) as raised:
        claim_service.submit_claim(payload, actor=employee_actor)
    assert "existingClaimNumber" in raised.value.details


def test_duplicate_check_is_scoped_to_the_employee(
    claim_service, claim_payload, employee_actor, other_employee, db_session
):
    payload = claim_payload(merchantVendor="Shared Cafe")
    claim_service.submit_claim(payload, actor=employee_actor)

    from tests.conftest import make_actor

    other_actor = make_actor(
        "employee", employee_code=other_employee.employee_code, sub="sub-other"
    )
    # The same expense filed by a colleague is legitimate.
    assert claim_service.submit_claim(payload, actor=other_actor) is not None


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("-10")])
def test_non_positive_amount_rejected(claim_service, claim_payload, employee_actor, amount):
    with pytest.raises(ValidationError, match="greater than zero"):
        claim_service.submit_claim(
            claim_payload(amount=amount, amountUSD=amount), actor=employee_actor
        )


def test_future_expense_date_rejected(claim_service, claim_payload, employee_actor):
    with pytest.raises(ValidationError, match="future"):
        claim_service.submit_claim(
            claim_payload(expenseDate=date.today() + timedelta(days=10)), actor=employee_actor
        )


def test_missing_mandatory_field_rejected(claim_service, claim_payload, employee_actor):
    payload = claim_payload()
    payload["merchantVendor"] = "   "
    with pytest.raises(ValidationError, match="merchantVendor"):
        claim_service.submit_claim(payload, actor=employee_actor)


def test_invalid_currency_rejected(claim_service, claim_payload, employee_actor):
    with pytest.raises(ValidationError, match="ISO 4217"):
        claim_service.submit_claim(claim_payload(currency="DOLLARS"), actor=employee_actor)


def test_client_entertainment_requires_attendees(claim_service, claim_payload, employee_actor):
    with pytest.raises(ValidationError, match="attendees"):
        claim_service.submit_claim(
            claim_payload(category="Client Entertainment", amount=Decimal("300.00"),
                          amountUSD=Decimal("300.00"), attendees=None),
            actor=employee_actor,
        )


def test_stale_claim_is_accepted_but_flagged(claim_service, claim_payload, employee_actor):
    """A late claim must still be fileable — the policy engine flags it for director approval."""
    claim = claim_service.submit_claim(
        claim_payload(expenseDate=date.today() - timedelta(days=120)), actor=employee_actor
    )
    assert claim.status is ClaimStatus.MANAGER_REVIEW
    assert claim.policy_validation["requiresDirectorApprovalForAge"] is True


def test_failed_submission_leaves_no_partial_claim(
    claim_service, claim_payload, employee_actor, repositories, db_session
):
    """Validation failure must not leave an orphan Draft behind."""
    before = repositories["claims"].count()
    with pytest.raises(ValidationError):
        claim_service.submit_claim(
            claim_payload(amount=Decimal("-5"), amountUSD=Decimal("-5")), actor=employee_actor
        )
    db_session.rollback()
    assert repositories["claims"].count() == before


# --- receipts ----------------------------------------------------------------


@pytest.fixture
def receipt(db_session, employee):
    from app.models.receipt import ExtractionStatus, Receipt

    row = Receipt(
        file_name="receipt.png", extraction_status=ExtractionStatus.COMPLETED,
        employee_id=employee.employee_code, employee_ref_id=employee.id,
        vendor_name="Sweetgreen SF", total_amount=Decimal("22.50"), currency="USD",
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_attaching_a_receipt_links_it_and_creates_an_attachment(
    claim_service, claim_payload, employee_actor, receipt
):
    claim = claim_service.submit_claim(
        claim_payload(receiptId=str(receipt.id)), actor=employee_actor
    )
    assert claim.receipt_id == receipt.id
    assert any(a.receipt_id == receipt.id for a in claim.attachments)


def test_a_receipt_cannot_back_two_claims(claim_service, claim_payload, employee_actor, receipt):
    claim_service.submit_claim(claim_payload(receiptId=str(receipt.id)), actor=employee_actor)
    with pytest.raises(ReceiptAlreadyClaimedError):
        claim_service.submit_claim(
            claim_payload(receiptId=str(receipt.id), merchantVendor="Another Vendor"),
            actor=employee_actor,
        )


def test_unknown_receipt_is_rejected(claim_service, claim_payload, employee_actor):
    with pytest.raises(NotFoundError):
        claim_service.submit_claim(
            claim_payload(receiptId=str(uuid.uuid4())), actor=employee_actor
        )


def test_malformed_receipt_id_is_rejected(claim_service, claim_payload, employee_actor):
    with pytest.raises(ValidationError, match="receiptId"):
        claim_service.submit_claim(claim_payload(receiptId="not-a-uuid"), actor=employee_actor)


def test_cannot_attach_another_employees_receipt(
    claim_service, claim_payload, employee_actor, db_session, other_employee
):
    from app.models.receipt import ExtractionStatus, Receipt

    foreign = Receipt(
        file_name="theirs.png", extraction_status=ExtractionStatus.COMPLETED,
        employee_id=other_employee.employee_code, employee_ref_id=other_employee.id,
    )
    db_session.add(foreign)
    db_session.flush()

    with pytest.raises(ForbiddenError, match="another employee"):
        claim_service.submit_claim(
            claim_payload(receiptId=str(foreign.id)), actor=employee_actor
        )


# --- decisions ---------------------------------------------------------------


def test_manager_approval_escalates_to_finance_review(
    claim_service, make_claim, manager_actor
):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    updated = claim_service.execute_action(
        claim.claim_number, action="APPROVE", actor=manager_actor, notes="Looks correct"
    )
    assert updated.status is ClaimStatus.FINANCE_REVIEW

    workflow = updated.active_workflow
    if workflow is not None:
        manager_step = next(s for s in workflow.steps if s.required_role == "manager")
        assert manager_step.status is ApprovalStepStatus.APPROVED


def test_finance_approval_approves_the_claim(claim_service, make_claim, finance_actor):
    claim = make_claim(ClaimStatus.FINANCE_REVIEW)
    updated = claim_service.execute_action(
        claim.claim_number, action="APPROVE", actor=finance_actor
    )
    assert updated.status is ClaimStatus.APPROVED
    assert updated.approved_at is not None


def test_rejection_is_terminal(claim_service, make_claim, manager_actor):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    updated = claim_service.execute_action(
        claim.claim_number, action="REJECT", actor=manager_actor, notes="Not reimbursable"
    )
    assert updated.status is ClaimStatus.REJECTED
    assert updated.rejection_reason == "Not reimbursable"

    with pytest.raises(ImmutableEntityError, match="closed"):
        claim_service.execute_action(claim.claim_number, action="APPROVE", actor=manager_actor)


def test_finance_can_disburse_an_approved_claim(claim_service, make_claim, finance_actor):
    claim = make_claim(ClaimStatus.APPROVED)
    updated = claim_service.execute_action(
        claim.claim_number, action="DISBURSE", actor=finance_actor
    )
    assert updated.status is ClaimStatus.REIMBURSED
    assert updated.reimbursed_at is not None


def test_manager_cannot_disburse(claim_service, make_claim, manager_actor):
    claim = make_claim(ClaimStatus.APPROVED)
    with pytest.raises(ForbiddenError, match="finance or admin"):
        claim_service.execute_action(claim.claim_number, action="DISBURSE", actor=manager_actor)


def test_auto_approved_claim_can_be_disbursed_directly(claim_service, make_claim, finance_actor):
    claim = make_claim(ClaimStatus.AUTO_APPROVED)
    updated = claim_service.execute_action(
        claim.claim_number, action="DISBURSE", actor=finance_actor
    )
    assert updated.status is ClaimStatus.REIMBURSED


def test_reimbursed_claim_is_terminal(claim_service, make_claim, finance_actor):
    claim = make_claim(ClaimStatus.REIMBURSED)
    with pytest.raises(ImmutableEntityError, match="closed"):
        claim_service.execute_action(claim.claim_number, action="APPROVE", actor=finance_actor)


def test_cannot_disburse_a_claim_still_in_review(claim_service, make_claim, finance_actor):
    """The state machine, not the caller, decides when money can move."""
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    with pytest.raises(InvalidStateTransitionError) as raised:
        claim_service.execute_action(claim.claim_number, action="DISBURSE", actor=finance_actor)
    assert raised.value.details["currentStatus"] == "Manager_Review"
    assert "Disbursed" not in raised.value.details["allowedNextStatuses"]


def test_manual_fraud_flag_persists_a_verdict(
    claim_service, make_claim, finance_actor, repositories
):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    updated = claim_service.execute_action(
        claim.claim_number, action="FLAG_FRAUD", actor=finance_actor, notes="Vendor mismatch"
    )
    assert updated.status is ClaimStatus.FLAGGED_FRAUD

    verdict = repositories["fraud"].latest_for_claim(claim.id)
    assert verdict.is_flagged is True
    assert any(flag["code"] == "MANUAL_FRAUD_FLAG" for flag in verdict.flag_list)
    assert "FRAUD_FLAG" in _audit_actions(repositories, claim.claim_number)


def test_flagged_claim_can_be_closed_after_investigation(
    claim_service, make_claim, finance_actor
):
    claim = make_claim(ClaimStatus.FLAGGED_FRAUD)
    updated = claim_service.execute_action(
        claim.claim_number, action="REJECT", actor=finance_actor, notes="Confirmed abuse"
    )
    assert updated.status is ClaimStatus.REJECTED


def test_action_notes_become_a_comment(claim_service, make_claim, manager_actor):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    updated = claim_service.execute_action(
        claim.claim_number, action="APPROVE", actor=manager_actor, notes="Verified with vendor"
    )
    assert any(comment.body == "Verified with vendor" for comment in updated.comments)


def test_every_decision_is_audited_with_before_and_after(
    claim_service, make_claim, manager_actor, repositories
):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    claim_service.execute_action(claim.claim_number, action="APPROVE", actor=manager_actor)

    entries = repositories["audit"].list_for_entity("Claim", claim.claim_number)
    approval = next(e for e in entries if e.action == "APPROVAL_ACTION")
    assert approval.before == {"status": "Manager_Review"}
    assert approval.after["status"] == "Finance_Review"
    assert approval.actor_role == "manager"


def test_unsupported_action_rejected(claim_service, make_claim, manager_actor):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    with pytest.raises(ValidationError, match="Unsupported action"):
        claim_service.execute_action(claim.claim_number, action="DELETE", actor=manager_actor)


def test_action_on_unknown_claim_is_not_found(claim_service, manager_actor):
    with pytest.raises(NotFoundError):
        claim_service.execute_action(str(uuid.uuid4()), action="APPROVE", actor=manager_actor)


def test_stale_expected_version_is_rejected(claim_service, make_claim, manager_actor):
    """Two reviewers acting on one claim: the second must be told, not silently overwritten."""
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    with pytest.raises(ConcurrentUpdateError):
        claim_service.execute_action(
            claim.claim_number, action="APPROVE", actor=manager_actor,
            expected_version=claim.version - 1,
        )


def test_matching_expected_version_is_accepted(claim_service, make_claim, manager_actor):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    updated = claim_service.execute_action(
        claim.claim_number, action="APPROVE", actor=manager_actor,
        expected_version=claim.version,
    )
    assert updated.status is ClaimStatus.FINANCE_REVIEW


# --- reads and ownership -----------------------------------------------------


def test_employee_sees_only_their_own_claims(
    claim_service, make_claim, employee, other_employee, employee_actor
):
    mine = make_claim(owner=employee)
    theirs = make_claim(owner=other_employee)

    visible = {c.id for c in claim_service.list_claims(actor=employee_actor)}
    assert mine.id in visible
    assert theirs.id not in visible


def test_employee_filter_is_ignored_for_employees(
    claim_service, make_claim, other_employee, employee_actor
):
    """An employee cannot widen their own scope by passing someone else's id."""
    theirs = make_claim(owner=other_employee)
    visible = {
        c.id
        for c in claim_service.list_claims(
            actor=employee_actor, employee_code=other_employee.employee_code
        )
    }
    assert theirs.id not in visible


def test_manager_can_filter_by_employee(
    claim_service, make_claim, other_employee, manager_actor
):
    theirs = make_claim(owner=other_employee)
    visible = {
        c.id
        for c in claim_service.list_claims(
            actor=manager_actor, employee_code=other_employee.employee_code
        )
    }
    assert theirs.id in visible


def test_filtering_by_unknown_employee_returns_empty(claim_service, manager_actor):
    assert claim_service.list_claims(actor=manager_actor, employee_code="emp-nope") == []


def test_invalid_status_filter_is_a_validation_error(claim_service, manager_actor):
    with pytest.raises(ValidationError, match="ClaimStatus"):
        claim_service.list_claims(actor=manager_actor, status="Teleported")


def test_employee_gets_not_found_for_another_persons_claim(
    claim_service, make_claim, other_employee, employee_actor
):
    """404, not 403 — the endpoint must not confirm the claim exists."""
    theirs = make_claim(owner=other_employee)
    with pytest.raises(NotFoundError):
        claim_service.get_claim_for_actor(str(theirs.id), actor=employee_actor)


def test_manager_can_read_any_claim(claim_service, make_claim, other_employee, manager_actor):
    theirs = make_claim(owner=other_employee)
    assert claim_service.get_claim_for_actor(str(theirs.id), actor=manager_actor).id == theirs.id


# --- comments and draft editing ----------------------------------------------


def test_employee_can_comment_on_their_own_claim(claim_service, make_claim, employee_actor):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    updated = claim_service.add_comment(
        claim.claim_number, body="Attaching the itemised receipt.", actor=employee_actor
    )
    assert any(c.body == "Attaching the itemised receipt." for c in updated.comments)


def test_employees_cannot_create_internal_comments(claim_service, make_claim, employee_actor):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    with pytest.raises(ForbiddenError):
        claim_service.add_comment(
            claim.claim_number, body="secret", actor=employee_actor, is_internal=True
        )


def test_blank_comment_rejected(claim_service, make_claim, manager_actor):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    with pytest.raises(ValidationError):
        claim_service.add_comment(claim.claim_number, body="   ", actor=manager_actor)


def test_draft_can_be_edited_by_its_owner(claim_service, make_claim, employee_actor):
    claim = make_claim(ClaimStatus.DRAFT)
    updated = claim_service.update_draft(
        claim.claim_number,
        changes={"purposeDescription": "Corrected purpose", "amount": Decimal("31.00")},
        actor=employee_actor,
    )
    assert updated.purpose_description == "Corrected purpose"
    assert updated.amount == Decimal("31.00")


def test_submitted_claim_cannot_be_edited(claim_service, make_claim, employee_actor):
    claim = make_claim(ClaimStatus.SUBMITTED)
    with pytest.raises(ImmutableEntityError):
        claim_service.update_draft(
            claim.claim_number, changes={"purposeDescription": "too late"}, actor=employee_actor
        )


def test_approved_claim_cannot_be_edited(claim_service, make_claim, employee_actor):
    """The spec's "no modification after approval" rule."""
    claim = make_claim(ClaimStatus.APPROVED)
    with pytest.raises(ImmutableEntityError):
        claim_service.update_draft(
            claim.claim_number, changes={"amount": Decimal("999.00")}, actor=employee_actor
        )


def test_draft_edit_validates_the_new_values(claim_service, make_claim, employee_actor):
    claim = make_claim(ClaimStatus.DRAFT)
    with pytest.raises(ValidationError):
        claim_service.update_draft(
            claim.claim_number, changes={"amount": Decimal("-1")}, actor=employee_actor
        )


def test_empty_edit_rejected(claim_service, make_claim, employee_actor):
    claim = make_claim(ClaimStatus.DRAFT)
    with pytest.raises(ValidationError, match="No editable fields"):
        claim_service.update_draft(claim.claim_number, changes={}, actor=employee_actor)


def test_draft_edit_is_audited(claim_service, make_claim, employee_actor, repositories):
    claim = make_claim(ClaimStatus.DRAFT)
    claim_service.update_draft(
        claim.claim_number, changes={"purposeDescription": "new"}, actor=employee_actor
    )
    assert "CLAIM_UPDATE" in _audit_actions(repositories, claim.claim_number)


# --- reviewer assignment -----------------------------------------------------


def test_assign_reviewer(claim_service, make_claim, manager_actor, other_employee, repositories):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    updated = claim_service.assign_reviewer(
        claim.claim_number, reviewer_code=other_employee.employee_code, actor=manager_actor
    )
    assert updated.assigned_reviewer_id == other_employee.id
    assert "REVIEWER_ASSIGNED" in _audit_actions(repositories, claim.claim_number)


def test_assign_unknown_reviewer_is_not_found(claim_service, make_claim, manager_actor):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    with pytest.raises(NotFoundError):
        claim_service.assign_reviewer(
            claim.claim_number, reviewer_code="emp-nope", actor=manager_actor
        )


def test_cannot_assign_a_reviewer_to_a_closed_claim(claim_service, make_claim, manager_actor,
                                                    other_employee):
    claim = make_claim(ClaimStatus.REIMBURSED)
    with pytest.raises(ImmutableEntityError):
        claim_service.assign_reviewer(
            claim.claim_number, reviewer_code=other_employee.employee_code, actor=manager_actor
        )


# --- full happy path ---------------------------------------------------------


def test_complete_lifecycle_draft_to_reimbursed(
    claim_service, claim_payload, employee_actor, manager_actor, finance_actor, repositories
):
    """The canonical journey, asserting the recorded trail at the end."""
    claim = claim_service.submit_claim(
        claim_payload(amount=Decimal("38.00"), amountUSD=Decimal("38.00")), actor=employee_actor
    )
    assert claim.status is ClaimStatus.MANAGER_REVIEW

    claim = claim_service.execute_action(
        claim.claim_number, action="APPROVE", actor=manager_actor, notes="Manager OK"
    )
    assert claim.status is ClaimStatus.FINANCE_REVIEW

    claim = claim_service.execute_action(
        claim.claim_number, action="APPROVE", actor=finance_actor, notes="Finance OK"
    )
    assert claim.status is ClaimStatus.APPROVED

    claim = claim_service.execute_action(
        claim.claim_number, action="DISBURSE", actor=finance_actor, notes="Paid"
    )
    assert claim.status is ClaimStatus.REIMBURSED

    # Timestamps for each milestone.
    assert claim.submitted_at and claim.processing_started_at and claim.review_started_at
    assert claim.approved_at and claim.reimbursed_at

    # Gap-free history ending at the terminal state.
    sequences = [entry.sequence for entry in claim.status_history]
    assert sequences == list(range(1, len(sequences) + 1))
    assert claim.status_history[-1].to_status is ClaimStatus.REIMBURSED

    # Every business action left an audit record.
    actions = _audit_actions(repositories, claim.claim_number)
    assert actions.count("APPROVAL_ACTION") == 3
    assert "SUBMIT_CLAIM" in actions

    # The workflow closed out.
    workflow = claim.active_workflow
    assert workflow is not None
    assert all(not step.is_open for step in workflow.steps)
