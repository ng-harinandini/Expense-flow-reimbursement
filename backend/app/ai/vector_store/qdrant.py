"""Qdrant adapter.

Purpose-built vector database: named vectors, payload indexes, and a filter language rich enough to
express the whole DSL without a post-filter. Its filter model is also the closest of the five to the
DSL's own — ``must`` is conjunction, ``must_not`` covers ``ne``/``nin``, ``match.any`` covers ``in``
— so :func:`to_qdrant_filter` is a direct mapping rather than an emulation.

**Not verified against a live Qdrant.** The filter translation and the payload mapping are unit
tested; the SDK calls are not. Treat this file as reviewed-not-proven until a live instance is in
CI.

Two Qdrant specifics worth stating:

* ``must_not`` semantics match the DSL's treatment of missing values by accident of good design: a
  point with no ``country`` key does not match ``match(country='US')``, so ``must_not`` includes
  it — which is exactly what ``ne`` must do here (see :mod:`app.ai.vector_store.filters`).
* Its ``ne`` on a *missing* field therefore needs no ``IsNull`` clause, unlike the SQL translation.
  That is the one place the two dialects differ in shape while agreeing in meaning, and it is why
  the contract suite tests semantics rather than generated queries.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from app.ai.core.enums import DistanceMetric, TelemetryOperation
from app.ai.core.errors import ProviderError, ProviderNotConfiguredError
from app.ai.core.types import EmbeddedChunk, EmbeddingVector, MetadataFilter
from app.ai.interfaces.vector_store import VectorMatch
from app.ai.vector_store import capabilities as caps
from app.ai.vector_store.external import (
    CHUNK_KEY,
    SPEC_KEY,
    TENANT_KEY,
    ExternalVectorStoreBase,
    chunk_from_payload,
    embedded_chunk_from_payload,
)
from app.ai.vector_store.filters import FilterOp, NormalizedFilter, normalize_filters
from app.core.logging import get_logger

logger = get_logger(__name__)

STORE_NAME = "qdrant"

# Qdrant's distance names. Cosine is the platform default; the mapping exists so a deployment that
# built its collection with a different distance is not silently queried with the wrong one.
_DISTANCES = {
    DistanceMetric.COSINE: "Cosine",
    DistanceMetric.EUCLIDEAN: "Euclid",
    DistanceMetric.INNER_PRODUCT: "Dot",
}


def to_qdrant_filter(
    filters: Sequence[MetadataFilter] | Sequence[NormalizedFilter],
    *,
    tenant_id: str,
    spec_key: str,
) -> dict[str, Any]:
    """Translate the DSL into a Qdrant filter, as plain dicts.

    Dicts rather than the SDK's ``models.Filter`` objects so this function is importable and
    testable on a host without ``qdrant_client`` installed — which is every CI host. The SDK accepts
    the dict form on the REST API.

    The tenant and embedding-version clauses are added here rather than left to the caller: they are
    not optional, and a translator that could produce a filter without them would eventually be
    asked to.
    """
    must: list[dict[str, Any]] = [
        {"key": TENANT_KEY, "match": {"value": tenant_id}},
        {"key": SPEC_KEY, "match": {"value": spec_key}},
    ]
    must_not: list[dict[str, Any]] = []

    for spec in normalize_filters(filters) if _is_raw(filters) else filters:
        key = _payload_key(spec)
        op = spec.op
        value = spec.value

        if op is FilterOp.EQ:
            must.append({"key": key, "match": {"value": value}})
        elif op is FilterOp.NE:
            must_not.append({"key": key, "match": {"value": value}})
        elif op is FilterOp.IN:
            must.append({"key": key, "match": {"any": list(value)}})
        elif op is FilterOp.NIN:
            must_not.append({"key": key, "match": {"any": list(value)}})
        elif op is FilterOp.CONTAINS:
            # An array payload matches an element with a plain ``match``; text uses full-text
            # ``text``, which needs a text payload index on the field to be exact rather than
            # tokenized.
            if spec.field.name == "tags":
                must.append({"key": key, "match": {"value": value}})
            else:
                must.append({"key": key, "match": {"text": value}})
        elif op is FilterOp.EXISTS:
            clause = {"is_empty": {"key": key}}
            (must if not value else must_not).append(clause)
        else:
            must.append({"key": key, "range": _range_clause(op, value)})

    result: dict[str, Any] = {"must": must}
    if must_not:
        result["must_not"] = must_not
    return result


def _range_clause(op: FilterOp, value: Any) -> dict[str, Any]:
    """``gt``/``gte``/``lt``/``lte`` as a Qdrant range.

    Dates reach here as ``date`` objects and are rendered ISO, matching how
    :func:`~app.ai.vector_store.external.payload_for` stored them — ISO-8601 compares
    lexicographically in chronological order, so a string range is a correct date range.
    """
    rendered = value.isoformat() if hasattr(value, "isoformat") else value
    return {op.value: rendered}


def _payload_key(spec: NormalizedFilter) -> str:
    """The payload key a filter addresses. JSONB paths become dotted payload paths."""
    if spec.is_json:
        return "extra." + ".".join(spec.json_path)
    return spec.field.name


def _is_raw(filters: Sequence[Any]) -> bool:
    return bool(filters) and not isinstance(filters[0], NormalizedFilter)


class QdrantVectorStore(ExternalVectorStoreBase):
    """Dense search against a Qdrant collection."""

    def __init__(
        self,
        *,
        dimensions: int,
        collection: str,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
        metric: DistanceMetric = DistanceMetric.COSINE,
        client: Any = None,
        recorder: Any = None,
    ) -> None:
        if client is None and not url:
            raise ProviderNotConfiguredError(
                provider=STORE_NAME, kind="VECTOR_STORE",
                remedy="Set AI_QDRANT_URL (and AI_QDRANT_API_KEY for Qdrant Cloud).",
            )
        super().__init__(
            name=STORE_NAME,
            dimensions=dimensions,
            capabilities=caps.QDRANT,
            collection=collection,
            metric=metric,
            client=client,
            recorder=recorder,
        )
        self._url = url
        self._api_key = api_key

    def _build_client(self) -> Any:
        from qdrant_client import QdrantClient

        return QdrantClient(url=self._url, api_key=self._api_key)

    def _ping(self, client: Any) -> None:
        client.get_collections()

    # --- lifecycle -----------------------------------------------------------

    def ensure_ready(self) -> None:
        """Create the collection and the payload indexes if they are absent.

        Unlike the PostgreSQL stores this *does* create structure, because there is no Alembic for
        Qdrant — the adapter is the only thing that knows the collection's required shape.
        Idempotent: an existing collection is left exactly as it is, never recreated, because
        recreating one deletes every vector in it.

        Payload indexes on the filtered keys are not an optimization here. Without them Qdrant
        filters by scanning payloads, and a filtered ANN query degrades to a full scan at the size
        this platform is designed for.
        """
        client = self.client()
        try:
            existing = {c.name for c in client.get_collections().collections}
            if self.collection not in existing:
                client.create_collection(
                    collection_name=self.collection,
                    vectors_config={
                        "size": self.dimensions,
                        "distance": _DISTANCES[self.metric],
                    },
                )
            for key in (TENANT_KEY, SPEC_KEY, CHUNK_KEY, "document_id", "country", "currency",
                        "category", "department", "source_type", "effective_date"):
                try:
                    client.create_payload_index(
                        collection_name=self.collection, field_name=key, field_schema="keyword",
                    )
                except Exception:  # noqa: BLE001 - an existing index is reported as an error
                    continue
        except Exception as exc:
            raise ProviderError(self.name, f"ensure_ready failed: {exc}") from exc

    # --- writes --------------------------------------------------------------

    def upsert(self, chunks: Sequence[EmbeddedChunk], *, tenant_id: str = "default") -> int:
        spec_key = self._validate_batch(chunks)
        with self._span(TelemetryOperation.VECTOR_UPSERT, count=len(chunks)):
            points = [
                {
                    "id": str(self._point_id(chunk.id, spec_key)),
                    "vector": list(chunk.vector.values),
                    "payload": self._payload(
                        chunk.chunk, spec_key=spec_key, tenant_id=tenant_id
                    ),
                }
                for chunk in chunks
            ]
            try:
                self.client().upsert(collection_name=self.collection, points=points, wait=True)
            except Exception as exc:
                raise ProviderError(self.name, f"upsert failed: {exc}") from exc
            return len(points)

    def delete_by_document(self, document_id: Any, *, tenant_id: str = "default") -> int:
        """Delete by filter, counting first because Qdrant's delete reports no count.

        Counted before deleting rather than after, so the return value means what the contract says
        it means. Two calls where one would do; the alternative is a number that is always zero and
        a caller that cannot tell a no-op from a success.
        """
        selector = {
            "must": [
                {"key": TENANT_KEY, "match": {"value": tenant_id}},
                {"key": "document_id", "match": {"value": str(document_id)}},
            ]
        }
        client = self.client()
        try:
            found = client.count(
                collection_name=self.collection, count_filter=selector, exact=True
            ).count
            if found:
                client.delete(
                    collection_name=self.collection, points_selector={"filter": selector}, wait=True
                )
            return int(found)
        except Exception as exc:
            raise ProviderError(self.name, f"delete_by_document failed: {exc}") from exc

    # --- reads ---------------------------------------------------------------

    def get(self, chunk_id: Any, *, tenant_id: str = "default") -> Optional[EmbeddedChunk]:
        """Fetch by payload rather than by point id.

        The point id is derived from ``(chunk_id, spec_key)``, and ``get`` is version-agnostic by
        contract, so the id cannot be reconstructed from its argument. Scrolling by payload is the
        only correct way to answer.
        """
        selector = {
            "must": [
                {"key": TENANT_KEY, "match": {"value": tenant_id}},
                {"key": CHUNK_KEY, "match": {"value": str(chunk_id)}},
            ]
        }
        try:
            points, _ = self.client().scroll(
                collection_name=self.collection,
                scroll_filter=selector,
                limit=1,
                with_payload=True,
                with_vectors=True,
            )
        except Exception as exc:
            raise ProviderError(self.name, f"get failed: {exc}") from exc
        if not points:
            return None
        point = points[0]
        return embedded_chunk_from_payload(
            _payload_of(point), _vector_of(point), self.dimensions
        )

    def count(self, *, tenant_id: Optional[str] = None) -> int:
        selector = (
            {"must": [{"key": TENANT_KEY, "match": {"value": tenant_id}}]}
            if tenant_id is not None
            else None
        )
        try:
            return int(
                self.client().count(
                    collection_name=self.collection, count_filter=selector, exact=True
                ).count
            )
        except Exception as exc:
            raise ProviderError(self.name, f"count failed: {exc}") from exc

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
            try:
                hits = self.client().search(
                    collection_name=self.collection,
                    query_vector=list(vector.values),
                    query_filter=to_qdrant_filter(
                        normalized, tenant_id=tenant_id, spec_key=vector.spec_key
                    ),
                    limit=top_k,
                    offset=offset,
                    with_payload=True,
                )
            except Exception as exc:
                raise ProviderError(self.name, f"search failed: {exc}") from exc

            matches: list[VectorMatch] = []
            for hit in hits:
                payload = _payload_of(hit)
                chunk = chunk_from_payload(payload)
                # Qdrant returns a *similarity* for Cosine and Dot, and a distance for Euclid. The
                # contract wants a [0, 1] similarity, so the two cases go through different paths
                # rather than one that is wrong for one of them.
                score = self._score_of(float(getattr(hit, "score", 0.0)))
                if score < score_threshold:
                    continue
                matches.append(
                    VectorMatch(
                        chunk_id=chunk.id,
                        document_id=chunk.metadata.document_id,
                        score=score,
                        chunk=chunk,
                        raw_distance=None,
                    )
                )
            if span is not None:
                span.set_counts(candidates_in=len(hits), candidates_out=len(matches))
            return matches

    def _score_of(self, native: float) -> float:
        """Qdrant's native score, mapped onto the contract's ``[0, 1]``.

        Cosine and Dot are similarities already (``[-1, 1]`` for unit vectors), so they are clamped,
        not converted. Euclid is a distance and goes through the shared conversion. Getting this
        backwards would rank correctly and score nonsensically, which is worse than failing.
        """
        if self.metric is DistanceMetric.EUCLIDEAN:
            return self.score_from_distance(native)
        return max(0.0, min(1.0, native))


def _payload_of(point: Any) -> dict[str, Any]:
    return dict(getattr(point, "payload", None) or {})


def _vector_of(point: Any) -> Sequence[float]:
    raw = getattr(point, "vector", None)
    if isinstance(raw, dict):  # named vectors
        raw = next(iter(raw.values()), ())
    return tuple(float(v) for v in (raw or ()))


__all__ = ["STORE_NAME", "QdrantVectorStore", "to_qdrant_filter"]
