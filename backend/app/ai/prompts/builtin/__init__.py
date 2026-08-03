"""Built-in prompt definitions.

Plain data, not auto-registered on import — a DB write must never be a side effect of importing a
module. :func:`register_builtin_prompts` is the explicit, callable entry point that publishes them
through :class:`~app.ai.prompts.registry.PromptRegistry`, exactly as any other prompt would be
published.
"""

from __future__ import annotations

from app.ai.prompts.builtin.definitions import BuiltinPrompt
from app.ai.prompts.builtin.fraud_explanation import FRAUD_EXPLANATION_PROMPT
from app.ai.prompts.builtin.policy_explanation import POLICY_EXPLANATION_PROMPT
from app.ai.prompts.builtin.policy_rule_extraction import POLICY_RULE_EXTRACTION_PROMPT
from app.ai.prompts.builtin.policy_section_boundary import POLICY_SECTION_BOUNDARY_PROMPT
from app.ai.prompts.registry import PromptRegistry
from app.domain.actor import Actor

BUILTIN_PROMPTS: tuple[BuiltinPrompt, ...] = (
    POLICY_EXPLANATION_PROMPT,
    FRAUD_EXPLANATION_PROMPT,
    POLICY_RULE_EXTRACTION_PROMPT,
    POLICY_SECTION_BOUNDARY_PROMPT,
)


def register_builtin_prompts(registry: PromptRegistry, *, actor: Actor) -> list[object]:
    """Publish every built-in prompt through ``registry``. Idempotent in effect (not in version
    number): calling this twice publishes two versions of each, the second identical to the
    first — callers that only want a one-time seed should check ``get_active`` first."""
    return [
        registry.publish(
            prompt.code, name=prompt.name, template_text=prompt.template_text,
            variables=prompt.variables, description=prompt.description, actor=actor,
        )
        for prompt in BUILTIN_PROMPTS
    ]


__all__ = ["BUILTIN_PROMPTS", "register_builtin_prompts"]
