"""The token-budget packer — the mechanism behind the platform's one hard promise for retrieval:
the returned context never exceeds ``context_budget_tokens``.

Chunks arrive already ranked (fused, optionally reranked, optionally compressed); this module never
reorders them. It is a prefix cutoff: walk the ranked list, keep a running total, and stop admitting
chunks once the next one would push the total over budget.

**The top-ranked chunk is always included, even alone over budget.** A caller who asked for context
and got none because the single best match happened to be a long chunk would receive nothing when
something — even an over-length something — was available. The alternative (return nothing) is
strictly worse: it looks like "no evidence found" when evidence was found and merely didn't fit
cleanly. Every chunk *after* the first is admitted only if it fits.
"""

from __future__ import annotations

from typing import Callable, Sequence

from app.ai.core.text import estimate_tokens
from app.ai.core.types import RetrievedChunk

TokenCounter = Callable[[str], int]


def pack(
    chunks: Sequence[RetrievedChunk],
    *,
    budget_tokens: int,
    count_tokens: TokenCounter = estimate_tokens,
) -> tuple[tuple[RetrievedChunk, ...], bool]:
    """Return ``(kept, truncated)``. ``truncated`` is true iff any ranked chunk was dropped.

    ``count_tokens`` falls back to each chunk's own recorded ``token_count`` when it is already
    known and only estimates for one that (unusually) has none — recomputing a token count for
    every chunk on every retrieval would be wasted work the chunking stage already did once.
    """
    if budget_tokens < 1:
        raise ValueError(f"budget_tokens must be >= 1, received {budget_tokens}.")
    if not chunks:
        return (), False

    kept: list[RetrievedChunk] = []
    used = 0
    for index, chunk in enumerate(chunks):
        cost = chunk.chunk.token_count or count_tokens(chunk.text)
        if index == 0:
            kept.append(chunk)
            used += cost
            continue
        if used + cost > budget_tokens:
            # Not a break: a later, smaller chunk further down the ranking could still fit even
            # though this one didn't. Skipping (not stopping) is what lets a budget get used well
            # rather than being wasted the moment one mid-sized chunk doesn't fit.
            continue
        kept.append(chunk)
        used += cost

    truncated = len(kept) < len(chunks)
    return tuple(kept), truncated


__all__ = ["pack"]
