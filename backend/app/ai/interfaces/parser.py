"""Document parser contract (Tasks 1 and 2).

Parsers self-declare the MIME types and extensions they handle and are resolved through a registry,
so supporting a new format is adding one file — never editing a dispatch ``if`` chain.

Contract:

1. ``can_parse`` is pure and cheap; it must not read the whole document.
2. ``parse`` returns text plus whatever structure the format genuinely carries. A format without
   headings returns a single section rather than fabricating a hierarchy.
3. A parser whose optional dependency is missing reports ``is_available() is False`` and is skipped
   by the registry — it must not raise ``ImportError`` at module import, or one absent library would
   break the whole platform's startup.
4. Parsers do not normalize, classify, scan for PII or score quality. Those are separate,
   independently-testable stages composed by the ingestion pipeline.
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from app.ai.core.types import DocumentSection, RawDocument


@runtime_checkable
class DocumentParser(Protocol):
    """Extracts text and structure from one family of document formats."""

    name: str

    @property
    def mime_types(self) -> frozenset[str]:
        ...

    @property
    def extensions(self) -> frozenset[str]:
        """Lowercase, dot-prefixed (``{'.pdf'}``). Used when the MIME type is absent or generic —
        browsers frequently send ``application/octet-stream`` for perfectly ordinary files."""
        ...

    def is_available(self) -> bool:
        """False when an optional dependency or credential is missing. Must not raise."""
        ...

    def can_parse(self, document: RawDocument) -> bool:
        ...

    def parse(self, document: RawDocument) -> tuple[str, Sequence[DocumentSection]]:
        """Return ``(full_text, sections)``. Raises ``ProviderError`` if the bytes are corrupt."""
        ...


__all__ = ["DocumentParser"]
