"""Policy-rules and audit-log API integration tests."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from app.models.enums import ClaimStatus
from tests.conftest import SEED_EMPLOYEE_CODE, SEED_MANAGER_CODE, as_role

LEGACY_RULE_KEYS = {
    "category", "maxAmountUSD", "autoApproveLimitUSD", "receiptRequiredAboveUSD",
    "requiresPreApproval", "gradeTier", "specialRules",
}

LEGACY_AUDIT_KEYS = {
    "id", "timestamp", "actor", "role", "eventType", "targetId", "details", "ipAddress",
}


# --- policy rules ------------------------------------------------------------


def test_get_policy_rules_returns_the_legacy_array(client):
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    response = client.get("/api/policy-rules")

    assert response.status_code == 200
    rules = response.json()
    assert isinstance(rules, list) and rules
    for rule in rules:
        assert LEGACY_RULE_KEYS <= set(rule)

    meals = next(rule for rule in rules if rule["category"] == "Meals")
    assert meals["maxAmountUSD"] == 40.0
    assert meals["autoApproveLimitUSD"] == 25.0
    assert any("Alcohol" in clause for clause in meals["specialRules"])


def test_policy_rules_readable_by_any_authenticated_role(client):
    for role in ("employee", "manager", "finance", "admin", "auditor"):
        as_role(role, employee_id=SEED_EMPLOYEE_CODE)
        assert client.get("/api/policy-rules").status_code == 200


def test_policy_rules_require_authentication(anon_client):
    assert anon_client.get("/api/policy-rules").status_code == 401


def test_put_policy_rules_restricted_to_finance_and_admin(client):
    payload = [{"category": "Meals", "maxAmountUSD": 45.0}]

    for role in ("employee", "manager", "auditor"):
        as_role(role, employee_id=SEED_EMPLOYEE_CODE)
        assert client.put("/api/policy-rules", json=payload).status_code == 403

    as_role("finance")
    assert client.put("/api/policy-rules", json=payload).status_code == 200


def test_put_publishes_a_new_version_and_keeps_the_old_one(client):
    as_role("finance")
    response = client.put(
        "/api/policy-rules",
        json=[
            {
                "category": "Meals",
                "maxAmountUSD": 55.0,
                "autoApproveLimitUSD": 30.0,
                "receiptRequiredAboveUSD": 30.0,
                "requiresPreApproval": False,
                "gradeTier": "All Staff",
                "specialRules": ["Updated cap."],
            }
        ],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "updated"
    assert body["count"] == 1
    assert body["rules"][0]["version"] == 2

    # The active ruleset now reflects the change...
    active = client.get("/api/policy-rules").json()
    assert next(r for r in active if r["category"] == "Meals")["maxAmountUSD"] == 55.0

    # ...and version 1 is still on record.
    versions = client.get("/api/policy-rules/MEALS_STANDARD/versions").json()
    assert [rule["version"] for rule in versions] == [2, 1]
    assert versions[1]["maxAmountUSD"] == 40.0
    assert versions[1]["isActive"] is False


def test_put_audits_the_change(client):
    as_role("finance", email="fin@corp.com")
    client.put("/api/policy-rules", json=[{"category": "Meals", "maxAmountUSD": 55.0}])

    logs = client.get("/api/audit-logs?action=POLICY_UPDATE").json()
    assert logs
    assert logs[0]["actor"] == "fin@corp.com"
    assert logs[0]["role"] == "finance"


def test_put_rejects_a_rule_without_a_category(client):
    as_role("finance")
    assert client.put("/api/policy-rules", json=[{"maxAmountUSD": 10.0}]).status_code == 422


def test_put_rejects_a_non_list_payload(client):
    as_role("finance")
    assert client.put("/api/policy-rules", json={"category": "Meals"}).status_code == 422


def test_rule_versions_endpoint_is_restricted(client):
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    assert client.get("/api/policy-rules/MEALS_STANDARD/versions").status_code == 403


def test_unknown_rule_versions_returns_404(client):
    as_role("admin")
    assert client.get("/api/policy-rules/NOT_A_RULE/versions").status_code == 404


# --- audit logs --------------------------------------------------------------


def test_audit_logs_require_authentication(anon_client):
    assert anon_client.get("/api/audit-logs").status_code == 401


def test_audit_logs_forbidden_for_employee_and_manager(client):
    for role in ("employee", "manager"):
        as_role(role, employee_id=SEED_EMPLOYEE_CODE)
        assert client.get("/api/audit-logs").status_code == 403


def test_audit_logs_allowed_for_finance_admin_auditor(client):
    for role in ("finance", "admin", "auditor"):
        as_role(role)
        assert client.get("/api/audit-logs").status_code == 200


def test_audit_logs_keep_the_legacy_entry_shape(client):
    # Generate an entry through a real business action.
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    client.post(
        "/api/claims",
        json={
            "category": "Meals",
            "amount": 12.0,
            "currency": "USD",
            "merchantVendor": f"Vendor {uuid.uuid4().hex[:6]}",
            "expenseDate": (date.today() - timedelta(days=1)).isoformat(),
            "receiptAttached": True,
        },
    )

    as_role("auditor")
    logs = client.get("/api/audit-logs").json()
    assert isinstance(logs, list) and logs
    for entry in logs:
        assert LEGACY_AUDIT_KEYS <= set(entry)

    submission = next(e for e in logs if e["eventType"] == "SUBMIT_CLAIM")
    assert submission["role"] == "employee"
    assert submission["targetId"].startswith("EXP-")
    # Additive provenance the in-memory list could not provide.
    assert submission["requestId"]
    assert submission["entityType"] == "Claim"


def test_audit_logs_newest_first(client):
    as_role("auditor")
    logs = client.get("/api/audit-logs?limit=50").json()
    timestamps = [entry["timestamp"] for entry in logs]
    assert timestamps == sorted(timestamps, reverse=True)


def test_audit_logs_filter_by_action(client, make_claim):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    as_role("manager", employee_id=SEED_MANAGER_CODE)
    client.post(f"/api/claims/{claim.claim_number}/action", json={"action": "APPROVE"})

    as_role("auditor")
    logs = client.get("/api/audit-logs?action=APPROVAL_ACTION").json()
    assert logs
    assert all(entry["eventType"] == "APPROVAL_ACTION" for entry in logs)


def test_entity_audit_trail_endpoint(client, make_claim):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    as_role("manager", employee_id=SEED_MANAGER_CODE)
    client.post(
        f"/api/claims/{claim.claim_number}/action", json={"action": "APPROVE", "notes": "ok"}
    )

    as_role("auditor")
    trail = client.get(f"/api/audit-logs/Claim/{claim.claim_number}").json()
    assert trail
    assert all(entry["targetId"] == claim.claim_number for entry in trail)
    assert any(entry["before"] and entry["after"] for entry in trail)


def test_entity_audit_trail_is_restricted(client, make_claim):
    claim = make_claim()
    as_role("employee", employee_id=SEED_EMPLOYEE_CODE)
    assert client.get(f"/api/audit-logs/Claim/{claim.claim_number}").status_code == 403


def test_audit_log_api_offers_no_write_endpoints(client):
    """The trail is append-only: nothing but GET is routed."""
    as_role("admin")
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        response = client.request(method, "/api/audit-logs")
        assert response.status_code == 405, f"{method} unexpectedly routed"
