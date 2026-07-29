"""Registration and resolution for the eight vector stores.

``AI_VECTOR_STORE`` names one; nothing above this module knows which. That is the whole claim of
Task 5, and this file is where it either holds or does not.

**Every factory is lazy and every constructor cheap.** All eight are registered on a host with no
drivers installed and no credentials set, because that is the normal state — the adapters exist to
be
*selectable*. A registration that dialled a service or imported an SDK would make the platform
un-bootable anywhere but a fully provisioned production host.

**PostgreSQL-backed stores are resolved bound to a session.** They are per-request objects sharing
caller's transaction (see :mod:`app.ai.vector_store.postgres`), so :func:`resolve_vector_store`
takes the session and returns a bound instance. The registry still holds an unbound one, which is
what answers ``is_available`` and ``describe`` for the health and governance endpoints without
needing a request to be in flight.

**Fallback is opt-in and narrow.** The memory store can always serve, and falling back to it quietly
would turn "the vector database is down" into "retrieval returns nothing, with no error" — a corpus
that looks empty. So there is no automatic fallback to ``memory``: an unavailable store raises. The
one degradation offered is ``pgvector`` to ``postgres_native``, because those two read the *same
rows* and differ only in whether the index is used — a genuine equivalent, not a stand-in.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from app.ai.core.errors import ProviderNotConfiguredError
from app.ai.interfaces.vector_store import VectorStore
from app.ai.vector_store.capabilities import CAPABILITIES
from app.ai.vector_store.memory import InMemoryVectorStore
from app.ai.vector_store.pgvector import PgVectorStore
from app.ai.vector_store.postgres import PostgresVectorStoreBase, default_session_provider
from app.ai.vector_store.postgres_native import PostgresNativeVectorStore
from app.core.logging import get_logger

logger = get_logger(__name__)

# The one substitution that is a true equivalent rather than a downgrade: same rows, same tenant,
# same vectors, exact instead of approximate. Slower, and never wrong.
PGVECTOR_FALLBACK = ("postgres_native",)


def register_vector_stores() -> None:
    """Register all eight adapters. Called once by the composition root."""
    from app.ai.core.config import ai_settings as s
    from app.ai.registry.registry import vector_store_registry as reg

    dimensions = s.EMBEDDING_DIMENSIONS
    metric = s.VECTOR_DISTANCE_METRIC
    collection = s.VECTOR_COLLECTION
    session_provider = default_session_provider

    reg.register(
        "pgvector",
        lambda: PgVectorStore(
            dimensions=dimensions,
            metric=metric,
            session_provider=session_provider(),
            ef_search=s.VECTOR_HNSW_EF_SEARCH,
        ),
        protocol=VectorStore,
        description="pgvector with an HNSW index, in the application's own database. The default.",
        metadata=_metadata("pgvector", local=True),
        replace=True,
    )
    reg.register(
        "postgres_native",
        lambda: PostgresNativeVectorStore(
            dimensions=dimensions, metric=metric, session_provider=session_provider(),
        ),
        protocol=VectorStore,
        description="Exact search over the same rows, scored in Python. No index, no operator.",
        metadata=_metadata("postgres_native", local=True),
        replace=True,
    )
    reg.register(
        "memory",
        lambda: InMemoryVectorStore(dimensions=dimensions, metric=metric),
        protocol=VectorStore,
        description="In-process dict. Exact, unpersisted. For CI and local demos only.",
        metadata=_metadata("memory", local=True, persistent=False),
        replace=True,
    )
    reg.register(
        "qdrant",
        lambda: _qdrant(s, dimensions, metric, collection),
        protocol=VectorStore,
        description="Qdrant collection. Payload filters, HNSW.",
        metadata=_metadata("qdrant", local=False),
        replace=True,
    )
    reg.register(
        "opensearch",
        lambda: _opensearch(s, dimensions, metric, collection),
        protocol=VectorStore,
        description="OpenSearch knn_vector index with a pre-filtered kNN query.",
        metadata=_metadata("opensearch", local=False),
        replace=True,
    )
    reg.register(
        "pinecone",
        lambda: _pinecone(s, dimensions, metric),
        protocol=VectorStore,
        description="Pinecone index, one namespace per tenant. No paging.",
        metadata=_metadata("pinecone", local=False),
        replace=True,
    )
    reg.register(
        "milvus",
        lambda: _milvus(s, dimensions, metric, collection),
        protocol=VectorStore,
        description="Milvus collection with a JSON metadata field and an expression filter.",
        metadata=_metadata("milvus", local=False),
        replace=True,
    )
    reg.register(
        "weaviate",
        lambda: _weaviate(s, dimensions, metric, collection),
        protocol=VectorStore,
        description="Weaviate class with vectorizer=none; vectors always come from the platform.",
        metadata=_metadata("weaviate", local=False),
        replace=True,
    )


# The external adapters are imported inside their factories, not at module scope. Their modules
# import nothing heavy, but keeping the pattern uniform means adding an adapter that *does* need a
# driver at
# import time cannot break startup for everyone.


def _qdrant(settings: Any, dimensions: int, metric: Any, collection: str):
    from app.ai.vector_store.qdrant import QdrantVectorStore

    return QdrantVectorStore(
        dimensions=dimensions, collection=collection, metric=metric,
        url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY,
    )


def _opensearch(settings: Any, dimensions: int, metric: Any, collection: str):
    from app.ai.vector_store.opensearch import OpenSearchVectorStore

    return OpenSearchVectorStore(
        dimensions=dimensions, collection=collection, metric=metric, url=settings.OPENSEARCH_URL,
    )


def _pinecone(settings: Any, dimensions: int, metric: Any):
    from app.ai.vector_store.pinecone import PineconeVectorStore

    # Pinecone names the index rather than a collection, and it is a separate setting because an
    # account holds many indexes with different dimensionalities.
    return PineconeVectorStore(
        dimensions=dimensions,
        collection=settings.PINECONE_INDEX or settings.VECTOR_COLLECTION,
        metric=metric,
        api_key=settings.PINECONE_API_KEY,
    )


def _milvus(settings: Any, dimensions: int, metric: Any, collection: str):
    from app.ai.vector_store.milvus import MilvusVectorStore

    return MilvusVectorStore(
        dimensions=dimensions, collection=collection, metric=metric, uri=settings.MILVUS_URI,
    )


def _weaviate(settings: Any, dimensions: int, metric: Any, collection: str):
    from app.ai.vector_store.weaviate import WeaviateVectorStore

    return WeaviateVectorStore(
        dimensions=dimensions, collection=collection, metric=metric, url=settings.WEAVIATE_URL,
    )


def _metadata(name: str, *, local: bool, persistent: bool = True) -> dict[str, Any]:
    """Registration metadata, built from the declared capability matrix.

    Read from ``CAPABILITIES`` rather than restated so the ``/metrics`` payload and the adapter's
    own ``capabilities`` property cannot disagree.
    """
    return {"local": local, "persistent": persistent, **CAPABILITIES[name].to_dict()}


def resolve_vector_store(
    *, session: Optional[Session] = None, name: Optional[str] = None
) -> VectorStore:
    """The configured store, bound to ``session`` when it is a PostgreSQL-backed one.

    ``name`` overrides configuration and exists for two callers: the recall comparison, which needs
    ``postgres_native`` alongside whatever is configured, and the tests.

    Raises ``ProviderNotConfiguredError`` when the configured store is unusable and no equivalent is
    available. Deliberately not "fall back to memory and carry on" — see the module docstring.
    """
    from app.ai.core.config import ai_settings as s
    from app.ai.registry.registry import vector_store_registry as reg

    if not reg.names():
        register_vector_stores()

    requested = (name or s.VECTOR_STORE).strip().lower()
    preferences = [requested]
    if requested == "pgvector":
        preferences.extend(PGVECTOR_FALLBACK)

    store = (
        reg.resolve_with_fallback(preferences)
        if len(preferences) > 1
        else reg.resolve(requested)
    )

    if isinstance(store, PostgresVectorStoreBase):
        if session is None:
            raise ProviderNotConfiguredError(
                provider=requested, kind="VECTOR_STORE",
                remedy=(
                    "This store reads and writes through the caller's transaction. Pass "
                    "resolve_vector_store(session=...) with the request's session."
                ),
            )
        return store.bind(session)
    return store


def describe_vector_stores() -> list[dict[str, Any]]:
    """Every registered store with its declared capabilities, without constructing any of them."""
    from app.ai.registry.registry import vector_store_registry as reg

    if not reg.names():
        register_vector_stores()
    return reg.describe()


__all__ = [
    "PGVECTOR_FALLBACK",
    "describe_vector_stores",
    "register_vector_stores",
    "resolve_vector_store",
]
