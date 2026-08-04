"""PDF — text-layer extraction via ``pypdf``, with a per-page OCR fallback for scanned pages.

**Two kinds of PDF, one parser.** A PDF with a real text layer (the overwhelming majority of
policy documents, exported invoices, generated reports) extracts directly. A *scanned* PDF has no
text layer at all — ``page.extract_text()`` returns empty for every page — and needs OCR. Rather
than deciding
which kind a document is up front, this parser tries text extraction first and only reaches for OCR
on the pages that actually came back empty, so a mixed document (a text policy with one scanned
signature page appended) gets the right treatment page by page instead of being OCR'd wholesale or
rejected as unsupported.

**OCR runs one page at a time, deliberately.** Amazon Textract's synchronous ``DetectDocumentText``
accepts a PDF, but only a *single-page* one — the multi-page case needs the asynchronous job API,
which requires the document to already be in S3, a materially bigger dependency this parser does
not take on. ``pypdf`` can already split a page out as its own standalone one-page PDF in memory, so
each empty page is extracted and OCR'd independently through
:func:`app.ai.parsing.image_ocr.ocr_text_from_bytes`, which respects that same constraint. Capped
at :data:`MAX_OCR_PAGES` per document — this parser is for a policy document with an occasional
scanned page, not a bulk digitisation pipeline, and an uncapped fallback would turn one large
scanned PDF into an unbounded number of billed Textract calls.

Page numbers are preserved on every section (``DocumentSection.page_number``), which is the one
thing this format reliably carries that HTML/Markdown/Word do not — and exactly what lets a citation
say "p.12" instead of only naming a heading.
"""

from __future__ import annotations

import io
from typing import Optional, Sequence

from app.ai.core.errors import ProviderError, ProviderNotConfiguredError
from app.ai.core.types import DocumentSection, RawDocument
from app.ai.parsing.base import DEFAULT_MAX_BYTES, BaseDocumentParser
from app.ai.parsing.image_ocr import is_ocr_available, ocr_text_from_bytes
from app.core.logging import get_logger

logger = get_logger(__name__)

NAME = "pdf"
MIME_TYPES = frozenset({"application/pdf"})
EXTENSIONS = frozenset({".pdf"})

MAX_OCR_PAGES = 50


class PdfParser(BaseDocumentParser):
    """PDF, sectioned by page, with OCR for pages whose text layer is empty."""

    def __init__(
        self, *, region: Optional[str] = None, max_bytes: int = DEFAULT_MAX_BYTES, recorder=None
    ) -> None:
        super().__init__(
            name=NAME, mime_types=MIME_TYPES, extensions=EXTENSIONS,
            max_bytes=max_bytes, recorder=recorder,
        )
        self._region = region

    def is_available(self) -> bool:
        try:
            import pypdf  # noqa: F401, PLC0415
        except ImportError:
            return False
        return True

    def _parse(self, document: RawDocument) -> tuple[str, Sequence[DocumentSection]]:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(document.content))
        page_texts = [(page.extract_text() or "").strip() for page in reader.pages]
        empty_pages = [index for index, text in enumerate(page_texts) if not text]
        ocr_budget = MAX_OCR_PAGES

        if empty_pages and not is_ocr_available():
            # Every other degraded path here logs (a per-page OCR failure, the budget running out);
            # silently dropping these pages instead would be the one path that doesn't, and it is
            # also the most consequential — a fully-scanned PDF would parse to an empty document
            # with nothing in the log to explain why.
            logger.warning(
                "ai.parsing.pdf_pages_dropped_no_ocr",
                extra={
                    "file": document.file_name, "emptyPages": len(empty_pages),
                    "totalPages": len(page_texts),
                },
            )
        elif empty_pages:
            for index, page in ((i, reader.pages[i]) for i in empty_pages):
                if ocr_budget <= 0:
                    logger.warning(
                        "ai.parsing.pdf_ocr_budget_exhausted",
                        extra={"file": document.file_name, "maxOcrPages": MAX_OCR_PAGES},
                    )
                    break
                page_texts[index] = self._ocr_one_page(page, page_number=index + 1)
                ocr_budget -= 1

        sections = tuple(
            DocumentSection(text=text, page_number=index + 1)
            for index, text in enumerate(page_texts)
            if text.strip()
        )
        full_text = "\n\n".join(s.text for s in sections)
        return full_text, sections

    def _ocr_one_page(self, page, *, page_number: int) -> str:  # noqa: ANN001
        """OCR one page, in isolation from the rest of the document.

        A failure here (a transient Textract error, say) must not fail the whole document when
        other pages already extracted cleanly — logged and left empty, exactly like the size-cap
        case, rather than raised.
        """
        try:
            return ocr_text_from_bytes(_single_page_pdf_bytes(page), region=self._region)
        except (ProviderError, ProviderNotConfiguredError) as exc:
            logger.warning(
                "ai.parsing.pdf_page_ocr_failed",
                extra={"page": page_number, "error": f"{type(exc).__name__}: {exc}"},
            )
            return ""


def _single_page_pdf_bytes(page) -> bytes:  # noqa: ANN001
    """Extract one ``pypdf`` page as its own standalone one-page PDF, in memory.

    What makes the per-page OCR fallback possible without a PDF-to-image rasterization dependency:
    Textract's synchronous API accepts a single-page *PDF* directly, so the page never needs to
    become an image at all.
    """
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_page(page)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


__all__ = ["EXTENSIONS", "MAX_OCR_PAGES", "MIME_TYPES", "NAME", "PdfParser"]
