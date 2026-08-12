"""Claims API integration tests — real routes, real services, real database.

Two things are being verified together: that the HTTP contract the existing frontend depends on is
unchanged, and that domain failures surface as the right status codes without any route catching
them by hand.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

import pytest

from app.core import deps
from app.main import app
from app.models.enums import ClaimStatus
from conftest import (
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


class _RecordingDocumentClassifier:
    def __init__(self, **report_overrides: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        self._report = {
            "documentType": "receipt",
            "suggestedCategory": "Meals",
            "confidence": 0.91,
            "categoryMismatch": False,
            "categoryReviewRequired": False,
            "extractedFields": {"mealType": "team_lunch"},
            "notes": "Matched Meals.",
        }
        self._report.update(report_overrides)

    def classify_and_extract(self, **kwargs) -> dict[str, Any]:
        self.calls.append(kwargs)
        return dict(self._report)


def _classified_claim_payload(**item_overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "category": "Meals",
        "subCategory": "Team Lunch",
        "amount": 22.50,
        "amountUSD": 22.50,
        "currency": "USD",
        "merchantVendor": f"Vendor {uuid.uuid4().hex[:8]}",
        "expenseDate": (date.today() - timedelta(days=1)).isoformat(),
        "purposeDescription": "Team sync lunch.",
        "receiptAttached": True,
        "ocrExtractedJson": {
            "vendorName": "Cafe Example",
            "transactionDate": (date.today() - timedelta(days=1)).isoformat(),
            "totalAmount": 22.50,
            "currency": "USD",
        },
    }
    item.update(item_overrides)
    return {"title": "Classified receipt", "currency": "USD", "items": [item]}


def test_document_classification_runs_during_claim_submission_and_is_returned(client):
    classifier = _RecordingDocumentClassifier()
    app.dependency_overrides[deps.get_optional_document_classification] = lambda: classifier

    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    response = client.post(
        "/api/claims",
        json={
            "title": "Classified receipt",
            "currency": "USD",
            "items": [
                {
                    "category": "Meals",
                    "subCategory": "Team Lunch",
                    "amount": 22.50,
                    "amountUSD": 22.50,
                    "currency": "USD",
                    "merchantVendor": f"Vendor {uuid.uuid4().hex[:8]}",
                    "expenseDate": (date.today() - timedelta(days=1)).isoformat(),
                    "purposeDescription": "Team sync lunch.",
                    "receiptAttached": True,
                    "ocrExtractedJson": {
                        "vendorName": "Cafe Example",
                        "transactionDate": (date.today() - timedelta(days=1)).isoformat(),
                        "totalAmount": 22.50,
                        "currency": "USD",
                    },
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    claim = response.json()

    assert len(classifier.calls) == 1
    assert classifier.calls[0]["employee_category"] == "Meals"
    assert classifier.calls[0]["ocr_text"]

    item = claim["items"][0]
    assert item["documentClassification"] == {
        "documentType": "receipt",
        "suggestedCategory": "Meals",
        "confidence": 0.91,
        "categoryMismatch": False,
        "needsManualReview": False,
        "extractedFields": {"mealType": "team_lunch"},
        "notes": "Matched Meals.",
    }

    history_steps = [step["stepName"] for step in claim["workflowHistory"]]
    assert history_steps.index("Category Classification (item 1)") < history_steps.index(
        "Policy Validation (item 1)"
    )

    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    fetched = client.get(f"/api/claims/{claim['claimNumber']}")
    assert fetched.status_code == 200
    assert fetched.json()["items"][0]["documentClassification"] == item["documentClassification"]


def test_classification_review_flag_holds_the_item_without_disturbing_policy_or_fraud(client):
    """``categoryReviewRequired`` is the one way classification touches routing: a clean item is
    held for a human instead of auto-approving. The policy and fraud engines still run and still
    report — the failure mode being guarded against is a classification change quietly becoming a
    second, competing verdict.
    """
    classifier = _RecordingDocumentClassifier(
        categoryReviewRequired=True,
        confidence=0.42,
        notes="AI classification unavailable.",
        extractedFields=None,
    )
    app.dependency_overrides[deps.get_optional_document_classification] = lambda: classifier

    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    response = client.post("/api/claims", json=_classified_claim_payload())
    assert response.status_code == 201, response.text
    claim = response.json()
    item = claim["items"][0]

    assert item["documentClassification"]["needsManualReview"] is True
    assert item["status"] == "Policy_Hold"
    assert claim["status"] == "Manager_Review"

    # The engines were not skipped, short-circuited, or overwritten by the classification verdict.
    assert item["policyValidation"] is not None
    assert item["policyValidation"]["overallPassed"] is True
    assert item["fraudScreening"] is not None
    history_steps = [step["stepName"] for step in claim["workflowHistory"]]
    assert history_steps.index("Category Classification (item 1)") < history_steps.index(
        "Policy Validation (item 1)"
    )


def test_classification_failure_does_not_break_submission(client):
    """Requirement 6 at the API boundary: a provider blow-up still yields a 201 and a held item."""

    class _ExplodingClassifier:
        def classify_and_extract(self, **kwargs) -> dict[str, Any]:
            raise RuntimeError("Provider 'bedrock' failed: ValidationException")

    app.dependency_overrides[deps.get_optional_document_classification] = _ExplodingClassifier

    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    response = client.post("/api/claims", json=_classified_claim_payload())

    assert response.status_code == 201, response.text
    claim = response.json()
    item = claim["items"][0]
    assert item["documentClassification"]["needsManualReview"] is True
    assert item["documentClassification"]["documentType"] is None
    assert item["status"] == "Policy_Hold"
    assert item["policyValidation"] is not None  # the rest of the pipeline still ran


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


# --- team claims (reports-to view) --------------------------------------------


def test_team_endpoint_returns_the_managers_reports(client):
    """``employee`` reports to ``SEED_MANAGER_CODE`` — see the ``employee`` fixture."""
    mine = _submit(client)

    as_role("manager", employee_id=SEED_MANAGER_CODE)
    body = client.get("/api/claims/team").json()
    assert mine["id"] in {c["id"] for c in body}
    assert REQUIRED_CLAIM_KEYS <= set(body[0])  # same shape as GET /claims


def test_team_endpoint_excludes_claims_outside_the_team(client, make_claim, other_employee):
    """``other_employee`` has no manager, so their claim is nobody's team."""
    someone_elses = make_claim(owner=other_employee)
    as_role("manager", employee_id=SEED_MANAGER_CODE)
    body = client.get("/api/claims/team").json()
    assert someone_elses.id not in {c["id"] for c in body}


