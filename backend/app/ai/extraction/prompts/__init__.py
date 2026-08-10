"""Selectable prompt-template versions for policy rule extraction.

Each sibling module (``v1``, ``v2``, ...) exports one ``PROMPT_TEMPLATE`` string using the same
``<<PLACEHOLDER>>`` sentinels ``PolicyRuleExtractor._build_prompt`` fills in. Which one is active
is a runtime choice — ``AI_RULE_EXTRACTION_PROMPT_VERSION`` (see ``app.ai.core.config``) — so a
prompt-wording change or regression can be rolled back, or a new version trialled, without a code
deploy touching the extraction pipeline itself.
"""

from __future__ import annotations

from app.ai.extraction.prompts import v1, v2, v3

_VERSIONS: dict[str, str] = {
    "v1": v1.PROMPT_TEMPLATE,
    "v2": v2.PROMPT_TEMPLATE,
    "v3": v3.PROMPT_TEMPLATE,
}


def get_prompt_template(version: str) -> str:
    """Return the prompt text for ``version``. Raises ``ValueError`` on an unknown version."""
    try:
        return _VERSIONS[version]
    except KeyError:
        available = ", ".join(sorted(_VERSIONS))
        raise ValueError(
            f"Unknown rule-extraction prompt version '{version}'. Available: {available}."
        ) from None


__all__ = ["get_prompt_template"]
