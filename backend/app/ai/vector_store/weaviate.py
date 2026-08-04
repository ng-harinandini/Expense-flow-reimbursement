"""Weaviate adapter.

Object-oriented rather than point-oriented: a class with typed properties, and vectors attached to
objects. Its filter model is a nested ``where`` tree with a typed value key per property type —
``valueText``, ``valueInt``, ``valueDate`` — which is the one thing this translation has to get
right that the others do not: passing a date as ``valueText`` matches nothing, silently.

**Not verified against a live Weaviate.** The ``where`` builder is unit tested, including the
value-key selection; the client calls are not.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Mapping, Optional, Sequence

from app.ai.core.enums import DistanceMetric, TelemetryOperation
from app.ai.core.errors import ProviderError, ProviderNotConfiguredError
from app.ai.core.types import EmbeddedChunk, EmbeddingVector, MetadataFilter
from app.ai.interfaces.vector_store import VectorMatch
from app.ai.vector_store import capabilities as caps
from app.ai.vector_store.external import (
    CHUNK_KEY,
    EXTRA_KEY,
    SPEC_KEY,
    TENANT_KEY,
    ExternalVectorStoreBase,
    chunk_from_payload,
    embedded_chunk_from_payload,
)
from app.ai.vector_store.filters import FieldKind, FilterOp, NormalizedFilter, normalize_filters
from app.core.logging import get_logger

logger = get_logger(__name__)

STORE_NAME = "weaviate"

_DISTANCES = {
    DistanceMetric.COSINE: "cosine",
    DistanceMetric.EUCLIDEAN: "l2-squared",
    DistanceMetric.INNER_PRODUCT: "dot",
}

# Weaviate's operator names.
_OPERATORS = {
    FilterOp.EQ: "Equal",
    FilterOp.NE: "NotEqual",
    FilterOp.GT: "GreaterThan",
    FilterOp.GTE: "GreaterThanEqual",
    FilterOp.LT: "LessThan",
    FilterOp.LTE: "LessThanEqual",
}


def value_key_for(spec: NormalizedFilter) -> str:
    """Which ``value*`` key a clause must use.

    Determined by the *field's declared kind*, not by the runtime type of the value, for the same
    reason ``contains`` is: a filter's meaning must not depend on how a caller happened to spell its
    argument. A JSON metadata key has no declared type, so it is compared as text — matching how
    :func:`~app.ai.vector_store.external.payload_for` writes it.
    """
    if spec.is_json:
        return "valueText"
    if spec.field.kind is FieldKind.INT:
        return "valueInt"
    if spec.field.kind is FieldKind.DATE:
        return "valueDate"
    return "valueText"


def to_weaviate_where(
    filters: Sequence[MetadataFilter] | Sequence[NormalizedFilter],
    *,
    tenant_id: str,
    spec_key: str,
) -> dict[str, Any]:
    """Translate the DSL into a Weaviate ``where`` filter."""
    normalized = normalize_filters(filters) if _is_raw(filters) else filters
    operands: list[dict[str, Any]] = [
        {"path": [TENANT_KEY], "operator": "Equal", "valueText": tenant_id},
        {"path": [SPEC_KEY], "operator": "Equal", "valueText": spec_key},
    ]

    for spec in normalized:
        path = _path(spec)
        op = spec.op
        key = value_key_for(spec)

        if op is FilterOp.EXISTS:
            operands.append({
                "path": path, "operator": "IsNull", "valueBoolean": not spec.value,
            })
        elif op in (FilterOp.IN, FilterOp.NIN):
            # No native list operator: ``in`` becomes an Or of Equals, ``nin`` an And of NotEquals.
            # Expanded here rather than left to the caller so both dialects mean the same thing.
            inner_operator = "Equal" if op is FilterOp.IN else "NotEqual"
            operands.append({
                "operator": "Or" if op is FilterOp.IN else "And",
                "operands": [
                    {"path": path, "operator": inner_operator, key: _render(v, key)}
                    for v in spec.value
                ],
            })
        elif op is FilterOp.CONTAINS:
            if spec.field.kind is FieldKind.ARRAY:
                operands.append({
                    "path": path, "operator": "ContainsAny",
                    "valueTextArray": [_render(spec.value, "valueText")],
                })
            else:
                operands.append({
                    "path": path, "operator": "Like",
                    "valueText": f"*{_render(spec.value, 'valueText')}*",
                })
        else:
            operands.append({
                "path": path, "operator": _OPERATORS[op], key: _render(spec.value, key),
            })

    return {"operator": "And", "operands": operands}


def _path(spec: NormalizedFilter) -> list[str]:
    """The property path. Nested metadata keys are flattened into one underscore-joined property.

    Weaviate does not filter into an untyped blob, so ``extra.cost_centre`` is stored and filtered
    as the property ``extra_cost_centre``. Flattening at both ends keeps the DSL unchanged.
    """
    if spec.is_json:
        return [f"{EXTRA_KEY}_" + "_".join(spec.json_path)]
    return [spec.field.name]


def _render(value: Any, key: str) -> Any:
    """Render a value for the chosen ``value*`` key.

    ``valueDate`` requires RFC 3339, so a plain date is widened to midnight UTC. Sending the bare
    ``YYYY-MM-DD`` is accepted by some versions and silently matches nothing in others.
    """
    if key == "valueDate":
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return datetime.combine(value, time.min, tzinfo=timezone.utc).isoformat()
        return str(value)
    if key == "valueInt":
        return int(value)
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _is_raw(filters: Sequence[Any]) -> bool:
    return bool(filters) and not isinstance(filters[0], NormalizedFilter)


def flatten_properties(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten the payload's nested ``extra`` into ``extra_<key>`` properties. See :func:`_path`."""
    flat: dict[str, Any] = {}
    for key, value in payload.items():
        if key == EXTRA_KEY and isinstance(value, Mapping):
            for sub_key, sub_value in _flatten(value):
                flat[f"{EXTRA_KEY}_{sub_key}"] = sub_value
            continue
        flat[key] = value
    return flat


