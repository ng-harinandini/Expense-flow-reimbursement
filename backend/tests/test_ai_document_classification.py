"""Document classification: what a reviewer can trust without reopening the receipt.

These tests pin the promises requirement 10 asks for directly:

1. A confident, matching classification runs category-field extraction.
2. A mismatched category is flagged for review; the employee's own category is never touched by
   anything in this module (nothing here even accepts an ``ExpenseItem``).
3. A category the model invents is rejected — never persisted as ``suggested_category``.
4. Low confidence flags for review even when the category name matches.
5. A provider failure never raises — it degrades to a review flag.

No provider or database is contacted: a stub client replays canned responses (the same pattern as
``test_ai_rule_extraction.FakeProvider``), and the service-level tests use small hand-written fakes
for ``CategoryRepository``/``AIInferenceRepository`` instead of a real session.
"""

from __future__ import annotations

import io
import json
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

import pytest

from app.ai.classification.category_field_extractor import CategoryFieldExtractor
from app.ai.classification.document_classifier import DocumentCategoryClassifier
from app.ai.classification.service import DocumentClassificationService
from app.models.category import ExpenseCategory

VALID_CATEGORIES = ["Meals", "Air Travel", "Hotel / Lodging"]


class FakeProvider:
    """Bedrock-shaped stub: records prompts/images, replays canned responses."""

    def __init__(self, *responses: str, raise_on_call: Optional[Exception] = None) -> None:
        self._responses = list(responses) or ["{}"]
        self._raise_on_call = raise_on_call
        self.calls: list[dict[str, Any]] = []

    def generate_text(self, prompt: str, *, images=None, **kwargs) -> str:
        self.calls.append({"prompt": prompt, "images": images})
        if self._raise_on_call is not None:
            raise self._raise_on_call
        index = min(len(self.calls) - 1, len(self._responses) - 1)
        return self._responses[index]


def classify(response: str, *, employee_category: str = "Meals", min_confidence: float = 0.7):
    client = FakeProvider(response)
    classifier = DocumentCategoryClassifier(client=client)
    outcome = classifier.classify(
        ocr_text="Vendor: The Coffee House\nTotal: 42.50",
        images=None,
        valid_categories=VALID_CATEGORIES,
        employee_category=employee_category,
        min_confidence=min_confidence,
    )
    return outcome, client


def png_bytes(size: tuple[int, int] = (40, 30)) -> bytes:
    """A real PNG. Bogus byte strings no longer reach the model — document_render rejects them."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="PNG")
    return buffer.getvalue()


def pdf_bytes(pages: int = 1) -> bytes:
    """A real PDF — the input that used to fail the whole Bedrock request."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def response(**overrides: Any) -> str:
    payload = {
        "documentType": "restaurant receipt",
        "suggestedCategory": "Meals",
        "confidence": 0.92,
        "reasoning": None,
    }
    payload.update(overrides)
    return json.dumps(payload)


# --- 1. matching category, confident -----------------------------------------------------------


def test_matching_confident_category_is_not_flagged():
    outcome, _ = classify(response(), employee_category="Meals")

    assert outcome.suggested_category == "Meals"
    assert outcome.category_mismatch is False
    assert outcome.needs_review is False
    assert outcome.failed is False
    assert outcome.document_type == "restaurant receipt"


# --- 2. mismatched category ----------------------------------------------------------------------


def test_mismatched_category_is_flagged_and_employee_category_is_never_read_back():
    outcome, _ = classify(
        response(suggestedCategory="Air Travel", confidence=0.9), employee_category="Meals"
    )

    assert outcome.suggested_category == "Air Travel"  # a valid category, just not the employee's
    assert outcome.category_mismatch is True
    assert outcome.needs_review is True
    # Nothing about this outcome ever names the employee's own category as something to overwrite.
    assert not hasattr(outcome, "category")


# --- 3. invalid/unrecognized category from the model ---------------------------------------------


def test_invented_category_is_rejected_not_persisted():
    outcome, _ = classify(
        response(suggestedCategory="Alien Spaceship Fuel", confidence=0.99),
        employee_category="Meals",
    )

    assert outcome.suggested_category is None
    assert outcome.category_mismatch is True
    assert outcome.needs_review is True
    assert "Alien Spaceship Fuel" in (outcome.notes or "")


# --- 4. low confidence with a matching name -------------------------------------------------------


