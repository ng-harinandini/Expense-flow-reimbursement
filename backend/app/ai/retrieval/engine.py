"""``HybridRetrievalEngine`` — the one place lexical, dense, fusion, reranking, compression and the
token budget compose into a single call.

Every stage below is individually disableable and individually timed, per the task constraint:

* **Strategy** (``RetrievalQuery.strategy``) picks which legs run. ``LEXICAL``/``DENSE`` skip fusion
  entirely and use that leg's own already-normalized score directly — running a single leg through
  Reciprocal Rank Fusion would throw away its real score in favour of a rank-position approximation
  for no benefit, since RRF only earns its complexity when there are two legs to reconcile.
* **Reranking** (``RetrievalQuery.rerank``) is skipped when it is false or no ``RerankService`` was
  wired in (``AI_RERANK_ENABLED`` is the composition root's decision whether to wire one at all).
  ``RerankService`` itself never raises — an unavailable or failing provider degrades to the fused
  ordering, which is why this engine holds no ``try/except`` of its own around it.
* **Compression** (``RetrievalQuery.compress`` and ``AI_RETRIEVAL_COMPRESSION_ENABLED``) is opt-in
  and off by default; see :mod:`app.ai.retrieval.compression`.
* **The token budget** (:mod:`app.ai.retrieval.budget`) always runs — it is the platform's one hard
  promise about the shape of what comes back, not a stage a caller can turn off.

Every stage opens its own span under one ``RETRIEVE`` root, so ``RetrievalResult.timings`` is a
complete per-stage breakdown regardless of which stages actually ran.
"""

from __future__ import annotations

from typing import Any, Optional

from app.ai.core.enums import RetrievalStrategy, TelemetryOperation
from app.ai.core.errors import AIValidationError
from app.ai.core.ids import config_fingerprint
from app.ai.core.types import RetrievalQuery, RetrievalResult, RetrievedChunk
from app.ai.embeddings.service import EmbeddingService
from app.ai.interfaces.vector_store import VectorStore
from app.ai.reranking.service import RerankService
from app.ai.repositories.knowledge_repository import (
    KnowledgeChunkRepository,
    KnowledgeDocumentRepository,
)
from app.ai.retrieval import budget, compression, fusion, scoring
from app.ai.retrieval.dense import dense_leg
from app.ai.retrieval.filters import build_document_scope, combined_chunk_filters
from app.ai.retrieval.lexical import lexical_leg
from app.ai.telemetry.recorder import NullTelemetryRecorder


