"""Turns raw extractor output into rows, and approved rows into a publishable ruleset.

Two separate jobs, deliberately in one module because they share the same vocabulary:

1. :func:`normalize_rule` — one raw object from the model becomes one :class:`NormalizedRule`,
   faithful to the document. Amounts are parsed to ``Decimal``, dashes and grade tiers are
   canonicalized, and a rule outside the closed category set is *flagged* rather than dropped or
   forced.
2. :func:`build_ruleset_payload` — approved rows become the payload for
   ``PolicyRuleService.replace_ruleset``. This is where two structural facts about ``policy_rules``
   are handled, either of which silently destroys data if ignored:

   **One row per category, not per grade.** ``replace_ruleset`` derives a rule's ``code`` from its
   category, and ``publish_version`` deactivates the previous active row for that code. Submitting a
   document's two lodging rows — one per grade band — therefore publishes the first and then
   immediately retires it by publishing the second: the lower band vanishes with no error. So
   grade-differentiated rows are merged into one publishable rule whose per-grade detail lives in
   ``actions``, which is exactly the shape the seeded reference data already uses.

   **Absence means retirement.** ``_retire_absent`` deactivates every active rule whose code is not
   in the submitted payload. A payload containing only the categories one document happened to
   mention would retire all the others, dropping them into the policy engine's generic fallback. So
   the payload is always a *merge over the current active ruleset*, never just the extracted subset.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Optional, Sequence

from app.ai.core.enums import LimitBasis
from app.ai.policy_extraction.rule_ids import generate_rule_id

#: Bases that describe a limit money cannot express. These carry ``limit_expression`` and no amount.
NON_SCALAR_BASES = frozenset({LimitBasis.FORMULA, LimitBasis.AGREEMENT, LimitBasis.OTHER})

#: Grade-tier label used when one category's rows differ by grade — the seeded convention.
GRADE_DEPENDENT = "Grade Dependent"
#: Grade-tier label for a rule that applies to everyone — also the ``replace_ruleset`` default.
ALL_STAFF = "All Staff"

_ALL_STAFF_SYNONYMS = frozenset(
    {"all", "all staff", "everyone", "every employee", "all employees", "any", "any level"}
)

# `policy_rules.grade_tier` is String(64); `category` is String(64); `limit_expression` String(120).
_MAX_GRADE_TIER = 64
_MAX_CATEGORY = 64
_MAX_LIMIT_EXPRESSION = 120

_MONEY_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


@dataclass(slots=True)
class NormalizedRule:
    """One extracted rule, cleaned but still faithful to the document."""

    category: str
    grade_tier: str
    basis: LimitBasis
    page_numbers: tuple[int, ...]
    source_quote: str
    confidence: Decimal
    representable: bool
    unrepresentable_reason: Optional[str] = None
    sub_category: Optional[str] = None
    currency: str = "USD"
    max_amount: Optional[Decimal] = None
    limit_expression: Optional[str] = None
    auto_approve_limit: Optional[Decimal] = None
    receipt_required_above: Optional[Decimal] = None
    requires_pre_approval: bool = False
    always_manual: bool = False
    special_rules: list[str] = field(default_factory=list)
    #: Stable slot identifier (see :mod:`app.ai.policy_extraction.rule_ids`). Assigned by
    #: :func:`normalize_rule`; the merge step groups by it, so it must never be left blank.
    rule_id: str = ""
    #: Which detected section this rule was read from, and the chunks that section covered. Set by
    #: the orchestrator after extraction — normalization sees one rule at a time and has no view of
    #: the document — and carried through so a reviewer can trace an item back to its source text.
    section_index: Optional[int] = None
    source_chunk_ids: tuple[str, ...] = ()


# --- scalar cleaners ----------------------------------------------------------


def normalize_dashes(text: str) -> str:
    """Fold every Unicode dash onto ASCII ``-``.

    A policy table writes an en dash in ``L1-L3`` and a plain hyphen in ``L5-Director`` in the same
    document, so string comparison against either form fails roughly half the time without this.
    """
    return "".join("-" if unicodedata.category(ch) == "Pd" else ch for ch in text)


def parse_amount(value: Any) -> Optional[Decimal]:
    """Parse a money value from a number or a string such as ``"$2,000 / year"``.

    Returns ``None`` for absent, blank, or unparseable input — but note the caller must not treat
    that as "no limit" without also checking ``limit_expression``: a formula-based limit is a real
    limit with no number.

    Quantized to 2dp because ``policy_rules`` money columns are ``NUMERIC(14, 2)``; an unrounded
    third decimal would be silently truncated by the database instead of here.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            parsed = Decimal(str(value))
        except InvalidOperation:
            return None
    else:
        match = _MONEY_RE.search(str(value))
        if not match:
            return None
        try:
            parsed = Decimal(match.group(0).replace(",", ""))
        except InvalidOperation:
            return None
    if parsed < 0:
        return None
    return parsed.quantize(Decimal("0.01"))


