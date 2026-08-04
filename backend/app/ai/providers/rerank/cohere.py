"""Cohere Rerank, via its dedicated ``/v2/rerank`` endpoint — not the embed endpoint.

Same guarded-import, cheap-constructor shape as the M5 cloud embedding adapters
(``app/ai/providers/embeddings/cloud.py``): registerable with no ``httpx`` installed and no API key
set, failing only at the point of use with a remedy in the error.

**Not verified against a live account.** Written to Cohere's documented request/response shape and
exercised in tests with a stubbed transport.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from app.ai.core.errors import ProviderError, ProviderNotConfiguredError, ProviderTimeoutError
from app.ai.core.types import RetrievedChunk
from app.core.logging import get_logger

logger = get_logger(__name__)

PROVIDER_NAME = "cohere"
ENDPOINT = "https://api.cohere.com/v2/rerank"
DEFAULT_MODEL = "rerank-v3.5"
# Cohere's own ceiling on documents per rerank call.
MAX_DOCUMENTS = 1000


class CohereReranker:
    """Implements the ``Reranker`` protocol against Cohere's hosted rerank model."""

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        api_key: Optional[str],
        model: str = DEFAULT_MODEL,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds

    def is_available(self) -> bool:
        if not self._api_key:
            return False
        try:
            import httpx  # noqa: F401, PLC0415
        except ImportError:
            return False
        return True

    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievedChunk],
        *,
        top_n: Optional[int] = None,
    ) -> list[RetrievedChunk]:
        """Reorder ``candidates``, honouring ``top_n`` server-side when given.

        Unlike the in-process providers (``heuristic``, ``cross_encoder_local``), which ignore
        ``top_n`` and return every candidate reordered, this one passes it to Cohere so the response
        body is smaller — a real saving over the network that has no equivalent for a local call.
        ``RerankService`` still applies its own truncation afterward regardless, so the *caller's*
        contract (clause 1: truncation is the caller's decision) holds either way; this is purely an
        optimization of how that truncation gets carried out for an HTTP-based provider.
        """
        from dataclasses import replace

        if not candidates:
            return []
        if len(candidates) > MAX_DOCUMENTS:
            raise ProviderError(
                PROVIDER_NAME,
                f"{len(candidates)} candidates exceeds Cohere's ceiling of {MAX_DOCUMENTS}; "
                "lower AI_RETRIEVAL_CANDIDATE_K",
            )
        if not self._api_key:
            raise ProviderNotConfiguredError(
                provider=PROVIDER_NAME, kind="RERANK",
                remedy="Set AI_COHERE_API_KEY.",
            )
        try:
            import httpx
        except ImportError as exc:
            raise ProviderNotConfiguredError(
                provider=PROVIDER_NAME, kind="RERANK",
                remedy="Install the optional dependency: pip install httpx.",
            ) from exc

        body: dict[str, Any] = {
            "model": self._model,
            "query": query,
            "documents": [c.text for c in candidates],
            # The scored documents are already held as RetrievedChunk.text; asking Cohere to echo
            # them back would only double the response payload for no benefit.
            "return_documents": False,
        }
        if top_n is not None:
            body["top_n"] = top_n

        client = httpx.Client(timeout=self._timeout)
        try:
            response = client.post(
                ENDPOINT,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
        except Exception as exc:
            name = type(exc).__name__
            if "Timeout" in name:
                raise ProviderTimeoutError(PROVIDER_NAME, self._timeout) from exc
            raise ProviderError(PROVIDER_NAME, f"{name}: {exc}") from exc
        finally:
            client.close()

        if response.status_code >= 400:
            raise ProviderError(
                PROVIDER_NAME,
                f"HTTP {response.status_code}: {response.text[:200]}",
                details={"status": response.status_code},
            )
        try:
            results = response.json()["results"]
        except (KeyError, ValueError, TypeError) as exc:
            raise ProviderError(
                PROVIDER_NAME, f"unexpected response shape: {type(exc).__name__}: {exc}"
            ) from exc

        # Cohere returns each result's original index into `documents` plus its relevance score,
        # already sorted descending -- but re-sorted here too, so this adapter's output ordering is
        # asserted rather than trusted blindly from the vendor.
        try:
            reranked = [
                replace(
                    candidates[item["index"]],
                    score=float(item["relevance_score"]),
                    rerank_score=float(item["relevance_score"]),
                )
                for item in results
            ]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                PROVIDER_NAME, f"malformed result entry: {type(exc).__name__}: {exc}"
            ) from exc

        reranked.sort(key=lambda c: (-c.score, str(c.id)))
        return reranked


__all__ = ["DEFAULT_MODEL", "ENDPOINT", "MAX_DOCUMENTS", "PROVIDER_NAME", "CohereReranker"]
