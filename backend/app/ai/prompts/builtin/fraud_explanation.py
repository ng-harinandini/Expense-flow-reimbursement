"""Explains why a claim was flagged by the deterministic fraud engine."""

from __future__ import annotations

from app.ai.prompts.builtin.definitions import BuiltinPrompt

FRAUD_EXPLANATION_PROMPT = BuiltinPrompt(
    code="FRAUD_EXPLANATION",
    name="Fraud Flag Explanation",
    description=(
        "Explains a fraud-engine risk score in plain language for a reviewer. The LLM is never the "
        "source of truth here — this only narrates a verdict app.services.fraud_engine already "
        "reached deterministically."
    ),
    template_text=(
        "Claim {{claim_number}} was scored {{risk_score}}/100 ({{risk_level}} risk) by the fraud "
        "engine. The signals that fired: {{fired_signals}}."
    ),
    variables=["claim_number", "risk_score", "risk_level", "fired_signals"],
)

__all__ = ["FRAUD_EXPLANATION_PROMPT"]
