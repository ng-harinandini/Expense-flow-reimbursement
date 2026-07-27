# Skill — Testing

**Goal:** Prove an Atomic Task behaves as intended with automated checks where possible.

## Procedure

1. Identify the check type for the change: unit, integration, type-check, lint, build,
   or manual observation.
2. Write or extend tests to cover the new behavior and at least one edge/failure case.
3. Run the relevant test command; capture the exact command and result.
4. Ensure the test is deterministic and runnable by anyone from a clean checkout.

## Guidance

- Tests are part of the same Atomic Task, not a separate afterthought.
- If a change is not automatically testable, document the manual steps in the task's
  Done Check and in `LOG.md`.
- Keep the test suite green; a red suite blocks Build progression.

## Output

Passing tests + the recorded command/result feeding into the Done Check.
