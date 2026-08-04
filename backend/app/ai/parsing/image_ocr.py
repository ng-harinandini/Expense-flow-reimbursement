"""OCR for scanned images, via Amazon Textract's ``DetectDocumentText`` — plain text extraction,
not the receipt-structured ``AnalyzeExpense`` T001's ``textract_service.py`` uses.

Deliberately a separate call from T001's: ``AnalyzeExpense`` looks for vendor/amount/line-item
fields and is right for a receipt, wrong for a scanned policy page, which has no such structure to
find. Both share the same credential chain, region setting and graceful-degradation shape (guarded
``boto3`` import, ``is_available()`` checks for a resolvable credential without a network call,
every failure degrades to unavailable rather than raising at import or construction) — the pattern
is reused even though the API call is not.

**Deliberately not identified by MIME type/extension alone as "the" PDF handler.** A PDF can have a
real text layer (route to ``pdf.py``) or be a scan with no extractable text at all (needs OCR).
:mod:`app.ai.parsing.pdf` detects the empty-text-layer case itself and calls
:func:`ocr_text_from_bytes` directly, page image by page image, rather than this module registering
itself as a competing parser for ``.pdf`` — the registry resolves *one* parser per document, and PDF
routing depends on the *content*, not just the extension.
"""

from __future__ import annotations

from typing import Optional, Sequence

from app.ai.core.errors import ProviderError, ProviderNotConfiguredError
from app.ai.core.types import DocumentSection, RawDocument
from app.ai.parsing.base import BaseDocumentParser, single_section
from app.core.logging import get_logger

logger = get_logger(__name__)

NAME = "image_ocr"
MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/tiff"})
EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".tiff", ".tif"})

# Textract's own synchronous-API ceiling for DetectDocumentText.
MAX_BYTES = 10 * 1024 * 1024


class ImageOcrParser(BaseDocumentParser):
    """Standalone scanned images (not PDF pages — see the module docstring)."""

    def __init__(self, *, region: Optional[str] = None, recorder=None) -> None:
        super().__init__(
            name=NAME, mime_types=MIME_TYPES, extensions=EXTENSIONS,
            max_bytes=MAX_BYTES, recorder=recorder,
        )
        self._region = region

    def is_available(self) -> bool:
        return is_ocr_available()

    def _parse(self, document: RawDocument) -> tuple[str, Sequence[DocumentSection]]:
        text = ocr_text_from_bytes(document.content, region=self._region)
        return text, single_section(text)


def is_ocr_available() -> bool:
    """Whether OCR can actually run: boto3 installed and a credential resolvable.

    Never raises, and never makes a network call — matching ``BedrockTitanEmbeddingProvider``'s own
    ``is_available`` (checking *presence* of a credential, not that it is valid, which would cost a
    round trip on every registry introspection).
    """
    try:
        import boto3
    except ImportError:
        return False
    try:
        return boto3.Session().get_credentials() is not None
    except Exception:
        return False


def ocr_text_from_bytes(content: bytes, *, region: Optional[str] = None) -> str:
    """Run Textract ``DetectDocumentText`` over one image and return its lines, newline-joined.

    Raises :class:`ProviderNotConfiguredError` when boto3/credentials are absent and
    :class:`ProviderError` when the call itself fails — the same distinction every other provider in
    this platform makes, so a caller can tell "not set up" from "set up but broken" apart.
    """
    if not is_ocr_available():
        raise ProviderNotConfiguredError(
            provider=NAME, kind="OCR",
            remedy="Install boto3 and configure AWS credentials (or disable OCR sources).",
        )
    try:
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "textract", region_name=region, config=Config(retries={"max_attempts": 0})
        )
        response = client.detect_document_text(Document={"Bytes": content})
    except Exception as exc:
        raise ProviderError(
            NAME, f"DetectDocumentText failed: {type(exc).__name__}: {exc}"
        ) from exc

    lines = [
        block.get("Text", "")
        for block in response.get("Blocks", []) or []
        if block.get("BlockType") == "LINE"
    ]
    return "\n".join(line for line in lines if line)


__all__ = [
    "EXTENSIONS",
    "MAX_BYTES",
    "MIME_TYPES",
    "NAME",
    "ImageOcrParser",
    "is_ocr_available",
    "ocr_text_from_bytes",
]
