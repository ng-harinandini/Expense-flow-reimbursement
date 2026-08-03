"""The values section detection produces, and the vocabulary it labels them with.

A :class:`DetectedSection` is deliberately *not* a database row: detection runs over chunks in
memory, and only the orchestrator decides which of its fields get persisted. Keeping the dataclass
free of ORM concerns is what lets every detector and the splitter be tested as pure functions with
no session.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from app.models.enums import _WireEnum


class DetectionMethod(_WireEnum):
    """How a section boundary was found, in descending order of trustworthiness.

    Persisted on ``policy_document_sections.detection_method`` as ``VARCHAR``: new detectors are a
    routine future addition (a layout-aware PDF parser would add one) and must not need a migration.

    ``LLM`` is the only member that costs a model call, and is reached only where the deterministic
    tiers cannot answer — either no heading was found in an oversized section, or a section is too
    large to extract in one prompt and has to be sub-divided.
    """

    NUMBERING = "NUMBERING"
    HEADING = "HEADING"
    SEMANTIC = "SEMANTIC"
    LLM = "LLM"


class SectionStatus(_WireEnum):
    """Per-section extraction lifecycle.

    Operational state, not governance: it records whether *this* section's LLM call succeeded, so a
    reviewer can see that 11 of 12 sections extracted and one failed. ``VARCHAR`` rather than a
    native enum for that reason — unlike :class:`~app.ai.core.enums.ProposalStatus`, nothing about
    approval depends on it, and a future ``RETRYING``/``SKIPPED`` must not need a migration.
    """

    PENDING = "PENDING"
    EXTRACTED = "EXTRACTED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class HeadingCandidate:
    """One detector's claim that a chunk begins a new section.

    Confidence is what makes the tiers composable: every detector scores its own findings on the
    same 0..1 scale, so the pipeline can rank candidates from different tiers against each other
    and against a single configured threshold, instead of hard-coding a precedence order that would
    have to change every time a detector is added.
    """

    chunk_index: int
    title: str
    confidence: Decimal
    method: DetectionMethod


@dataclass(frozen=True, slots=True)
class DetectedSection:
    """One logical policy section: a contiguous run of chunks under one heading.

    A section is *not* a page. It routinely spans several (a lodging table starting on p3 and
    continuing on p4 is one section), and several can share a page (three short subsections on p7).
    That is the whole point of sectioning over the page grouping it replaces: the unit sent to the
    model is now a unit of *meaning*, so a rule and the heading that gives it context always travel
    together.
    """

    index: int
    title: str
    start_page: int
    end_page: int
    chunk_ids: tuple[uuid.UUID, ...]
    text: str
    detection_method: DetectionMethod
    detection_confidence: Decimal
    table_detected: bool = False
    char_count: int = 0
    token_count: int = 0
    #: Trailing text from the previous section, supplied to the model as read-only context.
    #: A limits table whose header ends one section and whose rows begin the next would otherwise be
    #: split across two independent calls with neither able to interpret its half.
    previous_context: str = ""
    chunk_count: int = field(default=0)

    @property
    def page_range(self) -> str:
        """Human label for a log line or a reviewer's citation, e.g. ``p3`` or ``p3-5``."""
        if self.start_page == self.end_page:
            return f"p{self.start_page}"
        return f"p{self.start_page}-{self.end_page}"


__all__ = [
    "DetectedSection",
    "DetectionMethod",
    "HeadingCandidate",
    "SectionStatus",
]
