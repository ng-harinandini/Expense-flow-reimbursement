"""Idempotent Cognito provisioning for T002 (M1).

Reuses the EXISTING User Pool + App Client named by COGNITO_USER_POOL_ID / COGNITO_APP_CLIENT_ID
and converges them to the state the backend needs, without recreating or clobbering the manually
created resources:

  * ensures the `custom:role_id` and `custom:employeeId` string attributes exist,
  * ensures the app client has ALLOW_USER_PASSWORD_AUTH (merged into existing flows),
  * ensures the app client can read both custom attributes (only touches ReadAttributes when it is
    a restrictive list; `None` already means "read all"),
  * ensures BOOTSTRAP_ADMIN_EMAIL exists with `custom:role_id=admin` (verifies/sets, never recreates).

RBAC uses `custom:role_id` — this script never creates or uses Cognito Groups.
Re-running is safe: every step is a no-op when already satisfied.

Usage:
    BOOTSTRAP_ADMIN_EMAIL=admin@corp.com python scripts/provision_cognito.py
"""

from __future__ import annotations

import os
import sys

# Allow running as `python scripts/provision_cognito.py` from backend/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings  # noqa: E402
from app.services.cognito import cognito_client  # noqa: E402

VALID_ROLES = ("employee", "manager", "finance", "admin", "auditor")
REQUIRED_CUSTOM_ATTRS = ("role_id", "employeeId")  # stored as custom:role_id / custom:employeeId

# Keys we are allowed to echo back on update_user_pool_client (others reset to defaults if omitted,
# so we copy every present one to preserve the manual configuration).
_CLIENT_UPDATE_KEYS = (
    "ClientName", "RefreshTokenValidity", "AccessTokenValidity", "IdTokenValidity",
    "TokenValidityUnits", "ReadAttributes", "WriteAttributes", "ExplicitAuthFlows",
    "SupportedIdentityProviders", "CallbackURLs", "LogoutURLs", "DefaultRedirectURI",
    "AllowedOAuthFlows", "AllowedOAuthScopes", "AllowedOAuthFlowsUserPoolClient",
    "AnalyticsConfiguration", "PreventUserExistenceErrors", "EnableTokenRevocation",
    "EnablePropagateAdditionalUserContextData", "AuthSessionValidity",
)


def log(msg: str) -> None:
    print(f"[provision] {msg}")


def ensure_custom_attributes(client, pool_id: str) -> None:
    pool = client.describe_user_pool(UserPoolId=pool_id)["UserPool"]
    present = {a["Name"] for a in pool.get("SchemaAttributes", [])}
    to_add = [
        {
            "Name": name,
            "AttributeDataType": "String",
            "Mutable": True,
            "StringAttributeConstraints": {"MinLength": "0", "MaxLength": "2048"},
        }
        for name in REQUIRED_CUSTOM_ATTRS
        if f"custom:{name}" not in present
    ]
    if not to_add:
        log("custom attributes already present: custom:role_id, custom:employeeId")
        return
    client.add_custom_attributes(UserPoolId=pool_id, CustomAttributes=to_add)
    log(f"added custom attributes: {', '.join('custom:' + a['Name'] for a in to_add)}")


def ensure_app_client(client, pool_id: str, client_id: str) -> None:
    cfg = client.describe_user_pool_client(UserPoolId=pool_id, ClientId=client_id)["UserPoolClient"]

    params = {k: cfg[k] for k in _CLIENT_UPDATE_KEYS if k in cfg}
    changed = []

    flows = list(params.get("ExplicitAuthFlows") or [])
    if "ALLOW_USER_PASSWORD_AUTH" not in flows:
        flows.append("ALLOW_USER_PASSWORD_AUTH")
        params["ExplicitAuthFlows"] = flows
        changed.append("ExplicitAuthFlows += ALLOW_USER_PASSWORD_AUTH")

    # ReadAttributes: None == "read all" (custom attrs already visible) -> leave alone.
    # Only merge when it is an explicit, restrictive list.
    read_attrs = cfg.get("ReadAttributes")
    if read_attrs:
        merged = list(read_attrs)
        for attr in ("custom:role_id", "custom:employeeId"):
            if attr not in merged:
                merged.append(attr)
        if len(merged) != len(read_attrs):
            params["ReadAttributes"] = merged
            changed.append("ReadAttributes += custom:role_id/custom:employeeId")
    else:
        log("app client ReadAttributes is unset (reads all attributes) — custom attrs are visible")

    if not changed:
        log("app client already has USER_PASSWORD_AUTH and can read the custom attributes")
        return

    client.update_user_pool_client(UserPoolId=pool_id, ClientId=client_id, **params)
    log("app client updated: " + "; ".join(changed))


def ensure_bootstrap_admin(client, pool_id: str, email: str, name: str) -> None:
    email = email.strip().lower()
    try:
        user = client.admin_get_user(UserPoolId=pool_id, Username=email)
    except client.exceptions.UserNotFoundException:
        # Include `name`: the pool requires it, and it must exist before the first-login
        # NEW_PASSWORD_REQUIRED can complete.
        client.admin_create_user(
            UserPoolId=pool_id,
            Username=email,
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
                {"Name": "name", "Value": name},
                {"Name": "custom:role_id", "Value": "admin"},
            ],
            DesiredDeliveryMediums=["EMAIL"],
        )
        log(f"created bootstrap admin {email} (name={name}, custom:role_id=admin, FORCE_CHANGE_PASSWORD)")
        return

    # Existing user: converge role and name without recreating or touching the password.
    attrs = {a["Name"]: a.get("Value") for a in user.get("UserAttributes", [])}
    updates = []
    if attrs.get("custom:role_id") != "admin":
        updates.append({"Name": "custom:role_id", "Value": "admin"})
    if not attrs.get("name"):
        updates.append({"Name": "name", "Value": name})
    if not updates:
        log(f"bootstrap admin {email} already has custom:role_id=admin and name — no change")
        return
    client.admin_update_user_attributes(UserPoolId=pool_id, Username=email, UserAttributes=updates)
    log(f"updated existing user {email}: {', '.join(u['Name'] for u in updates)} (password untouched)")


def main() -> int:
    if not settings.cognito_configured:
        log("ERROR: COGNITO_USER_POOL_ID and COGNITO_APP_CLIENT_ID must be set.")
        return 2
    pool_id = settings.COGNITO_USER_POOL_ID
    client_id = settings.COGNITO_APP_CLIENT_ID
    log(f"region={settings.cognito_region} pool={pool_id} client={client_id}")

    client = cognito_client()
    ensure_custom_attributes(client, pool_id)
    ensure_app_client(client, pool_id, client_id)

    admin_email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL")
    if admin_email:
        # BOOTSTRAP_ADMIN_NAME is optional; default to the email local-part.
        admin_name = os.environ.get("BOOTSTRAP_ADMIN_NAME") or admin_email.split("@", 1)[0]
        ensure_bootstrap_admin(client, pool_id, admin_email, admin_name)
    else:
        log("BOOTSTRAP_ADMIN_EMAIL not set — skipping bootstrap admin step")

    log("done (idempotent).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
