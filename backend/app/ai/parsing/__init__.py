"""Document parsing (Task 1/2): turns bytes into text plus structure, and the independent stages —
normalization, language detection, PII scanning, classification, quality scoring — that turn that
into a full :class:`~app.ai.core.types.ParsedDocument`.

**Composition is deliberately not done here.** ``interfaces/parser.py`` states it as contract clause
4: a parser's only job is extraction; normalizing, classifying, PII-scanning and quality-scoring a
document are separate, independently-testable stages the *ingestion pipeline* (M8) composes. This
package registers parsers and exposes each stage as a standalone function — ``pii.scan``,
``classifier.classify``, ``quality.score``, ``normalizer.clean_sections``,
``language.detect_document_language`` — precisely so M8 can compose them in one place rather than
this package silently deciding the ingestion order on M8's behalf.

Layout:

* ``base`` — the invariants every parser inherits: size limit, a timing span, MIME/extension
  matching.
* ``registry`` — resolves a document to the parser that handles it, by predicate rather than by
  name (unlike ``app.ai.registry.ComponentRegistry`` — see that module's own docstring for why).
* ``text``/``json_parser``/``html_md``/``docx``/``xlsx_csv``/``pdf``/``image_ocr`` — one format
  family each.
* ``normalizer``/``language``/``pii``/``classifier``/``quality``/``checksum`` — the independent
  post-parse stages.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.ai.parsing.registry import ParserRegistry, parser_registry, register_all

if TYPE_CHECKING:
    from app.ai.core.types import RawDocument
    from app.ai.interfaces.parser import DocumentParser

FALLBACK_AVAILABLE_ALWAYS = ("text", "json", "csv", "markdown")


def register_parsers() -> None:
    """Register every parser. Called once by the composition root.

    Every constructor is cheap and every optional dependency guarded (contract clause 3), so this
    succeeds on a host with none of ``pypdf``/``python-docx``/``openpyxl``/``beautifulsoup4``/
    ``boto3`` installed — those parsers simply report ``is_available() is False`` and the registry
    skips them, leaving the dependency-free formats (plain text, JSON, CSV, Markdown) always usable.
    """
    from app.ai.core.config import ai_settings as s
    from app.ai.parsing.docx import DocxParser
    from app.ai.parsing.html_md import HtmlParser, MarkdownParser
    from app.ai.parsing.image_ocr import ImageOcrParser
    from app.ai.parsing.json_parser import JsonParser
    from app.ai.parsing.pdf import PdfParser
    from app.ai.parsing.text import TextParser
    from app.ai.parsing.xlsx_csv import CsvParser, XlsxParser

    # Every parser but the image one shares AI_MAX_DOCUMENT_BYTES; the image OCR parser keeps its
    # own, tighter ceiling (MAX_BYTES in image_ocr.py) because it is Textract's synchronous-API
    # limit, not a configuration choice.
    max_bytes = s.MAX_DOCUMENT_BYTES
    register_all(
        parser_registry,
        [
            PdfParser(region=s.OCR_REGION, max_bytes=max_bytes),
            DocxParser(max_bytes=max_bytes),
            XlsxParser(max_bytes=max_bytes),
            HtmlParser(max_bytes=max_bytes),
            MarkdownParser(max_bytes=max_bytes),
            CsvParser(max_bytes=max_bytes),
            JsonParser(max_bytes=max_bytes),
            ImageOcrParser(region=s.OCR_REGION),
            TextParser(max_bytes=max_bytes),  # broadest fallback: registered last, tried last
        ],
    )


def resolve_parser(document: "RawDocument") -> "DocumentParser":
    """The parser that handles ``document``, registering the default set on first use."""
    if not parser_registry.names():
        register_parsers()
    return parser_registry.resolve(document)


__all__ = [
    "FALLBACK_AVAILABLE_ALWAYS",
    "ParserRegistry",
    "parser_registry",
    "register_parsers",
    "resolve_parser",
]