def test_low_confidence_flags_review_even_when_category_matches():
    outcome, _ = classify(
        response(confidence=0.4), employee_category="Meals", min_confidence=0.7
    )

    assert outcome.suggested_category == "Meals"
    assert outcome.category_mismatch is False
    assert outcome.needs_review is True


# --- 5. model/provider failure ---------------------------------------------------------------------


def test_provider_failure_never_raises_and_flags_review():
    client = FakeProvider(raise_on_call=TimeoutError("Bedrock timed out"))
    classifier = DocumentCategoryClassifier(client=client)

    outcome = classifier.classify(
        ocr_text="Vendor: The Coffee House",
        images=None,
        valid_categories=VALID_CATEGORIES,
        employee_category="Meals",
        min_confidence=0.7,
    )

    assert outcome.failed is True
    assert outcome.needs_review is True
    assert "Bedrock timed out" in (outcome.notes or "")


def test_unparseable_response_flags_review_without_raising():
    outcome, _ = classify("not json at all, sorry")

    assert outcome.failed is True
    assert outcome.needs_review is True


def test_confidence_percentage_is_normalized_to_0_1():
    outcome, _ = classify(response(confidence=92))  # model leaked a percentage, not a fraction
    assert outcome.confidence == Decimal("0.920")


def test_category_matching_is_case_and_whitespace_insensitive():
    outcome, _ = classify(
        response(suggestedCategory="  meals  "), employee_category="MEALS"
    )
    assert outcome.suggested_category == "Meals"
    assert outcome.category_mismatch is False


# --- category field extractor: data-type coercion -------------------------------------------------


FIELD_SCHEMA = [
    {"name": "flight_number", "label": "Flight Number", "data_type": "text"},
    {"name": "seat_class", "label": "Seat Class", "data_type": "enum", "options": ["Economy", "Business"]},
    {"name": "base_fare", "label": "Base Fare", "data_type": "number"},
    {"name": "departure_date", "label": "Departure Date", "data_type": "date"},
    {"name": "refundable", "label": "Refundable", "data_type": "boolean"},
]


def test_field_extractor_coerces_each_declared_data_type():
    client = FakeProvider(
        json.dumps(
            {
                "fields": {
                    "flight_number": "AI202",
                    "seat_class": "business",  # different casing than the declared option
                    "base_fare": "$1,250.00",
                    "departure_date": "12 October 2025",
                    "refundable": "true",
                }
            }
        )
    )
    extractor = CategoryFieldExtractor(client=client)
    outcome = extractor.extract(
        field_schema=FIELD_SCHEMA, ocr_text="some ocr text", images=None
    )

    assert outcome.failed is False
    assert outcome.fields["flight_number"] == "AI202"
    assert outcome.fields["seat_class"] == "Business"
    assert outcome.fields["base_fare"] == 1250.00
    assert outcome.fields["departure_date"] == "2025-10-12"
    assert outcome.fields["refundable"] is True


def test_field_extractor_drops_an_enum_value_outside_options():
    client = FakeProvider(json.dumps({"fields": {"seat_class": "First"}}))
    extractor = CategoryFieldExtractor(client=client)
    outcome = extractor.extract(
        field_schema=FIELD_SCHEMA, ocr_text="text", images=None
    )

    assert "seat_class" not in outcome.fields
    assert "seat_class" in (outcome.notes or "")


def test_field_extractor_never_invents_a_field_outside_the_schema():
    client = FakeProvider(json.dumps({"fields": {"flight_number": "AI202", "made_up_field": "x"}}))
    extractor = CategoryFieldExtractor(client=client)
    outcome = extractor.extract(
        field_schema=FIELD_SCHEMA, ocr_text="text", images=None
    )

    assert set(outcome.fields) == {"flight_number"}


# --- DocumentClassificationService: orchestration + edge cases -------------------------------------


def _category(
    name: str, *, code: str, is_common: bool = False, is_active: bool = True, custom_fields=None
) -> ExpenseCategory:
    return ExpenseCategory(
        id=uuid.uuid4(),
        code=code,
        name=name,
        is_common=is_common,
        is_active=is_active,
        custom_fields=custom_fields,
        display_order=100,
    )


class FakeCategoryRepository:
    def __init__(self, categories: list[ExpenseCategory]) -> None:
        self._categories = categories

    def list_all(self, *, include_inactive: bool = False):
        if include_inactive:
            return list(self._categories)
        return [c for c in self._categories if c.is_active]


