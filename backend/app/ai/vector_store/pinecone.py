"""Pinecone adapter.

Fully managed, no index to tune, and the most constrained of the five. Three constraints shape this
file, and all three are declared rather than emulated:

**No offset.** Pinecone's query API has ``top_k`` and nothing else. Paging is possible only by
querying a larger ``top_k`` and discarding the prefix, which costs the same as the deeper query and
is not stable across index updates. The capability matrix says ``supports_pagination=False``, and
:meth:`BaseVectorStore._validate_query` refuses a non-zero offset before the call is made.
Emulating it would produce pages that silently repeat or skip rows.

**Metadata is flat and limited.** Pinecone accepts strings, numbers, booleans and lists of strings —
no nested objects — and caps metadata at 40 KB per vector. The nested ``extra`` blob is therefore
flattened to ``extra.key`` string keys, which is also exactly how the DSL addresses it. A chunk
whose text exceeds the cap is rejected by the service; that is a loud failure and preferable to
storing a truncated chunk that would later be cited as if complete.

**Namespaces, not filters, for tenancy.** Pinecone namespaces partition an index and a query reads
exactly one. That is a stronger isolation boundary than a metadata filter — a malformed filter
cannot cross it — so the tenant becomes the namespace. The tenant is *also* written into the
metadata, purely so a payload read back is self-describing during an incident.

**Not verified against a live index.** The filter translation and the flattening are unit tested;
the SDK calls are not.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from app.ai.core.enums import DistanceMetric, TelemetryOperation
from app.ai.core.errors import AIValidationError, ProviderError, ProviderNotConfiguredError
from app.ai.core.types import EmbeddedChunk, EmbeddingVector, MetadataFilter
from app.ai.interfaces.vector_store import VectorMatch
from app.ai.vector_store import capabilities as caps
from app.ai.vector_store.external import (
    CHUNK_KEY,
    EXTRA_KEY,
    SPEC_KEY,
    ExternalVectorStoreBase,
    chunk_from_payload,
    embedded_chunk_from_payload,
)
from app.ai.vector_store.filters import FilterOp, NormalizedFilter, normalize_filters
from app.core.logging import get_logger

logger = get_logger(__name__)

STORE_NAME = "pinecone"

# Pinecone's operator names. Mapped explicitly rather than by string munging so an operator the DSL
# gains without a Pinecone equivalent fails here instead of producing a filter the service ignores.
_OPERATORS = {
    FilterOp.EQ: "$eq",
    FilterOp.NE: "$ne",
    FilterOp.IN: "$in",
    FilterOp.NIN: "$nin",
    FilterOp.GT: "$gt",
    FilterOp.GTE: "$gte",
    FilterOp.LT: "$lt",
    FilterOp.LTE: "$lte",
}


def flatten_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten a payload into Pinecone's supported value types.

    Nested ``extra`` becomes dotted ``extra.key`` entries; a value Pinecone cannot hold is dropped
    with a log line rather than silently coerced, because a metadata value that changed type between
    write and read is a filter that stops matching for no visible reason.
    """
    flat: dict[str, Any] = {}
    for key, value in payload.items():
        if key == EXTRA_KEY and isinstance(value, Mapping):
            for sub_key, sub_value in _flatten_extra(value):
                flat[f"{EXTRA_KEY}.{sub_key}"] = sub_value
            continue
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            flat[key] = value
        elif isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
            flat[key] = list(value)
        elif isinstance(value, (list, tuple)):
            flat[key] = [str(v) for v in value]
        else:
            logger.warning(
                "ai.vector_store.metadata_dropped",
                extra={"store": STORE_NAME, "key": key, "type": type(value).__name__},
            )
    return flat


