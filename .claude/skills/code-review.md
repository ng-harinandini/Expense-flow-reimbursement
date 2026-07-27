# Skill — Code Review

**Goal:** Assess the task's diff for correctness, clarity, and scope before handoff.

## Procedure

1. Review the full diff on the feature branch (`git diff <base>...HEAD`).
2. Check for:
   - **Correctness:** logic bugs, edge cases, error handling.
   - **Scope:** changes match the plan; no unrelated edits.
   - **Reuse/simplicity:** no duplication, no dead code, minimal surface.
   - **Style:** matches surrounding code and project conventions.
   - **Tests:** adequate coverage of new behavior.
   - **Security:** input validation, secrets, injection risks.
3. Record findings; fix blocking issues (open bug tasks if needed) before handoff.

## Output

A review summary in the task file / `HANDOFF.md`; blocking issues resolved or tracked.
