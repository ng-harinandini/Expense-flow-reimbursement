"""The category policy engine, now fed from the ``policy_rules`` table.

Phase 1 changed the engine's *input* (a rules argument sourced from the database) but must not have
changed its *verdicts*. These cases pin the thresholds per category so a later refactor toward the
declarative ``conditions``/``actions`` payloads can be checked against known-good behaviour.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services.policy_engine import GENERAL_POLICY_CONSTANTS, evaluate_expense_policy


@pytest.fixture
def rules(policy_rule_service):
    """The live, effective ruleset in the shape the engine consumes."""
    return policy_rule_service.rules_for_engine()


def _claim(**overrides) -> dict:
    claim = {
        "category": "Meals",
        "amountUSD": 20.0,
        "employeeGrade": "L3",
        "receiptAttached": True,
        "expenseDate": (date.today() - timedelta(days=1)).isoformat(),
        "submissionDate": date.today().isoformat(),
    }
    claim.update(overrides)
    return claim


def _rule_ids(report: dict) -> set[str]:
    return {check["ruleId"] for check in report["checks"]}


# --- meals -------------------------------------------------------------------


def test_meal_within_auto_approve_passes_cleanly(rules):
    report = evaluate_expense_policy(_claim(amountUSD=20.0), rules)
    assert report["overallPassed"] is True
    assert report["requiresManualReview"] is False
    assert report["isWithinAutoApproveLimit"] is True
    assert report["maxLimitAllowed"] == 40.0
    assert report["autoApproveLimit"] == 25.0


def test_meal_above_auto_approve_needs_review(rules):
    report = evaluate_expense_policy(_claim(amountUSD=32.0), rules)
    assert report["overallPassed"] is True
    assert report["requiresManualReview"] is True
    assert "MEAL_REQUIRES_REVIEW" in _rule_ids(report)


def test_meal_over_cap_fails(rules):
    report = evaluate_expense_policy(_claim(amountUSD=55.0), rules)
    assert report["overallPassed"] is False
    assert "MEAL_MAX_EXCEEDED" in _rule_ids(report)


def test_alcohol_is_not_reimbursable_under_meals(rules):
    report = evaluate_expense_policy(
        _claim(amountUSD=20.0, extractedReceipt={"hasAlcohol": True}), rules
    )
    assert report["overallPassed"] is False
    assert "MEAL_ALCOHOL_PROHIBITED" in _rule_ids(report)


# --- ground transport --------------------------------------------------------


def test_ground_transport_thresholds(rules):
    within = evaluate_expense_policy(
        _claim(category="Ground Transport", amountUSD=45.0), rules
    )
    assert within["maxLimitAllowed"] == 150.0
    assert within["autoApproveLimit"] == 50.0
    assert within["requiresManualReview"] is False

    over_auto = evaluate_expense_policy(
        _claim(category="Ground Transport", amountUSD=90.0), rules
    )
    assert over_auto["requiresManualReview"] is True
    assert "TAXI_REVIEW_REQUIRED" in _rule_ids(over_auto)

    over_cap = evaluate_expense_policy(
        _claim(category="Ground Transport", amountUSD=200.0), rules
    )
    assert over_cap["overallPassed"] is False
    assert "TAXI_MAX_EXCEEDED" in _rule_ids(over_cap)


# --- flights -----------------------------------------------------------------


def test_flights_always_require_manual_review(rules):
    report = evaluate_expense_policy(_claim(category="Flights", amountUSD=520.0), rules)
    assert report["requiresManualReview"] is True
    assert report["autoApproveLimit"] == 0.0
    assert "FLIGHT_ALWAYS_MANUAL" in _rule_ids(report)


@pytest.mark.parametrize(
    "grade, expected_rule",
    [
        ("L2", "FLIGHT_CLASS_L1_L4"),
        ("L5", "FLIGHT_CLASS_L5"),
        ("Director", "FLIGHT_CLASS_L5"),
        ("VP", "FLIGHT_CLASS_VP"),
    ],
)
def test_flight_cabin_class_depends_on_grade(rules, grade, expected_rule):
    report = evaluate_expense_policy(
        _claim(category="Flights", amountUSD=800.0, employeeGrade=grade), rules
    )
    assert expected_rule in _rule_ids(report)


# --- lodging -----------------------------------------------------------------


@pytest.mark.parametrize("grade, cap", [("L2", 120.0), ("L3", 120.0), ("L4", 250.0), ("VP", 250.0)])
def test_lodging_nightly_cap_depends_on_grade(rules, grade, cap):
    report = evaluate_expense_policy(
        _claim(category="Lodging", amountUSD=cap - 1, employeeGrade=grade), rules
    )
    assert report["maxLimitAllowed"] == cap
    assert report["overallPassed"] is True
    assert report["requiresManualReview"] is True  # lodging is always reviewed


def test_lodging_over_the_grade_cap_fails(rules):
    report = evaluate_expense_policy(
        _claim(category="Lodging", amountUSD=200.0, employeeGrade="L3"), rules
    )
    assert report["overallPassed"] is False
    assert "LODGING_LIMIT_EXCEEDED" in _rule_ids(report)


# --- client entertainment ----------------------------------------------------


def test_client_entertainment_restricted_to_manager_grades(rules):
    junior = evaluate_expense_policy(
        _claim(category="Client Entertainment", amountUSD=200.0, employeeGrade="L3",
               attendees="Alice, Bob (ACME)"),
        rules,
    )
    assert junior["overallPassed"] is False
    assert "CLIENT_ENT_GRADE_RESTRICTION" in _rule_ids(junior)

    senior = evaluate_expense_policy(
        _claim(category="Client Entertainment", amountUSD=200.0, employeeGrade="L5",
               attendees="Alice, Bob (ACME)"),
        rules,
    )
    assert senior["overallPassed"] is True
    assert "CLIENT_ENT_GRADE_PASS" in _rule_ids(senior)


def test_client_entertainment_requires_attendees_and_respects_the_cap(rules):
    missing_attendees = evaluate_expense_policy(
        _claim(category="Client Entertainment", amountUSD=200.0, employeeGrade="L5",
               attendees=""),
        rules,
    )
    assert "CLIENT_ENT_MISSING_ATTENDEES" in _rule_ids(missing_attendees)

    over_cap = evaluate_expense_policy(
        _claim(category="Client Entertainment", amountUSD=900.0, employeeGrade="Director",
               attendees="Alice, Bob (ACME)"),
        rules,
    )
    assert over_cap["overallPassed"] is False
    assert "CLIENT_ENT_MAX_EXCEEDED" in _rule_ids(over_cap)


# --- cross-category rules ----------------------------------------------------


def test_missing_receipt_fails_when_one_is_required(rules):
    report = evaluate_expense_policy(
        _claim(category="Ground Transport", amountUSD=30.0, receiptAttached=False), rules
    )
    assert report["overallPassed"] is False
    assert "RECEIPT_REQUIRED_MISSING" in _rule_ids(report)


def test_claims_older_than_ninety_days_need_director_approval(rules):
    stale = (date.today() - timedelta(days=120)).isoformat()
    report = evaluate_expense_policy(_claim(expenseDate=stale), rules)

    assert report["overallPassed"] is False
    assert report["requiresDirectorApprovalForAge"] is True
    assert report["daysSinceExpense"] > GENERAL_POLICY_CONSTANTS["CLAIM_AGE_MAX_DAYS"]
    assert "AGE_LIMIT_90_DAYS" in _rule_ids(report)


def test_unknown_category_falls_back_to_a_review_route(rules):
    report = evaluate_expense_policy(
        _claim(category="Misc / Other", amountUSD=75.0), rules
    )
    assert report["requiresManualReview"] is True


def test_engine_tolerates_an_empty_ruleset():
    """The engine must still produce a verdict if no rules are configured yet."""
    report = evaluate_expense_policy(_claim(category="Relocation", amountUSD=100.0), [])
    assert "overallPassed" in report
    assert report["requiresManualReview"] is True


def test_engine_reads_thresholds_from_the_supplied_rules(policy_rule_service, admin_actor):
    """Changing the stored rule must change the verdict — proof the DB is the source of truth."""
    # No Relocation rule is seeded, so the uncategorised fallback cap ($300) applies and $250 is
    # within it.
    baseline = evaluate_expense_policy(
        _claim(category="Relocation", amountUSD=250.0),
        policy_rule_service.rules_for_engine(),
    )
    assert baseline["maxLimitAllowed"] == 300.0
    assert baseline["overallPassed"] is True

    # Publishing a stricter Relocation cap must flip the same claim to a violation.
    policy_rule_service.replace_ruleset(
        [
            {
                "category": "Relocation",
                "maxAmountUSD": 200.0,
                "autoApproveLimitUSD": 50.0,
                "receiptRequiredAboveUSD": 0.0,
            }
        ],
        actor=admin_actor,
    )
    after = evaluate_expense_policy(
        _claim(category="Relocation", amountUSD=250.0),
        policy_rule_service.rules_for_engine(),
    )
    assert after["maxLimitAllowed"] == 200.0
    assert after["overallPassed"] is False
    assert "CATEGORY_MAX_EXCEEDED" in _rule_ids(after)
