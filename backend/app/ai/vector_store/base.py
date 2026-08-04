"""Shared behaviour every vector store adapter inherits.

The ``VectorStore`` protocol in ``app/ai/interfaces`` states the contract; this class implements the
parts of it that must not be re-implemented per adapter, because six independent implementations of
the same validation is six chances to get one of them subtly wrong:

* **Dimension checks.** A vector of the wrong width is rejected before it reaches an engine that
  might pad, truncate, or store it and fail only at query time.
* **Spec-key homogeneity.** One upsert batch carries exactly one embedding version. A batch mixing
  versions is always a bug in the caller, and unrecoverable once written: the store cannot tell
  afterwards which vectors were comparable to which.
* **Score normalization.** Engines return distances on incompatible scales — cosine distance in
  ``[0, 2]``, Euclidean in ``[0, inf)``, pgvector's negated dot product in ``[-1, 1]``. The contract
  promises a similarity in ``[0, 1]`` where 1 is identical, so the conversion lives here, once, and
  is metric-aware. Every mapping is *monotone decreasing* in distance, which is what lets an adapter
  order by the engine's native distance and still return contract-correct scores.
* **Paging validation.** A negative offset or an unbounded ``top_k`` is refused rather than passed
  down: on an approximate index, ``top_k`` above ``ef_search`` silently degrades recall, so an
  enormous ``top_k`` does not just cost time, it changes the answer.

Adapters implement only what is genuinely engine-specific: how to write, how to filter, how to order
by distance. Everything above is inherited and therefore identical across all eight.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter
from typing import Any, Optional, Sequence

from app.ai.core.enums import (
    ChunkStrategy,
    DistanceMetric,
    KnowledgeSourceType,
    TelemetryOperation,
)
from app.ai.core.errors import AIValidationError, DimensionMismatchError
from app.ai.core.types import EmbeddedChunk, EmbeddingVector, MetadataFilter
from app.ai.embeddings.math import similarity_from_distance
from app.ai.interfaces.vector_store import VectorMatch, VectorStoreCapabilities
from app.ai.vector_store.filters import NormalizedFilter, normalize_filters
from app.core.logging import get_logger

logger = get_logger(__name__)

# An upper bound on ``top_k``. Not arbitrary: HNSW cannot return more neighbours than ``ef_search``
# without degrading into a scan, and every candidate returned is a row hydrated, scored and held in
# memory downstream. A caller wanting more wants an export, which is a different operation.
MAX_TOP_K = 500

# Same for offset. Deep paging through an approximate index is not meaningful — page 40 of an ANN
# result set is noise — and an unbounded offset makes the database do unbounded work.
MAX_OFFSET = 10_000


class BaseVectorStore(ABC):
    """Base for every adapter. Implements the invariants; leaves the engine work abstract."""

    def __init__(
        self,
        *,
        name: str,
        dimensions: int,
        capabilities: VectorStoreCapabilities,
        metric: DistanceMetric = DistanceMetric.COSINE,
        recorder: Any = None,
    ) -> None:
        if dimensions < 1:
            raise AIValidationError(f"Vector dimensions must be >= 1, received {dimensions}.")
        maximum = capabilities.max_dimensions
        if maximum is not None and dimensions > maximum:
            # Caught at construction, not at index-creation time, where the failure would be a
            # migration that half-applied.
            raise DimensionMismatchError(expected=maximum, actual=dimensions)

        self.name = name
        self._dimensions = dimensions
        self._capabilities = capabilities
        self._metric = metric
        self._recorder = recorder

    # --- declared properties -------------------------------------------------

    @property
    def capabilities(self) -> VectorStoreCapabilities:
        return self._capabilities

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def metric(self) -> DistanceMetric:
        return self._metric

    # --- the abstract surface ------------------------------------------------

    @abstractmethod
    def is_available(self) -> bool:
        """Cheap reachability check. Must never raise (the registry reads a raise as "no")."""

    @abstractmethod
    def ensure_ready(self) -> None:
        """Create whatever the engine needs, idempotently. A no-op where Alembic owns the schema."""

    @abstractmethod
    def upsert(self, chunks: Sequence[EmbeddedChunk], *, tenant_id: str = "default") -> int:
        ...

    @abstractmethod
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
        ...

    @abstractmethod
    def get(self, chunk_id: Any, *, tenant_id: str = "default") -> Optional[EmbeddedChunk]:
        ...

    @abstractmethod
    def delete_by_document(self, document_id: Any, *, tenant_id: str = "default") -> int:
        ...

    @abstractmethod
    def count(self, *, tenant_id: Optional[str] = None) -> int:
        ...

    # --- validation ----------------------------------------------------------

    def _validate_batch(self, chunks: Sequence[EmbeddedChunk]) -> str:
        """Check an upsert batch and return the single embedding version it carries.

        Returning the spec key rather than just validating means the caller cannot forget to derive
        it, and cannot derive it from a different element than the one that was checked.
        """
        if not chunks:
            raise AIValidationError("An upsert batch must contain at least one chunk.")

        spec_keys = {c.vector.spec_key for c in chunks}
        if len(spec_keys) > 1:
            raise AIValidationError(
                "An upsert batch must carry exactly one embedding version; received "
                f"{sorted(spec_keys)}. Vectors from different models are not comparable, and once "
                "written together there is no way to tell which are which.",
                details={"specKeys": sorted(spec_keys)},
            )
        spec_key = spec_keys.pop()
        if not spec_key:
            raise AIValidationError(
                "Every vector must carry a spec key. A vector whose provenance is lost cannot be "
                "compared to anything, re-derived, or invalidated when the model changes."
            )

        ids = [c.id for c in chunks]
        if len(set(ids)) != len(ids):
            # A single batch is one write, not a sequence of writes — clause 1's idempotency is
            # about repeated *calls*, not the last of several entries for one chunk silently
            # winning inside one call. On a PostgreSQL-backed store two entries for one chunk miss
            # the "already exists" check and both attempt an INSERT, surfacing as a raw unique-
            # constraint violation instead of a clear caller error.
            counts = Counter(ids)
            duplicates = sorted(str(chunk_id) for chunk_id, n in counts.items() if n > 1)
            raise AIValidationError(
                f"An upsert batch must not repeat a chunk id; found {len(duplicates)} repeated.",
                details={"duplicateChunkIds": duplicates[:20]},
            )

        for chunk in chunks:
            self._validate_dimensions(chunk.vector)
        return spec_key

    def _validate_dimensions(self, vector: EmbeddingVector) -> None:
        if vector.dimensions != self._dimensions:
            raise DimensionMismatchError(expected=self._dimensions, actual=vector.dimensions)
        if len(vector.values) != self._dimensions:  # pragma: no cover - EmbeddingVector guards this
            raise DimensionMismatchError(expected=self._dimensions, actual=len(vector.values))

    def _validate_query(
        self,
        vector: EmbeddingVector,
        *,
        top_k: int,
        offset: int,
        score_threshold: float,
        tenant_id: str,
    ) -> None:
        self._validate_dimensions(vector)
        if not tenant_id or not tenant_id.strip():
            raise AIValidationError(
                "A tenant id is required for every search. There is no cross-tenant search."
            )
        if top_k < 1:
            raise AIValidationError(f"top_k must be >= 1, received {top_k}.")
        if top_k > MAX_TOP_K:
            raise AIValidationError(
                f"top_k must be <= {MAX_TOP_K}, received {top_k}. Above this the approximate index "
                "silently loses recall; use the ingestion or export path for bulk reads.",
                details={"maxTopK": MAX_TOP_K},
            )
        if offset < 0:
            raise AIValidationError(f"offset must be >= 0, received {offset}.")
        if offset > MAX_OFFSET:
            raise AIValidationError(
                f"offset must be <= {MAX_OFFSET}, received {offset}.",
                details={"maxOffset": MAX_OFFSET},
            )
        if not 0.0 <= score_threshold <= 1.0:
            raise AIValidationError(
                "score_threshold is a normalized similarity and must be within [0, 1], received "
                f"{score_threshold}."
            )
        if offset > 0 and not self._capabilities.supports_pagination:
            raise AIValidationError(
                f"The '{self.name}' store does not support paging (see its capability matrix "
                "entry). Raise top_k instead of paging.",
                details={"store": self.name},
            )

    def _normalize_filters(self, filters: Sequence[MetadataFilter]) -> tuple[NormalizedFilter, ...]:
        """Validate filters, refusing them outright on a store that cannot apply them.

        Silently ignoring filters an engine cannot express would return a *wider* result set than
        asked for — the failure mode this whole layer exists to prevent.
        """
        normalized = normalize_filters(filters)
        if normalized and not self._capabilities.supports_filters:
            raise AIValidationError(
                f"The '{self.name}' store cannot apply metadata filters, and applying them after "
                "scoring would return fewer than top_k results without saying so.",
                details={"store": self.name},
            )
        return normalized

    # --- scoring -------------------------------------------------------------

    def score_from_distance(self, distance: float) -> float:
        """Convert the engine's native distance to the contract's ``[0, 1]`` similarity.

        Monotone decreasing for all three metrics, so ordering by ascending distance and ordering by
        descending score are the same ordering — which lets an adapter have the engine do
        the sorting and convert afterwards.
        """
        return score_from_distance(distance, self._metric)

    # --- telemetry -----------------------------------------------------------

    def _span(self, operation: TelemetryOperation, **attributes: Any):
        if self._recorder is None:
            return _NullSpanContext()
        return self._recorder.span(
            operation, attributes={"store": self.name, "metric": self._metric.value, **attributes}
        )

    # --- introspection -------------------------------------------------------

    def describe(self) -> dict[str, Any]:
        """Active configuration, for ``/metrics`` and API response metadata."""
        return {
            "store": self.name,
            "dimensions": self._dimensions,
            "metric": self._metric.value,
            "available": self._safe_available(),
            **self._capabilities.to_dict(),
        }

    def _safe_available(self) -> bool:
        """``is_available`` that cannot break introspection, mirroring the registry's guard."""
        try:
            return bool(self.is_available())
        except Exception:  # pragma: no cover - an adapter's is_available should not raise
            return False

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} {self.name} {self._dimensions}d {self._metric.value}>"


