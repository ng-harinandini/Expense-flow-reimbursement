# CLAUDE.md — Project Operating Manual

This repository uses a **modular, file-backed, self-documenting workflow**. Repository
files are the *only* source of memory. If a fact is not written to `HANDOFF.md`,
`LOG.md`, `TASKS/`, `PLANS/`, or `DECISIONS/`, it does not exist.

## Project

- **Name:** expense
- **Purpose:** _TBD — set when the first Primary Task is created._
- **Git:** Not yet initialized. Hook 3 (Branch) will run `git init` on first use.

## Golden Rules

1. **Files are memory.** Never rely on conversation state. Persist every decision,
   status change, and result to the appropriate file.
2. **Everything is reusable.** Hooks live in `.claude/hooks/`, skills in
   `.claude/skills/`. One concept per file. No task-specific logic hard-coded into a
   hook or skill.
3. **Task format is mandatory.** Every Primary, Macro, and Atomic task carries all
   nine fields (see below).
4. **Follow the execution order** on every session.

## Execution Order (run every session)

```
Read CLAUDE.md
  → Read HANDOFF.md
  → Read TASKS/task.md
  → Create or Resume Task
  → Break into Macro Tasks
  → Break into Atomic Tasks
  → Hook 1: Baseline
  → Hook 2: Planning
  → Approval (human gate)
  → Hook 3: Branch
  → Hook 4: Build
  → Hook 5: Verify
  → Hook 6: Handoff
```

## Task Format (all task levels)

Every task — Primary, Macro, Atomic — MUST include:

| Field         | Meaning                                                        |
|---------------|----------------------------------------------------------------|
| Goal          | What "done" achieves, in one sentence.                         |
| Constraints   | Hard limits (tech, style, scope, perf).                        |
| Inputs        | Files, data, prior tasks required to start.                    |
| Outputs       | Concrete artifacts produced.                                   |
| Done Check    | Objective, runnable/observable pass criteria.                  |
| Out of Scope  | Explicitly excluded work.                                      |
| Dependencies  | Task IDs that must complete first.                             |
| Status        | `todo` \| `in-progress` \| `blocked` \| `done` \| `cancelled`  |

## Directory Map

| Path               | Role                                                        |
|--------------------|-------------------------------------------------------------|
| `CLAUDE.md`        | This manual. Read first.                                    |
| `HANDOFF.md`       | Current state + next action. Read second.                   |
| `LOG.md`           | Append-only chronological event log (baselines, verifies).  |
| `TASKS/task.md`    | Source of truth for task status & dependencies.             |
| `TASKS/task-*.md`  | One file per Primary Task with full breakdown.              |
| `PLANS/plan-*.md`  | Implementation plan per task, human-approved.               |
| `DECISIONS/*.md`   | Architecture Decision Records (ADRs).                       |
| `.claude/hooks/`   | Reusable workflow hooks (1–6).                              |
| `.claude/skills/`  | Reusable skills (Task Breakdown, Planning, …).              |

## Hooks (`.claude/hooks/`)

1. **Baseline** — run Done Checks, record baseline in `LOG.md`, flag existing failures.
2. **Planning** — write `PLANS/plan-[id].md`, wait for human approval.
3. **Branch** — create/switch to a feature branch for the approved task.
4. **Build** — execute Atomic Tasks by dependency order; update status continuously.
5. **Verify** — run Done Checks; on failure open a bug task, replan, fix, repeat.
6. **Handoff** — update `DECISIONS/`, `LOG.md`, `HANDOFF.md`; mark ready for review.

## Skills (`.claude/skills/`)

Task Breakdown · Task Planning · Implementation · Testing · Done Check · Bug Fixing ·
Verification · Code Review · Documentation · Decision Logging · Handoff · Git Commit.

**Git Commit rule (always):** commit messages contain the changes summary *only* —
never mention Claude/AI or add a `Co-Authored-By`/"Generated with" trailer. See
`.claude/skills/git-commit.md`.

## ID & Naming Conventions

- Primary Task ID: `T<NNN>` (e.g. `T001`). File: `TASKS/task-T001-[slug].md`.
- Macro Task ID: `T<NNN>-M<N>`. Atomic Task ID: `T<NNN>-M<N>-A<N>`.
- Bug Task ID: `BUG-<NNN>`. Plan: `PLANS/plan-T001.md`. ADR: `DECISIONS/ADR-<NNN>-[slug].md`.
