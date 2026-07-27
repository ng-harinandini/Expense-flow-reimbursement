# T001 — Receipt Upload, Textract Extraction & PostgreSQL Persistence API

> Primary Task. Detail file for the receipt upload + S3 + Textract + PostgreSQL feature.
> Source-of-truth status lives in `TASKS/task.md`.

## Primary Task

- **Goal:** Add a backend API that uploads a receipt (any type) to Amazon S3, extracts it
  with Amazon Textract (`AnalyzeExpense`), and persists the raw Textract JSON plus normalized
  fields and line items to PostgreSQL.
- **Constraints:**
  - **PostgreSQL only.** No SQLite fallback, no Docker Compose DB, no hardcoded creds, no
    assumed host/db/user/password.
  - **SQLAlchemy 2.x + psycopg (v3) + Alembic.** Alembic is the **only** schema owner.
    Application startup must **NOT** create or modify schema. **Never** `Base.metadata.create_all()`.
  - Config via **pydantic-settings** and environment variables. Priority: `DATABASE_URL`,
    else construct from `DB_*`. Provide `.env.example` with **placeholders only**; commit no secrets.
  - `database.py` engine must use `pool_pre_ping=True`, a sane `pool_recycle`, and pooling;
    never log DB passwords.
  - AWS via the normal **boto3 credential chain**; `AWS_REGION`, `S3_BUCKET_NAME`,
    `TEXTRACT_ENABLED` from env. No Docker Postgres required (all deps local/AWS).
  - **DB safety (critical):** never auto `DROP`/`TRUNCATE`/`DELETE`/reset/stamp. If migration
    state and actual schema disagree → **STOP** and report expected vs actual + safest strategy.
    Only manage tables owned by this application.
  - Graceful degradation for AWS: when `TEXTRACT_ENABLED` is false or boto3/creds/call fails,
    return a deterministic fallback extraction tagged `source="fallback"` (mirrors
    `gemini_service.py`), so local dev without AWS still works.
- **Inputs:** Existing backend (`main.py`, `app/api/`, `app/core/config.py`, `app/services/`,
  `app/schemas/`), `requirements.txt`.
- **Outputs:**
  - `POST /api/receipts/upload` (multipart → S3 → Textract → PostgreSQL), `GET /api/receipts`,
    `GET /api/receipts/{id}`.
  - `GET /health` (liveness) and `GET /ready` (readiness; verifies PostgreSQL connectivity).
  - `app/core/config.py` (pydantic-settings), `app/core/database.py`, `app/models/receipt.py`,
    `app/services/s3_service.py`, `app/services/textract_service.py`, Alembic
    (`alembic.ini`, `alembic/env.py`, initial migration), `.env.example`, updated `requirements.txt`.
- **Done Check:**
  - App boots without touching schema: `python -c "from app.main import app"` exits 0.
  - `.env.example` exists with placeholders only; no real credentials in repo.
  - Alembic wired to app config; `alembic upgrade head` creates all tables/indexes/FKs/enums;
    `alembic current` and `alembic downgrade -1` work. (Requires a real `DATABASE_URL`.)
  - With DB reachable: `POST /api/receipts/upload` returns `201` with a row persisted holding
    non-empty `raw_textract` (JSONB), `normalized_extraction`, and line items; original stored
    in S3 (or skipped with `source="fallback"` when AWS disabled).
  - `GET /api/receipts` lists it; `GET /api/receipts/{id}` returns raw JSON + fields + line items.
  - `GET /ready` returns healthy only when PostgreSQL is reachable.
- **Out of Scope:**
  - Auto-creating an ExpenseClaim from the receipt; frontend UI; auth/permissions.
  - Async multi-page Textract jobs; S3 pre-signed download URLs.
  - Migrating existing in-memory stores (claims/policy/audit) to Postgres (this task adds the
    receipts domain only).
- **Dependencies:** none.
- **Status:** in-progress (build complete; DB/AWS-dependent Done Checks deferred — see below)

---

## Build & Verification Status (2026-07-24)

**Build complete** — all atomic tasks M1-A1 → M7-A3 implemented on branch
`feature/T001-receipt-textract-postgres`.

**Verified now (no live DB/AWS required):**
- App boots without touching schema (`from app.main import app` exits 0).
- OpenAPI lists `POST /api/receipts/upload`, `GET /api/receipts`, `GET /api/receipts/{id}`,
  `GET /api/health`, `GET /api/ready`.
- Models map cleanly; `Base.metadata` = {receipts, receipt_fields, receipt_line_items};
  no `create_all` in app code.