class FakeAIInferenceRepository:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def record(self, **kwargs: Any) -> None:
        self.records.append(kwargs)


def _service_with(categories, monkeypatch, *responses: str, raise_on_call=None):
    service = DocumentClassificationService(FakeCategoryRepository(categories), FakeAIInferenceRepository())
    client = FakeProvider(*responses, raise_on_call=raise_on_call)
    monkeypatch.setattr(DocumentClassificationService, "_client", lambda self: client)
    return service, client


def test_no_receipt_at_all_is_a_silent_no_op(monkeypatch):
    service, client = _service_with([_category("Meals", code="MEALS")], monkeypatch)

    result = service.classify_and_extract(
        ocr_text=None,
        ocr_is_fallback=False,
        has_receipt=False,
        image_bytes=None,
        image_mime_type=None,
        employee_category="Meals",
        claim_id=None,
        expense_item_id=None,
        actor_sub=None,
    )

    assert result["categoryReviewRequired"] is False
    assert result["suggestedCategory"] is None
    assert client.calls == []  # no Bedrock call was made


def test_receipt_uploaded_but_unreadable_flags_review_without_calling_bedrock_uselessly(monkeypatch):
    service, client = _service_with([_category("Meals", code="MEALS")], monkeypatch)

    result = service.classify_and_extract(
        ocr_text=None,
        ocr_is_fallback=True,  # Textract fell back to its fabricated stub
        has_receipt=True,
        image_bytes=None,
        image_mime_type=None,
        employee_category="Meals",
        claim_id=None,
        expense_item_id=None,
        actor_sub=None,
    )

    assert result["categoryReviewRequired"] is True
    assert client.calls == []


def test_fallback_ocr_text_is_excluded_from_the_prompt_when_an_image_is_available(monkeypatch):
    service, client = _service_with(
        [_category("Meals", code="MEALS")], monkeypatch, response()
    )

    service.classify_and_extract(
        ocr_text="Vendor: Acme Merchant Cafe\nTotal: 42.50",  # the fabricated fallback stub
        ocr_is_fallback=True,
        has_receipt=True,
        image_bytes=png_bytes(),
        image_mime_type="image/jpeg",  # deliberately wrong: the bytes decide, not the header
        employee_category="Meals",
        claim_id=None,
        expense_item_id=None,
        actor_sub=None,
    )

    assert len(client.calls) == 1
    assert "Acme Merchant Cafe" not in client.calls[0]["prompt"]
    assert client.calls[0]["images"] is not None


def test_matching_confident_result_runs_field_extraction_merging_common_and_category_fields(monkeypatch):
    common = _category(
        "Common",
        code="COMMON",
        is_common=True,
        custom_fields=[{"name": "invoice_number", "label": "Invoice #", "data_type": "text"}],
    )
    meals = _category(
        "Meals",
        code="MEALS",
        custom_fields=[{"name": "attendee_count", "label": "Attendees", "data_type": "number"}],
    )
    field_response = json.dumps({"fields": {"invoice_number": "INV-1", "attendee_count": "3"}})
    service, client = _service_with(
        [common, meals], monkeypatch, response(), field_response
    )

    result = service.classify_and_extract(
        ocr_text="Vendor: The Coffee House\nTotal: 42.50",
        ocr_is_fallback=False,
        has_receipt=True,
        image_bytes=None,
        image_mime_type=None,
        employee_category="Meals",
        claim_id=None,
        expense_item_id=None,
        actor_sub=None,
    )

    assert result["categoryMismatch"] is False
    assert result["categoryReviewRequired"] is False
    assert result["extractedFields"] == {"invoice_number": "INV-1", "attendee_count": 3.0}
    assert len(client.calls) == 2  # classification, then field extraction


def test_mismatch_skips_field_extraction_entirely(monkeypatch):
    meals = _category("Meals", code="MEALS", custom_fields=[{"name": "x", "label": "X", "data_type": "text"}])
    air = _category("Air Travel", code="AIR", custom_fields=[{"name": "y", "label": "Y", "data_type": "text"}])
    service, client = _service_with(
        [meals, air], monkeypatch, response(suggestedCategory="Air Travel", confidence=0.95)
    )

    result = service.classify_and_extract(
        ocr_text="Vendor: The Coffee House\nTotal: 42.50",
        ocr_is_fallback=False,
        has_receipt=True,
        image_bytes=None,
        image_mime_type=None,
        employee_category="Meals",
        claim_id=None,
        expense_item_id=None,
        actor_sub=None,
    )

    assert result["categoryMismatch"] is True
    assert result["categoryReviewRequired"] is True
    assert result["extractedFields"] is None
    assert len(client.calls) == 1  # only the classification call, never field extraction


