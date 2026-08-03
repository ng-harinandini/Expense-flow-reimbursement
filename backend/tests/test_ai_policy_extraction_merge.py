"""Merging section outputs, and the line between a duplicate and a conflict.

The distinction this module draws is the most consequential one in the pipeline. Once a document is
read section by section, the same rule arrives more than once — the summary table on page 5 and the
detail section on page 18. If both readings agree, merging is right and lossless. If they disagree,
merging would silently pick a winner and publish a limit the document does not unambiguously state.

So: **agreement collapses, disagreement is recorded and both sides survive.** A conflict a reviewer
never sees is worse than no extraction at all, because it looks like a confident answer.

Also under test is stable rule identity, which is what makes any of this possible: two readings can
only be compared if something says they are about the same thing.
"""

from __future__ import annotations

from decimal import Decimal

from app.ai.core.enums import LimitBasis
from app.ai.policy_extraction.merge import SEVERITY_HIGH, SEVERITY_MEDIUM, merge_section_rules
from app.ai.policy_extraction.normalization import NormalizedRule
from app.ai.policy_extraction.rule_ids import generate_rule_id


def rule(
    *,
    category: str = "Meals",
    grade_tier: str = "All Staff",
    basis: LimitBasis = LimitBasis.PER_DAY,
    max_amount: str | None = "40",
    auto_approve_limit: str | None = None,
    receipt_required_above: str | None = None,
    limit_expression: str | None = None,
    always_manual: bool = False,
    pages: tuple[int, ...] = (1,),
    quote: str = "All $40 / day",
    confidence: str = "0.9",
    section_index: int = 0,
) -> NormalizedRule:
    return NormalizedRule(
        category=category,
        grade_tier=grade_tier,
        basis=basis,
        page_numbers=pages,
        source_quote=quote,
        confidence=Decimal(confidence),
        representable=True,
        rule_id=generate_rule_id(category, grade_tier, basis),
        max_amount=Decimal(max_amount) if max_amount is not None else None,
        auto_approve_limit=(
            Decimal(auto_approve_limit) if auto_approve_limit is not None else None
        ),
        receipt_required_above=(
            Decimal(receipt_required_above) if receipt_required_above is not None else None
        ),
        limit_expression=limit_expression,
        always_manual=always_manual,
        section_index=section_index,
    )


# --- rule identity ---------------------------------------------------------------


def test_the_same_slot_produces_the_same_id_across_readings() -> None:
    assert generate_rule_id("Meals", "All Staff", LimitBasis.PER_DAY) == generate_rule_id(
        "Meals", "All Staff", LimitBasis.PER_DAY
    )


def test_the_amount_is_not_part_of_the_id() -> None:
    """An id that encoded the amount would change the moment the limit changed — which is exactly
    the event the id exists to let you observe."""
    cheap = rule(max_amount="40")
    dear = rule(max_amount="50")
    assert cheap.rule_id == dear.rule_id


def test_grade_tiers_that_differ_only_by_a_plus_do_not_collide() -> None:
    """``L4`` and ``L4+`` are different tiers. Collapsing them would merge two real rules into one
    id and manufacture a conflict that does not exist."""
    assert generate_rule_id("Lodging", "L4", LimitBasis.PER_NIGHT) != generate_rule_id(
        "Lodging", "L4+", LimitBasis.PER_NIGHT
    )


def test_basis_is_part_of_the_id_so_per_day_and_per_claim_stay_distinct() -> None:
    assert generate_rule_id("Meals", "All Staff", LimitBasis.PER_DAY) != generate_rule_id(
        "Meals", "All Staff", LimitBasis.PER_CLAIM
    )


# --- duplicates ------------------------------------------------------------------


def test_agreeing_readings_collapse_into_one_rule() -> None:
    result = merge_section_rules(
        [[rule(pages=(5,), section_index=0)], [rule(pages=(18,), section_index=3)]]
    )
    assert len(result.rules) == 1
    assert result.conflicts == []


def test_a_collapsed_rule_cites_every_page_it_was_read_from() -> None:
    result = merge_section_rules([[rule(pages=(5,))], [rule(pages=(18,))]])
    assert result.rules[0].page_numbers == (5, 18)


def test_the_better_evidenced_quote_wins() -> None:
    result = merge_section_rules(
        [
            [rule(quote="summary table row", confidence="0.6")],
            [rule(quote="Meals are capped at $40 per day", confidence="0.95")],
        ]
    )
    assert result.rules[0].source_quote == "Meals are capped at $40 per day"


