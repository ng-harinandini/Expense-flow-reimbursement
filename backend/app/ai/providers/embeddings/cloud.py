"""Cloud embedding adapters: Bedrock Titan, OpenAI, Cohere, Voyage (Task 4).

All four share one shape, so they share one module: authenticate, POST a batch of texts, read back a
list of vectors, normalize. The differences that matter are the request/response schema and the
batch ceiling, which is what each subclass supplies.

**Every import is guarded and every constructor is cheap.** These adapters must be *registerable* on
a machine with no credentials and no ``boto3``/``httpx`` installed — the registry stores factories,
so nothing is constructed here until it is selected. An absent dependency or key becomes
``ProviderNotConfiguredError`` (503, naming the remedy) at the point of use, never an ImportError at
startup.

**None of these is verified against a live service.** They are written to each vendor's documented
API and validated against the shared provider contract suite with a stubbed transport. Marked as
technical debt: the first one to be funded should get a live smoke test before production use.

Vectors are normalized locally regardless of what the vendor returns, so ``spec.normalized`` is true
for all of them and cosine/inner-product ranking cannot diverge between providers.
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional, Sequence

from app.ai.core.errors import (
    ProviderError,
    ProviderNotConfiguredError,
    ProviderTimeoutError,
)
from app.ai.core.text import estimate_tokens
from app.ai.core.types import (
    EmbeddingResult,
    EmbeddingSpec,
    EmbeddingUsage,
    EmbeddingVector,
)
from app.ai.embeddings.math import normalize
from app.core.logging import get_logger

logger = get_logger(__name__)


class _HttpEmbeddingProvider:
    """Shared machinery for the HTTP-based vendors (OpenAI, Cohere, Voyage)."""

    name = "http"
    is_semantic = True
    endpoint = ""
    max_batch = 96

    def __init__(
        self,
        *,
        api_key: Optional[str],
        model: str,
        dimensions: int,
        version: str,
        timeout_seconds: float = 60.0,
        cost_per_million_tokens_usd: float = 0.0,
        max_input_tokens: int = 8192,
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._spec = EmbeddingSpec(
            provider=self.name,
            model=model,
            dimensions=dimensions,
            version=version,
            normalized=True,
            max_input_tokens=max_input_tokens,
            cost_per_million_tokens_usd=cost_per_million_tokens_usd,
        )

    @property
    def spec(self) -> EmbeddingSpec:
        return self._spec

    def is_available(self) -> bool:
        if not self._api_key:
            return False
        try:
            import httpx  # noqa: F401, PLC0415
        except ImportError:
            return False
        return True

    def _client(self):
        if not self._api_key:
            raise ProviderNotConfiguredError(
                provider=self.name, kind="EMBEDDING",
                remedy=f"Set the API key for '{self.name}' (see .env.example).",
            )
        try:
            import httpx
        except ImportError as exc:
            raise ProviderNotConfiguredError(
                provider=self.name, kind="EMBEDDING",
                remedy="Install the optional dependency: pip install httpx.",
            ) from exc
        return httpx.Client(timeout=self._timeout)

    # --- vendor hooks -------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _payload(self, texts: Sequence[str], *, is_query: bool) -> dict[str, Any]:
        raise NotImplementedError

    def _parse(self, body: dict[str, Any]) -> tuple[list[list[float]], int]:
        """Return ``(vectors, tokens)``."""
        raise NotImplementedError

    # --- transport ----------------------------------------------------------

    def _post(self, texts: Sequence[str], *, is_query: bool) -> tuple[list[list[float]], int, int]:
        started = time.perf_counter()
        client = self._client()
        try:
            response = client.post(
                self.endpoint, headers=self._headers(),
                json=self._payload(texts, is_query=is_query),
            )
        except Exception as exc:
            name = type(exc).__name__
            if "Timeout" in name:
                raise ProviderTimeoutError(self.name, self._timeout) from exc
            raise ProviderError(self.name, f"{name}: {exc}") from exc
        finally:
            client.close()

        if response.status_code >= 400:
            # The body can echo the submitted text; truncate hard so receipt content never lands in
            # a log or an exception message.
            raise ProviderError(
                self.name,
                f"HTTP {response.status_code}: {response.text[:200]}",
                details={"status": response.status_code},
            )
        try:
            vectors, tokens = self._parse(response.json())
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderError(
                self.name, f"unexpected response shape: {type(exc).__name__}: {exc}"
            ) from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        return vectors, tokens, latency_ms

    def _result(self, texts: Sequence[str], *, is_query: bool) -> EmbeddingResult:
        if len(texts) > self.max_batch:
            raise ProviderError(
                self.name,
                f"batch of {len(texts)} exceeds this provider's ceiling of {self.max_batch}; "
                "lower AI_EMBEDDING_BATCH_SIZE",
            )
        raw, tokens, latency_ms = self._post(texts, is_query=is_query)
        if len(raw) != len(texts):
            raise ProviderError(
                self.name, f"returned {len(raw)} vectors for {len(texts)} inputs"
            )

        vectors = tuple(
            EmbeddingVector(
                values=normalize(values),
                spec_key=self._spec.key,
                dimensions=self._spec.dimensions,
            )
            for values in raw
        )
        cost = (tokens / 1_000_000) * self._spec.cost_per_million_tokens_usd
        usage = EmbeddingUsage(
            texts=len(texts), tokens=tokens, latency_ms=latency_ms, cost_usd=cost,
            provider=self.name, model=self._spec.model,
        )
        return EmbeddingResult(vectors=vectors, spec=self._spec, usage=usage)

    def embed_documents(self, texts: Sequence[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=(), spec=self._spec, usage=EmbeddingUsage())
        return self._result(texts, is_query=False)

    def embed_query(self, text: str) -> EmbeddingVector:
        return self._result([text], is_query=True).vectors[0]


class OpenAIEmbeddingProvider(_HttpEmbeddingProvider):
    """``text-embedding-3-*``. Symmetric, so queries and documents share one payload shape."""

    name = "openai"
    endpoint = "https://api.openai.com/v1/embeddings"
    max_batch = 2048

    def _payload(self, texts: Sequence[str], *, is_query: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": self._spec.model, "input": list(texts)}
        # text-embedding-3 supports native truncation to a smaller width; only send it when the
        # configured width differs from the model's default, or the API rejects it.
        if self._spec.dimensions not in (1536, 3072):
            payload["dimensions"] = self._spec.dimensions
        return payload

    def _parse(self, body: dict[str, Any]) -> tuple[list[list[float]], int]:
        # ``index`` is authoritative: the API does not guarantee response order.
        items = sorted(body["data"], key=lambda item: item["index"])
        tokens = int(body.get("usage", {}).get("total_tokens", 0))
        return [item["embedding"] for item in items], tokens


class CohereEmbeddingProvider(_HttpEmbeddingProvider):
    """``embed-*``. Asymmetric: ``input_type`` must differ between indexing and searching."""

    name = "cohere"
    endpoint = "https://api.cohere.com/v2/embed"
    max_batch = 96

    def _payload(self, texts: Sequence[str], *, is_query: bool) -> dict[str, Any]:
        return {
            "model": self._spec.model,
            "texts": list(texts),
            # Getting this wrong measurably reduces recall — Cohere trains the two roles apart.
            "input_type": "search_query" if is_query else "search_document",
            "embedding_types": ["float"],
        }

    def _parse(self, body: dict[str, Any]) -> tuple[list[list[float]], int]:
        embeddings = body["embeddings"]
        vectors = embeddings["float"] if isinstance(embeddings, dict) else embeddings
        tokens = int(
            body.get("meta", {}).get("billed_units", {}).get("input_tokens", 0)
        )
        return vectors, tokens


class VoyageEmbeddingProvider(_HttpEmbeddingProvider):
    """``voyage-*``. Asymmetric, like Cohere."""

    name = "voyage"
    endpoint = "https://api.voyageai.com/v1/embeddings"
    max_batch = 128

    def _payload(self, texts: Sequence[str], *, is_query: bool) -> dict[str, Any]:
        return {
            "model": self._spec.model,
            "input": list(texts),
            "input_type": "query" if is_query else "document",
        }

    def _parse(self, body: dict[str, Any]) -> tuple[list[list[float]], int]:
        items = sorted(body["data"], key=lambda item: item.get("index", 0))
        tokens = int(body.get("usage", {}).get("total_tokens", 0))
        return [item["embedding"] for item in items], tokens


class BedrockTitanEmbeddingProvider:
    """Amazon Titan Text Embeddings via boto3.

    Separate from the HTTP family because Bedrock is SigV4-signed and **embeds one text per call** —
    there is no batch endpoint for Titan. Batching therefore happens client-side here, which makes
    it
    the slowest adapter for bulk ingestion and worth knowing before choosing it for a large corpus.

    Credentials come from the boto3 default chain, matching the convention T001 established for S3
    and Textract; nothing is read from application settings.
    """

    name = "bedrock_titan"
    is_semantic = True

    def __init__(
        self,
        *,
        model: str = "amazon.titan-embed-text-v2:0",
        dimensions: int = 1024,
        version: str = "v1",
        region: Optional[str] = None,
        timeout_seconds: float = 60.0,
        cost_per_million_tokens_usd: float = 0.02,
    ) -> None:
        self._region = region
        self._timeout = timeout_seconds
        self._client: Any = None
        self._spec = EmbeddingSpec(
            provider=self.name,
            model=model,
            dimensions=dimensions,
            version=version,
            normalized=True,
            max_input_tokens=8192,
            cost_per_million_tokens_usd=cost_per_million_tokens_usd,
        )

    @property
    def spec(self) -> EmbeddingSpec:
        return self._spec

    def is_available(self) -> bool:
        try:
            import boto3  # noqa: PLC0415
        except ImportError:
            return False
        try:
            # Presence of resolvable credentials, without making a network call.
            return boto3.Session().get_credentials() is not None
        except Exception:
            return False

    def _bedrock(self):
        if self._client is not None:
            return self._client
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise ProviderNotConfiguredError(
                provider=self.name, kind="EMBEDDING",
                remedy="Install boto3: pip install boto3.",
            ) from exc
        try:
            self._client = boto3.client(
                "bedrock-runtime",
                region_name=self._region,
                config=Config(read_timeout=self._timeout, retries={"max_attempts": 0}),
            )
        except Exception as exc:
            raise ProviderNotConfiguredError(
                provider=self.name, kind="EMBEDDING",
                remedy=(
                    "Could not create a bedrock-runtime client. Check AWS credentials and "
                    f"AI_BEDROCK_REGION. Cause: {type(exc).__name__}: {exc}"
                ),
            ) from exc
        return self._client

    def _invoke(self, text: str) -> list[float]:
        client = self._bedrock()
        body = {"inputText": text, "dimensions": self._spec.dimensions, "normalize": True}
        try:
            response = client.invoke_model(
                modelId=self._spec.model, body=json.dumps(body),
                accept="application/json", contentType="application/json",
            )
            parsed = json.loads(response["body"].read())
        except Exception as exc:
            name = type(exc).__name__
            if "Timeout" in name or "ReadTimeout" in name:
                raise ProviderTimeoutError(self.name, self._timeout) from exc
            raise ProviderError(self.name, f"{name}: {exc}") from exc

        vector = parsed.get("embedding")
        if not isinstance(vector, list):
            raise ProviderError(self.name, "response contained no 'embedding' array")
        return vector

    def embed_documents(self, texts: Sequence[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=(), spec=self._spec, usage=EmbeddingUsage())

        started = time.perf_counter()
        vectors: list[EmbeddingVector] = []
        for text in texts:
            vectors.append(
                EmbeddingVector(
                    values=normalize(self._invoke(text)),
                    spec_key=self._spec.key,
                    dimensions=self._spec.dimensions,
                )
            )
        latency_ms = int((time.perf_counter() - started) * 1000)
        # Titan does not report token usage, so cost is estimated from the heuristic counter and
        # labelled as such wherever it is surfaced.
        tokens = sum(estimate_tokens(t) for t in texts)
        usage = EmbeddingUsage(
            texts=len(texts), tokens=tokens, latency_ms=latency_ms,
            cost_usd=(tokens / 1_000_000) * self._spec.cost_per_million_tokens_usd,
            provider=self.name, model=self._spec.model,
        )
        return EmbeddingResult(vectors=tuple(vectors), spec=self._spec, usage=usage)

    def embed_query(self, text: str) -> EmbeddingVector:
        """Titan is symmetric — no separate query encoding."""
        return EmbeddingVector(
            values=normalize(self._invoke(text)),
            spec_key=self._spec.key,
            dimensions=self._spec.dimensions,
        )


class TeiHttpEmbeddingProvider(_HttpEmbeddingProvider):
    """A self-hosted HuggingFace Text Embeddings Inference endpoint.

    Covers the "BGE / E5 / Instructor on our own GPU" case from Task 4 without the platform having
    to
    host a model runtime itself. The base URL is the configuration; no API key is required for a
    typical in-VPC deployment, so availability is decided by the URL alone.
    """

    name = "tei_http"

    def __init__(self, *, base_url: Optional[str], model: str, dimensions: int,
                 version: str, timeout_seconds: float = 60.0,
                 query_prefix: str = "", passage_prefix: str = "") -> None:
        super().__init__(
            api_key=base_url or None, model=model, dimensions=dimensions,
            version=version, timeout_seconds=timeout_seconds,
        )
        self._base_url = (base_url or "").rstrip("/")
        # E5 and Instructor need these; bge-m3 must not have them. Configurable rather than assumed.
        self._query_prefix = query_prefix
        self._passage_prefix = passage_prefix

    @property
    def endpoint(self) -> str:  # type: ignore[override]
        return f"{self._base_url}/embed"

    def is_available(self) -> bool:
        if not self._base_url:
            return False
        try:
            import httpx  # noqa: F401, PLC0415
        except ImportError:
            return False
        return True

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def _payload(self, texts: Sequence[str], *, is_query: bool) -> dict[str, Any]:
        prefix = self._query_prefix if is_query else self._passage_prefix
        return {"inputs": [f"{prefix}{t}" for t in texts], "truncate": True}

    def _parse(self, body: Any) -> tuple[list[list[float]], int]:
        # TEI returns a bare array of arrays and reports no token usage.
        if not isinstance(body, list):
            raise ValueError("TEI response was not a list of embeddings")
        return body, 0


__all__ = [
    "BedrockTitanEmbeddingProvider",
    "CohereEmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "TeiHttpEmbeddingProvider",
    "VoyageEmbeddingProvider",
]
