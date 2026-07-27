# Hook 5 — Verify

**Purpose:** Confirm the task's Done Checks pass. Drive a fix loop until they do.

## When to Run

After Build (Hook 4), before Handoff (Hook 6).

## Steps

1. Run every Done Check for the task and the project (use **Verification** +
   **Done Check** skills), the same way the Baseline ran them.
2. Compare against the Baseline in `LOG.md`. New failures = regressions caused here.
3. **If all pass** → record the passing run in `LOG.md`; proceed to Hook 6.
4. **If any fail** (loop):
   a. Create a Bug Task `BUG-<NNN>` in `TASKS/task.md` and a detail entry
      (full task format), linked to the active task.
   b. Generate a new plan `PLANS/plan-BUG-<NNN>.md` (Hook 2 style, approval-gated
      if the fix is non-trivial).
   c. Fix using the **Bug Fixing** skill.
   d. Re-run Done Checks. Repeat until green.

## Inputs

- Built code, Baseline record, task Done Checks.

## Outputs

- A green verification entry in `LOG.md`.
- Any bug tasks + their plans + fixes.

## Done Check

- All task and project Done Checks pass with no new regressions vs. baseline.

## Notes

- Never mark a task done with failing checks.
- Pre-existing baseline failures do not block, but must be noted.