def score_from_distance(distance: float, metric: DistanceMetric) -> float:
    """Normalize a distance to a ``[0, 1]`` similarity for the given metric.

    A module-level function as well as a method because the retrieval engine (M7) fuses scores from
    stores configured with different metrics, and needs the conversion without holding a store.

    **All three metrics give the same score for the same pair of unit vectors.** That is the point
    of the specific formulas below, and it is what makes ``AI_VECTOR_DISTANCE_METRIC`` a genuinely
    free choice: switching it changes which index and operator the engine uses, not what any caller
    sees. Every embedding provider the platform ships returns L2-normalized vectors (asserted by the
    M5 provider contract suite) and ``AI_EMBEDDING_NORMALIZE`` defaults on, so the unit-vector
    assumption holds wherever the platform's own vectors are involved.

    * ``COSINE`` — pgvector's ``<=>`` is ``1 - cos``; see
      :func:`~app.ai.embeddings.math.similarity_from_distance`.
    * ``INNER_PRODUCT`` — pgvector's ``<#>`` is ``-(a·b)``, and for unit vectors ``a·b`` *is* the
      cosine, so the score is ``-d`` clamped.
    * ``EUCLIDEAN`` — for unit vectors ``d² = 2 - 2·cos``, so ``cos = 1 - d²/2``. Exact for unit
      vectors; for vectors that are not unit length this clamps to 0 well before the true distance
      ceiling, which is one more reason cosine is the default.
    """
    value = float(distance)
    if metric is DistanceMetric.COSINE:
        return similarity_from_distance(value)
    if metric is DistanceMetric.INNER_PRODUCT:
        return max(0.0, min(1.0, -value))
    if metric is DistanceMetric.EUCLIDEAN:
        return max(0.0, min(1.0, 1.0 - (value * value) / 2.0))
    raise AIValidationError(  # pragma: no cover - DistanceMetric is closed
        f"No score normalization defined for metric '{metric}'."
    )


