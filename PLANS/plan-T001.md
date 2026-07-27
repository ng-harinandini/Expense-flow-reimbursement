# Plan T001 — Receipt Upload, Textract Extraction & PostgreSQL Persistence API
Status: awaiting-approval
Supersedes: in-memory draft (revised per owner to PostgreSQL persistence + S3 storage).

## Objective & Success Criteria
Add a backend API that uploads a receipt (any type) to **Amazon S3**, extracts it with
**Amazon Textract** (`AnalyzeExpense`), and persists the **raw Textract JSON** plus normalized
fields and line items to **PostgreSQL** (schema owned solely by Alembic).

Success = Primary Task Done Check (see `TASKS/task-T001-receipt-textract-upload.md`):
1. App boots without touching schema: `python -c "from app.main import app"` exits 0.
2. `.env.example` exists with placeholders only; no real credentials committed.
3. Alembic wired to app config; `alembic upgrade head` builds all tables/indexes/FKs/enums;
   `alembic current` and `alembic downgrade -1` work (requires a real `DATABASE_URL`).
4. With DB reachable: `POST /api/receipts/upload` → `201`, row persisted with non-empty
   `raw_textract` (JSONB), `normalized_extraction`, and line items; original in S3 (or skipped
   with `source="fallback"` when AWS disabled).
5. `GET /api/receipts` lists it; `GET /api/receipts/{id}` returns raw JSON + fields + line items.
6. `GET /ready` returns healthy only when PostgreSQL is reachable.

## Key Design Decisions (confirm at approval)
- **PostgreSQL only.** No SQLite fallback, no Docker Compose DB, no hardcoded creds. Config via
  **pydantic-settings**: `DATABASE_URL` takes priority, else constructed from `DB_*` vars.
- **SQLAlchemy 2.x + psycopg (v3) + Alembic.** Alembic is the **only** schema owner. App startup
  never calls `Base.metadata.create_all()` and never mutates schema.
- **DB safety (critical):** no auto `DROP`/`TRUNCATE`/`DELETE`/reset/stamp. If migration state and
  actual schema disagree → **STOP** and report expected vs actual + safest strategy. Only manage
  tables owned by this app (receipts domain). Existing in-memory stores are untouched.
- **S3 for originals:** boto3 default credential chain; `AWS_REGION`, `S3_BUCKET_NAME`. Upload
  returns bucket/key/region; safe-skip + `fallback` marker when disabled/unavailable (mirrors the
  `gemini_service.py` graceful-degradation pattern).
- **Textract:** `analyze_expense` (receipt/invoice-specific, handles "any type"), sync single call
  (S3Object when available else raw bytes). Deterministic **fallback** stub when
  `TEXTRACT_ENABLED` is false or boto3/creds/call fails — local dev works with no AWS.
- **Scope boundary:** standalone extract-and-store. Does NOT auto-create an ExpenseClaim, no
  frontend, no auth, no async multi-page Textract, no pre-signed URLs.

## Data Model (Alembic initial migration owns this)
`enum extraction_status` = `PENDING | PROCESSING | COMPLETED | FAILED`
- **receipts** — id UUID PK; file_name/content_type/file_size_bytes; s3_bucket/s3_key/s3_region
  (nullable); employee_id; extraction_status (default PENDING); extraction_source
  (`textract`|`fallback`); raw_textract JSONB; normalized_extraction JSONB; vendor_name;
  transaction_date DATE; total_amount NUMERIC(12,2); currency; error_message; created_at/updated_at
  TIMESTAMPTZ; indexes on extraction_status, employee_id.
- **receipt_fields** — id UUID PK; receipt_id FK→receipts ON DELETE CASCADE (indexed); field_type;
  field_label; field_value; confidence NUMERIC(5,2).
- **receipt_line_items** — id UUID PK; receipt_id FK→receipts ON DELETE CASCADE (indexed);
  line_number; description; quantity NUMERIC; unit_price NUMERIC(12,2); amount NUMERIC(12,2); raw JSONB.

