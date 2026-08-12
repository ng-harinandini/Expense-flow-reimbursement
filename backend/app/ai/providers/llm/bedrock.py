"""Amazon Bedrock Runtime adapter for Google's Gemma instruction model.

The adapter is intentionally lazy: importing the AI package must continue to work on machines
that do not have boto3 or AWS credentials (for example, local development and CI).  Credentials
are resolved by boto3's normal chain, including the Bedrock bearer token environment variable
supported by recent botocore releases.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any, Optional

from app.ai.core.errors import ProviderError, ProviderNotConfiguredError, ProviderTimeoutError

logger = logging.getLogger(__name__)

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
        kind: str = "RULE_EXTRACTION",
        model_setting_name: str = "AI_RULE_EXTRACTION_MODEL",
    ) -> None:
        self._model = model
        self._region = region
        self._timeout = timeout_seconds
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature
        self._client = client
        # Labels this provider instance's failures in the AI error hierarchy (surfaced in the 503
        # body's ``kind``/``remedy`` fields) — callers other than rule extraction must pass their
        # own label or an outage would be misreported as a rule-extraction failure.
        self._kind = kind
        self._model_setting_name = model_setting_name

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
                kind=self._kind,
                remedy="Install boto3: pip install boto3.",
            ) from exc

        if not self._region and not (os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")):
            raise ProviderNotConfiguredError(
                provider=PROVIDER_NAME,
                kind=self._kind,
                remedy="Set AI_BEDROCK_REGION (for example, us-east-1).",
            )
        if not os.getenv("AWS_BEARER_TOKEN_BEDROCK"):
            try:
                if boto3.Session(region_name=self._region).get_credentials() is None:
                    raise ProviderNotConfiguredError(
                        provider=PROVIDER_NAME,
                        kind=self._kind,
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
                    kind=self._kind,
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
                kind=self._kind,
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
        images: Optional[list[dict[str, Any]]] = None,
        max_output_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """Generate text, optionally grounded in one or more images.

        ``images`` is ``[{"bytes": <raw bytes>, "mime_type": "image/png"}, ...]``. When present,
        ``content`` becomes a list of parts (text + base64 data-URL images) instead of a plain
        string, following the OpenAI-chat-compatible convention Bedrock marketplace models expose —
        the same convention already evidenced by the plain-string ``content`` used below.

        **This shape is confirmed accepted by the deployed model.** A PDF sent here as though it were
        an image came back as ``BadRequestError: cannot identify image file <_io.BytesIO ...>`` — a
        Pillow error raised *inside the model container*, which means the endpoint had already parsed
        ``messages``, found the ``image_url`` part and base64-decoded it before reaching its image
        decoder. The envelope was never the problem; the media was. What callers owe this method is
        therefore real, decodable image bytes — see :mod:`app.ai.classification.document_render`,
        which rasterizes PDFs and normalizes exotic formats for exactly this reason.

        Non-image parts are dropped rather than sent, so a caller that gets this wrong loses the
        image and still gets a text-only answer instead of a failed request.
        """
        if not self._model:
            raise ProviderNotConfiguredError(
                provider=PROVIDER_NAME,
                kind=self._kind,
                remedy=f"Set {self._model_setting_name} to the Bedrock model ID to use.",
            )
        client = self._bedrock()
        content: Any = prompt
        if images:
            parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
            for image in images:
                data = image.get("bytes")
                if not data:
                    continue
                mime_type = image.get("mime_type")
                if not mime_type or not mime_type.startswith("image/"):
                    # Anything else (a PDF being the case that actually happened) is rejected by the
                    # model's image decoder, taking the whole request with it. Dropping the part
                    # degrades to text-only, which is a usable answer.
                    logger.warning(
                        "ai.bedrock.non_image_part_skipped",
                        extra={"mimeType": mime_type, "bytes": len(data)},
                    )
                    continue
                encoded = base64.b64encode(data).decode("ascii")
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                    }
                )
            if len(parts) > 1:
                content = parts
        request = {
            "messages": [{"role": "user", "content": content}],
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
                    kind=self._kind,
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
