# TASKS — Source of Truth

> This file is the authoritative index of task **status** and **dependencies**.
> Each Primary Task also has a detailed file: `TASKS/task-<ID>-<slug>.md`.
> Update this table the moment any status changes.

## Status Legend

`todo` · `in-progress` · `blocked` · `done` · `cancelled`

## Primary Tasks

| ID   | Title | Status | Depends On | Detail File |
|------|-------|--------|------------|-------------|
| T001 | Receipt Upload, Textract Extraction & PostgreSQL Persistence API | in-progress (build done; DB/AWS verify deferred) | — | `TASKS/task-T001-receipt-textract-upload.md` |
| T002 | AWS Cognito Authentication & Role-Based Access Control Enforcement | in-progress (M1–M6 built; 45 tests pass; live login verified to first-login challenge; provisioning idempotent) | — | `TASKS/task-T002-rbac-auth.md` |

## Active Bug Tasks

| ID  | Title | For Task | Status | Plan |
|-----|-------|----------|--------|------|
| _(none)_ | | | | |

## Dependency Notes

- T001 has no upstream dependencies. Atomic order:
  M1-A1, M1-A2 → M2-A1 → M3-A1 → M3-A2 → M4-A1 → M4-A2 → M4-A3 → M5-A1.
- T002 macro order: M1 (provision pool + bootstrap admin) → M2 (auth core) → M3 (deps + allowlist)
  → M4 (enforce + ownership) → M5 (admin user-management router) → M6 (verify + docs).
  No dependency on T001; live verification needs a Cognito pool + AWS creds (independent of T001's DB creds).
