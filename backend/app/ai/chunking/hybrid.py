"""Hybrid strategy: heading-aware boundaries with content-aware packing inside them.

The production-quality default for cases where :mod:`.heading`'s "one chunk per section, however
tiny" is too fragmented and plain :mod:`.recursive` throws away citation precision entirely.

``config.respect_headings`` (default ``True``) controls the boundary: when true, a chunk never
spans two sections, but *within* a section this strategy applies the full paragraph/sentence/word
cascade (:func:`recursive_pack`) rather than heading's simpler word-window fallback — genuinely
smarter splitting of an oversized section, not just a coarser one. When false, section boundaries
are ignored entirely and this strategy degenerates to exactly :class:`RecursiveChunker`'s behaviour
over the whole document text.
"""

from __future__ import annotations

from app.ai.chunking.base import (
    BaseChunker,
    RawChunk,
    merge_small_trailing_chunk,
    recursive_pack,
)
from app.ai.core.enums import ChunkStrategy
from app.ai.core.types import DocumentSection, ParsedDocument
from app.ai.interfaces.chunker import ChunkingConfig


class HybridChunker(BaseChunker):
    strategy = ChunkStrategy.HYBRID

    def _split(self, document: ParsedDocument, config: ChunkingConfig) -> list[RawChunk]:
        if not config.respect_headings:
            packed = recursive_pack(
                document.text, max_tokens=config.max_tokens, overlap_tokens=config.overlap_tokens,
                count_tokens=self._count,
            )
            packed = merge_small_trailing_chunk(
                packed, min_tokens=config.min_tokens, max_tokens=config.max_tokens,
                count_tokens=self._count,
            )
            return [RawChunk(text=t, index=i) for i, t in enumerate(packed) if t.strip()]

        sections = document.sections or (DocumentSection(text=document.text),)
        raw: list[RawChunk] = []
        index = 0
        for section in sections:
            if not section.text.strip():
                continue
            pieces = recursive_pack(
                section.text, max_tokens=config.max_tokens, overlap_tokens=config.overlap_tokens,
                count_tokens=self._count,
            )
            pieces = merge_small_trailing_chunk(
                pieces, min_tokens=config.min_tokens, max_tokens=config.max_tokens,
                count_tokens=self._count,
            )
            for piece in pieces:
                if not piece.strip():
                    continue
                raw.append(RawChunk(
                    text=piece, index=index,
                    heading_path=section.heading_path, page_number=section.page_number,
                ))
                index += 1
        return raw


__all__ = ["HybridChunker"]
