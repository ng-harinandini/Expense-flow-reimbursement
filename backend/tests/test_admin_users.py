"""M5 tests — admin user-management router (Cognito Admin APIs mocked).

Auth is simulated via dependency-override on get_current_user; the boto3 Cognito client is
replaced with a fake that records calls. No AWS account required.

Phase 1 update: these routes now write a durable audit record for every mutation, so they use the
shared ``client`` fixture from ``conftest`` (transaction-scoped test database) instead of a bare
``TestClient``.
"""

import pytest

import app.api.admin_users as admin_module
from app.core import deps
from app.core.config import settings
from app.core.deps import CurrentUser
from app.main import app


def user(role, email="admin@corp.com", employee_id=None):
    return CurrentUser(sub="sub-x", email=email, role=role, employee_id=employee_id, claims={})


class FakeCognito:
    def __init__(self):
        self.calls = []
        self.attrs = [
            {"Name": "sub", "Value": "sub-1"},
            {"Name": "email", "Value": "new@corp.com"},
            {"Name": "custom:role_id", "Value": "employee"},
        ]

    def admin_create_user(self, **kw):
        self.calls.append(("admin_create_user", kw))
        return {"User": {"Username": "uuid-1", "Enabled": True, "UserStatus": "FORCE_CHANGE_PASSWORD",
                         "Attributes": kw["UserAttributes"] + [{"Name": "sub", "Value": "sub-1"}]}}

    def admin_update_user_attributes(self, **kw):
        self.calls.append(("admin_update_user_attributes", kw))
        return {}

    def admin_get_user(self, **kw):
        self.calls.append(("admin_get_user", kw))
        return {"Username": "uuid-1", "Enabled": True, "UserStatus": "CONFIRMED", "UserAttributes": self.attrs}

    def admin_enable_user(self, **kw):
        self.calls.append(("admin_enable_user", kw))
        return {}

    def admin_disable_user(self, **kw):
        self.calls.append(("admin_disable_user", kw))
        return {}

    def list_users(self, **kw):
        self.calls.append(("list_users", kw))
        return {"Users": [{"Username": "uuid-1", "Enabled": True, "UserStatus": "CONFIRMED",
                           "Attributes": self.attrs}], "PaginationToken": "next-42"}


@pytest.fixture
def fake(monkeypatch):
    f = FakeCognito()
    monkeypatch.setattr(admin_module, "cognito_client", lambda: f)
    monkeypatch.setattr(settings, "COGNITO_USER_POOL_ID", "ap-south-1_test", raising=False)
    monkeypatch.setattr(settings, "COGNITO_APP_CLIENT_ID", "cid", raising=False)
    return f


def as_role(role, **kw):
    app.dependency_overrides[deps.get_current_user] = lambda: user(role, **kw)


def called(fake, name):
    return [c for c in fake.calls if c[0] == name]


# --- admin-only gating -------------------------------------------------------

def test_non_admin_forbidden_on_every_route(fake, client):
    as_role("finance")  # not admin
    assert client.get("/api/admin/users").status_code == 403
    payload = {"email": "a@b.com", "role": "employee", "name": "A B", "grade": "L1"}
    assert client.post("/api/admin/users", json=payload).status_code == 403
    assert client.get("/api/admin/users/a@b.com").status_code == 403
    assert client.post("/api/admin/users/a@b.com/role", json={"role": "manager"}).status_code == 403
    assert client.post("/api/admin/users/a@b.com/disable").status_code == 403


def test_unauthenticated_is_401(fake, client):
    # No dependency override → real get_current_user with no bearer token.
    assert client.get("/api/admin/users").status_code == 401


# --- create ------------------------------------------------------------------

def test_create_user_sets_role_id(fake, client):
    as_role("admin")
    payload = {"email": "New@Corp.com", "role": "manager", "name": "New Corp", "grade": "L3"}
    r = client.post("/api/admin/users", json=payload)
    assert r.status_code == 201, r.text
    body = r.json()
    (_, kw), = called(fake, "admin_create_user")
    assert kw["Username"] == "new@corp.com"  # normalized
    attrs = {a["Name"]: a["Value"] for a in kw["UserAttributes"]}
    assert attrs["custom:role_id"] == "manager"
    # employeeId is server-generated (client-supplied values are ignored) — cross-check with the
    # employees row it just created rather than hardcoding the code.
    assert attrs["custom:employeeId"] == body["employeeId"]
    assert body["grade"] == "L3"
    assert body["fullName"] == "New Corp"
    assert body["isActive"] is True


def test_create_user_invalid_role_400(fake, client):
    as_role("admin")
    payload = {"email": "a@b.com", "role": "superuser", "name": "A B", "grade": "L1"}
    r = client.post("/api/admin/users", json=payload)
    assert r.status_code == 400
    assert called(fake, "admin_create_user") == []  # rejected before touching Cognito


def test_create_user_provisions_a_postgres_employee_row(fake, client, db_session):
    """The point of this change: creating a user must not be Cognito-only."""
    from app.models.organization import Employee

    as_role("admin")
    payload = {"email": "provisioned@corp.com", "role": "employee", "name": "Provisioned Person",
               "grade": "L2"}
    r = client.post("/api/admin/users", json=payload)
    assert r.status_code == 201, r.text
    body = r.json()

    row = db_session.query(Employee).filter_by(employee_code=body["employeeId"]).one()
    assert row.full_name == "Provisioned Person"
    assert row.email == "provisioned@corp.com"
    assert row.role.name == "employee"
    assert row.cognito_sub == "sub-1"  # linked from the (fake) Cognito response
    assert row.is_active is True


def test_create_user_with_manager_links_manager_id(fake, client, employee):
    as_role("admin")
    payload = {"email": "reports-to@corp.com", "role": "employee", "name": "Reports To",
               "grade": "L1", "managerId": str(employee.id)}
    r = client.post("/api/admin/users", json=payload)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["managerId"] == str(employee.id)
    assert body["managerName"] == employee.full_name


def test_create_user_unknown_manager_404(fake, client):
    as_role("admin")
    payload = {"email": "orphan@corp.com", "role": "employee", "name": "Orphan", "grade": "L1",
               "managerId": "00000000-0000-0000-0000-000000000000"}
    r = client.post("/api/admin/users", json=payload)
    assert r.status_code == 404
    assert called(fake, "admin_create_user") == []  # rejected before touching Cognito


# --- change role -------------------------------------------------------------

def test_change_role_updates_attribute(fake, client):
    as_role("admin")
    r = client.post("/api/admin/users/new@corp.com/role", json={"role": "finance"})
    assert r.status_code == 200
    (_, kw), = called(fake, "admin_update_user_attributes")
    attrs = {a["Name"]: a["Value"] for a in kw["UserAttributes"]}
    assert attrs == {"custom:role_id": "finance"}


def test_change_role_invalid_400(fake, client):
    as_role("admin")
    r = client.post("/api/admin/users/new@corp.com/role", json={"role": "root"})
    assert r.status_code == 400
    assert called(fake, "admin_update_user_attributes") == []


# --- enable / disable / list -------------------------------------------------

def test_disable_user(fake, client):
    as_role("admin")
    r = client.post("/api/admin/users/new@corp.com/disable")
    assert r.status_code == 200
    assert called(fake, "admin_disable_user")


def test_list_users_paginates(fake, client):
    as_role("admin")
    r = client.get("/api/admin/users?limit=10")
    assert r.status_code == 200
    body = r.json()
    assert body["users"][0]["role"] == "employee"
    assert body["nextToken"] == "next-42"
