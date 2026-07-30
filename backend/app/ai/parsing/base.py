"""Shared behaviour every document parser inherits.

The ``DocumentParser`` protocol in ``app/ai/interfaces/parser.py`` states the contract; this class
implements the parts every format-specific parser would otherwise duplicate:

* **``can_parse`` by MIME type or extension**, in that order, because a browser or an S3 upload
  frequently sends a generic ``application/octet-stream`` MIME type for a perfectly ordinary file —
  the extension is often the only reliable signal, so it must be checked even when the MIME type is
  present but unhelpful.
* **The size ceiling.** ``AI_MAX_DOCUMENT_BYTES`` is enforced once, here, so a huge file fails with
  a clear ``UnsupportedDocumentError`` naming the limit before any format-specific library gets a
  chance to run out of memory trying to parse it.
* **A timing span per parse**, matching the pattern ``BaseVectorStore``/``EmbeddingService`` already
  establish for their own stages.

What is deliberately *not* here: normalization, language detection, PII scanning, classification,
quality scoring. Those are separate, independently-testable stages the ingestion pipeline composes
(interfaces/parser.py, contract clause 4) — a parser's only job is turning bytes into text plus
whatever structure the format genuinely carries.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Sequence

from app.ai.core.enums import TelemetryOperation
from app.ai.core.errors import AIValidationError, ProviderError
from app.ai.core.types import DocumentSection, RawDocument
from app.ai.telemetry.recorder import NullTelemetryRecorder
from app.core.logging import get_logger

logger = get_logger(__name__)

# 25 MB by default (AISettings.MAX_DOCUMENT_BYTES), enforced here rather than trusted to whichever
# format library ends up parsing the bytes — some (openpyxl loading a whole workbook into memory)
# have no size guard of their own at all.
DEFAULT_MAX_BYTES = 25 * 1024 * 1024


class BaseDocumentParser(ABC):
    """Base for every parser. Implements the invariants; ``_parse`` stays abstract."""

    def __init__(
        self,
        *,
        name: str,
        mime_types: frozenset[str],
        extensions: frozenset[str],
        max_bytes: int = DEFAULT_MAX_BYTES,
        recorder: Any = None,
    ) -> None:
        self.name = name
        self._mime_types = mime_types
        self._extensions = extensions
        self._max_bytes = max_bytes
        self._recorder = recorder or NullTelemetryRecorder()

    @property
    def mime_types(self) -> frozenset[str]:
        return self._mime_types

    @property
    def extensions(self) -> frozenset[str]:
        return self._extensions

    # --- declared contract ----------------------------------------------------

    @abstractmethod
    def is_available(self) -> bool:
        """False when an optional dependency or credential is missing. Must never raise."""

    def can_parse(self, document: RawDocument) -> bool:
        """MIME type match first; extension match as the fallback for a generic/absent MIME type."""
        if document.mime_type and document.mime_type.lower() in self._mime_types:
            return True
        return _extension_of(document.file_name) in self._extensions

    def parse(self, document: RawDocument) -> tuple[str, Sequence[DocumentSection]]:
        """Validate, time and delegate to :meth:`_parse`. Never called for bytes over the limit."""
        if document.size_bytes > self._max_bytes:
            raise AIValidationError(
                f"'{document.file_name}' is {document.size_bytes} bytes, over the "
                f"{self._max_bytes}-byte limit (AI_MAX_DOCUMENT_BYTES).",
                details={
                    "fileName": document.file_name, "sizeBytes": document.size_bytes,
                    "maxBytes": self._max_bytes,
                },
            )
        with self._recorder.span(
            TelemetryOperation.PARSE, attributes={"parser": self.name, "file": document.file_name}
        ) as span:
            try:
                text, sections = self._parse(document)
            except (AIValidationError, ProviderError):
                raise
            except Exception as exc:
                # Contract clause 2 on DocumentParser: "raises ProviderError if the bytes are
                # corrupt." A parser was found and matched this document's MIME/extension; it is the
                # library underneath that failed, which is exactly what ProviderError means here —
                # an upstream (the parsing library) failed on input it claimed to support.
                span.fail(f"{type(exc).__name__}: {exc}")
                raise ProviderError(
                    self.name,
                    f"failed to parse '{document.file_name}': {type(exc).__name__}: {exc}",
                ) from exc
            span.set_counts(candidates_in=1, candidates_out=1 if text.strip() else 0)
        return text, sections

    @abstractmethod
    def _parse(self, document: RawDocument) -> tuple[str, Sequence[DocumentSection]]:
        """Format-specific extraction. Let any exception propagate; :meth:`parse` wraps it."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} {self.name}>"


def _extension_of(file_name: str) -> str:
    """Lowercase, dot-prefixed extension, or ``""`` for a name with none."""
    dot = file_name.rfind(".")
    return file_name[dot:].lower() if dot > -1 else ""


def single_section(text: str) -> tuple[DocumentSection, ...]:
    """A format with no heading structure gets one section spanning the whole document.

    Contract clause 2: "A format without headings returns a single section rather than
    fabricating a hierarchy." A fabricated heading would give a citation a page/section label the
    source document never actually had.
    """
    return (DocumentSection(text=text),) if text.strip() else ()


def sections_from_headings(
    entries: Sequence[tuple[int, str, str]],
) -> tuple[DocumentSection, ...]:
    """Turn ``(level, heading, body)`` triples, in document order, into sections with a correct
    ``heading_path``.

    Shared by every parser whose format expresses structure as a heading hierarchy (HTML, Markdown,
    Word) rather than each re-implementing ancestor tracking. ``heading_path`` is the *full*
    ancestor chain, so a level-3 heading nested under two higher-level ones carries both — what
    turns a retrieved chunk into a citable location ("Travel Policy > Meals > Alcohol") rather than
    an opaque offset into the document. ``level`` is 0 for text appearing before the first heading.
    """
    if not entries:
        return ()
    if len(entries) == 1 and entries[0][0] == 0 and not entries[0][1]:
        return single_section(entries[0][2])

    sections: list[DocumentSection] = []
    stack: list[tuple[int, str]] = []  # (level, heading) ancestors, outermost first
    for level, heading, body in entries:
        if heading:
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading))
        path = tuple(h for _, h in stack)
        combined = f"{heading}\n{body}".strip() if heading else body.strip()
        if not combined:
            continue
        sections.append(
            DocumentSection(text=combined, heading=heading or None, level=level, heading_path=path)
        )
    return tuple(sections)


__all__ = [
    "DEFAULT_MAX_BYTES",
    "BaseDocumentParser",
    "sections_from_headings",
    "single_section",
]
