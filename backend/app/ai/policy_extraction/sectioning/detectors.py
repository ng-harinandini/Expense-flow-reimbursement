"""The deterministic heading detectors, in descending order of confidence.

**Why heuristics at all, when there is an LLM available.** A PDF's chunks arrive here with no
structural metadata whatsoever: ``PdfParser`` emits one section per *page* with only a page number
(``heading``/``heading_path`` are populated by the HTML/Markdown/Word parsers, never the PDF one),
and ``pypdf`` exposes no font size or weight to infer a heading from. So detection has only the text
itself — but the text is usually enough, and asking a model to segment every document would cost a
call per document to answer a question a regex answers correctly on a numbered policy.

Each detector scores its findings on the same 0..1 scale so the pipeline can rank across tiers
against one configured threshold. Nothing here calls a model or touches the database.

The failure mode these are written against is **over-detection, not under-detection**. A missed
boundary merges two sections, which costs a larger prompt; a spurious boundary can cut a table
in half, so a rule's amount and the grade tier it applies to end up in different LLM calls with
neither able to read the other. Every threshold below is therefore set to prefer silence over a
guess, and the pipeline's fallback (no boundaries at all -> one section for the whole document) is a
correct outcome rather than a degraded one.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Optional, Protocol, Sequence

from app.ai.policy_extraction.normalization import normalize_dashes
from app.ai.policy_extraction.sectioning.types import DetectionMethod, HeadingCandidate

#: ``4.1 Meals``, ``4.6 Communications & IT Equipment``, ``7 Fraud``. Anchored at both ends: a line
#: that merely *starts* with a number ("40 per day is the cap") is not a heading, and requiring the
#: title to begin with a capital rejects a numbered list item mid-sentence.
_NUMBERED_HEADING_RE = re.compile(
    r"^(\d{1,2}(?:\.\d{1,2}){0,2})\.?\s+([A-Z][\w &/,'\-()]{2,80})$"
)

#: A chunk containing at least this many numbered-heading lines is a table of contents, not a
#: boundary. Policy PDFs open with one, and every entry in it looks exactly like the real heading it
#: points at — without this guard a 12-section document detects 12 spurious sections on page 1 and
#: then finds the real ones again later, producing 24 sections of which half are empty.
_TOC_DENSITY_THRESHOLD = 3

#: A heading is short. 60 characters is comfortably above the longest real section title in a policy
#: document ("Communications & IT Equipment" is 29) and well below a sentence of prose.
_MAX_HEADING_CHARS = 60
_MIN_HEADING_CHARS = 3

#: Sentence-ending punctuation never appears in a heading, and its presence is the single most
#: reliable signal that a short line is prose (or a running footer) rather than a title.
_PROSE_PUNCTUATION = frozenset(".;:!?")

#: Exempt from the title-case test: "Travel and Entertainment" is a heading even though "and" is
#: lowercase.
_SMALL_WORDS = frozenset({"and", "or", "of", "the", "for", "to", "in", "a", "an", "&"})

_NUMBERED_CONFIDENCE = Decimal("0.95")
_HEADING_KNOWN_CATEGORY_CONFIDENCE = Decimal("0.75")
_HEADING_CONFIDENCE = Decimal("0.60")
_SEMANTIC_CONFIDENCE = Decimal("0.45")


class HeadingDetectorProtocol(Protocol):
    """One tier of detection over one chunk's text.

    Returning ``None`` is the normal case — most chunks are body text. A detector is called once per
    chunk and must be pure: the pipeline runs all tiers over the same input and compares their
    scores, which is only meaningful if no detector's answer depends on what it saw previously.
    """

    method: DetectionMethod

    def detect(
        self, text: str, *, chunk_index: int, known_categories: Sequence[str]
    ) -> Optional[HeadingCandidate]: ...


class NumberedHeadingDetector:
    """Tier 1: an explicitly numbered heading. The strongest signal available in plain text.

    A document that numbers its sections has told us where they start, so this is treated as near
    certain — with the one exception of a table-of-contents chunk, which is dense with lines that
    are individually indistinguishable from real headings and is rejected wholesale.
    """

    method = DetectionMethod.NUMBERING

    def detect(
        self, text: str, *, chunk_index: int, known_categories: Sequence[str]
    ) -> Optional[HeadingCandidate]:
        lines = _lines(text)
        if not lines:
            return None
        if _count_numbered_lines(lines) >= _TOC_DENSITY_THRESHOLD:
            return None

        match = _NUMBERED_HEADING_RE.match(lines[0])
        if match is None:
            return None
        number, title = match.group(1), match.group(2).strip()
        return HeadingCandidate(
            chunk_index=chunk_index,
            title=f"{number} {title}",
            confidence=_NUMBERED_CONFIDENCE,
            method=self.method,
        )


class HeadingDetector:
    """Tier 2: an unnumbered standalone heading, scored higher when it names a known category.

    ``Meals`` or ``GROUND TRANSPORTATION`` alone on the first line of a chunk is a heading in every
    policy document; the risk is a repeated running header or footer carrying the same words. Two
    cheap guards handle that: the line must look like a title (short, unpunctuated, title-case or
    all-caps) *and* the chunk must contain more than the heading itself — a chunk that is **only** a
    short line is far more likely to be page furniture than a section that genuinely starts with a
    heading and no body.
    """

    method = DetectionMethod.HEADING

    def detect(
        self, text: str, *, chunk_index: int, known_categories: Sequence[str]
    ) -> Optional[HeadingCandidate]:
        lines = _lines(text)
        if len(lines) < 2 or not _looks_like_a_heading(lines[0]):
            return None

        title = lines[0].strip()
        confidence = (
            _HEADING_KNOWN_CATEGORY_CONFIDENCE
            if _matches_known_category(title, known_categories)
            else _HEADING_CONFIDENCE
        )
        return HeadingCandidate(
            chunk_index=chunk_index, title=title, confidence=confidence, method=self.method,
        )


class SemanticHeadingDetector:
    """Tier 3: a weak structural guess, for a document that numbers nothing and titles nothing.

    Scored *below* the default threshold on purpose. On its own it never creates a boundary; it
    exists so an operator who lowers ``AI_EXTRACTION_MIN_HEURISTIC_CONFIDENCE`` for a document known
    to be loosely structured gets sensible boundaries rather than none, and so the reason a section
    was cut where it was is recorded as ``SEMANTIC`` rather than silently attributed to a stronger
    tier.

    "Semantic" here means *structural inference from shape*, not an embedding — extraction never
    touches a vector. The signals are: a short line, followed by a blank line, followed by content
    that reads like a table or a paragraph.
    """

    method = DetectionMethod.SEMANTIC

    def detect(
        self, text: str, *, chunk_index: int, known_categories: Sequence[str]
    ) -> Optional[HeadingCandidate]:
        raw_lines = text.splitlines()
        lines = _lines(text)
        if len(lines) < 2:
            return None
        first = lines[0]
        if len(first) > _MAX_HEADING_CHARS or len(first) < _MIN_HEADING_CHARS:
            return None
        if _PROSE_PUNCTUATION & set(first):
            return None
        if not _followed_by_blank_line(raw_lines):
            return None
        return HeadingCandidate(
            chunk_index=chunk_index,
            title=first.strip(),
            confidence=_SEMANTIC_CONFIDENCE,
            method=self.method,
        )


#: Tier order is documentation only — the pipeline ranks by confidence, not by position — but it
#: keeps the "cheap and certain first" reading order explicit for anyone adding a detector.
DEFAULT_DETECTORS: tuple[HeadingDetectorProtocol, ...] = (
    NumberedHeadingDetector(),
    HeadingDetector(),
    SemanticHeadingDetector(),
)


# --- shared predicates --------------------------------------------------------


def _lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _count_numbered_lines(lines: Sequence[str]) -> int:
    return sum(1 for line in lines if _NUMBERED_HEADING_RE.match(line))


def _looks_like_a_heading(line: str) -> bool:
    """Short, unpunctuated, and cased like a title rather than a sentence."""
    stripped = line.strip()
    if not _MIN_HEADING_CHARS <= len(stripped) <= _MAX_HEADING_CHARS:
        return False
    if _PROSE_PUNCTUATION & set(stripped):
        return False
    words = stripped.split()
    if not words or len(words) > 8:
        return False
    if stripped.isupper():
        return True
    # Title case: every word that carries a letter starts with a capital.
    significant = [w for w in words if any(ch.isalpha() for ch in w)]
    return bool(significant) and all(
        word[0].isupper() or word.lower() in _SMALL_WORDS for word in significant
    )


def _matches_known_category(title: str, known_categories: Sequence[str]) -> bool:
    """True when the title names one of the configured expense categories.

    Uses the same case/separator-insensitive comparison as
    :func:`app.ai.policy_extraction.normalization.normalize_category`, so a heading the extractor
    would later map onto a category is also recognised as a boundary here — the two must not
    disagree about what counts as the same name.
    """
    key = _match_key(title)
    return any(key == _match_key(name) for name in known_categories)


def _match_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_dashes(str(value)).lower())


def _followed_by_blank_line(raw_lines: Sequence[str]) -> bool:
    """Whether the first non-blank line is followed by a blank one — a visual heading break."""
    seen_content = False
    for line in raw_lines:
        if line.strip():
            if seen_content:
                return False
            seen_content = True
            continue
        if seen_content:
            return True
    return False


__all__ = [
    "DEFAULT_DETECTORS",
    "HeadingDetector",
    "HeadingDetectorProtocol",
    "NumberedHeadingDetector",
    "SemanticHeadingDetector",
]