def coerce_source_type(value: Optional[str]) -> Optional[KnowledgeSourceType]:
    """Read a stored ``source_type`` string, tolerating one this build does not know.

    ``source_type`` is deliberately ``VARCHAR`` (and a plain payload string in the external stores)
    so that new kinds of knowledge need no migration. The corollary is that a stored row can hold a
    value this build's enum lacks — after a rollback, or when a newer writer shares the corpus.
    Refusing to return the chunk would turn a forward-compatible column into an outage, so the value
    is dropped and logged.
    """
    if not value:
        return None
    try:
        return KnowledgeSourceType.coerce(value)
    except ValueError:
        logger.warning("ai.chunk.unknown_source_type", extra={"value": value})
        return None


def coerce_strategy(value: Optional[str]) -> ChunkStrategy:
    """Read a stored chunk strategy, defaulting to ``RECURSIVE`` for an unknown one."""
    if not value:
        return ChunkStrategy.RECURSIVE
    try:
        return ChunkStrategy.coerce(value)
    except ValueError:
        logger.warning("ai.chunk.unknown_strategy", extra={"value": value})
        return ChunkStrategy.RECURSIVE


class _NullSpanContext:
    """Stands in for a span when no recorder is wired, so call sites stay branch-free."""

    def __enter__(self):
        return None

    def __exit__(self, *_: object) -> None:
        return None


__all__ = [
    "MAX_OFFSET",
    "MAX_TOP_K",
    "BaseVectorStore",
    "coerce_source_type",
    "coerce_strategy",
    "score_from_distance",
]
