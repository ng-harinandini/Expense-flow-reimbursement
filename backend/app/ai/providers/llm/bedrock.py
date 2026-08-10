"""Amazon Bedrock Runtime adapter for Google's Gemma instruction model.

The adapter is intentionally lazy: importing the AI package must continue to work on machines
that do not have boto3 or AWS credentials (for example, local development and CI).  Credentials
are resolved by boto3's normal chain, including the Bedrock bearer token environment variable
supported by recent botocore releases.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from app.ai.core.errors import ProviderError, ProviderNotConfiguredError, ProviderTimeoutError

PROVIDER_NAME = "bedrock"


class BedrockGemmaProvider:
    """Small text-generation adapter around ``bedrock-runtime.invoke_model``."""

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        model: Optional[str] = None,
        region: Optional[str] = None,
        timeout_seconds: float = 45.0,
        max_output_tokens: int = 8192,
        temperature: float = 0.0,
        client: Any = None,
    ) -> None:
        self._model = model
        self._region = region
        self._timeout = timeout_seconds
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature
        self._client = client

    @property
    def model(self) -> Optional[str]:
        return self._model

    def is_available(self) -> bool:
        if self._client is not None:
            return True
        try:
            import boto3  # noqa: PLC0415
        except ImportError:
            return False
        # Bedrock API keys are represented by AWS_BEARER_TOKEN_BEDROCK.  Otherwise use the
        # standard boto3 credential chain (environment, shared config, role, etc.).
        if os.getenv("AWS_BEARER_TOKEN_BEDROCK"):
            return True
        try:
            return boto3.Session(region_name=self._region).get_credentials() is not None
        except Exception:
            return False

    def _bedrock(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import boto3  # noqa: PLC0415
            from botocore.config import Config  # noqa: PLC0415
        except ImportError as exc:
            raise ProviderNotConfiguredError(
                provider=PROVIDER_NAME,
                kind="RULE_EXTRACTION",
                remedy="Install boto3: pip install boto3.",
            ) from exc

        if not self._region and not (os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")):
            raise ProviderNotConfiguredError(
                provider=PROVIDER_NAME,
                kind="RULE_EXTRACTION",
                remedy="Set AI_BEDROCK_REGION (for example, us-east-1).",
            )
        if not os.getenv("AWS_BEARER_TOKEN_BEDROCK"):
            try:
                if boto3.Session(region_name=self._region).get_credentials() is None:
                    raise ProviderNotConfiguredError(
                        provider=PROVIDER_NAME,
                        kind="RULE_EXTRACTION",
                        remedy=(
                            "Configure AWS credentials or set AWS_BEARER_TOKEN_BEDROCK, and grant "
                            "Bedrock model access."
                        ),
                    )
            except ProviderNotConfiguredError:
                raise
            except Exception as exc:
                raise ProviderNotConfiguredError(
                    provider=PROVIDER_NAME,
                    kind="RULE_EXTRACTION",
                    remedy=f"Could not resolve AWS credentials: {type(exc).__name__}: {exc}",
                ) from exc

        try:
            self._client = boto3.client(
                "bedrock-runtime",
                region_name=self._region,
                config=Config(read_timeout=self._timeout, retries={"max_attempts": 0}),
            )
        except Exception as exc:
            raise ProviderNotConfiguredError(
                provider=PROVIDER_NAME,
                kind="RULE_EXTRACTION",
                remedy=(
                    "Could not create a bedrock-runtime client. Check AWS credentials, "
                    f"AI_BEDROCK_REGION, and model access. Cause: {type(exc).__name__}: {exc}"
                ),
            ) from exc
        return self._client

    @staticmethod
    def _read_body(body: Any) -> str:
        if hasattr(body, "read"):
            body = body.read()
        if isinstance(body, bytes):
            return body.decode("utf-8")
        return str(body or "")

    @classmethod
    def _response_text(cls, response: Any) -> str:
        try:
            payload = json.loads(cls._read_body(response["body"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(PROVIDER_NAME, f"malformed response body: {exc}") from exc

        # Gemma response shapes have changed across Bedrock API versions; accept the documented
        # generation shape plus the common Converse/compatibility wrappers.
        for key in ("generation", "output_text", "text"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
        content = payload.get("content")
        if isinstance(content, list):
            text = "".join(
                item.get("text", "") for item in content if isinstance(item, dict)
            )
            if text:
                return text
        choices = payload.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or choices[0]
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"]
        raise ProviderError(PROVIDER_NAME, "response contained no generated text")

    def generate_text(
        self,
        prompt: str,
        *,
        max_output_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        if not self._model:
            raise ProviderNotConfiguredError(
                provider=PROVIDER_NAME,
                kind="RULE_EXTRACTION",
                remedy="Set AI_RULE_EXTRACTION_MODEL to the Bedrock model ID to use.",
            )
        client = self._bedrock()
        request = {
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self._max_output_tokens if max_output_tokens is None else max_output_tokens,
            "temperature": self._temperature if temperature is None else temperature,
        }
        try:
            response = client.invoke_model(
                modelId=self._model,
                body=json.dumps(request),
                accept="application/json",
                contentType="application/json",
            )
            return self._response_text(response)
        except ProviderError:
            raise
        except Exception as exc:
            name = type(exc).__name__
            if "Timeout" in name or "ReadTimeout" in name:
                raise ProviderTimeoutError(PROVIDER_NAME, self._timeout) from exc
            message = str(exc)
            if any(
                marker in name or marker in message
                for marker in (
                    "AccessDenied",
                    "ResourceNotFound",
                    "ModelNotReady",
                    "model access",
                    "modelAccess",
                )
            ):
                raise ProviderNotConfiguredError(
                    provider=PROVIDER_NAME,
                    kind="RULE_EXTRACTION",
                    remedy=(
                        f"Enable model access for '{self._model}' in the selected Bedrock region "
                        f"and verify IAM permissions. Cause: {name}: {message}"
                    ),
                ) from exc
            raise ProviderError(PROVIDER_NAME, f"{name}: {exc}") from exc

    # A convenient alias for callers that use the generic provider vocabulary.
    generate = generate_text
    invoke = generate_text


BedrockRuntimeAdapter = BedrockGemmaProvider

__all__ = ["PROVIDER_NAME", "BedrockGemmaProvider", "BedrockRuntimeAdapter"]
