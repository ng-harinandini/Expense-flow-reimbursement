# PLAN — T002 · AWS Cognito Authentication & RBAC Enforcement

**Status:** `awaiting-approval`
**Task detail:** `TASKS/task-T002-rbac-auth.md`
**Owner decisions locked in:** real auth via **AWS Cognito** (Cognito owns identity & tokens; backend verifies); employees restricted to their own claims & receipts (ownership enforced); **normal user onboarding is admin-driven** (Admin → FastAPI → boto3 → Cognito); **email** = sign-in alias + admin-facing identifier, Cognito **`sub`** = canonical internal identity; **role = `custom:role_id` attribute (single source of truth; Cognito Groups not used for RBAC)**; provisioning seeds only pool/client/custom-attributes + one env-configured bootstrap admin.

---

## 1. Context / current state (baseline)

- Backend boots clean; migration head `0001_initial_receipts`. boto3 and `AWS_REGION` already present (used by S3/Textract).
- **No authentication or authorization exists today.** All 15 endpoints are open; `actorRole` is read from the request body and never validated.
- Storage is hybrid (receipts → Postgres; claims/audit/policy → in-memory). **With Cognito, identity is fully external — no local user table and no DB coupling for auth.**

## 2. Approach

Cognito User Pool is the identity provider. Users authenticate against Cognito; the **role is the `custom:role_id` attribute** (exactly one of the 5 roles) — the single source of truth for RBAC. **Cognito Groups are not used for authorization.** The employee link is a **`custom:employeeId`** attribute. The FastAPI backend is a *resource server*: it verifies the Cognito-signed JWT on every protected route and enforces a per-route role allowlist (from `custom:role_id`) + row-level ownership for employees. A thin `/auth/login` proxy calls Cognito `InitiateAuth` so the existing frontend can get tokens without an Amplify rewrite. **User lifecycle is admin-driven**: provisioning seeds only the pool + one bootstrap admin, and all subsequent onboarding flows Admin → FastAPI → boto3 → Cognito through an admin-only user-management router. The frontend never calls Cognito Admin APIs.