def normalize_grade_tier(value: Any) -> str:
    """Canonicalize a grade tier, mapping every "everyone" phrasing onto one label.

    Other tiers are kept close to the document's own wording: ``grade_tier`` is free text that the
    policy engine never reads, so fidelity to the source is worth more to a reviewer than forcing
    the value into a fixed vocabulary.
    """
    text = normalize_dashes(str(value or "")).strip()
    if not text:
        return ALL_STAFF
    collapsed = re.sub(r"\s+", " ", text)
    if collapsed.lower().strip(" .") in _ALL_STAFF_SYNONYMS:
        return ALL_STAFF
    return collapsed[:_MAX_GRADE_TIER]


def clamp_confidence(value: Any) -> Decimal:
    """Confidence as a 0..1 ``Decimal``.

    The schema cannot express a numeric range (structured-output mode rejects ``minimum``), so the
    bound is enforced here. An unparseable or absent value becomes ``0`` — "unknown" must read as
    *least* trustworthy, never as certainty.
    """
    parsed = parse_amount(value)
    if parsed is None:
        return Decimal("0.00")
    return min(max(parsed, Decimal("0")), Decimal("1")).quantize(Decimal("0.01"))


def normalize_category(value: Any, known: Sequence[str]) -> tuple[str, Optional[str]]:
    """Map an extracted heading onto the closed category set.

    Returns ``(category, unrepresentable_reason)``. On a match the canonical spelling is returned
    with no reason; otherwise the document's own heading is returned alongside the reason it cannot
    be published. Never invents a category: ``expense_categories.name`` must stay in lockstep with
    ``policy_rules.category``, and a value in neither is a rule the engine would never match.
    """
    raw = re.sub(r"\s+", " ", normalize_dashes(str(value or "")).strip())
    if not raw:
        return "", "Extracted rule carried no category."

    lookup = {_category_key(name): name for name in known}
    canonical = lookup.get(_category_key(raw))
    if canonical is not None:
        return canonical, None
    return (
        raw[:_MAX_CATEGORY],
        f"'{raw}' is not one of the configured expense categories "
        f"({', '.join(known)}); it needs a category before it can be published.",
    )


def _category_key(name: str) -> str:
    """Comparison key: case, spacing and separators are not meaningful in a category name."""
    return re.sub(r"[^a-z0-9]+", "", normalize_dashes(str(name)).lower())


def coerce_basis(value: Any) -> LimitBasis:
    """Coerce a basis, degrading to ``OTHER`` instead of raising.

    ``_WireEnum.coerce`` raises on an unrecognized value, which is right at the API edge where a bad
    request should be rejected. Here it is wrong: the schema already constrains this field, so an
    unrecognized value means the model went off-script, and losing the whole extraction over one
    unparseable period would discard every correctly-read rule alongside it. ``OTHER`` keeps the
    rule with its wording intact and visibly unclassified.
    """
    try:
        return LimitBasis.coerce(value)
    except (ValueError, TypeError):
        return LimitBasis.OTHER


# --- rule normalization -------------------------------------------------------


