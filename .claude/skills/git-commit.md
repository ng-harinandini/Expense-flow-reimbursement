# Skill — Git Commit

**Goal:** Produce clean commits whose messages describe *only the changes* — never
mentioning Claude, AI, or any assistant attribution.

## Rules

1. **No AI attribution.** The commit message MUST NOT contain "Claude", "Anthropic",
   "AI", "Co-Authored-By: Claude", "Generated with", or any similar trailer/mention.
2. **Summary only.** The message describes *what changed and why* — nothing else.
3. **Never commit unless asked.** Only commit when the user explicitly requests it.
4. Do not skip hooks or bypass signing unless the user explicitly asks.

## Message Format

```
<type>: <concise summary of the change>

<optional body: what changed and why, wrapped ~72 cols>
```

- `type` ∈ `feat | fix | refactor | docs | test | chore | build`.
- Subject line ≤ 72 chars, imperative mood ("add", not "added").
- Body only when the change needs context; otherwise subject alone.

## Procedure

1. Review staged/unstaged changes (`git status`, `git diff`) to understand the change.
2. Draft a summary describing the changes only.
3. **Self-check** the message against the Rules below before committing.
4. Commit (on a feature branch, per project convention).

## Self-Test (run before every commit)

- Does the message mention Claude / AI / an assistant? → If yes, **rewrite**.
- Does it contain a `Co-Authored-By` / "Generated with" trailer? → If yes, **remove**.
- Does the subject clearly state the change? → If no, rewrite.

## Output

A commit whose message is a pure changes summary, free of any AI attribution.
