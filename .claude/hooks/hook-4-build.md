# Hook 4 — Build

**Purpose:** Execute the Atomic Tasks in dependency order, keeping status live.

## When to Run

After Branch (Hook 3), before Verify (Hook 5).

## Steps

1. Read the approved `PLANS/plan-<ID>.md` and the Atomic Task list in
   `TASKS/task-<ID>-*.md`.
2. Compute execution order from Atomic Task `Dependencies`. Only start a task whose
   dependencies are all `done`.
3. For each Atomic Task:
   a. Set its `Status` to `in-progress` (in the task file **and** implied by `task.md`).
   b. Apply the single logical change (use the **Implementation** skill).
   c. Run its Atomic Done Check (use **Testing** / **Done Check** skills).
   d. On pass → set `Status: done`, note in `LOG.md`.
      On fail → set `Status: blocked`, hand off to Hook 5 / **Bug Fixing** skill.
4. Commit after each Atomic Task (small, reviewable commits).

## Inputs

- Approved plan, Atomic Task list.

## Outputs

- Code changes on the feature branch, one commit per Atomic Task.
- Continuously updated task statuses.

## Done Check

- All Atomic Tasks are `done` (or explicitly `cancelled`/`blocked` with a reason).

## Notes

- One Atomic Task = one logical change. Do not batch unrelated edits.
- Keep `TASKS/task.md` and the detail file in sync at every status flip.
