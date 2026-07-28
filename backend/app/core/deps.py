"""FastAPI auth dependencies.

Authorization is derived **exclusively from the `custom:role_id` attribute** on the verified
Cognito ID token. Cognito Groups are ignored. The employee ownership link is `custom:employeeId`;
the canonical identity is `sub`.

- `get_current_user`  -> 401 on missing/invalid/expired token; 403 on missing/invalid role.
- `require_roles(...)` -> 403 when the caller's role isn't in the allowlist.
"""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import TokenError, verify_id_token

ROLE_CLAIM = "custom:role_id"
EMPLOYEE_ID_CLAIM = "custom:employeeId"

# The only valid application roles (mirrors frontend/src/types.ts UserRole).
VALID_ROLES = frozenset({"employee", "manager", "finance", "admin", "auditor"})

_bearer = HTTPBearer(auto_error=False)


class CurrentUser:
    """Verified caller identity resolved from the Cognito ID token."""

    def __init__(self, sub: Optional[str], email: Optional[str], role: str,
                 employee_id: Optional[str], claims: dict):
        self.sub = sub
        self.email = email
        self.role = role
        self.employee_id = employee_id
        self.claims = claims


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> CurrentUser:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = verify_id_token(credentials.credentials)
    except TokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    role = claims.get(ROLE_CLAIM)
    if role not in VALID_ROLES:
        # Authenticated, but no usable application role -> not authorized for anything.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing or invalid custom:role_id.",
        )

    return CurrentUser(
        sub=claims.get("sub"),
        email=claims.get("email"),
        role=role,
        employee_id=claims.get(EMPLOYEE_ID_CLAIM),
        claims=claims,
    )


def require_roles(*roles: str):
    """Dependency factory: allow only callers whose custom:role_id is in `roles`."""
    allowed = frozenset(roles)

    def _dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role for this operation.",
            )
        return user

    return _dependency
