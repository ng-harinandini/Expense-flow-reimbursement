# Hook 2 — Planning

**Purpose:** Produce a concrete, reviewable implementation plan and pause for human
approval before any code is written.

## When to Run

After Baseline (Hook 1), before Branch (Hook 3).

## Steps

1. Read the active `TASKS/task-<ID>-*.md` and the baseline in `LOG.md`.
2. Using the **Task Planning** skill, write `PLANS/plan-<ID>.md` containing:
   - Objective & success criteria (mirror the task Done Check).
   - Ordered Atomic Task execution list with dependency order.
   - Files to add/change per Atomic Task.
   - Risks, unknowns, and rollback approach.
   - Test/verification strategy.
3. Set the plan status to `awaiting-approval`.
4. **STOP.** Present the plan and wait for explicit human approval.

## Inputs

- `TASKS/task-<ID>-*.md`, `LOG.md` baseline.

## Outputs

- `PLANS/plan-<ID>.md` with status `awaiting-approval`.

## Done Check

- Plan file exists, covers every Atomic Task, and human approval is recorded
  (flip plan status to `approved` and note it in `LOG.md`).

## Notes

- Do **not** proceed to Hook 3 until approval is explicit.
- If the plan changes after approval, re-request approval.
