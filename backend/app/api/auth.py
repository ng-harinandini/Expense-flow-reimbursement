"""Authentication API — AWS Cognito login proxy + pool connectivity check.

The backend does not store passwords. It exchanges credentials for Cognito tokens via
boto3 `initiate_auth` (USER_PASSWORD_AUTH) and reports whether the configured User Pool is
reachable. Cognito owns passwords, the temporary-password / first-login flow, and token issuance.

Endpoints:
  POST /auth/login                    email + password -> Cognito tokens (or a challenge, e.g. first login)
  POST /auth/respond-challenge        complete a login challenge (first-login NEW_PASSWORD_REQUIRED)
  POST /auth/forgot-password          email a password-reset confirmation code
  POST /auth/confirm-forgot-password  code + new password -> password reset
  POST /auth/logout                   global sign-out (revokes the caller's tokens everywhere)
  GET  /auth/me                       the authenticated caller's identity
  GET  /auth/pool-status              verify the configured Cognito User Pool + app client are reachable

There is no self-service signup: users are provisioned by an admin (see app/api/admin_users.py),
and complete the NEW_PASSWORD_REQUIRED challenge on first login.

Clients authenticate subsequent requests with the **access token**. The caller's role is not
carried in any token — it is read from the ``employees`` row matched on `sub`, both here and in
``app.core.deps``, so login never reports a role the API would refuse to honour.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from typing import Optional

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import CurrentUser, get_current_user, require_roles
from app.repositories.employee_repository import EmployeeRepository
from app.schemas.schemas import (
    AuthenticatedUserSchema,
    ConfirmForgotPasswordRequestSchema,
    ForgotPasswordRequestSchema,
    LoginRequestSchema,
    LoginResponseSchema,
    MessageResponseSchema,
    RespondChallengeRequestSchema,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])

# /logout reads the raw bearer credential itself rather than going through
# get_current_user, so a token Cognito still recognises can be revoked even when it no
# longer resolves to an active employee. auto_error=False so we raise our own 401.
_logout_bearer = HTTPBearer(auto_error=False)


def _cognito_client():
    """boto3 Cognito IDP client in the resolved region (credentials via the standard chain)."""
    return boto3.client(
        "cognito-idp",
        region_name=settings.cognito_region,
        config=Config(
            connect_timeout=10,
            read_timeout=30,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )


def _secret_hash(username: str) -> Optional[str]:
    """SECRET_HASH required only when the app client is configured with a secret."""
    if not settings.COGNITO_APP_CLIENT_SECRET:
        return None
    message = (username + settings.COGNITO_APP_CLIENT_ID).encode("utf-8")
    key = settings.COGNITO_APP_CLIENT_SECRET.encode("utf-8")
    return base64.b64encode(hmac.new(key, message, hashlib.sha256).digest()).decode()


def _decode_token_claims(token: str) -> dict:
    """Decode a JWT payload WITHOUT signature verification.

    Safe here because the token was just received directly from Cognito over TLS via
    initiate_auth — we are enriching our own login response, not trusting an inbound request.
    Inbound tokens on protected endpoints are verified against the pool JWKS in
    ``app.core.security.verify_access_token``.
    """
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # pad to a multiple of 4
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except (IndexError, ValueError, binascii.Error, json.JSONDecodeError):
        return {}


def _user_from_tokens(
    db: Session, id_token: Optional[str], access_token: Optional[str]
) -> Optional[AuthenticatedUserSchema]:
    """Build the login response's user block.

    `sub` comes from the token; the role, email and employee link come from the
    ``employees`` row, matching how ``app.core.deps`` authorizes later requests — so
    the client is never told a role the API would not honour.
    """
    claims = _decode_token_claims(id_token or access_token or "")
    sub = claims.get("sub")
    if not sub:
        return None

    employee = EmployeeRepository(db).get_by_cognito_sub(sub)
    if employee is None:
        # Authenticated with Cognito but not provisioned in the app yet.
        return AuthenticatedUserSchema(sub=sub, email=claims.get("email"))

    return AuthenticatedUserSchema(
        sub=sub,
        email=employee.email,
        name=employee.full_name,
        role=employee.role_name,
        employeeCode=employee.employee_code,
        employeeId=str(employee.id),
    )


def _require_configured() -> None:
    if not settings.cognito_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cognito is not configured. Set COGNITO_USER_POOL_ID and COGNITO_APP_CLIENT_ID.",
        )


@router.post("/login", response_model=LoginResponseSchema)
def login(payload: LoginRequestSchema, db: Session = Depends(get_db)):
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
    access_token = result.get("AccessToken")
    return LoginResponseSchema(
        accessToken=access_token,
        expiresIn=result.get("ExpiresIn"),
        user=_user_from_tokens(db, id_token, access_token),
    )


@router.post("/respond-challenge", response_model=LoginResponseSchema)
def respond_challenge(payload: RespondChallengeRequestSchema, db: Session = Depends(get_db)):
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
    access_token = result.get("AccessToken")
    return LoginResponseSchema(
        idToken=id_token,
        accessToken=access_token,
        refreshToken=result.get("RefreshToken"),
        expiresIn=result.get("ExpiresIn"),
        tokenType=result.get("TokenType", "Bearer"),
        user=_user_from_tokens(db, id_token, access_token),
    )


@router.post("/forgot-password", response_model=MessageResponseSchema)
def forgot_password(payload: ForgotPasswordRequestSchema):
    """Start a self-service password reset: Cognito emails a confirmation code."""
    _require_configured()

    kwargs = {"ClientId": settings.COGNITO_APP_CLIENT_ID, "Username": payload.email}
    secret_hash = _secret_hash(payload.email)
    if secret_hash:
        kwargs["SecretHash"] = secret_hash

    try:
        _cognito_client().forgot_password(**kwargs)
    except ClientError as e:
        error = e.response.get("Error", {})
        code = error.get("Code", "UnknownError")
        # Do not reveal whether the address is registered — that would make this
        # endpoint an account-enumeration oracle. Report success either way.
        if code in ("UserNotFoundException", "InvalidParameterException"):
            pass
        elif code in ("LimitExceededException", "TooManyRequestsException"):
            raise HTTPException(status_code=429, detail="Too many attempts; try again later.")
        elif code == "NotAuthorizedException":
            # e.g. the user is disabled, or has no verified delivery address.
            raise HTTPException(status_code=403, detail="Password reset is not available for this account.")
        else:
            raise HTTPException(status_code=502, detail=f"Cognito error: {code}")
    except (NoCredentialsError, BotoCoreError) as e:
        raise HTTPException(status_code=502, detail=f"Cognito unreachable: {e}")

    return MessageResponseSchema(
        detail="If that account exists, a reset code has been sent to its email address."
    )


@router.post("/confirm-forgot-password", response_model=MessageResponseSchema)
def confirm_forgot_password(payload: ConfirmForgotPasswordRequestSchema):
    """Complete a password reset with the emailed confirmation code."""
    _require_configured()

    kwargs = {
        "ClientId": settings.COGNITO_APP_CLIENT_ID,
        "Username": payload.email,
        "ConfirmationCode": payload.code,
        "Password": payload.newPassword,
    }
    secret_hash = _secret_hash(payload.email)
    if secret_hash:
        kwargs["SecretHash"] = secret_hash

    try:
        _cognito_client().confirm_forgot_password(**kwargs)
    except ClientError as e:
        error = e.response.get("Error", {})
        code = error.get("Code", "UnknownError")
        message = error.get("Message") or code
        if code == "CodeMismatchException":
            raise HTTPException(status_code=400, detail="Invalid confirmation code.")
        if code == "ExpiredCodeException":
            raise HTTPException(status_code=400, detail="Confirmation code has expired; request a new one.")
        if code == "InvalidPasswordException":
            raise HTTPException(status_code=400, detail=f"New password rejected: {message}")
        if code in ("LimitExceededException", "TooManyFailedAttemptsException"):
            raise HTTPException(status_code=429, detail="Too many attempts; try again later.")
        if code in ("UserNotFoundException", "NotAuthorizedException"):
            # Same non-enumerating posture as /forgot-password: an unknown user and a
            # bad/expired code are indistinguishable to the caller.
            raise HTTPException(status_code=400, detail="Invalid confirmation code.")
        raise HTTPException(status_code=502, detail=f"Cognito error: {code}")
    except (NoCredentialsError, BotoCoreError) as e:
        raise HTTPException(status_code=502, detail=f"Cognito unreachable: {e}")

    return MessageResponseSchema(detail="Password reset successfully. You can now sign in.")


@router.post("/logout", response_model=MessageResponseSchema)
def logout(credentials: Optional[HTTPAuthorizationCredentials] = Depends(_logout_bearer)):
    """Globally sign the caller out, revoking their tokens on every device.

    Takes the Cognito **access** token — the same credential used for every other request.
    Deliberately does not depend on ``get_current_user``: a caller whose employee record was
    deactivated must still be able to revoke their live token.
    """
    _require_configured()

    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        _cognito_client().global_sign_out(AccessToken=credentials.credentials)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "UnknownError")
        if code in ("NotAuthorizedException", "UserNotFoundException"):
            # Already expired/revoked — the caller's intent (be signed out) already holds.
            return MessageResponseSchema(detail="Signed out.")
        raise HTTPException(status_code=502, detail=f"Cognito error: {code}")
    except (NoCredentialsError, BotoCoreError) as e:
        raise HTTPException(status_code=502, detail=f"Cognito unreachable: {e}")

    return MessageResponseSchema(detail="Signed out.")


@router.get("/me", response_model=AuthenticatedUserSchema)
def me(current: CurrentUser = Depends(get_current_user)):
    """Return the authenticated caller's identity (role resolved from their employee record)."""
    return AuthenticatedUserSchema(
        sub=current.sub,
        email=current.email,
        name=current.display_name,
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
