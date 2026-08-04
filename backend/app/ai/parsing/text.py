"""Plain text — the parser every other parser's fallback path can lean on.

No optional dependency, so ``is_available`` is unconditionally ``True``. The only real work is
picking a text encoding: browsers and export tools still routinely produce Windows-1252 or Latin-1
files with no BOM and no declared charset, and guessing wrong turns readable text into replacement
characters that then poison every downstream stage (search, PII scanning, citation snippets).
"""

from __future__ import annotations

from typing import Sequence

from app.ai.core.types import DocumentSection, RawDocument
from app.ai.parsing.base import DEFAULT_MAX_BYTES, BaseDocumentParser, single_section

NAME = "text"
MIME_TYPES = frozenset({"text/plain"})
EXTENSIONS = frozenset({".txt", ".text", ".log"})

# Tried in order. utf-8-sig strips a BOM if present; cp1252 is a superset of latin-1 covering the
# "smart quotes" Windows tools commonly emit, so it is tried before the byte-for-byte latin-1
# fallback that can decode literally anything and therefore must go last.
_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


def decode_best_effort(content: bytes) -> str:
    """Decode ``content`` with the first encoding in :data:`_ENCODINGS` that does not raise.

    ``latin-1`` never raises (every byte value is a valid Latin-1 codepoint), so this always
    returns *something* — the point of trying the earlier encodings first is producing *correct*
    text for the common cases rather than merely non-crashing text for all of them.
    """
    for encoding in _ENCODINGS:
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("latin-1")  # pragma: no cover - unreachable; latin-1 cannot raise


class TextParser(BaseDocumentParser):
    """Plain text and anything close enough to it (``.log``)."""

    def __init__(self, *, max_bytes: int = DEFAULT_MAX_BYTES, recorder=None) -> None:
        super().__init__(
            name=NAME, mime_types=MIME_TYPES, extensions=EXTENSIONS,
            max_bytes=max_bytes, recorder=recorder,
        )

    def is_available(self) -> bool:
        return True

    def _parse(self, document: RawDocument) -> tuple[str, Sequence[DocumentSection]]:
        text = decode_best_effort(document.content)
        return text, single_section(text)


__all__ = ["EXTENSIONS", "MIME_TYPES", "NAME", "TextParser", "decode_best_effort"]