- S3 + Textract fallback paths return the correct shape (`source="fallback"`).
- `/health` → 200; `/ready` → 503 `unconfigured` (correct with no DB).
- `alembic history` → `0001_initial_receipts (head)`; offline `alembic upgrade head --sql`
  emits the enum + 3 tables + indexes + FKs (`ON DELETE CASCADE`).

**Deferred (require owner-supplied `DATABASE_URL`, optionally AWS creds) — M8-A1 tail:**
- `alembic upgrade head` / `current` / `downgrade -1` against a real database.
- `POST /api/receipts/upload` → 201 with a persisted row (raw_textract + fields + line items).
- `GET /api/receipts` + `GET /api/receipts/{id}` reading from the DB.
- `/ready` returning `ready` when PostgreSQL is reachable.
- With AWS creds + `TEXTRACT_ENABLED=true`: real S3 upload + Textract extraction (`source="textract"`).

Activating the deferred checks needs **environment configuration only — no code changes.**

---

## Proposed Data Model (Alembic initial migration owns this)

**enum `extraction_status`** = `PENDING | PROCESSING | COMPLETED | FAILED`

**`receipts`**
- `id` UUID PK
- `file_name` TEXT, `content_type` TEXT, `file_size_bytes` INT
- `s3_bucket` TEXT NULL, `s3_key` TEXT NULL, `s3_region` TEXT NULL   ← S3 location
- `employee_id` TEXT NULL
- `extraction_status` extraction_status NOT NULL DEFAULT 'PENDING'
- `extraction_source` TEXT NULL   (`textract` | `fallback`)
- `raw_textract` JSONB NULL        ← verbatim AnalyzeExpense response
- `normalized_extraction` JSONB NULL   ← summary dict
- `vendor_name` TEXT NULL, `transaction_date` DATE NULL, `total_amount` NUMERIC(12,2) NULL, `currency` TEXT NULL
- `error_message` TEXT NULL
- `created_at` TIMESTAMPTZ, `updated_at` TIMESTAMPTZ
- index on `extraction_status`, `employee_id`

**`receipt_fields`** (AnalyzeExpense SummaryFields)
- `id` UUID PK, `receipt_id` UUID FK→receipts(id) ON DELETE CASCADE (indexed)
- `field_type` TEXT, `field_label` TEXT NULL, `field_value` TEXT NULL, `confidence` NUMERIC(5,2) NULL

**`receipt_line_items`**
- `id` UUID PK, `receipt_id` UUID FK→receipts(id) ON DELETE CASCADE (indexed)
- `line_number` INT, `description` TEXT NULL, `quantity` NUMERIC NULL,
  `unit_price` NUMERIC(12,2) NULL, `amount` NUMERIC(12,2) NULL, `raw` JSONB NULL

---

## Macro Tasks

### T001-M1 — Configuration (pydantic-settings + .env.example)
- **Goal:** Load DB + AWS config from env with `DATABASE_URL` priority, else build from `DB_*`.
- **Outputs:** rewritten `app/core/config.py` (BaseSettings), `.env.example` (placeholders), `requirements.txt` deps.
- **Done Check:** `settings.database_url` resolves from `DATABASE_URL` or `DB_*`; AWS/S3/Textract fields present; `.env.example` has placeholders only.
- **Out of Scope:** Real credentials. **Dependencies:** none. **Status:** todo

### T001-M2 — Database Connection Layer
- **Goal:** `app/core/database.py` with engine, session factory, declarative Base, `get_db`.
- **Constraints:** `pool_pre_ping=True`, `pool_recycle`, pooling; no password logging; no schema creation at import/startup.
- **Done Check:** `from app.core.database import engine, SessionLocal, Base, get_db` imports; no `create_all` anywhere.
- **Dependencies:** T001-M1. **Status:** todo

### T001-M3 — Data Model (SQLAlchemy 2.x)
- **Goal:** Declarative models for `receipts`, `receipt_fields`, `receipt_line_items` + enum.
- **Done Check:** Models import and map cleanly; relationships + cascade defined.
- **Dependencies:** T001-M2. **Status:** todo

### T001-M4 — Alembic (sole schema owner)
- **Goal:** Init Alembic; `env.py` reuses app config; author initial migration creating all
  tables/indexes/FKs/enums/constraints; add pre-apply schema-state inspection with STOP-on-conflict.
- **Constraints:** No `create_all`; no destructive ops; DB may pre-exist.
- **Done Check:** `alembic upgrade head`, `alembic current`, `alembic downgrade -1` work against a real `DATABASE_URL`; conflict path STOPs with a report.
- **Dependencies:** T001-M3. **Status:** todo

