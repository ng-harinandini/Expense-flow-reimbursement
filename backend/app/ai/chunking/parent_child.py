"""Parent-child strategy: large context blocks with small, precisely embeddable children.

Parents are packed to ``config.parent_max_tokens`` (wide context for a model to read); each parent
is then re-packed to ``config.max_tokens`` for its children (precise units for embedding search).
Both parent and child chunks are included in the returned sequence, interleaved in document order —
a parent immediately followed by its own children — so a caller can filter by
:attr:`~app.ai.core.types.Chunk.is_child` or resolve a child's ``parent_id`` without a second
lookup. The parent's text is stored once, on the parent chunk; children hold only their own smaller
span, never a copy of the parent's text (contract clause 5).
"""

from __future__ import annotations

from app.ai.chunking.base import BaseChunker, RawChunk, recursive_pack
from app.ai.core.enums import ChunkStrategy
from app.ai.core.errors import AIValidationError
from app.ai.core.types import ParsedDocument
from app.ai.interfaces.chunker import ChunkingConfig


class ParentChildChunker(BaseChunker):
    strategy = ChunkStrategy.PARENT_CHILD

    def _split(self, document: ParsedDocument, config: ChunkingConfig) -> list[RawChunk]:
        if config.max_tokens >= config.parent_max_tokens:
            # ChunkingConfig only validates overlap/min against max_tokens, not this pairing. Left
            # unchecked, a parent already within config.max_tokens would re-pack into a single
            # "child" that is a verbatim copy of the parent's full text — contradicting this
            # strategy's own contract that a child never holds a copy of the parent's text.
            raise AIValidationError(
                f"parent_max_tokens ({config.parent_max_tokens}) must be > max_tokens "
                f"({config.max_tokens}) for PARENT_CHILD chunking, or a child would just be a copy "
                "of its parent.",
                details={
                    "maxTokens": config.max_tokens, "parentMaxTokens": config.parent_max_tokens,
                },
            )
        parent_texts = recursive_pack(
            document.text, max_tokens=config.parent_max_tokens,
            overlap_tokens=config.overlap_tokens, count_tokens=self._count,
        )
        raw: list[RawChunk] = []
        index = 0
        for parent_text in parent_texts:
            if not parent_text.strip():
                continue
            parent_index = index
            raw.append(RawChunk(text=parent_text, index=parent_index))
            index += 1

            child_texts = recursive_pack(
                parent_text, max_tokens=config.max_tokens, overlap_tokens=config.overlap_tokens,
                count_tokens=self._count,
            )
            for child_text in child_texts:
                if not child_text.strip():
                    continue
                raw.append(RawChunk(text=child_text, index=index, parent_index=parent_index))
                index += 1
        return raw


__all__ = ["ParentChildChunker"]
