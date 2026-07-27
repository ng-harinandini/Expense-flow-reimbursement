# Skill — Implementation

**Goal:** Apply one Atomic Task as a single, clean, reviewable change.

## Procedure

1. Confirm all dependencies of the Atomic Task are `done`.
2. Set the Atomic Task `Status: in-progress`.
3. Read the surrounding code; match its style, naming, and idioms.
4. Make the minimal change that satisfies the Atomic Task Goal — nothing extra.
5. Run the Atomic Done Check (delegate to **Testing** / **Done Check**).
6. Commit with a message referencing the Atomic Task ID.
7. Flip `Status: done` (or `blocked` with a reason) and note it in `LOG.md`.

## Principles

- One logical change per task. Resist scope creep — extra ideas become new tasks.
- Prefer editing existing files over adding new ones unless the plan says otherwise.
- Leave the tree runnable after each commit.

## Output

Code change + commit on the feature branch; updated task status.