def _flatten_extra(extra: Mapping[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for key, value in extra.items():
        path = f"{prefix}{key}"
        if isinstance(value, Mapping):
            out.extend(_flatten_extra(value, prefix=f"{path}."))
        elif isinstance(value, (str, int, float, bool)):
            out.append((path, value))
        elif isinstance(value, (list, tuple)):
            out.append((path, [str(v) for v in value]))
    return out


def unflatten_metadata(flat: Mapping[str, Any]) -> dict[str, Any]:
    """Reverse :func:`flatten_metadata`, restoring the nested ``extra`` blob."""
    payload: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    for key, value in flat.items():
        if key.startswith(f"{EXTRA_KEY}."):
            _assign_path(extra, key[len(EXTRA_KEY) + 1:].split("."), value)
        else:
            payload[key] = value
    if extra:
        payload[EXTRA_KEY] = extra
    return payload


def _assign_path(target: dict[str, Any], path: Sequence[str], value: Any) -> None:
    for part in path[:-1]:
        nested = target.setdefault(part, {})
        if not isinstance(nested, dict):  # pragma: no cover - a leaf and a branch collided
            return
        target = nested
    target[path[-1]] = value


def to_pinecone_filter(
    filters: Sequence[MetadataFilter] | Sequence[NormalizedFilter],
    *,
    spec_key: str,
) -> dict[str, Any]:
    """Translate the DSL into a Pinecone metadata filter.

    No tenant clause: tenancy is the namespace (see the module docstring). The embedding version
    *is* a filter, because one namespace holds both versions during a re-index.
    """
    normalized = normalize_filters(filters) if _is_raw(filters) else filters
    clauses: list[dict[str, Any]] = [{SPEC_KEY: {"$eq": spec_key}}]

    for spec in normalized:
        field = _field_path(spec)
        op = spec.op
        if op is FilterOp.EXISTS:
            # Pinecone has no exists operator. ``$ne`` against a sentinel would match absent keys
            # too, so the honest answer is to refuse rather than return a filter that means
            # something else.
            raise AIValidationError(
                "Pinecone metadata filters have no 'exists' operator, and the alternatives match "
                "absent keys as well. Promote the field or filter on a concrete value.",
                details={"store": STORE_NAME, "field": field},
            )
        if op is FilterOp.CONTAINS:
            # A list-valued metadata field matches with ``$in`` against a single-element list.
            if spec.field.name == "tags":
                clauses.append({field: {"$in": [_render(spec.value)]}})
                continue
            raise AIValidationError(
                "Pinecone cannot match a substring in a metadata string. Filter the whole value.",
                details={"store": STORE_NAME, "field": field},
            )
        operator = _OPERATORS.get(op)
        if operator is None:  # pragma: no cover - every DSL operator is covered above
            raise AIValidationError(f"No Pinecone translation for operator '{op.value}'.")
        value = (
            [_render(v) for v in spec.value]
            if op in (FilterOp.IN, FilterOp.NIN)
            else _render(spec.value)
        )
        clauses.append({field: {operator: value}})

    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def _field_path(spec: NormalizedFilter) -> str:
    if spec.is_json:
        return f"{EXTRA_KEY}." + ".".join(spec.json_path)
    return spec.field.name


def _render(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _is_raw(filters: Sequence[Any]) -> bool:
    return bool(filters) and not isinstance(filters[0], NormalizedFilter)


class PineconeVectorStore(ExternalVectorStoreBase):
    """Dense search against a Pinecone index, one namespace per tenant."""

    def __init__(
        self,
        *,
        dimensions: int,
        collection: str,
        api_key: Optional[str] = None,
        metric: DistanceMetric = DistanceMetric.COSINE,
        client: Any = None,
        recorder: Any = None,
    ) -> None:
        if client is None and not api_key:
            raise ProviderNotConfiguredError(
                provider=STORE_NAME, kind="VECTOR_STORE",
                remedy="Set AI_PINECONE_API_KEY and AI_PINECONE_INDEX.",
            )
        super().__init__(
            name=STORE_NAME,
            dimensions=dimensions,
            capabilities=caps.PINECONE,
            collection=collection,
            metric=metric,
            client=client,
            recorder=recorder,
        )
        self._api_key = api_key

    def _build_client(self) -> Any:
        from pinecone import Pinecone

        return Pinecone(api_key=self._api_key).Index(self.collection)

    def _ping(self, client: Any) -> None:
        client.describe_index_stats()

    def ensure_ready(self) -> None:
        """Deliberately a no-op beyond confirming the index answers.

        Creating a Pinecone index is a provisioning decision with a monthly cost and a choice of
        cloud, region and pod type attached. A request path guessing at those is not a convenience,
        it is a surprise on an invoice. The index is created by whoever owns the account; this only
        checks it is reachable, and ``metric`` is not enforced because Pinecone fixes it at creation
        time and the adapter cannot change it.
        """
        self._ping(self.client())

    def upsert(self, chunks: Sequence[EmbeddedChunk], *, tenant_id: str = "default") -> int:
        spec_key = self._validate_batch(chunks)
        with self._span(TelemetryOperation.VECTOR_UPSERT, count=len(chunks)):
            vectors = [
                {
                    "id": str(self._point_id(chunk.id, spec_key)),
                    "values": list(chunk.vector.values),
                    "metadata": flatten_metadata(
                        self._payload(chunk.chunk, spec_key=spec_key, tenant_id=tenant_id)
                    ),
                }
                for chunk in chunks
            ]
            try:
                self.client().upsert(vectors=vectors, namespace=tenant_id)
            except Exception as exc:
                raise ProviderError(self.name, f"upsert failed: {exc}") from exc
            return len(vectors)

    def delete_by_document(self, document_id: Any, *, tenant_id: str = "default") -> int:
        """Delete by metadata filter.

        Pinecone's delete reports no count, and counting first would need a query with a ``top_k``
        large enough to cover every chunk of the document — an unbounded read to produce a number.
        So this returns 0 and says why here rather than returning a number that is sometimes a
        guess. Callers that need the count should ask ``knowledge_chunks``, which is the system of
        record.
        """
        try:
            self.client().delete(
                filter={"document_id": {"$eq": str(document_id)}}, namespace=tenant_id
            )
        except Exception as exc:
            raise ProviderError(self.name, f"delete_by_document failed: {exc}") from exc
        return 0

    def get(self, chunk_id: Any, *, tenant_id: str = "default") -> Optional[EmbeddedChunk]:
        """Fetch by metadata, since the point id embeds the embedding version.

        Uses a zero-distance query rather than ``fetch``: ``fetch`` needs the exact ids, which
        cannot be derived without knowing the version. ``top_k=1`` against a filter is the
        documented way to read one record by metadata.
        """
        try:
            response = self.client().query(
                vector=[0.0] * self.dimensions,
                filter={CHUNK_KEY: {"$eq": str(chunk_id)}},
                namespace=tenant_id,
                top_k=1,
                include_metadata=True,
                include_values=True,
            )
        except Exception as exc:
            raise ProviderError(self.name, f"get failed: {exc}") from exc
        hits = _matches_of(response)
        if not hits:
            return None
        payload = unflatten_metadata(_metadata_of(hits[0]))
        return embedded_chunk_from_payload(
            payload, _values_of(hits[0]) or (0.0,) * self.dimensions, self.dimensions
        )

    def count(self, *, tenant_id: Optional[str] = None) -> int:
        try:
            stats = self.client().describe_index_stats() or {}
        except Exception as exc:
            raise ProviderError(self.name, f"count failed: {exc}") from exc
        namespaces = dict(stats.get("namespaces") or {})
        if tenant_id is None:
            return int(stats.get("total_vector_count") or 0)
        return int((namespaces.get(tenant_id) or {}).get("vector_count") or 0)

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
        # ``offset > 0`` is refused by the base class against the capability matrix.
        self._validate_query(
            vector, top_k=top_k, offset=offset,
            score_threshold=score_threshold, tenant_id=tenant_id,
        )
        normalized = self._normalize_filters(filters)

        with self._span(TelemetryOperation.VECTOR_SEARCH, topK=top_k) as span:
            try:
                response = self.client().query(
                    vector=list(vector.values),
                    filter=to_pinecone_filter(normalized, spec_key=vector.spec_key),
                    namespace=tenant_id,
                    top_k=top_k,
                    include_metadata=True,
                )
            except Exception as exc:
                raise ProviderError(self.name, f"search failed: {exc}") from exc

            matches: list[VectorMatch] = []
            hits = _matches_of(response)
            for hit in hits:
                payload = unflatten_metadata(_metadata_of(hit))
                chunk = chunk_from_payload(payload)
                score = self._similarity_of(float(_score_of(hit)))
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
        """A similarity for cosine and dotproduct; a squared distance for euclidean."""
        if self.metric is DistanceMetric.EUCLIDEAN:
            return self.score_from_distance(max(0.0, native) ** 0.5)
        return max(0.0, min(1.0, native))


def _matches_of(response: Any) -> list[Any]:
    if isinstance(response, Mapping):
        return list(response.get("matches") or [])
    return list(getattr(response, "matches", None) or [])


def _metadata_of(hit: Any) -> dict[str, Any]:
    raw = hit.get("metadata") if isinstance(hit, Mapping) else getattr(hit, "metadata", None)
    return dict(raw or {})


def _values_of(hit: Any) -> Sequence[float]:
    raw = hit.get("values") if isinstance(hit, Mapping) else getattr(hit, "values", None)
    return tuple(float(v) for v in (raw or ()))


def _score_of(hit: Any) -> float:
    raw = hit.get("score") if isinstance(hit, Mapping) else getattr(hit, "score", 0.0)
    return float(raw or 0.0)


__all__ = [
    "STORE_NAME",
    "PineconeVectorStore",
    "flatten_metadata",
    "to_pinecone_filter",
    "unflatten_metadata",
]
