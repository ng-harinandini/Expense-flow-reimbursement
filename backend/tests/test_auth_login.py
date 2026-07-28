"""Tests for the Cognito login flow (POST /api/auth/login, GET /api/auth/pool-status).

Unit tests mock the boto3 Cognito client, so they run offline with no AWS account.
The live test at the bottom is opt-in: it only runs when COGNITO_TEST_EMAIL /
COGNITO_TEST_PASSWORD are set in the environment (credentials are never committed).
"""

import base64
import json
import os

import pytest
from botocore.exceptions import ClientError, NoCredentialsError
from fastapi.testclient import TestClient

import app.api.auth as auth_module
from app.core import deps
from app.core.config import settings
from app.core.deps import CurrentUser
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _as_admin():
    app.dependency_overrides[deps.get_current_user] = lambda: CurrentUser(
        sub="sub-a", email="admin@corp.com", role="admin", employee_id=None, claims={}
    )


# --- helpers -----------------------------------------------------------------

def _b64url(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


def make_id_token(claims: dict) -> str:
    """A structurally-valid JWT (header.payload.sig) — signature is irrelevant to login,
    which decodes without verifying (the token came straight from Cognito)."""
    return f"{_b64url({'alg': 'none'})}.{_b64url(claims)}.signature"


def client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "InitiateAuth")


class FakeCognito:
    """Stand-in for boto3 cognito-idp; records what login sent."""

    def __init__(self, initiate=None, describe=None, respond=None):
        self._initiate = initiate
        self._describe = describe
        self._respond = respond
        self.last_auth_params = None
        self.last_challenge_responses = None

    def initiate_auth(self, **kwargs):
        self.last_auth_params = kwargs.get("AuthParameters")
        if isinstance(self._initiate, Exception):
            raise self._initiate
        return self._initiate

    def describe_user_pool(self, **kwargs):
        if isinstance(self._describe, Exception):
            raise self._describe
        return self._describe

    def respond_to_auth_challenge(self, **kwargs):
        self.last_challenge_responses = kwargs.get("ChallengeResponses")
        if isinstance(self._respond, Exception):
            raise self._respond
        return self._respond


@pytest.fixture
def use_fake(monkeypatch):
    """Install a FakeCognito and ensure Cognito looks configured."""
    def _install(initiate=None, describe=None, respond=None):
        fake = FakeCognito(initiate=initiate, describe=describe, respond=respond)
        monkeypatch.setattr(auth_module, "_cognito_client", lambda: fake)
        monkeypatch.setattr(settings, "COGNITO_USER_POOL_ID", "ap-south-1_test", raising=False)
        monkeypatch.setattr(settings, "COGNITO_APP_CLIENT_ID", "testclientid", raising=False)
        monkeypatch.setattr(settings, "COGNITO_APP_CLIENT_SECRET", None, raising=False)
        return fake
    return _install


# --- POST /api/auth/login ----------------------------------------------------

def test_login_success_returns_tokens_and_role(use_fake):
    id_token = make_id_token({
        "sub": "abc-123",
        "email": "harini@corp.com",
        "custom:role_id": "admin",
        "custom:employeeId": "emp-101",
        "cognito:groups": ["admin"],
    })
    use_fake(initiate={
        "AuthenticationResult": {
            "IdToken": id_token,
            "AccessToken": "access-xyz",
            "RefreshToken": "refresh-xyz",
            "ExpiresIn": 3600,
            "TokenType": "Bearer",
        }
    })
    r = client.post("/api/auth/login", json={"email": "harini@corp.com", "password": "pw"})
    assert r.status_code == 200
    body = r.json()
    assert body["idToken"] == id_token
    assert body["accessToken"] == "access-xyz"
    assert body["user"]["sub"] == "abc-123"
    assert body["user"]["role"] == "admin"            # <- custom:role_id integrated
    assert body["user"]["employeeId"] == "emp-101"
    assert body["user"]["email"] == "harini@corp.com"


def test_login_success_without_role_claim(use_fake):
    """Role is None (not an error) when custom:role_id isn't set/readable yet."""
    id_token = make_id_token({"sub": "s1", "email": "no-role@corp.com"})
    use_fake(initiate={"AuthenticationResult": {"IdToken": id_token, "AccessToken": "a"}})
    r = client.post("/api/auth/login", json={"email": "no-role@corp.com", "password": "pw"})
    assert r.status_code == 200
    assert r.json()["user"]["role"] is None


