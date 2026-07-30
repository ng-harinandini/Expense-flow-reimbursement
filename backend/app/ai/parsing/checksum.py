"""Document identity, and finding the boilerplate repeated across a document's own pages.

The identity half is a thin wrapper: :func:`checksum_for` exists so every call site names the
concept ("the document's checksum") rather than reaching for :mod:`app.ai.core.ids` directly, and so
this is the one place that would change if the platform ever needed a second, non-cryptographic
digest for a different purpose.

The repetition half is the reason this module exists as more than a wrapper. A page header
("ExpenseFlow Travel Policy — Confidential") or footer ("Page 3 of 40") appears on every page of a
PDF or every sheet of a workbook, and if left in, it is indexed on *every* chunk of the document —
diluting lexical search (the header's words appear in every match) and wasting a slice of every
chunk's token budget on text that carries no information. Detecting it requires nothing semantic:
text repeated near-verbatim across enough of a document's own structural units (pages, sheets,
sections) *is* boilerplate, by definition, regardless of what language or format it is in.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Sequence

from app.ai.core.ids import content_checksum
from app.ai.core.text import normalize_for_matching
from app.ai.core.types import DocumentSection, RawDocument

# A line repeated across at least this fraction of a document's sections is boilerplate rather than
# coincidence — three near-identical section headers in a 40-page policy is not a coincidence, but
# neither is requiring *all* of them, since a header can legitimately be absent from the first page.
REPETITION_THRESHOLD = 0.6

# Below this many sections there is not enough structure to distinguish "repeated on every page"
# from "the document happens to say this twice" — boilerplate detection stays off rather than
# guessing on a three-paragraph memo.
MIN_SECTIONS_FOR_DETECTION = 4

# A line longer than this is treated as body content even if it repeats — a genuinely repeated
# *paragraph* of substantive text is unusual enough that stripping it automatically would risk
# deleting real content a boilerplate header never contains this much of.
MAX_BOILERPLATE_LINE_CHARS = 200


def checksum_for(document: RawDocument) -> str:
    """The document's identity: SHA-256 of its original, untouched bytes."""
    return content_checksum(document.content)


def repeated_lines(sections: Sequence[DocumentSection]) -> frozenset[str]:
    """Lines appearing near-verbatim across a large enough share of the document's own sections.

    Compared after :func:`~app.ai.core.text.normalize_for_matching` (casefolded, accent- and
    punctuation-stripped) so "Page 3 of 40" and "PAGE 12 OF 40" are recognised as the same
    boilerplate line despite differing in the one token that makes each occurrence unique.
    """
    if len(sections) < MIN_SECTIONS_FOR_DETECTION:
        return frozenset()

    occurrences: Counter[str] = Counter()
    originals: dict[str, str] = {}
    for section in sections:
        seen_this_section: set[str] = set()
        for line in section.text.split("\n"):
            stripped = line.strip()
            if not stripped or len(stripped) > MAX_BOILERPLATE_LINE_CHARS:
                continue
            key = normalize_for_matching(stripped)
            if not key or key in seen_this_section:
                continue
            seen_this_section.add(key)
            occurrences[key] += 1
            originals.setdefault(key, stripped)

    # ceil, not int(): a plain truncation quietly asks for a *higher* share than
    # REPETITION_THRESHOLD names (e.g. int(5 * 0.6) == 3, which is only 3/5 == 60% by luck; at 4
    # sections int(4 * 0.6) == 2, i.e. 50%, already under the stated 60%). ceil is the smallest
    # count that actually clears the threshold. No further floor is applied here — the
    # MIN_SECTIONS_FOR_DETECTION guard above already ensures there are enough sections for this
    # ratio to mean something; reapplying it as a second, absolute floor is what silently forced
    # 100% agreement on a 4-section document regardless of what REPETITION_THRESHOLD said.
    threshold = math.ceil(len(sections) * REPETITION_THRESHOLD)
    return frozenset(
        originals[key] for key, count in occurrences.items() if count >= threshold
    )


def strip_boilerplate(text: str, boilerplate: frozenset[str]) -> str:
    """Remove every line matching a detected boilerplate line, preserving the rest verbatim."""
    if not boilerplate:
        return text
    normalized_boilerplate = {normalize_for_matching(b) for b in boilerplate}
    kept = [
        line for line in text.split("\n")
        if normalize_for_matching(line.strip()) not in normalized_boilerplate
    ]
    return "\n".join(kept)


__all__ = [
    "MAX_BOILERPLATE_LINE_CHARS",
    "MIN_SECTIONS_FOR_DETECTION",
    "REPETITION_THRESHOLD",
    "checksum_for",
    "repeated_lines",
    "strip_boilerplate",
]
