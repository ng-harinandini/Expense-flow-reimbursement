"""Composition roots for :class:`~app.ai.knowledge.service.KnowledgeService` and
:class:`~app.ai.duplicate_detection.service.DuplicateDetectionService`.

The one place that resolves an embedding provider, a vector store and (optionally) a reranker into
a fully-wired service for one request's session — mirroring how
:class:`~app.ai.retrieval.engine.HybridRetrievalEngine` (M7) and every vector-store adapter (M6) are
themselves built per call, never as a shared singleton, because they are bound to one session.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.ai.duplicate_detection.service import DuplicateDetectionService
from app.ai.embeddings import EmbeddingService
from app.ai.interfaces.vector_store import VectorStore
from app.ai.knowledge.service import KnowledgeService
from app.ai.providers.embeddings import resolve_embedding_provider
from app.ai.providers.rerank import resolve_rerank_provider
from app.ai.registry.flags import feature_flags
from app.ai.reranking.service import RerankService
from app.ai.vector_store.factory import resolve_vector_store

if TYPE_CHECKING:
    from app.ai.policy_extraction.service import PolicyRuleExtractionService


def build_ingestion_providers(session: Session) -> tuple[EmbeddingService, VectorStore]:
    """Resolve the embedding provider and vector store shared by every ingestion call site.

    :func:`build_knowledge_service` and the M13 upload/reindex routes (``app/ai/api/knowledge.py``)
    both need this exact pair to call :func:`~app.ai.ingestion.pipeline.ingest_document`; pulled out
    here so neither has to duplicate the two-line resolution.
    """
    embedding_service = EmbeddingService(resolve_embedding_provider())
    vector_store = resolve_vector_store(session=session)
    return embedding_service, vector_store


def build_knowledge_service(session: Session, *, tenant_id: str = "default") -> KnowledgeService:
    """The fully-wired :class:`KnowledgeService` for one request's session."""
    embedding_service, vector_store = build_ingestion_providers(session)

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


def build_duplicate_detection_service(
    session: Session, *, tenant_id: str = "default"
) -> DuplicateDetectionService:
    """The fully-wired :class:`DuplicateDetectionService` for one request's session.

    The embedding provider is shared with :func:`build_knowledge_service`'s resolution logic but
    deliberately re-resolved rather than passed in: the two services are built independently per
    request (see ``app/core/deps.py``), and duplicate detection's ``EMBEDDING_SIMILARITY`` signal is
    the only thing here that needs it — every other signal is pure Python.
    """
    embedding_service = EmbeddingService(resolve_embedding_provider())
    return DuplicateDetectionService(
        session=session, tenant_id=tenant_id, embedding_service=embedding_service,
    )


def build_policy_rule_extraction_service(session: Session) -> "PolicyRuleExtractionService":
    """The fully-wired :class:`PolicyRuleExtractionService` for one request's session.

    ``resolve_llm_provider()`` raises (503) rather than degrading when no LLM is configured — unlike
    the embedding and rerank resolvers, there is no fallback provider here, because a stub that
    invented policy limits would be worse than an outright unavailable error. See
    ``app/ai/providers/llm/__init__.py`` for why.

    The extraction pipeline's stages are assembled here rather than inside the service so each stays
    injectable: a test supplies a fake ``PolicyExtractor`` and needs no provider, and swapping the
    sequential executor for a parallel one later is a change to this function alone.
    """
    from app.ai.policy_extraction.extractor import LLMPolicyExtractor, LLMSubsectionAdvisor
    from app.ai.policy_extraction.orchestrator import PolicyExtractionOrchestrator
    from app.ai.policy_extraction.service import PolicyRuleExtractionService
    from app.ai.providers.llm import resolve_llm_provider
    from app.ai.repositories.knowledge_repository import KnowledgeChunkRepository
    from app.ai.repositories.policy_proposal_repository import (
        PolicyDocumentPageRepository,
        PolicyDocumentSectionRepository,
        PolicyRuleProposalRepository,
    )

    llm_provider = resolve_llm_provider()
    return PolicyRuleExtractionService(
        orchestrator=PolicyExtractionOrchestrator(
            chunk_repository=KnowledgeChunkRepository(session),
            proposal_repository=PolicyRuleProposalRepository(session),
            page_repository=PolicyDocumentPageRepository(session),
            section_repository=PolicyDocumentSectionRepository(session),
            extractor=LLMPolicyExtractor(llm_provider=llm_provider),
            # Only consulted for a section too large to send whole; a document whose sections all
            # fit never reaches it and pays nothing for it.
            subsection_advisor=LLMSubsectionAdvisor(llm_provider=llm_provider),
        )
    )


__all__ = [
    "build_duplicate_detection_service",
    "build_ingestion_providers",
    "build_knowledge_service",
    "build_policy_rule_extraction_service",
]
