"""What the five external vector-service adapters share.

Qdrant, OpenSearch, Pinecone, Milvus and Weaviate differ in their APIs and agree on their shape: a
point with an id, a vector, and a flat payload of metadata you can filter on. Everything that
follows
from that shape lives here — id derivation, payload construction, payload parsing — so the
vendor-specific files contain only the vendor's own calls and its filter dialect.

That split is what makes an unverifiable adapter testable. None of these five is exercised against a
live service in this repository (see the declared debt in the plan), so the amount of *logic*
sitting behind the network call is exactly the amount of untested logic. Keeping the metadata
mapping here, where it is covered by the shared contract suite, and keeping each filter translator a
pure function with its own unit tests, leaves the unverified surface as a thin layer of SDK calls.

**Three decisions the shape forces:**

*The point id includes the embedding version.* A chunk legitimately has two vectors during a
re-index — one per model — and a store keyed on chunk id alone would have the second overwrite the
first, silently destroying the ability to roll back. The id is therefore a deterministic UUID5 of
``chunk_id:spec_key``: stable, so upsert stays idempotent, and distinct per version. UUID rather
than a composite string because Qdrant accepts only UUIDs and unsigned integers as point ids.

*Dates are stored as ISO-8601 strings.* None of these services has a date type, and ISO-8601 sorts
lexicographically in the same order it sorts chronologically, so ``lte`` on a date filter is correct
as a string comparison. Storing an epoch number would work too and would make every payload
unreadable in a vendor console during an incident.

*The payload carries the chunk text.* The contract's ``get`` returns an ``EmbeddedChunk``, which
needs
it, and hydrating from PostgreSQL instead would make the adapter depend on a database the deployment
may have deliberately moved retrieval away from. This has a governance consequence and it is not
hidden: choosing an external store copies chunk text — including anything the redaction stage let
through — into a third-party service. That is the operator's decision to make, which is why the
capability matrix flags ``requires_external_service`` and why PII redaction (M11) happens *before*
ingestion rather than at retrieval.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Mapping, Optional, Sequence

from app.ai.core.enums import DistanceMetric
from app.ai.core.errors import ProviderNotConfiguredError
from app.ai.core.types import Chunk, ChunkMetadata, EmbeddedChunk, EmbeddingVector
from app.ai.interfaces.vector_store import VectorStoreCapabilities
from app.ai.vector_store.base import BaseVectorStore, coerce_source_type, coerce_strategy
from app.core.logging import get_logger

logger = get_logger(__name__)

# Fixed namespace for deriving point ids. A constant, never regenerated: change it and every
# existing point becomes unreachable while re-upserting silently creates a duplicate set.
POINT_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")

# Payload keys that are platform bookkeeping rather than chunk metadata.
TENANT_KEY = "tenant_id"
SPEC_KEY = "spec_key"
CHUNK_KEY = "chunk_id"
TEXT_KEY = "content"
EXTRA_KEY = "extra"


def point_id_for(chunk_id: uuid.UUID, spec_key: str) -> uuid.UUID:
    """Deterministic point id for one chunk under one embedding version."""
    return uuid.uuid5(POINT_NAMESPACE, f"{chunk_id}:{spec_key}")


def payload_for(chunk: Chunk, *, spec_key: str, tenant_id: str) -> dict[str, Any]:
    """Flatten a chunk into a filterable payload.

    Keys are the *DSL field names* from :mod:`app.ai.vector_store.filters`, not the column names, so
    every vendor's filter translator can map a ``MetadataFilter`` to a payload key without a lookup
    table of its own. ``None`` values are omitted rather than stored: a stored null is a value in
    most of these services, and ``exists`` has to be able to tell "absent" from "present and empty".
    """
    meta = chunk.metadata
    payload: dict[str, Any] = {
        TENANT_KEY: tenant_id,
        SPEC_KEY: spec_key,
        CHUNK_KEY: str(chunk.id),
        TEXT_KEY: chunk.text,
        "index": chunk.index,
        "token_count": chunk.token_count,
        "strategy": chunk.strategy.value,
        "heading_path": list(chunk.heading_path),
    }
    optional: dict[str, Any] = {
        "document_id": _str_or_none(meta.document_id),
        "parent_id": _str_or_none(chunk.parent_id),
        "policy_version": meta.policy_version,
        "department": meta.department,
        "country": meta.country,
        "currency": meta.currency,
        "category": meta.category,
        "language": meta.language,
        "source_type": meta.source_type.value if meta.source_type else None,
        "section": meta.section,
        "page_number": meta.page_number,
        "owner": meta.owner,
        "checksum": meta.checksum,
        "effective_date": _iso_or_none(meta.effective_date),
        "expiry_date": _iso_or_none(meta.expiry_date),
    }
    payload.update({k: v for k, v in optional.items() if v is not None})
    if meta.tags:
        payload["tags"] = list(meta.tags)
    if meta.extra:
        payload[EXTRA_KEY] = dict(meta.extra)
    return payload


def chunk_from_payload(payload: Mapping[str, Any]) -> Chunk:
    """Rebuild a :class:`Chunk` from a payload written by :func:`payload_for`.

    Lossless for everything retrieval and citation need. Tolerant of a payload written by an older
    build — a missing key reads as absent rather than raising — because the corpus in an external
    service outlives any one deployment of this code, and a rollback must not make it unreadable.
    """
    metadata = ChunkMetadata(
        document_id=_uuid_or_none(payload.get("document_id")),
        policy_version=payload.get("policy_version"),
        department=payload.get("department"),
        country=payload.get("country"),
        currency=payload.get("currency"),
        category=payload.get("category"),
        language=payload.get("language"),
        effective_date=_date_or_none(payload.get("effective_date")),
        expiry_date=_date_or_none(payload.get("expiry_date")),
        section=payload.get("section"),
        page_number=_int_or_none(payload.get("page_number")),
        owner=payload.get("owner"),
        checksum=payload.get("checksum"),
        tenant_id=str(payload.get(TENANT_KEY) or "default"),
        source_type=coerce_source_type(payload.get("source_type")),
        tags=tuple(payload.get("tags") or ()),
        extra=dict(payload.get(EXTRA_KEY) or {}),
    )
    return Chunk(
        id=_uuid_or_none(payload.get(CHUNK_KEY)) or uuid.uuid4(),
        text=str(payload.get(TEXT_KEY) or ""),
        index=_int_or_none(payload.get("index")) or 0,
        strategy=coerce_strategy(payload.get("strategy")),
        parent_id=_uuid_or_none(payload.get("parent_id")),
        token_count=_int_or_none(payload.get("token_count")) or 0,
        heading_path=tuple(payload.get("heading_path") or ()),
        metadata=metadata,
    )


def embedded_chunk_from_payload(
    payload: Mapping[str, Any], vector: Sequence[float], dimensions: int
) -> EmbeddedChunk:
    """A payload plus its vector, as the value type the contract's ``get`` returns."""
    return EmbeddedChunk(
        chunk=chunk_from_payload(payload),
        vector=EmbeddingVector(
            values=tuple(float(v) for v in vector),
            spec_key=str(payload.get(SPEC_KEY) or ""),
            dimensions=dimensions,
        ),
    )