class HybridRetrievalEngine:
    """Precision-oriented retrieval over one tenant's knowledge corpus.

    Constructed per request with the caller's session-bound repositories and vector store — the same
    reasoning as the vector store adapters themselves (M6): a store or repository bound to a
    transaction is a per-call object, never a shared singleton, so the engine is too.
    """

    def __init__(
        self,
        *,
        chunk_repository: KnowledgeChunkRepository,
        document_repository: KnowledgeDocumentRepository,
        vector_store: VectorStore,
        embedding_service: EmbeddingService,
        reranker: Optional[RerankService] = None,
        recorder: Any = None,
    ) -> None:
        self._chunks = chunk_repository
        self._documents = document_repository
        self._vector_store = vector_store
        self._embedding_service = embedding_service
        self._reranker = reranker
        self._recorder = recorder or NullTelemetryRecorder()

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        root = self._recorder.span(
            TelemetryOperation.RETRIEVE,
            attributes={"strategy": query.strategy.value, "tenantId": query.tenant_id},
        )
        with root:
            scope = build_document_scope(
                self._documents,
                tenant_id=query.tenant_id,
                effective_on=query.effective_on,
                include_superseded=query.include_superseded,
            )
            filters = combined_chunk_filters(query.filters, scope)

            lexical_chunks: list[RetrievedChunk] = []
            dense_chunks: list[RetrievedChunk] = []
            spec_key = self._embedding_service.spec_key

            if query.strategy in (RetrievalStrategy.LEXICAL, RetrievalStrategy.HYBRID):
                lexical_chunks = lexical_leg(
                    chunks=self._chunks,
                    query_text=query.text,
                    tenant_id=query.tenant_id,
                    candidate_k=query.candidate_k,
                    filters=filters,
                    scope=scope,
                    recorder=self._recorder,
                )

            if query.strategy in (RetrievalStrategy.DENSE, RetrievalStrategy.HYBRID):
                if query.embedding_version and query.embedding_version != spec_key:
                    # A fresh query can only ever be embedded by this deployment's one active
                    # provider — there is no second live model to embed it with an older version's
                    # weights. Naming an indexed-but-inactive version here (unlike
                    # resolve_query_spec_key's "answer from the only indexed version" fallback,
                    # which applies to reading back an *already-stored* vector, not embedding new
                    # text) is a caller error, not something to silently downgrade past.
                    raise AIValidationError(
                        f"This engine embeds a fresh query with its active provider's version "
                        f"('{spec_key}') only; '{query.embedding_version}' was requested. Use "
                        "RetrievalStrategy.LEXICAL to query without embedding, or reconfigure the "
                        "active provider to the requested version.",
                        details={"requested": query.embedding_version, "active": spec_key},
                    )
                query_vector = self._embedding_service.embed_query(query.text)
                dense_chunks = dense_leg(
                    vector_store=self._vector_store,
                    query_vector=query_vector,
                    tenant_id=query.tenant_id,
                    candidate_k=query.candidate_k,
                    filters=filters,
                    scope=scope,
                    recorder=self._recorder,
                )

            combined = self._combine(query, lexical_chunks, dense_chunks)

            reranked = self._rerank(query, combined)

            # Cut to top_k here, before compression: `combined` (fused, or a single already-sorted
            # leg) and whatever `_rerank` returns are both already in final descending order, so
            # slicing now discards exactly the chunks scoring.finalize would discard later anyway —
            # just before compression does real work on them instead of after. Skipping this and
            # compressing the full candidate_k-sized set (up to ~50 chunks) only to keep the top few
            # is wasted work on every query that does not use every candidate.
            reranked = reranked[: query.top_k]

            if query.compress:
                with self._recorder.span(TelemetryOperation.COMPRESS) as span:
                    span.set_counts(candidates_in=len(reranked), candidates_out=len(reranked))
                    reranked = compression.compress_chunks(
                        reranked,
                        query_text=query.text,
                        context_budget_tokens=query.context_budget_tokens,
                        top_k=query.top_k,
                    )

            finalized = scoring.finalize(reranked, score_threshold=query.score_threshold)
            packed, truncated = budget.pack(finalized, budget_tokens=query.context_budget_tokens)
            root.set_attribute("truncated", truncated)

            fingerprint = config_fingerprint(self._fingerprint_inputs(query, spec_key))
            timings = root.timings()

        return RetrievalResult(
            chunks=packed,
            query=query,
            embedding_version=spec_key,
            config_fingerprint=fingerprint,
            timings=timings,
            total_candidates=len(lexical_chunks) + len(dense_chunks),
            cache_hit=False,
        )

    # --- stage composition -----------------------------------------------------

    def _combine(
        self,
        query: RetrievalQuery,
        lexical_chunks: list[RetrievedChunk],
        dense_chunks: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        """Fuse both legs for ``HYBRID``; otherwise return the single leg that ran, unchanged.

        See the module docstring: running one leg through RRF would replace its real score with a
        rank-position approximation for no benefit.
        """
        if query.strategy is RetrievalStrategy.LEXICAL:
            return lexical_chunks
        if query.strategy is RetrievalStrategy.DENSE:
            return dense_chunks

        with self._recorder.span(TelemetryOperation.FUSION) as span:
            span.set_counts(candidates_in=len(lexical_chunks) + len(dense_chunks))
            fused = fusion.fuse(
                {"lexical": lexical_chunks, "dense": dense_chunks},
                weights={"lexical": query.lexical_weight, "dense": query.dense_weight},
            )
            span.set_counts(candidates_out=len(fused))
        return fused

    def _rerank(
        self, query: RetrievalQuery, chunks: list[RetrievedChunk]
    ) -> list[RetrievedChunk]:
        if not query.rerank or self._reranker is None or not chunks:
            return chunks
        # top_n is a hint, not a guarantee about what comes back — see RerankService.rerank. It is
        # only ever a hint to *shrink an HTTP-based provider's response*, so it must not be smaller
        # than the largest candidate set scoring.finalize will need afterward, which is why it is
        # query.top_k (the final output size) rather than query.candidate_k (the input size).
        return self._reranker.rerank(query.text, chunks, top_n=query.top_k)

    # --- helpers -----------------------------------------------------------

    def _fingerprint_inputs(self, query: RetrievalQuery, spec_key: str) -> dict[str, Any]:
        """What must match for two retrievals to be considered reproductions of each other.

        Every field that changes which chunks come back or in what order belongs here — not only
        the ones about *how* they were scored. ``top_k``/``score_threshold``/``filters`` change the
        result set itself; ``effective_on``/``include_superseded`` change the document scope. Two
        queries differing only in ``top_k`` must fingerprint differently, or
        ``app.ai.core.ids.retrieval_cache_key`` (built from this fingerprint) would collide a
        3-chunk answer with a 10-chunk one once retrieval caching is wired up.
        """
        return {
            "strategy": query.strategy.value,
            "specKey": spec_key,
            "store": self._vector_store.name,
            "metric": self._vector_store.metric.value,
            "topK": query.top_k,
            "candidateK": query.candidate_k,
            "scoreThreshold": query.score_threshold,
            "filters": [repr(f) for f in query.filters],
            "effectiveOn": query.effective_on.isoformat() if query.effective_on else None,
            "includeSuperseded": query.include_superseded,
            "lexicalWeight": query.lexical_weight,
            "denseWeight": query.dense_weight,
            "rrfK": fusion.DEFAULT_RRF_K,
            "rerank": bool(query.rerank and self._reranker is not None),
            "rerankProvider": getattr(self._reranker, "name", None) if query.rerank else None,
            "compress": query.compress,
            "contextBudgetTokens": query.context_budget_tokens,
        }


__all__ = ["HybridRetrievalEngine"]
