"""The write side of decision memory: turns one lifecycle event into an indexed knowledge document.

Reuses :func:`app.ai.ingestion.pipeline.ingest_document` rather than writing rows directly, so a
decision-memory entry gets exactly the same normalization, chunking, embedding and indexing every
other document does — and the same audit trail in ``knowledge_ingestion_runs``.
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.ai.core.enums import ChunkStrategy, DecisionMemoryKind
from app.ai.embeddings import EmbeddingService
from app.ai.ingestion.pipeline import (
    DocumentMetadataInput,
    IngestionResult,
    ingest_document,
)
from app.ai.ingestion.sources.inline import InlineSource
from app.ai.interfaces.vector_store import VectorStore
from app.ai.memory.decision_memory import source_type_for

# Short, largely unstructured records: no benefit to a paragraph/sentence cascade.
_MEMORY_CHUNK_OVERRIDES = {"max_tokens": 256, "overlap_tokens": 32, "min_tokens": 1}


def record_decision(
    kind: DecisionMemoryKind,
    subject_id: object,
    summary: str,
    *,
    session: Session,
    embedding_service: Optional[EmbeddingService] = None,
    vector_store: Optional[VectorStore] = None,
    actor_sub: str | None = None,
    tenant_id: str = "default",
) -> IngestionResult:
    """Ingest ``summary`` as a new decision-memory entry of kind ``kind``.

    ``embedding_service``/``vector_store`` should always be the *same instances* the calling
    ``KnowledgeService`` was constructed with, not left to default — passing neither would let
    ``ingest_document`` silently re-resolve fresh ones instead of the caller's configured pair.
    For a persistent store (pgvector) this is invisible, since every instance reads/writes the same
    underlying table; for a non-persistent one (``InMemoryVectorStore``, the CI/test default) two
    independently-resolved instances never see each other's data at all, which would make an entry
    just written here silently unfindable by that same ``KnowledgeService``'s own retrieval methods.

    Each call is a genuinely new entry, never a new *version* of a previous one — unlike a policy
    document, re-recording the same claim's next decision is not "the same title, changed bytes".
    The title therefore always includes a fresh id, so
    :meth:`~app.ai.repositories.knowledge_repository.KnowledgeDocumentRepository.latest_for_title`
    never matches a prior entry and triggers M8's supersede-by-title logic.

    Always called as a side effect nested inside a *caller's* own transaction (a claim lifecycle
    event, most concretely) — never as its own top-level unit of work — so this runs
    ``ingest_document`` with ``manage_transaction=False`` inside its own ``SAVEPOINT``
    (:meth:`Session.begin_nested`). A failure here rolls back only this entry's own partial work
    (undoing the savepoint) and re-raises for the caller to decide what to do; it can never
    prematurely commit or roll back whatever the caller staged before calling this, which a plain
    (non-nested) failure here would otherwise do.
    """
    title = f"{kind.value.lower()}-{subject_id}-{uuid.uuid4().hex[:12]}"
    source = InlineSource(
        text=summary, file_name=f"{title}.txt", source_type=source_type_for(kind),
        tenant_id=tenant_id,
    )
    with session.begin_nested():
        return ingest_document(
            source,
            session=session,
            metadata=DocumentMetadataInput(title=title, owner=actor_sub),
            chunk_strategy=ChunkStrategy.RECURSIVE,
            chunk_overrides=_MEMORY_CHUNK_OVERRIDES,
            embedding_service=embedding_service,
            vector_store=vector_store,
            actor_sub=actor_sub,
            manage_transaction=False,
        )


__all__ = ["record_decision"]
