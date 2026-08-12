"""The Bedrock receipt extractor: a drop-in replacement for Textract that also reads a category.

The promises pinned here:

1. Its output satisfies the Textract contract — every downstream normalizer accepts it unchanged.
   This is what makes the provider toggle a toggle rather than a fork of the upload path.
2. The prompt is built from the *database*, so an admin adding or renaming a category reaches the
   model with no code deploy and no prompt edit.
3. The COMMON row supplies shared fields but is never offered as a selectable category.
4. A category the model invents is rejected, not passed to the form.
5. Category-field values are coerced against the declared ``data_type``, never guessed.
6. A PDF is rasterized before being sent — a vision model cannot read a PDF.
7. Every failure degrades to the same empty result Textract returns when disabled, and never
   silently reaches for Textract instead (the toggle is strict).

No provider and no database are contacted: a stub client replays canned JSON, and the repositories
are the same hand-written fakes ``test_ai_document_classification`` uses.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.ai.extraction.receipt_extractor import BedrockReceiptExtractor
from app.services import receipt_extraction
from tests.test_ai_document_classification import (
    FakeAIInferenceRepository,
    FakeCategoryRepository,
    FakeProvider,
    _category,
    pdf_bytes,
    png_bytes,
)

COMMON_FIELDS = [
    {"name": "invoice_date", "label": "Invoice Date", "data_type": "date", "required": True},
    {"name": "currency", "label": "Currency", "data_type": "text", "required": True},
]
MEALS_FIELDS = [
    {"name": "meal_type", "label": "Meal Type", "data_type": "enum",
     "options": ["Breakfast", "Lunch", "Dinner"]},
    {"name": "number_of_persons", "label": "Number of Persons", "data_type": "number"},
]
AIR_FIELDS = [{"name": "travel_route", "label": "Travel Route", "data_type": "text"}]


def _categories() -> list:
    return [
        _category("Common Fields", code="COMMON", is_common=True, custom_fields=COMMON_FIELDS),
        _category("Meals", code="MEALS", custom_fields=MEALS_FIELDS),
        _category("Air Travel", code="AIR", custom_fields=AIR_FIELDS),
    ]


def _extractor(*responses: str, categories=None, raise_on_call=None):
    client = FakeProvider(*responses, raise_on_call=raise_on_call)
    extractor = BedrockReceiptExtractor(
        FakeCategoryRepository(categories if categories is not None else _categories()),
        FakeAIInferenceRepository(),
        client=client,
    )
    return extractor, client


def response(**overrides: Any) -> str:
    payload = {
        "documentType": "restaurant receipt",
        "suggestedCategory": "Meals",
        "confidence": 0.91,
        "vendorName": "La Piazza Trattoria",
        "transactionDate": "2026-06-25",
        "totalAmount": 336.96,
        "subtotalAmount": 312.00,
        "taxAmount": 24.96,
        "currency": "usd",
        "lineItems": [{"description": "Set lunch", "quantity": 2, "unitPrice": 156.0, "amount": 312.0}],
        "categoryFields": {"invoice_date": "June 25, 2026", "meal_type": "Lunch"},
    }
    payload.update(overrides)
    return json.dumps(payload)


# --- 1. the Textract contract ------------------------------------------------------------------


def test_output_is_shaped_exactly_like_textracts():
    extractor, _ = _extractor(response())

    result = extractor.extract(data=png_bytes(), mime_type="image/png", file_name="r.png")

    assert result["source"] == "bedrock"
    assert result["error"] is None
    assert set(result["summary"]) == {
        "vendorName", "transactionDate", "totalAmount", "currency", "fields"
    }
    assert result["summary"]["vendorName"] == "La Piazza Trattoria"
    assert result["summary"]["transactionDate"] == "2026-06-25"
    assert result["summary"]["totalAmount"] == 336.96
    assert result["summary"]["currency"] == "USD"  # normalized, as Textract's is


def test_every_downstream_normalizer_accepts_it_unchanged():
    """The load-bearing test: this is why nothing downstream had to change for the new engine."""
    extractor, _ = _extractor(response())

    result = extractor.extract(data=png_bytes(), mime_type="image/png", file_name="r.png")

    assert receipt_extraction.summarize_extraction(result) == {
        "vendor": "La Piazza Trattoria",
        "transactionDate": "2026-06-25",
        "totalAmount": 336.96,
        "currency": "USD",
    }
    # No per-field confidences exist for a single-call model, so the top-level 0-1 score is the one
    # normalize_confidence rescales — the documented escape hatch for a non-Textract extractor.
    assert receipt_extraction.normalize_confidence(result) == {"overall": 91.0}
    # Never raises on the shape, whatever it currently manages to pull out of it.
    assert isinstance(receipt_extraction.flatten_extraction_for_prompt(result), str)


def test_the_frontends_tax_field_is_present_by_the_name_it_looks_for():
    extractor, _ = _extractor(response())

    result = extractor.extract(data=png_bytes(), mime_type="image/png", file_name="r.png")

    by_type = {f["fieldType"]: f["fieldValue"] for f in result["summary"]["fields"]}
    assert by_type["TAX"] == "24.96"
    assert by_type["SUBTOTAL"] == "312.0"
    assert by_type["TOTAL"] == "336.96"


def test_a_field_the_document_did_not_state_is_omitted_not_emitted_as_null():
    extractor, _ = _extractor(response(taxAmount=None, subtotalAmount=None))

    result = extractor.extract(data=png_bytes(), mime_type="image/png", file_name="r.png")

    assert {f["fieldType"] for f in result["summary"]["fields"]} == {
        "VENDOR_NAME", "INVOICE_RECEIPT_DATE", "TOTAL"
    }


def test_the_classification_verdict_rides_outside_summary_so_it_is_never_persisted():
    extractor, _ = _extractor(response())

    result = extractor.extract(data=png_bytes(), mime_type="image/png", file_name="r.png")

    # `summary` is the only part the upload response persists as ocrExtractedJson. Submission
    # re-derives its own verdict, so the upload-time guess must not masquerade as stored truth.
    assert "suggestedCategory" not in result["summary"]
    assert result["suggestedCategory"] == "Meals"
    assert result["documentType"] == "restaurant receipt"
    assert result["categoryConfidence"] == 0.91


# --- 2. the prompt comes from the database ------------------------------------------------------


def test_the_prompt_lists_the_databases_categories_and_their_own_fields():
    extractor, client = _extractor(response())

    extractor.extract(data=png_bytes(), mime_type="image/png", file_name="r.png")

    prompt = client.calls[0]["prompt"]
    assert "Meals" in prompt and "Air Travel" in prompt
    assert "meal_type" in prompt and "travel_route" in prompt
    assert "options=['Breakfast', 'Lunch', 'Dinner']" in prompt


def test_a_category_an_admin_adds_reaches_the_model_with_no_code_change():
    categories = _categories() + [
        _category("Drone Insurance", code="DRONE",
                  custom_fields=[{"name": "policy_no", "label": "Policy", "data_type": "text"}])
    ]
    extractor, client = _extractor(response(suggestedCategory="Drone Insurance"),
                                   categories=categories)

    result = extractor.extract(data=png_bytes(), mime_type="image/png", file_name="r.png")

    assert "Drone Insurance" in client.calls[0]["prompt"]
    assert "policy_no" in client.calls[0]["prompt"]
    assert result["suggestedCategory"] == "Drone Insurance"


def test_an_inactive_category_is_never_offered():
    categories = _categories() + [_category("Retired", code="OLD", is_active=False)]
    extractor, client = _extractor(response(), categories=categories)

    extractor.extract(data=png_bytes(), mime_type="image/png", file_name="r.png")

    assert "Retired" not in client.calls[0]["prompt"]


# --- 3. the COMMON bucket ------------------------------------------------------------------------


def test_common_fields_are_always_requested_but_common_is_not_a_selectable_category():
    extractor, client = _extractor(response())

    result = extractor.extract(data=png_bytes(), mime_type="image/png", file_name="r.png")

    prompt = client.calls[0]["prompt"]
    assert "invoice_date" in prompt  # the COMMON row's field is asked for
    assert "* Common Fields" not in prompt  # but it is not offered as a category
    assert result["categoryFields"]["invoice_date"] == "2026-06-25"


def test_a_model_that_answers_common_fields_alone_still_returns_them():
    extractor, _ = _extractor(response(suggestedCategory=None,
                                       categoryFields={"invoice_date": "2026-06-25"}))

    result = extractor.extract(data=png_bytes(), mime_type="image/png", file_name="r.png")

    assert result["suggestedCategory"] is None
    assert result["categoryFields"] == {"invoice_date": "2026-06-25"}


# --- 4. invented categories ----------------------------------------------------------------------


def test_a_category_the_model_invents_is_rejected():
    extractor, _ = _extractor(response(suggestedCategory="Alien Spaceship Fuel"))

    result = extractor.extract(data=png_bytes(), mime_type="image/png", file_name="r.png")

    # The form would otherwise offer an option that does not exist, and the submitted claim would
    # carry a category no policy ruleset has anything to say about.
    assert result["suggestedCategory"] is None


def test_a_category_is_matched_case_insensitively_and_returned_canonically():
    extractor, _ = _extractor(response(suggestedCategory="  mEaLs  "))

    result = extractor.extract(data=png_bytes(), mime_type="image/png", file_name="r.png")

    assert result["suggestedCategory"] == "Meals"


# --- 5. category-field coercion ------------------------------------------------------------------


def test_category_fields_are_coerced_by_their_declared_data_type():
    extractor, _ = _extractor(
        response(categoryFields={"invoice_date": "25 June 2026", "number_of_persons": "$4"})
    )

    result = extractor.extract(data=png_bytes(), mime_type="image/png", file_name="r.png")

    assert result["categoryFields"]["invoice_date"] == "2026-06-25"
    assert result["categoryFields"]["number_of_persons"] == 4.0


def test_a_value_outside_an_enums_options_is_dropped_not_guessed():
    extractor, _ = _extractor(response(categoryFields={"meal_type": "Brunch"}))

    result = extractor.extract(data=png_bytes(), mime_type="image/png", file_name="r.png")

    assert "meal_type" not in (result["categoryFields"] or {})


def test_fields_belonging_to_a_category_the_model_did_not_choose_are_dropped():
    extractor, _ = _extractor(
        response(suggestedCategory="Meals",
                 categoryFields={"meal_type": "Lunch", "travel_route": "HYD-DEL"})
    )

    result = extractor.extract(data=png_bytes(), mime_type="image/png", file_name="r.png")

    assert result["categoryFields"] == {"meal_type": "Lunch"}


def test_an_invented_field_name_is_never_returned():
    extractor, _ = _extractor(response(categoryFields={"meal_type": "Lunch", "made_up": "x"}))

    result = extractor.extract(data=png_bytes(), mime_type="image/png", file_name="r.png")

    assert "made_up" not in (result["categoryFields"] or {})


# --- 6. PDFs -------------------------------------------------------------------------------------


def test_a_pdf_is_rasterized_before_being_sent():
    extractor, client = _extractor(response())

    result = extractor.extract(
        data=pdf_bytes(1), mime_type="application/pdf", file_name="invoice.pdf"
    )

    images = client.calls[0]["images"]
    assert images and all(i["mime_type"] == "image/png" for i in images)
    assert result["source"] == "bedrock"


def test_the_declared_mime_type_does_not_override_the_real_one():
    """Browsers and S3 both lie about content types; magic bytes decide."""
    extractor, client = _extractor(response())

    extractor.extract(data=pdf_bytes(1), mime_type="image/png", file_name="mislabelled.png")

    assert client.calls[0]["images"][0]["mime_type"] == "image/png"  # rendered, not passed through
    assert len(client.calls[0]["images"]) == 1


# --- 7. strict degradation -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, why",
    [
        ({"raise_on_call": RuntimeError("bedrock exploded")}, "provider failure"),
        ({}, "unparseable response"),
    ],
)
def test_any_failure_degrades_to_the_empty_result_textract_returns_when_disabled(kwargs, why):
    responses = ("not json at all",) if not kwargs else ()
    extractor, _ = _extractor(*responses, **kwargs)

    result = extractor.extract(data=png_bytes(), mime_type="image/png", file_name="r.png")

    assert result["source"] == "fallback", why
    assert result["summary"]["totalAmount"] is None
    assert result["summary"]["fields"] == []
    assert result["lineItems"] == []
    # The strict half of the toggle: a bedrock failure must not silently produce a Textract result.
    # A deployment that switched providers should be able to see that it switched.
    assert result.get("suggestedCategory") is None
    assert receipt_extraction.summarize_extraction(result)["vendor"] is None


def test_an_undecodable_document_never_reaches_the_model():
    extractor, client = _extractor(response())

    result = extractor.extract(data=b"not an image", mime_type="image/png", file_name="junk.png")

    assert result["source"] == "fallback"
    assert client.calls == []  # nothing to look at, so nothing was paid for


def test_no_configured_categories_is_a_fallback_not_a_crash():
    extractor, client = _extractor(
        response(), categories=[_category("Common Fields", code="COMMON", is_common=True)]
    )

    result = extractor.extract(data=png_bytes(), mime_type="image/png", file_name="r.png")

    assert result["source"] == "fallback"
    assert client.calls == []


def test_an_empty_upload_is_a_fallback():
    extractor, client = _extractor(response())

    result = extractor.extract(data=b"", mime_type="image/png", file_name="empty.png")

    assert result["source"] == "fallback"
    assert client.calls == []


# --- the ledger ----------------------------------------------------------------------------------


def test_the_call_is_recorded_against_no_claim_and_no_item():
    ledger = FakeAIInferenceRepository()
    client = FakeProvider(response())
    extractor = BedrockReceiptExtractor(FakeCategoryRepository(_categories()), ledger, client=client)

    extractor.extract(data=png_bytes(), mime_type="image/png", file_name="r.png")

    assert len(ledger.records) == 1
    entry = ledger.records[0]
    assert entry["operation"] == "receipt_extraction"
    # Neither exists yet at upload time; both columns are nullable for exactly this case.
    assert entry["claim_id"] is None and entry["expense_item_id"] is None
    assert entry["output_summary"]["suggestedCategory"] == "Meals"


def test_a_provider_failure_is_recorded_too():
    ledger = FakeAIInferenceRepository()
    client = FakeProvider(raise_on_call=RuntimeError("no model access"))
    extractor = BedrockReceiptExtractor(FakeCategoryRepository(_categories()), ledger, client=client)

    extractor.extract(data=png_bytes(), mime_type="image/png", file_name="r.png")

    assert ledger.records and "no model access" in ledger.records[0]["error_message"]
