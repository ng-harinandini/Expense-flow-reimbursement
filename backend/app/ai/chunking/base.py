"""Shared behaviour every chunking strategy inherits.

The ``Chunker`` protocol (``app/ai/interfaces/chunker.py``) states the contract; this module
implements the parts every strategy would otherwise duplicate:

* **Raw-to-final assembly.** A strategy only decides *what text goes where* — it returns
  :class:`RawChunk` values naming a parent by position, not by id. :class:`BaseChunker` turns
  those into real :class:`~app.ai.core.types.Chunk` objects: deterministic ids
  (:func:`app.ai.core.ids.chunk_id_for`), parent-id resolution, per-chunk metadata (page number,
  section, content checksum), and token counts from the injected counter.
* **Invariant checks** (contract clauses 2 and 5): after a strategy runs, every chunk's budget and
  every parent reference are verified here, once, rather than re-implemented per strategy. A
  strategy bug that produces an oversized or dangling-parent chunk fails loudly instead of shipping
  a silently broken index.
* **Two packing helpers** — :func:`pack_with_overlap` (generic greedy packing with a token-based
  overlap tail) and :func:`recursive_pack` (paragraph -> sentence -> word cascade on top of it) —
  shared by every strategy that needs to fit natural-language spans into a token budget.

What is deliberately *not* here: any strategy's actual splitting logic. That is each strategy
file's only job.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from app.ai.core.enums import ChunkStrategy
from app.ai.core.errors import AIValidationError
from app.ai.core.ids import chunk_id_for, deterministic_uuid, text_checksum
from app.ai.core.text import estimate_tokens, split_paragraphs, split_sentences
from app.ai.core.types import Chunk, ChunkMetadata, ParsedDocument
from app.ai.interfaces.chunker import ChunkingConfig
from app.ai.interfaces.embeddings import TokenCounter

# --- token counting default ---------------------------------------------------


class HeuristicTokenCounter:
    """Default :class:`TokenCounter`: wraps the script-aware estimate.

    Used whenever a strategy is built without an injected counter, so every strategy is usable
    without a loaded model (bge-m3's real tokenizer, via ``BgeM3TokenCounter``, is the exact
    alternative once embeddings are available).
    """

    def count(self, text: str) -> int:
        return estimate_tokens(text)


DEFAULT_TOKEN_COUNTER = HeuristicTokenCounter()


# --- packing helpers ------------------------------------------------------------


def grow_window(
    units: Sequence[str],
    start: int,
    *,
    max_tokens: int,
    count_tokens: Callable[[str], int],
    joiner: str = " ",
) -> tuple[str, int]:
    """Greedily extend a window from ``start``, measuring the actual **joined** candidate text.

    Returns ``(joined_text, end)`` where ``units[start:end]`` is the window. A single unit that
    alone exceeds ``max_tokens`` is still returned whole (``end = start + 1``) rather than looping
    forever — callers that want no unit to exceed the budget must pre-split with something finer
    (:func:`recursive_pack`'s paragraph/sentence/word cascade).

    Measuring the joined string at every step, rather than summing independent per-unit counts, is
    what every caller of this function needs: summing would silently ignore the joiner characters
    (and, for a real subword tokenizer, cross-unit merges), letting a window's true count exceed the
    budget it was supposedly built to respect. Shared by :func:`pack_with_overlap` and
    :class:`~app.ai.chunking.sliding_window.SlidingWindowChunker`, which grow windows identically
    and differ only in how they advance past one.
    """
    current_text = units[start]
    end = start + 1
    n = len(units)
    while end < n:
        candidate = current_text + joiner + units[end]
        if count_tokens(candidate) > max_tokens:
            break
        current_text = candidate
        end += 1
    return current_text, end


def pack_with_overlap(
    units: Sequence[str],
    *,
    max_tokens: int,
    overlap_tokens: int,
    count_tokens: Callable[[str], int],
    joiner: str = " ",
) -> list[str]:
    """Greedily pack ``units`` (paragraphs, sentences or words) into chunks <= ``max_tokens``.

    Each chunk after the first starts with up to ``overlap_tokens`` of trailing content carried
    over from the previous chunk, for context continuity across the boundary.

    Progress (contract clause 4) is unconditional: the window always advances by at least one unit,
    even in the degenerate case where the whole current window falls inside the overlap tail.
    """
    if not units:
        return []
    chunks: list[str] = []
    start = 0
    n = len(units)
    while start < n:
        current_text, end = grow_window(
            units, start, max_tokens=max_tokens, count_tokens=count_tokens, joiner=joiner
        )
        chunks.append(current_text)
        if end >= n:
            break

        overlap_start = end
        tail_text = ""
        while overlap_start > start:
            candidate_tail = (
                units[overlap_start - 1] if not tail_text
                else units[overlap_start - 1] + joiner + tail_text
            )
            if count_tokens(candidate_tail) > overlap_tokens:
                break
            tail_text = candidate_tail
            overlap_start -= 1
        next_start = overlap_start if start < overlap_start < end else end
        start = next_start
    return chunks


def recursive_pack(
    text: str,
    *,
    max_tokens: int,
    overlap_tokens: int,
    count_tokens: Callable[[str], int],
) -> list[str]:
    """Paragraph -> sentence -> word cascade feeding :func:`pack_with_overlap`.

    Any paragraph over budget is broken into sentences; any sentence still over budget is broken
    into words. This is what lets ``pack_with_overlap`` treat "a single unit too large to split
    further" as the genuine edge case it is, rather than the common one.
    """
    if not text or not text.strip():
        return []
    units: list[str] = []
    for paragraph in split_paragraphs(text) or [text]:
        if count_tokens(paragraph) <= max_tokens:
            units.append(paragraph)
            continue
        for sentence in split_sentences(paragraph) or [paragraph]:
            if count_tokens(sentence) <= max_tokens:
                units.append(sentence)
                continue
            units.extend(sentence.split())
    return pack_with_overlap(
        units, max_tokens=max_tokens, overlap_tokens=overlap_tokens, count_tokens=count_tokens
    )


def merge_small_trailing_chunk(chunks: list[str], *, min_tokens: int, max_tokens: int,
                                count_tokens: Callable[[str], int], joiner: str = " ") -> list[str]:
    """Fold a too-small final chunk into its predecessor, honouring ``config.min_tokens``.

    Only ever touches the *last* chunk: merging mid-sequence would shift every later chunk's index
    and, with it, every later chunk's id (contract clause 3). Skipped if the merge would itself
    exceed ``max_tokens``, or if there is only one chunk to begin with.
    """
    if len(chunks) < 2:
        return chunks
    if count_tokens(chunks[-1]) >= min_tokens:
        return chunks
    merged = joiner.join(chunks[-2:])
    if count_tokens(merged) > max_tokens:
        return chunks
    return chunks[:-2] + [merged]


# --- raw chunk (strategy output, before id/metadata assembly) -----------------


@dataclass(frozen=True, slots=True)
class RawChunk:
    """What a strategy emits: text and its place in the document. No id yet."""

    text: str
    index: int
    parent_index: int | None = None  # position of the parent within the SAME raw list
    heading_path: tuple[str, ...] = ()
    page_number: int | None = None


# --- base class -----------------------------------------------------------------


class BaseChunker(ABC):
    """Base for every chunking strategy. Implements assembly and invariants; ``_split`` is the
    only method a strategy provides."""

    strategy: ChunkStrategy

    def __init__(self, *, token_counter: TokenCounter | None = None) -> None:
        self._token_counter = token_counter or DEFAULT_TOKEN_COUNTER

    def _count(self, text: str) -> int:
        return self._token_counter.count(text)

    def chunk(
        self,
        document: ParsedDocument,
        *,
        config: ChunkingConfig,
        metadata: ChunkMetadata | None = None,
    ) -> tuple[Chunk, ...]:
        if config.strategy is not self.strategy:
            raise AIValidationError(
                f"{type(self).__name__} implements {self.strategy.value}, "
                f"but config declares {config.strategy.value}.",
                details={"chunker": self.strategy.value, "config": config.strategy.value},
            )
        if not document.text.strip():
            return ()

        raw_chunks = self._split(document, config)
        if not raw_chunks:
            return ()

        document_id = (
            metadata.document_id if metadata and metadata.document_id else None
        ) or deterministic_uuid("parsed_document", document.checksum)
        ids = [chunk_id_for(document_id, r.index, r.text) for r in raw_chunks]

        base_metadata = metadata or ChunkMetadata()
        chunks: list[Chunk] = []
        for raw, chunk_id in zip(raw_chunks, ids, strict=True):
            parent_id = ids[raw.parent_index] if raw.parent_index is not None else None
            chunk_metadata = replace(
                base_metadata,
                document_id=document_id,
                page_number=(
                    raw.page_number if raw.page_number is not None else base_metadata.page_number
                ),
                section=raw.heading_path[-1] if raw.heading_path else base_metadata.section,
                checksum=text_checksum(raw.text),
                language=base_metadata.language or document.language,
            )
            chunks.append(
                Chunk(
                    id=chunk_id,
                    text=raw.text,
                    index=raw.index,
                    strategy=self.strategy,
                    parent_id=parent_id,
                    token_count=self._count(raw.text),
                    heading_path=raw.heading_path,
                    metadata=chunk_metadata,
                )
            )

        self._validate_invariants(chunks, config)
        return tuple(chunks)

    @abstractmethod
    def _split(self, document: ParsedDocument, config: ChunkingConfig) -> list[RawChunk]:
        """Strategy-specific splitting. Indices must be sequential from 0; a parent must appear at
        a lower index than any child that references it."""

    @staticmethod
    def _validate_invariants(chunks: Sequence[Chunk], config: ChunkingConfig) -> None:
        ids = {c.id for c in chunks}
        parent_ids = {c.parent_id for c in chunks if c.parent_id is not None}
        dangling = parent_ids - ids
        if dangling:
            raise RuntimeError(
                f"Chunking produced {len(dangling)} chunk(s) with a parent_id absent from the same "
                "result set. This is an internal chunker bug, not a data problem."
            )
        for chunk in chunks:
            budget = config.parent_max_tokens if chunk.id in parent_ids else config.max_tokens
            if chunk.token_count > budget:
                raise RuntimeError(
                    f"Chunk {chunk.index} has {chunk.token_count} tokens, over its {budget}-token "
                    f"budget. This is an internal {chunk.strategy.value} chunker bug."
                )


__all__ = [
    "DEFAULT_TOKEN_COUNTER",
    "BaseChunker",
    "HeuristicTokenCounter",
    "RawChunk",
    "grow_window",
    "merge_small_trailing_chunk",
    "pack_with_overlap",
    "recursive_pack",
]
