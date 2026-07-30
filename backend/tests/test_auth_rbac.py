"""RBAC tests: token verification (app.core.security) and authorization (app.core.deps).

Runs fully offline. A locally-generated RSA keypair stands in for the Cognito pool JWKS, so
we exercise the *real* RS256 verification path without a live pool.

Callers present a Cognito **access token**, which carries no role. `get_current_user` resolves
the role from the caller's ``employees`` row (matched on `sub`), so these tests stub the
employee repository rather than putting a role in the token.
"""

import time
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwk, jwt

import app.core.deps as deps_module
import app.core.security as security
from app.core import deps
from app.core.config import settings

KID = "test-key-1"
POOL_ID = "ap-south-1_test"
CLIENT_ID = "testclientid"


@pytest.fixture(autouse=True)
def _configure_cognito(monkeypatch):
    monkeypatch.setattr(settings, "COGNITO_USER_POOL_ID", POOL_ID, raising=False)
    monkeypatch.setattr(settings, "COGNITO_APP_CLIENT_ID", CLIENT_ID, raising=False)
    monkeypatch.setattr(settings, "COGNITO_REGION", "ap-south-1", raising=False)


@pytest.fixture(scope="module")
def rsa_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    public_jwk = jwk.construct(public_pem, "RS256").to_dict()
    public_jwk["kid"] = KID
    # jose emits bytes for e/n; JSON/JWKS expects str.
    for field in ("e", "n"):
        if isinstance(public_jwk.get(field), bytes):
            public_jwk[field] = public_jwk[field].decode()
    return {"private_pem": private_pem, "jwk": public_jwk}


@pytest.fixture(autouse=True)
def _stub_jwks(rsa_key, monkeypatch):
    monkeypatch.setattr(security, "_get_jwks", lambda force=False: [rsa_key["jwk"]])


def make_token(rsa_key, *, token_use="access", client_id=CLIENT_ID, iss=None,
               exp_delta=3600, sub="sub-123"):
    """Build a Cognito-shaped access token (no email/role — Cognito omits them)."""
    now = int(time.time())
    claims = {
        "sub": sub,
        "client_id": client_id,
        "iss": iss or security.issuer(),
        "token_use": token_use,
        "iat": now,
        "exp": now + exp_delta,
        "scope": "aws.cognito.signin.user.admin",
    }
    return jwt.encode(claims, rsa_key["private_pem"], algorithm="RS256",
                      headers={"kid": KID})


def fake_employee(*, role="admin", code="emp-101", email="u@corp.com",
                  name="Test User", is_active=True):
    return SimpleNamespace(
        role_name=role, employee_code=code, email=email,
        full_name=name, is_active=is_active,
    )


@pytest.fixture
def employee(monkeypatch):
    """Stub EmployeeRepository.get_by_cognito_sub; set `.value` per test (None = not found)."""
    holder = SimpleNamespace(value=fake_employee())

    class _Repo:
        def __init__(self, db):
            pass

        def get_by_cognito_sub(self, sub):
            return holder.value

    monkeypatch.setattr(deps_module, "EmployeeRepository", _Repo)
    return holder


# --- security.verify_access_token --------------------------------------------

def test_verify_valid_token(rsa_key):
    claims = security.verify_access_token(make_token(rsa_key))
    assert claims["sub"] == "sub-123"
    assert claims["token_use"] == "access"


def test_verify_expired_token(rsa_key):
    with pytest.raises(security.TokenError):
        security.verify_access_token(make_token(rsa_key, exp_delta=-10))


def test_verify_wrong_client_id(rsa_key):
    """A token minted for another app client in the same pool must be rejected."""
    with pytest.raises(security.TokenError):
        security.verify_access_token(make_token(rsa_key, client_id="someone-else"))


def test_verify_wrong_issuer(rsa_key):
    with pytest.raises(security.TokenError):
        security.verify_access_token(make_token(rsa_key, iss="https://evil.example.com/pool"))


def test_verify_rejects_id_token(rsa_key):
    """The ID token is no longer accepted as an API credential."""
    with pytest.raises(security.TokenError):
        security.verify_access_token(make_token(rsa_key, token_use="id"))


def test_verify_tampered_signature(rsa_key):
    token = make_token(rsa_key)
    tampered = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")
    with pytest.raises(security.TokenError):
        security.verify_access_token(tampered)


# --- deps.get_current_user ---------------------------------------------------

def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_get_current_user_success(rsa_key, employee):
    employee.value = fake_employee(role="manager", code="emp-101")
    user = deps.get_current_user(_creds(make_token(rsa_key)), db=None)
    assert user.role == "manager"
    assert user.sub == "sub-123"
    assert user.employee_id == "emp-101"
    # email/name come from the employee row, not the token
    assert user.email == "u@corp.com"
    assert user.display_name == "Test User"


def test_get_current_user_no_token_401():
    with pytest.raises(HTTPException) as ei:
        deps.get_current_user(None, db=None)
    assert ei.value.status_code == 401


def test_get_current_user_bad_token_401():
    with pytest.raises(HTTPException) as ei:
        deps.get_current_user(_creds("not.a.jwt"), db=None)
    assert ei.value.status_code == 401


def test_get_current_user_unlinked_sub_403(rsa_key, employee):
    """Valid token, but no employee row is linked to the sub."""
    employee.value = None
    with pytest.raises(HTTPException) as ei:
        deps.get_current_user(_creds(make_token(rsa_key)), db=None)
    assert ei.value.status_code == 403


def test_get_current_user_deactivated_403(rsa_key, employee):
    employee.value = fake_employee(is_active=False)
    with pytest.raises(HTTPException) as ei:
        deps.get_current_user(_creds(make_token(rsa_key)), db=None)
    assert ei.value.status_code == 403


def test_get_current_user_missing_role_403(rsa_key, employee):
    employee.value = fake_employee(role=None)
    with pytest.raises(HTTPException) as ei:
        deps.get_current_user(_creds(make_token(rsa_key)), db=None)
    assert ei.value.status_code == 403


def test_get_current_user_invalid_role_403(rsa_key, employee):
    employee.value = fake_employee(role="superuser")
    with pytest.raises(HTTPException) as ei:
        deps.get_current_user(_creds(make_token(rsa_key)), db=None)
    assert ei.value.status_code == 403


# --- deps.require_roles ------------------------------------------------------

def test_require_roles_allows_matching(rsa_key, employee):
    employee.value = fake_employee(role="admin")
    user = deps.get_current_user(_creds(make_token(rsa_key)), db=None)
    dep = deps.require_roles("admin", "finance")
    assert dep(user=user) is user


def test_require_roles_forbids_unauthorized(rsa_key, employee):
    employee.value = fake_employee(role="employee")
    user = deps.get_current_user(_creds(make_token(rsa_key)), db=None)
    dep = deps.require_roles("admin", "finance")
    with pytest.raises(HTTPException) as ei:
        dep(user=user)
    assert ei.value.status_code == 403
