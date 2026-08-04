"""Reranking via Amazon Bedrock's dedicated Rerank API (``bedrock-agent-runtime``).

Not the same client T003/M5 use for Bedrock Titan embeddings (``bedrock-runtime``) — reranking is a
separate Bedrock API surface (``bedrock-agent-runtime.rerank``) that hosts either Amazon's own
Rerank model or Cohere Rerank on Bedrock, chosen by ``model_arn`` rather than by a request field.

Credentials come from the boto3 default chain, matching every other AWS integration in this
codebase (S3, Textract, Bedrock Titan) — nothing is read from application settings beyond the
region and the model ARN.

**Not verified against a live account.** Written to the documented ``rerank`` request/response
shape and exercised in tests with an injected client double.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any, Optional, Sequence

from app.ai.core.errors import ProviderError, ProviderNotConfiguredError, ProviderTimeoutError
from app.ai.core.types import RetrievedChunk
from app.core.logging import get_logger

logger = get_logger(__name__)

PROVIDER_NAME = "bedrock_rerank"

# Amazon's own hosted rerank model, the default when no Cohere-on-Bedrock ARN is configured.
DEFAULT_MODEL_ARN = "amazon.rerank-v1:0"

# Bedrock's own ceiling on documents per rerank call.
MAX_DOCUMENTS = 1000


class BedrockReranker:
    """Implements the ``Reranker`` protocol against Bedrock's ``rerank`` operation."""

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        model_arn: str = DEFAULT_MODEL_ARN,
        region: Optional[str] = None,
        timeout_seconds: float = 15.0,
        client: Any = None,
    ) -> None:
        self._model_arn = model_arn
        self._region = region
        self._timeout = timeout_seconds
        self._client = client

    def is_available(self) -> bool:
        if self._client is not None:
            return True
        try:
            import boto3  # noqa: PLC0415
        except ImportError:
            return False
        try:
            return boto3.Session().get_credentials() is not None
        except Exception:
            return False

    def _bedrock(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise ProviderNotConfiguredError(
                provider=PROVIDER_NAME, kind="RERANK",
                remedy="Install boto3: pip install boto3.",
            ) from exc
        try:
            self._client = boto3.client(
                "bedrock-agent-runtime",
                region_name=self._region,
                config=Config(read_timeout=self._timeout, retries={"max_attempts": 0}),
            )
        except Exception as exc:
            raise ProviderNotConfiguredError(
                provider=PROVIDER_NAME, kind="RERANK",
                remedy=(
                    "Could not create a bedrock-agent-runtime client. Check AWS credentials and "
                    f"AI_BEDROCK_REGION. Cause: {type(exc).__name__}: {exc}"
                ),
            ) from exc
        return self._client

    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievedChunk],
        *,
        top_n: Optional[int] = None,
    ) -> list[RetrievedChunk]:
        """Reorder ``candidates``, honouring ``top_n`` server-side when given — the same reasoning
        as ``CohereReranker.rerank``: an HTTP-ish (SigV4) call, so a smaller response is a real
        saving; ``RerankService`` truncates again regardless, so the contract holds either way.
        """
        if not candidates:
            return []
        if len(candidates) > MAX_DOCUMENTS:
            raise ProviderError(
                PROVIDER_NAME,
                f"{len(candidates)} candidates exceeds Bedrock's ceiling of {MAX_DOCUMENTS}; "
                "lower AI_RETRIEVAL_CANDIDATE_K",
            )
        client = self._bedrock()
        number_of_results = min(top_n, len(candidates)) if top_n is not None else len(candidates)

        try:
            started = time.perf_counter()
            response = client.rerank(
                queries=[{"type": "TEXT", "textQuery": {"text": query}}],
                sources=[
                    {
                        "type": "INLINE",
                        "inlineDocumentSource": {
                            "type": "TEXT", "textDocument": {"text": candidate.text},
                        },
                    }
                    for candidate in candidates
                ],
                rerankingConfiguration={
                    "type": "BEDROCK_RERANKING_MODEL",
                    "bedrockRerankingConfiguration": {
                        "modelConfiguration": {"modelArn": self._model_arn},
                        "numberOfResults": number_of_results,
                    },
                },
            )
        except Exception as exc:
            name = type(exc).__name__
            if "Timeout" in name or "ReadTimeout" in name:
                raise ProviderTimeoutError(PROVIDER_NAME, self._timeout) from exc
            raise ProviderError(PROVIDER_NAME, f"{name}: {exc}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        try:
            results = response["results"]
            reranked = [
                replace(
                    candidates[item["index"]],
                    score=float(item["relevanceScore"]),
                    rerank_score=float(item["relevanceScore"]),
                )
                for item in results
            ]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                PROVIDER_NAME, f"malformed response: {type(exc).__name__}: {exc}"
            ) from exc

        logger.debug(
            "ai.rerank.bedrock_call",
            extra={"provider": PROVIDER_NAME, "latencyMs": latency_ms, "count": len(candidates)},
        )
        reranked.sort(key=lambda c: (-c.score, str(c.id)))
        return reranked


__all__ = ["DEFAULT_MODEL_ARN", "MAX_DOCUMENTS", "PROVIDER_NAME", "BedrockReranker"]
