"""Milvus adapter.

The highest-throughput option of the five and the most schema-oriented: a collection has typed
fields declared up front, and its filter language is a boolean *expression string* rather than a
document. That last point is why :func:`to_milvus_expression` is the centre of this file — building
an expression by string concatenation is how injection happens, so every literal goes through
:func:`_literal`, which is the only place a value becomes text.

**Metadata lives in one JSON field.** Declaring twenty scalar fields would make a new metadata key
a collection migration, which Milvus does not support in place. A single ``metadata`` JSON field
plus scalar fields for the handful that are *always* filtered — tenant, spec key, document, chunk —
keeps the schema stable while leaving the rest filterable through JSON path syntax.

**Not verified against a live Milvus.** The expression builder and the schema are unit tested; the
calls are not.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Optional, Sequence

from app.ai.core.enums import DistanceMetric, TelemetryOperation
from app.ai.core.errors import AIValidationError, ProviderError, ProviderNotConfiguredError
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

STORE_NAME = "milvus"

VECTOR_FIELD = "embedding"
METADATA_FIELD = "metadata"

# Scalar fields promoted out of the JSON blob because every query filters on them.
SCALAR_FIELDS = (TENANT_KEY, SPEC_KEY, CHUNK_KEY, "document_id")

_METRIC_TYPES = {
    DistanceMetric.COSINE: "COSINE",
    DistanceMetric.EUCLIDEAN: "L2",
    DistanceMetric.INNER_PRODUCT: "IP",
}


def to_milvus_expression(
    filters: Sequence[MetadataFilter] | Sequence[NormalizedFilter],
    *,
    tenant_id: str,
    spec_key: str,
) -> str:
    """Build the boolean expression string Milvus filters with.

    Every value is rendered by :func:`_literal`, which quotes and escapes. No caller-supplied text
    ever reaches the expression unescaped — the reason this is one function rather than inline
    concatenation at the call site.
    """
    normalized = normalize_filters(filters) if _is_raw(filters) else filters
    parts = [
        f'{TENANT_KEY} == {_literal(tenant_id)}',
        f'{SPEC_KEY} == {_literal(spec_key)}',
    ]

    for spec in normalized:
        field = _field_expression(spec)
        op = spec.op
        value = spec.value

        if op is FilterOp.EQ:
            parts.append(f"{field} == {_literal(value)}")
        elif op is FilterOp.NE:
            parts.append(f"{field} != {_literal(value)}")
        elif op is FilterOp.IN:
            parts.append(f"{field} in [{', '.join(_literal(v) for v in value)}]")
        elif op is FilterOp.NIN:
            parts.append(f"{field} not in [{', '.join(_literal(v) for v in value)}]")
        elif op is FilterOp.EXISTS:
            # Milvus 2.3+ exposes ``json_contains_...`` for JSON keys and nothing for a NULL scalar;
            # ``exists`` is therefore supported only against the metadata blob.
            if not spec.is_json:
                raise AIValidationError(
                    "Milvus has no null test for a scalar field. Use 'exists' only on an "
                    "'extra.' metadata key.",
                    details={"store": STORE_NAME, "field": spec.field.name},
                )
            keys = ", ".join(_literal(k) for k in spec.json_path)
            clause = f"json_contains_all({METADATA_FIELD}, [{keys}])"
            parts.append(clause if spec.value else f"not {clause}")
        elif op is FilterOp.CONTAINS:
            if spec.field.name == "tags":
                parts.append(
                    f'json_contains({METADATA_FIELD}["tags"], {_literal(value)})'
                )
            else:
                # Milvus supports prefix/suffix/infix matching through ``like`` with ``%``.
                escaped = str(value).replace("%", r"\%").replace("_", r"\_")
                parts.append(f'{field} like {_literal(f"%{escaped}%")}')
        else:
            parts.append(f"{field} {_COMPARISONS[op]} {_literal(value)}")

    return " and ".join(parts)


_COMPARISONS = {
    FilterOp.GT: ">",
    FilterOp.GTE: ">=",
    FilterOp.LT: "<",
    FilterOp.LTE: "<=",
}


def _field_expression(spec: NormalizedFilter) -> str:
    """A scalar field name, or a JSON path into the metadata blob."""
    if spec.is_json:
        path = "".join(f'["{key}"]' for key in spec.json_path)
        return f"{METADATA_FIELD}{path}"
    if spec.field.name in SCALAR_FIELDS:
        return spec.field.name
    return f'{METADATA_FIELD}["{spec.field.name}"]'


def _literal(value: Any) -> str:
    """Render a Python value as a Milvus expression literal, escaped.

    The one place a value becomes text. Booleans before numbers because ``bool`` is a subclass of
    ``int`` and would otherwise render as ``1``/``0``, which Milvus compares against a boolean field
    as a type error rather than as a match.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, date):
        value = value.isoformat()
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _is_raw(filters: Sequence[Any]) -> bool:
    return bool(filters) and not isinstance(filters[0], NormalizedFilter)


