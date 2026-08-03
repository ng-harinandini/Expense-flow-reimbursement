"""Bedrock LLM provider tests.

Fully offline: every test either exercises pure request assembly or injects a fake client into
``provider._client``, so nothing here needs AWS credentials or the ``anthropic`` package to be
importable at call time. The one live test lives at the bottom and is opt-in via an env var,
matching the convention ``test_auth_login.py`` established for the live Cognito login.

The four constraint tests (no sampling parameter, no ``budget_tokens``, adaptive thinking,
``output_config.format`` instead of a forced tool call) are the load-bearing ones: each encodes a
rule that returns a 400 from the real API, so a regression there is invisible until a live call
fails.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from app.ai.core.errors import (
    FeatureDisabledError,
    ProviderError,
    ProviderNotConfiguredError,
)
from app.ai.interfaces.llm import LLMProvider
from app.ai.providers.llm import resolve_llm_provider
from app.ai.providers.llm.bedrock import (
    MAX_NON_STREAMING_TOKENS,
    BedrockAnthropicLLMProvider,
    normalize_bedrock_model_id,
)

RULE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["rules"],
    "properties": {
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["category", "maxAmountUSD"],
                "properties": {
                    "category": {"type": "string"},
                    "maxAmountUSD": {"type": "number"},
                },
            },
        }
    },
}


def _message(
    text: str,
    *,
    stop_reason: str = "end_turn",
    input_tokens: int = 120,
    output_tokens: int = 45,
    stop_details: Optional[Any] = None,
    model: str = "anthropic.claude-sonnet-4-5-20250929-v1:0",
) -> SimpleNamespace:
    """A stand-in for the SDK's ``Message``, carrying only what the provider reads."""
    return SimpleNamespace(
        content=[
            # A thinking block precedes the text on a thinking model; the provider must skip it.
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        model=model,
        stop_reason=stop_reason,
        stop_details=stop_details,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


class _FakeClient:
    """Captures the request body and returns a canned message."""

    def __init__(self, message: Any) -> None:
        self._message = message
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **body: Any) -> Any:
        self.calls.append(body)
        if isinstance(self._message, Exception):
            raise self._message
        return self._message


def _provider(message: Any = None, **kwargs: Any) -> BedrockAnthropicLLMProvider:
    provider = BedrockAnthropicLLMProvider(region="ap-south-1", **kwargs)
    if message is not None:
        provider._client = _FakeClient(message)
    return provider


# --- model id normalization ---------------------------------------------------


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("claude-sonnet-5", "anthropic.claude-sonnet-4-5-20250929-v1:0"),
        ("anthropic.claude-sonnet-4-5-20250929-v1:0", "anthropic.claude-sonnet-4-5-20250929-v1:0"),
        ("  claude-sonnet-5  ", "anthropic.claude-sonnet-4-5-20250929-v1:0"),
        ("", "anthropic.claude-sonnet-4-5-20250929-v1:0"),
        ("anthropic.claude-opus-4-8", "anthropic.claude-opus-4-8"),
    ],
)
def test_model_id_gets_the_bedrock_prefix(configured: str, expected: str) -> None:
    """Bedrock 400s on a bare first-party id, and the error does not say why."""
    assert normalize_bedrock_model_id(configured) == expected


def test_provider_satisfies_the_llm_protocol() -> None:
    assert isinstance(_provider(), LLMProvider)


# --- the four API constraints -------------------------------------------------


def test_request_sends_no_sampling_parameter() -> None:
    """Current Claude models reject a non-default temperature/top_p/top_k outright.

    ``AISettings.LLM_TEMPERATURE`` defaults to 0.0 — itself a non-default value — so forwarding the
    setting would 400 every single request.
    """
    provider = _provider(_message('{"rules": []}'))
    provider.generate("hello", temperature=0.2)

    body = provider._client.calls[0]
    assert "temperature" not in body
    assert "top_p" not in body
    assert "top_k" not in body


def test_request_sends_adaptive_thinking_and_never_budget_tokens() -> None:
    """``budget_tokens`` is removed from the API; depth comes from ``output_config.effort``."""
    provider = _provider(_message("ok"), effort="xhigh")
    provider.generate("hello")

    body = provider._client.calls[0]
    assert body["thinking"] == {"type": "adaptive", "display": "omitted"}
    assert body["output_config"]["effort"] == "xhigh"
    assert "budget_tokens" not in json.dumps(body)


def test_structured_output_uses_output_config_not_a_forced_tool_call() -> None:
    """On Bedrock a forced ``tool_choice`` requires thinking disabled.

    Using strict tool use here would make structured output and thinking mutually exclusive on this
    platform, defeating the point of choosing a thinking model.
    """
    provider = _provider(_message('{"rules": []}'))
    provider.generate_structured("extract", RULE_SCHEMA)

    body = provider._client.calls[0]
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["format"]["schema"] == RULE_SCHEMA
    assert "tools" not in body
    assert "tool_choice" not in body
    # Thinking stays on alongside the structured constraint — the whole point.
    assert body["thinking"]["type"] == "adaptive"


def test_max_tokens_is_clamped_below_the_non_streaming_ceiling() -> None:
    """``max_tokens`` bounds thinking *plus* reply, and a huge value risks an HTTP timeout."""
    provider = _provider(_message("ok"))
    provider.generate("hello", max_output_tokens=10**9)
    assert provider._client.calls[0]["max_tokens"] == MAX_NON_STREAMING_TOKENS


