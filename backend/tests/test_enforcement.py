"""M4 tests — RBAC + ownership wired onto the real endpoints.

Auth is simulated via dependency-override on get_current_user. Endpoints backed by in-memory
stores (claims, policy, audit) are exercised directly; DB-backed receipts are covered by the
ownership unit in the deps/receipts logic and the live suite.
"""

import pytest
from fastapi.testclient import TestClient

from app.core import deps
from app.core.deps import CurrentUser
from app.main import app
from app.services.store import claims_store


def user(role, email=None, employee_id=None):
    return CurrentUser(sub=f"sub-{role}", email=email or f"{role}@corp.com",
                       role=role, employee_id=employee_id, claims={})


@pytest.fixture
def client():
    c = TestClient(app)
    yield c
    app.dependency_overrides.clear()


def as_role(role, **kw):
    app.dependency_overrides[deps.get_current_user] = lambda: user(role, **kw)


# --- gating ------------------------------------------------------------------

def test_audit_logs_requires_auth(client):
    assert client.get("/api/audit-logs").status_code == 401  # no token


def test_audit_logs_forbidden_for_employee(client):
    as_role("employee")
    assert client.get("/api/audit-logs").status_code == 403


def test_audit_logs_allowed_for_finance(client):
    as_role("finance")
    assert client.get("/api/audit-logs").status_code == 200


def test_policy_rules_readable_by_any_role_but_put_restricted(client):
    as_role("employee")
    assert client.get("/api/policy-rules").status_code == 200
    assert client.put("/api/policy-rules", json=[]).status_code == 403
    as_role("finance")
    assert client.put("/api/policy-rules", json=[]).status_code == 200


def test_aws_export_admin_only(client):
    as_role("employee")
    assert client.get("/api/aws/export-code").status_code == 403
    as_role("admin")
    assert client.get("/api/aws/export-code").status_code == 200


# --- ownership + DISBURSE ----------------------------------------------------

def test_employee_sees_only_own_claims(client):
    target = claims_store[0]["employeeId"]
    as_role("employee", employee_id=target)
    r = client.get("/api/claims")
    assert r.status_code == 200
    assert r.json(), "expected at least one seeded claim for this employee"
    assert all(c["employeeId"] == target for c in r.json())


def test_manager_cannot_disburse_finance_can(client):
    claim_id = claims_store[0]["id"]
    as_role("manager")
    assert client.post(f"/api/claims/{claim_id}/action", json={"action": "DISBURSE"}).status_code == 403
    as_role("finance")
    r = client.post(f"/api/claims/{claim_id}/action", json={"action": "DISBURSE"})
    assert r.status_code == 200
    assert r.json()["status"] == "Disbursed"


def test_create_claim_requires_employee_role(client):
    as_role("manager")
    r = client.post("/api/claims", json={"amount": 10, "merchantVendor": "X"})
    assert r.status_code == 403
