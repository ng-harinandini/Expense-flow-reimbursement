"""Deterministic quality scoring, bounded ``[0, 100]``.

This scores the *extraction*, not the document's content — it answers "how much do I trust that this
text is what the source actually says," not "is this a good policy." A perfectly well-written policy
that Textract garbled into symbol noise must score low; a terse, badly-written one that extracted
cleanly must not. That framing is what keeps every factor below deterministic and free of any
judgment about what the text says: character composition, word-length sanity, structural presence
and language-detection success are all signals about extraction fidelity, not content quality.

``AISettings.MIN_QUALITY_SCORE`` is compared against this score by whatever ingests the document
(M8) to decide whether to warn — a low score is what makes a badly-OCR'd scan visible to a reviewer
instead of silently entering the corpus as confidently as a clean one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.ai.core.text import detect_language

_WORD = re.compile(r"\S+")
_ALNUM_OR_SPACE = re.compile(r"[^\W\d_]|\d|\s", re.UNICODE)

# Weights sum to 1.0. Length and character-composition carry the most weight because they are the
# most direct evidence of a failed or garbled extraction; structure and language detection are
# corroborating signals, not primary ones — plenty of genuinely clean single-section documents in an
# undetectable-by-this-heuristic language exist and must not be penalised heavily for it.
_WEIGHT_LENGTH = 0.30
_WEIGHT_ALNUM_RATIO = 0.30
_WEIGHT_WORD_SHAPE = 0.20
_WEIGHT_STRUCTURE = 0.10
_WEIGHT_LANGUAGE = 0.10

# A raw text shorter than this is treated as a failed extraction regardless of how clean the few
# characters that did come out look — there is not enough of it to be usable evidence of anything.
_MIN_USABLE_CHARS = 80
# Length score saturates here: a document does not get "better" for being longer once it is clearly
# a real, substantial document.
_SATURATION_CHARS = 2000

# A real word (in the scripts this platform mostly sees) is rarely useful evidence at either
# extreme: a swarm of 1-character "words" usually means OCR broke on individual glyphs, and an
# average far above this usually means whitespace was lost, merging words together.
_PLAUSIBLE_WORD_LEN_LOW = 2.0
_PLAUSIBLE_WORD_LEN_HIGH = 10.0


@dataclass(frozen=True, slots=True)
class QualityBreakdown:
    """The score plus each factor that fed it, for a governance view of *why* a document scored
    the way it did rather than only the final number."""

    score: float
    length_score: float
    alnum_ratio_score: float
    word_shape_score: float
    structure_score: float
    language_score: float


def score(text: str, *, section_count: int = 1) -> float:
    """The bounded ``[0, 100]`` score alone. See :func:`score_with_breakdown` for the detail."""
    return score_with_breakdown(text, section_count=section_count).score


def score_with_breakdown(text: str, *, section_count: int = 1) -> QualityBreakdown:
    stripped = text.strip()
    if not stripped:
        return QualityBreakdown(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    length_score = _length_score(stripped)
    alnum_score = _alnum_ratio_score(stripped)
    word_score = _word_shape_score(stripped)
    structure_score = 1.0 if section_count > 1 else 0.5
    language_score = 1.0 if detect_language(stripped) else 0.3

    combined = (
        _WEIGHT_LENGTH * length_score
        + _WEIGHT_ALNUM_RATIO * alnum_score
        + _WEIGHT_WORD_SHAPE * word_score
        + _WEIGHT_STRUCTURE * structure_score
        + _WEIGHT_LANGUAGE * language_score
    )
    final = max(0.0, min(100.0, combined * 100))
    return QualityBreakdown(
        score=final,
        length_score=length_score,
        alnum_ratio_score=alnum_score,
        word_shape_score=word_score,
        structure_score=structure_score,
        language_score=language_score,
    )


def _length_score(text: str) -> float:
    if len(text) < _MIN_USABLE_CHARS:
        return len(text) / _MIN_USABLE_CHARS * 0.3  # never fully zero-credit for *some* text
    span = _SATURATION_CHARS - _MIN_USABLE_CHARS
    return min(1.0, 0.3 + 0.7 * (len(text) - _MIN_USABLE_CHARS) / span)


def _alnum_ratio_score(text: str) -> float:
    matches = len(_ALNUM_OR_SPACE.findall(text))
    return matches / len(text)


def _word_shape_score(text: str) -> float:
    words = _WORD.findall(text)
    if not words:
        return 0.0
    average = sum(len(w) for w in words) / len(words)
    if _PLAUSIBLE_WORD_LEN_LOW <= average <= _PLAUSIBLE_WORD_LEN_HIGH:
        return 1.0
    # Linear falloff outside the plausible band, floored at 0 — a document whose "words" average
    # 40 characters (whitespace almost certainly lost) is not a borderline case.
    distance = (
        _PLAUSIBLE_WORD_LEN_LOW - average
        if average < _PLAUSIBLE_WORD_LEN_LOW
        else average - _PLAUSIBLE_WORD_LEN_HIGH
    )
    return max(0.0, 1.0 - distance / _PLAUSIBLE_WORD_LEN_HIGH)


__all__ = ["QualityBreakdown", "score", "score_with_breakdown"]
