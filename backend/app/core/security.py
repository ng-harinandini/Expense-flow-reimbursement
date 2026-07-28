"""Cognito ID-token verification.

Fetches and caches the User Pool's JWKS, then verifies an ID token's RS256 signature and
standard claims (`iss`, `aud`, `token_use`, `exp`). Role/authorization decisions live in
`app.core.deps` and read `custom:role_id` from the verified claims — never Cognito Groups.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import requests
from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTClaimsError, JWTError

from app.core.config import settings

_JWKS_TTL_SECONDS = 3600
_jwks_cache: Dict[str, Any] = {"keys": None, "fetched_at": 0.0}


class TokenError(Exception):
    """Raised when a token cannot be verified (bad signature, claims, or key)."""


def issuer() -> str:
    return (
        f"https://cognito-idp.{settings.cognito_region}.amazonaws.com/"
        f"{settings.COGNITO_USER_POOL_ID}"
    )


def _jwks_url() -> str:
    return f"{issuer()}/.well-known/jwks.json"


def _get_jwks(force: bool = False) -> List[dict]:
    now = time.time()
    cached = _jwks_cache["keys"]
    if not force and cached and (now - _jwks_cache["fetched_at"]) < _JWKS_TTL_SECONDS:
        return cached
    resp = requests.get(_jwks_url(), timeout=5)
    resp.raise_for_status()
    keys = resp.json()["keys"]
    _jwks_cache["keys"] = keys
    _jwks_cache["fetched_at"] = now
    return keys


def _find_key(kid: str) -> Optional[dict]:
    for key in _get_jwks():
        if key.get("kid") == kid:
            return key
    # A rotated key we haven't seen: refresh once before giving up.
    for key in _get_jwks(force=True):
        if key.get("kid") == kid:
            return key
    return None


def verify_id_token(token: str) -> Dict[str, Any]:
    """Verify a Cognito ID token and return its claims, or raise TokenError."""
    if not settings.cognito_configured:
        raise TokenError("Cognito is not configured")
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as e:
        raise TokenError(f"malformed token: {e}")

    key = _find_key(header.get("kid", ""))
    if key is None:
        raise TokenError("no matching signing key (kid)")

    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=settings.COGNITO_APP_CLIENT_ID,
            issuer=issuer(),
            options={"require_aud": True, "require_iss": True, "require_exp": True},
        )
    except ExpiredSignatureError:
        raise TokenError("token expired")
    except (JWTClaimsError, JWTError) as e:
        raise TokenError(f"invalid token: {e}")

    if claims.get("token_use") != "id":
        raise TokenError("not an ID token")
    return claims
