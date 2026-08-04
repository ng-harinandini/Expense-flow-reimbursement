"""Claims API integration tests — real routes, real services, real database.

Two things are being verified together: that the HTTP contract the existing frontend depends on is
unchanged, and that domain failures surface as the right status codes without any route catching
them by hand.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from app.models.enums import ClaimStatus
from tests.conftest import (
    SEED_EMPLOYEE_CODE,
    SEED_MANAGER_CODE,
    SEED_OTHER_EMPLOYEE_CODE,
    as_role,
)

# The exact keys the frontend's ExpenseClaim interface reads.
REQUIRED_CLAIM_KEYS = {
    "id", "claimNumber", "employeeId", "employeeName", "employeeGrade",
    "expenseDate", "submissionDate", "category", "subCategory", "amount", "currency",
    "amountUSD", "merchantVendor", "purposeDescription", "receiptAttached",
    "policyValidation", "fraudScreening", "status", "workflowHistory", "comments",
}


def _submit(client, **overrides) -> dict:
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    body = {
        "category": "Meals",
        "subCategory": "Team Lunch",
        "amount": 22.50,
        "currency": "USD",
        "merchantVendor": f"Vendor {uuid.uuid4().hex[:8]}",
        "expenseDate": (date.today() - timedelta(days=1)).isoformat(),
        "purposeDescription": "Team sync lunch.",
        "receiptAttached": True,
    }
    body.update(overrides)
    response = client.post("/api/claims", json=body)
    assert response.status_code == 201, response.text
    return response.json()


# --- contract ----------------------------------------------------------------


def test_create_claim_returns_the_legacy_shape(client):
    claim = _submit(client)
    assert REQUIRED_CLAIM_KEYS <= set(claim)
    assert claim["employeeId"] == SEED_EMPLOYEE_CODE
    assert claim["employeeName"] == "Test Employee"
    assert claim["employeeGrade"] == "L3"
    assert claim["status"] == "Auto_Approved"
    assert claim["amountUSD"] == 22.50


def test_workflow_history_entries_keep_their_field_names(client):
    claim = _submit(client)
    step = claim["workflowHistory"][0]
    assert set(step) >= {"timestamp", "actorName", "actorRole", "stepName", "action", "status"}


def test_fraud_screening_block_keeps_its_field_names(client):
    claim = _submit(client)
    screening = claim["fraudScreening"]
    assert set(screening) >= {
        "riskScore", "isFlagged", "riskLevel", "flags", "rationale", "recommendedAction"
    }


def test_policy_validation_block_keeps_its_field_names(client):
    claim = _submit(client)
    report = claim["policyValidation"]
    assert set(report) >= {
        "overallPassed", "requiresManualReview", "maxLimitAllowed", "autoApproveLimit",
        "checks", "reasoningSummary",
    }


def test_list_claims_returns_a_bare_array(client):
    """The frontend does ``res.json()`` straight into an array — no envelope."""
    _submit(client)
    response = client.get("/api/claims")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_response_carries_correlation_headers(client):
    response = client.get("/api/claims")
    assert response.headers["X-Request-Id"]
    assert response.headers["X-Correlation-Id"]


def test_supplied_correlation_id_is_echoed(client):
    response = client.get("/api/claims", headers={"X-Correlation-Id": "trace-42"})
    assert response.headers["X-Correlation-Id"] == "trace-42"


# --- authentication / authorization ------------------------------------------


def test_unauthenticated_requests_are_rejected(anon_client):
    assert anon_client.get("/api/claims").status_code == 401
    assert anon_client.post("/api/claims", json={}).status_code == 401


def test_only_employees_can_create_claims(client):
    as_role("manager")
    assert client.post("/api/claims", json={"amount": 10, "merchantVendor": "X"}).status_code == 403


def test_owner_binding_ignores_a_forged_employee_id(client):
    """Passing someone else's employeeId must not file a claim against them."""
    claim = _submit(client, employeeId=SEED_OTHER_EMPLOYEE_CODE)
    assert claim["employeeId"] == SEED_EMPLOYEE_CODE


def test_employee_listing_is_scoped_to_the_caller(client, make_claim, other_employee):
    make_claim(owner=other_employee)
    mine = _submit(client)

    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    body = client.get("/api/claims").json()
    assert mine["id"] in {c["id"] for c in body}
    assert all(c["employeeId"] == SEED_EMPLOYEE_CODE for c in body)


def test_employee_cannot_read_another_persons_claim(client, make_claim, other_employee):
    theirs = make_claim(owner=other_employee)
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    assert client.get(f"/api/claims/{theirs.id}").status_code == 404


