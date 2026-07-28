# HANDOFF

> Read this second (after `CLAUDE.md`). It is the single "where are we / what's next" pointer.

## Current State

- **Date:** 2026-07-27
- **Phase:** T001 **Build complete**, verify partial (DB/AWS deferred). **T002 planned — awaiting approval** (Hooks 1-2 done, paused at approval gate; no code written).
- **Active Task:** T002 — JWT Authentication & RBAC Enforcement (`todo`, plan awaiting approval). T001 still open pending DB/AWS creds.
- **Branch:** `feature/T001-receipt-textract-postgres` (T002 branch not yet created — Hook 3 runs on approval). No commits yet — commit only on owner request, summary-only messages per CLAUDE.md.
- **Baseline:** deps installed in `backend/venv`; app boots clean; migration head `0001_initial_receipts`; zero auth on all routers today.

## T002 — M2/M3/M4/M5 built (RBAC via custom:role_id); M1 + M6 docs remain

Branch `feature/T002-auth-rbac` (app repo). **RBAC source of truth = `custom:role_id`** (Cognito
Groups not used). Built & unit-tested offline (41 passed, 1 skipped):
- **M2** `app/api/auth.py` — `POST /auth/login` (boto3), `GET /auth/me`, admin-only `GET /auth/pool-status`.
- **M2/M3** `app/core/security.py` (JWKS RS256 verify) + `app/core/deps.py` (`get_current_user`,
  `require_roles`) — authorize exclusively from `custom:role_id`; 401 bad/missing token, 403 missing/invalid/unauthorized role.
- **M4** enforcement + ownership wired onto all routers per the matrix (claims/receipts ownership,
  DISBURSE finance/admin, admin-only ai-refine/aws/pool-status, etc.).
- **M5** `app/api/admin_users.py` — admin-only create/list/get/patch/role/enable/disable via Cognito
  Admin APIs; role change sets `custom:role_id`; audits with `sub`.

**Cognito-side setup still required for live login** (pool `ap-south-1_uZwsnjASV`): enable
`ALLOW_USER_PASSWORD_AUTH` on the app client; grant app client **read** on `custom:role_id` +
`custom:employeeId`; set `harinandini@ngenux.com` permanent password + `custom:role_id=admin`.
**Remaining T002:** M1 provisioning script; M6 README/frontend-matrix docs. Uncommitted.

## T002 — Earlier: Build started (M2 login proxy live-verified)

Branch `feature/T002-auth-rbac` (app repo, off `e45b404`). **Built + live-verified:** `POST
/api/auth/login` (boto3 `initiate_auth` USER_PASSWORD_AUTH) and `GET /api/auth/pool-status`
against owner pool `ap-south-1_uZwsnjASV` — pool reachable, USER_PASSWORD_AUTH enabled, but the
pool has **0 users** so a successful token needs a user created first (console or
`admin-create-user` + `admin-set-user-password --permanent`, or the future M5 admin API). New
files: `app/api/auth.py`, `.env.example`; edited `config.py` (Cognito settings, region derived
from pool-id), `schemas.py` (Login schemas), `main.py` (router). Uncommitted.
**Remaining T002:** M1 provisioning, M3 token verify/JWKS, M4 RBAC enforcement, M5 admin
user-management, M6 tests+docs.

## T002 — Original plan gate (still relevant for the rest)

**Auth = AWS Cognito, admin-driven onboarding** (plan revision approved). User Pool owns
identity/tokens; backend verifies Cognito JWT; roles = Cognito Groups; employee link =
`custom:employeeId`. Provisioning seeds only pool/client/groups + one bootstrap admin
(`BOOTSTRAP_ADMIN_EMAIL`); all onboarding flows Admin → FastAPI → boto3 → Cognito via new
admin-only router `app/api/admin_users.py`. Email = sign-in/admin-facing id; Cognito `sub` =
canonical internal id. Macros: M1 provision → M2 auth core → M3 deps → M4 enforce+ownership →
M5 admin user-mgmt → M6 verify+docs. See `PLANS/plan-T002.md`, `TASKS/task-T002-rbac-auth.md`,
`DECISIONS/ADR-002-auth-rbac.md`.

**Two open questions before build** (plan §7): (1) provisioning — boto3 script (recommended) vs
CDK/Terraform; (2) token acquisition — `/auth/login` proxy (recommended) vs Hosted UI/Amplify.
On answers I proceed Hook 3 (Branch `feature/T002-auth-rbac` off app-repo `e45b404`) → Hook 4
(Build M1→M6). Note: README + frontend matrix doc edits are part of M6 (they describe live
endpoints, deferred until built). Enforcing auth will 401 the current frontend until it sends a
Bearer token — frontend login/user-management UI wiring is a separate task.

## What Exists

- Workflow scaffolding + `DECISIONS/ADR-001-receipts-persistence-architecture.md`.
- Backend at `Expense-flow-reimbursement/backend/` with the **new receipts feature**:
  - `app/core/config.py` (pydantic-settings; `DATABASE_URL` || `DB_*`), `.env.example` (placeholders).
  - `app/core/database.py` (lazy engine, pool_pre_ping, `get_db`; no `create_all`).
  - `app/models/receipt.py` (receipts / receipt_fields / receipt_line_items + `extraction_status` enum).
  - `alembic/` (env wired to app config; initial migration `0001_initial_receipts`; `README.md` with STOP-on-conflict policy).
  - `app/services/s3_service.py`, `app/services/textract_service.py` (graceful fallback).
  - `app/api/receipts.py` (upload/list/get), `/api/ready` in `health.py`, router registered in `main.py`.

## Next Action (owner-gated — needs credentials)

To finish T001 verification (no code changes required, config only):
1. Provide a **PostgreSQL** connection: set `DATABASE_URL` (or `DB_USER`/`DB_HOST`/`DB_NAME`/`DB_PASSWORD`)
   in `backend/.env` or the environment.
2. Run migrations from `backend/`: `alembic upgrade head` → `alembic current` → (optional) `alembic downgrade -1`.
3. Start the API and confirm `GET /api/ready` returns `ready`, then `POST /api/receipts/upload` a file
   → expect `201` with a persisted row; `GET /api/receipts` + `/api/receipts/{id}` return it.
4. (Optional, real extraction) Provide AWS creds via the boto3 chain (`AWS_ACCESS_KEY_ID`/
   `AWS_SECRET_ACCESS_KEY`[/`AWS_SESSION_TOKEN`]), set `S3_BUCKET_NAME` + `TEXTRACT_ENABLED=true`
   → uploads store in S3 and extraction runs via Textract (`source="textract"`).
5. On green, flip T001 → `done` in `TASKS/task.md` and log the verify run in `LOG.md`.

## Blockers / Open Questions

- **Verification of DB/AWS Done Checks is blocked on owner-supplied `DATABASE_URL` (and optional AWS creds).**
  Everything that can be verified without them passes (see task detail Build & Verification Status).

## Ready for Review

- [x] Framework scaffolding (bootstrap).
- [x] `PLANS/plan-T001.md` (revised) — approved.
- [x] T001 implementation on `feature/T001-receipt-textract-postgres` — non-DB verification green.
- [ ] T001 DB/AWS end-to-end verification — pending credentials.
