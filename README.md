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
