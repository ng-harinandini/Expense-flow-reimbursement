"""Reciprocal Rank Fusion — combining two ranked lists by *position*, not by raw score.

RRF exists because the lexical and dense legs' scores are not comparable: ``ts_rank_cd`` is an
unbounded cover-density statistic and cosine similarity is bounded in ``[0, 1]``, and there is no
principled way to average one against the other. RRF sidesteps the problem entirely by combining on
each leg's own *rank position* — a chunk ranked 1st by one leg and 5th by another combines the same
way regardless of what either leg's raw score happened to be. This is also why
:mod:`app.ai.retrieval.lexical` bothers normalizing ``ts_rank_cd`` at all: not for fusion (which
never looks at it), but so ``lexical_score`` is a meaningful, bounded number when it is the *only*
score a caller ever sees — pure-lexical retrieval, or a stage score shown for explainability.

Formula, per candidate: ``sum(weight[leg] / (k + rank_in_leg))`` over the legs it appears in — zero
contribution from a leg it did not appear in at all, not a penalty. ``k`` dampens the advantage of
rank 1 over rank 2 (a large ``k`` flattens the curve; ``k=60`` is the value the original RRF paper
uses and this platform's own default).

**The raw formula is unbounded above by nothing intuitive** — even its best possible value depends
on how many legs ran and their weights, which is not a number a caller reading ``score_threshold``
should have to compute. So the result is rescaled by the highest score any candidate could
theoretically achieve (rank 1 in *every* leg that ran, weighted), which puts a perfect double-first
finish at exactly ``1.0`` and keeps the ordering — RRF's only real claim — completely unchanged,
since rescaling by a positive constant cannot reorder anything.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Mapping, Sequence

from app.ai.core.types import RetrievedChunk

DEFAULT_RRF_K = 60


def fuse(
    legs: Mapping[str, Sequence[RetrievedChunk]],
    *,
    weights: Mapping[str, float],
    k: int = DEFAULT_RRF_K,
) -> list[RetrievedChunk]:
    """Combine named legs (e.g. ``{"lexical": [...], "dense": [...]}``) into one ranked list.

    Each input list must already be in descending-relevance order — the *position* in the list is
    what gets used, not any score on the chunks. A candidate appearing in more than one leg is
    merged into a single :class:`RetrievedChunk` carrying every stage score it earned, rather than
    one leg's copy silently shadowing the other's.
    """
    if k < 1:
        raise ValueError(f"k must be >= 1, received {k}.")

    contributions: dict[uuid.UUID, float] = {}
    stage_scores: dict[uuid.UUID, dict[str, float]] = {}
    representative: dict[uuid.UUID, RetrievedChunk] = {}
    max_possible = 0.0

    for leg_name, ranked in legs.items():
        weight = float(weights.get(leg_name, 0.0))
        if weight <= 0.0 or not ranked:
            continue
        max_possible += weight / (k + 1)
        for position, chunk in enumerate(ranked, start=1):
            contributions[chunk.id] = contributions.get(chunk.id, 0.0) + weight / (k + position)
            stage_scores.setdefault(chunk.id, {})[f"{leg_name}_score"] = _leg_score(
                chunk, leg_name
            )
            # Prefer the first leg's hydration seen for this id — every leg builds the same Chunk
            # content for the same id (same underlying row), so which one wins is arbitrary; the
            # point is to keep exactly one, not accumulate duplicates.
            representative.setdefault(chunk.id, chunk)

    if max_possible <= 0.0:
        return []

    fused: list[RetrievedChunk] = []
    for chunk_id, raw_score in contributions.items():
        normalized = min(1.0, raw_score / max_possible)
        base = representative[chunk_id]
        fused.append(
            replace(
                base,
                score=normalized,
                fused_score=normalized,
                lexical_score=stage_scores[chunk_id].get("lexical_score", base.lexical_score),
                dense_score=stage_scores[chunk_id].get("dense_score", base.dense_score),
            )
        )

    fused.sort(key=lambda c: (-c.score, str(c.id)))
    return fused


def _leg_score(chunk: RetrievedChunk, leg_name: str) -> float:
    """The stage score a leg's own candidate carries, defaulting to 0.0 if somehow absent.

    Read rather than assumed present: a leg module is expected to stamp its own ``*_score`` field
    (``lexical.py`` sets ``lexical_score``, ``dense.py`` sets ``dense_score``), but reading it back
    here — instead of trusting the caller passed the right attribute name — is what a mismatch in
    that convention would actually break on, rather than silently fusing with the wrong number.
    """
    value = getattr(chunk, f"{leg_name}_score", None)
    return float(value) if value is not None else 0.0


__all__ = ["DEFAULT_RRF_K", "fuse"]
