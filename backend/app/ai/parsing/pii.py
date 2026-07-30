"""Deterministic PII detection — regex plus checksum validators, never a model.

Task 2's constraint is explicit and this module is where it is actually kept: an LLM asked "does
this contain personal data?" can miss things confidently and invent things convincingly, and either
failure mode is unacceptable for a scan whose result decides whether text gets indexed, masked or
rejected outright (see ``AISettings.PII_REDACTION_MODE``). Every finding here is either matched by a
regular expression, or matched *and then validated* by the exact checksum algorithm the identifier
format itself defines (Luhn for card numbers, ISO 7064 mod-97-10 for IBAN) — reproducible,
auditable, and exactly as accurate as the regex, no more and no less.

**Findings never overlap.** A single span of text produces exactly one finding, at the highest
confidence any scanner would have assigned it — a 16-digit run that also happens to satisfy a
looser "phone number" shape must not be reported twice under two different kinds. Scanners run in a
fixed order (checksum-validated kinds first, since a passing checksum is the strongest evidence
deterministic matching can produce; contextual keyword-anchored kinds last, since they are the
least specific) and a later scanner's match is discarded wherever it overlaps an earlier,
higher-confidence one.

**Only the span and a redacted sample are kept — never the matched text itself.** ``PIIFinding``
storing the real value would copy the very data this scan exists to control into the finding it
produces, and findings are exactly the kind of thing that ends up in logs and audit tables.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Sequence

from app.ai.core.enums import PIIKind
from app.ai.core.types import PIIFinding

# ---------------------------------------------------------------------------
# checksum validators — the exact algorithm each identifier format defines
# ---------------------------------------------------------------------------


def luhn_valid(digits: str) -> bool:
    """The Luhn checksum every major card network's PAN satisfies. ISO/IEC 7812-1."""
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


_IBAN_LETTER_VALUE = {chr(c): str(c - 55) for c in range(ord("A"), ord("Z") + 1)}


def iban_checksum_valid(candidate: str) -> bool:
    """ISO 7064 MOD 97-10: move the first 4 characters to the end, map letters to digits, mod 97."""
    compact = candidate.replace(" ", "").upper()
    if len(compact) < 15 or len(compact) > 34:
        return False
    rearranged = compact[4:] + compact[:4]
    digits = "".join(_IBAN_LETTER_VALUE.get(c, c) for c in rearranged)
    if not digits.isdigit():
        return False
    return int(digits) % 97 == 1


# ---------------------------------------------------------------------------
# patterns
# ---------------------------------------------------------------------------

_EMAIL = re.compile(r"\b[\w.+-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+\b")
_CREDIT_CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{2,4}){3,8}\b")
_IP_V4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IP_V6 = re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b")
# US SSN. Area 000/666/900-999 are reserved and never issued (SSA), excluded to cut false positives
# on arbitrary NNN-NN-NNNN-shaped reference numbers that are not really national ids.
_NATIONAL_ID_US_SSN = re.compile(
    r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"
)
# US EIN: NN-NNNNNNN.
_TAX_ID_US_EIN = re.compile(r"\b\d{2}-\d{7}\b")
_PHONE = re.compile(
    r"(?<!\d)(?:\+?\d{1,3}[ .-]?)?(?:\(\d{2,4}\)[ .-]?)?\d{3}[ .-]\d{3,4}[ .-]?\d{0,4}(?!\d)"
)

# Contextual kinds: a keyword within this many characters before the candidate span is required, so
# a bare number is never enough on its own — "account" near a digit run is a real signal a bare
# 10-digit number is not.
_CONTEXT_WINDOW = 40
_PASSPORT_KEYWORD = re.compile(r"\bpassport\b", re.IGNORECASE)
_PASSPORT_VALUE = re.compile(r"\b[A-Z][A-Z0-9]{6,8}\b")
_BANK_ACCOUNT_KEYWORD = re.compile(r"\b(?:account|acct|a/c)\b", re.IGNORECASE)
_BANK_ACCOUNT_VALUE = re.compile(r"\b\d{8,17}\b")

_NAME_TITLE = re.compile(
    r"\b(?:Mr|Mrs|Ms|Miss|Dr)\.\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})"
)
_NAME_LABEL = re.compile(r"\bName:\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})")

_STREET_SUFFIXES = (
    "Street", "St", "Avenue", "Ave", "Road", "Rd", "Boulevard", "Blvd", "Lane", "Ln",
    "Drive", "Dr", "Court", "Ct", "Way", "Place", "Pl",
)
_ADDRESS = re.compile(
    r"\b\d{1,5}\s+[A-Z][A-Za-z0-9.\s]{2,40}\b(?:" + "|".join(_STREET_SUFFIXES) + r")\b"
    r"(?:[,\s]+[A-Za-z\s]{2,30})?(?:[,\s]+\d{4,6})?"
)


@dataclass(frozen=True, slots=True)
class _Scanner:
    kind: PIIKind
    confidence: float
    find: Callable[[str], Sequence[tuple[int, int]]]


