"""RBAC tests: token verification (app.core.security) and authorization (app.core.deps).

Runs fully offline. A locally-generated RSA keypair stands in for the Cognito pool JWKS, so
we exercise the *real* RS256 verification path without a live pool. Role decisions read only
`custom:role_id`; Cognito Groups are never consulted.
"""

import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwk, jwt

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


def make_token(rsa_key, *, role="admin", employee_id="emp-101", token_use="id",
               aud=CLIENT_ID, iss=None, exp_delta=3600, sub="sub-123", email="u@corp.com"):
    now = int(time.time())
    claims = {
        "sub": sub,
        "email": email,
        "aud": aud,
        "iss": iss or security.issuer(),
        "token_use": token_use,
        "iat": now,
        "exp": now + exp_delta,
    }
    if role is not None:
        claims["custom:role_id"] = role
    if employee_id is not None:
        claims["custom:employeeId"] = employee_id
    return jwt.encode(claims, rsa_key["private_pem"], algorithm="RS256",
                      headers={"kid": KID})


# --- security.verify_id_token ------------------------------------------------

def test_verify_valid_token(rsa_key):
    claims = security.verify_id_token(make_token(rsa_key, role="finance"))
    assert claims["custom:role_id"] == "finance"
    assert claims["sub"] == "sub-123"


def test_verify_expired_token(rsa_key):
    with pytest.raises(security.TokenError):
        security.verify_id_token(make_token(rsa_key, exp_delta=-10))


def test_verify_wrong_audience(rsa_key):
    with pytest.raises(security.TokenError):
        security.verify_id_token(make_token(rsa_key, aud="someone-else"))


def test_verify_wrong_issuer(rsa_key):
    with pytest.raises(security.TokenError):
        security.verify_id_token(make_token(rsa_key, iss="https://evil.example.com/pool"))


def test_verify_rejects_access_token(rsa_key):
    with pytest.raises(security.TokenError):
        security.verify_id_token(make_token(rsa_key, token_use="access"))


def test_verify_tampered_signature(rsa_key):
    token = make_token(rsa_key)
    tampered = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")
    with pytest.raises(security.TokenError):
        security.verify_id_token(tampered)


# --- deps.get_current_user ---------------------------------------------------

def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_get_current_user_success(rsa_key):
    user = deps.get_current_user(_creds(make_token(rsa_key, role="manager")))
    assert user.role == "manager"
    assert user.sub == "sub-123"
    assert user.employee_id == "emp-101"


def test_get_current_user_no_token_401():
    with pytest.raises(HTTPException) as ei:
        deps.get_current_user(None)
    assert ei.value.status_code == 401


def test_get_current_user_bad_token_401():
    with pytest.raises(HTTPException) as ei:
        deps.get_current_user(_creds("not.a.jwt"))
    assert ei.value.status_code == 401


def test_get_current_user_missing_role_403(rsa_key):
    with pytest.raises(HTTPException) as ei:
        deps.get_current_user(_creds(make_token(rsa_key, role=None)))
    assert ei.value.status_code == 403


def test_get_current_user_invalid_role_403(rsa_key):
    with pytest.raises(HTTPException) as ei:
        deps.get_current_user(_creds(make_token(rsa_key, role="superuser")))
    assert ei.value.status_code == 403


# --- deps.require_roles ------------------------------------------------------

def test_require_roles_allows_matching(rsa_key):
    user = deps.get_current_user(_creds(make_token(rsa_key, role="admin")))
    dep = deps.require_roles("admin", "finance")
    assert dep(user=user) is user


def test_require_roles_forbids_unauthorized(rsa_key):
    user = deps.get_current_user(_creds(make_token(rsa_key, role="employee")))
    dep = deps.require_roles("admin", "finance")
    with pytest.raises(HTTPException) as ei:
        dep(user=user)
    assert ei.value.status_code == 403
