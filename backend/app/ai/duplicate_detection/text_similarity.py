"""OCR-text near-duplicate scoring: pure Python, no database or model dependency.

A signal that must run on whatever short excerpt of extracted text is available (see
``ClaimFingerprint.ocr_text_excerpt``), including the degenerate cases of empty or near-empty text,
which a naive ratio would score as a perfect match. :func:`text_similarity_ratio` treats "nothing to
compare" as "no signal", not "identical" — the same convention
:mod:`app.ai.embeddings.math` uses for a degenerate vector.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Optional

# Below this many meaningful characters, a similarity ratio is not trustworthy: two near-empty
# strings can score 1.0 by coincidence (e.g. two receipts that both extracted only "$" ). The signal
# is skipped rather than risk a false-positive "near duplicate" on almost no evidence.
_MIN_COMPARABLE_LENGTH = 8


def _normalize(text: str) -> str:
    return " ".join((text or "").split()).lower()


def text_similarity_ratio(text_a: str, text_b: str) -> Optional[float]:
    """``SequenceMatcher`` ratio over whitespace-normalized text, or ``None`` if either side is too
    short to compare meaningfully."""
    normalized_a = _normalize(text_a)
    normalized_b = _normalize(text_b)
    if len(normalized_a) < _MIN_COMPARABLE_LENGTH or len(normalized_b) < _MIN_COMPARABLE_LENGTH:
        return None
    return SequenceMatcher(None, normalized_a, normalized_b).ratio()


__all__ = ["text_similarity_ratio"]
