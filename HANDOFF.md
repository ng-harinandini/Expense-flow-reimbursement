# HANDOFF

> Read this second (after `CLAUDE.md`). It is the single "where are we / what's next" pointer.

## Current State

- **Date:** 2026-07-27
- **Phase:** T001 **Build complete**, verify partial (DB/AWS deferred). **T002 planned — awaiting approval** (Hooks 1-2 done, paused at approval gate; no code written).
- **Active Task:** T002 — JWT Authentication & RBAC Enforcement (`todo`, plan awaiting approval). T001 still open pending DB/AWS creds.
- **Branch:** `feature/T001-receipt-textract-postgres` (T002 branch not yet created — Hook 3 runs on approval). No commits yet — commit only on owner request, summary-only messages per CLAUDE.md.
- **Baseline:** deps installed in `backend/venv`; app boots clean; migration head `0001_initial_receipts`; zero auth on all routers today.

## T002 — Next Action (owner-gated: approve plan)

**Auth approach = AWS Cognito** (User Pool owns identity/tokens; backend verifies Cognito JWT; roles = Cognito Groups; employee link = `custom:employeeId`). No local user table / DB coupling. Read `PLANS/plan-T002.md` and answer the three open questions: (1) provisioning — boto3 script (recommended) vs IaC; (2) token acquisition — `/auth/login` proxy (recommended) vs Hosted UI/Amplify; (3) live pool now vs build-offline-and-defer the live login-proxy check. On answers I proceed Hook 3 (Branch `feature/T002-auth-rbac`) → Hook 4 (Build M1→M5). Note: enforcing auth will 401 the current frontend until it sends a Bearer token — frontend login wiring is a separate task.

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
