"""Deterministic extractive context compression — opt-in, off by default.

"The LLM is never the source of truth" rules out the obvious approach (ask a model to summarize each
chunk): a summarizer can misstate what a policy says, and there would be no way to tell from the
output alone that it had. This compressor only ever *removes* sentences the original document
already contained — every word a caller sees was written by the source, not paraphrased into it — so
what comes out is still literally quotable.

**Extractive, not generative.** Each chunk's sentences are scored by how many query terms they
contain and the highest-scoring ones are kept, in their *original* order (reading order, not score
order — a policy clause read out of sequence can invert its own meaning, e.g. an exception clause
separated from the rule it exempts).

**Per-chunk, not per-corpus.** The target token count for one chunk is a fair share of the overall
context budget, split across how many chunks the query asked for (``top_k``) — not a fixed constant,
so a query asking for 3 chunks lets each one keep more text than a query asking for 20. Only chunks
that actually exceed their share are touched; a chunk that already fits is returned unchanged,
including its citation-bearing metadata.

Disabled by default (``AI_RETRIEVAL_COMPRESSION_ENABLED=false``) because removing sentences, however
carefully, is still removing evidence a reviewer might have wanted to see — the platform's own
"auditable and reproducible" constraint favours showing the whole chunk unless a caller has
explicitly traded completeness for a tighter budget.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Sequence

from app.ai.core.text import estimate_tokens, normalize_for_matching, split_sentences
from app.ai.core.types import RetrievedChunk

MIN_TARGET_TOKENS = 32

TokenCounter = Callable[[str], int]


def compress_chunks(
    chunks: Sequence[RetrievedChunk],
    *,
    query_text: str,
    context_budget_tokens: int,
    top_k: int,
    count_tokens: TokenCounter = estimate_tokens,
) -> list[RetrievedChunk]:
    """Compress each chunk that exceeds its fair share of the budget; leave the rest untouched."""
    target = max(MIN_TARGET_TOKENS, context_budget_tokens // max(1, top_k))
    query_terms = frozenset(normalize_for_matching(query_text).split())
    return [
        _compress_one(
            chunk, query_terms=query_terms, target_tokens=target, count_tokens=count_tokens
        )
        for chunk in chunks
    ]


def _compress_one(
    chunk: RetrievedChunk, *, query_terms: frozenset[str], target_tokens: int,
    count_tokens: TokenCounter,
) -> RetrievedChunk:
    current = chunk.chunk.token_count or count_tokens(chunk.text)
    if current <= target_tokens:
        return chunk

    sentences = split_sentences(chunk.text)
    if len(sentences) <= 1:
        # Nothing to extract from — nowhere to cut without paraphrasing, which this module refuses
        # to do. Returned whole, over budget; the token-budget packer downstream decides whether it
        # still fits alongside everything else.
        return chunk

    ranked = sorted(
        range(len(sentences)),
        key=lambda i: (-_overlap(sentences[i], query_terms), i),
    )

    kept: set[int] = set()
    used_tokens = 0
    for index in ranked:
        cost = count_tokens(sentences[index])
        # The most relevant sentence is always kept, even alone over budget: an empty chunk is
        # strictly worse evidence than one over-length sentence. ``kept`` is empty only on this
        # first iteration, so every subsequent sentence is genuinely budget-checked.
        if kept and used_tokens + cost > target_tokens:
            continue
        kept.add(index)
        used_tokens += cost
        if used_tokens >= target_tokens:
            break

    text = " ".join(sentences[i] for i in sorted(kept))
    compressed_chunk = replace(chunk.chunk, text=text, token_count=count_tokens(text))
    return replace(chunk, chunk=compressed_chunk)


def _overlap(sentence: str, query_terms: frozenset[str]) -> int:
    """How many query-term occurrences this sentence contains. Ties break by original position."""
    if not query_terms:
        return 0
    words = normalize_for_matching(sentence).split()
    return sum(1 for word in words if word in query_terms)


__all__ = ["MIN_TARGET_TOKENS", "compress_chunks"]
