# Hook 1 — Baseline

**Purpose:** Establish a known-good (or known-bad) starting point before any change,
so regressions are attributable.

## When to Run

After a task is created/resumed and broken down, **before** planning.

## Steps

1. Locate the current Done Checks for the active task and for the project as a whole
   (tests, linters, build, type-check, manual observations).
2. Run each Done Check exactly as it will be run at Verify time.
3. Record the baseline in `LOG.md`:
   - Command(s) run, exit codes, pass/fail counts.
   - Any *pre-existing* failures (these are NOT caused by this task).
4. If no Done Checks exist yet (greenfield), record that explicitly:
   `baseline = empty; no checks defined`.

## Inputs

- `TASKS/task-<ID>-*.md` (Done Check fields), project test/build config.

## Outputs

- A dated baseline entry in `LOG.md`.
- List of known pre-existing failures (carried into Verify so they are not counted
  as new regressions).

## Done Check (for the hook itself)

- `LOG.md` contains a baseline entry for this task with concrete results.

## Notes

- Uses the **Done Check** skill to enumerate checks.
- Never fix failures here — only observe and record.
