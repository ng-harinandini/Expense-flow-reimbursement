"""Sliding-window strategy: fixed-stride overlapping windows for maximum recall.

Distinct from :mod:`app.ai.chunking.token`: TOKEN advances by ``max_tokens - overlap_tokens`` (and
``overlap_tokens`` is constrained below ``max_tokens`` by :class:`ChunkingConfig`), while this
strategy advances by an independently configured ``sliding_stride_tokens`` — which can be far
smaller than that, producing much denser, more overlapping coverage at the cost of many more
chunks. Intended for the highest-recall retrieval profiles, not the default.
"""

from __future__ import annotations

from app.ai.chunking.base import BaseChunker, RawChunk, grow_window
from app.ai.core.enums import ChunkStrategy
from app.ai.core.types import ParsedDocument
from app.ai.interfaces.chunker import ChunkingConfig


class SlidingWindowChunker(BaseChunker):
    strategy = ChunkStrategy.SLIDING_WINDOW

    def _split(self, document: ParsedDocument, config: ChunkingConfig) -> list[RawChunk]:
        words = document.text.split()
        if not words:
            return []
        texts: list[str] = []
        start = 0
        n = len(words)
        while start < n:
            current_text, end = grow_window(
                words, start, max_tokens=config.max_tokens, count_tokens=self._count,
            )
            texts.append(current_text)
            if end >= n:
                break
            # Advance by stride tokens, measured on the actual joined advancing text — not summed
            # per-word estimates, which would ignore the joiner characters between words and could
            # under-count the true stride (the same class of bug `grow_window` itself guards
            # against for the window it builds).
            advance_text = ""
            next_start = start
            while next_start < end:
                next_word = words[next_start]
                candidate = next_word if not advance_text else advance_text + " " + next_word
                if self._count(candidate) > config.sliding_stride_tokens:
                    break
                advance_text = candidate
                next_start += 1
            start = next_start if next_start > start else start + 1
        return [RawChunk(text=t, index=i) for i, t in enumerate(texts) if t.strip()]


__all__ = ["SlidingWindowChunker"]
