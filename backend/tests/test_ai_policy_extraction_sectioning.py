"""Section detection: the detectors in isolation, and the pipeline that ranks and applies them.

Pure functions over fake chunk objects — no database, no provider, no session. That is the point of
keeping detection free of ORM concerns: the logic most likely to be wrong about a real document is
also the logic that can be exercised fastest.

Two properties carry most of the weight here, and both are about *not* splitting:

* A table of contents lists lines indistinguishable from the headings it points at. Without a guard,
  a 12-section document detects 12 spurious sections on page 1 and then the 12 real ones later.
* A document with no detectable structure must collapse to exactly one section, which reproduces the
  single-call behaviour sectioning replaced. That makes "no headings found" a correct outcome rather
  than a degraded one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Sequence

from app.ai.policy_extraction.sectioning import (
    PREAMBLE_TITLE,
    WHOLE_DOCUMENT_TITLE,
    DetectionMethod,
    HeadingDetector,
    NumberedHeadingDetector,
    SectionDetectionPipeline,
    SemanticHeadingDetector,
)

KNOWN_CATEGORIES = ("Meals", "Ground Transport", "Flights", "Lodging", "Client Entertainment")


@dataclass
class FakeChunk:
    """The three fields detection reads off a ``KnowledgeChunk``."""

    content: str
    page_number: int
    chunk_index: int
    id: uuid.UUID = None  # type: ignore[assignment]
    content_tokens: Optional[int] = None

    def __post_init__(self) -> None:
        if self.id is None:
            self.id = uuid.uuid4()


def chunks(*texts: str, per_page: int = 1) -> list[FakeChunk]:
    """One chunk per text, pages advancing every ``per_page`` chunks."""
    return [
        FakeChunk(content=text, page_number=(index // per_page) + 1, chunk_index=index)
        for index, text in enumerate(texts)
    ]


# --- tier 1: numbered headings --------------------------------------------------


def test_numbered_heading_is_detected_with_its_number() -> None:
    detector = NumberedHeadingDetector()
    found = detector.detect(
        "4.1 Meals\nGrade Max amount\nAll $40 / day", chunk_index=1, known_categories=(),
    )
    assert found is not None
    assert found.title == "4.1 Meals"
    assert found.method is DetectionMethod.NUMBERING
    assert found.confidence >= Decimal("0.9")


def test_numbered_heading_matches_an_ampersand_title() -> None:
    """The real document's §4.6 is 'Communications & IT Equipment' — punctuation in a title must
    not stop it being recognised."""
    found = NumberedHeadingDetector().detect(
        "4.6 Communications & IT Equipment\nMobile phone reimbursement $50 / month",
        chunk_index=2, known_categories=(),
    )
    assert found is not None
    assert found.title == "4.6 Communications & IT Equipment"


def test_a_number_mid_sentence_is_not_a_heading() -> None:
    found = NumberedHeadingDetector().detect(
        "40 per day is the cap for meals.", chunk_index=1, known_categories=(),
    )
    assert found is None


def test_a_table_of_contents_chunk_yields_no_boundary() -> None:
    """Every line of a TOC looks exactly like the heading it points at. Splitting on one would
    produce a fan of empty sections on page 1 and duplicate every real section later."""
    toc = "4.1 Meals\n4.2 Ground Transport\n4.3 Flights\n4.4 Lodging\n4.5 Client Entertainment"
    assert NumberedHeadingDetector().detect(toc, chunk_index=1, known_categories=()) is None


# --- tier 2: standalone headings ------------------------------------------------


def test_standalone_known_category_scores_higher_than_an_unknown_title() -> None:
    detector = HeadingDetector()
    known = detector.detect("Meals\nAll staff $40 per day.", chunk_index=1,
                            known_categories=KNOWN_CATEGORIES)
    unknown = detector.detect("Relocation\nPer signed agreement.", chunk_index=1,
                              known_categories=KNOWN_CATEGORIES)
    assert known is not None and unknown is not None
    assert known.confidence > unknown.confidence


def test_a_sentence_is_never_a_standalone_heading() -> None:
    found = HeadingDetector().detect(
        "Meals are reimbursed at $40 per day.\nReceipts required.",
        chunk_index=1, known_categories=KNOWN_CATEGORIES,
    )
    assert found is None


def test_a_lone_short_line_with_no_body_is_not_a_heading() -> None:
    """A chunk that is *only* a short line is far more likely to be a running header or footer than
    a section that begins with a heading and no content."""
    found = HeadingDetector().detect("Meals", chunk_index=1, known_categories=KNOWN_CATEGORIES)
    assert found is None


# --- tier 3: semantic ------------------------------------------------------------


def test_semantic_detector_scores_below_the_default_threshold() -> None:
    """It exists to label a weak boundary and to serve an operator who lowers the threshold — never
    to create one on its own at the default setting."""
    found = SemanticHeadingDetector().detect(
        "Overnight stays\n\nBook through the corporate travel desk where possible",
        chunk_index=1, known_categories=(),
    )
    assert found is not None
    assert found.confidence < Decimal("0.5")


# --- the pipeline ----------------------------------------------------------------


def test_numbered_headings_become_one_section_each() -> None:
    detected = SectionDetectionPipeline().detect(
        chunks(
            "4.1 Meals\nAll $40 / day",
            "4.3 Flights\nL1-L4 Economy",
            "4.4 Lodging\nL1-L3 $120 / night",
        ),
        known_categories=KNOWN_CATEGORIES,
    )
    assert [s.title for s in detected] == ["4.1 Meals", "4.3 Flights", "4.4 Lodging"]
    assert all(s.detection_method is DetectionMethod.NUMBERING for s in detected)


def test_a_section_spans_every_page_its_chunks_cover() -> None:
    """The reason sections replaced page grouping: a lodging table starting on one page and
    continuing on the next is one rule set, not two."""
    detected = SectionDetectionPipeline().detect(
        chunks(
            "4.4 Lodging\nGrade Max amount",
            "L1-L3 $120 / night",
            "L4+ $250 / night",
            "4.5 Client Entertainment\nAll $75 / person",
        ),
        known_categories=KNOWN_CATEGORIES,
    )
    lodging = detected[0]
    assert lodging.title == "4.4 Lodging"
    assert (lodging.start_page, lodging.end_page) == (1, 3)
    assert lodging.chunk_count == 3


def test_content_before_the_first_heading_becomes_an_introduction() -> None:
    """Front matter states the effective date, scope and currency — dropping it would discard
    exactly the document-level facts the extractor is asked to report."""
    detected = SectionDetectionPipeline().detect(
        chunks(
            "This policy governs reimbursement of business expenses for all employees.",
            "4.1 Meals\nAll $40 / day",
        ),
        known_categories=KNOWN_CATEGORIES,
    )
    assert [s.title for s in detected] == [PREAMBLE_TITLE, "4.1 Meals"]


def test_a_leading_heading_titles_its_own_section_rather_than_being_called_a_preamble() -> None:
    detected = SectionDetectionPipeline().detect(
        chunks("4.1 Meals\nAll $40 / day", "4.2 Ground Transport\nAll $60 / day"),
        known_categories=KNOWN_CATEGORIES,
    )
    assert detected[0].title == "4.1 Meals"


def test_a_document_with_no_headings_collapses_to_one_section() -> None:
    """The safety property: with nothing detected, extraction makes exactly one call with the whole
    document — precisely the behaviour sectioning replaced. Adding sectioning cannot regress it."""
    detected = SectionDetectionPipeline().detect(
        chunks(
            "Employees may claim reasonable business expenses incurred while travelling.",
            "Claims should be submitted within thirty days of the expense being incurred.",
        ),
        known_categories=KNOWN_CATEGORIES,
    )
    assert len(detected) == 1
    assert detected[0].title == WHOLE_DOCUMENT_TITLE
    assert detected[0].chunk_count == 2


def test_every_chunk_lands_in_exactly_one_section() -> None:
    """No chunk may be dropped or double-counted: a dropped chunk is silently missing rules, and a
    duplicated one is the same rule extracted twice by two independent calls."""
    source = chunks(
        "Scope and purpose of this policy.",
        "4.1 Meals\nAll $40 / day",
        "Receipts are required above $25.",
        "4.4 Lodging\nL1-L3 $120 / night",
    )
    detected = SectionDetectionPipeline().detect(source, known_categories=KNOWN_CATEGORIES)

    assigned = [cid for section in detected for cid in section.chunk_ids]
    assert assigned == [c.id for c in source]


def test_a_section_carries_the_previous_sections_tail_as_context() -> None:
    """A limits table can end one section and continue into the next. Without the tail, the second
    call sees a headerless table and cannot tell the cap column from the threshold column."""
    detected = SectionDetectionPipeline().detect(
        chunks("4.3 Flights\nGrade Class Advance booking", "4.4 Lodging\nL1-L3 $120 / night"),
        known_categories=KNOWN_CATEGORIES,
    )
    assert detected[0].previous_context == ""
    assert "4.3 Flights" in detected[1].previous_context


def test_section_text_carries_page_markers_so_rules_can_cite_a_page() -> None:
    detected = SectionDetectionPipeline().detect(
        chunks("4.4 Lodging\nGrade Max amount", "L1-L3 $120 / night"),
        known_categories=KNOWN_CATEGORIES,
    )
    assert "[Page 1]" in detected[0].text
    assert "[Page 2]" in detected[0].text


def test_a_malformed_inline_pipe_table_is_flagged() -> None:
    detected = SectionDetectionPipeline().detect(
        chunks(
            "4.6 Communications\n| Category | Grade | Max amount | Receipt | "
            "|---|---|---|---| | Mobile phone | All | $50 / month | $0 |",
        ),
        known_categories=KNOWN_CATEGORIES,
    )
    assert detected[0].table_detected is True


def test_no_chunks_yields_no_sections() -> None:
    assert SectionDetectionPipeline().detect([], known_categories=KNOWN_CATEGORIES) == []


def test_raising_the_threshold_suppresses_weaker_boundaries() -> None:
    """Confidence is a real dial, not decoration: an operator who has seen a document over-split can
    tighten it, and only the strongest tier survives."""
    source = chunks("Meals\nAll staff $40 per day.", "Lodging\nL1-L3 $120 per night.")
    loose = SectionDetectionPipeline(min_confidence=Decimal("0.5")).detect(
        source, known_categories=KNOWN_CATEGORIES
    )
    strict = SectionDetectionPipeline(min_confidence=Decimal("0.9")).detect(
        source, known_categories=KNOWN_CATEGORIES
    )
    assert len(loose) == 2
    assert len(strict) == 1


def _titles(sections: Sequence[object]) -> list[str]:
    return [s.title for s in sections]  # type: ignore[attr-defined]
