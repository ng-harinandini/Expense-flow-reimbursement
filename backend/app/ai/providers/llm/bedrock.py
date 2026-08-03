"""Anthropic Claude on Amazon Bedrock — the first implementation of :class:`LLMProvider`.

Uses the official Anthropic SDK's Bedrock client (``AnthropicBedrockMantle``) rather than a
hand-rolled ``boto3.invoke_model`` body. That choice is what makes ``generate_structured`` viable:
the Messages-API surface exposes ``output_config.format``, which constrains the reply to a JSON
schema server-side, and adaptive thinking, both of which would otherwise have to be assembled and
parsed by hand against a raw InvokeModel payload.

**Four API constraints are encoded here, not left to the caller.** Each one is a 400 (or a silent
behaviour change) if got wrong, and three of them differ from what older Claude models accepted:

1. ``budget_tokens`` is **removed** on the current models. Thinking depth is controlled by
   ``output_config.effort``, not a token budget, so this provider sends
   ``thinking={"type": "adaptive"}`` and an effort level. There is no knob for a thinking budget
   because the API no longer has one.
2. **Non-default ``temperature``/``top_p``/``top_k`` are rejected.** ``AISettings.LLM_TEMPERATURE``
   defaults to ``0.0``, which is *not* the API default — forwarding it would 400 every request.
   This provider therefore never sends a sampling parameter, and deliberately ignores that setting;
   see :func:`_reject_sampling_settings` for why the setting still exists.
3. **On Bedrock specifically, a forced ``tool_choice`` requires thinking to be disabled.** So
   structured output here goes through ``output_config.format``, never through strict tool use —
   otherwise "structured" and "thinking" would be mutually exclusive on this platform, and the
   whole point of choosing a thinking model would be lost.
4. Model ids carry an ``anthropic.`` prefix on Bedrock (``anthropic.claude-sonnet-4-5-20250929-v1:0``). A bare
   first-party id 400s. :func:`normalize_bedrock_model_id` adds it when it is missing so an
   operator who sets the first-party string in ``.env`` still gets a working request.

Task budgets, fast mode, and the Files API are **not** available on Bedrock; nothing here reaches
for them.

Like every adapter under ``app/ai/providers``, the import is guarded and the constructor is cheap:
this class must be *registerable* on a host with no credentials and no ``anthropic`` package. An
absent dependency becomes :class:`ProviderNotConfiguredError` (503, naming the remedy) at the point
of use, never an ``ImportError`` at startup.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Optional, Sequence

from app.ai.core.errors import (
    ProviderError,
    ProviderNotConfiguredError,
    ProviderTimeoutError,
)
from app.ai.core.types import LLMResponse, TokenUsage
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Bedrock requires a provider prefix on every Anthropic model id.
_BEDROCK_PREFIX = "anthropic."

#: Default model. Sonnet 5 is a thinking model with adaptive thinking on by default, and is the
#: tier this workload was specified against; override with ``AI_LLM_MODEL``.
DEFAULT_MODEL = "anthropic.claude-sonnet-4-5-20250929-v1:0"

#: Effort levels the API accepts inside ``output_config``.
VALID_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})

#: ``max_tokens`` bounds thinking *plus* reply text. With adaptive thinking on, the SDK's 1024-ish
#: convention truncates a structured extraction mid-object, so this provider floors it much higher.
#: Above roughly this value a non-streaming request risks an HTTP timeout and must stream instead.
MAX_NON_STREAMING_TOKENS = 16_000


def normalize_bedrock_model_id(model: str) -> str:
    """Return ``model`` with the ``anthropic.`` prefix Bedrock requires.

    Accepts either form so ``AI_LLM_MODEL=claude-sonnet-5`` and
    ``AI_LLM_MODEL=anthropic.claude-sonnet-4-5-20250929-v1:0`` both work; a bare first-party id would otherwise 400
    on Bedrock and the error message does not make the missing prefix obvious.
    """
    cleaned = (model or "").strip()
    if not cleaned:
        return DEFAULT_MODEL
    if cleaned.startswith(_BEDROCK_PREFIX):
        return cleaned
    return f"{_BEDROCK_PREFIX}{cleaned}"


class BedrockAnthropicLLMProvider:
    """Claude on Amazon Bedrock. Advisory only — it may explain, never decide.

    The protocol's hardest clause applies in full (:mod:`app.ai.interfaces.llm`): if this call times
    out, errors, or is switched off, every claim must still receive a correct status. Callers get an
    exception they are expected to absorb, and no code path from here reaches ``ClaimService``.
    """

    name = "bedrock"

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        region: Optional[str] = None,
        effort: str = "high",
        timeout_seconds: float = 45.0,
        max_output_tokens: int = MAX_NON_STREAMING_TOKENS,
        thinking_display: str = "omitted",
        cost_per_million_input_usd: float = 3.0,
        cost_per_million_output_usd: float = 15.0,
    ) -> None:
        self._model = normalize_bedrock_model_id(model)
        self._region = region
        self._effort = effort if effort in VALID_EFFORTS else "high"
        self._timeout = timeout_seconds
        self._max_output_tokens = max(1, min(int(max_output_tokens), MAX_NON_STREAMING_TOKENS))
        self._thinking_display = (
            thinking_display if thinking_display in {"omitted", "summarized"} else "omitted"
        )
        self._cost_in = cost_per_million_input_usd
        self._cost_out = cost_per_million_output_usd
        self._client: Any = None

    @property
    def model(self) -> str:
        return self._model

    # --- availability --------------------------------------------------------

    def is_available(self) -> bool:
        """True when the SDK is installed *and* AWS credentials resolve. No network call."""
        try:
            import anthropic  # noqa: F401, PLC0415
        except ImportError:
            return False
        try:
            import boto3  # noqa: PLC0415
        except ImportError:
            return False
        try:
            return boto3.Session().get_credentials() is not None
        except Exception:  # pragma: no cover - environment dependent
            return False

    def _bedrock(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from anthropic import AnthropicBedrockMantle
        except ImportError as exc:
            raise ProviderNotConfiguredError(
                provider=self.name, kind="LLM",
                remedy='Install the Bedrock extra: pip install "anthropic[bedrock]".',
            ) from exc
        if not self._region:
            raise ProviderNotConfiguredError(
                provider=self.name, kind="LLM",
                remedy="Set AI_BEDROCK_REGION to the region hosting the Claude model.",
            )
        try:
            self._client = AnthropicBedrockMantle(
                aws_region=self._region, timeout=self._timeout,
            )
        except Exception as exc:
            raise ProviderNotConfiguredError(
                provider=self.name, kind="LLM",
                remedy=(
                    "Could not create a Bedrock client. Check AWS credentials and "
                    f"AI_BEDROCK_REGION. Cause: {type(exc).__name__}: {exc}"
                ),
            ) from exc
        return self._client

    # --- request assembly ----------------------------------------------------

    def _request(
        self,
        prompt: str,
        *,
        system: Optional[str],
        max_output_tokens: int,
        stop: Sequence[str] = (),
        output_format: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        """Build the request body.

        Deliberately absent: ``temperature``, ``top_p``, ``top_k`` (rejected by the model) and
        ``budget_tokens`` (removed from the API). ``max_tokens`` covers thinking *and* reply text.
        """
        output_config: dict[str, Any] = {"effort": self._effort}
        if output_format is not None:
            output_config["format"] = dict(output_format)

        body: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max(1, min(int(max_output_tokens), MAX_NON_STREAMING_TOKENS)),
            # Adaptive is the only on-mode; depth comes from `effort` above.
            "thinking": {"type": "adaptive", "display": self._thinking_display},
            "output_config": output_config,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system
        if stop:
            body["stop_sequences"] = list(stop)
        return body

    def _invoke(self, body: dict[str, Any]) -> tuple[Any, int]:
        client = self._bedrock()
        started = time.perf_counter()
        try:
            message = client.messages.create(**body)
        except Exception as exc:
            name = type(exc).__name__
            if "Timeout" in name:
                raise ProviderTimeoutError(self.name, self._timeout) from exc
            # Never echo the prompt: it can carry policy or receipt content.
            raise ProviderError(self.name, f"{name}: {str(exc)[:300]}") from exc
        return message, int((time.perf_counter() - started) * 1000)

    def _to_response(
        self,
        message: Any,
        *,
        latency_ms: int,
        prompt_version: Optional[str],
        structured: Optional[Mapping[str, Any]] = None,
    ) -> LLMResponse:
        usage = TokenUsage(
            input_tokens=int(getattr(message.usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(message.usage, "output_tokens", 0) or 0),
        )
        cost = (
            (usage.input_tokens / 1_000_000) * self._cost_in
            + (usage.output_tokens / 1_000_000) * self._cost_out
        )
        return LLMResponse(
            text=_text_of(message),
            provider=self.name,
            model=str(getattr(message, "model", self._model)),
            prompt_version=prompt_version,
            structured=structured,
            usage=usage,
            latency_ms=latency_ms,
            cost_usd=cost,
            finish_reason=getattr(message, "stop_reason", None),
        )

    # --- LLMProvider ---------------------------------------------------------

    def generate(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        max_output_tokens: int = MAX_NON_STREAMING_TOKENS,
        temperature: float = 0.0,
        prompt_version: Optional[str] = None,
        stop: Sequence[str] = (),
    ) -> LLMResponse:
        """Free-text generation.

        ``temperature`` is accepted to satisfy the protocol and then **ignored** — the current
        models reject a non-default sampling parameter outright. Steering belongs in the prompt.
        """
        _reject_sampling_settings(temperature)
        body = self._request(
            prompt, system=system, max_output_tokens=max_output_tokens, stop=stop,
        )
        message, latency_ms = self._invoke(body)
        _guard_refusal(self.name, message)
        return self._to_response(message, latency_ms=latency_ms, prompt_version=prompt_version)

    def generate_structured(
        self,
        prompt: str,
        schema: Mapping[str, Any],
        *,
        system: Optional[str] = None,
        max_output_tokens: int = MAX_NON_STREAMING_TOKENS,
        prompt_version: Optional[str] = None,
    ) -> LLMResponse:
        """Generate JSON conforming to ``schema``, validated before it is returned.

        Uses ``output_config.format`` rather than a forced tool call: on Bedrock a forced
        ``tool_choice`` demands ``thinking: {"type": "disabled"}``, which would make structured
        output and thinking mutually exclusive on this platform.

        The API constrains generation to the schema, and
        :func:`app.ai.reasoning.structured_output.validate` re-checks the parsed object — clause 4
        of the protocol says invalid output is a ``ProviderError``, never a half-parsed dict handed
        onward, and one enforcement point that can be bypassed is not an enforcement point.
        """
        from app.ai.reasoning.structured_output import validate

        body = self._request(
            prompt,
            system=system,
            max_output_tokens=max_output_tokens,
            output_format={"type": "json_schema", "schema": dict(schema)},
        )
        message, latency_ms = self._invoke(body)
        _guard_refusal(self.name, message)

        if getattr(message, "stop_reason", None) == "max_tokens":
            raise ProviderError(
                self.name,
                "reply hit max_tokens before the JSON object closed; raise "
                "AI_LLM_MAX_OUTPUT_TOKENS or lower AI_LLM_EFFORT",
                details={"maxTokens": body["max_tokens"]},
            )

        parsed = _parse_json(self.name, _text_of(message))
        ok, errors = validate(parsed, schema)
        if not ok:
            raise ProviderError(
                self.name,
                "structured reply failed schema validation: " + "; ".join(errors[:5]),
                details={"violations": errors[:20]},
            )
        return self._to_response(
            message, latency_ms=latency_ms, prompt_version=prompt_version, structured=parsed,
        )


# --- helpers -----------------------------------------------------------------


def _reject_sampling_settings(temperature: float) -> None:
    """Log once when a caller passes a sampling value that the model would reject.

    Silently dropping it would be worse than either alternative: an operator who sets
    ``AI_LLM_TEMPERATURE=0.2`` expecting determinism deserves to know it had no effect. Raising
    would be worse still — an advisory provider must not turn a config nit into a failed request.
    """
    if temperature not in (0.0, 1.0):
        logger.warning(
            "ai.llm.sampling_ignored",
            extra={"temperature": temperature, "reason": "rejected by current Claude models"},
        )


def _guard_refusal(provider: str, message: Any) -> None:
    """Turn a safety refusal into a provider error before ``content`` is read.

    A refusal is a successful HTTP 200 whose ``content`` may be empty, so code that indexes
    ``content[0]`` breaks on it. Extraction from an expense policy is not the kind of request that
    trips a classifier, but the check costs nothing and the failure mode is otherwise a confusing
    ``IndexError`` far from the cause.
    """
    if getattr(message, "stop_reason", None) != "refusal":
        return
    details = getattr(message, "stop_details", None)
    category = getattr(details, "category", None) if details else None
    raise ProviderError(
        provider,
        "the model declined this request",
        details={"stopReason": "refusal", "category": category},
    )


def _text_of(message: Any) -> str:
    """Concatenate the ``text`` blocks of a reply, skipping thinking and tool blocks."""
    parts: list[str] = []
    for block in getattr(message, "content", None) or ():
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    return "".join(parts).strip()


def _parse_json(provider: str, text: str) -> dict[str, Any]:
    import json

    if not text:
        raise ProviderError(provider, "structured reply was empty")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderError(provider, f"structured reply was not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ProviderError(
            provider, f"structured reply was {type(parsed).__name__}, expected an object"
        )
    return parsed


__all__ = [
    "DEFAULT_MODEL",
    "MAX_NON_STREAMING_TOKENS",
    "VALID_EFFORTS",
    "BedrockAnthropicLLMProvider",
    "normalize_bedrock_model_id",
]
