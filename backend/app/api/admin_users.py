"""Admin user-management API — the Admin → FastAPI → boto3 → Cognito onboarding path.

Every route is admin-only. Users are addressed by normalized email (the pool's sign-in alias).
The application role lives in the `custom:role_id` attribute (one of the 5 roles) — this API
never touches Cognito Groups. Cognito owns passwords and the first-login flow; nothing sensitive
is stored or logged here. Every mutation is audited with the affected user's Cognito `sub`.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.config import settings
from app.core.deps import VALID_ROLES, CurrentUser, require_roles
from app.schemas.schemas import (
    AdminChangeRoleSchema,
    AdminCreateUserSchema,
    AdminUpdateUserSchema,
    AdminUserListSchema,
    AdminUserSummarySchema,
)
from app.services.cognito import cognito_client
from app.services.store import add_audit_log

ROLE_ATTR = "custom:role_id"
EMPLOYEE_ID_ATTR = "custom:employeeId"

router = APIRouter(prefix="/admin/users", tags=["Admin · Users"])


# --- helpers -----------------------------------------------------------------

def _require_configured() -> None:
    if not settings.cognito_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cognito is not configured.",
        )


def _norm_email(email: str) -> str:
    return email.strip().lower()


def _validate_role(role: str) -> str:
    if role not in VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"role must be one of {sorted(VALID_ROLES)}.",
        )
    return role


def _attrs_to_dict(attrs: List[dict]) -> Dict[str, str]:
    return {a["Name"]: a.get("Value") for a in (attrs or [])}


def _summary(username: str, attrs: Dict[str, str], enabled=None, user_status=None) -> AdminUserSummarySchema:
    return AdminUserSummarySchema(
        username=username,
        sub=attrs.get("sub"),
        email=attrs.get("email"),
        role=attrs.get(ROLE_ATTR),
        employeeId=attrs.get(EMPLOYEE_ID_ATTR),
        enabled=enabled,
        status=user_status,
    )


def _handle_client_error(e: ClientError) -> HTTPException:
    code = e.response.get("Error", {}).get("Code", "UnknownError")
    mapping = {
        "UserNotFoundException": (404, "User not found."),
        "UsernameExistsException": (409, "A user with that email already exists."),
        "InvalidParameterException": (400, "Invalid parameter."),
        "InvalidPasswordException": (400, "Password does not meet the pool policy."),
    }
    http_status, detail = mapping.get(code, (502, f"Cognito error: {code}"))
    return HTTPException(status_code=http_status, detail=detail)


def _get_user_raw(client, email: str) -> dict:
    try:
        return client.admin_get_user(UserPoolId=settings.COGNITO_USER_POOL_ID, Username=email)
    except ClientError as e:
        raise _handle_client_error(e)
    except (NoCredentialsError, BotoCoreError) as e:
        raise HTTPException(status_code=502, detail=f"Cognito unreachable: {e}")


def _sub_of(client, email: str) -> Optional[str]:
    attrs = _attrs_to_dict(_get_user_raw(client, email).get("UserAttributes", []))
    return attrs.get("sub")


# --- endpoints (all admin-only) ----------------------------------------------

@router.post("", status_code=status.HTTP_201_CREATED, response_model=AdminUserSummarySchema)
def create_user(payload: AdminCreateUserSchema, admin: CurrentUser = Depends(require_roles("admin"))):
    _require_configured()
    role = _validate_role(payload.role)
    email = _norm_email(payload.email)

    user_attributes = [
        {"Name": "email", "Value": email},
        {"Name": "email_verified", "Value": "true"},
        {"Name": ROLE_ATTR, "Value": role},
    ]
    if payload.employeeId:
        user_attributes.append({"Name": EMPLOYEE_ID_ATTR, "Value": payload.employeeId})
    if payload.name:
        user_attributes.append({"Name": "name", "Value": payload.name})

    client = cognito_client()
    try:
        # No TemporaryPassword: Cognito generates one and emails the invite (FORCE_CHANGE_PASSWORD).
        resp = client.admin_create_user(
            UserPoolId=settings.COGNITO_USER_POOL_ID,
            Username=email,
            UserAttributes=user_attributes,
            DesiredDeliveryMediums=["EMAIL"],
        )
    except ClientError as e:
        raise _handle_client_error(e)
    except (NoCredentialsError, BotoCoreError) as e:
        raise HTTPException(status_code=502, detail=f"Cognito unreachable: {e}")

    user = resp.get("User", {})
    attrs = _attrs_to_dict(user.get("Attributes", []))
    add_audit_log(admin.email or "admin", "admin", "USER_CREATE",
                  attrs.get("sub") or email, f"Created user {email} with role {role}")
    return _summary(user.get("Username"), attrs, user.get("Enabled"), user.get("UserStatus"))


@router.get("", response_model=AdminUserListSchema)
def list_users(
    limit: int = Query(25, ge=1, le=60),
    nextToken: Optional[str] = None,
    _admin: CurrentUser = Depends(require_roles("admin")),
):
    _require_configured()
    client = cognito_client()
    kwargs = {"UserPoolId": settings.COGNITO_USER_POOL_ID, "Limit": limit}
    if nextToken:
        kwargs["PaginationToken"] = nextToken
    try:
        resp = client.list_users(**kwargs)
    except ClientError as e:
        raise _handle_client_error(e)
    except (NoCredentialsError, BotoCoreError) as e:
        raise HTTPException(status_code=502, detail=f"Cognito unreachable: {e}")

    users = [
        _summary(u.get("Username"), _attrs_to_dict(u.get("Attributes", [])),
                 u.get("Enabled"), u.get("UserStatus"))
        for u in resp.get("Users", [])
    ]
    return AdminUserListSchema(users=users, nextToken=resp.get("PaginationToken"))


@router.get("/{email}", response_model=AdminUserSummarySchema)
def get_user(email: str, _admin: CurrentUser = Depends(require_roles("admin"))):
    _require_configured()
    client = cognito_client()
    resp = _get_user_raw(client, _norm_email(email))
    return _summary(resp.get("Username"), _attrs_to_dict(resp.get("UserAttributes", [])),
                    resp.get("Enabled"), resp.get("UserStatus"))


@router.patch("/{email}", response_model=AdminUserSummarySchema)
def update_user(email: str, payload: AdminUpdateUserSchema,
                admin: CurrentUser = Depends(require_roles("admin"))):
    _require_configured()
    email = _norm_email(email)
    updates = []
    if payload.employeeId is not None:
        updates.append({"Name": EMPLOYEE_ID_ATTR, "Value": payload.employeeId})
    if payload.name is not None:
        updates.append({"Name": "name", "Value": payload.name})
    if not updates:
        raise HTTPException(status_code=400, detail="No attributes to update.")

    client = cognito_client()
    try:
        client.admin_update_user_attributes(
            UserPoolId=settings.COGNITO_USER_POOL_ID, Username=email, UserAttributes=updates,
        )
    except ClientError as e:
        raise _handle_client_error(e)
    except (NoCredentialsError, BotoCoreError) as e:
        raise HTTPException(status_code=502, detail=f"Cognito unreachable: {e}")

    resp = _get_user_raw(client, email)
    attrs = _attrs_to_dict(resp.get("UserAttributes", []))
    add_audit_log(admin.email or "admin", "admin", "USER_UPDATE",
                  attrs.get("sub") or email, f"Updated attributes for {email}")
    return _summary(resp.get("Username"), attrs, resp.get("Enabled"), resp.get("UserStatus"))


@router.post("/{email}/role", response_model=AdminUserSummarySchema)
def change_role(email: str, payload: AdminChangeRoleSchema,
                admin: CurrentUser = Depends(require_roles("admin"))):
    _require_configured()
    role = _validate_role(payload.role)
    email = _norm_email(email)

    client = cognito_client()
    try:
        client.admin_update_user_attributes(
            UserPoolId=settings.COGNITO_USER_POOL_ID,
            Username=email,
            UserAttributes=[{"Name": ROLE_ATTR, "Value": role}],
        )
    except ClientError as e:
        raise _handle_client_error(e)
    except (NoCredentialsError, BotoCoreError) as e:
        raise HTTPException(status_code=502, detail=f"Cognito unreachable: {e}")

    resp = _get_user_raw(client, email)
    attrs = _attrs_to_dict(resp.get("UserAttributes", []))
    add_audit_log(admin.email or "admin", "admin", "USER_ROLE_CHANGE",
                  attrs.get("sub") or email, f"Set custom:role_id={role} for {email}")
    return _summary(resp.get("Username"), attrs, resp.get("Enabled"), resp.get("UserStatus"))


@router.post("/{email}/enable", response_model=AdminUserSummarySchema)
def enable_user(email: str, admin: CurrentUser = Depends(require_roles("admin"))):
    return _set_enabled(email, True, admin)


@router.post("/{email}/disable", response_model=AdminUserSummarySchema)
def disable_user(email: str, admin: CurrentUser = Depends(require_roles("admin"))):
    return _set_enabled(email, False, admin)


def _set_enabled(email: str, enabled: bool, admin: CurrentUser) -> AdminUserSummarySchema:
    _require_configured()
    email = _norm_email(email)
    client = cognito_client()
    op = client.admin_enable_user if enabled else client.admin_disable_user
    try:
        op(UserPoolId=settings.COGNITO_USER_POOL_ID, Username=email)
    except ClientError as e:
        raise _handle_client_error(e)
    except (NoCredentialsError, BotoCoreError) as e:
        raise HTTPException(status_code=502, detail=f"Cognito unreachable: {e}")

    resp = _get_user_raw(client, email)
    attrs = _attrs_to_dict(resp.get("UserAttributes", []))
    add_audit_log(admin.email or "admin", "admin",
                  "USER_ENABLE" if enabled else "USER_DISABLE",
                  attrs.get("sub") or email,
                  f"{'Enabled' if enabled else 'Disabled'} user {email}")
    return _summary(resp.get("Username"), attrs, resp.get("Enabled"), resp.get("UserStatus"))
