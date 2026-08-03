"""Turns a document's chunks into logical sections, using the detectors to find the boundaries.

This replaces the page grouping the extractor previously used. The difference matters for two
reasons beyond scale: a page is not a unit of meaning (a rule and the heading that qualifies it
routinely sit on different pages), and a page-per-prompt scheme cannot be retried or reasoned about
per topic. A section can be re-extracted, marked failed, and cited on its own.

**The safety property.** If no detector ever clears the confidence threshold, the whole document
becomes exactly one section — which makes one LLM call with the whole document, i.e. precisely the
behaviour this pipeline replaced. A document with no detectable structure is therefore not a failure
case, it is the old path, and adding sectioning cannot regress it.

Attribution is per *chunk*, never per line. A chunk is already the smallest unit the ingestion
pipeline produced, it never spans two pages, and its text is what gets sent to the model — splitting
one mid-way here would mean the persisted ``chunk_ids`` no longer described what was actually read.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Optional, Sequence

from app.ai.core.config import ai_settings
from app.ai.core.text import estimate_tokens
from app.ai.policy_extraction.sectioning.detectors import (
    DEFAULT_DETECTORS,
    HeadingDetectorProtocol,
)
from app.ai.policy_extraction.sectioning.types import (
    DetectedSection,
    DetectionMethod,
    HeadingCandidate,
)

#: Title for content appearing before the first detected heading. Never dropped: a policy's front
#: matter is where the effective date, scope, and currency are usually stated, and those are exactly
#: the document-level facts the extractor is asked to report.
PREAMBLE_TITLE = "Introduction"

#: Title used when nothing was detected at all and the document is one section.
WHOLE_DOCUMENT_TITLE = "Full Document"

#: Several ``|`` on one line is a table a PDF extractor rendered badly — the case the extraction
#: prompt is specifically written to still read. Recorded per section so a reviewer can see which
#: sections carried tables, matching the existing per-page ``table_detected`` convention.
_TABLE_PIPE_THRESHOLD = 3


class SectionDetectionPipeline:
    """Runs every detector over every chunk and builds sections from the winning boundaries.

    All tiers run over all chunks rather than short-circuiting on the first hit, because the tiers
    are not a precedence chain — they are independent scorers. A chunk can look like a numbered
    heading *and* a plain one; taking the highest score is what makes adding a fourth detector a
    local change rather than a re-ordering of the whole cascade.
    """

    def __init__(
        self,
        *,
        detectors: Sequence[HeadingDetectorProtocol] = DEFAULT_DETECTORS,
        min_confidence: Optional[Decimal] = None,
    ) -> None:
        self._detectors = tuple(detectors)
        self._min_confidence = (
            min_confidence
            if min_confidence is not None
            else Decimal(str(ai_settings.EXTRACTION_MIN_HEURISTIC_CONFIDENCE))
        )

    def detect(
        self, chunks: Sequence[Any], *, known_categories: Sequence[str] = ()
    ) -> list[DetectedSection]:
        """Group ``chunks`` into sections. ``chunks`` are ``KnowledgeChunk`` rows in any order."""
        ordered = _ordered_chunks(chunks)
        if not ordered:
            return []

        candidates = self._candidates(ordered, known_categories=known_categories)
        return _build_sections(ordered, candidates)

    def _candidates(
        self, ordered: Sequence[Any], *, known_categories: Sequence[str]
    ) -> dict[int, HeadingCandidate]:
        """The winning candidate per chunk position, keyed by position in ``ordered``.

        Position 0 is included even though it can never *split* anything. A document whose very
        first chunk is a heading is the common case, and knowing that lets the first section carry
        its real title instead of being labelled a preamble it is not.
        """
        winners: dict[int, HeadingCandidate] = {}
        for position, chunk in enumerate(ordered):
            best: Optional[HeadingCandidate] = None
            for detector in self._detectors:
                candidate = detector.detect(
                    chunk.content or "",
                    chunk_index=position,
                    known_categories=known_categories,
                )
                if candidate is None or candidate.confidence < self._min_confidence:
                    continue
                if best is None or candidate.confidence > best.confidence:
                    best = candidate
            if best is not None:
                winners[position] = best
        return winners


def _ordered_chunks(chunks: Sequence[Any]) -> list[Any]:
    """Document order: by page, then by the chunker's own index within the page.

    ``chunk_index`` is already globally sequential in document order (see ``BaseChunker.chunk``), so
    sorting by page first is belt-and-braces against a caller that hands over chunks from a query
    with no ``ORDER BY``.
    """
    return sorted(chunks, key=lambda c: ((c.page_number or 0), c.chunk_index))


def _build_sections(
    ordered: Sequence[Any], candidates: dict[int, HeadingCandidate]
) -> list[DetectedSection]:
    # A boundary is a heading that something precedes. A heading at position 0 titles the first
    # section but splits nothing.
    starts = sorted(position for position in candidates if position > 0)
    leading = candidates.get(0)

    if not starts:
        return [
            _section_from(
                ordered,
                index=0,
                title=leading.title if leading else WHOLE_DOCUMENT_TITLE,
                candidate=leading,
            )
        ]

    sections: list[DetectedSection] = [
        _section_from(
            ordered[: starts[0]],
            index=0,
            # Only "Introduction" when the opening text really is untitled front matter.
            title=leading.title if leading else PREAMBLE_TITLE,
            candidate=leading,
        )
    ]
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(ordered)
        candidate = candidates[start]
        sections.append(
            _section_from(
                ordered[start:end],
                index=len(sections),
                title=candidate.title,
                candidate=candidate,
                previous_context=_trailing_context(ordered[:start]),
            )
        )
    return sections


def _section_from(
    chunks: Sequence[Any],
    *,
    index: int,
    title: str,
    candidate: Optional[HeadingCandidate],
    previous_context: str = "",
) -> DetectedSection:
    text = "\n\n".join(_page_tagged(chunk) for chunk in chunks)
    pages = [c.page_number for c in chunks if c.page_number]
    return DetectedSection(
        index=index,
        title=title,
        start_page=min(pages) if pages else 1,
        end_page=max(pages) if pages else 1,
        chunk_ids=tuple(_chunk_id(c) for c in chunks),
        text=text,
        detection_method=(
            candidate.method if candidate is not None else DetectionMethod.SEMANTIC
        ),
        detection_confidence=(
            candidate.confidence if candidate is not None else Decimal("0.00")
        ),
        table_detected=any(_looks_like_a_table(c.content or "") for c in chunks),
        char_count=sum(len(c.content or "") for c in chunks),
        token_count=sum(
            (c.content_tokens or estimate_tokens(c.content or "")) for c in chunks
        ),
        previous_context=previous_context,
        chunk_count=len(chunks),
    )


def _page_tagged(chunk: Any) -> str:
    """Prefix each chunk with its page number.

    The extraction prompt requires a page citation for every rule, and the page number is only
    knowable to the model if it is written into the text — the model has no access to the chunk row
    it came from. Kept per *chunk* rather than per section because a section spans pages, and a rule
    must cite the page its own evidence sits on, not the section's first page.
    """
    page = chunk.page_number or 0
    body = (chunk.content or "").strip()
    return f"[Page {page}]\n{body}" if page else body


def _chunk_id(chunk: Any) -> uuid.UUID:
    return chunk.id


def _trailing_context(preceding: Sequence[Any], *, chunks: int = 1) -> str:
    """The last chunk(s) of everything before this section, as read-only continuity context.

    A limits table can end one section and continue into the next — the header row under the old
    heading, the data rows under the new one. Without the tail of the previous section, the second
    LLM call sees a headerless table and cannot tell which column is the cap and which the
    auto-approve threshold. The prompt marks this block as context and forbids re-reporting rules
    already fully contained in it, so the overlap cannot double-count a rule.
    """
    if not preceding:
        return ""
    return "\n\n".join(_page_tagged(chunk) for chunk in preceding[-chunks:])


def _looks_like_a_table(text: str) -> bool:
    return any(line.count("|") >= _TABLE_PIPE_THRESHOLD for line in text.splitlines())


__all__ = [
    "PREAMBLE_TITLE",
    "WHOLE_DOCUMENT_TITLE",
    "SectionDetectionPipeline",
]
