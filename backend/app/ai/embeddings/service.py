"""The embedding framework: batching, caching, retry, version stamping and cost accounting.

This is the layer business code talks to. It never names a model — the provider arrives from the
registry, so swapping bge-m3 for Titan is configuration.

What the service adds over a bare provider, and why each part is here rather than in the adapters:

**Caching, keyed on ``(text checksum, spec key)``.** Re-embedding unchanged policy text is the
single largest avoidable cost in the platform, and a re-ingest of a mostly-unchanged document hits
it constantly. The spec key *must* be part of the key — keying on text alone is how a model upgrade
ends up serving the previous model's vectors out of cache, a corruption that produces no error.

**Batching.** Providers charge and rate-limit per request. Splitting a 500-chunk document into
batches of ``AI_EMBEDDING_BATCH_SIZE`` is a property of the framework, so no adapter has to
re-implement it.

**Retry with backoff, on transport failures only.** A ``ProviderNotConfiguredError`` is not retried:
credentials will not appear between attempts, and retrying turns a clear 503 into a slow one.

**Cache-aware ordering.** Cached and freshly-embedded vectors are recombined into the caller's
original order. Getting this wrong silently pairs vectors with the wrong chunks — the kind of bug
that degrades retrieval quality without ever raising, so it has a dedicated test.
"""

from __future__ import annotations

import time
from typing import Optional, Sequence

from app.ai.core.config import AISettings, ai_settings
from app.ai.core.enums import TelemetryOperation
from app.ai.core.errors import (
    AIValidationError,
    ProviderError,
    ProviderNotConfiguredError,
)
from app.ai.core.ids import embedding_cache_key
from app.ai.core.types import (
    EmbeddingResult,
    EmbeddingSpec,
    EmbeddingUsage,
    EmbeddingVector,
)
from app.ai.embeddings.math import is_normalized
from app.core.logging import get_logger

logger = get_logger(__name__)


