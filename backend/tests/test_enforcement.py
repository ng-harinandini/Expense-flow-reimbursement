"""RBAC + ownership enforcement on the real endpoints (T002 coverage, Phase 1 update).

Previously these ran against the in-memory ``claims_store``. They now run against PostgreSQL
through the real service/repository graph — the enforcement contract is unchanged; only the
backing store is. Authentication is still simulated by overriding ``get_current_user``.
"""

from __future__ import annotations

from tests.conftest import SEED_EMPLOYEE_CODE, SEED_MANAGER_CODE, as_role

from app.models.enums import ClaimStatus


# --- gating ------------------------------------------------------------------

def test_audit_logs_requires_auth(anon_client):
    assert anon_client.get("/api/audit-logs").status_code == 401  # no token


def test_audit_logs_forbidden_for_employee(client):
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    assert client.get("/api/audit-logs").status_code == 403


def test_audit_logs_allowed_for_finance(client):
    as_role("finance")
    assert client.get("/api/audit-logs").status_code == 200


def test_policy_rules_readable_by_any_role_but_put_restricted(client):
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    assert client.get("/api/policy-rules").status_code == 200
    assert client.put("/api/policy-rules", json=[]).status_code == 403
    as_role("finance")
    assert client.put("/api/policy-rules", json=[]).status_code == 200


def test_aws_export_admin_only(client):
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    assert client.get("/api/aws/export-code").status_code == 403
    as_role("admin")
    assert client.get("/api/aws/export-code").status_code == 200


# --- ownership + DISBURSE ----------------------------------------------------

def test_employee_sees_only_own_claims(client, make_claim, employee, other_employee):
    mine = make_claim(owner=employee)
    make_claim(owner=other_employee)

    as_role("employee", employee_id=employee.employee_code)
    response = client.get("/api/claims")
    assert response.status_code == 200
    body = response.json()
    assert body, "expected at least one claim for this employee"
    assert str(mine.id) in {c["id"] for c in body}
    assert all(c["employeeId"] == employee.employee_code for c in body)


def test_manager_cannot_disburse_finance_can(client, make_claim):
    claim = make_claim(ClaimStatus.AUTO_APPROVED)

    as_role("manager", employee_id=SEED_MANAGER_CODE)
    assert client.post(
        f"/api/claims/{claim.id}/action", json={"action": "DISBURSE"}
    ).status_code == 403

    as_role("finance")
    response = client.post(f"/api/claims/{claim.id}/action", json={"action": "DISBURSE"})
    assert response.status_code == 200
    assert response.json()["status"] == "Disbursed"


def test_create_claim_requires_employee_role(client):
    as_role("manager", employee_id=SEED_MANAGER_CODE)
    r = client.post("/api/claims", json={"amount": 10, "merchantVendor": "X"})
    assert r.status_code == 403
