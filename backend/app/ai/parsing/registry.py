"""The parser registry — resolves a :class:`RawDocument` to the parser that handles it.

Deliberately **not** ``app.ai.registry.ComponentRegistry``. That registry answers "give me the one
active component of this kind by configured name" (one embedding provider, one vector store).
Parsing answers a different question — "which of these N registered parsers, if any, actually
handles *this* document?" — so resolution is a linear scan by predicate (``can_parse``), not a
name lookup. Supporting a new format is registering one more parser, never editing a dispatch
``if``/``elif`` chain (the module docstring on ``interfaces/parser.py`` states this as the point).

**Registration order is priority order.** Two parsers claiming the same extension is possible in
principle (an OCR fallback for a scanned PDF, say, versus the primary PDF text-layer parser) and the
first *available and matching* one registered wins. Register the more specific/preferred parser
first.
"""

from __future__ import annotations

import threading
from typing import Any, Iterable

from app.ai.core.errors import UnsupportedDocumentError
from app.ai.core.types import RawDocument
from app.ai.interfaces.parser import DocumentParser
from app.core.logging import get_logger

logger = get_logger(__name__)


class ParserRegistry:
    """Ordered parsers, resolved by predicate rather than by name."""

    def __init__(self) -> None:
        self._parsers: dict[str, DocumentParser] = {}
        self._order: list[str] = []
        self._lock = threading.RLock()

    def register(self, parser: DocumentParser, *, replace: bool = False) -> None:
        with self._lock:
            if parser.name in self._parsers and not replace:
                raise ValueError(
                    f"Parser '{parser.name}' is already registered. Pass replace=True to "
                    "override deliberately."
                )
            if parser.name not in self._parsers:
                self._order.append(parser.name)
            self._parsers[parser.name] = parser

    def names(self) -> tuple[str, ...]:
        return tuple(self._order)

    def resolve(self, document: RawDocument) -> DocumentParser:
        """The first registered, available parser that claims this document.

        Availability is checked before ``can_parse``: an unavailable parser (a missing optional
        dependency) must never win the match and then fail — a later parser that genuinely can
        handle the format should get the chance instead.
        """
        with self._lock:
            candidates = [self._parsers[name] for name in self._order]

        tried: list[str] = []
        for parser in candidates:
            if not parser.is_available():
                tried.append(f"{parser.name}: unavailable")
                continue
            if parser.can_parse(document):
                return parser
            tried.append(f"{parser.name}: does not claim this format")

        raise UnsupportedDocumentError(
            document.mime_type, document.file_name, supported=self._available_mime_types(),
        )

    def _available_mime_types(self) -> list[str]:
        """MIME types a parser can *actually* handle right now — not merely declares.

        Feeds ``UnsupportedDocumentError.details['supportedMimeTypes']``, so this excludes any
        parser reporting ``is_available() is False``. Listing an unavailable parser's formats there
        would produce a self-contradictory error: "no parser is registered for '.docx'" naming
        ``.docx`` as supported, on a host that is missing ``python-docx``.
        """
        seen: set[str] = set()
        for name in self._order:
            parser = self._parsers[name]
            if _safe_available(parser):
                seen.update(parser.mime_types)
        return sorted(seen)

    def describe(self) -> list[dict[str, Any]]:
        """Registered parsers and their declared formats, for the governance/metrics endpoint."""
        with self._lock:
            parsers = [self._parsers[name] for name in self._order]
        return [
            {
                "name": p.name,
                "available": _safe_available(p),
                "mimeTypes": sorted(p.mime_types),
                "extensions": sorted(p.extensions),
            }
            for p in parsers
        ]

    def reset(self) -> None:
        """Test helper: return the registry to empty."""
        with self._lock:
            self._parsers.clear()
            self._order.clear()


def _safe_available(parser: DocumentParser) -> bool:
    try:
        return bool(parser.is_available())
    except Exception:  # pragma: no cover - defensive; a parser's is_available must not raise
        return False


def register_all(registry: ParserRegistry, parsers: Iterable[DocumentParser]) -> None:
    """Register a batch in order. Convenience for the composition root."""
    for parser in parsers:
        registry.register(parser, replace=True)


#: Module-level singleton, matching the pattern ``app.ai.registry`` uses for its own registries —
#: populated once by :func:`app.ai.parsing.register_parsers`.
parser_registry = ParserRegistry()


__all__ = ["ParserRegistry", "parser_registry", "register_all"]
