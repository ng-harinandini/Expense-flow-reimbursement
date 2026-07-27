# Skill — Documentation

**Goal:** Keep human-facing docs accurate and in sync with shipped changes.

## Procedure

1. Identify docs affected by the change: `README`, usage guides, API docs, inline
   comments, `CLAUDE.md` (if the workflow itself changed).
2. Update them to reflect current reality — no aspirational or stale statements.
3. Document new setup/run/test steps so a fresh clone can be brought up from docs alone.
4. Cross-link related tasks, plans, and ADRs.

## Principles

- Docs describe what *is*, not what *was planned*.
- Prefer updating existing docs over creating new fragmented ones.
- Every new user-facing capability gets a usage note.

## Output

Updated documentation committed alongside the change.