def test_manager_can_read_any_claim(client, make_claim, other_employee):
    theirs = make_claim(owner=other_employee)
    as_role("manager", employee_id=SEED_MANAGER_CODE)
    assert client.get(f"/api/claims/{theirs.id}").status_code == 200


def test_internal_comments_hidden_from_the_claim_owner(client, make_claim):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)

    as_role("manager", employee_id=SEED_MANAGER_CODE)
    response = client.post(
        f"/api/claims/{claim.claim_number}/comments",
        json={"text": "Escalate quietly", "isInternal": True},
    )
    assert response.status_code == 201

    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    owner_view = client.get(f"/api/claims/{claim.claim_number}").json()
    assert all(comment["text"] != "Escalate quietly" for comment in owner_view["comments"])

    as_role("manager", employee_id=SEED_MANAGER_CODE)
    reviewer_view = client.get(f"/api/claims/{claim.claim_number}").json()
    assert any(comment["text"] == "Escalate quietly" for comment in reviewer_view["comments"])


# --- filters -----------------------------------------------------------------


def test_filter_by_status_and_category(client, make_claim):
    lodging = make_claim(ClaimStatus.MANAGER_REVIEW, category="Lodging")
    as_role("manager", employee_id=SEED_MANAGER_CODE)

    by_status = client.get("/api/claims?status=Manager_Review").json()
    assert lodging.id.__str__() in {c["id"] for c in by_status}
    assert all(c["status"] == "Manager_Review" for c in by_status)

    by_category = client.get("/api/claims?category=Lodging").json()
    assert all(c["category"] == "Lodging" for c in by_category)


def test_canonical_status_alias_accepted_in_filters(client, make_claim):
    """``Reimbursed`` is the spec spelling of the wire value ``Disbursed``."""
    reimbursed = make_claim(ClaimStatus.REIMBURSED)
    as_role("finance")
    body = client.get("/api/claims?status=Reimbursed").json()
    assert str(reimbursed.id) in {c["id"] for c in body}


def test_invalid_status_filter_returns_422(client):
    as_role("manager", employee_id=SEED_MANAGER_CODE)
    response = client.get("/api/claims?status=Teleported")
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_pagination_limits_are_enforced(client):
    as_role("manager", employee_id=SEED_MANAGER_CODE)
    assert client.get("/api/claims?limit=0").status_code == 422
    assert client.get("/api/claims?limit=5000").status_code == 422


# --- request validation ------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount": 0},
        {"amount": -10},
        {"currency": "DOLLARS"},
        {"merchantVendor": ""},
        {"expenseDate": (date.today() + timedelta(days=10)).isoformat()},
        {"amount": "not-a-number"},
    ],
)
def test_malformed_bodies_are_rejected_with_422(client, overrides):
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    body = {
        "amount": 10.0,
        "merchantVendor": "Vendor",
        "expenseDate": (date.today() - timedelta(days=1)).isoformat(),
    }
    body.update(overrides)
    assert client.post("/api/claims", json=body).status_code == 422


def test_missing_amount_is_rejected(client):
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    assert client.post("/api/claims", json={"merchantVendor": "Vendor"}).status_code == 422


# --- domain errors -> status codes -------------------------------------------


def test_duplicate_submission_returns_409(client):
    first = _submit(client, merchantVendor="Sweetgreen SF")
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    response = client.post(
        "/api/claims",
        json={
            "category": "Meals",
            "amount": 22.50,
            "currency": "USD",
            "merchantVendor": "Sweetgreen SF",
            "expenseDate": first["expenseDate"],
            "receiptAttached": True,
        },
    )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "duplicate_claim"
    assert body["context"]["existingClaimNumber"] == first["claimNumber"]
    assert body["requestId"]


