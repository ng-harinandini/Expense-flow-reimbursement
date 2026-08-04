"""General search and context retrieval — the platform's read surface.

Every route here is a single call onto ``KnowledgeService``, the mandatory facade (M9): no
retrieval internal is touched from this module. Open to any authenticated caller, per the M13 RBAC
matrix ("search/retrieve = any authenticated") — retrieval is read-only and carries no cost the
platform needs to gate more tightly than login.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.ai.api.schemas import ContextRequestSchema, SearchRequestSchema
from app.ai.core.enums import RetrievalStrategy
from app.ai.core.errors import AIValidationError
from app.ai.core.types import ContextBundle, RetrievalQuery, RetrievalResult
from app.ai.knowledge.service import KnowledgeService
from app.core.deps import CurrentUser, get_current_user, get_knowledge_service
from app.domain.actor import Actor

router = APIRouter(prefix="/ai/knowledge", tags=["AI Knowledge Platform"])

_TENANT_ID = "default"


def _serialize_result(result: RetrievalResult) -> dict:
    return {
        "chunks": [
            {
                "chunkId": str(chunk.id),
                "text": chunk.text,
                "score": chunk.score,
                "rank": chunk.rank,
                "citation": chunk.citation.to_label() if chunk.citation else None,
            }
            for chunk in result.chunks
        ],
        "citations": [citation.to_label() for citation in result.citations],
        "embeddingVersion": result.embedding_version,
        "configFingerprint": result.config_fingerprint,
        "totalCandidates": result.total_candidates,
        "truncated": result.truncated,
    }


def _serialize_context(bundle: ContextBundle) -> dict:
    return {
        "text": bundle.text,
        "citations": [citation.to_label() for citation in bundle.citations],
        "tokenCount": bundle.token_count,
        "chunkCount": bundle.chunk_count,
        "truncated": bundle.truncated,
        "embeddingVersion": bundle.embedding_version,
        "configFingerprint": bundle.config_fingerprint,
    }


@router.post("/search")
def search(
    payload: SearchRequestSchema,
    current: CurrentUser = Depends(get_current_user),
    knowledge_service: KnowledgeService = Depends(get_knowledge_service),
) -> dict:
    actor = Actor.from_current_user(current)
    query = RetrievalQuery(
        text=payload.text, tenant_id=_TENANT_ID,
        strategy=RetrievalStrategy.coerce(payload.strategy),
        top_k=payload.topK, rerank=payload.rerank, effective_on=payload.effectiveOn,
    )
    result = knowledge_service.search(query, actor=actor)
    return _serialize_result(result)


@router.post("/context")
def context(
    payload: ContextRequestSchema,
    current: CurrentUser = Depends(get_current_user),
    knowledge_service: KnowledgeService = Depends(get_knowledge_service),
) -> dict:
    actor = Actor.from_current_user(current)
    if payload.kind == "policy":
        bundle = knowledge_service.retrieve_policy(
            payload.query, category=payload.category, country=payload.country,
            currency=payload.currency, effective_on=payload.effectiveOn, top_k=payload.topK,
            actor=actor,
        )
    elif payload.kind == "similar_claims":
        bundle = knowledge_service.retrieve_similar_claims(
            payload.query, top_k=payload.topK, actor=actor
        )
    elif payload.kind == "vendor":
        bundle = knowledge_service.get_vendor_context(
            payload.query, top_k=payload.topK, actor=actor
        )
    else:  # pragma: no cover - Literal in the schema already rejects anything else at validation
        raise AIValidationError(f"Unknown context kind '{payload.kind}'.")
    return _serialize_context(bundle)
