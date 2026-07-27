# Skill — Done Check

**Goal:** Define and run objective pass/fail criteria for any task.

## Procedure

1. For the task, enumerate every Done Check as a concrete, runnable command or an
   observable, unambiguous outcome.
2. Run each check; record command, exit code, and pass/fail.
3. A task is `done` only when **all** its Done Checks pass.

## What Makes a Good Done Check

- **Objective:** no interpretation needed ("`npm test` exits 0", not "tests look fine").
- **Runnable/observable:** a command or a clearly stated observation.
- **Traceable:** matches the task Goal directly.

## Reuse

Called by Hook 1 (Baseline), Hook 4 (Build), and Hook 5 (Verify) so the same checks
run identically at every stage.

## Output

A pass/fail record (command + result) for each check, written to `LOG.md` at
baseline/verify time.
