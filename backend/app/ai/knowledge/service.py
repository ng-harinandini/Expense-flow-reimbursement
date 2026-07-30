"""``KnowledgeService`` — the single, mandatory public entry point to all AI intelligence.

Nothing outside ``app/ai`` may reach retrieval, chunking, embeddings or the vector store directly
(``tests/test_ai_architecture.py`` enforces this by AST scan); everything goes through the eight
methods below. Every method that returns retrieved evidence returns a :class:`ContextBundle` or
:class:`RetrievalResult` carrying citations and a reproducibility stamp (embedding version,
retrieval config fingerprint) — never bare text.

Constructed per request with the caller's session-bound repositories, exactly like
:class:`~app.ai.retrieval.engine.HybridRetrievalEngine` itself (M7) and every vector-store adapter
(M6): a service bound to one transaction is a per-call object, never a shared singleton.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.orm import Session

from app.ai.core.enums import DecisionMemoryKind, KnowledgeSourceType
from app.ai.core.types import (
    Citation,
    ContextBundle,
    MetadataFilter,
    RetrievalQuery,
    RetrievalResult,
)
from app.ai.embeddings import EmbeddingService
from app.ai.interfaces.vector_store import VectorStore
from app.ai.knowledge.citations import attach_citations
from app.ai.knowledge.context_builder import build_context
from app.ai.knowledge.vendor_knowledge import vendor_context_query
from app.ai.memory import indexer
from app.ai.memory.decision_memory import similar_claims_query
from app.ai.registry.flags import FeatureFlags
from app.ai.registry.flags import feature_flags as default_feature_flags
from app.ai.repositories.knowledge_repository import (
    KnowledgeChunkRepository,
    KnowledgeDocumentRepository,
)
from app.ai.reranking.service import RerankService
from app.ai.retrieval.engine import HybridRetrievalEngine
from app.domain.actor import Actor


class KnowledgeService:
    """The platform's one retrieval/memory facade. See the module docstring."""

    def __init__(
        self,
        *,
        session: Session,
        embedding_service: EmbeddingService,
        vector_store: VectorStore,
        tenant_id: str = "default",
        reranker: RerankService | None = None,
        flags: FeatureFlags | None = None,
        recorder: Any = None,
    ) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._embedding_service = embedding_service
        self._vector_store = vector_store
        self._reranker = reranker
        self._flags = flags or default_feature_flags
        self._document_repo = KnowledgeDocumentRepository(session)
        self._chunk_repo = KnowledgeChunkRepository(session)
        self._engine = HybridRetrievalEngine(
            chunk_repository=self._chunk_repo,
            document_repository=self._document_repo,
            vector_store=vector_store,
            embedding_service=embedding_service,
            reranker=reranker,
            recorder=recorder,
        )

    def _with_citations(self, result: RetrievalResult, *, tenant_id: str) -> RetrievalResult:
        return attach_citations(result, document_repo=self._document_repo, tenant_id=tenant_id)

    # --- 1. general search ----------------------------------------------------

    def search(self, query: RetrievalQuery, *, actor: Actor | None = None) -> RetrievalResult:
        """The general-purpose retrieval surface. Also the shape
        :class:`app.ai.interfaces.planner.Retriever` expects, so this service can be handed to a
        future planner directly."""
        self._flags.require("ai.retrieval")
        result = self._engine.retrieve(query)
        return self._with_citations(result, tenant_id=query.tenant_id)

    # --- 2. policy retrieval ----------------------------------------------------

    def retrieve_policy(
        self,
        question: str,
        *,
        category: str | None = None,
        country: str | None = None,
        currency: str | None = None,
        effective_on=None,
        source_types: Sequence[KnowledgeSourceType] = (),
        top_k: int | None = None,
        actor: Actor | None = None,
    ) -> ContextBundle:
        """Policy Q&A, filtered by the metadata a policy question naturally carries."""
        self._flags.require("ai.retrieval")
        filters: list[MetadataFilter] = []
        if category:
            filters.append(MetadataFilter(field_name="category", value=category))
        if country:
            filters.append(MetadataFilter(field_name="country", value=country))
        if currency:
            filters.append(MetadataFilter(field_name="currency", value=currency))
        if source_types:
            filters.append(
                MetadataFilter(
                    field_name="source_type", op="in",
                    value=[source_type.value for source_type in source_types],
                )
            )
        query = RetrievalQuery(
            text=question, tenant_id=self._tenant_id, top_k=top_k or 8,
            effective_on=effective_on, filters=tuple(filters),
        )
        result = self._engine.retrieve(query)
        result = self._with_citations(result, tenant_id=self._tenant_id)
        return build_context(result)

    # --- 3. similar-claims retrieval (decision memory) --------------------------

    def retrieve_similar_claims(
        self, claim_summary: str, *, top_k: int = 5, actor: Actor | None = None
    ) -> ContextBundle:
        """Searches decision memory for similar past claims, reviews, approvals and rejections —
        the historical-case memory this platform's reasoning retrieves from."""
        self._flags.require("ai.retrieval")
        query = similar_claims_query(claim_summary, tenant_id=self._tenant_id, top_k=top_k)
        result = self._engine.retrieve(query)
        result = self._with_citations(result, tenant_id=self._tenant_id)
        return build_context(result)

    # --- 4. vendor intelligence --------------------------------------------------

    def get_vendor_context(
        self, vendor_name: str, *, top_k: int = 5, actor: Actor | None = None
    ) -> ContextBundle:
        """Indexed vendor knowledge (contracts, manuals) for one vendor."""
        self._flags.require("ai.retrieval")
        query = vendor_context_query(vendor_name, tenant_id=self._tenant_id, top_k=top_k)
        result = self._engine.retrieve(query)
        result = self._with_citations(result, tenant_id=self._tenant_id)
        return build_context(result)

    # --- 5. explanation -----------------------------------------------------------

    def explain(
        self, question: str, context: ContextBundle, *, actor: Actor | None = None
    ) -> Mapping[str, Any]:
        """A structured, cited explanation built from already-retrieved context.

        Deterministic and extractive — no LLM ships in T004 (Task 15's own scope), so this composes
        an answer from the retrieved text rather than generating one. A conclusion this method
        returns is always traceable to ``context.citations``; it never fabricates evidence it was
        not handed.
        """
        if context.is_empty():
            summary = "No supporting evidence was found for this question."
        else:
            summary = context.text[:1000]
        return {
            "question": question,
            "summary": summary,
            "citations": [citation.to_label() for citation in context.citations],
            "embeddingVersion": context.embedding_version,
            "configFingerprint": context.config_fingerprint,
            "truncated": context.truncated,
        }

    # --- 6. decision memory: write side -----------------------------------------

    def record_decision(
        self,
        kind: DecisionMemoryKind,
        subject_id: object,
        summary: str,
        *,
        actor: Actor | None = None,
    ) -> None:
        """Write one lifecycle event into decision memory.

        A no-op when ``ai.decision_memory`` is disabled — the caller (e.g. ``ClaimService``) never
        needs to check the flag itself; disabling AI must leave its own behaviour unchanged.
        """
        if not self._flags.is_enabled("ai.decision_memory"):
            return
        indexer.record_decision(
            kind, subject_id, summary,
            session=self._session, embedding_service=self._embedding_service,
            vector_store=self._vector_store, actor_sub=actor.sub if actor else None,
            tenant_id=self._tenant_id,
        )

    # --- 7. citation resolution --------------------------------------------------

    def get_document(
        self, document_id: uuid.UUID, *, actor: Actor | None = None
    ) -> Citation | None:
        """Resolve one document's citation metadata directly, for a caller that already has an id
        and needs to render a citation without re-retrieving."""
        document = self._document_repo.get_scoped(document_id, tenant_id=self._tenant_id)
        if document is None:
            return None
        return Citation(
            document_id=document.id,
            document_title=document.title,
            document_version=document.version,
            source_type=KnowledgeSourceType(document.source_type) if document.source_type else None,
            section=None,
            page_number=None,
            effective_date=document.effective_date,
            source_uri=document.source_uri,
        )

    # --- 8. reproducibility / observability --------------------------------------

    def describe(self) -> dict[str, Any]:
        """Active embedding version, vector store, reranker and feature-flag state — what
        ``/api/ai/knowledge/health`` and ``/metrics`` (M13) expose over HTTP."""
        return {
            "tenantId": self._tenant_id,
            "embeddingSpecKey": self._embedding_service.spec_key,
            "isSemantic": self._embedding_service.is_semantic,
            "vectorStore": self._vector_store.name,
            "reranker": getattr(self._reranker, "name", None),
            "featureFlags": self._flags.snapshot(),
        }


__all__ = ["KnowledgeService"]
