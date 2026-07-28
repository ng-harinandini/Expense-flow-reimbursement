"""Authentication API — AWS Cognito login proxy + pool connectivity check.

The backend does not store passwords. It exchanges credentials for Cognito tokens via
boto3 `initiate_auth` (USER_PASSWORD_AUTH) and reports whether the configured User Pool is
reachable. Cognito owns passwords, the temporary-password / first-login flow, and token issuance.

Endpoints:
  POST /auth/login        email + password -> Cognito tokens (or a challenge, e.g. first login)
  GET  /auth/pool-status  verify the configured Cognito User Pool + app client are reachable
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import settings
from app.core.deps import CurrentUser, get_current_user, require_roles
from app.schemas.schemas import (
    AuthenticatedUserSchema,
    LoginRequestSchema,
    LoginResponseSchema,
    RespondChallengeRequestSchema,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])

# Cognito prepends "custom:" to custom attributes. Role is carried in custom:role_id.
ROLE_CLAIM = "custom:role_id"
EMPLOYEE_ID_CLAIM = "custom:employeeId"


def _cognito_client():
    """boto3 Cognito IDP client in the resolved region (credentials via the standard chain)."""
    return boto3.client("cognito-idp", region_name=settings.cognito_region)


def _secret_hash(username: str) -> Optional[str]:
    """SECRET_HASH required only when the app client is configured with a secret."""
    if not settings.COGNITO_APP_CLIENT_SECRET:
        return None
    message = (username + settings.COGNITO_APP_CLIENT_ID).encode("utf-8")
    key = settings.COGNITO_APP_CLIENT_SECRET.encode("utf-8")
    return base64.b64encode(hmac.new(key, message, hashlib.sha256).digest()).decode()


def _decode_id_token_claims(id_token: str) -> dict:
    """Decode the JWT payload WITHOUT signature verification.

    Safe here because the token was just received directly from Cognito over TLS via
    initiate_auth — we are enriching our own login response, not trusting an inbound request.
    Signature verification against the pool JWKS belongs on protected endpoints (T002 M3).
    """
    try:
        payload_b64 = id_token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # pad to a multiple of 4
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except (IndexError, ValueError, binascii.Error, json.JSONDecodeError):
        return {}


def _user_from_id_token(id_token: Optional[str]) -> Optional[AuthenticatedUserSchema]:
    if not id_token:
        return None
    claims = _decode_id_token_claims(id_token)
    if not claims:
        return None
    return AuthenticatedUserSchema(
        sub=claims.get("sub"),
        email=claims.get("email"),
        role=claims.get(ROLE_CLAIM),  # custom:role_id — the only RBAC source
        employeeId=claims.get(EMPLOYEE_ID_CLAIM),
    )


def _require_configured() -> None:
    if not settings.cognito_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cognito is not configured. Set COGNITO_USER_POOL_ID and COGNITO_APP_CLIENT_ID.",
        )


@router.post("/login", response_model=LoginResponseSchema)
def login(payload: LoginRequestSchema):
    """Exchange email + password for Cognito tokens via USER_PASSWORD_AUTH."""
    _require_configured()

    auth_params = {"USERNAME": payload.email, "PASSWORD": payload.password}
    secret_hash = _secret_hash(payload.email)
    if secret_hash:
        auth_params["SECRET_HASH"] = secret_hash

    try:
        resp = _cognito_client().initiate_auth(
            AuthFlow="USER_PASSWORD_AUTH",
            ClientId=settings.COGNITO_APP_CLIENT_ID,
            AuthParameters=auth_params,
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "UnknownError")
        if code in ("NotAuthorizedException", "UserNotFoundException"):
            # Do not leak which of the two failed.
            raise HTTPException(status_code=401, detail="Invalid email or password.")
        if code == "UserNotConfirmedException":
            raise HTTPException(status_code=403, detail="User is not confirmed.")
        if code == "PasswordResetRequiredException":
            raise HTTPException(status_code=403, detail="Password reset required.")
        if code == "InvalidParameterException":
            # Most commonly: USER_PASSWORD_AUTH not enabled on the app client.
            raise HTTPException(
                status_code=400,
                detail="Invalid auth request (is ALLOW_USER_PASSWORD_AUTH enabled on the app client?).",
            )
        raise HTTPException(status_code=502, detail=f"Cognito error: {code}")
    except (NoCredentialsError, BotoCoreError) as e:
        raise HTTPException(status_code=502, detail=f"Cognito unreachable: {e}")

    # Cognito may return a challenge (e.g. NEW_PASSWORD_REQUIRED for a first-login temp password)
    # instead of tokens. Surface it so the caller can complete the flow.
    if "ChallengeName" in resp:
        return LoginResponseSchema(
            challenge=resp["ChallengeName"],
            session=resp.get("Session"),
        )

    result = resp.get("AuthenticationResult", {})
    id_token = result.get("IdToken")
    return LoginResponseSchema(
        idToken=id_token,
        accessToken=result.get("AccessToken"),
        refreshToken=result.get("RefreshToken"),
        expiresIn=result.get("ExpiresIn"),
        tokenType=result.get("TokenType", "Bearer"),
        user=_user_from_id_token(id_token),
    )


@router.post("/respond-challenge", response_model=LoginResponseSchema)
def respond_challenge(payload: RespondChallengeRequestSchema):
    """Complete a login challenge (e.g. first-login NEW_PASSWORD_REQUIRED) → Cognito tokens."""
    _require_configured()

    responses = {"USERNAME": payload.email, "NEW_PASSWORD": payload.newPassword}
    secret_hash = _secret_hash(payload.email)
    if secret_hash:
        responses["SECRET_HASH"] = secret_hash
    # Supply any attributes the pool marks required but the user doesn't have yet — Cognito
    # demands them here (as userAttributes.<name>) before it will complete NEW_PASSWORD_REQUIRED.
    if payload.name:
        responses["userAttributes.name"] = payload.name
    for attr_name, attr_value in (payload.userAttributes or {}).items():
        responses[f"userAttributes.{attr_name}"] = attr_value

    try:
        resp = _cognito_client().respond_to_auth_challenge(
            ClientId=settings.COGNITO_APP_CLIENT_ID,
            ChallengeName=payload.challenge,
            Session=payload.session,
            ChallengeResponses=responses,
        )
    except ClientError as e:
        error = e.response.get("Error", {})
        code = error.get("Code", "UnknownError")
        message = error.get("Message") or code  # surface Cognito's real reason
        if code == "InvalidPasswordException":
            raise HTTPException(status_code=400, detail=f"New password rejected: {message}")
        if code in ("NotAuthorizedException", "ExpiredCodeException"):
            raise HTTPException(status_code=401, detail="Challenge session is invalid or expired; log in again.")
        if code in ("InvalidParameterException", "CodeMismatchException"):
            # e.g. "Required attributes missing: name" — pass name/userAttributes to fix.
            raise HTTPException(status_code=400, detail=message)
        raise HTTPException(status_code=502, detail=f"Cognito error: {code}: {message}")
    except (NoCredentialsError, BotoCoreError) as e:
        raise HTTPException(status_code=502, detail=f"Cognito unreachable: {e}")

    # A follow-on challenge is possible; surface it rather than pretending we have tokens.
    if "ChallengeName" in resp:
        return LoginResponseSchema(challenge=resp["ChallengeName"], session=resp.get("Session"))

    result = resp.get("AuthenticationResult", {})
    id_token = result.get("IdToken")
    return LoginResponseSchema(
        idToken=id_token,
        accessToken=result.get("AccessToken"),
        refreshToken=result.get("RefreshToken"),
        expiresIn=result.get("ExpiresIn"),
        tokenType=result.get("TokenType", "Bearer"),
        user=_user_from_id_token(id_token),
    )


@router.get("/me", response_model=AuthenticatedUserSchema)
def me(current: CurrentUser = Depends(get_current_user)):
    """Return the authenticated caller's identity (role from custom:role_id)."""
    return AuthenticatedUserSchema(
        sub=current.sub,
        email=current.email,
        role=current.role,
        employeeId=current.employee_id,
    )


@router.get("/pool-status", dependencies=[Depends(require_roles("admin"))])
def pool_status():
    """Verify the configured Cognito User Pool is reachable (admin only; no secrets returned)."""
    _require_configured()
    try:
        pool = _cognito_client().describe_user_pool(
            UserPoolId=settings.COGNITO_USER_POOL_ID
        )["UserPool"]
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "UnknownError")
        raise HTTPException(status_code=502, detail=f"Cannot reach Cognito pool: {code}")
    except (NoCredentialsError, BotoCoreError) as e:
        raise HTTPException(status_code=502, detail=f"Cognito unreachable: {e}")

    return {
        "status": "reachable",
        "region": settings.cognito_region,
        "userPoolId": settings.COGNITO_USER_POOL_ID,
        "name": pool.get("Name"),
        "estimatedNumberOfUsers": pool.get("EstimatedNumberOfUsers"),
        "appClientId": settings.COGNITO_APP_CLIENT_ID,
    }
