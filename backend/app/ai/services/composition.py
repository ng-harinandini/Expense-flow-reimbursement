"""Composition root for :class:`~app.ai.knowledge.service.KnowledgeService`.

The one place that resolves an embedding provider, a vector store and (optionally) a reranker into
a fully-wired service for one request's session — mirroring how
:class:`~app.ai.retrieval.engine.HybridRetrievalEngine` (M7) and every vector-store adapter (M6) are
themselves built per call, never as a shared singleton, because they are bound to one session.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.ai.embeddings import EmbeddingService
from app.ai.knowledge.service import KnowledgeService
from app.ai.providers.embeddings import resolve_embedding_provider
from app.ai.providers.rerank import resolve_rerank_provider
from app.ai.registry.flags import feature_flags
from app.ai.reranking.service import RerankService
from app.ai.vector_store.factory import resolve_vector_store


def build_knowledge_service(session: Session, *, tenant_id: str = "default") -> KnowledgeService:
    """The fully-wired :class:`KnowledgeService` for one request's session."""
    embedding_service = EmbeddingService(resolve_embedding_provider())
    vector_store = resolve_vector_store(session=session)

    reranker: RerankService | None = None
    if feature_flags.is_enabled("ai.rerank"):
        reranker = RerankService(provider=resolve_rerank_provider())

    return KnowledgeService(
        session=session,
        embedding_service=embedding_service,
        vector_store=vector_store,
        tenant_id=tenant_id,
        reranker=reranker,
    )


__all__ = ["build_knowledge_service"]
