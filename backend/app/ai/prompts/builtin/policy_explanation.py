"""Explains why a claim's category was routed under a specific policy limit."""

from __future__ import annotations

from app.ai.prompts.builtin.definitions import BuiltinPrompt

POLICY_EXPLANATION_PROMPT = BuiltinPrompt(
    code="POLICY_EXPLANATION",
    name="Policy Explanation",
    description=(
        "Explains why a claim's category was routed under a specific policy limit, for use by "
        "KnowledgeService.explain() once an LLM backend is wired (M12/M13)."
    ),
    template_text=(
        "The expense category '{{category}}' has a limit of {{currency}} {{limit}} per "
        "{{policy_period}}. The submitted amount was {{currency}} {{amount}}, which is "
        "{{comparison}} the limit, so the claim was routed to {{routed_status}}."
    ),
    variables=[
        "category", "currency", "limit", "policy_period", "amount", "comparison", "routed_status",
    ],
)

__all__ = ["POLICY_EXPLANATION_PROMPT"]
