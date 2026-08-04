"""Token strategy: pure word-level windows, no paragraph or sentence awareness.

The simplest strategy — useful for content with no natural prose structure (tables dumped as text,
code, garbled OCR) where paragraph/sentence splitting would not help and might mislead.
"""

from __future__ import annotations

from app.ai.chunking.base import (
    BaseChunker,
    RawChunk,
    merge_small_trailing_chunk,
    pack_with_overlap,
)
from app.ai.core.enums import ChunkStrategy
from app.ai.core.types import ParsedDocument
from app.ai.interfaces.chunker import ChunkingConfig


class TokenChunker(BaseChunker):
    strategy = ChunkStrategy.TOKEN

    def _split(self, document: ParsedDocument, config: ChunkingConfig) -> list[RawChunk]:
        words = document.text.split()
        packed = pack_with_overlap(
            words, max_tokens=config.max_tokens, overlap_tokens=config.overlap_tokens,
            count_tokens=self._count,
        )
        packed = merge_small_trailing_chunk(
            packed, min_tokens=config.min_tokens, max_tokens=config.max_tokens,
            count_tokens=self._count,
        )
        return [RawChunk(text=t, index=i) for i, t in enumerate(packed) if t.strip()]


__all__ = ["TokenChunker"]
