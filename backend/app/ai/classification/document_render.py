"""Turn an uploaded receipt into image bytes a vision model can actually decode.

**Why this module exists.** The classification layer sends the receipt to a multimodal model as a
base64 image part. Receipts arrive as PNG, JPEG *and PDF*, and a PDF is not an image: the model
container decodes the part with Pillow, which reports ``cannot identify image file <_io.BytesIO ...>``
and fails the whole request. Every PDF claim therefore fell back to ``categoryReviewRequired`` and
went to manual review. A PDF has to be rasterized on this side before it is ever sent.

**Why a rasterizer at all.** ``pypdf`` (used by :mod:`app.ai.parsing.pdf`) reads text layers and can
split a page out as its own one-page PDF, which is all Textract needs — its synchronous API accepts a
single-page PDF, so that parser deliberately avoids a rasterization dependency. A vision model has no
such affordance: the page must become pixels. Pillow cannot open a PDF either, hence ``pypdfium2``.

**Never raises, always reports.** Guarded exactly like :mod:`app.ai.duplicate_detection.image_hash`
and every parser in :mod:`app.ai.parsing`: a missing dependency, a corrupt file or an unreadable
format yields :class:`PreparedImages` with an empty ``images`` list and a human-readable ``note``,
never an exception. Classification then proceeds on OCR text alone — a degraded verdict, but still a
verdict — and only a document with neither usable text nor a usable image reaches manual review.

**The declared MIME type is not trusted.** ``ExpenseItem.mime_type`` comes from the browser's
multipart header and is never validated server-side, and the S3 ``ContentType`` echoed back on
download is whatever was stored (absent for some uploads, in which case the Bedrock adapter used to
default it to ``image/jpeg`` — actively mislabelling a PDF). The file's own magic bytes decide here;
the declared type is only a fallback for bytes we cannot recognise.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

PDF_MIME_TYPE = "application/pdf"
PNG_MIME_TYPE = "image/png"
JPEG_MIME_TYPE = "image/jpeg"

# Formats a vision model reliably decodes, and which therefore travel byte-for-byte: re-encoding a
# receipt that is already fine buys nothing and only risks losing detail the model needs to read.
_PASSTHROUGH_MIME_TYPES = frozenset({PNG_MIME_TYPE, JPEG_MIME_TYPE})

# Longest-edge cap. Every pixel becomes ~1.37 base64 characters in the request body, so a 12-megapixel
# phone photo of a lunch bill would be tens of megabytes on the wire for no gain in legibility — a
# receipt's text is readable well below this. Images under the cap are left exactly as they are.
_MAX_IMAGE_EDGE = 2000

# Re-encode quality for photographic receipts. Only used when a JPEG had to be resized; scans and
# screenshots go to PNG instead, where there is no quality knob to get wrong.
_JPEG_QUALITY = 85


@dataclass(frozen=True)
class PreparedImages:
    """Model-ready image parts plus the provenance the caller logs.

    ``images`` is exactly the shape ``BedrockGemmaProvider.generate_text`` expects
    (``[{"bytes": ..., "mime_type": ...}, ...]``), so nothing downstream has to reshape it. Empty
    means "classify from text only" and ``note`` says why.
    """

    images: list[dict[str, Any]] = field(default_factory=list)
    source_mime_type: Optional[str] = None
    was_pdf: bool = False
    pdf_page_count: int = 0
    pages_rendered: int = 0
    note: Optional[str] = None

    @property
    def image_mime_type(self) -> Optional[str]:
        """The MIME type actually being sent to the model, or ``None`` when no image is."""
        return self.images[0]["mime_type"] if self.images else None

    @property
    def total_bytes(self) -> int:
        return sum(len(i["bytes"]) for i in self.images)


def _pdfium_module():
    try:
        import pypdfium2  # noqa: PLC0415
    except ImportError:
        return None
    return pypdfium2


def _pillow_image_module():
    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError:
        return None
    return Image


def is_pdf_render_available() -> bool:
    """Whether a PDF receipt can be rasterized here (needs both pypdfium2 and Pillow).

    ``PdfBitmap.to_pil()`` is how the rendered page becomes encodable bytes, so Pillow is as
    load-bearing as pypdfium2 itself for this path.
    """
    return _pdfium_module() is not None and _pillow_image_module() is not None


def sniff_mime_type(data: bytes, declared: Optional[str] = None) -> Optional[str]:
    """The file's real type from its magic bytes, falling back to ``declared`` when unrecognised.

    Deliberately authoritative over ``declared``: see the module docstring on why neither the
    browser-supplied nor the S3-echoed content type can be believed.
    """
    if not data:
        return declared
    if data[:5] == b"%PDF-":
        return PDF_MIME_TYPE
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return PNG_MIME_TYPE
    if data[:3] == b"\xff\xd8\xff":
        return JPEG_MIME_TYPE
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if data[:2] == b"BM":
        return "image/bmp"
    # HEIC/HEIF: named explicitly so the note says "cannot decode HEIC" rather than "unknown type".
    # Pillow has no HEIF support without a plugin, so these degrade to text-only by design.
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"hevc", b"mif1", b"heim"):
        return "image/heic"
    return declared


def prepare_images(
    data: Optional[bytes],
    declared_mime_type: Optional[str] = None,
    *,
    max_pages: int = 1,
    scale: float = 2.0,
) -> PreparedImages:
    """Normalize receipt bytes into image parts, or explain why there are none.

    ``max_pages`` caps how many PDF pages are rendered; it is a parameter rather than a settings read
    so the cap is testable independently of what production is configured to send.
    """
    if not data:
        return PreparedImages(source_mime_type=declared_mime_type, note="Receipt bytes were empty.")

    source = sniff_mime_type(data, declared_mime_type)

    if source == PDF_MIME_TYPE:
        return _prepare_pdf(data, source, max_pages=max_pages, scale=scale)
    if source and source.startswith("image/"):
        return _prepare_image(data, source)
    return PreparedImages(
        source_mime_type=source,
        note=(
            f"Receipt type '{source}' is not an image or PDF; classified from text only."
            if source
            else "Receipt file type could not be identified; classified from text only."
        ),
    )


def _prepare_pdf(
    data: bytes, source: str, *, max_pages: int, scale: float
) -> PreparedImages:
    pdfium = _pdfium_module()
    Image = _pillow_image_module()
    if pdfium is None or Image is None:
        missing = "pypdfium2" if pdfium is None else "Pillow"
        return PreparedImages(
            source_mime_type=source,
            was_pdf=True,
            note=f"PDF receipt could not be converted to an image ({missing} is not installed).",
        )

    pages_wanted = max(1, max_pages)
    try:
        pdf = pdfium.PdfDocument(data)
        try:
            page_count = len(pdf)
            if page_count == 0:
                return PreparedImages(
                    source_mime_type=source, was_pdf=True, note="PDF receipt has no pages."
                )
            images: list[dict[str, Any]] = []
            for index in range(min(page_count, pages_wanted)):
                rendered = pdf[index].render(scale=scale).to_pil()
                try:
                    images.append(
                        {"bytes": _encode(rendered, PNG_MIME_TYPE, Image), "mime_type": PNG_MIME_TYPE}
                    )
                finally:
                    rendered.close()
        finally:
            pdf.close()
    except Exception as exc:  # noqa: BLE001 - a damaged PDF must degrade, never break submission
        logger.warning(
            "ai.classification.pdf_render_failed",
            extra={"error": f"{type(exc).__name__}: {exc}"[:300], "bytes": len(data)},
        )
        return PreparedImages(
            source_mime_type=source,
            was_pdf=True,
            note=f"PDF receipt could not be rendered as an image ({type(exc).__name__}).",
        )

    skipped = page_count - len(images)
    return PreparedImages(
        images=images,
        source_mime_type=source,
        was_pdf=True,
        pdf_page_count=page_count,
        pages_rendered=len(images),
        note=(
            f"PDF has {page_count} pages; only the first {len(images)} was sent for classification."
            if skipped > 0
            else None
        ),
    )


def _prepare_image(data: bytes, source: str) -> PreparedImages:
    Image = _pillow_image_module()
    if Image is None:
        # Without Pillow nothing can be validated or converted. Formats the model handles natively
        # are still worth sending as-is; anything else would just reproduce the original failure.
        if source in _PASSTHROUGH_MIME_TYPES:
            return PreparedImages(
                images=[{"bytes": data, "mime_type": source}], source_mime_type=source
            )
        return PreparedImages(
            source_mime_type=source,
            note=f"Receipt image '{source}' could not be converted (Pillow is not installed).",
        )

    try:
        # verify() is the cheap structural check that catches a truncated or mislabelled file here
        # instead of as an opaque 400 from the model. It consumes the handle, so decoding below
        # needs a fresh one.
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        with Image.open(io.BytesIO(data)) as image:
            oversized = max(image.size) > _MAX_IMAGE_EDGE
            if source in _PASSTHROUGH_MIME_TYPES and not oversized:
                return PreparedImages(
                    images=[{"bytes": data, "mime_type": source}], source_mime_type=source
                )
            # A resized photo stays a JPEG — re-encoding one as PNG can multiply its size.
            target = JPEG_MIME_TYPE if source == JPEG_MIME_TYPE else PNG_MIME_TYPE
            encoded = _encode(image, target, Image, downscale=oversized)
    except Exception as exc:  # noqa: BLE001 - an unreadable upload must degrade, not raise
        logger.warning(
            "ai.classification.image_decode_failed",
            extra={
                "sourceMimeType": source,
                "error": f"{type(exc).__name__}: {exc}"[:300],
                "bytes": len(data),
            },
        )
        return PreparedImages(
            source_mime_type=source,
            note=f"Receipt image could not be decoded ({type(exc).__name__}).",
        )

    return PreparedImages(images=[{"bytes": encoded, "mime_type": target}], source_mime_type=source)


def _encode(image, target_mime_type: str, Image, *, downscale: bool = True) -> bytes:
    """Encode a Pillow image to PNG or JPEG bytes, capping its longest edge."""
    frame = image.convert("RGB")
    try:
        if downscale and max(frame.size) > _MAX_IMAGE_EDGE:
            frame.thumbnail((_MAX_IMAGE_EDGE, _MAX_IMAGE_EDGE), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        if target_mime_type == JPEG_MIME_TYPE:
            frame.save(buffer, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
        else:
            frame.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
    finally:
        if frame is not image:
            frame.close()


__all__ = [
    "JPEG_MIME_TYPE",
    "PDF_MIME_TYPE",
    "PNG_MIME_TYPE",
    "PreparedImages",
    "is_pdf_render_available",
    "prepare_images",
    "sniff_mime_type",
]
