"""The capability matrix — what each adapter can actually do, declared in one place.

Two rules make this file worth having rather than putting the flags on each class:

**Capabilities describe the adapter, not the engine.** Qdrant supports sparse vectors and OpenSearch
is a text search engine, but neither of *these adapters* implements lexical retrieval, so both
declare ``native_lexical_search=False``. Declaring what the vendor is capable of would be a lie the
retrieval engine acts on — it reads these flags to decide where to run a query leg, and routing a
lexical leg to an adapter with no lexical method is an ``AttributeError`` in production.

**One definition, imported by both sides.** Each adapter returns the constant defined here, and the
matrix is built from the same constants, so the introspection endpoint and the running store cannot
disagree. A test asserts every registered store returns its matrix entry by identity.

Consequences of the current matrix, spelled out because they drive design elsewhere:

* No adapter declares native lexical or hybrid search. The ``VectorStore`` protocol has no lexical
  method, so none *can*. Hybrid retrieval (M7) therefore always runs its lexical leg against
  PostgreSQL via ``KnowledgeChunkRepository.lexical_search``, whatever the vector store is. That is
  not a limitation to work around: ``knowledge_chunks`` is the system of record for chunk text in
  every configuration, and the vector store is an *index* over it. An external store holding a stale
  copy of a chunk cannot corrupt an answer, because the text a reviewer is shown always comes from
  PostgreSQL.
* ``pgvector`` declares ``exact_search=False``. With an HNSW index the results are approximate, and
  pretending otherwise would make a recall regression invisible. ``postgres_native`` is exact over
  the same rows, which is exactly what makes it useful as the ground truth to measure pgvector's
  recall against.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.ai.interfaces.vector_store import VectorStoreCapabilities

# pgvector's own limits. Storage allows 16,000 dimensions; an HNSW or IVFFlat index over ``vector``
# is capped at 2,000. Declared so a future 4,096-dimension model is rejected with a clear message
# instead of failing at index-creation time in a migration.
PGVECTOR_MAX_INDEXED_DIMENSIONS = 2000
PGVECTOR_MAX_STORED_DIMENSIONS = 16000

MEMORY = VectorStoreCapabilities(
    exact_search=True,
    approximate_index=False,
    supports_filters=True,
    supports_pagination=True,
    requires_external_service=False,
)

POSTGRES_NATIVE = VectorStoreCapabilities(
    # Filters and candidate selection run in SQL; scoring runs in Python over the candidates, so the
    # ranking is exact rather than approximate.
    exact_search=True,
    approximate_index=False,
    supports_filters=True,
    supports_pagination=True,
    max_dimensions=PGVECTOR_MAX_STORED_DIMENSIONS,
    requires_external_service=False,
)

PGVECTOR = VectorStoreCapabilities(
    exact_search=False,
    approximate_index=True,
    supports_filters=True,
    supports_pagination=True,
    max_dimensions=PGVECTOR_MAX_INDEXED_DIMENSIONS,
    requires_external_service=False,
)

QDRANT = VectorStoreCapabilities(
    exact_search=False,
    approximate_index=True,
    supports_filters=True,
    supports_pagination=True,
    requires_external_service=True,
)

OPENSEARCH = VectorStoreCapabilities(
    exact_search=False,
    approximate_index=True,
    supports_filters=True,
    supports_pagination=True,
    # OpenSearch's own default ceiling for a knn_vector field.
    max_dimensions=16000,
    requires_external_service=True,
)

PINECONE = VectorStoreCapabilities(
    exact_search=False,
    approximate_index=True,
    supports_filters=True,
    # Pinecone's query API has no offset: paging is done by re-querying with a growing top_k and
    # discarding the prefix, which is neither cheap nor stable. Declared false rather than emulated.
    supports_pagination=False,
    max_dimensions=20000,
    requires_external_service=True,
)

MILVUS = VectorStoreCapabilities(
    exact_search=False,
    approximate_index=True,
    supports_filters=True,
    supports_pagination=True,
    max_dimensions=32768,
    requires_external_service=True,
)

WEAVIATE = VectorStoreCapabilities(
    exact_search=False,
    approximate_index=True,
    supports_filters=True,
    supports_pagination=True,
    requires_external_service=True,
)

# : Adapter name -> its declared capabilities. Keys match the ``AI_VECTOR_STORE`` vocabulary in :
# :class:`~app.ai.core.config.AISettings` exactly; a test asserts the two sets are equal, so adding
# : a store to one and forgetting the other cannot happen quietly.
CAPABILITIES: Mapping[str, VectorStoreCapabilities] = {
    "memory": MEMORY,
    "postgres_native": POSTGRES_NATIVE,
    "pgvector": PGVECTOR,
    "qdrant": QDRANT,
    "opensearch": OPENSEARCH,
    "pinecone": PINECONE,
    "milvus": MILVUS,
    "weaviate": WEAVIATE,
}


def capabilities_for(name: str) -> VectorStoreCapabilities:
    """Declared capabilities for ``name``, without constructing the adapter.

    The point of the lookup being name-based: the governance endpoint can report what every backend
    would do without dialling a single external service or loading a driver.
    """
    key = (name or "").strip().lower()
    known = CAPABILITIES.get(key)
    if known is None:
        raise KeyError(
            f"No capability declaration for vector store '{name}'. Known: "
            f"{', '.join(sorted(CAPABILITIES))}."
        )
    return known


def capability_matrix() -> list[dict[str, Any]]:
    """The whole matrix, for ``GET /api/ai/knowledge/metrics`` and the ADR."""
    return [
        {"store": name, **CAPABILITIES[name].to_dict()} for name in sorted(CAPABILITIES)
    ]


__all__ = [
    "CAPABILITIES",
    "MEMORY",
    "MILVUS",
    "OPENSEARCH",
    "PGVECTOR",
    "PGVECTOR_MAX_INDEXED_DIMENSIONS",
    "PGVECTOR_MAX_STORED_DIMENSIONS",
    "PINECONE",
    "POSTGRES_NATIVE",
    "QDRANT",
    "WEAVIATE",
    "capabilities_for",
    "capability_matrix",
]
