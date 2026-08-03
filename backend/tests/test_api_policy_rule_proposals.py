"""API tests for reviewing and approving extracted policy-rule proposals.

The two things that matter most here are not covered by the normalization unit tests: that the
whole HTTP path enforces RBAC (an employee must never see or act on a proposal), and that approving
one through the real endpoint — not just the pure ``build_ruleset_payload`` function — leaves every
other active rule untouched. The five categories seeded by migration ``0003`` are the live guardrail
for that: if approval regresses into a bare "submit only what changed," this is what would catch it.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.ai.core.enums import DocumentStatus, KnowledgeSourceType, ProposalStatus
from app.ai.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.ai.models.policy_proposal import PolicyRuleProposal, PolicyRuleProposalItem
from tests.conftest import as_role

TENANT_ID = "default"


def _make_document(session: Session, *, title: str = "expense-policy") -> KnowledgeDocument:
    document = KnowledgeDocument(
        tenant_id=TENANT_ID,
        source_type=KnowledgeSourceType.FINANCE_POLICY.value,
        title=title,
        checksum_sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        status=DocumentStatus.INDEXED,
        page_count=1,
        currency="USD",
        version=1,
    )
    session.add(document)
    session.flush()
    session.add(
        KnowledgeChunk(
            tenant_id=TENANT_ID, document_id=document.id, chunk_index=0,
            content="All $40 / day $25 $25", page_number=1, strategy="HYBRID",
        )
    )
    session.flush()
    return document


def _make_draft_proposal(
    session: Session, document: KnowledgeDocument, *, items: list[dict] | None = None,
) -> PolicyRuleProposal:
    proposal = PolicyRuleProposal(
        tenant_id=TENANT_ID,
        knowledge_document_id=document.id,
        document_version=document.version,
        document_title=document.title,
        status=ProposalStatus.DRAFT,
        llm_provider="fake",
        llm_model="fake-model",
        created_by_sub="sub-finance",
    )
    session.add(proposal)
    session.flush()

    rows = items or [
        {
            "category": "Meals",
            "grade_tier": "All Staff",
            "max_amount": 45,  # deliberately different from the seeded 40, to prove the change
            "auto_approve_limit": 25,
            "receipt_required_above": 25,
            "basis": "PER_DAY",
            "page_numbers": [1],
            "source_quote": "All $45 / day",
            "confidence": 0.95,
            "representable": True,
        },
    ]
    for index, row in enumerate(rows, start=1):
        session.add(
            PolicyRuleProposalItem(
                tenant_id=TENANT_ID, proposal_id=proposal.id, line_number=index, **row,
            )
        )
    proposal.rules_extracted = len(rows)
    proposal.rules_representable = sum(1 for r in rows if r.get("representable", True))
    session.flush()
    return proposal


@pytest.fixture
def draft_proposal(db_session: Session) -> PolicyRuleProposal:
    document = _make_document(db_session)
    return _make_draft_proposal(db_session, document)


# --- RBAC -----------------------------------------------------------------------


@pytest.mark.parametrize("role", ["employee", "manager"])
def test_employee_and_manager_cannot_view_proposals(
    client: TestClient, draft_proposal: PolicyRuleProposal, role: str,
) -> None:
    as_role(role)
    response = client.get(f"/api/policy-rule-proposals/{draft_proposal.id}")
    assert response.status_code == 403


def test_employee_cannot_approve(client: TestClient, draft_proposal: PolicyRuleProposal) -> None:
    as_role("employee")
    response = client.post(
        f"/api/policy-rule-proposals/{draft_proposal.id}/approve",
        json={"effectiveDate": date.today().isoformat()},
    )
    assert response.status_code == 403


def test_auditor_can_view_but_not_approve(
    client: TestClient, draft_proposal: PolicyRuleProposal,
) -> None:
    as_role("auditor")
    assert client.get(f"/api/policy-rule-proposals/{draft_proposal.id}").status_code == 200
    response = client.post(
        f"/api/policy-rule-proposals/{draft_proposal.id}/approve",
        json={"effectiveDate": date.today().isoformat()},
    )
    assert response.status_code == 403


# --- read paths ------------------------------------------------------------------


def test_get_proposal_returns_items_with_evidence(
    client: TestClient, draft_proposal: PolicyRuleProposal,
) -> None:
    as_role("finance")
    response = client.get(f"/api/policy-rule-proposals/{draft_proposal.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "DRAFT"
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["category"] == "Meals"
    assert item["pageNumbers"] == [1]
    assert item["sourceQuote"]


def test_get_unknown_proposal_is_404(client: TestClient) -> None:
    as_role("finance")
    response = client.get(f"/api/policy-rule-proposals/{uuid.uuid4()}")
    assert response.status_code == 404


def test_diff_shows_the_changed_category_and_no_retirements(
    client: TestClient, draft_proposal: PolicyRuleProposal,
) -> None:
    as_role("finance")
    response = client.get(f"/api/policy-rule-proposals/{draft_proposal.id}/diff")
    assert response.status_code == 200
    body = response.json()

    meals = next(c for c in body["changes"] if c["category"] == "Meals")
    assert meals["change"] == "changed"
    assert meals["proposed"]["maxAmountUSD"] == 45.0

    # Nothing else moves: the merge carries every untouched seeded category through unchanged.
    others = [c for c in body["changes"] if c["category"] != "Meals"]
    assert others, "the other seeded categories must appear in the diff"
    assert all(c["change"] == "unchanged" for c in others)
    assert body["wouldRetire"] == []


# --- approval --------------------------------------------------------------------


def test_approve_requires_an_effective_date(
    client: TestClient, draft_proposal: PolicyRuleProposal,
) -> None:
    as_role("finance")
    response = client.post(f"/api/policy-rule-proposals/{draft_proposal.id}/approve", json={})
    assert response.status_code == 422


def test_approve_publishes_and_leaves_the_other_seeded_rules_active(
    client: TestClient, db_session: Session, draft_proposal: PolicyRuleProposal,
) -> None:
    """The load-bearing regression test: approving one category must not retire the rest.

    If approval ever regressed into submitting only the proposal's own categories, this is exactly
    what ``_retire_absent`` would silently punish — every other seeded rule would deactivate with no
    error raised anywhere.
    """
    as_role("finance")
    response = client.post(
        f"/api/policy-rule-proposals/{draft_proposal.id}/approve",
        json={"effectiveDate": date.today().isoformat()},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "APPROVED"
    assert "Meals" in body["publishedCategories"]

    active = client.get("/api/policy-rules").json()
    active_by_category = {r["category"]: r for r in active}
    assert active_by_category["Meals"]["maxAmountUSD"] == 45.0
    # The four untouched seeded categories must still be active and untouched.
    for category in ("Ground Transport", "Flights", "Lodging", "Client Entertainment"):
        assert category in active_by_category, f"{category} was retired by an unrelated approval"


def test_approved_proposal_cannot_be_approved_again(
    client: TestClient, draft_proposal: PolicyRuleProposal,
) -> None:
    as_role("finance")
    first = client.post(
        f"/api/policy-rule-proposals/{draft_proposal.id}/approve",
        json={"effectiveDate": date.today().isoformat()},
    )
    assert first.status_code == 200

    second = client.post(
        f"/api/policy-rule-proposals/{draft_proposal.id}/approve",
        json={"effectiveDate": (date.today() + timedelta(days=1)).isoformat()},
    )
    assert second.status_code == 409


def test_a_proposal_with_nothing_representable_cannot_be_approved(
    client: TestClient, db_session: Session,
) -> None:
    document = _make_document(db_session, title="unrepresentable-only")
    proposal = _make_draft_proposal(
        db_session, document,
        items=[
            {
                "category": "Relocation", "grade_tier": "All Staff", "basis": "AGREEMENT",
                "limit_expression": "Per signed relocation agreement",
                "page_numbers": [4], "source_quote": "Per signed relocation agreement",
                "confidence": 0.8, "representable": False,
                "unrepresentable_reason": "No matching expense category.",
            },
        ],
    )
    as_role("finance")
    response = client.post(
        f"/api/policy-rule-proposals/{proposal.id}/approve",
        json={"effectiveDate": date.today().isoformat()},
    )
    assert response.status_code == 422


# --- rejection --------------------------------------------------------------------


def test_reject_requires_a_reason(
    client: TestClient, draft_proposal: PolicyRuleProposal,
) -> None:
    as_role("finance")
    response = client.post(f"/api/policy-rule-proposals/{draft_proposal.id}/reject", json={})
    assert response.status_code == 422


def test_reject_marks_the_proposal_rejected_and_touches_no_policy_rule(
    client: TestClient, draft_proposal: PolicyRuleProposal,
) -> None:
    as_role("finance")
    before = {r["category"]: r["maxAmountUSD"] for r in client.get("/api/policy-rules").json()}

    response = client.post(
        f"/api/policy-rule-proposals/{draft_proposal.id}/reject",
        json={"reason": "Extraction misread the auto-approve column."},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"

    after = {r["category"]: r["maxAmountUSD"] for r in client.get("/api/policy-rules").json()}
    assert before == after, "policy_rules must be completely untouched by a rejection"


def test_rejected_proposal_cannot_later_be_approved(
    client: TestClient, draft_proposal: PolicyRuleProposal,
) -> None:
    as_role("finance")
    client.post(
        f"/api/policy-rule-proposals/{draft_proposal.id}/reject",
        json={"reason": "Not accurate."},
    )
    response = client.post(
        f"/api/policy-rule-proposals/{draft_proposal.id}/approve",
        json={"effectiveDate": date.today().isoformat()},
    )
    assert response.status_code == 409