def test_invalid_effort_falls_back_to_high_rather_than_400ing() -> None:
    provider = _provider(_message("ok"), effort="turbo")
    provider.generate("hello")
    assert provider._client.calls[0]["output_config"]["effort"] == "high"


# --- structured output contract ----------------------------------------------


def test_generate_structured_returns_the_parsed_object() -> None:
    payload = {"rules": [{"category": "Meals", "maxAmountUSD": 40.0}]}
    provider = _provider(_message(json.dumps(payload)))

    response = provider.generate_structured("extract", RULE_SCHEMA, prompt_version="v1")

    assert response.structured == payload
    assert response.prompt_version == "v1"
    assert response.provider == "bedrock"
    assert response.usage.input_tokens == 120
    assert response.usage.output_tokens == 45
    # Cost is populated because every inference is billed and audited into ai_inference_logs.
    assert response.cost_usd > 0


def test_generate_structured_rejects_output_that_violates_the_schema() -> None:
    """Clause 4 of the protocol: invalid output is an error, never a half-parsed dict.

    The API constrains generation server-side, but re-validating locally means the guarantee does
    not rest on a single enforcement point that a provider swap could quietly remove.
    """
    provider = _provider(_message('{"rules": [{"category": "Meals"}]}'))  # maxAmountUSD missing

    with pytest.raises(ProviderError) as excinfo:
        provider.generate_structured("extract", RULE_SCHEMA)
    assert "schema validation" in str(excinfo.value)


@pytest.mark.parametrize("text", ["", "not json at all", "[1, 2, 3]"])
def test_generate_structured_rejects_unparseable_replies(text: str) -> None:
    provider = _provider(_message(text))
    with pytest.raises(ProviderError):
        provider.generate_structured("extract", RULE_SCHEMA)


def test_truncated_reply_names_the_setting_to_raise() -> None:
    """A JSON object cut off mid-write is the failure mode of too small a max_tokens."""
    provider = _provider(_message('{"rules": [', stop_reason="max_tokens"))

    with pytest.raises(ProviderError) as excinfo:
        provider.generate_structured("extract", RULE_SCHEMA)
    assert "AI_LLM_MAX_OUTPUT_TOKENS" in str(excinfo.value)


def test_refusal_is_raised_before_content_is_read() -> None:
    """A refusal is a 200 whose content may be empty, so indexing content[0] would break."""
    provider = _provider(
        _message("", stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"))
    )

    with pytest.raises(ProviderError) as excinfo:
        provider.generate("hello")
    assert excinfo.value.details["stopReason"] == "refusal"
    assert excinfo.value.details["category"] == "cyber"


def test_thinking_blocks_are_excluded_from_the_returned_text() -> None:
    provider = _provider(_message("the answer"))
    assert provider.generate("hello").text == "the answer"


def test_transport_failure_does_not_leak_the_prompt() -> None:
    """Prompts carry policy and receipt content; they must never reach a log or an exception."""
    secret = "EMPLOYEE-SECRET-VENDOR-NAME"
    provider = _provider(RuntimeError(f"boom while sending {secret}"))

    with pytest.raises(ProviderError) as excinfo:
        provider.generate(f"extract rules from {secret}")
    assert secret not in str(excinfo.value.details)


# --- resolution ---------------------------------------------------------------


def test_resolve_raises_503_when_no_llm_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """No silent stub: a fabricated limit is indistinguishable from an extracted one."""
    from app.ai.core.config import ai_settings

    monkeypatch.setattr(ai_settings, "LLM_PROVIDER", "none", raising=False)
    with pytest.raises(FeatureDisabledError) as excinfo:
        resolve_llm_provider()
    assert excinfo.value.http_status == 503


def test_resolve_rejects_an_unknown_provider_name(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ai.core.config import ai_settings

    monkeypatch.setattr(ai_settings, "LLM_PROVIDER", "gemini", raising=False)
    with pytest.raises(ProviderNotConfiguredError) as excinfo:
        resolve_llm_provider()
    assert excinfo.value.http_status == 503


def test_missing_region_is_a_configuration_error_not_a_crash() -> None:
    """503 naming the setting, never an ImportError or AttributeError at the call site."""
    provider = BedrockAnthropicLLMProvider(region=None)
    with pytest.raises(ProviderNotConfiguredError) as excinfo:
        provider.generate("hello")
    assert "AI_BEDROCK_REGION" in excinfo.value.remedy


# --- opt-in live check --------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("LIVE_BEDROCK_TEST"),
    reason="Set LIVE_BEDROCK_TEST=1 with AWS credentials to exercise a real Bedrock call.",
)
def test_live_bedrock_structured_call() -> None:
    """Proves the request shape the four constraint tests assert is actually accepted.

    Those tests pin the body we *send*; only a live call proves Bedrock does not 400 on it.
    """
    from app.ai.core.config import ai_settings

    provider = BedrockAnthropicLLMProvider(
        model=ai_settings.LLM_MODEL or "anthropic.claude-sonnet-4-5-20250929-v1:0",
        region=ai_settings.BEDROCK_REGION,
        effort="low",
    )
    response = provider.generate_structured(
        'Return {"rules": [{"category": "Meals", "maxAmountUSD": 40}]} exactly.', RULE_SCHEMA
    )
    assert response.structured is not None
    assert isinstance(response.structured.get("rules"), list)
