"""The final scoring policy: which stage's number becomes ``.score``, and in what order.

Every earlier stage leaves its own evidence on the chunk — ``lexical_score``, ``dense_score``,
``fused_score``, ``rerank_score`` — and none of them overwrites another (see
:class:`~app.ai.core.types.RetrievedChunk`'s own docstring on why the stage scores stay separate:
"a reviewer or an engineer can see that a result placed third because lexical matched strongly but
the cross-encoder disagreed"). This module is where a *single* precedence is applied to decide what
``.score`` means for ranking and for ``score_threshold`` — reranking beats fusion beats a single
leg's own score, because each stage in that order had more information than the one before it.

``score_threshold`` is applied here, once, to the final score — not inside the dense leg's own
search (which would prune before fusion had a chance to promote a candidate a strong lexical match
also found) and not inside fusion (which has no opinion about what counts as "relevant enough").
"""

from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from app.ai.core.types import RetrievedChunk


def finalize(
    chunks: Sequence[RetrievedChunk], *, score_threshold: float = 0.0
) -> tuple[RetrievedChunk, ...]:
    """Pick each chunk's final score, order by it, prune below the threshold, and rank.

    Ordering happens *before* pruning rather than after, even though the result is the same set of
    survivors either way — the point is that ``rank`` reflects each chunk's position in the full
    ranked list's numbering convention (1-based, contiguous from the top), not a renumbering that
    starts over after the threshold removed some middle-ranked chunks.
    """
    scored = [replace(chunk, score=_final_score(chunk)) for chunk in chunks]
    scored.sort(key=lambda c: (-c.score, str(c.id)))

    ranked = [replace(chunk, rank=index) for index, chunk in enumerate(scored, start=1)]
    return tuple(chunk for chunk in ranked if chunk.score >= score_threshold)


def _final_score(chunk: RetrievedChunk) -> float:
    """Rerank beats fusion beats a single leg's own score — each had strictly more context."""
    if chunk.rerank_score is not None:
        return chunk.rerank_score
    if chunk.fused_score is not None:
        return chunk.fused_score
    if chunk.dense_score is not None:
        return chunk.dense_score
    if chunk.lexical_score is not None:
        return chunk.lexical_score
    return chunk.score


__all__ = ["finalize"]