def _plain(pattern: re.Pattern) -> Callable[[str], Sequence[tuple[int, int]]]:
    return lambda text: [(m.start(), m.end()) for m in pattern.finditer(text)]


def _checksum_validated(
    pattern: re.Pattern, validator: Callable[[str], bool]
) -> Callable[[str], Sequence[tuple[int, int]]]:
    def find(text: str) -> Sequence[tuple[int, int]]:
        return [
            (m.start(), m.end())
            for m in pattern.finditer(text)
            if validator(m.group(0))
        ]
    return find


def _contextual(
    keyword: re.Pattern, value: re.Pattern
) -> Callable[[str], Sequence[tuple[int, int]]]:
    """Match ``value`` only where ``keyword`` appears within :data:`_CONTEXT_WINDOW` characters
    before it — the deterministic stand-in for "this number is labelled as an account/passport"
    that a human reader gets from context and a bare regex cannot."""

    def find(text: str) -> Sequence[tuple[int, int]]:
        keyword_ends = [m.end() for m in keyword.finditer(text)]
        if not keyword_ends:
            return []
        spans = []
        for m in value.finditer(text):
            if any(0 <= m.start() - end <= _CONTEXT_WINDOW for end in keyword_ends):
                spans.append((m.start(), m.end()))
        return spans

    return find


# Order is precedence: a checksum-validated match wins over a plain-regex match over the same span,
# which wins over a contextual match. See the module docstring.
_SCANNERS: tuple[_Scanner, ...] = (
    _Scanner(PIIKind.CREDIT_CARD, 0.95, _checksum_validated(
        _CREDIT_CARD, lambda s: luhn_valid(re.sub(r"[ -]", "", s))
    )),
    _Scanner(PIIKind.IBAN, 0.95, _checksum_validated(_IBAN, iban_checksum_valid)),
    _Scanner(PIIKind.EMAIL, 0.95, _plain(_EMAIL)),
    _Scanner(PIIKind.IP_ADDRESS, 0.9, _plain(_IP_V4)),
    _Scanner(PIIKind.IP_ADDRESS, 0.85, _plain(_IP_V6)),
    _Scanner(PIIKind.NATIONAL_ID, 0.75, _plain(_NATIONAL_ID_US_SSN)),
    _Scanner(PIIKind.TAX_ID, 0.7, _plain(_TAX_ID_US_EIN)),
    _Scanner(PIIKind.PHONE, 0.65, _plain(_PHONE)),
    _Scanner(PIIKind.POSTAL_ADDRESS, 0.65, _plain(_ADDRESS)),
    _Scanner(PIIKind.PASSPORT, 0.6, _contextual(_PASSPORT_KEYWORD, _PASSPORT_VALUE)),
    _Scanner(PIIKind.BANK_ACCOUNT, 0.6, _contextual(_BANK_ACCOUNT_KEYWORD, _BANK_ACCOUNT_VALUE)),
    _Scanner(PIIKind.PERSON_NAME, 0.6, lambda t: _name_spans(t)),
)


def _name_spans(text: str) -> Sequence[tuple[int, int]]:
    spans = [(m.start(1), m.end(1)) for m in _NAME_TITLE.finditer(text)]
    spans.extend((m.start(1), m.end(1)) for m in _NAME_LABEL.finditer(text))
    return spans


def scan(text: str) -> tuple[PIIFinding, ...]:
    """Every PII finding in ``text``, each span reported once at its highest-confidence kind."""
    if not text:
        return ()

    claimed: list[tuple[int, int]] = []
    findings: list[PIIFinding] = []
    for scanner in _SCANNERS:
        for start, end in scanner.find(text):
            if any(start < c_end and end > c_start for c_start, c_end in claimed):
                continue
            claimed.append((start, end))
            findings.append(
                PIIFinding(
                    kind=scanner.kind, start=start, end=end,
                    sample=_redact(text[start:end]), confidence=scanner.confidence,
                )
            )
    findings.sort(key=lambda f: f.start)
    return tuple(findings)


def _redact(raw: str) -> str:
    """A sample that shows shape, not value: first and last character, everything else masked."""
    if len(raw) <= 2:
        return "*" * len(raw)
    return raw[0] + "*" * (len(raw) - 2) + raw[-1]


def redact_text(text: str, findings: Sequence[PIIFinding]) -> str:
    """Replace every finding's span with a typed placeholder — the ``MASK`` redaction mode.

    Applied back-to-front so earlier replacements never shift the offsets later ones were computed
    against.
    """
    if not findings:
        return text
    ordered = sorted(findings, key=lambda f: f.start, reverse=True)
    for finding in ordered:
        placeholder = f"[REDACTED:{finding.kind.value}]"
        text = text[: finding.start] + placeholder + text[finding.end :]
    return text


__all__ = [
    "iban_checksum_valid",
    "luhn_valid",
    "redact_text",
    "scan",
]
