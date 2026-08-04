"""Reranking (cross-encoder) contract.

A reranker re-scores a *retrieved* candidate set by looking at the query and each passage together,
which a bi-encoder cannot do. It is always optional: if it is unavailable or times out, retrieval
returns the fused ordering unchanged. Precision degrades; nothing breaks.

Contract:

1. Returns exactly the candidates it was given — reranking reorders and rescores, it never drops or
   invents results. Truncation to ``top_n`` is the caller's decision, made after reranking.
2. Scores are normalized to ``[0, 1]``, comparable within one call but not across providers.
3. Order is by descending score, with a stable tie-break so results are reproducible.
4. An unavailable provider raises ``ProviderNotConfiguredError``; the retrieval engine catches
   it and proceeds without reranking.
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from app.ai.core.types import RetrievedChunk


@runtime_checkable
class Reranker(Protocol):
    """Re-scores query/passage pairs jointly."""

    name: str

    def is_available(self) -> bool:
        ...

    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievedChunk],
        *,
        top_n: int | None = None,
    ) -> list[RetrievedChunk]:
        """Reorder ``candidates`` by joint relevance to ``query``.

        Each returned chunk carries its ``rerank_score`` alongside the earlier stage scores, so the
        effect of this stage stays visible and explainable rather than overwriting what came before.
        """
        ...


__all__ = ["Reranker"]
