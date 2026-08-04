"""PolicyRuleRepository and PolicyRuleService: versioning and effective dating.

The property that matters: publishing a change must never destroy the rule text a historical claim
was judged under.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.domain.errors import NotFoundError, ValidationError


@pytest.fixture
def rules(repositories):
    return repositories["policy_rules"]


# --- reads -------------------------------------------------------------------


def test_seeded_rules_are_active(rules):
    codes = {rule.code for rule in rules.list_active()}
    assert "MEALS_STANDARD" in codes


def test_active_rules_are_ordered_by_priority(rules):
    priorities = [rule.priority for rule in rules.list_active()]
    assert priorities == sorted(priorities)


def test_get_active_for_category(rules):
    rule = rules.get_active_for_category("Meals")
    assert rule is not None
    assert rule.code == "MEALS_STANDARD"
    assert rule.expense_limit == Decimal("40.00")


def test_get_active_for_unknown_category_returns_none(rules):
    assert rules.get_active_for_category("Interstellar Travel") is None


def test_get_by_code_returns_the_newest_version(rules):
    rules.publish_version(
        code="MEALS_STANDARD", name="Meals v2", category="Meals",
        expense_limit=Decimal("45.00"),
    )
    assert rules.get_by_code("MEALS_STANDARD").version == 2
    assert rules.get_by_code("MEALS_STANDARD", version=1).expense_limit == Decimal("40.00")


def test_latest_version_number_for_new_code_is_zero(rules):
    assert rules.latest_version_number("BRAND_NEW_RULE") == 0


# --- versioning --------------------------------------------------------------


def test_publish_supersedes_without_deleting(rules):
    original = rules.get_by_code("MEALS_STANDARD", version=1)
    original_limit = original.expense_limit

    published = rules.publish_version(
        code="MEALS_STANDARD", name="Meals v2", category="Meals",
        expense_limit=Decimal("50.00"),
    )

    assert published.version == 2
    assert published.is_active is True
    # The old version survives, deactivated and expiry-stamped, with its text intact.
    assert original.is_active is False
    assert original.expiration_date is not None
    assert original.expense_limit == original_limit

    versions = rules.list_versions("MEALS_STANDARD")
    assert [rule.version for rule in versions] == [2, 1]


def test_only_one_active_version_per_code(rules):
    rules.publish_version(code="MEALS_STANDARD", name="v2", category="Meals")
    rules.publish_version(code="MEALS_STANDARD", name="v3", category="Meals")

    active = [
        rule for rule in rules.list_active() if rule.code == "MEALS_STANDARD"
    ]
    assert len(active) == 1
    assert active[0].version == 3


def test_expiry_never_precedes_effective_date(rules):
    """Backdating a new version must not create a rule that expired before it began."""
    future_rule = rules.publish_version(
        code="FUTURE_RULE", name="Future", category="Meals",
        effective_date=date.today() + timedelta(days=30),
    )
    rules.publish_version(
        code="FUTURE_RULE", name="Future v2", category="Meals", effective_date=date.today()
    )
    assert future_rule.expiration_date >= future_rule.effective_date


def test_deactivate_stamps_expiry(rules):
    rule = rules.get_by_code("MEALS_STANDARD")
    rules.deactivate(rule)
    assert rule.is_active is False
    assert rule.expiration_date is not None


def test_deactivate_all_active_returns_count(rules):
    count = rules.deactivate_all_active()
    assert count >= 5
    assert list(rules.list_active()) == []


# --- effective dating --------------------------------------------------------


def test_list_effective_excludes_not_yet_effective_rules(rules):
    rules.publish_version(
        code="NEXT_MONTH", name="Next month", category="Meals",
        effective_date=date.today() + timedelta(days=30),
    )
    effective_codes = {rule.code for rule in rules.list_effective()}
    assert "NEXT_MONTH" not in effective_codes


def test_list_effective_includes_a_rule_on_its_effective_date(rules):
    rules.publish_version(
        code="STARTS_TODAY", name="Starts today", category="Meals",
        effective_date=date.today(),
    )
    assert "STARTS_TODAY" in {rule.code for rule in rules.list_effective()}


def test_historical_claims_still_resolve_the_rule_that_governed_them(rules):
    """A claim from before a policy change must resolve the *old* rule."""
    changed_on = date.today()
    rules.publish_version(
        code="MEALS_STANDARD", name="Meals v2", category="Meals",
        expense_limit=Decimal("60.00"), effective_date=changed_on,
    )

    yesterday = changed_on - timedelta(days=1)
    historical = [
        rule for rule in rules.list_effective(yesterday) if rule.code == "MEALS_STANDARD"
    ]
    assert len(historical) == 0  # v1 is now inactive...

    # ...but its text remains queryable for explaining the historical decision.
    v1 = rules.get_by_code("MEALS_STANDARD", version=1)
    assert v1.expense_limit == Decimal("40.00")
    assert v1.is_effective_on(yesterday) is False


def test_is_effective_on_window(rules):
    rule = rules.publish_version(
        code="WINDOWED", name="Windowed", category="Meals",
        effective_date=date(2026, 1, 1),
    )
    rule.expiration_date = date(2026, 6, 30)
    assert rule.is_effective_on(date(2026, 3, 1)) is True
    assert rule.is_effective_on(date(2025, 12, 31)) is False
    assert rule.is_effective_on(date(2026, 7, 1)) is False


# --- service layer -----------------------------------------------------------


def test_service_exposes_the_legacy_wire_shape(policy_rule_service):
    payload = policy_rule_service.list_active_as_dicts()
    meals = next(rule for rule in payload if rule["category"] == "Meals")

    # Exact keys the existing frontend reads.
    for key in (
        "category", "maxAmountUSD", "autoApproveLimitUSD", "receiptRequiredAboveUSD",
        "requiresPreApproval", "gradeTier", "specialRules",
    ):
        assert key in meals
    assert meals["maxAmountUSD"] == 40.0
    assert meals["autoApproveLimitUSD"] == 25.0
    assert isinstance(meals["specialRules"], list)


def test_replace_ruleset_publishes_and_retires(policy_rule_service, admin_actor, rules):
    published = policy_rule_service.replace_ruleset(
        [
            {
                "category": "Meals",
                "maxAmountUSD": 55.0,
                "autoApproveLimitUSD": 30.0,
                "receiptRequiredAboveUSD": 30.0,
                "requiresPreApproval": False,
                "gradeTier": "All Staff",
                "specialRules": ["Updated meal cap."],
            }
        ],
        actor=admin_actor,
    )

    assert len(published) == 1
    assert published[0].code == "MEALS_STANDARD"
    assert published[0].version == 2
    assert published[0].expense_limit == Decimal("55.00")

    # Rules absent from the payload are retired, not deleted.
    active_codes = {rule.code for rule in rules.list_active()}
    assert active_codes == {"MEALS_STANDARD"}
    assert rules.get_by_code("FLIGHTS_STANDARD") is not None
    assert rules.get_by_code("FLIGHTS_STANDARD").is_active is False


def test_replace_ruleset_derives_a_code_from_the_category(policy_rule_service, admin_actor):
    published = policy_rule_service.replace_ruleset(
        [{"category": "Client Entertainment", "maxAmountUSD": 400.0}], actor=admin_actor
    )
    assert published[0].code == "CLIENT_ENTERTAINMENT_STANDARD"


def test_replace_ruleset_keeps_prose_limits(policy_rule_service, admin_actor):
    """"Per signed agreement" is not a number — it must survive the round trip."""
    published = policy_rule_service.replace_ruleset(
        [{"category": "Relocation", "maxAmountUSD": "Per signed agreement"}], actor=admin_actor
    )
    assert published[0].expense_limit is None
    assert published[0].limit_expression == "Per signed agreement"

    from app.services.mappers import policy_rule_to_dict

    assert policy_rule_to_dict(published[0])["maxAmountUSD"] == "Per signed agreement"


def test_replace_ruleset_audits_the_change(policy_rule_service, admin_actor, repositories):
    policy_rule_service.replace_ruleset(
        [{"category": "Meals", "maxAmountUSD": 55.0}], actor=admin_actor
    )
    entries = repositories["audit"].search(action="POLICY_UPDATE")
    assert entries
    latest = entries[0]
    assert latest.actor_role == "admin"
    assert latest.before is not None and latest.after is not None
    assert any(rule["category"] == "Meals" for rule in latest.before["rules"])


def test_replace_ruleset_rejects_a_rule_without_a_category(policy_rule_service, admin_actor):
    with pytest.raises(ValidationError, match="category"):
        policy_rule_service.replace_ruleset([{"maxAmountUSD": 10.0}], actor=admin_actor)


def test_replace_ruleset_rejects_a_non_object_entry(policy_rule_service, admin_actor):
    with pytest.raises(ValidationError, match="must be an object"):
        policy_rule_service.replace_ruleset(["not-a-rule"], actor=admin_actor)


def test_replace_ruleset_rejects_non_list_special_rules(policy_rule_service, admin_actor):
    with pytest.raises(ValidationError, match="specialRules"):
        policy_rule_service.replace_ruleset(
            [{"category": "Meals", "specialRules": "should be a list"}], actor=admin_actor
        )


def test_get_versions_raises_for_unknown_code(policy_rule_service):
    with pytest.raises(NotFoundError):
        policy_rule_service.get_versions("NO_SUCH_RULE")


def test_rules_for_engine_matches_the_engine_contract(policy_rule_service):
    """The engine reads these exact keys; a rename would silently disable policy checks."""
    engine_rules = policy_rule_service.rules_for_engine()
    meals = next(rule for rule in engine_rules if rule["category"] == "Meals")
    assert isinstance(meals["maxAmountUSD"], float)
    assert meals["receiptRequiredAboveUSD"] == 25.0