# --- 6. PDF receipts reach the model as images ----------------------------------------------------
# The production failure this section exists for: PDF bytes were base64'd into an image part
# unchanged, the model answered "cannot identify image file <_io.BytesIO ...>", and every PDF claim
# fell through to manual review with a null documentType.


def _classify_receipt(service, data: bytes, mime_type: Optional[str], **overrides: Any) -> dict:
    kwargs: dict[str, Any] = {
        "ocr_text": "Vendor: The Coffee House\nTotal: 42.50",
        "ocr_is_fallback": False,
        "has_receipt": True,
        "image_bytes": data,
        "image_mime_type": mime_type,
        "employee_category": "Meals",
        "claim_id": None,
        "expense_item_id": None,
        "actor_sub": None,
    }
    kwargs.update(overrides)
    return service.classify_and_extract(**kwargs)


def test_pdf_receipt_is_sent_to_bedrock_as_png_image_bytes(monkeypatch):
    """The regression test proper: valid image bytes on the wire, and a real verdict out."""
    pytest.importorskip("pypdfium2")
    service, client = _service_with([_category("Meals", code="MEALS")], monkeypatch, response())

    result = _classify_receipt(service, pdf_bytes(), "application/pdf")

    assert len(client.calls) == 1
    images = client.calls[0]["images"]
    assert images is not None and len(images) == 1
    assert images[0]["mime_type"] == "image/png"
    assert images[0]["bytes"].startswith(b"\x89PNG\r\n\x1a\n")
    assert b"%PDF-" not in images[0]["bytes"][:64]  # no raw PDF smuggled through

    # ...and the classification actually succeeded rather than degrading.
    assert result["documentType"] == "restaurant receipt"
    assert result["suggestedCategory"] == "Meals"
    assert result["categoryReviewRequired"] is False


def test_png_receipt_reaches_bedrock_untouched(monkeypatch):
    service, client = _service_with([_category("Meals", code="MEALS")], monkeypatch, response())
    original = png_bytes()

    _classify_receipt(service, original, "image/png")

    images = client.calls[0]["images"]
    assert images[0]["bytes"] == original
    assert images[0]["mime_type"] == "image/png"


def test_single_page_setting_still_sends_exactly_one_image(monkeypatch):
    """The cap can always be turned back down to one image per request, config-only.

    Multi-image is verified against the deployed Gemma model, but a slow or expensive deployment is
    a reason to send page 1 only, so that path must keep working.
    """
    pytest.importorskip("pypdfium2")
    from app.ai.core.config import ai_settings

    monkeypatch.setattr(ai_settings, "CLASSIFICATION_MAX_PDF_PAGES", 1)
    service, client = _service_with([_category("Meals", code="MEALS")], monkeypatch, response())

    result = _classify_receipt(service, pdf_bytes(pages=3), "application/pdf")

    assert len(client.calls[0]["images"]) == 1
    # The reviewer is told the rest of the document was not looked at.
    assert "3 pages" in (result["notes"] or "")


def test_pages_beyond_the_cap_are_reported_not_silently_dropped(monkeypatch):
    pytest.importorskip("pypdfium2")
    from app.ai.core.config import ai_settings

    monkeypatch.setattr(ai_settings, "CLASSIFICATION_MAX_PDF_PAGES", 3)
    service, client = _service_with([_category("Meals", code="MEALS")], monkeypatch, response())

    result = _classify_receipt(service, pdf_bytes(pages=6), "application/pdf")

    assert len(client.calls[0]["images"]) == 3
    assert "6 pages" in (result["notes"] or "")


def test_ocr_text_is_still_sent_alongside_the_rendered_pdf_page(monkeypatch):
    pytest.importorskip("pypdfium2")
    service, client = _service_with([_category("Meals", code="MEALS")], monkeypatch, response())

    _classify_receipt(service, pdf_bytes(), "application/pdf")

    # Both modalities, not one instead of the other.
    assert "The Coffee House" in client.calls[0]["prompt"]
    assert client.calls[0]["images"]


