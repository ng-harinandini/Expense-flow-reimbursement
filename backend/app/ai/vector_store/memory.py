"""In-memory vector store — the reference implementation.

This is the store the contract suite is written against first, and it earns its place three ways:

1. **It is the specification, executable.** When an adapter and this store disagree about what a
   filter means or how ties break, this one is right by definition and the adapter is wrong. That is
   only useful because it is small enough to read in one sitting and has no engine behaviour to hide
   behind.
2. **It makes the platform runnable with nothing installed.** Unit tests for retrieval, fusion,
   reranking and context building need *a* store, not a database. Requiring PostgreSQL for those
   would push the whole retrieval test suite into the slow, infrastructure-dependent tier.
3. **Exact.** Brute-force cosine over every candidate, so it is the ground truth an approximate
   index's recall can be measured against.

It is **not** a production store and says so through its capabilities: nothing is persisted, so a
restart loses the index, and search is O(n) in the tenant's corpus. ``AI_VECTOR_STORE=memory`` is a
legitimate choice for a CI run or a local demo and nothing else.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, Optional, Sequence

from app.ai.core.enums import DistanceMetric, TelemetryOperation
from app.ai.core.types import EmbeddedChunk, EmbeddingVector, MetadataFilter
from app.ai.embeddings.math import cosine_distance, dot, euclidean_distance
from app.ai.interfaces.vector_store import VectorMatch
from app.ai.vector_store import capabilities as caps
from app.ai.vector_store.base import BaseVectorStore
from app.ai.vector_store.filters import matches

STORE_NAME = "memory"


class InMemoryVectorStore(BaseVectorStore):
    """Brute-force exact search over a dict, partitioned by tenant and embedding version.

    The partition key is ``(tenant_id, spec_key)`` rather than just the tenant. Both halves matter:
    the tenant half is the isolation boundary, and the version half is what makes a re-index safe —
    vectors from the new model accumulate alongside the old ones and a query only ever sees the
    version its own query vector was produced by.
    """

    def __init__(
        self,
        *,
        dimensions: int,
        metric: DistanceMetric = DistanceMetric.COSINE,
        recorder: Any = None,
    ) -> None:
        super().__init__(
            name=STORE_NAME,
            dimensions=dimensions,
            capabilities=caps.MEMORY,
            metric=metric,
            recorder=recorder,
        )
        self._data: dict[tuple[str, str], dict[uuid.UUID, EmbeddedChunk]] = {}
        # Instances are shared through the registry and FastAPI serves from a thread pool, so two
        # concurrent ingestion calls can be inside upsert at the same time.
        self._lock = threading.RLock()

    # --- lifecycle -----------------------------------------------------------

    def is_available(self) -> bool:
        """Always. A dict has no outage mode, which is the whole point of this adapter."""
        return True

    def ensure_ready(self) -> None:
        """Nothing to create."""

    # --- writes --------------------------------------------------------------

    def upsert(self, chunks: Sequence[EmbeddedChunk], *, tenant_id: str = "default") -> int:
        spec_key = self._validate_batch(chunks)
        with self._span(TelemetryOperation.VECTOR_UPSERT, count=len(chunks)):
            with self._lock:
                bucket = self._data.setdefault((tenant_id, spec_key), {})
                for chunk in chunks:
                    # Assignment by id makes upsert idempotent: re-upserting a chunk replaces
                    # its vector and metadata in place and never produces a second entry.
                    bucket[chunk.id] = chunk
            return len(chunks)

    def delete_by_document(self, document_id: Any, *, tenant_id: str = "default") -> int:
        """Delete every version's vectors for one document. Safe to call twice."""
        key = _as_uuid(document_id)
        removed = 0
        with self._lock:
            for (bucket_tenant, _), bucket in self._data.items():
                if bucket_tenant != tenant_id:
                    continue
                doomed = [
                    chunk_id
                    for chunk_id, chunk in bucket.items()
                    if chunk.chunk.metadata.document_id == key
                ]
                for chunk_id in doomed:
                    del bucket[chunk_id]
                removed += len(doomed)
        return removed

    def clear(self) -> None:
        """Drop everything. Test helper — a production store has no such operation."""
        with self._lock:
            self._data.clear()

    # --- reads ---------------------------------------------------------------

    def get(self, chunk_id: Any, *, tenant_id: str = "default") -> Optional[EmbeddedChunk]:
        """The chunk under any embedding version, newest write wins.

        Version-agnostic because callers of ``get`` want the chunk, not a particular vector of it;
        ``search`` is where version isolation matters.
        """
        key = _as_uuid(chunk_id)
        with self._lock:
            for (bucket_tenant, _), bucket in self._data.items():
                if bucket_tenant != tenant_id:
                    continue
                found = bucket.get(key)
                if found is not None:
                    return found
        return None

    def count(self, *, tenant_id: Optional[str] = None) -> int:
        with self._lock:
            return sum(
                len(bucket)
                for (bucket_tenant, _), bucket in self._data.items()
                if tenant_id is None or bucket_tenant == tenant_id
            )

    def search(
        self,
        vector: EmbeddingVector,
        *,
        top_k: int = 10,
        tenant_id: str = "default",
        filters: Sequence[MetadataFilter] = (),
        score_threshold: float = 0.0,
        offset: int = 0,
    ) -> list[VectorMatch]:
        self._validate_query(
            vector, top_k=top_k, offset=offset,
            score_threshold=score_threshold, tenant_id=tenant_id,
        )
        normalized = self._normalize_filters(filters)

        with self._span(TelemetryOperation.VECTOR_SEARCH, topK=top_k) as span:
            with self._lock:
                # Copied out under the lock: scoring is the slow part and must not hold it, and a
                # concurrent upsert must not mutate the collection mid-iteration.
                candidates = list(self._data.get((tenant_id, vector.spec_key), {}).values())

            scored: list[VectorMatch] = []
            for candidate in candidates:
                # Filters before scoring — the contract's clause 2. Cheap here, load-bearing
                # everywhere: post-filtering is how a store returns fewer than top_k in silence.
                if normalized and not matches(candidate.chunk, normalized):
                    continue
                distance = self._distance(vector.values, candidate.vector.values)
                score = self.score_from_distance(distance)
                if score < score_threshold:
                    continue
                scored.append(
                    VectorMatch(
                        chunk_id=candidate.id,
                        document_id=candidate.chunk.metadata.document_id,
                        score=score,
                        chunk=candidate.chunk,
                        raw_distance=distance,
                    )
                )

            # Descending score, ties broken on the id. The tie-break is not cosmetic: without a
            # stable secondary key, two chunks with equal scores can swap places between calls and
            # page 2 then repeats or skips one of them.
            scored.sort(key=lambda match: (-match.score, str(match.chunk_id)))
            page = scored[offset: offset + top_k]
            if span is not None:
                span.set_counts(candidates_in=len(candidates), candidates_out=len(page))
            return page

    # --- internals -----------------------------------------------------------

    def _distance(self, query: Sequence[float], candidate: Sequence[float]) -> float:
        """The configured metric's distance, in the same convention the engines use.

        Computed as a *distance* rather than a similarity so it goes through the same
        ``score_from_distance`` conversion as every other adapter — if that mapping were wrong, this
        store would be wrong in the identical way, and the contract suite would agree with itself
        about the wrong answer.
        """
        if self.metric is DistanceMetric.EUCLIDEAN:
            return euclidean_distance(query, candidate)
        if self.metric is DistanceMetric.INNER_PRODUCT:
            return -dot(query, candidate)
        return cosine_distance(query, candidate)

    def spec_keys(self, *, tenant_id: Optional[str] = None) -> tuple[str, ...]:
        """Embedding versions currently indexed — the "is the re-index finished?" question.

        Mirrors ``KnowledgeEmbeddingRepository.count_by_spec`` so a caller can ask the same question
        of either store.
        """
        with self._lock:
            return tuple(sorted({
                spec_key
                for (bucket_tenant, spec_key) in self._data
                if tenant_id is None or bucket_tenant == tenant_id
            }))


def _as_uuid(value: Any) -> uuid.UUID:
    """Accept a ``UUID`` or its string form, as every repository in the codebase does."""
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"'{value}' is not a valid UUID.") from exc


__all__ = ["STORE_NAME", "InMemoryVectorStore"]
