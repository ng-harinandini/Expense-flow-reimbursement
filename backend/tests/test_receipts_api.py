"""Receipts API integration tests (T001 endpoints, Phase 1 refactor).

Runs without AWS: with ``TEXTRACT_ENABLED`` false and no S3 bucket, the services safe-skip to the
deterministic fallback path — the same behaviour local development relies on. What Phase 1 adds and
is verified here: the employee UUID link, the audit record, and the repository-backed queries.
"""

from __future__ import annotations

import io
import uuid

import pytest

from app.core.config import settings
from tests.conftest import SEED_EMPLOYEE_CODE, SEED_OTHER_EMPLOYEE_CODE, as_role

# 1x1 PNG — smallest valid image payload.
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


@pytest.fixture(autouse=True)
def offline_extraction(monkeypatch):
    """Force the credential-free fallback path so no AWS call is attempted."""
    monkeypatch.setattr(settings, "TEXTRACT_ENABLED", False, raising=False)
    monkeypatch.setattr(settings, "S3_BUCKET_NAME", None, raising=False)


def _upload(client, *, file_name: str = "receipt.png", content: bytes = PNG_BYTES):
    return client.post(
        "/api/receipts/upload",
        files={"file": (file_name, io.BytesIO(content), "image/png")},
    )


# --- upload ------------------------------------------------------------------


def test_upload_persists_the_receipt(client):
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    response = _upload(client)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["fileName"] == "receipt.png"
    assert body["contentType"] == "image/png"
    assert body["fileSizeBytes"] == len(PNG_BYTES)
    assert body["employeeId"] == SEED_EMPLOYEE_CODE
    assert body["extractionStatus"] in {"COMPLETED", "FAILED"}
    assert body["extractionSource"] == "fallback"
    assert "fields" in body and "lineItems" in body


def test_upload_links_the_employee_row(client, db_session, employee):
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    receipt_id = uuid.UUID(_upload(client).json()["id"])

    from app.models.receipt import Receipt

    stored = db_session.get(Receipt, receipt_id)
    # Both the external code (legacy filter) and the real foreign key.
    assert stored.employee_id == employee.employee_code
    assert stored.employee_ref_id == employee.id


def test_upload_is_audited(client):
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    receipt_id = _upload(client).json()["id"]

    as_role("auditor")
    trail = client.get(f"/api/audit-logs/Receipt/{receipt_id}").json()
    assert trail
    assert trail[0]["eventType"] == "RECEIPT_UPLOAD"
    assert trail[0]["role"] == "employee"


def test_empty_upload_rejected(client):
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    assert _upload(client, content=b"").status_code == 400


def test_upload_requires_the_employee_role(client):
    for role in ("manager", "finance", "admin", "auditor"):
        as_role(role)
        assert _upload(client).status_code == 403


def test_upload_requires_authentication(anon_client):
    assert _upload(anon_client).status_code == 401


def test_client_supplied_employee_id_is_ignored(client, db_session, employee):
    """The uploader is the authenticated employee, never a form field."""
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    response = client.post(
        "/api/receipts/upload",
        files={"file": ("r.png", io.BytesIO(PNG_BYTES), "image/png")},
        data={"employeeId": SEED_OTHER_EMPLOYEE_CODE},
    )
    assert response.status_code == 201
    assert response.json()["employeeId"] == SEED_EMPLOYEE_CODE


# --- list / get --------------------------------------------------------------


def test_employee_lists_only_their_own_receipts(client, db_session, other_employee):
    from app.models.receipt import ExtractionStatus, Receipt

    db_session.add(
        Receipt(
            file_name="theirs.png", extraction_status=ExtractionStatus.COMPLETED,
            employee_id=other_employee.employee_code, employee_ref_id=other_employee.id,
        )
    )
    db_session.flush()

    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    mine_id = _upload(client).json()["id"]

    body = client.get("/api/receipts").json()
    assert mine_id in {r["id"] for r in body}
    assert all(r["employeeId"] == SEED_EMPLOYEE_CODE for r in body)


def test_finance_can_list_all_receipts(client):
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    uploaded = _upload(client).json()["id"]

    as_role("finance")
    body = client.get("/api/receipts").json()
    assert uploaded in {r["id"] for r in body}


def test_receipt_list_paginates(client):
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    _upload(client)
    _upload(client)
    assert len(client.get("/api/receipts?limit=1").json()) == 1


def test_get_receipt_detail(client):
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    receipt_id = _upload(client).json()["id"]

    body = client.get(f"/api/receipts/{receipt_id}").json()
    assert body["id"] == receipt_id
    assert "rawTextract" in body
    assert "normalizedExtraction" in body


def test_malformed_receipt_id_returns_400(client):
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    assert client.get("/api/receipts/not-a-uuid").status_code == 400


def test_unknown_receipt_returns_404(client):
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    assert client.get(f"/api/receipts/{uuid.uuid4()}").status_code == 404


def test_employee_cannot_read_another_persons_receipt(client, db_session, other_employee):
    from app.models.receipt import ExtractionStatus, Receipt

    theirs = Receipt(
        file_name="theirs.png", extraction_status=ExtractionStatus.COMPLETED,
        employee_id=other_employee.employee_code, employee_ref_id=other_employee.id,
    )
    db_session.add(theirs)
    db_session.flush()

    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    # 404, not 403 — existence is not confirmed to a non-owner.
    assert client.get(f"/api/receipts/{theirs.id}").status_code == 404


# --- claim integration -------------------------------------------------------


def test_uploaded_receipt_can_back_a_claim_once(client):
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    receipt_id = _upload(client).json()["id"]

    body = {
        "category": "Meals",
        "amount": 18.0,
        "currency": "USD",
        "merchantVendor": "Sweetgreen SF",
        "receiptAttached": True,
        "receiptId": receipt_id,
    }
    first = client.post("/api/claims", json=body)
    assert first.status_code == 201
    assert first.json()["receiptId"] == receipt_id
    assert first.json()["attachments"]

    second = client.post("/api/claims", json={**body, "merchantVendor": "Other Vendor"})
    assert second.status_code == 409
    assert second.json()["code"] == "receipt_already_claimed"