class EmbeddingService:
    """Framework around an :class:`~app.ai.interfaces.embeddings.EmbeddingProvider`."""

    def __init__(
        self,
        provider,
        *,
        cache=None,
        recorder=None,
        settings: Optional[AISettings] = None,
    ) -> None:
        self._provider = provider
        self._cache = cache
        self._recorder = recorder
        self._settings = settings or ai_settings

    # --- identity -----------------------------------------------------------

    @property
    def provider_name(self) -> str:
        return getattr(self._provider, "name", "unknown")

    @property
    def spec(self) -> EmbeddingSpec:
        return self._provider.spec

    @property
    def spec_key(self) -> str:
        """The comparability unit stamped onto every vector this service produces."""
        return self._provider.spec.key

    @property
    def is_semantic(self) -> bool:
        """``False`` when the active provider is the deterministic stand-in.

        Surfaced all the way to API response metadata so no consumer can mistake plumbing-only
        vectors for real retrieval quality.
        """
        return bool(getattr(self._provider, "is_semantic", True))

    def is_available(self) -> bool:
        checker = getattr(self._provider, "is_available", None)
        if not callable(checker):
            return True
        try:
            return bool(checker())
        except Exception:
            return False

    # --- embedding ----------------------------------------------------------

    def embed_documents(self, texts: Sequence[str]) -> EmbeddingResult:
        """Embed texts for indexing, in order, using the cache where possible."""
        if not texts:
            return EmbeddingResult(vectors=(), spec=self.spec, usage=EmbeddingUsage())
        if any(text is None for text in texts):
            raise AIValidationError("Cannot embed a None value.")

        with self._span(TelemetryOperation.EMBED, count=len(texts)) as span:
            cached, pending_indices, pending_texts = self._partition_by_cache(texts)

            usage = EmbeddingUsage(
                cache_hits=len(texts) - len(pending_indices),
                cache_misses=len(pending_indices),
                provider=self.provider_name,
                model=self.spec.model,
            )

            fresh: dict[int, EmbeddingVector] = {}
            for batch_indices, batch_texts in self._batches(pending_indices, pending_texts):
                result = self._embed_with_retry(batch_texts)
                if len(result.vectors) != len(batch_texts):
                    # A provider that drops or adds a vector would misalign every downstream
                    # pairing.
                    raise ProviderError(
                        self.provider_name,
                        f"returned {len(result.vectors)} vectors for {len(batch_texts)} inputs",
                    )
                for position, vector in zip(batch_indices, result.vectors, strict=True):
                    fresh[position] = vector
                usage = usage.merged_with(result.usage)

            self._store_in_cache(pending_indices, pending_texts, fresh)

            # Recombine into the caller's order. Asserted by a dedicated test, because a
            # mis-ordering here pairs vectors with the wrong chunks and never raises.
            ordered: list[EmbeddingVector] = []
            for index in range(len(texts)):
                vector = cached.get(index) or fresh.get(index)
                if vector is None:  # pragma: no cover - defensive
                    raise ProviderError(
                        self.provider_name, f"no vector produced for input {index}"
                    )
                ordered.append(vector)

            if span is not None:
                span.set_counts(candidates_in=len(texts), candidates_out=len(ordered))
                span.set_attribute("cacheHits", usage.cache_hits)
                span.set_attribute("specKey", self.spec_key)

            self._record_usage(usage)
            return EmbeddingResult(vectors=tuple(ordered), spec=self.spec, usage=usage)

    def embed_query(self, text: str) -> EmbeddingVector:
        """Embed one search query.

        Queries are cached too: the same question asked twice is common in a review workflow, and a
        query embedding is exactly as reusable as a document one.
        """
        if not text or not text.strip():
            raise AIValidationError("Cannot embed an empty query.")

        key = embedding_cache_key(text, self.spec_key)
        cached = self._cache_get(key)
        if cached is not None:
            self._increment("embedding.cache_hits")
            return cached

        with self._span(TelemetryOperation.EMBED, count=1) as span:
            vector = self._retry(lambda: self._provider.embed_query(text))
            self._validate_vector(vector)
            if span is not None:
                span.set_attribute("specKey", self.spec_key)
        self._cache_set(key, vector)
        self._increment("embedding.cache_misses")
        return vector

    # --- cache --------------------------------------------------------------

    def _cache_enabled(self) -> bool:
        return self._cache is not None and self._settings.EMBEDDING_CACHE_ENABLED

    def _cache_get(self, key: str) -> Optional[EmbeddingVector]:
        """Read a vector from the cache, tolerating any corruption.

        Cached values are plain lists of floats (JSON-serializable, so the memory and Redis backends
        behave identically). A malformed entry is treated as a miss rather than trusted.
        """
        if not self._cache_enabled():
            return None
        try:
            raw = self._cache.get(key)
        except Exception:  # pragma: no cover - a cache must never break a request
            return None
        if raw is None:
            return None
        try:
            values = tuple(float(v) for v in raw)
        except (TypeError, ValueError):
            return None
        if len(values) != self.spec.dimensions:
            # Almost certainly a stale entry from a different model width.
            return None
        return EmbeddingVector(
            values=values, spec_key=self.spec_key, dimensions=self.spec.dimensions
        )

    def _cache_set(self, key: str, vector: EmbeddingVector) -> None:
        if not self._cache_enabled():
            return
        try:
            self._cache.set(
                key, list(vector.values),
                ttl_seconds=self._settings.EMBEDDING_CACHE_TTL_SECONDS,
            )
        except Exception:  # pragma: no cover - best effort by contract
            pass

    def _partition_by_cache(
        self, texts: Sequence[str]
    ) -> tuple[dict[int, EmbeddingVector], list[int], list[str]]:
        """Split inputs into already-cached vectors and the ones still to embed."""
        cached: dict[int, EmbeddingVector] = {}
        pending_indices: list[int] = []
        pending_texts: list[str] = []

        for index, text in enumerate(texts):
            hit = self._cache_get(embedding_cache_key(text, self.spec_key))
            if hit is not None:
                cached[index] = hit
            else:
                pending_indices.append(index)
                pending_texts.append(text)
        return cached, pending_indices, pending_texts

    def _store_in_cache(
        self,
        pending_indices: Sequence[int],
        pending_texts: Sequence[str],
        fresh: dict[int, EmbeddingVector],
    ) -> None:
        for position, text in zip(pending_indices, pending_texts, strict=True):
            vector = fresh.get(position)
            if vector is not None:
                self._cache_set(embedding_cache_key(text, self.spec_key), vector)

    # --- batching + retry ---------------------------------------------------

    def _batches(self, indices: Sequence[int], texts: Sequence[str]):
        size = max(1, self._settings.EMBEDDING_BATCH_SIZE)
        for start in range(0, len(texts), size):
            yield list(indices[start : start + size]), list(texts[start : start + size])

    def _embed_with_retry(self, texts: Sequence[str]) -> EmbeddingResult:
        result = self._retry(lambda: self._provider.embed_documents(texts))
        for vector in result.vectors:
            self._validate_vector(vector)
        return result

    def _retry(self, operation):
        """Retry transport failures with exponential backoff.

        ``ProviderNotConfiguredError`` is re-raised immediately: missing credentials or absent model
        weights will not resolve themselves between attempts, and retrying only delays a clear 503.
        """
        attempts = max(0, self._settings.EMBEDDING_MAX_RETRIES) + 1
        last: Optional[Exception] = None

        for attempt in range(attempts):
            try:
                return operation()
            except ProviderNotConfiguredError:
                raise
            except Exception as exc:
                last = exc
                if attempt == attempts - 1:
                    break
                delay = 0.25 * (2 ** attempt)
                logger.warning(
                    "ai.embedding.retry",
                    extra={
                        "provider": self.provider_name,
                        "attempt": attempt + 1,
                        "of": attempts,
                        "delaySeconds": delay,
                        "error": f"{type(exc).__name__}: {exc}"[:200],
                    },
                )
                time.sleep(delay)

        if isinstance(last, ProviderError):
            raise last
        raise ProviderError(
            self.provider_name,
            f"failed after {attempts} attempt(s): {type(last).__name__}: {last}",
        ) from last

    # --- validation ---------------------------------------------------------

    def _validate_vector(self, vector: EmbeddingVector) -> None:
        """Enforce the provider contract at the boundary.

        Catching a misbehaving adapter here — wrong width, wrong spec key, not actually normalized —
        keeps the bad vector out of the index. Once stored, it degrades every future comparison
        silently.
        """
        if vector.dimensions != self.spec.dimensions:
            raise ProviderError(
                self.provider_name,
                f"returned {vector.dimensions} dimensions, expected {self.spec.dimensions}",
            )
        if vector.spec_key != self.spec_key:
            raise ProviderError(
                self.provider_name,
                f"stamped spec key '{vector.spec_key}', expected '{self.spec_key}'",
            )
        if self.spec.normalized and not is_normalized(vector.values):
            raise ProviderError(
                self.provider_name,
                "claims normalized=True but returned a non-unit vector",
            )

    # --- telemetry ----------------------------------------------------------

    def _span(self, operation: TelemetryOperation, *, count: int):
        if self._recorder is None:
            return _NullSpanContext()
        return self._recorder.span(
            operation, attributes={"provider": self.provider_name, "inputs": count}
        )

    def _record_usage(self, usage: EmbeddingUsage) -> None:
        if self._recorder is None:
            return
        try:
            self._recorder.increment("embedding.texts", usage.texts)
            self._recorder.increment("embedding.tokens", usage.tokens)
            self._recorder.increment("embedding.cache_hits", usage.cache_hits)
            self._recorder.increment("embedding.cache_misses", usage.cache_misses)
            # Micro-USD as an integer: a float counter accumulates rounding error over a long run.
            self._recorder.increment("embedding.cost_micro_usd", int(usage.cost_usd * 1_000_000))
        except Exception:  # pragma: no cover - telemetry must never raise
            pass

    def _increment(self, name: str, amount: int = 1) -> None:
        if self._recorder is None:
            return
        try:
            self._recorder.increment(name, amount)
        except Exception:  # pragma: no cover
            pass

    # --- introspection ------------------------------------------------------

    def describe(self) -> dict[str, object]:
        """Active configuration, for ``/metrics`` and API response metadata."""
        from app.ai.embeddings.versioning import describe_spec

        return {
            **describe_spec(self.spec),
            "isSemantic": self.is_semantic,
            "available": self.is_available(),
            "cacheEnabled": self._cache_enabled(),
            "batchSize": self._settings.EMBEDDING_BATCH_SIZE,
        }


class _NullSpanContext:
    """Stands in for a span when no recorder is wired, so call sites stay branch-free."""

    def __enter__(self):
        return None

    def __exit__(self, *_: object) -> None:
        return None


__all__ = ["EmbeddingService"]
