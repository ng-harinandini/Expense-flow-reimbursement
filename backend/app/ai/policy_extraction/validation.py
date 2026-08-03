"""Deterministic checks on extracted rules, run before a proposal is written.

**Warnings, never failures.** Every finding here attaches to the item it concerns and is surfaced to
the reviewer; none of them stops a proposal being created. That is the whole design: an extraction
that read eleven rules correctly and one badly should produce twelve reviewable rows with the bad
one flagged, not an error that discards all twelve. The reviewer is the gate — validation's job is
to tell them where to look, not to make the decision for them.

**No model involvement.** These are arithmetic and vocabulary checks over already-normalized values.
That matters for trust: a warning that says "the auto-approve limit exceeds the maximum amount" is
verifiable by reading the two numbers, and a reviewer can act on it without wondering whether the
checker itself hallucinated.

Several checks overlap with guards that already exist further down the stack — database check
constraints on ``policy_rule_proposal_items``, the clamp in ``clamp_confidence``, the sign test in
``parse_amount``. That redundancy is intentional and cheap: those guards *silently correct or
reject*, and a value that had to be corrected is exactly the signal a reviewer wants to see. A check
that never fires costs nothing; one that does has caught something the extraction got wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Sequence

from app.ai.policy_extraction.normalization import NON_SCALAR_BASES, NormalizedRule

#: Severity is advisory ordering for a review UI, not an enum with behaviour attached.
#: ``ERROR`` marks a rule that cannot be correct as read (contradictory numbers, no limit at all);
#: ``WARNING`` marks one that is suspicious but publishable if the reviewer agrees with it.
SEVERITY_ERROR = "ERROR"
SEVERITY_WARNING = "WARNING"


@dataclass(frozen=True, slots=True)
class ValidationWarning:
    """One finding about one rule."""

    rule_id: str
    code: str
    message: str
    severity: str = SEVERITY_WARNING

    def to_dict(self) -> dict[str, Any]:
        return {
            "ruleId": self.rule_id,
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }


def validate_rules(rules: Sequence[NormalizedRule]) -> dict[str, list[ValidationWarning]]:
    """Check every rule, returning findings keyed by ``rule_id``.

    Keyed rather than flat so the orchestrator can attach each rule's findings to its own row
    without re-matching. A rule with nothing wrong does not appear in the mapping at all.
    """
    findings: dict[str, list[ValidationWarning]] = {}
    for rule in rules:
        for warning in _check_one(rule):
            findings.setdefault(warning.rule_id, []).append(warning)
    return findings


def _check_one(rule: NormalizedRule) -> list[ValidationWarning]:
    rule_id = rule.rule_id or "UNKNOWN"
    found: list[ValidationWarning] = []

    def add(code: str, message: str, severity: str = SEVERITY_WARNING) -> None:
        found.append(ValidationWarning(rule_id, code, message, severity))

    if not rule.category:
        add("MISSING_CATEGORY", "The rule has no category and cannot be matched to a claim.",
            SEVERITY_ERROR)
    elif not rule.representable:
        # Not a defect — the extractor is expected to report rules outside the configured category
        # set. Surfaced so the reviewer knows why the rule will not publish without also having to
        # open the item's `unrepresentable_reason`.
        add(
            "UNREPRESENTABLE_CATEGORY",
            rule.unrepresentable_reason
            or "The category is outside the configured set, so this rule cannot be published.",
        )

    if len(rule.currency) != 3 or not rule.currency.isalpha():
        add("INVALID_CURRENCY", f"'{rule.currency}' is not a three-letter ISO-4217 code.",
            SEVERITY_ERROR)

    _check_limits(rule, add)
    _check_thresholds(rule, add)
    _check_confidence(rule, add)
    _check_evidence(rule, add)
    return found


def _check_limits(rule: NormalizedRule, add) -> None:  # noqa: ANN001 - local callback
    """A rule must express a limit somehow: a number, a formula, or 'never automatic'."""
    has_amount = rule.max_amount is not None
    has_expression = bool(rule.limit_expression)
    non_scalar = rule.basis in NON_SCALAR_BASES

    if not has_amount and not has_expression and not rule.always_manual:
        add(
            "MISSING_LIMIT",
            "No maximum amount, limit expression, or manual-approval requirement was extracted, "
            "so this rule constrains nothing.",
            SEVERITY_ERROR,
        )
    if has_amount and rule.max_amount < 0:
        add("NEGATIVE_LIMIT", "The maximum amount is negative.", SEVERITY_ERROR)
    if has_amount and non_scalar:
        add(
            "AMOUNT_WITH_NON_SCALAR_BASIS",
            f"A fixed amount was read alongside basis '{rule.basis.value}', which describes a "
            "limit no single number can express.",
        )
    if has_amount and rule.max_amount == 0:
        add(
            "ZERO_LIMIT",
            "The maximum amount is zero, which permits no spend at all. Confirm the document says "
            "this rather than leaving the limit unstated.",
        )


def _check_thresholds(rule: NormalizedRule, add) -> None:  # noqa: ANN001 - local callback
    auto = rule.auto_approve_limit
    receipt = rule.receipt_required_above
    cap = rule.max_amount

    if rule.always_manual and auto is not None:
        add(
            "MANUAL_WITH_AUTO_APPROVE",
            "The rule is marked always-manual but also carries an auto-approve threshold.",
            SEVERITY_ERROR,
        )
    if auto is not None and cap is not None and auto > cap:
        add(
            "AUTO_APPROVE_ABOVE_LIMIT",
            f"The auto-approve threshold ({auto}) exceeds the maximum amount ({cap}), so every "
            "claim within policy would approve automatically.",
            SEVERITY_ERROR,
        )
    if receipt is not None and cap is not None and receipt > cap:
        add(
            "RECEIPT_THRESHOLD_ABOVE_LIMIT",
            f"The receipt threshold ({receipt}) exceeds the maximum amount ({cap}), so no claim "
            "within policy would ever require a receipt.",
        )
    if auto is not None and auto < 0:
        add("NEGATIVE_AUTO_APPROVE", "The auto-approve threshold is negative.", SEVERITY_ERROR)
    if receipt is not None and receipt < 0:
        add("NEGATIVE_RECEIPT_THRESHOLD", "The receipt threshold is negative.", SEVERITY_ERROR)
    if auto is None and not rule.always_manual:
        add(
            "MISSING_APPROVAL_RULE",
            "Neither an auto-approve threshold nor an always-manual marker was extracted, so how "
            "this category is approved is unstated.",
        )


def _check_confidence(rule: NormalizedRule, add) -> None:  # noqa: ANN001 - local callback
    if not Decimal("0") <= rule.confidence <= Decimal("1"):
        add("CONFIDENCE_OUT_OF_RANGE", f"Confidence {rule.confidence} is outside 0..1.",
            SEVERITY_ERROR)
    elif rule.confidence < Decimal("0.5"):
        add(
            "LOW_CONFIDENCE",
            f"The extractor reported low confidence ({rule.confidence}). Check this rule against "
            "the source page before approving.",
        )


def _check_evidence(rule: NormalizedRule, add) -> None:  # noqa: ANN001 - local callback
    """Evidence is what makes a rule reviewable at all.

    A rule with no page and no quote can only be checked by re-reading the whole document, which
    defeats the purpose of extracting it — so its absence is reported even though nothing downstream
    requires it.
    """
    if not rule.page_numbers:
        add("MISSING_PAGE_CITATION", "The rule cites no page, so its source cannot be located.")
    if not rule.source_quote:
        add("MISSING_SOURCE_QUOTE", "The rule carries no supporting quote from the document.")


__all__ = [
    "SEVERITY_ERROR",
    "SEVERITY_WARNING",
    "ValidationWarning",
    "validate_rules",
]
