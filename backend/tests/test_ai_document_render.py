"""Receipt bytes -> model-ready image parts (``app.ai.classification.document_render``).

The bug these tests exist for: a PDF receipt was base64'd into an ``image_url`` part unchanged, and
the model's own Pillow call answered ``cannot identify image file <_io.BytesIO ...>``, failing the
whole classification and sending every PDF claim to manual review. So the load-bearing assertions
here are (a) a PDF comes out as PNG bytes, and (b) nothing in this module ever raises — an
undecodable upload must cost the image only, leaving OCR text to classify from.

Fixtures are built in-process rather than committed as binaries: ``pypdf`` writes real PDFs and
Pillow writes real images, so the inputs are genuine files rather than hand-waved byte strings.
"""

from __future__ import annotations

import io

import pytest

from app.ai.classification import document_render
from app.ai.classification.document_render import (
    JPEG_MIME_TYPE,
    PDF_MIME_TYPE,
    PNG_MIME_TYPE,
    is_pdf_render_available,
    prepare_images,
    sniff_mime_type,
)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = b"\xff\xd8\xff"


def pdf_bytes(pages: int = 1, *, width: int = 200, height: int = 200) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=width, height=height)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def image_bytes(fmt: str, *, size: tuple[int, int] = (40, 30), color: str = "white") -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format=fmt)
    return buffer.getvalue()


# --- 1. PDF -> image conversion succeeds ------------------------------------------------------


def test_pdf_receipt_is_rendered_to_png() -> None:
    pytest.importorskip("pypdfium2")
    result = prepare_images(pdf_bytes(), PDF_MIME_TYPE)

    assert result.was_pdf is True
    assert result.source_mime_type == PDF_MIME_TYPE
    assert result.pdf_page_count == 1
    assert result.pages_rendered == 1
    assert len(result.images) == 1
    assert result.images[0]["mime_type"] == PNG_MIME_TYPE
    # The whole point: real PNG bytes, not a PDF wearing an image's MIME type.
    assert result.images[0]["bytes"].startswith(PNG_SIGNATURE)
    assert result.image_mime_type == PNG_MIME_TYPE
    assert result.note is None


def test_rendered_page_scales_with_the_scale_argument() -> None:
    pytest.importorskip("pypdfium2")
    from PIL import Image

    def edge(scale: float) -> int:
        result = prepare_images(pdf_bytes(), PDF_MIME_TYPE, scale=scale)
        with Image.open(io.BytesIO(result.images[0]["bytes"])) as rendered:
            return max(rendered.size)

    assert edge(2.0) > edge(1.0)


def test_multi_page_pdf_sends_only_the_first_page_by_default() -> None:
    """The production default is one image per request; skipped pages are still reported."""
    pytest.importorskip("pypdfium2")
    result = prepare_images(pdf_bytes(pages=5), PDF_MIME_TYPE, max_pages=1)

    assert len(result.images) == 1
    assert result.pages_rendered == 1
    assert result.pdf_page_count == 5
    # Not silently dropped — a reviewer can see the document had more to it.
    assert result.note is not None
    assert "5 pages" in result.note


def test_page_cap_is_honoured_when_raised() -> None:
    """Proves the cap logic ahead of enabling >1 page in config, once Bedrock is verified."""
    pytest.importorskip("pypdfium2")
    result = prepare_images(pdf_bytes(pages=5), PDF_MIME_TYPE, max_pages=3)

    assert len(result.images) == 3
    assert result.pages_rendered == 3
    assert result.pdf_page_count == 5
    assert all(i["mime_type"] == PNG_MIME_TYPE for i in result.images)
    assert all(i["bytes"].startswith(PNG_SIGNATURE) for i in result.images)


def test_page_cap_never_renders_more_pages_than_the_pdf_has() -> None:
    pytest.importorskip("pypdfium2")
    result = prepare_images(pdf_bytes(pages=2), PDF_MIME_TYPE, max_pages=3)

    assert result.pages_rendered == 2
    assert result.pdf_page_count == 2
    assert result.note is None  # nothing was skipped


# --- 2. an image receipt is sent directly -----------------------------------------------------


def test_png_receipt_is_passed_through_byte_for_byte() -> None:
    original = image_bytes("PNG")
    result = prepare_images(original, PNG_MIME_TYPE)

    assert result.images[0]["bytes"] == original  # no needless re-encoding
    assert result.images[0]["mime_type"] == PNG_MIME_TYPE
    assert result.was_pdf is False
    assert result.pdf_page_count == 0
    assert result.note is None


def test_jpeg_receipt_is_passed_through_byte_for_byte() -> None:
    original = image_bytes("JPEG")
    result = prepare_images(original, JPEG_MIME_TYPE)

    assert result.images[0]["bytes"] == original
    assert result.images[0]["mime_type"] == JPEG_MIME_TYPE


@pytest.mark.parametrize("fmt", ["TIFF", "BMP", "GIF"])
def test_other_image_formats_are_converted_to_png(fmt: str) -> None:
    """Textract accepts TIFF; a vision model may not, so normalize rather than gamble."""
    result = prepare_images(image_bytes(fmt), None)

    assert result.images[0]["mime_type"] == PNG_MIME_TYPE
    assert result.images[0]["bytes"].startswith(PNG_SIGNATURE)