class ExternalVectorStoreBase(BaseVectorStore):
    """Base for adapters backed by a separate service.

    Adds two things to :class:`BaseVectorStore`: a lazily built client, and the rule that a missing
    driver or credential is a :class:`ProviderNotConfiguredError` naming the remedy rather than an
    ``ImportError`` or a ``None`` dereference. Both matter because the platform ships with all eight
    adapters registered on hosts that have none of the drivers installed — that is the normal state,
    not a broken one.
    """

    def __init__(
        self,
        *,
        name: str,
        dimensions: int,
        capabilities: VectorStoreCapabilities,
        collection: str,
        metric: DistanceMetric = DistanceMetric.COSINE,
        client: Any = None,
        recorder: Any = None,
    ) -> None:
        super().__init__(
            name=name, dimensions=dimensions, capabilities=capabilities,
            metric=metric, recorder=recorder,
        )
        if not collection or not collection.strip():
            raise ProviderNotConfiguredError(
                provider=name, kind="VECTOR_STORE",
                remedy="Set AI_VECTOR_COLLECTION to the collection or index name to use.",
            )
        self.collection = collection.strip()
        # Injectable so the request and response shapes this adapter builds can be tested against a
        # double. Without the seam, the only way to exercise any of this is to have the service.
        self._client = client

    # --- client --------------------------------------------------------------

    def _build_client(self) -> Any:
        """Construct the vendor client. Subclasses implement; may raise."""
        raise NotImplementedError

    def client(self) -> Any:
        """The client, built on first use and cached.

        Construction failures become ``ProviderNotConfiguredError`` with the pip package or the
        setting named in them, because "None has no attribute upsert" three frames deep is not an
        error anyone can act on.
        """
        if self._client is None:
            try:
                self._client = self._build_client()
            except ProviderNotConfiguredError:
                raise
            except ImportError as exc:
                raise ProviderNotConfiguredError(
                    provider=self.name, kind="VECTOR_STORE",
                    remedy=f"The client library is not installed: {exc}",
                ) from exc
            except Exception as exc:
                raise ProviderNotConfiguredError(
                    provider=self.name, kind="VECTOR_STORE",
                    remedy=f"Building the client failed: {type(exc).__name__}: {exc}",
                ) from exc
        return self._client

    def is_available(self) -> bool:
        """Whether the service is reachable. Never raises; a failure is ``False`` and a log line."""
        try:
            self._ping(self.client())
            return True
        except Exception as exc:
            logger.warning(
                "ai.vector_store.unavailable",
                extra={"store": self.name, "error": f"{type(exc).__name__}: {exc}"},
            )
            return False

    def _ping(self, client: Any) -> None:
        """One cheap call proving the service answers. Subclasses implement."""
        raise NotImplementedError

    # --- shared payload helpers ---------------------------------------------

    def _payload(self, chunk: Chunk, *, spec_key: str, tenant_id: str) -> dict[str, Any]:
        return payload_for(chunk, spec_key=spec_key, tenant_id=tenant_id)

    def _point_id(self, chunk_id: uuid.UUID, spec_key: str) -> uuid.UUID:
        return point_id_for(chunk_id, spec_key)


# ---------------------------------------------------------------------------
# coercion helpers
# ---------------------------------------------------------------------------


def _str_or_none(value: Any) -> Optional[str]:
    return None if value is None else str(value)


def _iso_or_none(value: Optional[date]) -> Optional[str]:
    return None if value is None else value.isoformat()


def _uuid_or_none(value: Any) -> Optional[uuid.UUID]:
    if value is None or isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _date_or_none(value: Any) -> Optional[date]:
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "CHUNK_KEY",
    "EXTRA_KEY",
    "POINT_NAMESPACE",
    "SPEC_KEY",
    "TENANT_KEY",
    "TEXT_KEY",
    "ExternalVectorStoreBase",
    "chunk_from_payload",
    "embedded_chunk_from_payload",
    "payload_for",
    "point_id_for",
]
