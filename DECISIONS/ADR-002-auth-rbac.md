# ADR-002 — Authentication & RBAC via AWS Cognito with Admin-Driven Onboarding

- **Status:** Accepted (plan approved; implementation gated on T002 build)
- **Date:** 2026-07-27
- **Task:** T002
- **Deciders:** Project owner (approved plan), Claude (planning)

## Context

The `expense` backend had **no authentication or authorization**: all ~15 endpoints were open,
and `actorRole` was accepted from request bodies and never validated. RBAC was claimed in the
README/frontend but not enforced server-side. The backend models no users and no roles
(`INITIAL_EMPLOYEES` in `store.py` carries only profile fields; role is derived heuristically in
the frontend). T002 introduces real authentication and per-route role enforcement, with
employees restricted to their own claims & receipts.

## Decision

1. **AWS Cognito is the identity provider.** A Cognito User Pool owns users, passwords, the
   temporary-password/first-login (`FORCE_CHANGE_PASSWORD`) flow, and authentication. The FastAPI
   backend is a **resource server**: it only *verifies* Cognito-issued JWTs. No local user table,
   no password hashing, no self-issued tokens, no DB coupling for auth.
2. **Roles = the `custom:role_id` attribute (single source of truth).** `custom:role_id` holds
   exactly one of `employee`, `manager`, `finance`, `admin`, `auditor` (matches
   `frontend/src/types.ts`). **Cognito Groups are NOT used for RBAC and must never override
   `custom:role_id`.** Authorization is a reusable `require_roles(*roles)` dependency over a
   single per-route allowlist reading only `custom:role_id`, plus row-level ownership for
   `employee`. A token with a missing or out-of-set `custom:role_id` is unauthorized (403).
3. **Token model:** verify the **ID token** (RS256 against the pool JWKS; check `iss`,
   `aud`/`client_id`, `token_use`, `exp`) — it carries `custom:role_id` and `custom:employeeId`.
   **The app client must be granted read permission for both custom attributes**, or Cognito
   omits them from the ID token and every caller appears role-less.
4. **Identity keys:** **email** is the Cognito sign-in alias and the admin-facing identifier
   (user-management paths are keyed by `{email}`); **Cognito `sub` is the immutable canonical
   internal identity** — audit-log `targetId`, token subject, and any future application-DB
   mapping key on `sub`, never on email (which can change). `custom:employeeId` is an *optional*
   link to an employee id (e.g. `emp-101`), kept for this phase; business/employee mapping may
   migrate to the application DB later (fast-follow, out of scope).
5. **Admin-driven onboarding.** Provisioning (`scripts/provision_cognito.py`) seeds only the
   pool, app client (with read permission for the custom attributes), the `custom:role_id` +
   `custom:employeeId` attributes, and **one bootstrap admin** (email from `BOOTSTRAP_ADMIN_EMAIL`;
   `custom:role_id=admin`; Cognito temp password). It does **not** seed production users nor
   create RBAC groups. All subsequent onboarding flows **Admin → FastAPI → boto3 → Cognito**
   through an admin-only router `app/api/admin_users.py` (`/api/admin/users`, every route
   `require_roles("admin")`): create/invite, list, get, update-attributes, change-role, enable,
   disable — keyed by normalized email. **Role changes validate the value is one of the 5 and set
   `custom:role_id` via `admin_update_user_attributes` — never via Cognito Groups.** Every
   mutation writes an audit log entry via `add_audit_log(...)` with `sub` as `targetId`.
6. **Secret hygiene:** pool/client IDs and `BOOTSTRAP_ADMIN_EMAIL` via env/pydantic-settings.
   **No password is ever stored or logged locally, or passed via CLI arguments** — Cognito owns
   the credential lifecycle. boto3 resolves AWS credentials via its standard chain (as with S3/Textract).
7. **Frontend boundary:** the frontend never calls Cognito Admin APIs directly — only these
   FastAPI admin endpoints (with an `admin` token). A thin `/auth/login` proxy (Cognito
   `InitiateAuth`) lets the existing frontend obtain tokens without an Amplify rewrite.

## Consequences

- Enforcing auth will return **401 to the current frontend** until it logs in and sends a bearer
  token; full frontend login/user-management UI wiring is a separate task.
- Auth logic (token verify, allowlist, ownership, admin-only gating, missing/invalid-role
  rejection) is **unit-testable offline** via dependency-override + a locally-signed RSA keypair
  standing in for the JWKS. Only the live `/auth/login` proxy and Cognito Admin round-trips need a
  real pool + AWS creds; those checks are **deferred** like the T001 DB checks if creds aren't ready.
- **`custom:role_id` as a mutable string** (vs. immutable): chosen so admins can re-role a user in
  place; the value is always validated against the fixed 5-role set on write and on read.
- No Alembic migration, no `users` table, no `passlib` — identity is fully external.
- Cognito `sub` as canonical identity keeps the system resilient to email changes and eases a
  later move of business mapping into the application DB.

## Alternatives Considered

- **Self-managed JWT + local user store (bcrypt/passlib, Alembic `users` table):** rejected by
  owner in favor of Cognito — offloads password/credential lifecycle and MFA-readiness, avoids
  DB coupling.
- **Cognito Groups (`cognito:groups`) for roles:** rejected in favor of a single `custom:role_id`
  attribute — one authoritative value per user (no "multiple groups → which wins?" ambiguity),
  set/changed with one `admin_update_user_attributes` call, and trivially mapped to the frontend
  role union. Groups, if present, are ignored by authorization.
- **Trusted `X-Actor-Role` header or body-supplied role:** rejected — trivially spoofable, no
  real authentication.
- **Seeding all users at provisioning:** rejected — production onboarding must be admin-driven
  and audited; provisioning seeds only a bootstrap admin (test users behind a dev-only flag).
- **Verifying the access token:** rejected — `custom:employeeId` is not present without a
  pre-token-generation Lambda; the ID token carries both groups and the employee link.