def test_unreadable_pdf_falls_back_to_text_only_classification(monkeypatch):
    """A damaged PDF costs the image, not the verdict — this is the configured degradation."""
    service, client = _service_with([_category("Meals", code="MEALS")], monkeypatch, response())

    result = _classify_receipt(service, b"%PDF-1.4 hopelessly corrupt", "application/pdf")

    assert len(client.calls) == 1
    assert client.calls[0]["images"] is None  # text-only call
    assert "The Coffee House" in client.calls[0]["prompt"]
    assert result["suggestedCategory"] == "Meals"  # still a real classification
    assert result["categoryReviewRequired"] is False
    assert result["notes"]  # and it says why the image was dropped


def test_missing_rasterizer_falls_back_to_text_only_classification(monkeypatch):
    from app.ai.classification import document_render

    monkeypatch.setattr(document_render, "_pdfium_module", lambda: None)
    service, client = _service_with([_category("Meals", code="MEALS")], monkeypatch, response())

    result = _classify_receipt(service, pdf_bytes(), "application/pdf")

    assert client.calls[0]["images"] is None
    assert result["suggestedCategory"] == "Meals"
    assert "pypdfium2" in (result["notes"] or "")


def test_unreadable_pdf_with_no_ocr_text_flags_review_without_calling_bedrock(monkeypatch):
    service, client = _service_with([_category("Meals", code="MEALS")], monkeypatch, response())

    result = _classify_receipt(
        service, b"%PDF-1.4 hopelessly corrupt", "application/pdf", ocr_text=None
    )

    assert client.calls == []  # nothing usable to send at all
    assert result["categoryReviewRequired"] is True
    assert result["suggestedCategory"] is None
    assert result["notes"]


def test_provider_failure_on_a_pdf_still_flags_review(monkeypatch):
    """Requirement 6: the safe fallback survives the new image path."""
    pytest.importorskip("pypdfium2")
    service, client = _service_with(
        [_category("Meals", code="MEALS")],
        monkeypatch,
        raise_on_call=RuntimeError("Provider 'bedrock' failed: ValidationException"),
    )

    result = _classify_receipt(service, pdf_bytes(), "application/pdf")

    assert result["categoryReviewRequired"] is True
    assert result["documentType"] is None
    assert result["suggestedCategory"] is None
    assert result["confidence"] is None
    assert "ValidationException" in (result["notes"] or "")


def test_include_image_flag_off_skips_conversion_entirely(monkeypatch):
    from app.ai.core.config import ai_settings

    monkeypatch.setattr(ai_settings, "CLASSIFICATION_INCLUDE_IMAGE", False)
    service, client = _service_with([_category("Meals", code="MEALS")], monkeypatch, response())

    result = _classify_receipt(service, pdf_bytes(), "application/pdf")

    assert client.calls[0]["images"] is None
    assert result["suggestedCategory"] == "Meals"


def test_max_pdf_pages_setting_is_honoured(monkeypatch):
    """Raising the cap is a config change; this proves the wiring reads it."""
    pytest.importorskip("pypdfium2")
    from app.ai.core.config import ai_settings

    monkeypatch.setattr(ai_settings, "CLASSIFICATION_MAX_PDF_PAGES", 3)
    service, client = _service_with([_category("Meals", code="MEALS")], monkeypatch, response())

    _classify_receipt(service, pdf_bytes(pages=4), "application/pdf")

    assert len(client.calls[0]["images"]) == 3


def test_field_extraction_receives_the_same_rendered_images(monkeypatch):
    pytest.importorskip("pypdfium2")
    meals = _category(
        "Meals",
        code="MEALS",
        custom_fields=[{"name": "attendee_count", "label": "Attendees", "data_type": "number"}],
    )
    service, client = _service_with(
        [meals], monkeypatch, response(), json.dumps({"fields": {"attendee_count": "3"}})
    )

    result = _classify_receipt(service, pdf_bytes(), "application/pdf")

    assert len(client.calls) == 2
    assert client.calls[1]["images"][0]["mime_type"] == "image/png"
    assert result["extractedFields"] == {"attendee_count": 3.0}


def test_inference_ledger_records_the_conversion(monkeypatch):
    pytest.importorskip("pypdfium2")
    service, client = _service_with([_category("Meals", code="MEALS")], monkeypatch, response())

    _classify_receipt(service, pdf_bytes(), "application/pdf")

    summary = service._ai_inference.records[0]["input_summary"]
    assert summary["hasImage"] is True
    assert summary["pdfConverted"] is True
    assert summary["sourceMimeType"] == "application/pdf"
    assert summary["imageCount"] == 1