def normalize_rule(raw: Mapping[str, Any], *, known_categories: Sequence[str]) -> NormalizedRule:
    """Clean one raw extractor object into a :class:`NormalizedRule`."""
    category, reason = normalize_category(raw.get("category"), known_categories)
    basis = coerce_basis(raw.get("basis"))

    max_amount = parse_amount(raw.get("maxAmountUSD"))
    limit_expression = _clean_text(raw.get("limitExpression"), _MAX_LIMIT_EXPRESSION)

    # A non-scalar basis must not also carry a number: that pairing is contradictory, and keeping
    # the number would let a formula be published as though it were a flat cap.
    if basis in NON_SCALAR_BASES:
        max_amount = None
    if max_amount is None and not limit_expression and basis in NON_SCALAR_BASES:
        limit_expression = "Not expressible as a fixed amount."

    always_manual = bool(raw.get("alwaysManual", False))
    auto_approve = parse_amount(raw.get("autoApproveLimitUSD"))
    # "always manual" and a threshold of 0 are different rules; the flag wins so a model that
    # answered both ways cannot publish a permissive 0 where the document says never.
    if always_manual:
        auto_approve = None

    representable = bool(raw.get("representable", True)) and reason is None
    if reason is None and not bool(raw.get("representable", True)):
        reason = _clean_text(raw.get("unrepresentableReason"), 500) or (
            "The extractor flagged this rule as not publishable."
        )
    elif reason is None:
        reason = None

    grade_tier = normalize_grade_tier(raw.get("gradeTier"))
    return NormalizedRule(
        category=category,
        grade_tier=grade_tier,
        basis=basis,
        rule_id=generate_rule_id(category, grade_tier, basis),
        page_numbers=_page_numbers(raw.get("pageNumbers")),
        source_quote=_clean_text(raw.get("sourceQuote"), 2000) or "",
        confidence=clamp_confidence(raw.get("confidence")),
        representable=representable,
        unrepresentable_reason=reason,
        sub_category=_clean_text(raw.get("subCategory"), 120),
        currency=(str(raw.get("currency") or "USD").strip().upper() or "USD")[:3],
        max_amount=max_amount,
        limit_expression=limit_expression,
        auto_approve_limit=auto_approve,
        receipt_required_above=parse_amount(raw.get("receiptRequiredAboveUSD")),
        requires_pre_approval=bool(raw.get("requiresPreApproval", False)),
        always_manual=always_manual,
        special_rules=[
            text for text in (_clean_text(s, 500) for s in raw.get("specialRules") or []) if text
        ],
    )


def normalized_rule_from_item(item: Any) -> NormalizedRule:
    """Rebuild a :class:`NormalizedRule` from a persisted ``PolicyRuleProposalItem``.

    Used at approval time, not just at extraction time: a reviewer may have edited an item between
    extraction and approval (``reviewer_edited``), and approval must publish what is stored now, not
    what was first extracted. Going through the same dataclass ``build_ruleset_payload`` already
    consumes keeps the merge-and-publish logic identical for both call sites, rather than a second
    ad-hoc path that could drift from it.
    """
    basis = coerce_basis(item.basis)
    grade_tier = item.grade_tier or ALL_STAFF
    return NormalizedRule(
        category=item.category,
        grade_tier=grade_tier,
        basis=basis,
        # Recomputed rather than read from the stored column: a reviewer may have corrected the
        # category or grade tier since extraction, and the id must describe the rule as it stands
        # now, or approval would publish under an id that no longer matches its own contents.
        rule_id=generate_rule_id(item.category, grade_tier, basis),
        section_index=None,
        source_chunk_ids=tuple(str(cid) for cid in (item.source_chunk_ids or ())),
        page_numbers=tuple(item.page_numbers or ()),
        source_quote=item.source_quote or "",
        confidence=item.confidence if item.confidence is not None else Decimal("0.00"),
        representable=bool(item.representable),
        unrepresentable_reason=item.unrepresentable_reason,
        sub_category=item.sub_category,
        currency=item.currency or "USD",
        max_amount=item.max_amount,
        limit_expression=item.limit_expression,
        auto_approve_limit=item.auto_approve_limit,
        receipt_required_above=item.receipt_required_above,
        requires_pre_approval=bool(item.requires_pre_approval),
        always_manual=bool(item.always_manual),
        special_rules=list(item.special_rules or []),
    )


def normalize_extraction(
    payload: Mapping[str, Any], *, known_categories: Sequence[str]
) -> list[NormalizedRule]:
    """Normalize every rule in a validated extractor payload, in document order."""
    rules = payload.get("rules") or []
    return [
        normalize_rule(raw, known_categories=known_categories)
        for raw in rules
        if isinstance(raw, Mapping)
    ]


def _page_numbers(value: Any) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    pages: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            continue
        page = int(item)
        if page > 0 and page not in pages:
            pages.append(page)
    return tuple(sorted(pages))


def _clean_text(value: Any, limit: int) -> Optional[str]:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text[:limit] if text else None


# --- publishable payload ------------------------------------------------------


