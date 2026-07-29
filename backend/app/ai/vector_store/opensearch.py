"""OpenSearch adapter (``knn_vector`` field, k-NN plugin).

Chosen by deployments that already run OpenSearch for logs and want one fewer system. Its filter
model is Elasticsearch's ``bool`` query, which maps onto the DSL cleanly: ``filter`` is conjunction
without scoring, ``must_not`` covers ``ne``/``nin``, ``terms`` covers ``in``, ``range`` covers the
comparisons.

**Filters go inside the ``knn`` clause, not beside it.** OpenSearch supports both, and they mean
different things: an outer ``bool.filter`` applies *after* the k-NN search has already chosen its k
neighbours, so a selective filter yields far fewer than ``top_k`` results. The ``knn.filter`` form
is the pre-filter, which is what the contract requires. This is the single most important line in
the file.

**Not verified against a live cluster.** In particular the score inversion below is derived from the
k-NN plugin's documented ``space_type`` formulas, not measured. Until it is measured against a real
cluster, treat this adapter's *scores* as unproven even where its ordering is sound — the ordering
comes from the engine and cannot be wrong; the ``[0, 1]`` mapping is this file's arithmetic.
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
    TEXT_KEY,
    ExternalVectorStoreBase,
    chunk_from_payload,
    embedded_chunk_from_payload,
)
from app.ai.vector_store.filters import FilterOp, NormalizedFilter, normalize_filters
from app.core.logging import get_logger

logger = get_logger(__name__)

STORE_NAME = "opensearch"

VECTOR_FIELD = "embedding"

# k-NN ``space_type`` per metric. Cosine is the default everywhere in this platform.
_SPACES = {
    DistanceMetric.COSINE: "cosinesimil",
    DistanceMetric.EUCLIDEAN: "l2",
    DistanceMetric.INNER_PRODUCT: "innerproduct",
}

# Keyword rather than text mapping for the fields the DSL filters on: a ``text`` mapping is
# analyzed, so ``category == "Travel & Meals"`` would match on either token instead of the whole
# value.
_KEYWORD_FIELDS = (
    TENANT_KEY, SPEC_KEY, CHUNK_KEY, "document_id", "parent_id", "policy_version", "department",
    "country", "currency", "category", "language", "source_type", "owner", "checksum", "tags",
    "strategy",
)
_DATE_FIELDS = ("effective_date", "expiry_date")
_INT_FIELDS = ("index", "token_count", "page_number")


def to_opensearch_filter(
    filters: Sequence[MetadataFilter] | Sequence[NormalizedFilter],
    *,
    tenant_id: str,
    spec_key: str,
) -> dict[str, Any]:
    """Translate the DSL into an OpenSearch ``bool`` query for use as a k-NN pre-filter."""
    normalized = normalize_filters(filters) if _is_raw(filters) else filters
    must: list[dict[str, Any]] = [
        {"term": {TENANT_KEY: tenant_id}},
        {"term": {SPEC_KEY: spec_key}},
    ]
    must_not: list[dict[str, Any]] = []

    for spec in normalized:
        field = _field_path(spec)
        op = spec.op
        value = _render(spec.value)

        if op is FilterOp.EQ:
            must.append({"term": {field: value}})
        elif op is FilterOp.NE:
            must_not.append({"term": {field: value}})
        elif op is FilterOp.IN:
            must.append({"terms": {field: [_render(v) for v in spec.value]}})
        elif op is FilterOp.NIN:
            must_not.append({"terms": {field: [_render(v) for v in spec.value]}})
        elif op is FilterOp.EXISTS:
            (must if spec.value else must_not).append({"exists": {"field": field}})
        elif op is FilterOp.CONTAINS:
            # An array field is a multi-valued keyword, so containment is a ``term``. Free text uses
            # ``match_phrase`` on the analyzed field, which is the closest equivalent to a
            # substring.
            if spec.field.name == "tags":
                must.append({"term": {field: value}})
            else:
                must.append({"match_phrase": {field: value}})
        else:
            must.append({"range": {field: {op.value: value}}})

    query: dict[str, Any] = {"bool": {"filter": must}}
    if must_not:
        query["bool"]["must_not"] = must_not
    return query


def index_mapping(
    dimensions: int, metric: DistanceMetric = DistanceMetric.COSINE
) -> dict[str, Any]:
    """The index mapping ``ensure_ready`` creates.

    Exposed as a function so it can be asserted in a test and handed to an operator creating the
    index by hand — the two must not diverge, and the way they diverge in practice is a runbook
    nobody updated.
    """
    properties: dict[str, Any] = {
        VECTOR_FIELD: {
            "type": "knn_vector",
            "dimension": dimensions,
            "method": {
                "name": "hnsw",
                "space_type": _SPACES[metric],
                "engine": "lucene",
                "parameters": {"m": 16, "ef_construction": 64},
            },
        },
        TEXT_KEY: {"type": "text"},
        "section": {"type": "text"},
        "heading_path": {"type": "keyword"},
        "extra": {"type": "object", "enabled": True},
    }
    properties.update({name: {"type": "keyword"} for name in _KEYWORD_FIELDS})
    properties.update({name: {"type": "date"} for name in _DATE_FIELDS})
    properties.update({name: {"type": "integer"} for name in _INT_FIELDS})
    return {"settings": {"index": {"knn": True}}, "mappings": {"properties": properties}}


def _field_path(spec: NormalizedFilter) -> str:
    if spec.is_json:
        return "extra." + ".".join(spec.json_path)
    return spec.field.name


def _render(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _is_raw(filters: Sequence[Any]) -> bool:
    return bool(filters) and not isinstance(filters[0], NormalizedFilter)


class OpenSearchVectorStore(ExternalVectorStoreBase):
    """Dense k-NN search against an OpenSearch index."""

    def __init__(
        self,
        *,
        dimensions: int,
        collection: str,
        url: Optional[str] = None,
        metric: DistanceMetric = DistanceMetric.COSINE,
        client: Any = None,
        recorder: Any = None,
    ) -> None:
        if client is None and not url:
            raise ProviderNotConfiguredError(
                provider=STORE_NAME, kind="VECTOR_STORE",
                remedy="Set AI_OPENSEARCH_URL to the cluster endpoint.",
            )
        super().__init__(
            name=STORE_NAME,
            dimensions=dimensions,
            capabilities=caps.OPENSEARCH,
            collection=collection,
            metric=metric,
            client=client,
            recorder=recorder,
        )
        self._url = url

    def _build_client(self) -> Any:
        from opensearchpy import OpenSearch

        return OpenSearch(hosts=[self._url])

    def _ping(self, client: Any) -> None:
        client.info()

    def ensure_ready(self) -> None:
        """Create the index with the k-NN mapping if it is absent. Never modifies an existing one.

        A mapping cannot be changed in place in OpenSearch, so "fixing" an existing index would mean
        reindexing — a data migration, not something a request path may decide to do. An index whose
        mapping is wrong therefore stays wrong and fails loudly at query time, which is the safer of
        the two outcomes.
        """
        client = self.client()
        try:
            if not client.indices.exists(index=self.collection):
                client.indices.create(
                    index=self.collection, body=index_mapping(self.dimensions, self.metric)
                )
        except Exception as exc:
            raise ProviderError(self.name, f"ensure_ready failed: {exc}") from exc

    def upsert(self, chunks: Sequence[EmbeddedChunk], *, tenant_id: str = "default") -> int:
        spec_key = self._validate_batch(chunks)
        with self._span(TelemetryOperation.VECTOR_UPSERT, count=len(chunks)):
            operations: list[dict[str, Any]] = []
            for chunk in chunks:
                doc_id = str(self._point_id(chunk.id, spec_key))
                body = self._payload(chunk.chunk, spec_key=spec_key, tenant_id=tenant_id)
                body[VECTOR_FIELD] = list(chunk.vector.values)
                # ``index`` rather than ``create``: re-indexing the same id replaces the document,
                # which is what upsert means here.
                operations.append({"index": {"_index": self.collection, "_id": doc_id}})
                operations.append(body)
            try:
                response = self.client().bulk(body=operations, refresh=True)
            except Exception as exc:
                raise ProviderError(self.name, f"upsert failed: {exc}") from exc
            if isinstance(response, dict) and response.get("errors"):
                raise ProviderError(
                    self.name, f"upsert reported per-item errors: {_first_error(response)}"
                )
            return len(chunks)

    def delete_by_document(self, document_id: Any, *, tenant_id: str = "default") -> int:
        query = {
            "query": {
                "bool": {
                    "filter": [
                        {"term": {TENANT_KEY: tenant_id}},
                        {"term": {"document_id": str(document_id)}},
                    ]
                }
            }
        }
        try:
            response = self.client().delete_by_query(
                index=self.collection, body=query, refresh=True
            )
        except Exception as exc:
            raise ProviderError(self.name, f"delete_by_document failed: {exc}") from exc
        return int((response or {}).get("deleted", 0))

    def get(self, chunk_id: Any, *, tenant_id: str = "default") -> Optional[EmbeddedChunk]:
        query = {
            "size": 1,
            "query": {
                "bool": {
                    "filter": [
                        {"term": {TENANT_KEY: tenant_id}},
                        {"term": {CHUNK_KEY: str(chunk_id)}},
                    ]
                }
            },
        }
        try:
            response = self.client().search(index=self.collection, body=query)
        except Exception as exc:
            raise ProviderError(self.name, f"get failed: {exc}") from exc
        hits = _hits_of(response)
        if not hits:
            return None
        source = dict(hits[0].get("_source") or {})
        return embedded_chunk_from_payload(
            source, source.get(VECTOR_FIELD) or (), self.dimensions
        )

    def count(self, *, tenant_id: Optional[str] = None) -> int:
        body = (
            {"query": {"bool": {"filter": [{"term": {TENANT_KEY: tenant_id}}]}}}
            if tenant_id is not None
            else {"query": {"match_all": {}}}
        )
        try:
            response = self.client().count(index=self.collection, body=body) or {}
            return int(response.get("count", 0))
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
            body = {
                "size": top_k,
                "from": offset,
                "query": {
                    "knn": {
                        VECTOR_FIELD: {
                            "vector": list(vector.values),
                            # k must cover the page being asked for, or the engine cannot fill it.
                            "k": top_k + offset,
                            # The pre-filter. See the module docstring: an outer bool.filter would
                            # apply after neighbour selection and silently shorten the page.
                            "filter": to_opensearch_filter(
                                normalized, tenant_id=tenant_id, spec_key=vector.spec_key
                            ),
                        }
                    }
                },
            }
            try:
                response = self.client().search(index=self.collection, body=body)
            except Exception as exc:
                raise ProviderError(self.name, f"search failed: {exc}") from exc

            matches: list[VectorMatch] = []
            hits = _hits_of(response)
            for hit in hits:
                source = dict(hit.get("_source") or {})
                chunk = chunk_from_payload(source)
                score = self._similarity_from_score(float(hit.get("_score") or 0.0))
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

    def _similarity_from_score(self, native: float) -> float:
        """Invert the k-NN plugin's documented score formula for the configured space type.

        ``cosinesimil`` scores as ``1 / (1 + cosine_distance)``, so the distance is ``1/score - 1``;
        ``l2`` scores as ``1 / (1 + d²)``. Both are inverted back to a distance and then run through
        the platform's shared conversion, so an OpenSearch score means the same thing as a pgvector
        one. ``innerproduct`` has a piecewise formula that is not invertible to a distance, so its
        score is clamped and used directly.

        Derived from documentation, not measured — see the module docstring.
        """
        if native <= 0.0:
            return 0.0
        if self.metric is DistanceMetric.COSINE:
            return self.score_from_distance((1.0 / native) - 1.0)
        if self.metric is DistanceMetric.EUCLIDEAN:
            squared = max(0.0, (1.0 / native) - 1.0)
            return self.score_from_distance(squared ** 0.5)
        return max(0.0, min(1.0, native))


def _hits_of(response: Any) -> list[dict[str, Any]]:
    return list(((response or {}).get("hits") or {}).get("hits") or [])


def _first_error(response: dict[str, Any]) -> str:
    for item in response.get("items") or []:
        for outcome in item.values():
            if outcome.get("error"):
                return str(outcome["error"])
    return "unspecified"


__all__ = [
    "STORE_NAME",
    "VECTOR_FIELD",
    "OpenSearchVectorStore",
    "index_mapping",
    "to_opensearch_filter",
]
