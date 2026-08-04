"""Chunking contract (Task 3).

A chunker is a **pure function** of ``(ParsedDocument, ChunkingConfig)``. No I/O, no database, no
network. Semantic chunking needs similarity, so it receives an ``EmbeddingProvider`` by constructor
injection rather than reaching for one — which keeps it unit-testable with a deterministic stand-in.

Contract (asserted for every strategy in ``tests/test_ai_chunking.py``):

1. **No text loss.** Concatenating chunks in order reproduces the document's meaningful text, apart
   from deliberate overlap and collapsed whitespace.
2. **Budget respected.** No chunk exceeds ``config.max_tokens`` as measured by the injected counter.
3. **Determinism.** The same input yields the same chunks with the same ids.
4. **Progress.** Chunking always advances; an overlap >= chunk size is a configuration error caught
   at startup (``AISettings.require_valid``) rather than an infinite loop here.
5. **Parent–child integrity.** Any chunk with a ``parent_id`` refers to a chunk in the same result,
   and parent text is stored once, never duplicated into children.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, Sequence, runtime_checkable

from app.ai.core.enums import ChunkStrategy
from app.ai.core.types import Chunk, ChunkMetadata, ParsedDocument


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    """Per-strategy chunking parameters.

    Carried explicitly rather than read from global settings so one ingestion profile can chunk
    travel policies differently from receipts within the same process (Task 3: "each strategy
    configurable").
    """

    strategy: ChunkStrategy = ChunkStrategy.RECURSIVE
    max_tokens: int = 512
    overlap_tokens: int = 64
    min_tokens: int = 32
    parent_max_tokens: int = 2048
    semantic_threshold: float = 0.82
    sliding_stride_tokens: int = 256
    respect_headings: bool = True

    def __post_init__(self) -> None:
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be >= 1.")
        if self.overlap_tokens >= self.max_tokens:
            raise ValueError(
                f"overlap_tokens ({self.overlap_tokens}) must be < max_tokens ({self.max_tokens}); "
                "otherwise chunking cannot advance."
            )
        if self.min_tokens > self.max_tokens:
            raise ValueError("min_tokens cannot exceed max_tokens.")


@runtime_checkable
class Chunker(Protocol):
    """Splits a parsed document into retrievable chunks."""

    strategy: ChunkStrategy

    def chunk(
        self,
        document: ParsedDocument,
        *,
        config: ChunkingConfig,
        metadata: Optional[ChunkMetadata] = None,
    ) -> Sequence[Chunk]:
        """Produce ordered chunks. ``metadata`` is the document-level metadata each chunk
        inherits.
        """
        ...


__all__ = ["Chunker", "ChunkingConfig"]
