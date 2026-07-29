"""Vector store contract — the seam that makes storage engine independence real.

Task 5 requires pgvector, Qdrant, OpenSearch, Pinecone, Milvus and Weaviate to be interchangeable.
That claim is only credible if it is *tested*, so this protocol is paired with
``VectorStoreContractTests``: one suite, executed against every registered adapter. An adapter is
"supported" exactly when it passes that suite — never because a file exists for it.

Contract:

1. ``upsert`` is idempotent on chunk id. Re-upserting a chunk replaces its vector and metadata
   without creating a duplicate row and without changing its id.
2. ``search`` applies metadata filters **before** scoring, not after. Post-filtering silently
   returns fewer than ``top_k`` results and is the single most common way a "working" store
   produces wrong answers under a filter.
3. Results are ordered by descending similarity, and similarity is normalized to ``[0, 1]`` where 1
   is identical — regardless of whether the engine natively returns a distance.
4. Tenant isolation is absolute: a search scoped to tenant A can never return tenant B's chunk.
5. A vector whose length differs from the collection's configured dimensionality raises
   ``DimensionMismatchError``, never truncates or pads.
6. ``delete_by_document`` removes every chunk of a document version, and is safe to call twice.
7. Pagination is deterministic: equal scores are broken by a stable key, so page 2 never repeats or
   skips a row from page 1.
"""

from __future__ import annotations

import uuid
from typing import Optional, Protocol, Sequence, runtime_checkable

from app.ai.core.enums import DistanceMetric
from app.ai.core.types import Chunk, EmbeddedChunk, EmbeddingVector, MetadataFilter


class VectorStoreCapabilities:
    """What an adapter can actually do.

    Declared rather than assumed because the backends genuinely differ — not every engine has native
    lexical search or exact (non-approximate) scoring. The retrieval engine reads these flags to
    decide whether to run the lexical leg in the store or fall back to PostgreSQL, so an honest
    capability matrix is what keeps hybrid retrieval correct across adapters.
    """

    def __init__(
        self,
        *,
        native_lexical_search: bool = False,
        native_hybrid_search: bool = False,
        exact_search: bool = True,
        approximate_index: bool = False,
        supports_filters: bool = True,
        supports_pagination: bool = True,
        max_dimensions: Optional[int] = None,
        requires_external_service: bool = False,
    ) -> None:
        self.native_lexical_search = native_lexical_search
        self.native_hybrid_search = native_hybrid_search
        self.exact_search = exact_search
        self.approximate_index = approximate_index
        self.supports_filters = supports_filters
        self.supports_pagination = supports_pagination
        self.max_dimensions = max_dimensions
        self.requires_external_service = requires_external_service

    def to_dict(self) -> dict[str, object]:
        return {
            "nativeLexicalSearch": self.native_lexical_search,
            "nativeHybridSearch": self.native_hybrid_search,
            "exactSearch": self.exact_search,
            "approximateIndex": self.approximate_index,
            "supportsFilters": self.supports_filters,
            "supportsPagination": self.supports_pagination,
            "maxDimensions": self.max_dimensions,
            "requiresExternalService": self.requires_external_service,
        }


class VectorMatch:
    """One scored hit. Not frozen-dataclassed because adapters build these in hot loops."""

    __slots__ = ("chunk_id", "document_id", "score", "chunk", "raw_distance")

    def __init__(
        self,
        chunk_id: uuid.UUID,
        document_id: Optional[uuid.UUID],
        score: float,
        chunk: Optional[Chunk] = None,
        raw_distance: Optional[float] = None,
    ) -> None:
        self.chunk_id = chunk_id
        self.document_id = document_id
        self.score = score
        self.chunk = chunk
        self.raw_distance = raw_distance

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<VectorMatch {self.chunk_id} score={self.score:.4f}>"


@runtime_checkable
class VectorStore(Protocol):
    """Persistence and similarity search for embedded chunks."""

    name: str

    @property
    def capabilities(self) -> VectorStoreCapabilities:
        ...

    @property
    def dimensions(self) -> int:
        """Configured dimensionality. Vectors of any other length are rejected."""
        ...

    @property
    def metric(self) -> DistanceMetric:
        ...

    def is_available(self) -> bool:
        """Cheap reachability check. Must not raise (see EmbeddingProvider.is_available)."""
        ...

    def ensure_ready(self) -> None:
        """Create collections/indexes if absent. Idempotent.

        For pgvector this is a no-op: Alembic owns the schema (ADR-001), so a runtime path must
        never issue DDL. Adapters for external services do create their collection here.
        """
        ...

    def upsert(self, chunks: Sequence[EmbeddedChunk], *, tenant_id: str = "default") -> int:
        """Insert or replace chunks. Returns the number written."""
        ...

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
        """Nearest neighbours, filtered before scoring, ordered by descending similarity."""
        ...

    def get(self, chunk_id: uuid.UUID, *, tenant_id: str = "default") -> Optional[EmbeddedChunk]:
        ...

    def delete_by_document(self, document_id: uuid.UUID, *, tenant_id: str = "default") -> int:
        """Remove all chunks of one document version. Returns the number deleted."""
        ...

    def count(self, *, tenant_id: Optional[str] = None) -> int:
        ...


__all__ = ["VectorMatch", "VectorStore", "VectorStoreCapabilities"]
