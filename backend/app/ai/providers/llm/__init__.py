"""LLM provider adapters and their registration.

Mirrors ``app/ai/providers/rerank/__init__.py``: every factory is lazy, so registration succeeds on
a host with no credentials and no ``anthropic`` package.

**One deliberate difference from the embedding and rerank registries: there is no fallback.** Those
two degrade — a missing embedding provider falls back to the deterministic one so retrieval still
works, just without semantics. An LLM has no such equivalent: a stub that invents policy limits
would be far worse than an outright 503, because a fabricated ``$40 / day`` is indistinguishable
from an extracted one once it is sitting in a proposal row. So when ``AI_LLM_PROVIDER`` is unset or
the configured provider is unusable, :func:`resolve_llm_provider` raises and the caller returns 503.

This is the same reasoning the platform applies to claim decisions (ADR-003 §5, option C2): the LLM
advises, and when it cannot advise, nothing silently takes its place.
"""

from app.ai.providers.llm.bedrock import BedrockAnthropicLLMProvider

#: ``AI_LLM_PROVIDER`` values that mean "no LLM configured".
DISABLED = frozenset({"", "none", "off", "disabled"})


def register_llm_providers() -> None:
    """Register every LLM adapter. Called once by the composition root."""
    from app.ai.core.config import ai_settings as s
    from app.ai.interfaces.llm import LLMProvider
    from app.ai.registry.registry import llm_registry as reg

    reg.register(
        "bedrock",
        lambda: BedrockAnthropicLLMProvider(
            model=s.LLM_MODEL or "anthropic.claude-sonnet-4-5-20250929-v1:0",
            region=s.BEDROCK_REGION,
            effort=s.LLM_EFFORT,
            timeout_seconds=s.LLM_TIMEOUT_SECONDS,
            max_output_tokens=s.LLM_MAX_OUTPUT_TOKENS,
            thinking_display=s.LLM_THINKING_DISPLAY,
        ),
        protocol=LLMProvider,
        description=(
            "Anthropic Claude on Amazon Bedrock via the official SDK. Adaptive thinking; "
            "structured output through output_config.format."
        ),
        metadata={"semantic": True, "local": False, "requiresCredentials": True},
        replace=True,
    )


def resolve_llm_provider():
    """The configured LLM provider.

    Raises :class:`~app.ai.core.errors.FeatureDisabledError` (503) when no provider is configured,
    and :class:`~app.ai.core.errors.ProviderNotConfiguredError` (503) when the configured one is
    registered but unusable. Both name the setting to change — an LLM-backed feature that is off is
    a configuration state, not a bug, and must never surface as a 500.
    """
    from app.ai.core.config import ai_settings as s
    from app.ai.core.errors import FeatureDisabledError, ProviderNotConfiguredError
    from app.ai.registry.registry import llm_registry as reg

    configured = (s.LLM_PROVIDER or "").strip().lower()
    if configured in DISABLED:
        raise FeatureDisabledError("AI_LLM_PROVIDER")

    if not reg.names():
        register_llm_providers()

    if not reg.is_registered(configured):
        raise ProviderNotConfiguredError(
            provider=configured, kind="LLM",
            remedy=(
                "AI_LLM_PROVIDER names an unknown provider. Registered: "
                f"{', '.join(reg.names()) or 'none'}."
            ),
        )

    provider = reg.resolve(configured)
    if not provider.is_available():
        raise ProviderNotConfiguredError(
            provider=configured, kind="LLM",
            remedy=(
                'Install the Bedrock extra (pip install "anthropic[bedrock]"), set '
                "AI_BEDROCK_REGION, and make AWS credentials resolvable."
            ),
        )
    return provider


__all__ = [
    "DISABLED",
    "BedrockAnthropicLLMProvider",
    "register_llm_providers",
    "resolve_llm_provider",
]
