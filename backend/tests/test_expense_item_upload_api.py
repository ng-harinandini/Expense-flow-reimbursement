"""``POST /expense-items/upload`` — the extraction-provider toggle, over real HTTP.

The endpoint creates no claim and no expense item, so what is being pinned is the response contract
the submission form fills itself in from, and the guarantee that switching engines cannot break it:

1. On the Bedrock engine, the response carries a category the form can pre-select.
2. On Textract (the default), the response is exactly what it was before the toggle existed.
3. Whichever engine runs, it is handed the bytes that were just uploaded — never asked to re-fetch
   what the request already holds.
4. An extractor that fails never fails the upload, and never loses the S3 audit record.

No AWS is contacted: S3 returns ``{"stored": False}`` and Textract returns its fallback stub when
unconfigured, neither raising, so the default path exercises itself offline.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from app.core import deps
from app.main import app
from app.models.audit import AuditLog
from app.models.enums import AuditAction
from conftest import SEED_EMPLOYEE_CODE, as_role
from tests.test_ai_document_classification import png_bytes

PNG = png_bytes()


def _upload(client, *, data: bytes = PNG, name: str = "receipt.png", content_type: str = "image/png",
            **form: str):
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    return client.post(
        "/api/expense-items/upload",
        files={"file": (name, data, content_type)},
        data=form or None,
    )


class _RecordingExtractor:
    """Stands in for whichever engine is configured, recording exactly what it was handed."""

    def __init__(self, **overrides: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        self._result: dict[str, Any] = {
            "source": "bedrock",
            "rawTextract": {"_provider": "bedrock"},
            "summary": {
                "vendorName": "La Piazza Trattoria",
                "transactionDate": "2026-06-25",
                "totalAmount": 336.96,
                "currency": "USD",
                "fields": [
                    {"fieldType": "TAX", "fieldLabel": "Tax", "fieldValue": "24.96",
                     "confidence": None}
                ],
            },
            "lineItems": [],
            "error": None,
            "confidenceScore": 0.91,
            "documentType": "restaurant receipt",
            "suggestedCategory": "Meals",
            "categoryConfidence": 0.91,
            "categoryFields": {"meal_type": "Lunch"},
        }
        self._result.update(overrides)

    def extract(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return dict(self._result)


class _ExplodingExtractor:
    def extract(self, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("extraction engine unavailable")


def _use(extractor: Optional[Any]) -> None:
    app.dependency_overrides[deps.get_receipt_extractor] = lambda: extractor


# --- 1. the Bedrock engine feeds the form a category --------------------------------------------


def test_upload_returns_the_category_the_extractor_read(client):
    _use(_RecordingExtractor())

    body = _upload(client).json()

    assert body["suggestedCategory"] == "Meals"
    assert body["suggestedCategoryConfidence"] == 0.91
    assert body["documentType"] == "restaurant receipt"
    assert body["categoryFields"] == {"meal_type": "Lunch"}


def test_the_standard_prefill_fields_are_unaffected_by_the_engine(client):
    _use(_RecordingExtractor())

    body = _upload(client).json()

    assert body["suggestedVendor"] == "La Piazza Trattoria"
    assert body["suggestedDate"] == "2026-06-25"
    assert body["suggestedAmount"] == 336.96
    assert body["suggestedCurrency"] == "USD"
    assert body["ocrSource"] == "bedrock"
    # Only `summary` is echoed back at submit time as ocrExtractedJson — the classification verdict
    # rides on the response but is deliberately not part of what gets persisted.
    assert "suggestedCategory" not in body["extraction"]


def test_an_extractor_that_suggests_nothing_falls_back_to_the_caller_s_hint(client):
    _use(_RecordingExtractor(suggestedCategory=None, categoryConfidence=None, documentType=None))

    body = _upload(client, categoryHint="Air Travel").json()

    assert body["suggestedCategory"] == "Air Travel"
    assert body["suggestedCategoryConfidence"] is None


def test_the_extractor_outranks_the_caller_s_hint(client):
    _use(_RecordingExtractor())

    body = _upload(client, categoryHint="Air Travel").json()

    # The hint is a client-supplied guess; the engine actually looked at the document.
    assert body["suggestedCategory"] == "Meals"


# --- 2. the default engine is unchanged ----------------------------------------------------------


def test_the_default_textract_path_behaves_exactly_as_before(client):
    """No override: whatever get_receipt_extractor builds from the default setting runs for real."""
    response = _upload(client)

    assert response.status_code == 200, response.text
    body = response.json()
    # Textract cannot suggest a category — that is the entire reason the Bedrock engine exists.
    assert body["suggestedCategory"] is None
    assert body["suggestedCategoryConfidence"] is None
    assert body["documentType"] is None
    assert body["categoryFields"] is None
    assert body["fileHash"] and body["fileName"] == "receipt.png"


def test_the_default_path_still_echoes_a_category_hint(client):
    body = _upload(client, categoryHint="  Meals  ").json()

    assert body["suggestedCategory"] == "Meals"


# --- 3. the engine gets the bytes the request is already holding ---------------------------------


def test_the_uploaded_bytes_are_handed_straight_to_the_extractor(client):
    extractor = _RecordingExtractor()
    _use(extractor)

    _upload(client, name="invoice.png")

    call = extractor.calls[0]
    assert call["data"] == PNG  # no re-download of what we just received
    assert call["mime_type"] == "image/png"
    assert call["file_name"] == "invoice.png"


# --- 4. extraction is advisory; the upload is not ------------------------------------------------


def test_an_extractor_failure_never_fails_the_upload(client):
    """Both engines degrade internally, so reaching the route's guard means one broke its contract.

    The file is already in S3 by then and the employee can type the fields in themselves — neither
    is worth losing to a 500.
    """
    _use(_ExplodingExtractor())

    response = _upload(client)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["fileHash"] and body["fileName"] == "receipt.png"
    assert body["ocrSource"] == "fallback"
    assert body["suggestedVendor"] is None
    assert body["suggestedCategory"] is None


def test_the_receipt_audit_row_survives_an_extractor_failure(client, db_session):
    """The S3 object exists by then, and an unaudited write to object storage is a compliance gap."""
    _use(_ExplodingExtractor())

    _upload(client)

    rows = db_session.query(AuditLog).filter(AuditLog.action == AuditAction.RECEIPT_UPLOAD).all()
    assert rows, "the upload must be audited whatever the extractor did"


def test_an_empty_file_is_rejected_before_any_engine_runs(client):
    extractor = _RecordingExtractor()
    _use(extractor)

    response = _upload(client, data=b"")

    assert response.status_code == 400
    assert extractor.calls == []
