"""Splits a section too large to extract in one prompt into sub-sections.

A 500-page policy has sections larger than any context window — a "Travel" chapter can run 150
pages. Sending one whole is not an option, and silently truncating it would be the worst option:
the tail of the section would vanish with no error, and the rules in it would simply be absent from
the proposal with nothing to indicate they were ever there.

**Two strategies, tried in order.**

1. *Logical* sub-division, advised by a model. The model is asked one narrow question — "where
   do this section's sub-topics start?" — and answers with titles plus each one's first line.
   It never returns content: the actual split is performed here in Python by locating those lines
   among the section's chunks. A hallucinated line simply fails to match and is discarded, so a bad
   model answer degrades to strategy 2 rather than corrupting a section.
2. *Mechanical* packing by token budget, when there is no advisor, the advisor fails, or its answer
   yields no usable boundary. Deterministic and always available.

**Both strategies split on chunk boundaries only.** A chunk is the smallest unit ingestion produced,
never spans two pages, and is what the persisted ``chunk_ids`` refer to; cutting one in half would
mean a section's recorded provenance no longer described the text actually sent to the model. It
is also why no word-level packing helper is needed: ``AI_CHUNK_MAX_TOKENS`` (512 by default) is far
below any section budget, so packing whole chunks can always make progress.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Any, Mapping, Optional, Protocol, Sequence

from app.ai.core.text import estimate_tokens
from app.ai.policy_extraction.sectioning.types import DetectedSection, DetectionMethod
from app.core.logging import get_logger

logger = get_logger(__name__)

#: How many chunks of the previous sub-section each mechanical split carries as context. One is
#: enough to keep a table header with its rows, and every extra chunk is re-read by another call.
_OVERLAP_CHUNKS = 1

#: A hard stop on sub-sections produced from one section. A pathological input (one enormous section
#: and a tiny budget) would otherwise fan out into hundreds of billed LLM calls. Hitting this is
#: logged, never silent — a truncated split that claimed to be complete is exactly the failure this
#: module exists to avoid.
MAX_SUBSECTIONS = 60


@dataclass(frozen=True, slots=True)
class ProposedSubsection:
    """One sub-topic an advisor claims to have found: its title and its verbatim opening line."""

    title: str
    first_line: str


class SubsectionAdvisor(Protocol):
    """Proposes where a large section's sub-topics begin.

    Returning an empty list is a legitimate answer meaning "this section has no internal structure",
    which sends the caller to the mechanical fallback. Raising is also handled — an advisor is an
    optimisation, never a dependency, and no advisor failure may prevent a section from being split.
    """

    def propose(self, section: DetectedSection) -> Sequence[ProposedSubsection]: ...


def split_oversized_sections(
    sections: Sequence[DetectedSection],
    *,
    chunks_by_id: Mapping[uuid.UUID, Any],
    max_tokens: int,
    advisor: Optional[SubsectionAdvisor] = None,
) -> list[DetectedSection]:
    """Return ``sections`` with any over-budget section replaced by its sub-sections.

    Sections within budget pass through untouched and unre-indexed until the final renumbering, so a
    document whose sections all fit is unaffected by this stage beyond a token count per section.
    """
    result: list[DetectedSection] = []
    for section in sections:
        if section.token_count <= max_tokens:
            result.append(section)
            continue
        result.extend(
            _split_one(section, chunks_by_id=chunks_by_id, max_tokens=max_tokens, advisor=advisor)
        )
    return [replace(section, index=index) for index, section in enumerate(result)]


def _split_one(
    section: DetectedSection,
    *,
    chunks_by_id: Mapping[uuid.UUID, Any],
    max_tokens: int,
    advisor: Optional[SubsectionAdvisor],
) -> list[DetectedSection]:
    chunks = [chunks_by_id[cid] for cid in section.chunk_ids if cid in chunks_by_id]
    if len(chunks) < 2:
        # One chunk over budget cannot be divided without cutting a chunk. Left whole deliberately:
        # the provider will reject it or truncate it visibly, which is recoverable, whereas silently
        # dropping the only chunk of a section would lose its rules with no trace.
        logger.warning(
            "ai.policy_extraction.section_unsplittable",
            extra={"section": section.title, "tokens": section.token_count},
        )
        return [section]

    groups = _advised_groups(section, chunks, advisor=advisor, max_tokens=max_tokens)
    if groups is None:
        groups = _packed_groups(chunks, max_tokens=max_tokens)
        method = DetectionMethod.SEMANTIC
        titles = [f"{section.title} (part {n})" for n in range(1, len(groups) + 1)]
    else:
        groups, proposed_titles = groups
        method = DetectionMethod.LLM
        titles = [f"{section.title} / {title}" for title in proposed_titles]

    if len(groups) > MAX_SUBSECTIONS:
        logger.warning(
            "ai.policy_extraction.subsection_cap_reached",
            extra={
                "section": section.title,
                "produced": len(groups),
                "cap": MAX_SUBSECTIONS,
                "droppedChunks": sum(len(g) for g in groups[MAX_SUBSECTIONS:]),
            },
        )
        groups = groups[:MAX_SUBSECTIONS]
        titles = titles[:MAX_SUBSECTIONS]

    return [
        _subsection(
            group,
            index=section.index,
            title=title,
            method=method,
            confidence_source=section,
            previous_context=(
                section.previous_context if position == 0 else _context_of(groups[position - 1])
            ),
        )
        for position, (group, title) in enumerate(zip(groups, titles, strict=True))
    ]


def _advised_groups(
    section: DetectedSection,
    chunks: Sequence[Any],
    *,
    advisor: Optional[SubsectionAdvisor],
    max_tokens: int,
) -> Optional[tuple[list[list[Any]], list[str]]]:
    """Chunk groups derived from an advisor's proposal, or ``None`` to use the mechanical fallback.

    Every proposal is verified against the section's own text before it is trusted: a proposed
    opening line must occur in some chunk, and the resulting groups must all fit the budget.
    A proposal that fails either check is discarded wholesale rather than partially applied.
    """
    if advisor is None:
        return None
    try:
        proposals = list(advisor.propose(section))
    except Exception as exc:  # noqa: BLE001 - an advisor is an optimisation, never a dependency
        logger.warning(
            "ai.policy_extraction.subsection_advisor_failed",
            extra={"section": section.title, "error": f"{type(exc).__name__}: {exc}"},
        )
        return None
    if not proposals:
        return None

    matched: list[tuple[int, str]] = []
    for proposal in proposals:
        position = _locate(chunks, proposal.first_line)
        if position is None or position == 0:
            continue
        if any(position == existing for existing, _ in matched):
            continue
        matched.append((position, proposal.title))
    if not matched:
        logger.info(
            "ai.policy_extraction.subsection_advice_unusable",
            extra={"section": section.title, "proposed": len(proposals)},
        )
        return None

    matched.sort()
    starts = [0, *(position for position, _ in matched)]
    titles = ["Overview", *(title for _, title in matched)]

    groups = [chunks[starts[i]: (starts[i + 1] if i + 1 < len(starts) else len(chunks))]
              for i in range(len(starts))]
    groups = [list(group) for group in groups if group]
    titles = titles[: len(groups)]

    # Advice that leaves any group still over budget has not solved the problem it was asked to
    # solve. Falling back keeps the guarantee that no prompt exceeds the configured ceiling.
    if any(_tokens_of(group) > max_tokens for group in groups):
        logger.info(
            "ai.policy_extraction.subsection_advice_still_oversized",
            extra={"section": section.title, "groups": len(groups)},
        )
        return None
    return groups, titles


def _packed_groups(chunks: Sequence[Any], *, max_tokens: int) -> list[list[Any]]:
    """Greedily pack whole chunks up to ``max_tokens``, carrying one chunk of overlap forward.

    Progress is unconditional: a group always takes at least one chunk even when that chunk alone
    exceeds the budget, and the overlap can never push the start position backwards.
    """
    groups: list[list[Any]] = []
    start = 0
    total = len(chunks)
    while start < total:
        end = start + 1
        while end < total and _tokens_of(chunks[start:end + 1]) <= max_tokens:
            end += 1
        groups.append(list(chunks[start:end]))
        if end >= total:
            break
        start = max(end - _OVERLAP_CHUNKS, start + 1)
    return groups


def _subsection(
    chunks: Sequence[Any],
    *,
    index: int,
    title: str,
    method: DetectionMethod,
    confidence_source: DetectedSection,
    previous_context: str,
) -> DetectedSection:
    pages = [c.page_number for c in chunks if c.page_number]
    return DetectedSection(
        index=index,
        title=title,
        start_page=min(pages) if pages else confidence_source.start_page,
        end_page=max(pages) if pages else confidence_source.end_page,
        chunk_ids=tuple(c.id for c in chunks),
        text="\n\n".join(_page_tagged(c) for c in chunks),
        detection_method=method,
        # The sub-section inherits its parent's confidence: the parent boundary is what was actually
        # detected, and a sub-division of it is no more certain than the section it came from.
        detection_confidence=confidence_source.detection_confidence,
        table_detected=any(_looks_like_a_table(c.content or "") for c in chunks),
        char_count=sum(len(c.content or "") for c in chunks),
        token_count=_tokens_of(chunks),
        previous_context=previous_context,
        chunk_count=len(chunks),
    )


def _locate(chunks: Sequence[Any], first_line: str) -> Optional[int]:
    """Position of the chunk whose text contains ``first_line``, or ``None``.

    Comparison is whitespace-insensitive because the model retypes the line rather than copying
    bytes, and a PDF's extracted text is full of irregular spacing.
    """
    needle = _collapse(first_line)
    if len(needle) < 4:
        return None
    for position, chunk in enumerate(chunks):
        if needle in _collapse(chunk.content or ""):
            return position
    return None


def _collapse(text: str) -> str:
    return " ".join(str(text).split()).lower()


def _tokens_of(chunks: Sequence[Any]) -> int:
    return sum((c.content_tokens or estimate_tokens(c.content or "")) for c in chunks)


def _context_of(chunks: Sequence[Any]) -> str:
    return _page_tagged(chunks[-1]) if chunks else ""


def _page_tagged(chunk: Any) -> str:
    page = chunk.page_number or 0
    body = (chunk.content or "").strip()
    return f"[Page {page}]\n{body}" if page else body


def _looks_like_a_table(text: str) -> bool:
    return any(line.count("|") >= 3 for line in text.splitlines())


__all__ = [
    "MAX_SUBSECTIONS",
    "ProposedSubsection",
    "SubsectionAdvisor",
    "split_oversized_sections",
]
