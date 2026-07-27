# Hook 3 — Branch

**Purpose:** Isolate the approved task's work on its own feature branch.

## When to Run

After plan approval (Hook 2), before Build (Hook 4).

## Steps

1. If the repo is not a git repo yet, initialize it:
   `git init` → create `.gitignore` → initial commit of the framework.
2. Ensure the working tree is clean (stash or commit stray changes first).
3. Create and switch to a feature branch named `task/<ID>-<slug>`:
   `git checkout -b task/<ID>-<slug>`.
4. Record the branch name in `HANDOFF.md` and `LOG.md`.

## Inputs

- Approved `PLANS/plan-<ID>.md`, active task ID + slug.

## Outputs

- New feature branch checked out.
- Branch name recorded in `HANDOFF.md` and `LOG.md`.

## Done Check

- `git rev-parse --abbrev-ref HEAD` returns `task/<ID>-<slug>`.
- `HANDOFF.md` shows the active branch.

## Notes

- Branch only for **approved** tasks.
- Never work on the default branch directly.