def test_the_stricter_threshold_governs_when_readings_differ_in_completeness() -> None:
    """Two readings agreeing on the cap but differing on a threshold are the same rule read with
    differing precision, not a contradiction — so the tighter value publishes."""
    result = merge_section_rules(
        [[rule(auto_approve_limit="25")], [rule(auto_approve_limit="10")]]
    )
    assert result.rules[0].auto_approve_limit == Decimal("10")


def test_distinct_rules_are_never_merged() -> None:
    result = merge_section_rules(
        [[rule(category="Meals")], [rule(category="Lodging", basis=LimitBasis.PER_NIGHT)]]
    )
    assert len(result.rules) == 2
    assert result.conflicts == []


def test_document_order_survives_the_merge() -> None:
    result = merge_section_rules(
        [
            [rule(category="Meals")],
            [rule(category="Flights", basis=LimitBasis.OTHER, max_amount=None,
                  limit_expression="Economy")],
            [rule(category="Lodging", basis=LimitBasis.PER_NIGHT, max_amount="120")],
        ]
    )
    assert [r.category for r in result.rules] == ["Meals", "Flights", "Lodging"]


# --- conflicts -------------------------------------------------------------------


def test_disagreeing_amounts_become_a_conflict_and_both_rows_survive() -> None:
    """The load-bearing test. Silently publishing either $40 or $50 would state a limit the
    document does not — and would look exactly like a confident, correct answer."""
    result = merge_section_rules(
        [
            [rule(max_amount="40", pages=(5,), section_index=0)],
            [rule(max_amount="50", pages=(18,), section_index=4)],
        ]
    )

    assert len(result.conflicts) == 1
    assert len(result.rules) == 2, "every competing reading must remain editable by the reviewer"

    conflict = result.conflicts[0]
    assert conflict.category == "Meals"
    assert {value.max_amount for value in conflict.competing} == {Decimal("40"), Decimal("50")}
    assert {value.pages for value in conflict.competing} == {(5,), (18,)}


def test_a_conflict_records_where_each_reading_came_from() -> None:
    """Pages, quotes and the originating section are what let a reviewer adjudicate without
    re-reading the document."""
    result = merge_section_rules(
        [
            [rule(max_amount="40", pages=(5,), quote="All $40 / day", section_index=1)],
            [rule(max_amount="50", pages=(18,), quote="meals: $50 daily", section_index=6)],
        ]
    )
    competing = {value.section_index: value for value in result.conflicts[0].competing}
    assert competing[1].source_quote == "All $40 / day"
    assert competing[6].pages == (18,)


def test_a_wide_disagreement_is_graded_high_and_a_narrow_one_medium() -> None:
    wide = merge_section_rules([[rule(max_amount="40")], [rule(max_amount="100")]])
    narrow = merge_section_rules([[rule(max_amount="40")], [rule(max_amount="42")]])
    assert wide.conflicts[0].severity == SEVERITY_HIGH
    assert narrow.conflicts[0].severity == SEVERITY_MEDIUM


def test_a_number_against_a_formula_is_always_high_severity() -> None:
    """The two readings do not agree on what *kind* of limit this is, which no amount comparison
    can reconcile."""
    result = merge_section_rules(
        [
            [rule(max_amount="40")],
            [rule(max_amount=None, basis=LimitBasis.PER_DAY,
                  limit_expression="Published rate x days")],
        ]
    )
    assert result.conflicts[0].severity == SEVERITY_HIGH


def test_three_way_disagreement_records_all_three() -> None:
    result = merge_section_rules(
        [[rule(max_amount="40")], [rule(max_amount="50")], [rule(max_amount="60")]]
    )
    assert len(result.conflicts[0].competing) == 3
    assert len(result.rules) == 3


def test_conflicts_serialize_for_the_proposal_column() -> None:
    result = merge_section_rules([[rule(max_amount="40")], [rule(max_amount="50")]])
    payload = result.conflicts[0].to_dict()
    assert payload["ruleId"]
    assert payload["category"] == "Meals"
    assert [value["maxAmountUSD"] for value in payload["competing"]] == [40.0, 50.0]


def test_merging_nothing_yields_nothing() -> None:
    result = merge_section_rules([[], [], []])
    assert result.rules == []
    assert result.conflicts == []
