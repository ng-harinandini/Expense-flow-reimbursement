"""LLM provider contract — advisory only.

**The hardest constraint in this codebase lives here.** An implementation of this protocol may
explain, summarize, classify and recommend. It may never decide. The deterministic policy and fraud
engines own every claim outcome (ADR-003 §C2), and this package cannot reach them:
``app/ai/**`` is forbidden from importing ``ClaimService`` or ``ClaimRepository``, and
``tests/test_ai_isolation.py`` asserts a claim's status is unwritable from AI code.

The practical property that makes an LLM safe inside a finance workflow: **if this call times out,
errors, or is switched off, the claim still receives a correct status.** Every call site must be
written so that is true.

Contract:

1. ``generate`` never raises for an ordinary refusal or empty answer — it returns an
   :class:`~app.ai.core.types.LLMResponse` whose ``text`` may be empty. Only transport failures
   raise (``ProviderError`` / ``ProviderTimeoutError``).
2. Token usage, latency and cost are always populated, because every inference is billed and audited
   into ``ai_inference_logs``.
3. ``prompt_version`` is echoed back on the response. An explanation that cannot be tied to the
   prompt that produced it is not reproducible, which makes it useless as evidence.
4. ``generate_structured`` validates against the supplied JSON schema **before returning**. Invalid
   model output is a ``ProviderError``, never a half-parsed dict handed onward.
5. Prompts are not logged verbatim (they contain receipt and personal data); only redacted,
   structured summaries are — the convention ``ai_inference_logs`` already documents.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Protocol, Sequence, runtime_checkable

from app.ai.core.types import LLMResponse


@runtime_checkable
class LLMProvider(Protocol):
    """Generates text. Never authoritative over a business decision."""

    name: str

    @property
    def model(self) -> str:
        ...

    def is_available(self) -> bool:
        ...

    def generate(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        max_output_tokens: int = 1024,
        temperature: float = 0.0,
        prompt_version: Optional[str] = None,
        stop: Sequence[str] = (),
    ) -> LLMResponse:
        ...

    def generate_structured(
        self,
        prompt: str,
        schema: Mapping[str, Any],
        *,
        system: Optional[str] = None,
        max_output_tokens: int = 1024,
        prompt_version: Optional[str] = None,
    ) -> LLMResponse:
        """Generate output conforming to ``schema``, validated before it is returned.

        ``LLMResponse.structured`` holds the parsed object. Callers must consume that rather than
        re-parsing ``text``, so schema enforcement cannot be bypassed downstream.
        """
        ...


__all__ = ["LLMProvider"]
