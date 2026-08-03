"""Combines every section's rules into one set, and refuses to hide a disagreement while doing it.

Once a document is extracted section by section, the same rule can arrive more than once — a
"Meals" limit stated in the summary table on page 5 and restated in the detail section on page 18.
Two cases hide behind that, and collapsing them together is the single most damaging thing this
stage could do:

* **A true duplicate** — both readings agree. Merging them is right, and loses nothing: the pages
  are unioned, the better-evidenced quote is kept, and the reviewer sees one row citing both pages.
* **A conflict** — the readings disagree, e.g. $40 on page 5 and $50 on page 18. Merging them would
  silently pick a winner and publish a limit the document does not unambiguously state. Whether that
  is a policy contradiction or an extraction error is a question only a human can answer, so both
  readings are kept as separate rows and the disagreement is recorded on the proposal for a reviewer
  to resolve.

Pure and deterministic. No model call, no database access, no randomness — the same section outputs
always produce the same merged set and the same conflicts, which is what makes a re-run
comparable to the run before it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, Optional, Sequence

from app.ai.policy_extraction.normalization import NormalizedRule

#: How far apart two competing amounts must be, proportionally, before the conflict is called
#: ``HIGH``. A 10% disagreement is usually one reading picking up a nearby number from an adjacent
#: table column; a 2x disagreement is a substantively different policy.
_HIGH_SEVERITY_RATIO = Decimal("0.25")

SEVERITY_HIGH = "HIGH"
SEVERITY_MEDIUM = "MEDIUM"


@dataclass(frozen=True, slots=True)
class ConflictingValue:
    """One side of a disagreement, with everything needed to adjudicate it."""

    max_amount: Optional[Decimal]
    limit_expression: Optional[str]
    pages: tuple[int, ...]
    source_quote: str
    confidence: Decimal
    section_index: Optional[int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "maxAmountUSD": float(self.max_amount) if self.max_amount is not None else None,
            "limitExpression": self.limit_expression,
            "pageNumbers": list(self.pages),
            "sourceQuote": self.source_quote,
            "confidence": float(self.confidence),
            "sectionIndex": self.section_index,
        }


@dataclass(frozen=True, slots=True)
class RuleConflict:
    """Two or more irreconcilable readings of the same rule slot."""

    rule_id: str
    category: str
    grade_tier: str
    severity: str
    competing: tuple[ConflictingValue, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ruleId": self.rule_id,
            "category": self.category,
            "gradeTier": self.grade_tier,
            "severity": self.severity,
            "competing": [value.to_dict() for value in self.competing],
        }


@dataclass(frozen=True, slots=True)
class MergeResult:
    """The merged rule set plus every disagreement found while merging it."""

    rules: list[NormalizedRule] = field(default_factory=list)
    conflicts: list[RuleConflict] = field(default_factory=list)


def merge_section_rules(section_rules: Sequence[Sequence[NormalizedRule]]) -> MergeResult:
    """Merge per-section rule lists into one set, recording conflicts rather than resolving them.

    ``section_rules`` is one list per section, in section order. Order is preserved: a rule keeps
    the position of its first appearance, so the proposal reads in the same sequence as the document
    even though it was assembled from independent calls.
    """
    grouped: dict[str, list[NormalizedRule]] = {}
    order: list[str] = []
    for rules in section_rules:
        for rule in rules:
            key = rule.rule_id or _fallback_key(rule)
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append(rule)

    merged: list[NormalizedRule] = []
    conflicts: list[RuleConflict] = []
    for key in order:
        group = grouped[key]
        if len(group) == 1:
            merged.append(group[0])
            continue
        agreeing, disagreeing = _partition(group)
        if disagreeing is None:
            merged.append(_collapse(agreeing))
            continue
        conflicts.append(_conflict_from(disagreeing))
        # Every competing reading is kept. Dropping the losing side would leave the reviewer with a
        # conflict record naming a value that appears in no row they can edit or reject.
        merged.extend(_collapse(same) for same in disagreeing)
    return MergeResult(rules=merged, conflicts=conflicts)


def _partition(
    group: Sequence[NormalizedRule],
) -> tuple[list[NormalizedRule], Optional[list[list[NormalizedRule]]]]:
    """Split a rule-id group by the value its members claim.

    Returns ``(group, None)`` when every member agrees, or ``(group, buckets)`` where each bucket
    holds the members sharing one value.
    """
    buckets: dict[tuple[Any, ...], list[NormalizedRule]] = {}
    bucket_order: list[tuple[Any, ...]] = []
    for rule in group:
        key = _value_key(rule)
        if key not in buckets:
            buckets[key] = []
            bucket_order.append(key)
        buckets[key].append(rule)
    if len(buckets) == 1:
        return list(group), None
    return list(group), [buckets[key] for key in bucket_order]


def _value_key(rule: NormalizedRule) -> tuple[Any, ...]:
    """What "the same rule" means for agreement purposes: the limit, however it is expressed.

    Thresholds are excluded on purpose. Two readings that agree the cap is $40 but differ on the
    receipt threshold are the same rule read with differing completeness, and merging them (taking
    the stricter threshold) is right. Only a differing *limit* is a genuine contradiction.
    """
    return (
        rule.max_amount,
        (rule.limit_expression or "").strip().lower(),
        rule.always_manual,
    )


def _collapse(group: Sequence[NormalizedRule]) -> NormalizedRule:
    """Fold agreeing readings into one row, keeping the best-evidenced version of each field.

    The winner supplies the narrative fields (quote, expression) because they come as a set — a
    quote from one reading paired with an expression from another would describe neither. Numeric
    thresholds take the strictest value seen, so a rule stated loosely in a summary and precisely in
    the detail section publishes at the precise, tighter value.
    """
    winner = max(group, key=lambda r: r.confidence)
    pages = sorted({page for rule in group for page in rule.page_numbers})
    chunk_ids = tuple(
        dict.fromkeys(cid for rule in group for cid in rule.source_chunk_ids)
    )
    auto_limits = [r.auto_approve_limit for r in group if r.auto_approve_limit is not None]
    receipts = [r.receipt_required_above for r in group if r.receipt_required_above is not None]

    return replace(
        winner,
        page_numbers=tuple(pages),
        source_chunk_ids=chunk_ids,
        auto_approve_limit=min(auto_limits) if auto_limits else None,
        receipt_required_above=min(receipts) if receipts else None,
        requires_pre_approval=any(r.requires_pre_approval for r in group),
        always_manual=any(r.always_manual for r in group),
        special_rules=list(dict.fromkeys(text for rule in group for text in rule.special_rules)),
        confidence=winner.confidence,
    )


def _conflict_from(buckets: Sequence[Sequence[NormalizedRule]]) -> RuleConflict:
    representative = buckets[0][0]
    competing = tuple(_competing_value(bucket) for bucket in buckets)
    return RuleConflict(
        rule_id=representative.rule_id,
        category=representative.category,
        grade_tier=representative.grade_tier,
        severity=_severity(competing),
        competing=competing,
    )


def _competing_value(bucket: Sequence[NormalizedRule]) -> ConflictingValue:
    best = max(bucket, key=lambda r: r.confidence)
    return ConflictingValue(
        max_amount=best.max_amount,
        limit_expression=best.limit_expression,
        pages=tuple(sorted({page for rule in bucket for page in rule.page_numbers})),
        source_quote=best.source_quote,
        confidence=best.confidence,
        section_index=best.section_index,
    )


def _severity(competing: Sequence[ConflictingValue]) -> str:
    """Grade a conflict by how far apart the competing amounts are.

    A conflict where one side has no number at all (a formula against a fixed cap) is ``HIGH``: the
    two readings do not even agree on what kind of limit this is, which no amount comparison can
    reconcile.
    """
    amounts = [value.max_amount for value in competing if value.max_amount is not None]
    if len(amounts) < len(competing) or len(amounts) < 2:
        return SEVERITY_HIGH
    low, high = min(amounts), max(amounts)
    if low <= 0:
        return SEVERITY_HIGH
    return SEVERITY_HIGH if (high - low) / low >= _HIGH_SEVERITY_RATIO else SEVERITY_MEDIUM


def _fallback_key(rule: NormalizedRule) -> str:
    """Grouping key for a rule whose id is somehow blank.

    ``normalize_rule`` always sets one, so this only guards a hand-built rule in a test or a future
    caller that bypasses normalization — in which case grouping by the same three fields the id is
    derived from keeps the behaviour identical rather than treating every such rule as unique.
    """
    return f"{rule.category}|{rule.grade_tier}|{rule.basis.value}"


__all__ = [
    "SEVERITY_HIGH",
    "SEVERITY_MEDIUM",
    "ConflictingValue",
    "MergeResult",
    "RuleConflict",
    "merge_section_rules",
]
