"""Cleanup applied after parsing, before anything else touches the text.

Three passes, each targeting an artifact a specific class of source produces and none of the others
do:

1. **Bare page-number lines** (a lone ``"12"`` or ``"- 12 -"`` on its own line) — the one PDF/print
   artifact that is *never* the same text twice, so :mod:`app.ai.parsing.checksum`'s
   verbatim-repetition detector cannot catch it; caught here by shape instead of content.
2. **Repeated boilerplate** (headers/footers appearing near-verbatim across the document's own
   sections) — :mod:`app.ai.parsing.checksum` finds it, this module removes it.
3. **General text cleanup** — :func:`~app.ai.core.text.normalize_text` (NFKC folding, de-hyphenation
   across line breaks, whitespace collapse), applied last so the first two passes still see the
   original line boundaries they key on.

Run in this order because the page-number and boilerplate passes work on structured line boundaries
that ``normalize_text``'s whitespace collapse would otherwise destroy before they get a chance to
match.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Sequence

from app.ai.core.text import normalize_text
from app.ai.core.types import DocumentSection
from app.ai.parsing.checksum import repeated_lines, strip_boilerplate

# "12", "- 12 -", "Page 12", "12 / 40" alone on a line. Anchored to the whole stripped line so a
# genuine sentence that happens to contain a number is never touched.
_BARE_PAGE_NUMBER = re.compile(
    r"^[\-–—\s]*(?:page\s+)?\d{1,4}(?:\s*[/\-of]+\s*\d{1,4})?[\-–—\s]*$", re.IGNORECASE
)


def strip_page_numbers(text: str) -> str:
    """Remove lines that are only a page number/marker, keeping everything else untouched."""
    kept = [line for line in text.split("\n") if not _BARE_PAGE_NUMBER.match(line.strip())]
    return "\n".join(kept)


def clean_text(text: str, *, boilerplate: frozenset[str] = frozenset()) -> str:
    """The full cleanup pipeline: page numbers, then boilerplate, then general normalization."""
    text = strip_page_numbers(text)
    text = strip_boilerplate(text, boilerplate)
    return normalize_text(text)


def clean_sections(sections: Sequence[DocumentSection]) -> tuple[DocumentSection, ...]:
    """Apply the same cleanup to every section, detecting boilerplate across all of them first.

    Boilerplate detection needs the *whole* document's sections at once (a header is only
    recognisable as boilerplate by comparing it against the other sections it repeats across), so
    this is the entry point parsers' callers should use rather than calling :func:`clean_text` on
    each section in isolation.
    """
    boilerplate = repeated_lines(sections)
    return tuple(
        section
        for raw_section in sections
        for section in [_clean_one(raw_section, boilerplate)]
        if section.text
    )


def _clean_one(section: DocumentSection, boilerplate: frozenset[str]) -> DocumentSection:
    return replace(section, text=clean_text(section.text, boilerplate=boilerplate))


__all__ = ["clean_sections", "clean_text", "strip_page_numbers"]
