# ADR-001 — Receipts Persistence Architecture (S3 + Textract + PostgreSQL)

- **Status:** Accepted
- **Date:** 2026-07-24
- **Task:** T001
- **Deciders:** Project owner (approved plan), Claude (implementation)

## Context

The `expense` backend needed a receipts feature: accept an uploaded receipt of any type,
extract structured data, and durably persist the raw extraction plus normalized fields and line
items. The existing backend used in-memory stores (`store.py`) and a Gemini OCR service with a
graceful offline fallback. The owner expanded the scope to durable persistence and AWS-native
extraction, with credentials to be supplied later.

## Decision

1. **PostgreSQL for persistence** via SQLAlchemy 2.x + psycopg (v3). Three tables — `receipts`,
   `receipt_fields`, `receipt_line_items` — plus an `extraction_status` PG enum.
2. **Alembic is the sole schema owner.** The application never calls `Base.metadata.create_all()`
   and never mutates schema at import/startup. The engine is built lazily so the app boots with
   no database configured.
3. **DB safety:** no automated `DROP`/`TRUNCATE`/`DELETE`/reset/`stamp`. On a migration-vs-actual
   schema conflict, STOP and report expected-vs-actual (documented in `alembic/README.md` and
   `alembic/env.py`).
4. **Amazon S3** stores the original document; **Amazon Textract `AnalyzeExpense`** performs
   extraction. Credentials resolve through the standard boto3 credential chain (env vars / shared
   config / instance role) — never read from app settings or committed.
5. **Graceful degradation** (mirrors `gemini_service.py`): when `TEXTRACT_ENABLED` is false or
   boto3/credentials/AWS calls are unavailable, S3 upload safe-skips and Textract returns a
   deterministic stub tagged `source="fallback"`. Local dev and this credential-less environment
   work end-to-end without AWS.
6. **Config** via pydantic-settings: `DATABASE_URL` takes priority, else built from `DB_*`.
   `.env.example` holds placeholders only; real `.env` is git-ignored.

## Consequences

- The app boots and serves `/health`, fallback extraction, and the receipts routes with **no**
  AWS account or database. `/ready` correctly reports 503 until a DB is reachable.
- The DB/AWS-dependent Done Checks (real `alembic upgrade`, persisted `POST /upload`, `/ready`
  healthy) are **deferred** until the owner supplies `DATABASE_URL` (and optionally AWS creds).
  Activating them requires **no code changes** — only environment configuration.
- Alembic-as-sole-owner means schema changes are explicit and reviewable; the STOP-on-conflict
  policy prevents accidental data loss against a pre-existing database.
- Scope intentionally excludes: auto-creating an ExpenseClaim from a receipt, frontend UI, auth,
  async multi-page Textract jobs, and S3 pre-signed download URLs.

## Alternatives Considered

- **In-memory + raw-JSON files** (original draft): rejected by owner in favor of durable RDBMS.
- **`create_all()` at startup**: rejected — violates single-owner schema management and risks
  drift against migrations.
- **SQLite fallback for local dev**: rejected — "PostgreSQL only" constraint; the Textract/S3
  fallback already covers credential-less local development without a second database dialect.
