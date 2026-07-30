"""AI Knowledge Platform API tests (T004-M13).

Exercises the real HTTP surface end to end — through the app's own dependency graph
(``resolve_embedding_provider()``/``resolve_vector_store()``), not stubs — because this is
specifically the integration layer the Done Check asks to be proven: "upload -> list -> search ->
retrieve-context -> reindex -> delete round-trips in an integration test." Every other AI test
module (``test_ai_ingestion.py``, ``test_ai_knowledge.py``, ...) stubs the embedding/vector-store
pair instead; this module is the one place that deliberately does not, so at least one test proves
the wiring in ``app/ai/services/composition.py`` and ``app/core/deps.py`` actually holds together.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.ai.registry.flags import feature_flags
from tests.conftest import as_role

PREFIX = "/api/ai"

POLICY_TEXT = (
    "Conference Travel Reimbursement Policy\n\n"
    "Conference travel expenses must be submitted within thirty days of the event's end date. "
    "Meals during conference travel are reimbursed at actual cost up to the daily per-diem limit "
    "for the destination city, and alcohol is never a reimbursable expense under any circumstance."
)


def _upload(client: TestClient, *, title: str, text: str = POLICY_TEXT) -> dict:
    as_role("finance")
    response = client.post(
        f"{PREFIX}/knowledge/documents",
        files={"file": (f"{title}.txt", text.encode("utf-8"), "text/plain")},
        data={"title": title},
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# 1. Full round trip: upload -> list -> get -> search -> context -> reindex -> archive
# ---------------------------------------------------------------------------


def test_knowledge_document_round_trip(client: TestClient) -> None:
    title = f"Round Trip Policy {uuid.uuid4().hex[:8]}"

    uploaded = _upload(client, title=title)
    assert uploaded["status"] == "COMPLETED"
    assert uploaded["documentId"] is not None
    document_id = uploaded["documentId"]

    as_role("employee")
    listed = client.get(f"{PREFIX}/knowledge/documents")
    assert listed.status_code == 200
    assert any(d["id"] == document_id for d in listed.json())

    fetched = client.get(f"{PREFIX}/knowledge/documents/{document_id}")
    assert fetched.status_code == 200
    assert fetched.json()["title"] == title

    searched = client.post(f"{PREFIX}/knowledge/search", json={"text": "conference travel"})
    assert searched.status_code == 200
    assert searched.json()["chunks"]

    context = client.post(
        f"{PREFIX}/knowledge/context",
        json={"kind": "policy", "query": "conference travel per-diem limit"},
    )
    assert context.status_code == 200
    assert context.json()["chunkCount"] >= 1

    as_role("finance")
    reindexed = client.post(f"{PREFIX}/knowledge/documents/{document_id}/reindex")
    assert reindexed.status_code == 201, reindexed.text
    assert reindexed.json()["documentVersion"] == 2

    new_document_id = reindexed.json()["documentId"]
    archived = client.delete(f"{PREFIX}/knowledge/documents/{new_document_id}")
    assert archived.status_code == 200
    assert archived.json()["status"] == "ARCHIVED"

    as_role("employee")
    listed_after_archive = client.get(f"{PREFIX}/knowledge/documents")
    assert not any(d["id"] == new_document_id for d in listed_after_archive.json())


def test_archived_document_is_excluded_from_search_and_context(client: TestClient) -> None:
    """Regression test: archiving must make a document's content unretrievable everywhere, not
    only absent from the document list. Both retrieval legs
    (``KnowledgeDocumentRepository.superseded_ids()`` for the dense leg,
    ``KnowledgeChunkRepository._apply_filters()`` for the lexical leg) must exclude
    ``DocumentStatus.ARCHIVED`` exactly like they already exclude ``SUPERSEDED``."""
    marker = f"zyxvendorquark{uuid.uuid4().hex[:8]}"
    text = f"Distinctive Archive Test Policy\n\nThis document mentions {marker} exactly once."
    title = f"Archive Search Exclusion {uuid.uuid4().hex[:8]}"

    uploaded = _upload(client, title=title, text=text)
    document_id = uploaded["documentId"]

    before = client.post(f"{PREFIX}/knowledge/search", json={"text": marker})
    assert before.status_code == 200
    assert before.json()["chunks"], "sanity check: the document must be findable before archiving"

    as_role("finance")
    archived = client.delete(f"{PREFIX}/knowledge/documents/{document_id}")
    assert archived.status_code == 200

    after = client.post(f"{PREFIX}/knowledge/search", json={"text": marker})
    assert after.status_code == 200
    assert not after.json()["chunks"], "archived document content must not be searchable"

    context = client.post(
        f"{PREFIX}/knowledge/context", json={"kind": "policy", "query": marker}
    )
    assert context.status_code == 200
    assert context.json()["chunkCount"] == 0


def test_upload_oversized_document_returns_413(client: TestClient, monkeypatch) -> None:
    from app.ai.core.config import ai_settings

    monkeypatch.setattr(ai_settings, "MAX_DOCUMENT_BYTES", 10)
    as_role("finance")
    response = client.post(
        f"{PREFIX}/knowledge/documents",
        files={"file": ("big.txt", POLICY_TEXT.encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 413
    assert response.json()["code"] == "document_too_large"


# ---------------------------------------------------------------------------
# 2. Duplicate detection
# ---------------------------------------------------------------------------


def test_duplicate_scan_and_vendor_resolve_round_trip(client: TestClient) -> None:
    as_role("finance")
    payload = {
        "claimId": str(uuid.uuid4()),
        "employeeId": str(uuid.uuid4()),
        "merchantVendor": "AcmeTravelCo",
        "expenseDate": "2026-01-15",
        "amountUsd": "123.45",
        "currency": "USD",
    }
    scanned = client.post(f"{PREFIX}/duplicates/scan", json=payload)
    assert scanned.status_code == 201, scanned.text
    body = scanned.json()
    assert body["verdict"] == "NO_MATCH"
    assert body["claimId"] == payload["claimId"]

    resolved = client.get(f"{PREFIX}/duplicates/vendors/AcmeTravelCo")
    assert resolved.status_code == 200
    assert resolved.json()["canonicalName"]


# ---------------------------------------------------------------------------
# 3. Admin: prompt publish + flag override
# ---------------------------------------------------------------------------


def test_admin_prompt_publish(client: TestClient) -> None:
    as_role("admin")
    code = f"TEST_PROMPT_{uuid.uuid4().hex[:8]}"
    response = client.post(
        f"{PREFIX}/admin/prompts/{code}/publish",
        json={
            "name": "Test Prompt",
            "templateText": "Hello {{name}}.",
            "variables": ["name"],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["code"] == code
    assert body["version"] == 1


def test_admin_flag_override_round_trips(client: TestClient) -> None:
    as_role("admin")
    try:
        response = client.post(f"{PREFIX}/admin/flags/ai.rerank", json={"enabled": False})
        assert response.status_code == 200
        assert response.json() == {"flag": "ai.rerank", "enabled": False}
        assert feature_flags.is_enabled("ai.rerank") is False
    finally:
        feature_flags.set_override("ai.rerank", None)


# ---------------------------------------------------------------------------
# 4. Health
# ---------------------------------------------------------------------------


def test_health_is_public(anon_client: TestClient) -> None:
    response = anon_client.get(f"{PREFIX}/knowledge/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_metrics_requires_admin_or_auditor(client: TestClient) -> None:
    as_role("employee")
    assert client.get(f"{PREFIX}/knowledge/metrics").status_code == 403

    as_role("admin")
    response = client.get(f"{PREFIX}/knowledge/metrics")
    assert response.status_code == 200
    assert "knowledge" in response.json()
    assert "duplicateDetection" in response.json()


# ---------------------------------------------------------------------------
# 5. Every response carries X-Request-Id (RequestContextMiddleware, fully automatic)
# ---------------------------------------------------------------------------


def test_every_response_carries_request_id(anon_client: TestClient) -> None:
    response = anon_client.get(f"{PREFIX}/knowledge/health")
    assert "X-Request-Id" in response.headers


# ---------------------------------------------------------------------------
# 6. Unauthenticated -> 401
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method, path",
    [
        ("GET", "/knowledge/documents"),
        ("POST", "/knowledge/search"),
        ("GET", "/knowledge/metrics"),
        ("POST", "/duplicates/scan"),
    ],
)
def test_unauthenticated_requests_return_401(
    anon_client: TestClient, method: str, path: str
) -> None:
    response = anon_client.request(method, f"{PREFIX}{path}", json={})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# 7. Role gating, asserted per endpoint (all 13)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method, path, forbidden_role",
    [
        ("POST", "/knowledge/documents", "employee"),
        ("GET", "/knowledge/documents", None),
        ("GET", f"/knowledge/documents/{uuid.uuid4()}", None),
        ("POST", f"/knowledge/documents/{uuid.uuid4()}/reindex", "employee"),
        ("DELETE", f"/knowledge/documents/{uuid.uuid4()}", "employee"),
        ("POST", "/knowledge/search", None),
        ("POST", "/knowledge/context", None),
        ("POST", "/duplicates/scan", "employee"),
        ("GET", "/duplicates/vendors/Acme", "employee"),
        ("POST", "/admin/prompts/SOME_CODE/publish", "finance"),
        ("POST", "/admin/flags/ai.rerank", "finance"),
        ("GET", "/knowledge/metrics", "finance"),
    ],
)
def test_role_gating_per_endpoint(
    client: TestClient, method: str, path: str, forbidden_role: str | None
) -> None:
    """Every mutating/gated endpoint rejects a role outside its allowlist with 403.

    Routes with no ``forbidden_role`` (``None``) are open to any authenticated caller per the M13
    RBAC matrix ("search/retrieve = any authenticated") — asserted by simply not 403ing below.
    """
    if forbidden_role is None:
        as_role("employee")
    else:
        as_role(forbidden_role)
    response = client.request(method, f"{PREFIX}{path}", json={})
    if forbidden_role is None:
        assert response.status_code != 403
    else:
        assert response.status_code == 403
