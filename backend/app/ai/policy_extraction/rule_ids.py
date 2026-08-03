"""Stable identifiers for extracted rules.

An extracted rule has no natural key. Its database id is minted per proposal, so the "same" rule
re-extracted from version 2 of a document gets a different id from version 1 and nothing can be
compared across the two. That makes four things impossible: diffing versions, tracking when a limit
changed, detecting that two parts of one document state contradictory values for one rule, and
auditing a published limit back through its history.

A rule id fixes that by being derived from *what the rule is about* rather than when it was read:

    RULE_MEALS_ALLSTAFF_PER_DAY
    RULE_LODGING_L4PLUS_PER_NIGHT
    RULE_FLIGHTS_ALLSTAFF_OTHER

**Derived from the normalized values, deliberately.** ``normalize_rule`` has already folded en
dashes onto hyphens, mapped every "everyone" phrasing onto ``All Staff``, and canonicalized the
category against the configured set — so two extractions that read the same rule with cosmetically
different wording converge on one id. Doing this before normalization would make the id as unstable
as the raw model output.

**What is deliberately *not* in the id:** the amount. An id that encoded ``40`` would change the
moment the limit changed, which is precisely the event the id exists to let you observe. Category,
grade tier and basis identify the *slot*; the amount is the value in it.
"""

from __future__ import annotations

import re

from app.ai.core.enums import LimitBasis

_PREFIX = "RULE"
#: ``policy_rule_proposal_items.rule_id`` is ``String(160)``; components are trimmed to leave room
#: for the prefix and separators even in the worst case.
_MAX_COMPONENT = 40
_UNKNOWN = "UNKNOWN"


def generate_rule_id(category: str, grade_tier: str, basis: LimitBasis | str) -> str:
    """Build the stable id for one rule slot.

    Pure and total: any input, including empty strings, produces a usable id rather than raising.
    An extraction that read a rule badly still needs an id — it has to be storable, reviewable and
    rejectable, and an exception here would discard the rest of the extraction with it.
    """
    return "_".join((_PREFIX, _component(category), _component(grade_tier), _basis(basis)))


def _basis(basis: LimitBasis | str) -> str:
    """The basis, kept verbatim — it is a controlled vocabulary, not free text.

    Unlike a category or grade tier, a :class:`LimitBasis` value is already
    ``UPPER_SNAKE_CASE`` and safe. Passing it through ``_component`` would strip its underscore and
    turn ``PER_DAY`` into ``PERDAY``, making every id harder to read for no gain.
    """
    value = basis.value if isinstance(basis, LimitBasis) else str(basis or "")
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "", value).upper().strip("_")
    return (cleaned or _UNKNOWN)[:_MAX_COMPONENT]


def _component(value: str) -> str:
    """Uppercase, alphanumeric-only, with ``+`` spelled out.

    Separators are dropped rather than preserved because a category or grade tier is free text: the
    same tier can arrive as ``L1-L3``, ``L1 - L3`` or ``L1–L3``, and all three must produce one id.

    ``+`` survives as ``PLUS`` rather than being stripped, because ``L4+`` and ``L4`` are different
    grade tiers — collapsing them would merge two rules under one id, the one failure mode that
    would make conflict detection report a conflict that does not exist.
    """
    text = str(value or "").replace("+", "PLUS")
    cleaned = re.sub(r"[^A-Za-z0-9]+", "", text).upper()
    return (cleaned or _UNKNOWN)[:_MAX_COMPONENT]


__all__ = ["generate_rule_id"]