def test_unknown_claim_returns_404(client):
    as_role("manager", employee_id=SEED_MANAGER_CODE)
    response = client.get(f"/api/claims/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_illegal_action_returns_409_with_the_legal_alternatives(client, make_claim):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    as_role("finance")
    response = client.post(
        f"/api/claims/{claim.claim_number}/action", json={"action": "DISBURSE"}
    )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "invalid_state_transition"
    assert body["context"]["currentStatus"] == "Manager_Review"
    assert "Approved" in body["context"]["allowedNextStatuses"]


def test_editing_a_submitted_claim_returns_409(client, make_claim):
    claim = make_claim(ClaimStatus.SUBMITTED)
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    response = client.patch(
        f"/api/claims/{claim.claim_number}", json={"purposeDescription": "too late"}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "immutable_entity"


def test_stale_version_returns_409(client, make_claim):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    as_role("manager", employee_id=SEED_MANAGER_CODE)
    response = client.post(
        f"/api/claims/{claim.claim_number}/action",
        json={"action": "APPROVE", "expectedVersion": claim.version - 1},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "concurrent_update"


def test_unsupported_action_returns_422(client, make_claim):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    as_role("manager", employee_id=SEED_MANAGER_CODE)
    response = client.post(f"/api/claims/{claim.claim_number}/action", json={"action": "DELETE"})
    assert response.status_code == 422


# --- actions -----------------------------------------------------------------


def test_approval_flow_over_http(client, make_claim):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)

    as_role("manager", employee_id=SEED_MANAGER_CODE)
    after_manager = client.post(
        f"/api/claims/{claim.claim_number}/action",
        json={"action": "APPROVE", "notes": "Manager OK"},
    )
    assert after_manager.status_code == 200
    assert after_manager.json()["status"] == "Finance_Review"

    as_role("finance")
    after_finance = client.post(
        f"/api/claims/{claim.claim_number}/action", json={"action": "APPROVE"}
    )
    assert after_finance.json()["status"] == "Approved"

    disbursed = client.post(
        f"/api/claims/{claim.claim_number}/action", json={"action": "DISBURSE"}
    )
    assert disbursed.json()["status"] == "Disbursed"


def test_manager_cannot_disburse_finance_can(client, make_claim):
    claim = make_claim(ClaimStatus.AUTO_APPROVED)

    as_role("manager", employee_id=SEED_MANAGER_CODE)
    assert client.post(
        f"/api/claims/{claim.claim_number}/action", json={"action": "DISBURSE"}
    ).status_code == 403

    as_role("finance")
    response = client.post(
        f"/api/claims/{claim.claim_number}/action", json={"action": "DISBURSE"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "Disbursed"


def test_client_supplied_actor_identity_is_ignored(client, make_claim):
    """``actorName``/``actorRole`` in the body must not influence the recorded actor."""
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    as_role("manager", email="real.manager@corp.com", employee_id=SEED_MANAGER_CODE)
    body = client.post(
        f"/api/claims/{claim.claim_number}/action",
        json={"action": "APPROVE", "actorName": "Impostor", "actorRole": "admin",
              "notes": "note"},
    ).json()

    decision_steps = [
        step for step in body["workflowHistory"] if step["stepName"] == "Manager Approval"
    ]
    assert decision_steps
    assert decision_steps[-1]["actorName"] == "real.manager@corp.com"
    assert decision_steps[-1]["actorRole"] == "manager"


def test_reject_flow_over_http(client, make_claim):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    as_role("manager", employee_id=SEED_MANAGER_CODE)
    response = client.post(
        f"/api/claims/{claim.claim_number}/action",
        json={"action": "REJECT", "notes": "Not reimbursable"},
    )
    assert response.json()["status"] == "Rejected"
    assert response.json()["rejectionReason"] == "Not reimbursable"


def test_flag_fraud_over_http(client, make_claim):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    as_role("finance")
    body = client.post(
        f"/api/claims/{claim.claim_number}/action",
        json={"action": "FLAG_FRAUD", "notes": "Vendor mismatch"},
    ).json()
    assert body["status"] == "Flagged_Fraud"
    assert body["fraudScreening"]["isFlagged"] is True


def test_assign_reviewer_over_http(client, make_claim, other_employee):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    as_role("manager", employee_id=SEED_MANAGER_CODE)
    body = client.post(
        f"/api/claims/{claim.claim_number}/assign",
        json={"reviewerId": other_employee.employee_code},
    ).json()
    assert body["assignedReviewerId"] == other_employee.employee_code


def test_assign_reviewer_requires_a_reviewer_role(client, make_claim, other_employee):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    assert client.post(
        f"/api/claims/{claim.claim_number}/assign",
        json={"reviewerId": other_employee.employee_code},
    ).status_code == 403


# --- history -----------------------------------------------------------------


def test_history_endpoint_returns_the_ordered_trail(client):
    claim = _submit(client)
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    history = client.get(f"/api/claims/{claim['claimNumber']}/history").json()

    assert [entry["sequence"] for entry in history] == list(range(1, len(history) + 1))
    assert history[0]["toStatus"] == "Draft"
    assert history[-1]["toStatus"] == "Auto_Approved"
    assert history[0]["requestId"]  # provenance captured


def test_draft_patch_over_http(client, make_claim):
    claim = make_claim(ClaimStatus.DRAFT)
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    response = client.patch(
        f"/api/claims/{claim.claim_number}", json={"purposeDescription": "Updated"}
    )
    assert response.status_code == 200
    assert response.json()["purposeDescription"] == "Updated"
