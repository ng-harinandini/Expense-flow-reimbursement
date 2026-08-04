"""Knowledge-source profiles: every declared source type maps to a chunking strategy.

Parser resolution is already format-based, not source-type-based (a POLICY document might be a PDF,
a DOCX, or plain text — the same :func:`app.ai.parsing.resolve_parser` handles any of them), so the
one thing that genuinely varies *by knowledge source type* is how it should be chunked: a short
reviewer note needs different treatment from a long, heading-structured travel policy.

Every :class:`~app.ai.core.enums.KnowledgeSourceType` member is covered, with ``OTHER`` — and
therefore every type not explicitly listed — as the safe fallback.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.ai.core.enums import ChunkStrategy, KnowledgeSourceType


@dataclass(frozen=True, slots=True)
class IngestionProfile:
    """How one knowledge source type should be chunked and lightly categorised."""

    source_type: KnowledgeSourceType
    chunk_strategy: ChunkStrategy
    chunk_overrides: Mapping[str, Any] = field(default_factory=dict)
    default_category: str | None = None


# Long, heading-structured reference documents: preserve citable section boundaries while still
# packing small sections together (HYBRID), rather than one chunk per section regardless of size
# (HEADING) or ignoring structure entirely (RECURSIVE).
_HYBRID_POLICY_TYPES = (
    KnowledgeSourceType.POLICY,
    KnowledgeSourceType.FINANCE_POLICY,
    KnowledgeSourceType.TRAVEL_POLICY,
    KnowledgeSourceType.MEDICAL_POLICY,
    KnowledgeSourceType.COUNTRY_POLICY,
    KnowledgeSourceType.GOVERNMENT_GUIDELINE,
)

# Structured reference documents where a reader normally jumps straight to one named section
# (a chapter, a rate table, a rule number) — HEADING's strict one-chunk-per-section citation
# precision matters more here than packing small sections together.
_HEADING_REFERENCE_TYPES = (
    KnowledgeSourceType.EMPLOYEE_HANDBOOK,
    KnowledgeSourceType.VENDOR_MANUAL,
    KnowledgeSourceType.TAX_RULE,
)

# Short, largely unstructured records: a token window is simpler and cheaper than a paragraph/
# sentence cascade that has little structure to key on anyway.
_TOKEN_RECORD_TYPES = (
    KnowledgeSourceType.REVIEWER_NOTE,
    KnowledgeSourceType.RECEIPT,
    KnowledgeSourceType.INVOICE,
)

# Free-form narrative records, chunked for readability rather than strict structure.
_RECURSIVE_NARRATIVE_TYPES = (
    KnowledgeSourceType.VENDOR_CONTRACT,
    KnowledgeSourceType.HISTORICAL_CLAIM,
    KnowledgeSourceType.HISTORICAL_DECISION,
    KnowledgeSourceType.FRAUD_INVESTIGATION,
)

_PROFILES: dict[KnowledgeSourceType, IngestionProfile] = {
    **{
        st: IngestionProfile(st, ChunkStrategy.HYBRID, default_category="policy")
        for st in _HYBRID_POLICY_TYPES
    },
    **{
        st: IngestionProfile(st, ChunkStrategy.HEADING, default_category="reference")
        for st in _HEADING_REFERENCE_TYPES
    },
    **{
        st: IngestionProfile(st, ChunkStrategy.TOKEN, default_category="record")
        for st in _TOKEN_RECORD_TYPES
    },
    **{
        st: IngestionProfile(st, ChunkStrategy.RECURSIVE, default_category="narrative")
        for st in _RECURSIVE_NARRATIVE_TYPES
    },
    # Long-form onboarding/reference material benefits from wide parent context around the precise
    # child chunks that actually get matched.
    KnowledgeSourceType.TRAINING_DOCUMENT: IngestionProfile(
        KnowledgeSourceType.TRAINING_DOCUMENT, ChunkStrategy.PARENT_CHILD,
        default_category="training",
    ),
    # Question/answer pairs read naturally as topic-coherent groups rather than fixed-size windows.
    KnowledgeSourceType.FAQ: IngestionProfile(
        KnowledgeSourceType.FAQ, ChunkStrategy.SEMANTIC, default_category="faq",
    ),
    # Safe, structure-agnostic default for anything not explicitly profiled above.
    KnowledgeSourceType.OTHER: IngestionProfile(
        KnowledgeSourceType.OTHER, ChunkStrategy.RECURSIVE, default_category=None,
    ),
}


def profile_for(source_type: KnowledgeSourceType) -> IngestionProfile:
    """The ingestion profile for ``source_type``, falling back to ``OTHER``'s profile.

    Every ``KnowledgeSourceType`` member is covered explicitly above; the fallback exists for
    forward-compatibility with a member added to the enum after this module, not because a gap is
    expected today (a test asserts every current member has its own explicit entry).
    """
    return _PROFILES.get(source_type, _PROFILES[KnowledgeSourceType.OTHER])


__all__ = ["IngestionProfile", "profile_for"]
