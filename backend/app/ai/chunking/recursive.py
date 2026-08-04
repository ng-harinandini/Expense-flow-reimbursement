"""Recursive strategy: paragraph -> sentence -> word cascade, the default.

Ignores heading structure entirely (that is :mod:`app.ai.chunking.heading`'s and
:mod:`app.ai.chunking.hybrid`'s job) and works on the whole document text, which is what "recursive"
names: progressively finer splitting only where a unit does not already fit the budget.
"""

from __future__ import annotations

from app.ai.chunking.base import (
    BaseChunker,
    RawChunk,
    merge_small_trailing_chunk,
    recursive_pack,
)
from app.ai.core.enums import ChunkStrategy
from app.ai.core.types import ParsedDocument
from app.ai.interfaces.chunker import ChunkingConfig


class RecursiveChunker(BaseChunker):
    strategy = ChunkStrategy.RECURSIVE

    def _split(self, document: ParsedDocument, config: ChunkingConfig) -> list[RawChunk]:
        packed = recursive_pack(
            document.text,
            max_tokens=config.max_tokens,
            overlap_tokens=config.overlap_tokens,
            count_tokens=self._count,
        )
        packed = merge_small_trailing_chunk(
            packed, min_tokens=config.min_tokens, max_tokens=config.max_tokens,
            count_tokens=self._count,
        )
        return [RawChunk(text=t, index=i) for i, t in enumerate(packed) if t.strip()]


__all__ = ["RecursiveChunker"]
