# Skill — Task Breakdown

**Goal:** Decompose a Primary Task into Macro Tasks, then into single-change Atomic Tasks.

## Procedure

1. Create `TASKS/task-<ID>-<slug>.md` for the Primary Task using the full task format.
2. Break the Primary Task into **Macro Tasks** — coherent deliverable chunks.
3. Break each Macro Task into **Atomic Tasks** — each a single logical change with an
   objective completion criterion.
4. Fill all nine fields for every level (Goal, Constraints, Inputs, Outputs, Done Check,
   Out of Scope, Dependencies, Status).
5. Register the Primary Task row in `TASKS/task.md` and keep it as the source of truth.

## Atomic Task Test

An Atomic Task is correctly sized if:
- It changes one logical thing.
- Its Done Check is objectively verifiable (a command or a clear observation).
- It can be committed on its own.

## Template

```markdown
### <ID> — <title>
- **Goal:**
- **Constraints:**
- **Inputs:**
- **Outputs:**
- **Done Check:**
- **Out of Scope:**
- **Dependencies:**
- **Status:** todo
```

## Output

`TASKS/task-<ID>-<slug>.md` with Macro + Atomic breakdown, plus a row in `TASKS/task.md`.
