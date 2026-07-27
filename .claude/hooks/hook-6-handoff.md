# Hook 6 — Handoff

**Purpose:** Persist everything learned, update state, and mark the task ready for
review so the next session (or person) can resume with zero context loss.

## When to Run

After Verify (Hook 5) passes.

## Steps

1. **Decisions** — for any notable architectural/technical choice, add an ADR under
   `DECISIONS/` (use the **Decision Logging** skill).
2. **Log** — append a completion entry to `LOG.md` (what shipped, verify result,
   commits/branch).
3. **Documentation** — update relevant docs (use the **Documentation** skill).
4. **Task status** — flip the Primary Task and its Atomic Tasks to `done` in
   `TASKS/task.md` and the detail file.
5. **Handoff** — rewrite `HANDOFF.md`: current state, branch, what shipped, next
   action, open questions. Mark the task **Ready for Review**.

## Inputs

- Green Verify result, task files, plan, code diff.

## Outputs

- New/updated `DECISIONS/ADR-*.md`, `LOG.md`, `HANDOFF.md`, updated task status.

## Done Check

- `HANDOFF.md` reflects reality; task marked `done` / Ready for Review; ADRs written
  for every non-obvious decision.

## Notes

- Uses the **Handoff** and **Code Review** skills.
- Handoff is not complete until a fresh reader could resume from files alone.
