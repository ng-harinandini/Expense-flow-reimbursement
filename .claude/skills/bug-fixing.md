# Skill — Bug Fixing

**Goal:** Resolve a verification failure through a tracked, minimal fix.

## Procedure

1. Open a Bug Task `BUG-<NNN>` in `TASKS/task.md` + a detail entry (full task format),
   linked to the failing Primary/Atomic Task.
2. Reproduce the failure; capture the exact error and the failing Done Check.
3. Diagnose root cause — fix the cause, not the symptom.
4. Plan the fix (`PLANS/plan-BUG-<NNN>.md`); approval-gate if non-trivial.
5. Add a regression test that fails before the fix and passes after.
6. Apply the minimal fix (via **Implementation**).
7. Re-run the failing Done Check and the full suite; note result in `LOG.md`.
8. Flip `BUG-<NNN>` to `done`.

## Principles

- Every bug fix ships with a regression test where feasible.
- Keep the fix scoped to the bug; unrelated improvements become their own tasks.

## Output

Fixed code, regression test, updated bug task status, `LOG.md` entry.
