"""Splitting a section too large to send to the model in one prompt.

The guarantee under test is narrow and absolute: **no section handed to the extractor exceeds the
configured token budget**, whatever the advisor does. An advisor that fails, returns nothing, or
returns lines that appear nowhere in the section must all end at the same place — a mechanical split
that always works — because the alternative is a prompt that silently overflows and loses the tail
of a section with no error anywhere.

The second guarantee is that a bad advisor answer cannot corrupt anything. The model proposes titles
and opening lines; the split itself is performed by locating those lines among the section's own
chunks, so a hallucinated line simply fails to match.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Sequence

from app.ai.policy_extraction.sectioning import (
    DetectedSection,
    DetectionMethod,
    ProposedSubsection,
    split_oversized_sections,
)

MAX_TOKENS = 100


@dataclass
class FakeChunk:
    content: str
    page_number: int
    chunk_index: int
    content_tokens: int
    id: uuid.UUID = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.id is None:
            self.id = uuid.uuid4()


class FakeAdvisor:
    def __init__(self, proposals: Sequence[ProposedSubsection], *, raises: bool = False) -> None:
        self._proposals = list(proposals)
        self._raises = raises
        self.calls = 0

    def propose(self, section: DetectedSection) -> list[ProposedSubsection]:
        self.calls += 1
        if self._raises:
            raise RuntimeError("provider exploded")
        return list(self._proposals)


def _chunks(*texts: str, tokens: int = 40) -> list[FakeChunk]:
    return [
        FakeChunk(content=text, page_number=index + 1, chunk_index=index, content_tokens=tokens)
        for index, text in enumerate(texts)
    ]


def _section(chunks: Sequence[FakeChunk], *, title: str = "4. Travel") -> DetectedSection:
    return DetectedSection(
        index=0,
        title=title,
        start_page=chunks[0].page_number,
        end_page=chunks[-1].page_number,
        chunk_ids=tuple(c.id for c in chunks),
        text="\n\n".join(c.content for c in chunks),
        detection_method=DetectionMethod.NUMBERING,
        detection_confidence=Decimal("0.95"),
        char_count=sum(len(c.content) for c in chunks),
        token_count=sum(c.content_tokens for c in chunks),
        chunk_count=len(chunks),
    )


def _split(
    chunks: Sequence[FakeChunk],
    *,
    advisor: Optional[FakeAdvisor] = None,
    max_tokens: int = MAX_TOKENS,
) -> list[DetectedSection]:
    return split_oversized_sections(
        [_section(chunks)],
        chunks_by_id={c.id: c for c in chunks},
        max_tokens=max_tokens,
        advisor=advisor,
    )


# --- the budget guarantee --------------------------------------------------------


def test_a_section_within_budget_is_left_alone() -> None:
    chunks = _chunks("Flights content", "Hotels content", tokens=20)
    result = _split(chunks)
    assert len(result) == 1
    assert result[0].chunk_count == 2


def test_no_produced_section_exceeds_the_budget() -> None:
    chunks = _chunks(*[f"Sub-topic {n} body text" for n in range(8)], tokens=40)
    result = _split(chunks)
    assert len(result) > 1
    assert all(section.token_count <= MAX_TOKENS for section in result)


def test_mechanical_split_carries_overlap_so_a_table_keeps_its_header() -> None:
    """Consecutive parts share a chunk. A limits table whose header lands at the end of one part
    would otherwise reach the model without it."""
    chunks = _chunks("header row", "data row one", "data row two", "data row three", tokens=40)
    result = _split(chunks)
    assert len(result) > 1
    for earlier, later in zip(result, result[1:]):
        assert set(earlier.chunk_ids) & set(later.chunk_ids)


def test_split_sections_are_renumbered_contiguously() -> None:
    chunks = _chunks(*[f"part {n}" for n in range(6)], tokens=40)
    result = _split(chunks)
    assert [s.index for s in result] == list(range(len(result)))


def test_a_single_oversized_chunk_is_left_whole_rather_than_cut() -> None:
    """Cutting a chunk would make the section's recorded ``chunk_ids`` describe text that was never
    sent. A visible provider error is recoverable; silently dropping the only chunk is not."""
    chunks = _chunks("one enormous chunk", tokens=500)
    result = _split(chunks)
    assert len(result) == 1
    assert result[0].chunk_ids == (chunks[0].id,)


# --- advisor-guided splitting ----------------------------------------------------


def test_advice_splits_on_the_proposed_lines_and_is_labelled_llm() -> None:
    chunks = _chunks("Travel overview", "Hotels\nL1-L3 $120", "Taxi\nActual cost", tokens=40)
    advisor = FakeAdvisor(
        [
            ProposedSubsection(title="Hotels", first_line="Hotels"),
            ProposedSubsection(title="Taxi", first_line="Taxi"),
        ]
    )
    result = _split(chunks, advisor=advisor)

    assert advisor.calls == 1
    assert [s.title for s in result] == [
        "4. Travel / Overview", "4. Travel / Hotels", "4. Travel / Taxi",
    ]
    assert all(s.detection_method is DetectionMethod.LLM for s in result)


def test_a_hallucinated_line_that_matches_nothing_falls_back_to_a_mechanical_split() -> None:
    """The model never supplies content — only a pointer into text it was shown. A pointer that
    resolves to nothing costs a fallback, never a corrupted section."""
    chunks = _chunks("Travel overview", "Hotels body", "Taxi body", tokens=40)
    advisor = FakeAdvisor(
        [ProposedSubsection(title="Invented", first_line="This sentence is not in the document")]
    )
    result = _split(chunks, advisor=advisor)

    assert all(s.detection_method is DetectionMethod.SEMANTIC for s in result)
    assert all(s.token_count <= MAX_TOKENS for s in result)


def test_an_advisor_that_raises_does_not_fail_the_split() -> None:
    chunks = _chunks("a", "b", "c", tokens=40)
    result = _split(chunks, advisor=FakeAdvisor([], raises=True))
    assert all(s.token_count <= MAX_TOKENS for s in result)


def test_advice_that_leaves_a_part_still_over_budget_is_rejected_wholesale() -> None:
    """Advice that does not solve the problem it was asked to solve is not partially applied — the
    budget guarantee has to hold unconditionally."""
    chunks = _chunks("Travel overview", "Hotels body", "more hotels", "yet more hotels", tokens=40)
    advisor = FakeAdvisor([ProposedSubsection(title="Hotels", first_line="Hotels body")])
    result = _split(chunks, advisor=advisor)

    assert all(s.detection_method is DetectionMethod.SEMANTIC for s in result)
    assert all(s.token_count <= MAX_TOKENS for s in result)


def test_an_empty_proposal_means_no_internal_structure_and_falls_back() -> None:
    chunks = _chunks("a", "b", "c", tokens=40)
    advisor = FakeAdvisor([])
    result = _split(chunks, advisor=advisor)
    assert all(s.detection_method is DetectionMethod.SEMANTIC for s in result)


def test_advisor_is_not_consulted_for_a_section_within_budget() -> None:
    """The common case must cost no extra model call: a document whose sections already fit never
    reaches the advisor at all."""
    advisor = FakeAdvisor([ProposedSubsection(title="Hotels", first_line="Hotels")])
    _split(_chunks("Hotels", tokens=10), advisor=advisor)
    assert advisor.calls == 0


def test_page_ranges_follow_the_chunks_each_part_actually_covers() -> None:
    chunks = _chunks("Travel overview", "Hotels body", "Taxi body", tokens=40)
    advisor = FakeAdvisor(
        [
            ProposedSubsection(title="Hotels", first_line="Hotels body"),
            ProposedSubsection(title="Taxi", first_line="Taxi body"),
        ]
    )
    result = _split(chunks, advisor=advisor)
    assert [(s.start_page, s.end_page) for s in result] == [(1, 1), (2, 2), (3, 3)]