def test_team_endpoint_requires_manager_role(client):
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    assert client.get("/api/claims/team").status_code == 403


def test_team_endpoint_not_shadowed_by_the_claim_id_route(client):
    """A static ``/team`` path must resolve here, not fall through to ``/{claim_id}``."""
    as_role("manager", employee_id=SEED_MANAGER_CODE)
    response = client.get("/api/claims/team")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


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
    lodging = make_claim(ClaimStatus.MANAGER_REVIEW, category="Hotel / Lodging")
    as_role("manager", employee_id=SEED_MANAGER_CODE)

    by_status = client.get("/api/claims?status=Manager_Review").json()
    assert lodging.id.__str__() in {c["id"] for c in by_status}
    assert all(c["status"] == "Manager_Review" for c in by_status)

    by_category = client.get("/api/claims", params={"category": "Hotel / Lodging"}).json()
    assert all(c["category"] == "Hotel / Lodging" for c in by_category)


def test_canonical_status_alias_accepted_in_filters(client, make_claim):
    """``Reimbursed`` is the spec spelling of the wire value ``Disbursed``."""
    reimbursed = make_claim(ClaimStatus.REIMBURSED)
    as_role("finance")
    body = client.get("/api/claims?status=Reimbursed").json()
    assert str(reimbursed.id) in {c["id"] for c in body}


def test_filter_by_multiple_statuses_in_one_request(client, make_claim):
    """Repeated `?status=` params are OR'd — one request covers what used to take several."""
    review = make_claim(ClaimStatus.MANAGER_REVIEW)
    reimbursed = make_claim(ClaimStatus.REIMBURSED)
    rejected = make_claim(ClaimStatus.REJECTED)
    as_role("manager", employee_id=SEED_MANAGER_CODE)

    body = client.get(
        "/api/claims?status=Manager_Review&status=Disbursed"
    ).json()
    ids = {c["id"] for c in body}
    assert str(review.id) in ids
    assert str(reimbursed.id) in ids
    assert str(rejected.id) not in ids
    assert all(c["status"] in {"Manager_Review", "Disbursed"} for c in body)


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
    """Auto-approved has no ``-> Rejected`` edge — only a human review can reject a claim."""
    claim = make_claim(ClaimStatus.AUTO_APPROVED)
    as_role("finance")
    response = client.post(
        f"/api/claims/{claim.claim_number}/action", json={"action": "REJECT"}
    )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "invalid_state_transition"
    assert body["context"]["currentStatus"] == "Auto_Approved"
    assert "Approved" in body["context"]["allowedNextStatuses"]
    assert "Rejected" not in body["context"]["allowedNextStatuses"]


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
    assert after_finance.status_code == 200
    assert after_finance.json()["status"] == "Approved"


def test_disburse_action_no_longer_exists_over_http(client, make_claim):
    """``DISBURSE`` was retired — Approve is the final reviewer step, for every role."""
    claim = make_claim(ClaimStatus.APPROVED)

    for role, employee_id in [
        ("manager", SEED_MANAGER_CODE), ("finance", None), ("admin", None),
    ]:
        as_role(role, employee_id=employee_id)
        response = client.post(
            f"/api/claims/{claim.claim_number}/action", json={"action": "DISBURSE"}
        )
        assert response.status_code == 422
        assert response.json()["code"] == "validation_error"


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
