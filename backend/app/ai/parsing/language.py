"""Document-level language detection, built on the per-text heuristic in ``app.ai.core.text``.

**Detected per section and combined by a length-weighted vote, not by detecting once over the whole
concatenated text.** The difference matters for a genuinely mixed document — an English travel
policy with one clause quoted in French, or a multi-country handbook with per-country appendices in
different languages. Detecting once over the concatenation would let whichever language has more raw
characters silently win with no visibility into the fact that the document was mixed at all; a
weighted vote over sections at least means the majority language reflects how much of the document
is actually written in it, and :func:`section_languages` exposes the breakdown for anything upstream
that cares (routing, or a future per-section language tag on the chunk metadata).

This module has never itself judged a claim and never will — Task 2's constraint that this stays
"heuristic/rule-based, replaceable independently" is the same reasoning as everywhere else in the
platform: language is used for routing and filtering, never as evidence a decision engine reads.
"""

from __future__ import annotations

from collections import Counter
from typing import Optional, Sequence

from app.ai.core.text import detect_language
from app.ai.core.types import DocumentSection

# Below this many characters a section's own detection is too unreliable to contribute a vote —
# matches app.ai.core.text.detect_language's own minimum, stated here so the threshold this module
# applies is visible without reading the function it delegates to.
_MIN_SECTION_CHARS = 12


def section_languages(sections: Sequence[DocumentSection]) -> dict[int, Optional[str]]:
    """Best-effort language per section, keyed by its position in ``sections``."""
    return {
        index: detect_language(section.text)
        for index, section in enumerate(sections)
        if len(section.text.strip()) >= _MIN_SECTION_CHARS
    }


def detect_document_language(
    sections: Sequence[DocumentSection], *, default: Optional[str] = None
) -> Optional[str]:
    """The document's dominant language: a length-weighted vote across its sections.

    Falls back to detecting over the full concatenated text when the document has no sections long
    enough to vote individually (a short, single-section document) — never silently returns
    ``default`` just because structure happened to be absent.
    """
    weights: Counter[str] = Counter()
    for section in sections:
        text = section.text.strip()
        if len(text) < _MIN_SECTION_CHARS:
            continue
        language = detect_language(text)
        if language:
            weights[language] += len(text)

    if weights:
        return weights.most_common(1)[0][0]

    whole = "\n".join(s.text for s in sections)
    return detect_language(whole, default=default)


__all__ = ["detect_document_language", "section_languages"]
