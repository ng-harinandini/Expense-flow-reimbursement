# ExpenseFlow Orchestrator — Next.js Enterprise Expense & Fraud Platform

ExpenseFlow Orchestrator is a production-ready Next.js 15 App Router platform for enterprise expense reimbursement, automated policy enforcement, fraud detection, and AWS serverless architecture orchestration. It combines multimodal AI (Gemini 2.5 Flash), Next.js Server Route Handlers, automated policy reasoning, and Role-Based Access Control (RBAC).

## Authentication & RBAC (AWS Cognito)

Identity is owned by an **AWS Cognito User Pool**. The FastAPI backend is a resource server: it
verifies Cognito-issued **ID tokens** (RS256 against the pool JWKS) and authorizes every request
from the token's claims. It never stores passwords.

- **Roles** live in the **`custom:role_id`** attribute — exactly one of `employee`, `manager`,
  `finance`, `admin`, `auditor`. This is the **single source of truth** for RBAC; **Cognito Groups
  are not used**. A missing or invalid `custom:role_id` is unauthorized (403).
- **Ownership**: employees are scoped to their own claims & receipts via **`custom:employeeId`**.
- **Identity**: **email** is the sign-in alias and admin-facing identifier; Cognito **`sub`** is the
  immutable canonical id (used as the audit `targetId`).

### Auth endpoints
- `POST /api/auth/login` — email + password → Cognito tokens (or a first-login challenge).
- `POST /api/auth/respond-challenge` — complete `NEW_PASSWORD_REQUIRED` → tokens.
- `GET /api/auth/me` — the authenticated caller's identity.

### User onboarding (admin-driven)
Normal onboarding flows **Admin → FastAPI → boto3 → Cognito** through the admin-only
`/api/admin/users*` API (create/list/get/update/**change-role**/enable/disable). Creating a user
sets `custom:role_id`; a role change updates `custom:role_id` (never Cognito Groups). **The frontend
must never call Cognito Admin APIs directly** — only these FastAPI endpoints, with an `admin` token.

### Provisioning
`backend/scripts/provision_cognito.py` is **idempotent**: it reuses the existing pool/app client,
ensures the `custom:role_id`/`custom:employeeId` attributes and `USER_PASSWORD_AUTH`, and ensures a
bootstrap admin (`BOOTSTRAP_ADMIN_EMAIL`) has `custom:role_id=admin` — without recreating resources.

### Backend configuration (`backend/.env`)
`COGNITO_USER_POOL_ID`, `COGNITO_APP_CLIENT_ID`, and — when the app client has a secret —
`COGNITO_APP_CLIENT_SECRET`. Region is derived from the pool-id prefix. AWS credentials resolve via
the standard boto3 chain. See `backend/.env.example`.

## Persistence & Domain Model (PostgreSQL)

All business state lives in **PostgreSQL** — there are no in-memory stores. Claims, employees,
policy rules, approval workflows, fraud results, comments, attachments, and the audit trail are
durable, foreign-keyed, and constraint-enforced. **Alembic is the sole owner of the schema**; the
application never creates or alters tables at runtime.

Four properties are enforced by the **database**, not by convention:

- **Claim transitions cannot be bypassed.** `claims.status` is written only through the repository's
  guarded transition, *and* a `claims_status_transition_guard` trigger holds the same edge list — so
  raw SQL cannot corrupt the lifecycle either. A test asserts the two agree edge-for-edge.
- **The audit trail is append-only.** An `audit_logs_append_only` trigger rejects `UPDATE` and
  `DELETE`. Every business action writes one audit record *in the same transaction* as the change,
  with actor, before/after snapshots, and the request/correlation ids.
- **Concurrent decisions are safe.** `claims.version` is an optimistic lock; a lost race returns
  `409 concurrent_update` rather than silently overwriting the other reviewer's decision.
- **A receipt can back only one claim** (`uq_claims_receipt_id`), and a claim's expense fields are
  editable only while it is a `Draft`.

Policy rules are **versioned and effective-dated**: publishing a change inserts a new version and
retires the previous one, so a historical claim can always be explained by the rule text that was
live when it was submitted.

### Claim lifecycle

```
Draft → Submitted → Processing_AI ─┬→ Auto_Approved ──→ Disbursed
                                   ├→ Manager_Review → Finance_Review → Approved → Disbursed
                                   │                 ↘ Rejected
                                   └→ Flagged_Fraud  → (cleared back to review, or Rejected)
```

`Rejected` and `Disbursed` are terminal. Only `finance`/`admin` may disburse. Status strings are
unchanged from the frontend contract; the API also accepts the canonical spellings `Processing`,
`Pending_Review`, and `Reimbursed` as input aliases.

### Setup

```bash
cd backend
python -m venv venv && venv/Scripts/activate      # or source venv/bin/activate
pip install -r requirements.txt
# set DATABASE_URL, or DB_USER/DB_PASSWORD/DB_HOST/DB_PORT/DB_NAME in .env
alembic upgrade head                              # creates the schema + seeds reference data
python -m uvicorn app.main:app --reload
```

`GET /api/health` is liveness (no database); `GET /api/ready` verifies PostgreSQL connectivity.

### Observability

Every request is assigned an `X-Request-Id` and carries an `X-Correlation-Id` (supplied by the
client or generated), both echoed as response headers, written into every structured JSON log line,
and persisted on audit and claim-history rows — so a database row can be traced back to the exact
HTTP call that produced it.

Errors share one envelope with a stable machine-readable code:

```json
{"detail": "...", "code": "invalid_state_transition",
 "context": {"currentStatus": "Manager_Review", "allowedNextStatuses": ["Approved", "Rejected"]},
 "requestId": "..."}
```

### Tests

```bash
cd backend
python -m pytest tests -q                                   # 532 tests
python -m pytest tests -q --cov=app --cov-report=term-missing
```

Database-backed tests run against a real PostgreSQL: a dedicated `<database>_test` (or
`TEST_DATABASE_URL`) is created if absent, migrated, and each test runs in a rolled-back
transaction. Your working database is never touched. With no PostgreSQL reachable, the DB suites
skip and the pure unit suites still run.

See `DECISIONS/ADR-004-phase1-durable-domain-model.md` and
`DECISIONS/ADR-005-claim-lifecycle-state-machine.md` for the design rationale.