### T001-M5 — S3 Service
- **Goal:** Upload original document to S3 (boto3 credential chain), return bucket/key/region; safe skip + `fallback` when disabled/unavailable.
- **Done Check:** `upload_receipt_to_s3(bytes, key, content_type)` returns location dict or a fallback marker without raising.
- **Dependencies:** T001-M1. **Status:** todo

### T001-M6 — Textract Service
- **Goal:** `analyze_receipt_with_textract(...)` via `AnalyzeExpense` (S3Object when available else bytes); return raw JSON + normalized summary + line items; deterministic fallback.
- **Done Check:** Returns `{rawTextract, summary, lineItems, source}`; fallback covered.
- **Dependencies:** T001-M1. **Status:** todo

### T001-M7 — API Layer (receipts + health/ready)
- **Goal:** `app/api/receipts.py` (`POST /upload`, `GET ""`, `GET /{id}`) persisting via `get_db`;
  schemas in `schemas.py`; `GET /ready` verifies Postgres; register routers in `main.py`.
- **Done Check:** OpenAPI lists the three receipt routes + `/api/health` + `/api/ready`.
- **Dependencies:** T001-M3, T001-M5, T001-M6. **Status:** todo

### T001-M8 — Verification
- **Goal:** Boot check, `/health`, `/ready`, Alembic commands (if DB provided), end-to-end upload.
- **Done Check:** Primary Task Done Check passes; no regressions vs baseline.
- **Dependencies:** T001-M7. **Status:** todo

---

## Atomic Tasks (execution order)

- **M1-A1** Add deps to `requirements.txt` (`sqlalchemy>=2.0`, `alembic>=1.13`, `psycopg[binary]>=3.1`, `pydantic-settings>=2.0`, `boto3>=1.34`). **DC:** lines present. **Status:** todo
- **M1-A2** Rewrite `config.py` with pydantic-settings: keep PROJECT_NAME/VERSION/API_PREFIX/GEMINI_API_KEY; add `DATABASE_URL`/`DB_*` with priority resolver, `AWS_REGION`, `S3_BUCKET_NAME`, `TEXTRACT_ENABLED`, `RECEIPTS_RAW_DIR?`. **DC:** `settings.database_url` resolves both ways. Dep: M1-A1. **Status:** todo
- **M1-A3** Create `.env.example` (placeholders only). **DC:** file exists, no real secrets. Dep: M1-A2. **Status:** todo
- **M2-A1** Create `app/core/database.py` (engine/SessionLocal/Base/get_db, pool_pre_ping, pool_recycle). **DC:** imports; no create_all. Dep: M1-A2. **Status:** todo
- **M3-A1** Create `app/models/__init__.py` + `app/models/receipt.py` (3 models + enum). **DC:** models import/map. Dep: M2-A1. **Status:** todo
- **M4-A1** `alembic init alembic`; wire `alembic.ini`/`env.py` to `settings.database_url` + models' `Base.metadata`; no create_all. **DC:** `alembic current` runs (with DATABASE_URL). Dep: M3-A1. **Status:** todo
- **M4-A2** Author initial migration (all tables/indexes/FKs/enum). **DC:** `alembic upgrade head` then `downgrade -1` work. Dep: M4-A1. **Status:** todo
- **M4-A3** Add pre-apply schema-state inspection + STOP-on-conflict guidance (documented in migration/env + HANDOFF). **DC:** conflict path documented and non-destructive. Dep: M4-A2. **Status:** todo
- **M5-A1** Create `app/services/s3_service.py`. **DC:** upload returns location or fallback marker, never raises on missing AWS. Dep: M1-A2. **Status:** todo
- **M6-A1** Create `app/services/textract_service.py`. **DC:** returns raw+summary+lineItems+source; fallback covered. Dep: M1-A2. **Status:** todo
- **M7-A1** Add receipt request/response schemas to `schemas.py`. **DC:** import cleanly. Dep: M3-A1. **Status:** todo
- **M7-A2** Create `app/api/receipts.py` (upload/list/get via get_db). **DC:** router imports. Dep: M5-A1, M6-A1, M7-A1. **Status:** todo
- **M7-A3** Add `GET /ready` (DB connectivity) to health router; register receipts router in `main.py`. **DC:** OpenAPI shows routes; `/ready` checks DB. Dep: M7-A2, M2-A1. **Status:** todo
- **M8-A1** End-to-end verify (boot, health/ready, alembic if DB provided, upload flow). **DC:** Primary DC passes. Dep: M7-A3. **Status:** todo
