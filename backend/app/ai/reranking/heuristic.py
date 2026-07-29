"""The heuristic reranker — the guaranteed-available default.

Plays the same role for reranking that :class:`~app.ai.providers.embeddings.deterministic.\
DeterministicEmbeddingProvider` plays for embeddings: no model, no network call, no credentials, so
retrieval always has *some* reranking available even with every optional provider absent. It exists
for offline CI, as the graceful-degradation target, and as a second adapter proving the ``Reranker``
contract suite is real — never as a stand-in for a genuine cross-encoder.

Two signals, deterministic and explainable:

* **Term coverage** — the fraction of the query's distinct terms that appear anywhere in the chunk.
  A chunk mentioning every word of the query is doing more work than one mentioning half of them,
  regardless of how the earlier fusion stage scored it.
* **Exact-phrase bonus** — a flat bonus when the query appears verbatim (after the same
  case/accent-insensitive fold used everywhere else in the platform) as a substring of the chunk.
  Fusion and dense similarity can both miss this: a semantically-adjacent chunk can outscore the one
  containing the query's exact wording, which is usually the wrong outcome for a policy lookup where
  the caller typed the term they actually care about.

The blend (0.7 coverage, 0.3 phrase bonus) is a fixed, documented constant rather than a tuned
model — the point of *this* reranker is to be predictable, not maximally accurate; a real
cross-encoder is what a deployment reaches for when accuracy matters more than explainability.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional, Sequence

from app.ai.core.text import normalize_for_matching
from app.ai.core.types import RetrievedChunk

NAME = "heuristic"

COVERAGE_WEIGHT = 0.7
PHRASE_BONUS_WEIGHT = 0.3


class HeuristicReranker:
    """Deterministic term-coverage + exact-phrase reranker. Implements the ``Reranker`` protocol."""

    name = NAME

    def is_available(self) -> bool:
        """Always. No model, no network, no credentials — this is the whole point of it."""
        return True

    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievedChunk],
        *,
        top_n: Optional[int] = None,
    ) -> list[RetrievedChunk]:
        """Reorder every candidate given. ``top_n`` is accepted for protocol compliance and ignored.

        Truncation is the caller's decision, made after reranking (contract clause 1) — this runs
        entirely in-process, so there is no payload or cost saved by truncating early, unlike an
        HTTP-based provider that can shrink its response body.
        """
        query_terms = frozenset(normalize_for_matching(query).split())
        query_phrase = normalize_for_matching(query).strip()

        scored = [
            replace(candidate, score=score, rerank_score=score)
            for candidate, score in (
                (c, self._score(c, query_terms=query_terms, query_phrase=query_phrase))
                for c in candidates
            )
        ]
        # Descending score, id tie-break — matching every other ranked stage in the platform.
        scored.sort(key=lambda c: (-c.score, str(c.id)))
        return scored

    def _score(
        self, candidate: RetrievedChunk, *, query_terms: frozenset[str], query_phrase: str
    ) -> float:
        if not query_terms:
            return 0.0
        text = normalize_for_matching(candidate.text)
        text_words = frozenset(text.split())
        coverage = len(query_terms & text_words) / len(query_terms)
        phrase_bonus = 1.0 if (query_phrase and query_phrase in text) else 0.0
        return min(1.0, COVERAGE_WEIGHT * coverage + PHRASE_BONUS_WEIGHT * phrase_bonus)


__all__ = ["COVERAGE_WEIGHT", "NAME", "PHRASE_BONUS_WEIGHT", "HeuristicReranker"]
