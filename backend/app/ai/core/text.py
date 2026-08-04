"""Text normalization, segmentation, token estimation and language detection.

All deterministic and dependency-free. Two design points worth stating:

**Token counting is pluggable, and the default is honest about being an estimate.**
:func:`estimate_tokens` is a script-aware heuristic used when no real tokenizer is available. It is
*not* claimed to match any model's tokenizer. When the bge-m3 provider is active it exposes the
genuine SentencePiece tokenizer, and chunkers accept an injected counter
(:class:`~app.ai.interfaces.embeddings.TokenCounter`) so budgets are exact. Getting this wrong in
the optimistic direction is what causes silent truncation at the model boundary, so the heuristic
deliberately errs high for CJK.

**Language detection is a heuristic, not a model.** Unicode-script counting plus stop-word scoring
identifies the handful of languages that matter for routing (which is all the platform uses it for
— never for a decision). It returns ``None`` rather than guessing on short or ambiguous input.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

# --- normalization -----------------------------------------------------------

_WHITESPACE = re.compile(r"[^\S\n]+")           # runs of space/tab, but not newlines
_BLANK_LINES = re.compile(r"\n{3,}")
_SOFT_HYPHEN = "­"
# "reimburse-\nment" -> "reimbursement". PDF extraction produces these constantly and leaving them
# in splits one word into two tokens that no query will ever match.
_HYPHEN_LINEBREAK = re.compile(r"(\w)-\n(\w)")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LIGATURES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "−": "-", " ": " ",
}


def normalize_text(text: str) -> str:
    """Clean extracted text without changing its meaning or its length materially.

    Applied *after* the identity checksum is taken, so normalization improvements never re-identify
    existing documents (see :mod:`app.ai.core.ids`).

    NFKC folds compatibility forms — full-width digits, ligatures, non-breaking spaces — so that a
    query typed with ASCII matches a PDF that used typographic characters.
    """
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace(_SOFT_HYPHEN, "")
    for bad, good in _LIGATURES.items():
        text = text.replace(bad, good)
    text = unicodedata.normalize("NFKC", text)
    text = _HYPHEN_LINEBREAK.sub(r"\1\2", text)
    text = _CONTROL.sub(" ", text)
    text = _WHITESPACE.sub(" ", text)
    text = _BLANK_LINES.sub("\n\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def normalize_for_matching(text: str) -> str:
    """Aggressive fold for fuzzy comparison: casefold, strip accents and punctuation.

    Used by vendor-alias matching and OCR similarity, where ``"UBER *TRIP  HELP.UBER.CO"`` and
    ``"Uber Trip"`` must be comparable. Never used for indexed text — it destroys information.
    """
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.casefold()
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# --- segmentation ------------------------------------------------------------

# Split after ., !, ?, or their CJK equivalents, when followed by whitespace/end. The negative
# lookbehind keeps common abbreviations and decimal amounts ("USD 1.50", "Inc.") intact, which
# matters because a sentence split mid-amount corrupts the very numbers policy chunks are about.
_ABBREVIATIONS = r"(?<!\bMr)(?<!\bMrs)(?<!\bDr)(?<!\bNo)(?<!\bInc)(?<!\bLtd)(?<!\bJan)(?<!\bFeb)" \
                 r"(?<!\be\.g)(?<!\bi\.e)(?<!\bvs)(?<!\betc)(?<!\d)"
_SENTENCE_END = re.compile(rf"{_ABBREVIATIONS}([.!?。！？])\s+")
_PARAGRAPH = re.compile(r"\n\s*\n")


def split_paragraphs(text: str) -> list[str]:
    """Split on blank lines. The most reliable natural boundary in policy documents."""
    return [p.strip() for p in _PARAGRAPH.split(text or "") if p.strip()]


def split_sentences(text: str) -> list[str]:
    """Heuristic sentence split, abbreviation- and decimal-aware."""
    if not text or not text.strip():
        return []
    # A replacement *function*, not a template: ``re`` rejects ``\x00`` inside a substitution
    # template, and NUL is the one separator guaranteed absent from extracted document text.
    marked = _SENTENCE_END.sub(lambda m: m.group(1) + "\x00", text)
    return [s.strip() for s in marked.split("\x00") if s.strip()]


def split_lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").split("\n") if line.strip()]


# --- token estimation --------------------------------------------------------

# Average characters per token, by script. Latin subword tokenizers land near 4; CJK is roughly one
# token per character, so a shared constant would under-count CJK by ~4x and silently overflow the
# model's context window.
_CHARS_PER_TOKEN_LATIN = 4.0
_CHARS_PER_TOKEN_CJK = 1.1

_CJK_RANGES = (
    (0x3040, 0x30FF),   # Hiragana + Katakana
    (0x3400, 0x4DBF),   # CJK Ext A
    (0x4E00, 0x9FFF),   # CJK Unified
    (0xAC00, 0xD7AF),   # Hangul
    (0xF900, 0xFAFF),   # CJK Compatibility
)


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in _CJK_RANGES)


def count_cjk(text: str) -> int:
    return sum(1 for c in text if _is_cjk(c))


def estimate_tokens(text: str) -> int:
    """Script-aware token estimate. Rounds **up**, and errs high rather than low.

    A budget computed from an under-estimate overflows the model's context window at request time;
    an over-estimate merely wastes a little of it. Prefer an injected real tokenizer when one is
    available — see the module docstring.
    """
    if not text:
        return 0
    cjk = count_cjk(text)
    other = len(text) - cjk
    estimate = cjk / _CHARS_PER_TOKEN_CJK + other / _CHARS_PER_TOKEN_LATIN
    return max(1, int(estimate + 0.999))


def truncate_to_tokens(text: str, max_tokens: int) -> tuple[str, bool]:
    """Trim ``text`` to roughly ``max_tokens``, preferring a sentence then a word boundary.

    Returns ``(text, truncated)``. The flag is returned rather than logged so callers can surface
    truncation to the user — silently shortened evidence leads to confident wrong conclusions.
    """
    if max_tokens <= 0:
        return "", bool(text)
    if estimate_tokens(text) <= max_tokens:
        return text, False

    cjk_ratio = count_cjk(text) / max(1, len(text))
    chars_per_token = (
        _CHARS_PER_TOKEN_CJK * cjk_ratio + _CHARS_PER_TOKEN_LATIN * (1 - cjk_ratio)
    )
    cut = max(1, int(max_tokens * chars_per_token))
    head = text[:cut]

    for boundary in (". ", "\n", " "):
        idx = head.rfind(boundary)
        if idx > cut * 0.6:                    # only if it does not discard most of the budget
            head = head[: idx + len(boundary.rstrip(" ") or boundary)]
            break
    return head.strip(), True


# --- language detection ------------------------------------------------------

_STOPWORDS: dict[str, frozenset[str]] = {
    "en": frozenset({"the", "and", "of", "to", "for", "is", "in", "shall", "must", "be", "with",
                     "expense", "policy", "employee", "reimbursement"}),
    "es": frozenset({"el", "la", "de", "los", "para", "que", "es", "en", "y", "gastos",
                     "empleado", "por"}),
    "fr": frozenset({"le", "la", "les", "de", "des", "pour", "que", "est", "et", "en",
                     "frais", "salarie"}),
    "de": frozenset({"der", "die", "das", "und", "von", "fur", "ist", "in", "mit", "nicht",
                     "kosten", "mitarbeiter"}),
    "pt": frozenset({"de", "para", "que", "os", "as", "em", "nao", "despesas", "funcionario"}),
    "nl": frozenset({"de", "het", "een", "van", "voor", "is", "en", "niet", "kosten"}),
    "it": frozenset({"il", "la", "di", "per", "che", "non", "sono", "spese", "dipendente"}),
}

_SCRIPT_LANGUAGES = (
    ("ja", (0x3040, 0x30FF)),
    ("ko", (0xAC00, 0xD7AF)),
    ("zh", (0x4E00, 0x9FFF)),
    ("ar", (0x0600, 0x06FF)),
    ("he", (0x0590, 0x05FF)),
    ("ru", (0x0400, 0x04FF)),
    ("hi", (0x0900, 0x097F)),
    ("th", (0x0E00, 0x0E7F)),
)

_MIN_CHARS_FOR_DETECTION = 12
_SCRIPT_SHARE_THRESHOLD = 0.15
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def detect_language(text: str, *, default: Optional[str] = None) -> Optional[str]:
    """Best-effort ISO-639-1 code, or ``default`` when undecidable.

    Non-Latin scripts are decided by codepoint share, which is unambiguous. Latin-script languages
    are scored by stop-word overlap, and a tie or a weak signal yields ``default`` — the platform
    only uses language for routing and filtering, so guessing is worse than abstaining.
    """
    if not text or len(text.strip()) < _MIN_CHARS_FOR_DETECTION:
        return default

    sample = text[:4000]
    letters = [c for c in sample if c.isalpha()]
    if not letters:
        return default

    # Script-based detection first: a single Japanese kana settles it.
    for code, (low, high) in _SCRIPT_LANGUAGES:
        share = sum(1 for c in letters if low <= ord(c) <= high) / len(letters)
        if share >= _SCRIPT_SHARE_THRESHOLD:
            # Japanese uses Han characters too; kana presence distinguishes it from Chinese.
            if code == "zh" and any(0x3040 <= ord(c) <= 0x30FF for c in letters):
                return "ja"
            return code

    words = [w.casefold() for w in _WORD.findall(sample)]
    if not words:
        return default

    scores = {lang: sum(1 for w in words if w in stops) for lang, stops in _STOPWORDS.items()}
    best = max(scores, key=lambda k: scores[k])
    best_score = scores[best]
    if best_score == 0:
        return default
    # Require a clear winner; ties between Romance languages are common on short text.
    runner_up = max((s for lang, s in scores.items() if lang != best), default=0)
    if best_score == runner_up:
        return default
    return best


def collapse_for_snippet(text: str, max_chars: int = 240) -> str:
    """One-line preview for logs and API summaries."""
    flat = re.sub(r"\s+", " ", text or "").strip()
    return flat if len(flat) <= max_chars else flat[: max_chars - 1].rstrip() + "…"


__all__ = [
    "collapse_for_snippet",
    "count_cjk",
    "detect_language",
    "estimate_tokens",
    "normalize_for_matching",
    "normalize_text",
    "split_lines",
    "split_paragraphs",
    "split_sentences",
    "truncate_to_tokens",
]
