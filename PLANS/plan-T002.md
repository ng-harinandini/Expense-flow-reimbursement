# PLAN — T002 · AWS Cognito Authentication & RBAC Enforcement

**Status:** `awaiting-approval`
**Task detail:** `TASKS/task-T002-rbac-auth.md`
**Owner decisions locked in:** real auth via **AWS Cognito** (Cognito owns identity & tokens; backend verifies); employees restricted to their own claims & receipts (ownership enforced).

---

## 1. Context / current state (baseline)

- Backend boots clean; migration head `0001_initial_receipts`. boto3 and `AWS_REGION` already present (used by S3/Textract).
- **No authentication or authorization exists today.** All 15 endpoints are open; `actorRole` is read from the request body and never validated.
- Storage is hybrid (receipts → Postgres; claims/audit/policy → in-memory). **With Cognito, identity is fully external — no local user table and no DB coupling for auth.**

## 2. Approach

Cognito User Pool is the identity provider. Users authenticate against Cognito; roles are modeled as **Cognito Groups** (`cognito:groups` claim); the employee link is a **`custom:employeeId`** attribute. The FastAPI backend is a *resource server*: it verifies the Cognito-signed JWT on every protected route and enforces a per-route role allowlist + row-level ownership for employees. A thin `/auth/login` proxy calls Cognito `InitiateAuth` so the existing frontend can get tokens without an Amplify rewrite.

### Build order (macro)
1. **M1 — Provision Cognito.** `scripts/provision_cognito.py` (boto3): create User Pool, the 5 role groups, an app client with `USER_PASSWORD_AUTH`, the `custom:employeeId` attribute, and seed one user per role mapped to `INITIAL_EMPLOYEES`. Committed + reproducible (no click-ops).
2. **M2 — Auth core.** Add `python-jose[cryptography]`. `app/core/security.py`: fetch + cache the pool JWKS, verify RS256 signature + `iss`/`aud`/`token_use=id`/`exp`. `app/api/auth.py`: `POST /auth/login` (→ boto3 `initiate_auth`, returns Cognito tokens; SECRET_HASH if the client has a secret), `GET /auth/me`. New settings: `COGNITO_USER_POOL_ID`, `COGNITO_APP_CLIENT_ID`, `COGNITO_APP_CLIENT_SECRET?`; `.env.example` update.
3. **M3 — Deps.** `app/core/deps.py`: `oauth2_scheme` (bearer), `get_current_user` (verify → identity{sub, role from `cognito:groups`, employeeId from `custom:employeeId`}; 401 on failure), `require_roles(*roles)` (403). Allowlist defined once.
4. **M4 — Enforce.** Attach `Depends(require_roles(...))` per route per the matrix (unchanged from the task file). Force `employeeId` from the token on create; filter list + deny cross-employee detail for the `employee` role; restrict `DISBURSE` to finance/admin in the action handler. `/`, `/health`, `/ready` stay public.
5. **M5 — Verify + docs.** `tests/test_auth_rbac.py` runs **offline** (dependency-override on `get_current_user`, and a locally-signed RSA keypair standing in for the JWKS to exercise the real verifier). Fix README + `ArchitectureOverview.tsx` matrix to real backend paths and name Cognito as the IdP. Write `DECISIONS/ADR-002-auth-rbac.md`.

## 3. Files touched

- **New:** `app/core/security.py`, `app/core/deps.py`, `app/api/auth.py`, `scripts/provision_cognito.py`, `tests/test_auth_rbac.py`, `DECISIONS/ADR-002-auth-rbac.md`.
- **Edited:** `requirements.txt` (`python-jose[cryptography]`), `app/core/config.py` (Cognito settings), `.env.example`, `app/main.py` (register auth router), all `app/api/*.py` (dependencies + ownership), `README.md`, `frontend/src/components/ArchitectureOverview.tsx`.
- **No Alembic migration, no `users` table, no `passlib`** — Cognito owns identity.

## 4. Token model (the part that bites if wrong)

- **Verify the ID token** — it carries both `cognito:groups` (role) and `custom:employeeId`; the access token would need a pre-token-generation Lambda to include the custom attribute. `aud` = app client id; `token_use` = `id`.
- Roles = Cognito Groups, named exactly as the 5 roles. A user in multiple groups → highest-privilege wins (documented order).
- JWKS is cached in-process with a refresh on unknown `kid`.

## 5. Done Checks (runnable)

Mirror the task file: login proxy happy/sad path; 401 unauth/expired/tampered; 403 wrong group; 200 allowed; employee ownership on claims+receipts list & detail; DISBURSE role split; health stays public. Offline via TestClient (no live Cognito needed for the logic; live pool needed only for the end-to-end login proxy check).

## 6. Risks / mitigations

- **Live verification needs a real pool + AWS creds** — but all logic is unit-testable offline (override + local JWKS). Only the `/auth/login`→Cognito round-trip needs the live pool; defer that one check like the T001 DB checks if creds aren't ready.
- **Frontend not yet sending tokens** — enforcing auth 401s the current frontend until it logs in and sends `Authorization: Bearer`. The `/auth/login` proxy keeps the change minimal; full frontend wiring is a separate task.
- **Custom attribute placement** — mitigated by verifying the ID token (see §4).
- **Secrets** — pool/client IDs and any client secret via env only; never committed.

## 7. Open Questions for the approval gate

1. **Provisioning mechanism** — committed **boto3 script** (recommended: fast, reproducible, no new toolchain) vs. CDK/Terraform IaC (more "correct" long-term, heavier)?
2. **Token acquisition** — backend **`/auth/login` proxy** (recommended: no frontend rewrite) vs. Cognito Hosted UI / Amplify on the frontend with the backend verify-only vs. both?
3. **Live vs. deferred verification** — do you have (or want to create) a Cognito pool now, or should I build + prove everything offline and defer the single live login-proxy check (like the T001 DB checks)?

On answers I proceed to Hook 3 (Branch `feature/T002-auth-rbac`) → Hook 4 (Build M1→M5).