def _flatten(extra: Mapping[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for key, value in extra.items():
        path = f"{prefix}{key}"
        if isinstance(value, Mapping):
            out.extend(_flatten(value, prefix=f"{path}_"))
        else:
            out.append((path, value))
    return out


def unflatten_properties(flat: Mapping[str, Any]) -> dict[str, Any]:
    """Reverse :func:`flatten_properties`.

    Only the first underscore after the prefix is treated as a separator, so a nested key written as
    ``extra_a_b`` reads back as ``extra["a_b"]`` rather than ``extra["a"]["b"]``. Lossy for nested
    metadata and deliberately so: guessing where the nesting was would corrupt keys that
    legitimately contain an underscore, and a flat key with the right name is the more useful of the
    two failures.
    """
    payload: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    for key, value in flat.items():
        if key.startswith(f"{EXTRA_KEY}_"):
            extra[key[len(EXTRA_KEY) + 1:]] = value
        else:
            payload[key] = value
    if extra:
        payload[EXTRA_KEY] = extra
    return payload


class WeaviateVectorStore(ExternalVectorStoreBase):
    """Dense search against a Weaviate class."""

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
                remedy="Set AI_WEAVIATE_URL to the instance endpoint.",
            )
        super().__init__(
            name=STORE_NAME,
            dimensions=dimensions,
            capabilities=caps.WEAVIATE,
            collection=collection,
            metric=metric,
            client=client,
            recorder=recorder,
        )
        self._url = url
        self._api_key = api_key

    def _build_client(self) -> Any:
        import weaviate

        if self._api_key:
            return weaviate.connect_to_weaviate_cloud(
                cluster_url=self._url,
                auth_credentials=weaviate.auth.AuthApiKey(self._api_key),
            )
        return weaviate.connect_to_local(host=self._url)

    def _ping(self, client: Any) -> None:
        client.is_ready()

    def _objects(self) -> Any:
        """The collection handle every data call goes through."""
        return self.client().collections.get(self.collection)

    def ensure_ready(self) -> None:
        """Create the class with ``vectorizer=none`` if absent.

        ``none`` matters: Weaviate can embed text itself, and letting it would put a second,
        invisible embedding model into a platform whose whole premise is that one versioned model
        produces every vector. Vectors arrive from
        :class:`~app.ai.embeddings.service.EmbeddingService` or not at all.
        """
        client = self.client()
        try:
            if client.collections.exists(self.collection):
                return
            client.collections.create(
                name=self.collection,
                vectorizer_config=None,
                vector_index_config={"distance": _DISTANCES[self.metric]},
            )
        except Exception as exc:
            raise ProviderError(self.name, f"ensure_ready failed: {exc}") from exc

    def upsert(self, chunks: Sequence[EmbeddedChunk], *, tenant_id: str = "default") -> int:
        spec_key = self._validate_batch(chunks)
        with self._span(TelemetryOperation.VECTOR_UPSERT, count=len(chunks)):
            objects = self._objects()
            try:
                for chunk in chunks:
                    objects.data.insert(
                        uuid=str(self._point_id(chunk.id, spec_key)),
                        properties=flatten_properties(
                            self._payload(chunk.chunk, spec_key=spec_key, tenant_id=tenant_id)
                        ),
                        vector=list(chunk.vector.values),
                    )
            except Exception as exc:
                raise ProviderError(self.name, f"upsert failed: {exc}") from exc
            return len(chunks)

    def delete_by_document(self, document_id: Any, *, tenant_id: str = "default") -> int:
        where = {
            "operator": "And",
            "operands": [
                {"path": [TENANT_KEY], "operator": "Equal", "valueText": tenant_id},
                {"path": ["document_id"], "operator": "Equal", "valueText": str(document_id)},
            ],
        }
        try:
            result = self._objects().data.delete_many(where=where)
        except Exception as exc:
            raise ProviderError(self.name, f"delete_by_document failed: {exc}") from exc
        return int(getattr(result, "successful", 0) or 0)

    def get(self, chunk_id: Any, *, tenant_id: str = "default") -> Optional[EmbeddedChunk]:
        where = {
            "operator": "And",
            "operands": [
                {"path": [TENANT_KEY], "operator": "Equal", "valueText": tenant_id},
                {"path": [CHUNK_KEY], "operator": "Equal", "valueText": str(chunk_id)},
            ],
        }
        try:
            response = self._objects().query.fetch_objects(
                filters=where, limit=1, include_vector=True
            )
        except Exception as exc:
            raise ProviderError(self.name, f"get failed: {exc}") from exc
        found = list(getattr(response, "objects", None) or ())
        if not found:
            return None
        obj = found[0]
        payload = unflatten_properties(dict(getattr(obj, "properties", None) or {}))
        return embedded_chunk_from_payload(payload, _vector_of(obj), self.dimensions)

    def count(self, *, tenant_id: Optional[str] = None) -> int:
        where = (
            {"path": [TENANT_KEY], "operator": "Equal", "valueText": tenant_id}
            if tenant_id is not None
            else None
        )
        try:
            response = self._objects().aggregate.over_all(filters=where, total_count=True)
        except Exception as exc:
            raise ProviderError(self.name, f"count failed: {exc}") from exc
        return int(getattr(response, "total_count", 0) or 0)

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
                response = self._objects().query.near_vector(
                    near_vector=list(vector.values),
                    limit=top_k,
                    offset=offset,
                    filters=to_weaviate_where(
                        normalized, tenant_id=tenant_id, spec_key=vector.spec_key
                    ),
                    return_metadata=["distance"],
                )
            except Exception as exc:
                raise ProviderError(self.name, f"search failed: {exc}") from exc

            found = list(getattr(response, "objects", None) or ())
            matches: list[VectorMatch] = []
            for obj in found:
                payload = unflatten_properties(dict(getattr(obj, "properties", None) or {}))
                chunk = chunk_from_payload(payload)
                distance = _distance_of(obj)
                score = self._similarity_of(distance)
                if score < score_threshold:
                    continue
                matches.append(
                    VectorMatch(
                        chunk_id=chunk.id,
                        document_id=chunk.metadata.document_id,
                        score=score,
                        chunk=chunk,
                        raw_distance=distance,
                    )
                )
            if span is not None:
                span.set_counts(candidates_in=len(found), candidates_out=len(matches))
            return matches

    def _similarity_of(self, native_distance: float) -> float:
        """Weaviate's own distance convention, one metric at a time — never one shared formula.

        ``cosine``: Weaviate's returned distance is already ``1 - cosine_similarity``, matching the
        shared conversion exactly, so it is used unchanged.

        ``dot``: Weaviate's dot distance is documented as ``1 - dot_product``, the same ``1 - x``
        shape as cosine — **not** pgvector's ``-(a·b)`` convention, which is what the shared
        conversion's ``INNER_PRODUCT`` branch expects. Routing it through that branch would compute
        ``-(1 - dot)`` and return a negative score for almost every match, so it is handled here
        instead, with the same formula as cosine.

        ``l2-squared``: the only Euclidean distance Weaviate exposes is *squared* (there is no plain
        ``l2``). The shared conversion expects the unsquared distance — it computes ``1 - d²/2``
        itself — so squaring an already-squared value would score every real match near zero. The
        value is rooted before it reaches the shared conversion.
        """
        if self.metric is DistanceMetric.EUCLIDEAN:
            return self.score_from_distance(max(0.0, native_distance) ** 0.5)
        if self.metric is DistanceMetric.INNER_PRODUCT:
            return max(0.0, min(1.0, 1.0 - native_distance))
        return self.score_from_distance(native_distance)


def _vector_of(obj: Any) -> Sequence[float]:
    raw = getattr(obj, "vector", None)
    if isinstance(raw, Mapping):  # named vectors
        raw = next(iter(raw.values()), ())
    return tuple(float(v) for v in (raw or ()))


def _distance_of(obj: Any) -> float:
    meta = getattr(obj, "metadata", None)
    return float(getattr(meta, "distance", 0.0) or 0.0)


__all__ = [
    "STORE_NAME",
    "WeaviateVectorStore",
    "flatten_properties",
    "to_weaviate_where",
    "unflatten_properties",
    "value_key_for",
]