## Execution Order (by dependency) — all paths under `Expense-flow-reimbursement/backend/`
1. **M1-A1** — add deps to `requirements.txt` (`sqlalchemy>=2.0`, `alembic>=1.13`,
   `psycopg[binary]>=3.1`, `pydantic-settings>=2.0`, `boto3>=1.34`).
2. **M1-A2** — rewrite `app/core/config.py` as pydantic-settings BaseSettings (keep
   PROJECT_NAME/VERSION/API_PREFIX/GEMINI_API_KEY; add `DATABASE_URL`/`DB_*` resolver,
   `AWS_REGION`, `S3_BUCKET_NAME`, `TEXTRACT_ENABLED`).
3. **M1-A3** — create `.env.example` (placeholders only).
4. **M2-A1** — `app/core/database.py` (engine w/ pool_pre_ping + pool_recycle, SessionLocal, Base,
   `get_db`; no create_all).
5. **M3-A1** — `app/models/__init__.py` + `app/models/receipt.py` (3 models + enum).
6. **M4-A1** — `alembic init`; wire `alembic.ini`/`env.py` to `settings.database_url` +
   models' `Base.metadata`.
7. **M4-A2** — author initial migration (all tables/indexes/FKs/enum).
8. **M4-A3** — pre-apply schema-state inspection + STOP-on-conflict guidance (documented).
9. **M5-A1** — `app/services/s3_service.py` (upload → location dict, or fallback marker, never raises).
10. **M6-A1** — `app/services/textract_service.py` (`analyze_receipt_with_textract` → raw+summary+lineItems+source; fallback).
11. **M7-A1** — receipt request/response schemas in `app/schemas/schemas.py`.
12. **M7-A2** — `app/api/receipts.py` (`POST /upload`, `GET ""`, `GET /{id}`) via `get_db`.
13. **M7-A3** — add `GET /ready` (DB connectivity) to health router; register receipts router in `main.py`.
14. **M8-A1** — end-to-end verify (boot, health/ready, alembic if DB provided, upload flow).

## API Contract (proposed)
- `POST /api/receipts/upload` — `multipart/form-data`, field `file` (UploadFile), optional
  `employeeId`. Returns `201` with id, fileName, contentType, source (`textract`|`fallback`),
  s3 location (or null), normalized summary (vendorName/transactionDate/totalAmount/currency/lineItems),
  and `rawTextract`.
- `GET /api/receipts` — list stored records (summary-level).
- `GET /api/receipts/{id}` — full record incl. `rawTextract`, fields, line items; `404` if missing.
- `GET /api/health` — liveness (existing). `GET /api/ready` — readiness; verifies PostgreSQL.

## Verification Strategy
- Boot: `python -c "from app.main import app"` exits 0 (no schema touch at import).
- Install deps into venv.
- If a real `DATABASE_URL` is provided: `alembic upgrade head` → `alembic current` →
  `alembic downgrade -1`; then run server, `POST /upload` a sample → assert `201`, row persisted
  with non-empty `raw_textract` + line items; `GET` list + by-id; `/ready` healthy.
- No AWS creds here → the **fallback** extraction path is what gets verified locally; real
  Textract/S3 rely on the documented, stable boto3 contracts.

## Risks / Unknowns
- **No live PostgreSQL or AWS creds in this environment** → the DB-dependent Done Checks
  (`alembic upgrade`, real upload persistence, `/ready` healthy) can only be fully verified once
  the owner supplies a `DATABASE_URL` (and optionally AWS creds). Boot + import + fallback paths
  are verifiable here now.
- Alembic-as-sole-owner + STOP-on-conflict means if the target DB already has a conflicting
  `receipts` schema, we halt and report rather than mutate.
- boto3 + psycopg add install weight; acceptable for an AWS+Postgres feature.

## Rollback
Feature is additive to the receipts domain. Remove `receipts.py`, `s3_service.py`,
`textract_service.py`, `models/receipt.py`, `database.py`, the `alembic/` tree, the `/ready` +
router additions in `main.py`/health, and revert `config.py`/`requirements.txt`. No existing
endpoints change. Dropping created tables (if any) is a manual, owner-approved DB action — never
automated.