def collection_schema(dimensions: int) -> list[dict[str, Any]]:
    """The field list ``ensure_ready`` creates, as data so a test can assert it."""
    return [
        {"name": "id", "dtype": "VARCHAR", "is_primary": True, "max_length": 64},
        {"name": VECTOR_FIELD, "dtype": "FLOAT_VECTOR", "dim": dimensions},
        {"name": TENANT_KEY, "dtype": "VARCHAR", "max_length": 64},
        {"name": SPEC_KEY, "dtype": "VARCHAR", "max_length": 320},
        {"name": CHUNK_KEY, "dtype": "VARCHAR", "max_length": 64},
        {"name": "document_id", "dtype": "VARCHAR", "max_length": 64},
        {"name": METADATA_FIELD, "dtype": "JSON"},
    ]


class MilvusVectorStore(ExternalVectorStoreBase):
    """Dense search against a Milvus collection."""

    def __init__(
        self,
        *,
        dimensions: int,
        collection: str,
        uri: Optional[str] = None,
        metric: DistanceMetric = DistanceMetric.COSINE,
        client: Any = None,
        recorder: Any = None,
    ) -> None:
        if client is None and not uri:
            raise ProviderNotConfiguredError(
                provider=STORE_NAME, kind="VECTOR_STORE",
                remedy="Set AI_MILVUS_URI (e.g. http://localhost:19530 or a Zilliz endpoint).",
            )
        super().__init__(
            name=STORE_NAME,
            dimensions=dimensions,
            capabilities=caps.MILVUS,
            collection=collection,
            metric=metric,
            client=client,
            recorder=recorder,
        )
        self._uri = uri

    def _build_client(self) -> Any:
        from pymilvus import MilvusClient

        return MilvusClient(uri=self._uri)

    def _ping(self, client: Any) -> None:
        client.list_collections()

    def ensure_ready(self) -> None:
        """Create the collection and its vector index if absent. Never alters an existing one."""
        client = self.client()
        try:
            if self.collection in set(client.list_collections() or ()):
                return
            client.create_collection(
                collection_name=self.collection,
                dimension=self.dimensions,
                metric_type=_METRIC_TYPES[self.metric],
                id_type="string",
                max_length=64,
                schema=collection_schema(self.dimensions),
            )
        except Exception as exc:
            raise ProviderError(self.name, f"ensure_ready failed: {exc}") from exc

    def upsert(self, chunks: Sequence[EmbeddedChunk], *, tenant_id: str = "default") -> int:
        spec_key = self._validate_batch(chunks)
        with self._span(TelemetryOperation.VECTOR_UPSERT, count=len(chunks)):
            rows: list[dict[str, Any]] = []
            for chunk in chunks:
                payload = self._payload(chunk.chunk, spec_key=spec_key, tenant_id=tenant_id)
                rows.append({
                    "id": str(self._point_id(chunk.id, spec_key)),
                    VECTOR_FIELD: list(chunk.vector.values),
                    TENANT_KEY: tenant_id,
                    SPEC_KEY: spec_key,
                    CHUNK_KEY: str(chunk.id),
                    "document_id": str(payload.get("document_id") or ""),
                    METADATA_FIELD: payload,
                })
            try:
                self.client().upsert(collection_name=self.collection, data=rows)
            except Exception as exc:
                raise ProviderError(self.name, f"upsert failed: {exc}") from exc
            return len(rows)

    def delete_by_document(self, document_id: Any, *, tenant_id: str = "default") -> int:
        expression = (
            f"{TENANT_KEY} == {_literal(tenant_id)} and "
            f"document_id == {_literal(str(document_id))}"
        )
        try:
            result = self.client().delete(collection_name=self.collection, filter=expression)
        except Exception as exc:
            raise ProviderError(self.name, f"delete_by_document failed: {exc}") from exc
        return _delete_count(result)

    def get(self, chunk_id: Any, *, tenant_id: str = "default") -> Optional[EmbeddedChunk]:
        expression = (
            f"{TENANT_KEY} == {_literal(tenant_id)} and "
            f"{CHUNK_KEY} == {_literal(str(chunk_id))}"
        )
        try:
            rows = self.client().query(
                collection_name=self.collection,
                filter=expression,
                output_fields=[METADATA_FIELD, VECTOR_FIELD, SPEC_KEY],
                limit=1,
            )
        except Exception as exc:
            raise ProviderError(self.name, f"get failed: {exc}") from exc
        if not rows:
            return None
        row = rows[0]
        payload = dict(row.get(METADATA_FIELD) or {})
        payload.setdefault(SPEC_KEY, row.get(SPEC_KEY) or "")
        return embedded_chunk_from_payload(
            payload, row.get(VECTOR_FIELD) or (), self.dimensions
        )

    def count(self, *, tenant_id: Optional[str] = None) -> int:
        expression = f"{TENANT_KEY} == {_literal(tenant_id)}" if tenant_id is not None else ""
        try:
            rows = self.client().query(
                collection_name=self.collection,
                filter=expression,
                output_fields=["count(*)"],
            )
        except Exception as exc:
            raise ProviderError(self.name, f"count failed: {exc}") from exc
        if not rows:
            return 0
        first = rows[0]
        return int(first.get("count(*)") or 0) if isinstance(first, Mapping) else 0

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
                results = self.client().search(
                    collection_name=self.collection,
                    data=[list(vector.values)],
                    filter=to_milvus_expression(
                        normalized, tenant_id=tenant_id, spec_key=vector.spec_key
                    ),
                    limit=top_k,
                    offset=offset,
                    output_fields=[METADATA_FIELD, SPEC_KEY],
                    search_params={"metric_type": _METRIC_TYPES[self.metric]},
                )
            except Exception as exc:
                raise ProviderError(self.name, f"search failed: {exc}") from exc

            # Milvus returns one result list per query vector, and there is exactly one here.
            hits = list(results[0]) if results else []
            matches: list[VectorMatch] = []
            for hit in hits:
                entity = dict((hit.get("entity") if isinstance(hit, Mapping) else {}) or {})
                payload = dict(entity.get(METADATA_FIELD) or {})
                chunk = chunk_from_payload(payload)
                score = self._similarity_of(float((hit or {}).get("distance") or 0.0))
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

    def _similarity_of(self, native: float) -> float:
        """Milvus's ``distance`` is a similarity for COSINE and IP, a squared distance for L2."""
        if self.metric is DistanceMetric.EUCLIDEAN:
            return self.score_from_distance(max(0.0, native) ** 0.5)
        return max(0.0, min(1.0, native))


def _delete_count(result: Any) -> int:
    if isinstance(result, Mapping):
        return int(result.get("delete_count") or 0)
    return int(getattr(result, "delete_count", 0) or 0)


__all__ = [
    "METADATA_FIELD",
    "SCALAR_FIELDS",
    "STORE_NAME",
    "VECTOR_FIELD",
    "MilvusVectorStore",
    "collection_schema",
    "to_milvus_expression",
]
