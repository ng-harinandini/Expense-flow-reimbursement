"""Cognito access-token verification.

Fetches and caches the User Pool's JWKS, then verifies an access token's RS256 signature and
standard claims (`iss`, `client_id`, `token_use`, `exp`).

Access tokens, not ID tokens: the access token is the credential the SPA sends on every request
and the only one Cognito's ``global_sign_out`` accepts, so a single token covers the whole
session. It carries no profile or custom attributes — no `email`, no `custom:role_id` — so
`app.core.deps` resolves the caller's role and identity from the ``employees`` table keyed on
`sub`, making the database the single source of truth for authorization.
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


def verify_access_token(token: str) -> Dict[str, Any]:
    """Verify a Cognito access token and return its claims, or raise TokenError."""
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
            issuer=issuer(),
            # Access tokens have no `aud`; the app client is carried in `client_id`,
            # which is checked explicitly below.
            options={"require_aud": False, "require_iss": True, "require_exp": True,
                     "verify_aud": False},
        )
    except ExpiredSignatureError:
        raise TokenError("token expired")
    except (JWTClaimsError, JWTError) as e:
        raise TokenError(f"invalid token: {e}")

    if claims.get("token_use") != "access":
        raise TokenError("not an access token")
    # Equivalent of the `aud` check for ID tokens: a token minted for a different app
    # client in the same pool must not be accepted here.
    if claims.get("client_id") != settings.COGNITO_APP_CLIENT_ID:
        raise TokenError("token was not issued for this app client")
    return claims
