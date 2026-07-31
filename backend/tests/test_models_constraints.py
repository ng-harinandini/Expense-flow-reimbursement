"""Database-level invariants.

These assert that the *database* rejects bad data, not just the application. That distinction is
the point of Phase 1: a future service, a data-fix script, or a psql session cannot corrupt the
domain, because the constraints and guard triggers hold regardless of who is writing.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.models.claim import Claim, Comment
from app.models.enums import ClaimStatus, EmployeeGrade, FraudRiskLevel
from app.models.fraud import FraudResult
from app.models.organization import Employee
from app.models.policy import PolicyRule


def _claim_kwargs(employee: Employee, **overrides) -> dict:
    kwargs = dict(
        claim_number=f"EXP-TEST-{uuid.uuid4().hex[:10]}",
        employee_id=employee.id,
        employee_grade=employee.grade,
        expense_date=date.today() - timedelta(days=1),
        category="Meals",
        amount=Decimal("10.00"),
        currency="USD",
        amount_usd=Decimal("10.00"),
        merchant_vendor="Vendor",
        status=ClaimStatus.DRAFT,
    )
    kwargs.update(overrides)
    return kwargs


# --- claims check constraints -------------------------------------------------


@pytest.mark.parametrize(
    "overrides, constraint",
    [
        ({"amount": Decimal("0")}, "ck_claims_amount_positive"),
        ({"amount": Decimal("-5")}, "ck_claims_amount_positive"),
        ({"amount_usd": Decimal("0")}, "ck_claims_amount_usd_positive"),
        ({"currency": "US"}, "ck_claims_currency_iso4217"),
        ({"fx_rate": Decimal("0")}, "ck_claims_fx_rate_positive"),
    ],
)
def test_claims_reject_invalid_values(db_session, employee, overrides, constraint):
    db_session.add(Claim(**_claim_kwargs(employee, **overrides)))
    with pytest.raises(IntegrityError) as raised:
        db_session.flush()
    assert constraint in str(raised.value.orig)
    db_session.rollback()


def test_draft_claim_cannot_have_a_submission_timestamp(db_session, employee):
    """The status/timestamp pair must stay consistent: Draft means not yet submitted."""
    from datetime import datetime, timezone

    db_session.add(
        Claim(**_claim_kwargs(employee, submitted_at=datetime.now(timezone.utc)))
    )
    with pytest.raises(IntegrityError) as raised:
        db_session.flush()
    assert "ck_claims_submitted_at_matches_status" in str(raised.value.orig)
    db_session.rollback()


def test_submitted_claim_requires_a_submission_timestamp(db_session, employee):
    db_session.add(
        Claim(**_claim_kwargs(employee, status=ClaimStatus.SUBMITTED, submitted_at=None))
    )
    with pytest.raises(IntegrityError) as raised:
        db_session.flush()
    assert "ck_claims_submitted_at_matches_status" in str(raised.value.orig)
    db_session.rollback()


def test_claim_number_is_unique(db_session, employee):
    number = f"EXP-TEST-{uuid.uuid4().hex[:10]}"
    db_session.add(Claim(**_claim_kwargs(employee, claim_number=number)))
    db_session.flush()
    db_session.add(Claim(**_claim_kwargs(employee, claim_number=number)))
    with pytest.raises(IntegrityError) as raised:
        db_session.flush()
    assert "uq_claims_claim_number" in str(raised.value.orig)
    db_session.rollback()


def test_one_claim_per_receipt(db_session, employee):
    """The database half of "a receipt can only back one claim"."""
    from app.models.receipt import ExtractionStatus, Receipt

    receipt = Receipt(
        file_name="r.png", extraction_status=ExtractionStatus.COMPLETED,
        employee_id=employee.employee_code, employee_ref_id=employee.id,
    )
    db_session.add(receipt)
    db_session.flush()

    db_session.add(Claim(**_claim_kwargs(employee, receipt_id=receipt.id)))
    db_session.flush()
    db_session.add(Claim(**_claim_kwargs(employee, receipt_id=receipt.id)))
    with pytest.raises(IntegrityError) as raised:
        db_session.flush()
    assert "uq_claims_receipt_id" in str(raised.value.orig)
    db_session.rollback()


def test_claim_requires_a_real_employee(db_session, employee):
    db_session.add(Claim(**_claim_kwargs(employee, employee_id=uuid.uuid4())))
    with pytest.raises(IntegrityError) as raised:
        db_session.flush()
    assert "fk_claims_employee_id" in str(raised.value.orig)
    db_session.rollback()


def test_employee_cannot_be_deleted_while_claims_reference_it(db_session, employee):
    """``ON DELETE RESTRICT``: financial history must not lose its owner."""
    db_session.add(Claim(**_claim_kwargs(employee)))
    db_session.flush()

    db_session.delete(employee)
    with pytest.raises(IntegrityError) as raised:
        db_session.flush()
    assert "fk_claims_employee_id" in str(raised.value.orig)
    db_session.rollback()


# --- transition guard trigger ------------------------------------------------


def test_trigger_blocks_illegal_transition_via_raw_sql(db_session, employee):
    """Bypassing the repository does not bypass the state machine."""
    claim = Claim(**_claim_kwargs(employee))
    db_session.add(claim)
    db_session.flush()

    with pytest.raises(DBAPIError) as raised:
        db_session.execute(
            text("UPDATE claims SET status = 'Disbursed' WHERE id = :id"), {"id": claim.id}
        )
    assert "illegal claim status transition" in str(raised.value.orig)
    db_session.rollback()


def test_trigger_allows_legal_transition_via_raw_sql(db_session, employee):
    claim = Claim(**_claim_kwargs(employee))
    db_session.add(claim)
    db_session.flush()

    db_session.execute(
        text("UPDATE claims SET status = 'Submitted', submitted_at = now() WHERE id = :id"),
        {"id": claim.id},
    )
    db_session.expire(claim)
    assert claim.status is ClaimStatus.SUBMITTED


def test_trigger_permits_updates_that_do_not_change_status(db_session, employee):
    claim = Claim(**_claim_kwargs(employee))
    db_session.add(claim)
    db_session.flush()

    db_session.execute(
        text("UPDATE claims SET purpose_description = 'edited' WHERE id = :id"), {"id": claim.id}
    )
    db_session.expire(claim)
    assert claim.purpose_description == "edited"


# --- audit immutability -------------------------------------------------------


def test_audit_rows_cannot_be_updated(db_session, audit_service, employee_actor):
    from app.models.enums import AuditAction, AuditEntity

    entry = audit_service.record(
        actor=employee_actor, action=AuditAction.SUBMIT_CLAIM,
        entity_type=AuditEntity.CLAIM, entity_id="EXP-TEST", details="original",
    )
    with pytest.raises(DBAPIError) as raised:
        db_session.execute(
            text("UPDATE audit_logs SET details = 'tampered' WHERE id = :id"), {"id": entry.id}
        )
    assert "append-only" in str(raised.value.orig)
    db_session.rollback()


def test_audit_rows_cannot_be_deleted(db_session, audit_service, employee_actor):
    from app.models.enums import AuditAction, AuditEntity

    entry = audit_service.record(
        actor=employee_actor, action=AuditAction.SUBMIT_CLAIM,
        entity_type=AuditEntity.CLAIM, entity_id="EXP-TEST", details="keep",
    )
    with pytest.raises(DBAPIError) as raised:
        db_session.execute(text("DELETE FROM audit_logs WHERE id = :id"), {"id": entry.id})
    assert "append-only" in str(raised.value.orig)
    db_session.rollback()


# --- optimistic locking -------------------------------------------------------


def test_stale_write_is_rejected_by_the_version_check(db_session, employee):
    """Losing an optimistic-lock race must raise, not silently overwrite the winner.

    The competing writer is simulated with raw SQL that bumps ``version`` — exactly what another
    request's UPDATE would do — after which the in-session change is stale and its
    ``WHERE version = <loaded>`` clause matches no row.
    """
    claim = Claim(**_claim_kwargs(employee))
    db_session.add(claim)
    db_session.flush()
    loaded_version = claim.version

    db_session.execute(
        text("UPDATE claims SET version = version + 1, decision_notes = 'winner' WHERE id = :id"),
        {"id": claim.id},
    )

    claim.purpose_description = "loser"
    with pytest.raises(StaleDataError):
        db_session.flush()
    db_session.rollback()
    assert loaded_version >= 1


def test_version_increments_on_each_update(db_session, employee):
    claim = Claim(**_claim_kwargs(employee))
    db_session.add(claim)
    db_session.flush()
    first = claim.version

    claim.purpose_description = "one"
    db_session.flush()
    claim.purpose_description = "two"
    db_session.flush()
    assert claim.version == first + 2


# --- children -----------------------------------------------------------------


def test_comment_body_cannot_be_blank(db_session, employee, make_claim):
    claim = make_claim()
    db_session.add(
        Comment(claim_id=claim.id, author_name="A", author_role="employee", body="   ")
    )
    with pytest.raises(IntegrityError) as raised:
        db_session.flush()
    assert "ck_comments_body_not_blank" in str(raised.value.orig)
    db_session.rollback()


def test_claim_children_cascade_on_delete(db_session, employee, make_claim):
    """Deleting a claim removes its owned rows (used only by data-retention tooling)."""
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    claim_id = claim.id
    db_session.add(
        Comment(claim_id=claim_id, author_name="A", author_role="manager", body="note")
    )
    db_session.flush()

    db_session.delete(claim)
    db_session.flush()

    for table in ("comments", "claim_status_history", "fraud_results", "approval_workflows"):
        remaining = db_session.execute(
            text(f"SELECT count(*) FROM {table} WHERE claim_id = :id"), {"id": claim_id}
        ).scalar_one()
        assert remaining == 0, f"{table} rows survived the claim delete"


def test_fraud_risk_score_bounded(db_session, make_claim):
    claim = make_claim()
    db_session.add(
        FraudResult(
            claim_id=claim.id, risk_score=101, risk_level=FraudRiskLevel.LOW,
            recommended_action="AUTO_APPROVE",
        )
    )
    with pytest.raises(IntegrityError) as raised:
        db_session.flush()
    assert "ck_fraud_results_risk_score_range" in str(raised.value.orig)
    db_session.rollback()


def test_history_sequence_unique_per_claim(db_session, make_claim):
    from app.models.claim import ClaimStatusHistory

    claim = make_claim()
    db_session.add(
        ClaimStatusHistory(
            claim_id=claim.id, sequence=1, to_status=ClaimStatus.DRAFT,
            step_name="dup", action="duplicate sequence",
        )
    )
    with pytest.raises(IntegrityError) as raised:
        db_session.flush()
    assert "uq_claim_status_history_claim_sequence" in str(raised.value.orig)
    db_session.rollback()


# --- reference data -----------------------------------------------------------


def test_role_name_unique(db_session):
    from app.models.role import Role

    db_session.add(Role(name="employee", description="Duplicate"))
    with pytest.raises(IntegrityError) as raised:
        db_session.flush()
    assert "uq_roles_name" in str(raised.value.orig)
    db_session.rollback()


def test_employee_code_and_email_unique(db_session, employee, role_id):
    # Nested savepoints: the fixture-created ``employee`` was only flushed (not committed), so a
    # plain ``rollback()`` here would discard it too — scope each failed insert to its own
    # savepoint instead, matching ``employee``'s lifetime to the outer transaction.
    with pytest.raises(IntegrityError) as raised:
        with db_session.begin_nested():
            db_session.add(
                Employee(
                    employee_code=employee.employee_code, full_name="Impostor",
                    email="new@enterprise.com", grade=EmployeeGrade.L1, role_id=role_id,
                )
            )
            db_session.flush()
    assert "uq_employees_employee_code" in str(raised.value.orig)

    with pytest.raises(IntegrityError) as raised:
        with db_session.begin_nested():
            db_session.add(
                Employee(
                    employee_code="emp-999", full_name="Impostor",
                    email=employee.email, grade=EmployeeGrade.L1, role_id=role_id,
                )
            )
            db_session.flush()
    assert "uq_employees_email" in str(raised.value.orig)


def test_policy_rule_expiry_cannot_precede_effective_date(db_session):
    db_session.add(
        PolicyRule(
            code="BAD_WINDOW", version=1, name="Bad", category="Meals",
            effective_date=date(2026, 6, 1), expiration_date=date(2026, 1, 1),
        )
    )
    with pytest.raises(IntegrityError) as raised:
        db_session.flush()
    assert "ck_policy_rules_effective_window" in str(raised.value.orig)
    db_session.rollback()


def test_policy_rule_code_version_unique(db_session):
    db_session.add(
        PolicyRule(
            code="MEALS_STANDARD", version=1, name="Clash", category="Meals",
            effective_date=date(2026, 1, 1),
        )
    )
    with pytest.raises(IntegrityError) as raised:
        db_session.flush()
    assert "uq_policy_rules_code_version" in str(raised.value.orig)
    db_session.rollback()


def test_negative_policy_limit_rejected(db_session):
    db_session.add(
        PolicyRule(
            code="NEGATIVE_LIMIT", version=1, name="Negative", category="Meals",
            effective_date=date(2026, 1, 1), expense_limit=Decimal("-1.00"),
        )
    )
    with pytest.raises(IntegrityError) as raised:
        db_session.flush()
    assert "ck_policy_rules_expense_limit_non_negative" in str(raised.value.orig)
    db_session.rollback()
