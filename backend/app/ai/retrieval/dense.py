"""The dense leg: embed the query, search the configured vector store, apply document scope.

Everything about *which* store, *how* it scores, and *how* it applies chunk-level filters is M6's
concern and stays there — this module calls ``VectorStore.search`` exactly as any other caller
would. What it adds is the one thing no store can do for itself: excluding a superseded document
(passed down as a chunk filter every store understands, computed once by
:mod:`app.ai.retrieval.filters`) and effective-dating (which no store-level filter can express
correctly — see that module's docstring — so it is re-checked in Python against each result before
it reaches fusion).
"""

from __future__ import annotations

from typing import Sequence

from app.ai.core.enums import TelemetryOperation
from app.ai.core.types import EmbeddingVector, MetadataFilter, RetrievedChunk
from app.ai.interfaces.vector_store import VectorStore
from app.ai.retrieval.filters import DocumentScope
from app.core.logging import get_logger

logger = get_logger(__name__)


def dense_leg(
    *,
    vector_store: VectorStore,
    query_vector: EmbeddingVector,
    tenant_id: str,
    candidate_k: int,
    filters: Sequence[MetadataFilter],
    scope: DocumentScope,
    recorder,
) -> list[RetrievedChunk]:
    """Run the dense leg and return candidates carrying only ``dense_score``.

    ``score_threshold`` is deliberately not passed to the store: pruning here, before fusion has had
    a chance to combine this leg with the lexical one, would throw away a candidate that a strong
    lexical match could otherwise have promoted. The query's own ``score_threshold`` is applied
    once, by :mod:`app.ai.retrieval.scoring`, to the final blended score.
    """
    with recorder.span(TelemetryOperation.VECTOR_SEARCH, attributes={"store": vector_store.name}) \
            as span:
        matches = vector_store.search(
            query_vector,
            top_k=candidate_k,
            tenant_id=tenant_id,
            filters=filters,
            score_threshold=0.0,
        )
        hydrated = []
        for match in matches:
            if match.chunk is None:
                # The protocol allows it (some future adapter might return ids only), but this
                # engine cannot build a RetrievedChunk without the text, so it is unusable here —
                # logged rather than raised, since it is exactly one adapter's bug and must not take
                # down a query that would otherwise have other, well-formed candidates.
                logger.warning(
                    "ai.retrieval.dense_match_unhydrated",
                    extra={"store": vector_store.name, "chunkId": str(match.chunk_id)},
                )
                continue
            hydrated.append(match)
        kept = [m for m in hydrated if scope.admits(m.chunk)]
        span.set_counts(candidates_in=len(matches), candidates_out=len(kept))

    return [
        RetrievedChunk(chunk=match.chunk, score=match.score, dense_score=match.score)
        for match in kept
    ]


__all__ = ["dense_leg"]
