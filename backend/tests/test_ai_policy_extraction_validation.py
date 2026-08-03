"""Deterministic checks over extracted rules.

Two things are being asserted throughout, and the second matters more than any individual check:

* The checks catch what they claim to — contradictory thresholds, absent limits, missing evidence.
* **Nothing here ever raises.** A rule that fails every check still comes back as a rule with
  findings attached. An extraction that read eleven rules correctly and one badly must produce
  twelve reviewable rows with the bad one flagged, not an error that discards all twelve. Validation
  tells the reviewer where to look; it does not make the decision for them.
"""

from __future__ import annotations

from decimal import Decimal

from app.ai.core.enums import LimitBasis
from app.ai.policy_extraction.normalization import NormalizedRule
from app.ai.policy_extraction.rule_ids import generate_rule_id
from app.ai.policy_extraction.validation import (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    validate_rules,
)


def rule(**overrides) -> NormalizedRule:
    """A rule with nothing wrong with it, unless a test says otherwise."""
    defaults = {
        "category": "Meals",
        "grade_tier": "All Staff",
        "basis": LimitBasis.PER_DAY,
        "page_numbers": (1,),
        "source_quote": "All $40 / day",
        "confidence": Decimal("0.9"),
        "representable": True,
        "currency": "USD",
        "max_amount": Decimal("40"),
        "auto_approve_limit": Decimal("25"),
        "receipt_required_above": Decimal("25"),
    }
    defaults.update(overrides)
    defaults.setdefault(
        "rule_id",
        generate_rule_id(defaults["category"], defaults["grade_tier"], defaults["basis"]),
    )
    return NormalizedRule(**defaults)


def codes(rules) -> set[str]:
    return {
        warning.code
        for warnings in validate_rules(rules).values()
        for warning in warnings
    }


# --- the non-fatal guarantee -----------------------------------------------------


def test_a_clean_rule_produces_no_findings() -> None:
    assert validate_rules([rule()]) == {}


def test_a_thoroughly_broken_rule_still_returns_rather_than_raising() -> None:
    """The whole design in one test: a bad rule must never stop the proposal being written."""
    broken = rule(
        category="",
        currency="DOLLARS",
        max_amount=None,
        auto_approve_limit=None,
        receipt_required_above=None,
        limit_expression=None,
        confidence=Decimal("0"),
        page_numbers=(),
        source_quote="",
        representable=False,
        unrepresentable_reason="No matching category.",
    )
    findings = validate_rules([broken])
    assert findings, "a broken rule must produce findings, not an exception"


def test_findings_are_keyed_by_rule_so_they_can_be_attached_to_their_own_row() -> None:
    clean = rule()
    bad = rule(category="Lodging", basis=LimitBasis.PER_NIGHT, max_amount=None,
               auto_approve_limit=None, limit_expression=None)
    findings = validate_rules([clean, bad])
    assert clean.rule_id not in findings
    assert bad.rule_id in findings


# --- limits ----------------------------------------------------------------------


def test_a_rule_that_constrains_nothing_is_an_error() -> None:
    found = codes([rule(max_amount=None, limit_expression=None, always_manual=False,
                        auto_approve_limit=None)])
    assert "MISSING_LIMIT" in found


def test_a_formula_limit_with_no_amount_is_not_flagged_as_missing() -> None:
    """Mileage and per-agreement limits are real limits with no number. Demanding one would push
    the extractor toward inventing a figure to satisfy a check."""
    found = codes([
        rule(basis=LimitBasis.FORMULA, max_amount=None,
             limit_expression="Published mileage rate x miles")
    ])
    assert "MISSING_LIMIT" not in found


def test_always_manual_with_no_amount_is_not_flagged_as_missing() -> None:
    found = codes([rule(max_amount=None, limit_expression=None, always_manual=True,
                        auto_approve_limit=None)])
    assert "MISSING_LIMIT" not in found


def test_a_fixed_amount_alongside_a_non_scalar_basis_is_flagged() -> None:
    found = codes([rule(basis=LimitBasis.AGREEMENT, max_amount=Decimal("40"))])
    assert "AMOUNT_WITH_NON_SCALAR_BASIS" in found


def test_a_zero_cap_is_flagged_because_it_permits_nothing() -> None:
    found = codes([rule(max_amount=Decimal("0"), auto_approve_limit=None,
                        receipt_required_above=None)])
    assert "ZERO_LIMIT" in found


# --- contradictory thresholds ----------------------------------------------------


def test_an_auto_approve_threshold_above_the_cap_is_an_error() -> None:
    """Every claim within policy would approve automatically — the rule would review nothing."""
    findings = validate_rules([rule(max_amount=Decimal("40"), auto_approve_limit=Decimal("100"))])
    warning = next(
        w for ws in findings.values() for w in ws if w.code == "AUTO_APPROVE_ABOVE_LIMIT"
    )
    assert warning.severity == SEVERITY_ERROR


def test_always_manual_together_with_a_threshold_is_an_error() -> None:
    found = codes([rule(always_manual=True, auto_approve_limit=Decimal("25"))])
    assert "MANUAL_WITH_AUTO_APPROVE" in found


def test_a_receipt_threshold_above_the_cap_is_flagged() -> None:
    found = codes([rule(max_amount=Decimal("40"), receipt_required_above=Decimal("100"))])
    assert "RECEIPT_THRESHOLD_ABOVE_LIMIT" in found


def test_no_stated_approval_route_at_all_is_flagged() -> None:
    found = codes([rule(auto_approve_limit=None, always_manual=False)])
    assert "MISSING_APPROVAL_RULE" in found


def test_a_receipt_threshold_of_zero_is_accepted_as_a_real_value() -> None:
    """Zero means a receipt is always required. Treating it as absent would relax the rule."""
    found = codes([rule(receipt_required_above=Decimal("0"))])
    assert "NEGATIVE_RECEIPT_THRESHOLD" not in found


# --- currency, confidence, evidence ----------------------------------------------


def test_a_non_iso_currency_is_an_error() -> None:
    found = codes([rule(currency="DOLLARS")])
    assert "INVALID_CURRENCY" in found


def test_low_confidence_is_a_warning_not_an_error() -> None:
    findings = validate_rules([rule(confidence=Decimal("0.3"))])
    warning = next(w for ws in findings.values() for w in ws if w.code == "LOW_CONFIDENCE")
    assert warning.severity == SEVERITY_WARNING


def test_missing_evidence_is_reported_even_though_nothing_downstream_requires_it() -> None:
    """A rule with no page and no quote can only be checked by re-reading the whole document,
    which defeats the purpose of extracting it."""
    found = codes([rule(page_numbers=(), source_quote="")])
    assert {"MISSING_PAGE_CITATION", "MISSING_SOURCE_QUOTE"} <= found


def test_an_unrepresentable_rule_is_explained_not_treated_as_a_defect() -> None:
    """Reporting a rule outside the configured category set is expected behaviour. The finding
    tells the reviewer why it will not publish, without them having to open the item."""
    findings = validate_rules([
        rule(category="Relocation", representable=False,
             unrepresentable_reason="No matching expense category.")
    ])
    warning = next(
        w for ws in findings.values() for w in ws if w.code == "UNREPRESENTABLE_CATEGORY"
    )
    assert warning.severity == SEVERITY_WARNING
    assert "No matching expense category." in warning.message


def test_findings_serialize_for_the_item_column() -> None:
    findings = validate_rules([rule(currency="DOLLARS")])
    payload = next(iter(findings.values()))[0].to_dict()
    assert set(payload) == {"ruleId", "code", "message", "severity"}
