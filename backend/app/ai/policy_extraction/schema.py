"""The JSON schema the extractor constrains the model to.

Hand-written rather than generated, following the convention in
:mod:`app.ai.tools.builtin.schemas`, and deliberately shaped to mirror
:class:`app.schemas.schemas.PolicyRuleDefinitionSchema`'s **camelCase** keys. That alignment is the
point: an approved item can be handed to ``PolicyRuleService.replace_ruleset`` without a second
translation layer that could drift from the wire contract.

Beyond those keys the schema adds the fields a *proposal* needs and live configuration does not:

    basis                    what the amount is measured per — see :class:`LimitBasis`
    limitExpression          the document's wording when no single number can express the limit
    alwaysManual             the "— (always manual)" sentinel, kept distinct from a 0 threshold
    pageNumbers, sourceQuote the evidence a reviewer checks the numbers against
    confidence               how sure the model is, so a weak reading is visibly weak
    representable            false when the rule cannot map onto the closed category set

**Two constraints on what this schema may contain.** The API's structured-output mode rejects
numerical and string constraints (``minimum``, ``maxLength``, and friends) and requires
``additionalProperties: false`` on every object; and the local validator
(:func:`app.ai.reasoning.structured_output.validate`) understands only ``type``/``required``/
``properties``/``items``/``enum``. Everything here is therefore expressible in both, and range
checks such as clamping ``confidence`` to 0..1 happen in :mod:`normalization` instead.
"""

from __future__ import annotations

from typing import Any

from app.ai.core.enums import LimitBasis

#: Nullable numeric. The list form is how JSON Schema spells "or null"; a model emits an explicit
#: ``null`` for an absent amount at least as often as it omits the key, and both must be accepted.
_NULLABLE_NUMBER: dict[str, Any] = {"type": ["number", "null"]}
_NULLABLE_STRING: dict[str, Any] = {"type": ["string", "null"]}

RULE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    # Only the fields with no defensible default are required. An amount is *not* required: a rule
    # whose limit is a formula legitimately has none, and demanding one would push the model into
    # inventing a number to satisfy the schema.
    "required": [
        "category",
        "gradeTier",
        "basis",
        "pageNumbers",
        "sourceQuote",
        "confidence",
        "representable",
    ],
    "properties": {
        # --- keys that mirror PolicyRuleDefinitionSchema ---
        "category": {"type": "string"},
        "subCategory": _NULLABLE_STRING,
        "gradeTier": {"type": "string"},
        "currency": _NULLABLE_STRING,
        "maxAmountUSD": _NULLABLE_NUMBER,
        "autoApproveLimitUSD": _NULLABLE_NUMBER,
        "receiptRequiredAboveUSD": _NULLABLE_NUMBER,
        "requiresPreApproval": {"type": "boolean"},
        "specialRules": {"type": "array", "items": {"type": "string"}},
        # --- extraction-only fields ---
        "limitExpression": _NULLABLE_STRING,
        # Distinct from `autoApproveLimitUSD: 0`. Zero is a real threshold that permits nothing;
        # "always manual" is the absence of a threshold. Collapsing them loses the difference.
        "alwaysManual": {"type": "boolean"},
        "basis": {"type": "string", "enum": [b.value for b in LimitBasis]},
        "pageNumbers": {"type": "array", "items": {"type": "integer"}},
        "sourceQuote": {"type": "string"},
        "confidence": {"type": "number"},
        "representable": {"type": "boolean"},
        "unrepresentableReason": _NULLABLE_STRING,
    },
}

POLICY_RULE_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rules"],
    "properties": {
        "rules": {"type": "array", "items": RULE_ITEM_SCHEMA},
        # Anything a reviewer must decide: an unstated effective date, a conflict between two
        # sections, a limit the model could not classify. Surfaced on the proposal, not swallowed.
        "reviewNotes": _NULLABLE_STRING,
        # Policy templates commonly ship with a placeholder effective date. When that is what the
        # document says, this comes back null and the approver supplies the real date at publish
        # time — the service refuses to guess one.
        "documentEffectiveDate": _NULLABLE_STRING,
    },
}


__all__ = [
    "POLICY_RULE_EXTRACTION_SCHEMA",
    "RULE_ITEM_SCHEMA",
]
