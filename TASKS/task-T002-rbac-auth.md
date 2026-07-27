# T002 — JWT Authentication & Role-Based Access Control Enforcement

> Primary Task detail. Source of truth for status is `TASKS/task.md`.
> Status flips here mirror the index table.

## Primary Task

| Field | Value |
|-------|-------|
| **Goal** | Every API endpoint is protected by **AWS Cognito** JWT authentication and enforces a per-route role allowlist, with employees restricted to their own claims & receipts. |
| **Constraints** | Python/FastAPI backend only (`Expense-flow-reimbursement/backend`). **Identity is owned by an AWS Cognito User Pool** — Cognito issues the tokens; the backend only *verifies* them (no self-managed passwords, no local user table). Roles carried in the `cognito:groups` claim; groups named exactly `employee`, `manager`, `finance`, `admin`, `auditor` (matches `frontend/src/types.ts`). Employee↔claim linkage via a `custom:employeeId` attribute in the ID token. Token verification = RS256 signature against the pool JWKS + check `iss`, `aud`/`client_id`, `token_use`, `exp`. No breaking change to response shapes of existing endpoints. Secrets/pool IDs via env/`pydantic-settings`, never committed. boto3 uses its normal credential chain (as with S3/Textract). Git commits summary-only per CLAUDE.md. |
| **Inputs** | Existing routers (`app/api/*.py`), `app/schemas/schemas.py`, `app/core/config.py` (`AWS_REGION` already present; boto3 already a dep), `app/services/store.py` (`INITIAL_EMPLOYEES` → seed Cognito users/employeeId map), `frontend/src/types.ts` (role union → Cognito group names). |
| **Outputs** | `app/core/security.py` (Cognito JWKS fetch/cache + token verify), `app/api/auth.py` (`/auth/login` proxy to Cognito `InitiateAuth`, `/auth/me`), `app/core/deps.py` (`get_current_user`, `require_roles`), Cognito provisioning script (`scripts/provision_cognito.py` — pool, 5 groups, app client, `custom:employeeId`, seed users), role→route allowlist on every router, ownership filtering on claims & receipts, updated docs (README + frontend API matrix), `DECISIONS/ADR-002-auth-rbac.md`, tests. |
| **Done Check** | (1) `POST /api/auth/login` with a seeded Cognito user returns Cognito tokens; bad creds → 401. (2) Any protected endpoint with no/invalid/expired token → 401. (3) A valid token whose `cognito:groups` role is not in the route allowlist → 403. (4) An allowed role → 200 and correct behavior. (5) `employee` token on `GET /api/claims` and `GET /api/receipts` returns only that employee's rows (`custom:employeeId`); `manager`+ sees all. (6) `employee` cannot `GET /api/receipts/{id}` belonging to another employee (403/404). (7) `manager` cannot execute `DISBURSE`; `finance` can. (8) App still boots and `GET /api/health` stays public. |
| **Out of Scope** | Cognito Hosted-UI theming, refresh-token rotation/logout revocation, social/SAML federation, MFA, password-reset UI, rate limiting, frontend login UI wiring (beyond the docs matrix), migrating in-memory claims/audit/policy stores to Postgres. |
| **Dependencies** | None hard on other tasks. **Live verification requires a provisioned Cognito User Pool + AWS creds** (independent of T001's DB creds). Token-verification, allowlist, and ownership logic are unit-testable offline via dependency-override / a locally-signed JWKS (see M5). |
| **Status** | `todo` (awaiting plan approval) |

## Macro Tasks

### T002-M1 — Cognito User Pool provisioning
| Field | Value |
|-------|-------|
| Goal | A Cognito User Pool exists with a role-per-group model, an employeeId attribute, and seeded users covering every role. |
| Constraints | 5 groups named exactly `employee`/`manager`/`finance`/`admin`/`auditor`; custom attribute `custom:employeeId`; one app client with `USER_PASSWORD_AUTH` enabled (for the login proxy); reproducible (committed script, not click-ops). |
| Inputs | `INITIAL_EMPLOYEES` (name↔employeeId↔role), `AWS_REGION`. |
| Outputs | `scripts/provision_cognito.py` (boto3: create pool, groups, app client, custom attr, seed users + group assignment). |
| Done Check | Running the script yields a pool whose users can authenticate and whose ID token carries `cognito:groups` + `custom:employeeId`. |
| Out of Scope | IaC migration to CDK/Terraform (fast-follow if desired). |
| Dependencies | — (needs AWS creds to *run*, not to write) |
| Status | todo |

### T002-M2 — Auth core (Cognito token verify + login proxy)
| Field | Value |
|-------|-------|
| Goal | The backend verifies Cognito-issued tokens and can exchange credentials for tokens on behalf of the frontend. |
| Constraints | `python-jose[cryptography]`; RS256; fetch+cache JWKS from `https://cognito-idp.{region}.amazonaws.com/{poolId}/.well-known/jwks.json`; verify `iss`, `aud`/`client_id`, `token_use=id`, `exp`. `/auth/login` → boto3 `cognito-idp.initiate_auth` (USER_PASSWORD_AUTH; SECRET_HASH if client secret set). |
| Inputs | T002-M1 pool; `config.py`. |
| Outputs | `app/core/security.py` (JWKS + verify), `app/api/auth.py` (`POST /auth/login`, `GET /auth/me`), new settings (`COGNITO_USER_POOL_ID`, `COGNITO_APP_CLIENT_ID`, optional `COGNITO_APP_CLIENT_SECRET`), `.env.example` update. |
| Done Check | Valid Cognito token verifies and decodes to sub+groups+employeeId; invalid/expired/tampered → rejected; login proxy returns tokens for good creds, 401 for bad. |
| Out of Scope | Refresh-token rotation. |
| Dependencies | T002-M1 |

### T002-M3 — Authorization dependencies + allowlist
| Field | Value |
|-------|-------|
| Goal | Reusable FastAPI dependencies resolve the Cognito caller and enforce a per-route role allowlist. |
| Constraints | `get_current_user` verifies the bearer token (401) and maps `cognito:groups`→role + `custom:employeeId`→identity; `require_roles(*roles)` factory (403 on role mismatch); allowlist defined in one place. |
| Inputs | T002-M2 verify utils. |
| Outputs | `app/core/deps.py`; documented role→route matrix. |
| Done Check | Dependency returns identity for good token; raises 401/403 correctly in isolation. |
| Out of Scope | Row-level checks (M4). |
| Dependencies | T002-M2 |

### T002-M4 — Enforcement wiring + ownership
| Field | Value |
|-------|-------|
| Goal | Every router enforces its allowlist; employees are scoped to their own claims & receipts. |
| Constraints | Apply `Depends(require_roles(...))` per route per the matrix; `POST /claims` and `POST /receipts/upload` force `employeeId` = token identity; `GET` list/detail filter/deny by ownership for `employee`; `DISBURSE` restricted to finance/admin inside the action handler. Health/root stay public. |
| Inputs | M3 deps; all `app/api/*.py`. |
| Outputs | Updated routers; ownership logic in `claims.py` + `receipts.py`. |
| Done Check | Primary Done Checks 2–8 pass. |
| Out of Scope | New endpoints. |
| Dependencies | T002-M3 |

### T002-M5 — Verify + document
| Field | Value |
|-------|-------|
| Goal | Behavior is proven by tests and the docs reflect reality. |
| Constraints | `pytest` + FastAPI `TestClient`; matrix of role × endpoint expected status. Run **offline** without live Cognito by overriding `get_current_user` (dependency-override) and/or verifying against a locally-generated RSA keypair standing in for the pool JWKS. |
| Inputs | Full implementation. |
| Outputs | `tests/test_auth_rbac.py`; updated `README.md` + `frontend/.../ArchitectureOverview.tsx` matrix (paths + roles corrected to real backend, Cognito noted); `DECISIONS/ADR-002-auth-rbac.md`. |
| Done Check | All Done Checks green in CI/local; docs match implemented paths & roles and name Cognito as the IdP. |
| Out of Scope | Frontend login screen. |
| Dependencies | T002-M4 |

## Proposed Role → Endpoint Matrix

| Method & Path | Allowed roles |
|---|---|
| `POST /api/auth/login`, `GET /api/health`, `GET /api/ready`, `GET /` | public |
| `GET /api/auth/me` | any authenticated |
| `POST /api/claims` | employee (employeeId forced = self) |
| `GET /api/claims` | all roles; employee → own rows only |
| `POST /api/claims/{id}/action` | manager, finance, admin; `DISBURSE` → finance/admin only |
| `POST /api/receipts/upload` | employee (employeeId forced = self) |
| `GET /api/receipts`, `GET /api/receipts/{id}` | all roles; employee → own only |
| `POST /api/ai/ocr-extract` | any authenticated |
| `POST /api/ai/policy-reasoning` | manager, finance, admin |
| `POST /api/ai/refine-iam-policy` | admin |
| `GET /api/policy-rules` | any authenticated |
| `PUT /api/policy-rules` | finance, admin |
| `GET /api/audit-logs` | finance, admin, auditor |
| `GET /api/aws/export-code` | admin |
