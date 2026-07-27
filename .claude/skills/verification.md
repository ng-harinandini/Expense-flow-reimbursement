# Skill — Verification

**Goal:** Confirm the whole task meets its criteria with no new regressions.

## Procedure

1. Re-run all task + project Done Checks exactly as the Baseline ran them.
2. Diff results against the Baseline in `LOG.md`.
   - Same-or-better = pass.
   - Any **new** failure = regression → route to **Bug Fixing** (Hook 5 loop).
3. Verify the actual behavior/artifact, not just that commands exit 0
   (e.g. run the feature, inspect the output).
4. Record a dated verification entry in `LOG.md` (green or the failures found).

## Distinction

- **Done Check** = the criteria + running them.
- **Verification** = the gate that compares to baseline and decides pass/fail for the
  task as a whole.

## Output

A verification verdict in `LOG.md`; on failure, a bug task loop until green.
