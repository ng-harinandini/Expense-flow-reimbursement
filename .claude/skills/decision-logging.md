# Skill — Decision Logging

**Goal:** Capture non-obvious technical/architectural decisions as durable ADRs.

## Procedure

1. For each notable decision, create `DECISIONS/ADR-<NNN>-<slug>.md`.
2. Use the ADR template below.
3. Reference the ADR from the relevant task file and `HANDOFF.md`.

## When to Write an ADR

- A choice between real alternatives (framework, storage, structure, pattern).
- Anything a future reader would ask "why did they do it this way?".
- Reversible-but-costly decisions and their trade-offs.

## ADR Template

```markdown
# ADR-<NNN> — <title>
- **Date:** YYYY-MM-DD
- **Status:** proposed | accepted | superseded by ADR-<NNN>
- **Context:** the problem/forces at play.
- **Decision:** what was chosen.
- **Alternatives:** what was considered and rejected, and why.
- **Consequences:** trade-offs, follow-ups, risks accepted.
```

## Output

`DECISIONS/ADR-<NNN>-<slug>.md`, linked from the task and handoff.
