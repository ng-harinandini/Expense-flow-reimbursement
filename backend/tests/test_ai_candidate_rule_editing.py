"""PATCH /ai/knowledge/candidate-rules/{id}: Finance editing a candidate before approval.

There was previously no way to correct a candidate short of approving it as-is or dismissing it
outright. These tests pin the contract for the new edit endpoint: only fields present in the
request body change, editing is blocked once a candidate has left PENDING, and every edit is
audited the same way approve/dismiss already are.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.ai.models.knowledge import KnowledgeDocument
from app.models.audit import AuditLog
from app.models.candidate_rule import CandidatePolicyRule
from tests.conftest import as_role

_URL = "/api/ai/knowledge/candidate-rules"


def _make_document(db_session, **overrides) -> KnowledgeDocument:
    defaults = dict(
        source_type="upload",
        title="Expense Reimbursement Policy",
        checksum_sha256=uuid.uuid4().hex + uuid.uuid4().hex,
    )
    defaults.update(overrides)
    document = KnowledgeDocument(**defaults)
    db_session.add(document)
    db_session.flush()
    return document


def _make_candidate(db_session, **overrides) -> CandidatePolicyRule:
    defaults = dict(
        extraction_run_id=uuid.uuid4(),
        name="Domestic Meal Daily Cap",
        category="meals",
        description="Employees may claim up to USD 40 per day for meals.",
        currency="USD",
        expense_limit=Decimal("40"),
        conditions={"frequency": {"per": "day", "limit": 1}},
        source_text="All $40 / day $25 $25",
    )
    defaults.update(overrides)
    candidate = CandidatePolicyRule(**defaults)
    db_session.add(candidate)
    db_session.flush()
    return candidate


def test_finance_can_edit_only_the_fields_it_sends(client, db_session):
    candidate = _make_candidate(db_session)
    as_role("finance")

    resp = client.patch(
        f"{_URL}/{candidate.id}",
        json={"expenseLimit": 45, "reviewNotes": "Confirmed with Finance Director."},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["expenseLimit"] == 45.0
    assert body["reviewNotes"] == "Confirmed with Finance Director."
    # Untouched fields survive exactly as they were.
    assert body["description"] == "Employees may claim up to USD 40 per day for meals."
    assert body["category"] == "meals"
    assert body["currency"] == "USD"
    assert body["status"] == "PENDING"


def test_a_field_can_be_explicitly_cleared_to_null(client, db_session):
    candidate = _make_candidate(db_session, currency="USD")
    as_role("finance")

    resp = client.patch(f"{_URL}/{candidate.id}", json={"currency": None})

    assert resp.status_code == 200
    assert resp.json()["currency"] is None


def test_an_admin_can_also_edit(client, db_session):
    candidate = _make_candidate(db_session)
    as_role("admin")

    resp = client.patch(f"{_URL}/{candidate.id}", json={"name": "Renamed by Admin"})

    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed by Admin"


def test_a_non_finance_role_is_rejected(client, db_session):
    from tests.conftest import SEED_EMPLOYEE_CODE

    candidate = _make_candidate(db_session)
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)

    resp = client.patch(f"{_URL}/{candidate.id}", json={"name": "Should not work"})

    assert resp.status_code == 403


def test_an_unknown_category_is_rejected_with_422(client, db_session):
    candidate = _make_candidate(db_session)
    as_role("finance")

    resp = client.patch(f"{_URL}/{candidate.id}", json={"category": "airport_lounges"})

    assert resp.status_code == 422


def test_an_invalid_currency_is_rejected_with_422(client, db_session):
    candidate = _make_candidate(db_session)
    as_role("finance")

    resp = client.patch(f"{_URL}/{candidate.id}", json={"currency": "Dollars"})

    assert resp.status_code == 422


def test_an_empty_request_body_is_rejected(client, db_session):
    candidate = _make_candidate(db_session)
    as_role("finance")

    resp = client.patch(f"{_URL}/{candidate.id}", json={})

    assert resp.status_code == 422
    assert "No fields provided" in resp.json()["detail"]


def test_a_missing_candidate_is_404(client, db_session):
    as_role("finance")
    resp = client.patch(f"{_URL}/{uuid.uuid4()}", json={"name": "Ghost"})
    assert resp.status_code == 404


def test_an_already_approved_candidate_cannot_be_edited(client, db_session):
    candidate = _make_candidate(db_session, status="APPROVED")
    as_role("finance")

    resp = client.patch(f"{_URL}/{candidate.id}", json={"name": "Too late"})

    assert resp.status_code == 409
    assert "can no longer be edited" in resp.json()["detail"]


def test_a_dismissed_candidate_cannot_be_edited(client, db_session):
    candidate = _make_candidate(db_session, status="DISMISSED")
    as_role("finance")

    resp = client.patch(f"{_URL}/{candidate.id}", json={"name": "Too late"})

    assert resp.status_code == 409


def test_the_edit_is_audited(client, db_session):
    candidate = _make_candidate(db_session)
    as_role("finance")

    resp = client.patch(
        f"{_URL}/{candidate.id}",
        json={"expenseLimit": 999, "category": "travel"},
    )
    assert resp.status_code == 200

    entry = db_session.scalars(
        select(AuditLog)
        .where(AuditLog.action == "AI_CANDIDATE_EDITED")
        .where(AuditLog.entity_id == str(candidate.id))
    ).one()
    assert entry.after["expenseLimit"] == 999.0
    assert entry.after["category"] == "travel"
    assert entry.before["category"] == "meals"


# --- bulk edit: paste back a whole reviewed batch in one request -------------


def _bulk_url(document_id) -> str:
    return f"/api/ai/knowledge/documents/{document_id}/candidate-rules"


def test_bulk_edit_applies_every_candidate_in_one_transaction(client, db_session):
    document = _make_document(db_session)
    a = _make_candidate(db_session, document_id=document.id, name="Rule A")
    b = _make_candidate(db_session, document_id=document.id, name="Rule B", category="travel")
    as_role("finance")

    resp = client.patch(
        _bulk_url(document.id),
        json={
            "candidates": [
                {"id": str(a.id), "expenseLimit": 45},
                {"id": str(b.id), "reviewNotes": "Confirmed with Finance Director."},
            ]
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["updatedCount"] == 2
    assert body["skippedCount"] == 0
    by_id = {c["id"]: c for c in body["updated"]}
    assert by_id[str(a.id)]["expenseLimit"] == 45.0
    assert by_id[str(b.id)]["reviewNotes"] == "Confirmed with Finance Director."
    # Untouched fields on each candidate survive.
    assert by_id[str(b.id)]["category"] == "travel"


def test_bulk_edit_accepts_the_full_pasted_object_and_ignores_read_only_fields(client, db_session):
    """The whole point: paste back exactly what GET .../candidate-rules returned."""
    document = _make_document(db_session)
    candidate = _make_candidate(db_session, document_id=document.id)
    as_role("finance")

    full_object = client.get(f"/api/ai/knowledge/documents/{document.id}/candidate-rules").json()[0]
    full_object["expenseLimit"] = 55
    full_object["description"] = "Corrected by Finance."

    resp = client.patch(_bulk_url(document.id), json={"candidates": [full_object]})

    assert resp.status_code == 200
    assert resp.json()["updatedCount"] == 1
    updated = resp.json()["updated"][0]
    assert updated["expenseLimit"] == 55.0
    assert updated["description"] == "Corrected by Finance."
    assert updated["status"] == "PENDING"  # the pasted "status" field was ignored, not applied


def test_bulk_edit_reports_but_does_not_fail_on_unknown_wrong_document_or_non_pending(
    client, db_session
):
    document = _make_document(db_session)
    other_document = _make_document(db_session)
    editable = _make_candidate(db_session, document_id=document.id, name="Editable")
    wrong_doc = _make_candidate(db_session, document_id=other_document.id, name="Elsewhere")
    approved = _make_candidate(db_session, document_id=document.id, status="APPROVED")
    missing_id = uuid.uuid4()
    as_role("finance")

    resp = client.patch(
        _bulk_url(document.id),
        json={
            "candidates": [
                {"id": str(editable.id), "expenseLimit": 60},
                {"id": str(wrong_doc.id), "expenseLimit": 60},
                {"id": str(approved.id), "expenseLimit": 60},
                {"id": str(missing_id), "expenseLimit": 60},
            ]
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["updatedCount"] == 1
    assert body["skippedCount"] == 3
    reasons = {s["id"]: s["reason"] for s in body["skipped"]}
    assert reasons[str(wrong_doc.id)] == "wrong_document"
    assert reasons[str(approved.id)] == "not_pending"
    assert reasons[str(missing_id)] == "not_found"


def test_bulk_edit_rejects_an_empty_candidates_list(client, db_session):
    document = _make_document(db_session)
    as_role("finance")

    resp = client.patch(_bulk_url(document.id), json={"candidates": []})

    assert resp.status_code == 422


def test_bulk_edit_validates_every_item_before_changing_anything(client, db_session):
    """One bad item in the batch must not silently corrupt the others — nothing is a 4th option."""
    document = _make_document(db_session)
    good = _make_candidate(db_session, document_id=document.id)
    as_role("finance")

    resp = client.patch(
        _bulk_url(document.id),
        json={"candidates": [{"id": str(good.id), "category": "not-a-real-category"}]},
    )

    assert resp.status_code == 422
    db_session.refresh(good)
    assert good.category == "meals"  # untouched — the request never reached the DB layer


def test_a_missing_document_is_404_for_bulk_edit(client, db_session):
    as_role("finance")
    resp = client.patch(_bulk_url(uuid.uuid4()), json={"candidates": [{"id": str(uuid.uuid4())}]})
    assert resp.status_code == 404


def test_bulk_edit_is_audited_per_candidate(client, db_session):
    document = _make_document(db_session)
    a = _make_candidate(db_session, document_id=document.id, name="Rule A")
    b = _make_candidate(db_session, document_id=document.id, name="Rule B")
    as_role("finance")

    resp = client.patch(
        _bulk_url(document.id),
        json={
            "candidates": [
                {"id": str(a.id), "expenseLimit": 11},
                {"id": str(b.id), "expenseLimit": 22},
            ]
        },
    )
    assert resp.status_code == 200

    entries = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "AI_CANDIDATE_EDITED")
    ).all()
    assert len(entries) == 2
    assert all("(bulk update)" in e.details for e in entries)
