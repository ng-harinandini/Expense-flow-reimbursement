"""The lexical leg: PostgreSQL full-text search, with a trigram fallback for typos.

Uses ``KnowledgeChunkRepository.lexical_search`` (native FTS, ``ts_rank_cd``, no extension) as the
primary path. When it finds nothing — a common failure mode being a misspelled vendor name or OCR
noise that tokenization can't match — it retries once against ``trigram_search`` (``pg_trgm``
similarity), which matches substrings tokenization would have separated. Both queries run with the
same document scope and the same explicit filters, so the fallback cannot widen what was eligible in
the first attempt at getting an answer.

**Scores are normalized within this result set, not across queries.** ``ts_rank_cd`` has no fixed
scale — a score of 0.6 on a short query with common terms and 0.6 on a long query with rare terms do
not mean the same thing. Min-max scaling to ``[0, 1]`` (worst-in-this-set → 0, best-in-this-set → 1)
gives a bounded number that is meaningful as "how strong a match, relative to everything else this
query found" — which is exactly what Reciprocal Rank Fusion needs, since RRF combines on *rank*, not
on the raw score's absolute value anyway. A single result normalizes to 1.0 rather than dividing by
zero: on its own it is, definitionally, the strongest match this leg found.
"""

from __future__ import annotations

from typing import Sequence

from app.ai.core.enums import TelemetryOperation
from app.ai.core.types import MetadataFilter, RetrievedChunk
from app.ai.repositories.knowledge_repository import KnowledgeChunkRepository
from app.ai.retrieval.filters import DocumentScope
from app.ai.vector_store.postgres import chunk_to_value


def lexical_leg(
    *,
    chunks: KnowledgeChunkRepository,
    query_text: str,
    tenant_id: str,
    candidate_k: int,
    filters: Sequence[MetadataFilter],
    scope: DocumentScope,
    recorder,
) -> list[RetrievedChunk]:
    """Run the lexical leg and return candidates carrying only ``lexical_score``.

    ``recorder`` is whatever :func:`app.ai.telemetry.recorder.build_recorder` produced — never
    ``None`` by the time this is called (the engine defaults it), so ``.span()`` always returns a
    real, usable span.
    """
    with recorder.span(TelemetryOperation.LEXICAL_SEARCH, attributes={"fallback": False}) as span:
        rows = chunks.lexical_search(
            query_text,
            tenant_id=tenant_id,
            limit=candidate_k,
            effective_on=scope.effective_on,
            include_superseded=scope.include_superseded,
            filters=filters,
        )
        used_fallback = False
        candidates_in = len(rows)
        if not rows and query_text.strip():
            # A second attempt, not a silent widening: same filters, same scope, just a fuzzier
            # match rule. Effective-dating has no equivalent parameter on trigram_search (see the
            # module docstring on DocumentScope), so it is re-applied in Python below.
            used_fallback = True
            rows = chunks.trigram_search(
                query_text, tenant_id=tenant_id, limit=candidate_k, filters=filters
            )
            candidates_in = len(rows)
            rows = [
                (row, score) for row, score in rows if scope.admits(chunk_to_value(row))
            ]
        span.set_attribute("fallback", used_fallback)
        # `candidates_in` is the trigram fallback's own result count, before the effective-dating
        # post-filter below drops any of them — mirroring dense_leg's identical before/after split.
        # Folding both into one number here would hide exactly the funnel step Task 14 asks for.
        span.set_counts(candidates_in=candidates_in, candidates_out=len(rows))

    if not rows:
        return []

    scores = [score for _, score in rows]
    normalized = _min_max(scores)
    return [
        RetrievedChunk(chunk=chunk_to_value(row), score=score, lexical_score=score)
        for (row, _), score in zip(rows, normalized, strict=True)
    ]


def _min_max(scores: Sequence[float]) -> list[float]:
    """Scale ``scores`` to ``[0, 1]`` within this list. See the module docstring."""
    if len(scores) <= 1:
        return [1.0] * len(scores)
    low, high = min(scores), max(scores)
    if high - low < 1e-12:
        # Every candidate scored identically (a common case for a short, common query term) — there
        # is no relative ordering information to preserve, so all are equally "the best we found."
        return [1.0] * len(scores)
    return [(s - low) / (high - low) for s in scores]


__all__ = ["lexical_leg"]