### Build order (macro)
1. **M1 — Provision Cognito.** `scripts/provision_cognito.py` (boto3): create User Pool (email sign-in alias), the custom attributes `custom:role_id` + `custom:employeeId`, an app client with `USER_PASSWORD_AUTH` **and read permission for both custom attributes** (so they reach the ID token), and **one bootstrap admin** (email from `BOOTSTRAP_ADMIN_EMAIL`; `custom:role_id=admin`; Cognito-generated temp password + FORCE_CHANGE_PASSWORD). **No Cognito Groups**, no production user seeding; test/dev users behind a `--with-test-users` flag. Committed + reproducible (no click-ops).
2. **M2 — Auth core.** Add `python-jose[cryptography]`. `app/core/security.py`: fetch + cache the pool JWKS, verify RS256 signature + `iss`/`aud`/`token_use=id`/`exp`. `app/api/auth.py`: `POST /auth/login` (→ boto3 `initiate_auth`, returns Cognito tokens; SECRET_HASH if the client has a secret), `GET /auth/me`. New settings: `COGNITO_USER_POOL_ID`, `COGNITO_APP_CLIENT_ID`, `COGNITO_APP_CLIENT_SECRET?`, `BOOTSTRAP_ADMIN_EMAIL`; `.env.example` update.
3. **M3 — Deps.** `app/core/deps.py`: bearer scheme, `get_current_user` (verify → identity{sub, role from **`custom:role_id`**, employeeId from `custom:employeeId`}; 401 on bad/missing token; **403 when `custom:role_id` is missing or not one of the 5**), `require_roles(*roles)` (403 when the role isn't allowed). **Cognito Groups ignored.** Allowlist defined once.
4. **M4 — Enforce.** Attach `Depends(require_roles(...))` per route per the matrix (unchanged from the task file). Force `employeeId` from the token on create; filter list + deny cross-employee detail for the `employee` role; restrict `DISBURSE` to finance/admin in the action handler. `/`, `/health`, `/ready` stay public.
5. **M5 — Admin user-management router.** `app/api/admin_users.py` (prefix `/api/admin/users`, every route `require_roles("admin")`): create/invite, list, get, update-attributes, change-role, enable, disable — all keyed by normalized email, backed by Cognito Admin APIs (`admin_create_user`, `list_users`, `admin_get_user`, `admin_update_user_attributes`, `admin_enable_user`, `admin_disable_user`). Create sets `custom:role_id` + `custom:employeeId`; **role change validates the value is one of the 5 then sets `custom:role_id` via `admin_update_user_attributes`** (no Cognito Group manipulation). Every mutation audits with `sub` as `targetId`. **No passwords stored/logged locally.**
6. **M6 — Verify + docs.** `tests/test_auth_rbac.py` runs **offline** (dependency-override on `get_current_user`, and a locally-signed RSA keypair standing in for the JWKS to exercise the real verifier); includes admin-only user-management coverage + the role-change validation (value must be one of the 5) + missing/invalid `custom:role_id` → 403. Fix README + `ArchitectureOverview.tsx` matrix to real backend paths, add the admin user-management endpoints, and name Cognito as the IdP + the Admin→FastAPI→boto3→Cognito onboarding rule. Write `DECISIONS/ADR-002-auth-rbac.md`.

## 3. Files touched

- **New:** `app/core/security.py`, `app/core/deps.py`, `app/api/auth.py`, `app/api/admin_users.py`, `scripts/provision_cognito.py`, `tests/test_auth_rbac.py`, `DECISIONS/ADR-002-auth-rbac.md`.
- **Edited:** `requirements.txt` (`python-jose[cryptography]`), `app/core/config.py` (Cognito settings + `BOOTSTRAP_ADMIN_EMAIL`), `.env.example`, `app/main.py` (register auth + admin-users routers), all `app/api/*.py` (dependencies + ownership), `README.md`, `frontend/src/components/ArchitectureOverview.tsx`.
- **No Alembic migration, no `users` table, no `passlib`** — Cognito owns identity.

## 4. Token model (the part that bites if wrong)

- **Verify the ID token** — it carries the custom attributes `custom:role_id` and `custom:employeeId` (custom attributes ride the ID token, not the access token). `aud` = app client id; `token_use` = `id`.
- **Role = `custom:role_id`**, exactly one of the 5 values. A missing or out-of-set value is treated as unauthorized (403). Cognito Groups, if any exist, are ignored for authorization.
- **The app client must have read permission for `custom:role_id` and `custom:employeeId`**, or Cognito omits them from the ID token and every caller looks role-less.
- JWKS is cached in-process with a refresh on unknown `kid`.

## 5. Done Checks (runnable)

Mirror the task file: login proxy happy/sad path; 401 unauth/expired/tampered; **403 when `custom:role_id` is missing, invalid, or not in the route allowlist**; 200 allowed; employee ownership on claims+receipts list & detail; DISBURSE role split; health stays public. **Admin user-management:** only `admin` reaches `/api/admin/users*` (others 403, unauth 401); create → sets `custom:role_id` + `custom:employeeId` + FORCE_CHANGE_PASSWORD; role change validates the value is one of the 5 and sets `custom:role_id` (Cognito Groups never touched); disable blocks login / enable restores. Offline via TestClient (no live Cognito needed for the logic; live pool needed only for the end-to-end login proxy + Cognito Admin round-trips).

## 6. Risks / mitigations

- **Live verification needs a real pool + AWS creds** — but all logic is unit-testable offline (override + local JWKS). Only the `/auth/login`→Cognito round-trip needs the live pool; defer that one check like the T001 DB checks if creds aren't ready.
- **Frontend not yet sending tokens** — enforcing auth 401s the current frontend until it logs in and sends `Authorization: Bearer`. The `/auth/login` proxy keeps the change minimal; full frontend wiring is a separate task.
- **Custom attribute placement** — mitigated by verifying the ID token (see §4).
- **Secrets** — pool/client IDs and any client secret via env only; never committed.

## 7. Open Questions for the approval gate

**Resolved:** identity model (email = sign-in alias + admin-facing key; `sub` = canonical internal identity); **role model = `custom:role_id` attribute, single source of truth (Cognito Groups not used for RBAC)**; user seeding (provision only pool/client/custom-attributes + one env-configured bootstrap admin; no production seeding); onboarding (admin-driven via FastAPI). Remaining:

1. **Provisioning mechanism** — committed **boto3 script** (recommended: fast, reproducible, no new toolchain) vs. CDK/Terraform IaC (more "correct" long-term, heavier)?
2. **Token acquisition** — backend **`/auth/login` proxy** (recommended: no frontend rewrite) vs. Cognito Hosted UI / Amplify on the frontend with the backend verify-only vs. both?
3. **Live vs. deferred verification** — do you have (or want to create) a Cognito pool now, or should I build + prove everything offline and defer the live login-proxy + Cognito Admin round-trips (like the T001 DB checks)?

On answers I proceed to Hook 3 (Branch `feature/T002-auth-rbac`) → Hook 4 (Build M1→M6).