def build_ruleset_payload(
    approved: Sequence[NormalizedRule],
    *,
    current_active: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build the full ruleset to submit to ``PolicyRuleService.replace_ruleset``.

    ``current_active`` is the existing active ruleset as
    :func:`app.services.mappers.policy_rule_to_dict` returns it. Rules the proposal does not touch
    are carried through unchanged, because omission means retirement.

    Unrepresentable rules are excluded: they have no category the engine can match, so publishing
    them would create a rule that never fires while displacing nothing.
    """
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for rule in current_active:
        category = str(rule.get("category") or "").strip()
        if not category:
            continue
        key = _category_key(category)
        merged[key] = dict(rule)
        order.append(key)

    for category, group in _group_by_category(approved).items():
        if category not in merged:
            order.append(category)
        merged[category] = _merge_group(group, existing=merged.get(category))

    return [merged[key] for key in dict.fromkeys(order) if key in merged]


def _group_by_category(rules: Sequence[NormalizedRule]) -> dict[str, list[NormalizedRule]]:
    grouped: dict[str, list[NormalizedRule]] = {}
    for rule in rules:
        if not rule.representable or not rule.category:
            continue
        grouped.setdefault(_category_key(rule.category), []).append(rule)
    return grouped


def _merge_group(
    group: Sequence[NormalizedRule], *, existing: Optional[Mapping[str, Any]]
) -> dict[str, Any]:
    """Collapse one category's rows into the single rule ``policy_rules`` can hold.

    The published cap is the **highest** of the group's amounts, with every band recorded in
    ``actions.capUsdByGradeTier``. Highest rather than lowest because the cap is a ceiling the
    engine compares against: publishing the lowest band would reject a senior employee's
    legitimate claim, whereas the per-band detail needed to be stricter is preserved for the
    engine to consult.
    """
    primary = group[0]
    amounts = [r.max_amount for r in group if r.max_amount is not None]
    auto_limits = [r.auto_approve_limit for r in group if r.auto_approve_limit is not None]
    receipts = [r.receipt_required_above for r in group if r.receipt_required_above is not None]
    tiers = list(dict.fromkeys(r.grade_tier for r in group))

    payload: dict[str, Any] = {
        "category": primary.category,
        "name": f"{primary.category} Policy",
        "currency": primary.currency,
        "gradeTier": tiers[0] if len(tiers) == 1 else GRADE_DEPENDENT,
        # A number when the group has one; otherwise the prose the document used, which
        # `replace_ruleset` routes to `limit_expression` via its number-or-prose union.
        "maxAmountUSD": (
            float(max(amounts)) if amounts else (primary.limit_expression or None)
        ),
        # No auto-approve threshold anywhere in the group means never auto-approve. The lowest
        # threshold governs: a band that must be reviewed at $50 cannot be widened by a sibling.
        "autoApproveLimitUSD": float(min(auto_limits)) if auto_limits else None,
        # Likewise the lowest receipt threshold — the strictest documentation requirement wins.
        "receiptRequiredAboveUSD": float(min(receipts)) if receipts else 0.0,
        "requiresPreApproval": any(r.requires_pre_approval for r in group),
        "specialRules": list(
            dict.fromkeys(text for rule in group for text in rule.special_rules)
        ),
        "actions": _actions_for(group),
    }
    if existing is not None:
        # Keep the durable identity of the rule being replaced so a published version supersedes
        # the right code rather than creating a parallel one.
        for carried in ("code", "country", "priority", "description"):
            if existing.get(carried) is not None:
                payload.setdefault(carried, existing[carried])
    return payload


def _actions_for(group: Sequence[NormalizedRule]) -> dict[str, Any]:
    """The declarative payload, imitating the seeded ``actions`` convention.

    Stored, not yet evaluated — ``policy_rules.conditions``/``actions`` are documented as inert
    until a later rule engine reads them. Writing the per-grade and per-basis detail here is what
    makes a merged rule losslessly reconstructable.
    """
    actions: dict[str, Any] = {"routeOnBreach": "Manager_Review"}

    by_tier = {
        rule.grade_tier: float(rule.max_amount)
        for rule in group
        if rule.max_amount is not None
    }
    if len(by_tier) > 1:
        actions["capUsdByGradeTier"] = by_tier

    bases = list(dict.fromkeys(rule.basis.value for rule in group))
    if bases:
        actions["limitBasis"] = bases[0] if len(bases) == 1 else bases

    if any(rule.always_manual for rule in group):
        actions["autoApprove"] = False
    if any(rule.requires_pre_approval for rule in group):
        actions["requirePreApproval"] = True

    expressions = [rule.limit_expression for rule in group if rule.limit_expression]
    if expressions:
        actions["limitExpressions"] = list(dict.fromkeys(expressions))
    return actions


__all__ = [
    "ALL_STAFF",
    "GRADE_DEPENDENT",
    "NON_SCALAR_BASES",
    "NormalizedRule",
    "build_ruleset_payload",
    "clamp_confidence",
    "coerce_basis",
    "generate_rule_id",
    "normalize_category",
    "normalize_dashes",
    "normalize_extraction",
    "normalize_grade_tier",
    "normalize_rule",
    "normalized_rule_from_item",
    "parse_amount",
]
