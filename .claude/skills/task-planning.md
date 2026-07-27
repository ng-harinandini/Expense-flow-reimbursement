# Skill — Task Planning

**Goal:** Turn an approved task breakdown into an executable, review-ready plan.

## Procedure

1. Read the task detail file and the Baseline in `LOG.md`.
2. Write `PLANS/plan-<ID>.md` with:
   - **Objective & Success Criteria** (mirror the task Done Check).
   - **Execution Order** — Atomic Tasks sorted by dependency.
   - **Change Map** — files added/modified per Atomic Task.
   - **Verification Strategy** — how each Done Check will be run.
   - **Risks & Unknowns** and **Rollback** plan.
   - **Status:** `awaiting-approval`.
3. Stop and request human approval; on approval flip status to `approved`.

## Plan Template

```markdown
# Plan <ID> — <title>
Status: awaiting-approval

## Objective & Success Criteria
## Execution Order (by dependency)
## Change Map (files per Atomic Task)
## Verification Strategy
## Risks / Unknowns
## Rollback
```

## Output

`PLANS/plan-<ID>.md`, approval recorded in `LOG.md`.
