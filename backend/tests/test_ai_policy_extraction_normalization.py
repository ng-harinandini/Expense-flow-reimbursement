"""Normalization and schema tests for policy-rule extraction.

Pure functions only — no database, no model, no credentials. The values exercised here are the real
strings a policy PDF produces, not invented ones: mixed dash characters, ``"$2,000 / year"``,
``"— (always manual)"``, formula and per-agreement limits, and a compound per-person-per-event cap.

The two heaviest tests are :func:`test_grade_bands_merge_into_one_publishable_rule` and
:func:`test_payload_carries_untouched_rules_so_nothing_is_retired`. Both guard silent data loss in
``policy_rules`` — one from code collision, one from ``_retire_absent`` — and neither failure raises
at the time it happens.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.ai.core.enums import LimitBasis
from app.ai.policy_extraction.normalization import (
    ALL_STAFF,
    GRADE_DEPENDENT,
    build_ruleset_payload,
    clamp_confidence,
    normalize_category,
    normalize_dashes,
    normalize_extraction,
    normalize_grade_tier,
    normalize_rule,
    parse_amount,
)
from app.ai.policy_extraction.schema import POLICY_RULE_EXTRACTION_SCHEMA
from app.ai.reasoning.structured_output import validate

KNOWN = ("Meals", "Ground Transport", "Flights", "Lodging", "Client Entertainment")

#: The five seeded rules as ``policy_rule_to_dict`` returns them.
CURRENT_ACTIVE: list[dict[str, Any]] = [
    {"category": "Meals", "code": "MEALS_STANDARD", "maxAmountUSD": 40.0, "priority": 100},
    {
        "category": "Ground Transport",
        "code": "GROUND_TRANSPORT_STANDARD",
        "maxAmountUSD": 150.0,
        "priority": 100,
    },
    {"category": "Flights", "code": "FLIGHTS_STANDARD", "maxAmountUSD": 10000.0, "priority": 90},
    {"category": "Lodging", "code": "LODGING_STANDARD", "maxAmountUSD": 250.0, "priority": 95},
    {
        "category": "Client Entertainment",
        "code": "CLIENT_ENTERTAINMENT_STANDARD",
        "maxAmountUSD": 500.0,
        "priority": 90,
    },
]


def _raw(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "category": "Meals",
        "gradeTier": "All",
        "basis": "PER_DAY",
        "pageNumbers": [1],
        "sourceQuote": "All $40 / day",
        "confidence": 0.9,
        "representable": True,
    }
    base.update(overrides)
    return base


# --- money --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("$40 / day", Decimal("40.00")),
        ("$2,000 / year", Decimal("2000.00")),           # thousands separator
        ("$120 / night", Decimal("120.00")),
        ("$75 / person / event, max 4 events / year", Decimal("75.00")),  # compound
        ("$0", Decimal("0.00")),                          # a real threshold, not "absent"
        (40, Decimal("40.00")),
        (40.5, Decimal("40.50")),
        ("Current published mileage rate x miles", None),  # formula, no number
        ("Per signed relocation agreement", None),
        ("", None),
        (None, None),
        ("-5", None),                                     # negative violates a check constraint
        (True, None),                                     # bool is an int subclass; must not parse
    ],
)
def test_parse_amount(value: Any, expected: Any) -> None:
    assert parse_amount(value) == expected


def test_amounts_are_quantized_to_the_column_scale() -> None:
    """``policy_rules`` money columns are NUMERIC(14,2); rounding here beats a silent DB
    truncation."""
    assert parse_amount("10.999") == Decimal("11.00")


# --- dashes and grade tiers ---------------------------------------------------


def test_en_dash_and_hyphen_normalize_to_the_same_string() -> None:
    """A single document uses an en dash in 'L1-L3' and a hyphen in 'L5-Director'."""
    assert normalize_dashes("L1–L3") == "L1-L3"
    assert normalize_grade_tier("L1–L3") == normalize_grade_tier("L1-L3") == "L1-L3"


@pytest.mark.parametrize(
    "phrasing", ["All", "all", "  All  ", "Every employee", "all employees", "Any level"]
)
def test_every_everyone_phrasing_maps_to_one_label(phrasing: str) -> None:
    assert normalize_grade_tier(phrasing) == ALL_STAFF


def test_other_tiers_keep_the_documents_wording() -> None:
    """``grade_tier`` is free text the engine never reads, so fidelity beats a fixed vocabulary."""
    assert normalize_grade_tier("Manager+") == "Manager+"
    assert normalize_grade_tier("VP+") == "VP+"


def test_missing_grade_tier_defaults_to_all_staff() -> None:
    assert normalize_grade_tier(None) == ALL_STAFF


# --- categories ---------------------------------------------------------------


@pytest.mark.parametrize(
    "spelling", ["Meals", "meals", "MEALS", "  Meals  ", "Client entertainment"]
)
def test_category_matching_ignores_case_and_spacing(spelling: str) -> None:
    category, reason = normalize_category(spelling, KNOWN)
    assert reason is None
    assert category in KNOWN


@pytest.mark.parametrize(
    "heading",
    [
        "Communications & connectivity",
        "Training & professional development",
        "Software & subscriptions",
        "Team events",
        "Relocation",
        "Health & wellness",
    ],
)
def test_sections_outside_the_closed_set_are_flagged_not_invented(heading: str) -> None:
    """7 of this document's 12 sections have no category row.

    Inventing one would break the ``expense_categories`` / ``policy_rules`` lockstep invariant, and
    dropping them would hide real policy from the reviewer. So they are returned and flagged.
    """
    category, reason = normalize_category(heading, KNOWN)
    assert category  # preserved, not dropped
    assert reason is not None
    assert "not one of the configured expense categories" in reason


# --- confidence ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.95, Decimal("0.95")), (1.5, Decimal("1.00")), (-1, Decimal("0.00")),
     (None, Decimal("0.00")), ("nonsense", Decimal("0.00"))],
)
def test_confidence_is_clamped_and_unknown_reads_as_least_trustworthy(
    value: Any, expected: Decimal
) -> None:
    assert clamp_confidence(value) == expected


# --- sentinels and non-scalar limits -----------------------------------------


def test_always_manual_never_becomes_a_zero_threshold() -> None:
    """'— (always manual)' means *never* auto-approve; 0 would be a real, permissive threshold."""
    rule = normalize_rule(
        _raw(category="Lodging", alwaysManual=True, autoApproveLimitUSD=0), known_categories=KNOWN
    )
    assert rule.always_manual is True
    assert rule.auto_approve_limit is None


def test_a_zero_receipt_threshold_is_kept_because_it_is_a_real_rule() -> None:
    """Receipt-required-above 0 means a receipt is always required."""
    rule = normalize_rule(_raw(receiptRequiredAboveUSD=0), known_categories=KNOWN)
    assert rule.receipt_required_above == Decimal("0.00")


@pytest.mark.parametrize("basis", ["FORMULA", "AGREEMENT"])
def test_non_scalar_limits_carry_prose_and_no_amount(basis: str) -> None:
    """A formula published as a flat cap would be a different, wrong rule."""
    rule = normalize_rule(
        _raw(basis=basis, maxAmountUSD=99, limitExpression="Per signed agreement"),
        known_categories=KNOWN,
    )
    assert rule.max_amount is None
    assert rule.limit_expression == "Per signed agreement"


def test_basis_distinguishes_identical_amounts() -> None:
    """'$40 per day' and '$40 per claim' are different rules with the same number."""
    per_day = normalize_rule(_raw(maxAmountUSD=40, basis="PER_DAY"), known_categories=KNOWN)
    per_claim = normalize_rule(_raw(maxAmountUSD=40, basis="PER_CLAIM"), known_categories=KNOWN)
    assert per_day.max_amount == per_claim.max_amount
    assert per_day.basis is LimitBasis.PER_DAY
    assert per_claim.basis is LimitBasis.PER_CLAIM


def test_unknown_basis_degrades_to_other_rather_than_raising() -> None:
    assert normalize_rule(_raw(basis="per_fortnight"), known_categories=KNOWN).basis is (
        LimitBasis.OTHER
    )


def test_page_numbers_are_deduplicated_sorted_and_positive() -> None:
    rule = normalize_rule(_raw(pageNumbers=[3, 2, 2, 0, -1, "x", True]), known_categories=KNOWN)
    assert rule.page_numbers == (2, 3)


# --- publishable payload ------------------------------------------------------


def test_grade_bands_merge_into_one_publishable_rule() -> None:
    """``replace_ruleset`` derives one code per category and each publish retires the last.

    Submitting the document's two lodging bands as two rules would publish $120, then immediately
    retire it by publishing $250 — the lower band gone with no error raised anywhere.
    """
    rules = normalize_extraction(
        {
            "rules": [
                _raw(category="Lodging", gradeTier="L1–L3", maxAmountUSD=120,
                     basis="PER_NIGHT", alwaysManual=True),
                _raw(category="Lodging", gradeTier="L4+", maxAmountUSD=250,
                     basis="PER_NIGHT", alwaysManual=True),
            ]
        },
        known_categories=KNOWN,
    )
    assert len(rules) == 2, "both bands are kept as proposal rows, faithful to the document"

    payload = build_ruleset_payload(rules, current_active=CURRENT_ACTIVE)
    lodging = [r for r in payload if r["category"] == "Lodging"]

    assert len(lodging) == 1, "exactly one publishable row per category"
    merged = lodging[0]
    assert merged["gradeTier"] == GRADE_DEPENDENT
    # The ceiling is the highest band: publishing the lowest would reject a senior employee's
    # legitimate claim, while the per-band detail needed to be stricter is preserved below.
    assert merged["maxAmountUSD"] == 250.0
    assert merged["actions"]["capUsdByGradeTier"] == {"L1-L3": 120.0, "L4+": 250.0}
    assert merged["actions"]["autoApprove"] is False


def test_payload_carries_untouched_rules_so_nothing_is_retired() -> None:
    """``_retire_absent`` deactivates every active rule absent from the payload.

    A payload holding only the categories one document mentioned would silently retire the rest,
    dropping them into the policy engine's generic fallback.
    """
    rules = normalize_extraction(
        {"rules": [_raw(category="Meals", maxAmountUSD=45)]}, known_categories=KNOWN
    )
    payload = build_ruleset_payload(rules, current_active=CURRENT_ACTIVE)

    submitted = {r["category"] for r in payload}
    assert submitted == {r["category"] for r in CURRENT_ACTIVE}
    # Every existing code survives, so replace_ruleset supersedes rather than retires.
    assert {r.get("code") for r in payload} == {r["code"] for r in CURRENT_ACTIVE}
    # ...and the extracted change did land.
    assert [r for r in payload if r["category"] == "Meals"][0]["maxAmountUSD"] == 45.0


def test_unrepresentable_rules_are_never_published() -> None:
    """They have no category the engine matches, so publishing creates a rule that never fires."""
    rules = normalize_extraction(
        {
            "rules": [
                _raw(category="Relocation", representable=False,
                     unrepresentableReason="No matching expense category.", basis="AGREEMENT"),
            ]
        },
        known_categories=KNOWN,
    )
    assert rules[0].representable is False

    payload = build_ruleset_payload(rules, current_active=CURRENT_ACTIVE)
    assert "Relocation" not in {r["category"] for r in payload}
    assert len(payload) == len(CURRENT_ACTIVE)


def test_a_new_representable_category_is_added_not_merged() -> None:
    rules = normalize_extraction(
        {"rules": [_raw(category="Ground Transport", maxAmountUSD=150, basis="PER_TRIP")]},
        known_categories=KNOWN,
    )
    payload = build_ruleset_payload(rules, current_active=[])
    assert [r["category"] for r in payload] == ["Ground Transport"]


def test_strictest_thresholds_win_across_a_merged_group() -> None:
    """A band reviewed at $50 must not be widened by a sibling with a higher threshold."""
    rules = normalize_extraction(
        {
            "rules": [
                _raw(category="Meals", gradeTier="L1-L3", maxAmountUSD=40,
                     autoApproveLimitUSD=25, receiptRequiredAboveUSD=25),
                _raw(category="Meals", gradeTier="L4+", maxAmountUSD=60,
                     autoApproveLimitUSD=50, receiptRequiredAboveUSD=50),
            ]
        },
        known_categories=KNOWN,
    )
    merged = build_ruleset_payload(rules, current_active=[])[0]
    assert merged["maxAmountUSD"] == 60.0            # ceiling: highest
    assert merged["autoApproveLimitUSD"] == 25.0     # threshold: strictest
    assert merged["receiptRequiredAboveUSD"] == 25.0


# --- schema -------------------------------------------------------------------


def test_a_well_formed_extraction_validates() -> None:
    payload = {
        "rules": [_raw(maxAmountUSD=40.0, autoApproveLimitUSD=25.0)],
        "reviewNotes": None,
        "documentEffectiveDate": None,
    }
    ok, errors = validate(payload, POLICY_RULE_EXTRACTION_SCHEMA)
    assert ok, errors


def test_schema_accepts_explicit_nulls_for_absent_amounts() -> None:
    """A model emits ``null`` for an absent amount at least as often as it omits the key."""
    payload = {"rules": [_raw(maxAmountUSD=None, limitExpression=None, subCategory=None)]}
    ok, errors = validate(payload, POLICY_RULE_EXTRACTION_SCHEMA)
    assert ok, errors


@pytest.mark.parametrize(
    "bad",
    [
        {},                                                       # no rules key
        {"rules": [{"category": "Meals"}]},                       # missing required evidence
        {"rules": [_raw(confidence="high")]},                     # confidence must be numeric
        {"rules": [_raw(pageNumbers="page 3")]},                  # must be an array
        {"rules": [_raw(basis="whenever")]},                      # not in the enum
    ],
)
def test_schema_rejects_malformed_extractions(bad: dict[str, Any]) -> None:
    ok, _ = validate(bad, POLICY_RULE_EXTRACTION_SCHEMA)
    assert not ok


def test_schema_is_expressible_in_structured_output_mode() -> None:
    """Structured-output mode rejects numeric/string constraints and needs additionalProperties.

    A ``minimum`` added here would 400 at the API rather than failing a test, so the check is
    structural: walk every object and assert the shape stays within the supported subset.
    """
    banned = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
              "minLength", "maxLength", "pattern", "minItems", "maxItems", "$ref"}

    def walk(node: Any, path: str) -> None:
        if not isinstance(node, dict):
            return
        assert not (set(node) & banned), f"{path} uses unsupported keyword(s): {set(node) & banned}"
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False, f"{path} must forbid extra properties"
            for name, child in (node.get("properties") or {}).items():
                walk(child, f"{path}.{name}")
        if node.get("type") == "array":
            walk(node.get("items"), f"{path}[]")

    walk(POLICY_RULE_EXTRACTION_SCHEMA, "$")
