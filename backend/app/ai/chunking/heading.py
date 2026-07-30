"""Heading strategy: one chunk per parsed section, structure as the sole authority.

Sections never merge, even when tiny — the strictest citation precision ("Travel Policy > Meals >
Alcohol" names exactly one chunk), at the cost of more, smaller chunks than :mod:`.hybrid` would
produce for the same document. A section over budget falls back to plain word-window splitting
(no paragraph/sentence awareness): heading structure is treated as authoritative, so the fallback
stays as simple and predictable as possible rather than trying to out-guess the author's structure.
"""

from __future__ import annotations

from app.ai.chunking.base import BaseChunker, RawChunk, pack_with_overlap
from app.ai.core.enums import ChunkStrategy
from app.ai.core.types import DocumentSection, ParsedDocument
from app.ai.interfaces.chunker import ChunkingConfig


class HeadingChunker(BaseChunker):
    strategy = ChunkStrategy.HEADING

    def _split(self, document: ParsedDocument, config: ChunkingConfig) -> list[RawChunk]:
        sections = document.sections or (DocumentSection(text=document.text),)
        raw: list[RawChunk] = []
        index = 0
        for section in sections:
            if not section.text.strip():
                continue
            if self._count(section.text) <= config.max_tokens:
                raw.append(RawChunk(
                    text=section.text, index=index,
                    heading_path=section.heading_path, page_number=section.page_number,
                ))
                index += 1
                continue
            words = section.text.split()
            for piece in pack_with_overlap(
                words, max_tokens=config.max_tokens, overlap_tokens=config.overlap_tokens,
                count_tokens=self._count,
            ):
                if not piece.strip():
                    continue
                raw.append(RawChunk(
                    text=piece, index=index,
                    heading_path=section.heading_path, page_number=section.page_number,
                ))
                index += 1
        return raw


__all__ = ["HeadingChunker"]