@pytest.mark.parametrize("code", ["NotAuthorizedException", "UserNotFoundException"])
def test_login_bad_credentials_returns_401(use_fake, code):
    use_fake(initiate=client_error(code))
    r = client.post("/api/auth/login", json={"email": "x@corp.com", "password": "wrong"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid email or password."


def test_login_challenge_returned_without_tokens(use_fake):
    use_fake(initiate={"ChallengeName": "NEW_PASSWORD_REQUIRED", "Session": "sess-1"})
    r = client.post("/api/auth/login", json={"email": "new@corp.com", "password": "temp"})
    assert r.status_code == 200
    body = r.json()
    assert body["challenge"] == "NEW_PASSWORD_REQUIRED"
    assert body["session"] == "sess-1"
    assert body["idToken"] is None


def test_login_invalid_parameter_maps_400(use_fake):
    # Typically: USER_PASSWORD_AUTH not enabled on the app client.
    use_fake(initiate=client_error("InvalidParameterException"))
    r = client.post("/api/auth/login", json={"email": "x@corp.com", "password": "pw"})
    assert r.status_code == 400


def test_login_no_aws_credentials_returns_502(use_fake):
    use_fake(initiate=NoCredentialsError())
    r = client.post("/api/auth/login", json={"email": "x@corp.com", "password": "pw"})
    assert r.status_code == 502


def test_login_secret_hash_added_when_client_secret_set(use_fake, monkeypatch):
    fake = use_fake(initiate={"AuthenticationResult": {"IdToken": make_id_token({"sub": "s"})}})
    monkeypatch.setattr(settings, "COGNITO_APP_CLIENT_SECRET", "shhh", raising=False)
    r = client.post("/api/auth/login", json={"email": "x@corp.com", "password": "pw"})
    assert r.status_code == 200
    assert "SECRET_HASH" in fake.last_auth_params


def test_login_unconfigured_returns_503(monkeypatch):
    monkeypatch.setattr(settings, "COGNITO_USER_POOL_ID", None, raising=False)
    r = client.post("/api/auth/login", json={"email": "x@corp.com", "password": "pw"})
    assert r.status_code == 503


# --- POST /api/auth/respond-challenge ---------------------------------------

def test_respond_challenge_success_returns_tokens(use_fake):
    id_token = make_id_token({"sub": "s1", "email": "a@corp.com", "custom:role_id": "admin"})
    fake = use_fake(respond={"AuthenticationResult": {"IdToken": id_token, "AccessToken": "acc"}})
    r = client.post("/api/auth/respond-challenge",
                    json={"email": "a@corp.com", "session": "sess-1", "newPassword": "N3w!pass"})
    assert r.status_code == 200
    body = r.json()
    assert body["idToken"] == id_token
    assert body["user"]["role"] == "admin"
    assert fake.last_challenge_responses["NEW_PASSWORD"] == "N3w!pass"
    assert fake.last_challenge_responses["USERNAME"] == "a@corp.com"


def test_respond_challenge_invalid_password_400(use_fake):
    use_fake(respond=client_error("InvalidPasswordException"))
    r = client.post("/api/auth/respond-challenge",
                    json={"email": "a@corp.com", "session": "s", "newPassword": "weak"})
    assert r.status_code == 400


def test_respond_challenge_expired_session_401(use_fake):
    use_fake(respond=client_error("NotAuthorizedException"))
    r = client.post("/api/auth/respond-challenge",
                    json={"email": "a@corp.com", "session": "stale", "newPassword": "N3w!pass"})
    assert r.status_code == 401


def test_respond_challenge_followon_challenge(use_fake):
    use_fake(respond={"ChallengeName": "SMS_MFA", "Session": "s2"})
    r = client.post("/api/auth/respond-challenge",
                    json={"email": "a@corp.com", "session": "s1", "newPassword": "N3w!pass"})
    assert r.status_code == 200
    assert r.json()["challenge"] == "SMS_MFA"


# --- GET /api/auth/pool-status ----------------------------------------------

def test_pool_status_success(use_fake):
    use_fake(describe={"UserPool": {"Name": "User pool - dk8yji", "EstimatedNumberOfUsers": 1}})
    _as_admin()  # pool-status is admin-only
    r = client.get("/api/auth/pool-status")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "reachable"
    assert body["name"] == "User pool - dk8yji"


def test_pool_status_requires_admin(use_fake):
    app.dependency_overrides[deps.get_current_user] = lambda: CurrentUser(
        sub="s", email="e@corp.com", role="employee", employee_id=None, claims={}
    )
    assert client.get("/api/auth/pool-status").status_code == 403


def test_pool_status_unconfigured_returns_503(monkeypatch):
    monkeypatch.setattr(settings, "COGNITO_USER_POOL_ID", None, raising=False)
    _as_admin()
    r = client.get("/api/auth/pool-status")
    assert r.status_code == 503


# --- Live integration (opt-in) ----------------------------------------------

_LIVE = os.environ.get("COGNITO_TEST_EMAIL") and os.environ.get("COGNITO_TEST_PASSWORD")


@pytest.mark.skipif(not _LIVE, reason="set COGNITO_TEST_EMAIL / COGNITO_TEST_PASSWORD to run live login")
def test_live_login_returns_tokens():
    r = client.post("/api/auth/login", json={
        "email": os.environ["COGNITO_TEST_EMAIL"],
        "password": os.environ["COGNITO_TEST_PASSWORD"],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    # Either real tokens, or a first-login challenge if the temp password wasn't made permanent.
    assert body.get("idToken") or body.get("challenge"), body
    if body.get("idToken"):
        assert body["user"]["email"]
