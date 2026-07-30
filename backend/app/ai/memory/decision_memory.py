"""Decision memory as a knowledge source, not a parallel schema.

Every decision-memory entry (a claim, a review, an approval, a rejection, a comment, a fraud
finding, an AI explanation) is stored and retrieved through exactly the same
``knowledge_documents``/``knowledge_chunks``/``knowledge_embeddings`` tables and the same
:class:`~app.ai.retrieval.engine.HybridRetrievalEngine` every other knowledge source uses — it is
simply ingested under one of the already-existing historical/reviewer/fraud
:class:`~app.ai.core.enums.KnowledgeSourceType` members. This avoids a second embedding/indexing
pipeline that would need to be kept in sync with the real one, and means
``retrieve_similar_claims`` is a filtered version of the same retrieval every other method here
already does.
"""

from __future__ import annotations

from app.ai.core.enums import DecisionMemoryKind, KnowledgeSourceType
from app.ai.core.types import MetadataFilter, RetrievalQuery

_KIND_TO_SOURCE_TYPE: dict[DecisionMemoryKind, KnowledgeSourceType] = {
    DecisionMemoryKind.CLAIM: KnowledgeSourceType.HISTORICAL_CLAIM,
    DecisionMemoryKind.REVIEW: KnowledgeSourceType.HISTORICAL_DECISION,
    DecisionMemoryKind.APPROVAL: KnowledgeSourceType.HISTORICAL_DECISION,
    DecisionMemoryKind.REJECTION: KnowledgeSourceType.HISTORICAL_DECISION,
    DecisionMemoryKind.COMMENT: KnowledgeSourceType.REVIEWER_NOTE,
    DecisionMemoryKind.FRAUD_FINDING: KnowledgeSourceType.FRAUD_INVESTIGATION,
    DecisionMemoryKind.AI_EXPLANATION: KnowledgeSourceType.HISTORICAL_DECISION,
}

# Every source type any decision-memory kind maps to, for scoping a similarity search to "memory"
# entries specifically rather than every indexed document.
DECISION_MEMORY_SOURCE_TYPES = tuple(sorted({st.value for st in _KIND_TO_SOURCE_TYPE.values()}))


def source_type_for(kind: DecisionMemoryKind) -> KnowledgeSourceType:
    """Which knowledge-source type one decision-memory kind is ingested as."""
    return _KIND_TO_SOURCE_TYPE[kind]


def similar_claims_query(
    claim_summary: str, *, tenant_id: str = "default", top_k: int = 5
) -> RetrievalQuery:
    """A retrieval query scoped to every indexed decision-memory entry."""
    return RetrievalQuery(
        text=claim_summary,
        tenant_id=tenant_id,
        top_k=top_k,
        filters=(
            MetadataFilter(
                field_name="source_type", op="in", value=list(DECISION_MEMORY_SOURCE_TYPES),
            ),
        ),
    )


__all__ = [
    "DECISION_MEMORY_SOURCE_TYPES",
    "similar_claims_query",
    "source_type_for",
]