def test_oversized_image_is_downscaled_to_the_edge_cap() -> None:
    from PIL import Image

    result = prepare_images(image_bytes("PNG", size=(4000, 1000)), PNG_MIME_TYPE)

    with Image.open(io.BytesIO(result.images[0]["bytes"])) as sent:
        assert max(sent.size) <= 2000
        assert sent.size[0] > sent.size[1]  # aspect ratio preserved


def test_oversized_jpeg_stays_a_jpeg_when_resized() -> None:
    """Re-encoding a photograph as PNG can multiply its size; the payload cap is the point."""
    result = prepare_images(image_bytes("JPEG", size=(3000, 2000)), JPEG_MIME_TYPE)

    assert result.images[0]["mime_type"] == JPEG_MIME_TYPE
    assert result.images[0]["bytes"].startswith(JPEG_SIGNATURE)


# --- 3. invalid PDF / image fails gracefully --------------------------------------------------


def test_corrupt_pdf_degrades_instead_of_raising() -> None:
    result = prepare_images(b"%PDF-1.4 this is not really a pdf", PDF_MIME_TYPE)

    assert result.images == []
    assert result.was_pdf is True
    assert result.note is not None
    assert result.image_mime_type is None


def test_truncated_image_degrades_instead_of_raising() -> None:
    result = prepare_images(PNG_SIGNATURE + b"truncated garbage", PNG_MIME_TYPE)

    assert result.images == []
    assert result.note is not None


def test_unrecognized_bytes_degrade_instead_of_raising() -> None:
    result = prepare_images(b"\x00\x01\x02\x03 not a document at all", None)

    assert result.images == []
    assert result.note is not None


def test_empty_bytes_degrade_instead_of_raising() -> None:
    assert prepare_images(b"", PDF_MIME_TYPE).images == []
    assert prepare_images(None, PDF_MIME_TYPE).images == []


def test_non_image_non_pdf_type_is_reported_by_name() -> None:
    result = prepare_images(b"col_a,col_b\n1,2\n", "text/csv")

    assert result.images == []
    assert "text/csv" in (result.note or "")


def test_pdf_without_the_rasterizer_degrades_to_text_only(monkeypatch) -> None:
    """A host missing pypdfium2 loses the image, not the ability to classify."""
    monkeypatch.setattr(document_render, "_pdfium_module", lambda: None)
    assert is_pdf_render_available() is False

    result = prepare_images(pdf_bytes(), PDF_MIME_TYPE)

    assert result.images == []
    assert result.was_pdf is True
    assert "pypdfium2" in (result.note or "")


def test_image_without_pillow_still_passes_through_a_png(monkeypatch) -> None:
    monkeypatch.setattr(document_render, "_pillow_image_module", lambda: None)
    original = image_bytes("PNG")

    result = prepare_images(original, PNG_MIME_TYPE)

    assert result.images[0]["bytes"] == original
    assert result.images[0]["mime_type"] == PNG_MIME_TYPE


def test_exotic_image_without_pillow_degrades(monkeypatch) -> None:
    monkeypatch.setattr(document_render, "_pillow_image_module", lambda: None)

    result = prepare_images(image_bytes("TIFF"), "image/tiff")

    assert result.images == []
    assert "Pillow" in (result.note or "")


# --- 4. the declared MIME type is never trusted over the bytes --------------------------------


def test_pdf_declared_as_jpeg_is_still_detected_as_pdf() -> None:
    """The exact production hazard: mime_type comes from a browser header and S3 may echo nothing.

    Trusting it would send PDF bytes labelled ``image/jpeg`` — the original failure.
    """
    pytest.importorskip("pypdfium2")
    result = prepare_images(pdf_bytes(), JPEG_MIME_TYPE)

    assert result.source_mime_type == PDF_MIME_TYPE
    assert result.was_pdf is True
    assert result.images[0]["mime_type"] == PNG_MIME_TYPE
    assert result.images[0]["bytes"].startswith(PNG_SIGNATURE)


def test_png_with_no_declared_type_is_detected_from_its_bytes() -> None:
    result = prepare_images(image_bytes("PNG"), None)

    assert result.source_mime_type == PNG_MIME_TYPE
    assert result.images[0]["mime_type"] == PNG_MIME_TYPE


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [("PNG", PNG_MIME_TYPE), ("JPEG", JPEG_MIME_TYPE), ("GIF", "image/gif"), ("BMP", "image/bmp")],
)
def test_sniffing_identifies_real_files(fmt: str, expected: str) -> None:
    assert sniff_mime_type(image_bytes(fmt)) == expected


def test_sniffing_identifies_a_pdf() -> None:
    assert sniff_mime_type(pdf_bytes()) == PDF_MIME_TYPE


def test_sniffing_falls_back_to_the_declared_type_for_unknown_bytes() -> None:
    assert sniff_mime_type(b"unknown-bytes", "application/vnd.ms-excel") == "application/vnd.ms-excel"
    assert sniff_mime_type(b"unknown-bytes") is None


def test_heic_is_named_so_the_note_is_actionable() -> None:
    # Pillow has no HEIF support without a plugin; identifying it by name beats "unknown type".
    fake_heic = b"\x00\x00\x00\x20ftypheic" + b"\x00" * 16
    assert sniff_mime_type(fake_heic) == "image/heic"

    result = prepare_images(fake_heic, None)
    assert result.images == []
    assert result.note is not None
