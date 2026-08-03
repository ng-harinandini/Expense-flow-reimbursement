"""Extracts structured spending rules from **one section** of an ingested policy document.

Unlike ``POLICY_EXPLANATION``, whose output is prose a human reads, this prompt's output is
*proposed configuration* — so every clause below exists to stop a plausible-looking misreading from
reaching a reviewer as a confident-looking number.

**One section per call, not one document.** An enterprise policy runs to hundreds of pages; sending
it whole would exceed any context window, cost a fortune per run, and lose every correctly-read rule
the moment one call failed. Section-scoped calls are independent, individually retryable, and
individually attributable — which is also what lets a reviewer see that eleven sections extracted
cleanly and one did not.

The instructions are written against real failure modes observed in the shape of these documents:

* **Malformed tables.** A section's table can survive PDF extraction as inline pipe characters
  inside a paragraph rather than as rows. The model must still read it, because a table that merely
  *renders* badly is not a table that lacks data — and this is the main reason an LLM earns its keep
  here over a regex table parser.
* **Tables that straddle a section boundary.** A header row can end one section and its data rows
  begin the next. The caller supplies the tail of the previous section as read-only context so the
  second call can still interpret its half — with an explicit instruction not to re-report rules
  context already fully states, or the overlap would double-count them.
* **Sentinels that look like numbers.** An em-dash or "always manual" in an auto-approve column
  means *never auto-approve*. Emitting ``0`` there would be read downstream as a real threshold of
  zero, which is a different and much more permissive rule.
* **Limits that are not amounts.** "Current published mileage rate x miles", "Per signed
  relocation agreement", and a cabin class are all limits that no money column can hold. Forcing
  them into one loses the rule; the schema takes them as prose plus a basis instead.
* **Periods carry meaning.** "$40 per day" and "$40 per claim" are different rules with the same
  number. The basis is captured separately so the two never collapse.
* **Fraud prose is not a rule.** A section describing duplicate detection or spend-velocity
  screening documents an existing engine; it is not a spending limit and must not become one.

``{{known_categories}}`` is injected as the closed set the downstream table accepts. The model is
told to report anything outside it rather than inventing a category, because a category the rest of
the system has never heard of silently changes how claims are matched.
"""

from __future__ import annotations

from app.ai.prompts.builtin.definitions import BuiltinPrompt

POLICY_RULE_EXTRACTION_PROMPT = BuiltinPrompt(
    code="POLICY_RULE_EXTRACTION",
    name="Policy Rule Extraction",
    description=(
        "Extracts structured expense rules (limits, auto-approve thresholds, receipt thresholds, "
        "grade tiers) from one section of an ingested policy document, with a page citation and a "
        "confidence per rule. Output is a proposal for human review, never applied configuration."
    ),
    template_text=(
        "You are extracting spending rules from one section of an expense reimbursement policy so "
        "a finance reviewer can check them before they are applied. Your output is a proposal, not "
        "the live configuration.\n"
        "\n"
        "You are seeing SECTION {{section_index}} OF {{section_count}} of a larger document. "
        "Report only what this section states. Do not speculate about what other sections may "
        "contain, and "
        "do not report a rule you cannot see the text of.\n"
        "\n"
        "Return one rule object per distinct spending rule you find. A rule is a row of a limits "
        "table, or a paragraph that states a monetary cap, an approval requirement, or a receipt "
        "requirement. If this section states no spending rules at all -- it is an introduction, a "
        "list of responsibilities, a revision history -- return an empty `rules` array. That is a "
        "correct answer, not a failure.\n"
        "\n"
        "## Reading the section\n"
        "\n"
        "1. Some tables survive extraction badly and appear as inline pipe characters inside a "
        "paragraph instead of as rows. Read those as tables anyway. Do not skip a table because it "
        "is malformed.\n"
        "2. A table's header row and its data rows may fall on different pages within this "
        "section. "
        "Treat them as one table. Cite the page each individual rule's data appears on, not the "
        "header's page.\n"
        "3. Text under `PRECEDING CONTEXT` is the tail of the previous section, supplied only so a "
        "table or sentence continuing into this section can still be understood. Use it to "
        "interpret this section's content. Do NOT report a rule stated entirely within that "
        "context -- the previous section's own extraction already covers it.\n"
        "4. Read only what the document states. Never fill a field from your own knowledge of what "
        "expense policies usually say. If a value is absent, omit it.\n"
        "\n"
        "## Amounts and periods\n"
        "\n"
        "5. Put the number in `maxAmountUSD` and the period in `basis`. '$40 per day' is "
        "`maxAmountUSD: 40, basis: PER_DAY`; '$40 per claim' is the same number with "
        "`basis: PER_CLAIM`. These are different rules, so never drop the basis.\n"
        "6. If a limit is not a single number -- a formula such as a published rate multiplied by "
        "distance, a per-agreement amount, or a cabin class rather than a sum -- leave "
        "`maxAmountUSD` unset, put the document's own wording in `limitExpression`, and set "
        "`basis` to `FORMULA`, `AGREEMENT`, or `OTHER` as appropriate.\n"
        "7. A compound limit such as a per-person cap combined with a maximum number of events per "
        "year is one rule: use the per-unit amount with its basis, and record the full wording in "
        "`limitExpression` so nothing is lost.\n"
        "8. Never invent a currency conversion. Report amounts in the currency the document uses "
        "and name it in `currency`.\n"
        "\n"
        "## Auto-approve and receipt thresholds\n"
        "\n"
        "9. If the auto-approve column holds a dash, 'always manual', or any wording meaning the "
        "category is never approved automatically, omit `autoApproveLimitUSD` and set "
        "`alwaysManual: true`. Do NOT write 0 -- zero would be read as a real threshold and would "
        "permit different behaviour than 'never'.\n"
        "10. `receiptRequiredAboveUSD` is the amount above which a receipt is required. A stated "
        "threshold of zero means a receipt is always required; that is a genuine 0, so keep it.\n"
        "11. Set `requiresPreApproval: true` only when the document says approval must be obtained "
        "*before* the expense is incurred.\n"
        "\n"
        "## Categories and grades\n"
        "\n"
        "12. These are the only categories the target system accepts:\n"
        "{{known_categories}}\n"
        "    Map a rule onto one of them when it clearly corresponds. If a rule does not "
        "correspond to any of them, still return it, set `category` to the document's own "
        "heading, and set `representable: false` with a one-line `unrepresentableReason`. Never "
        "invent a category name that is not in the list and not in the document.\n"
        "13. Copy the grade or seniority tier verbatim into `gradeTier` ('All', 'L1-L3', 'L4+', "
        "'Manager+', and so on). If the rule applies to everyone, use 'All'.\n"
        "14. When this section states different limits per grade, emit one rule object per grade.\n"
        "\n"
        "## What is not a rule\n"
        "\n"
        "15. Do not emit rules for: fraud, anomaly, or duplicate-detection screening; general "
        "principles with no number; role and responsibility descriptions; review or revision "
        "schedules; or lists of non-reimbursable examples that state no limit.\n"
        "16. Do not emit a rule for the approval workflow itself. Per-category approval "
        "requirements belong on that category's rule via `alwaysManual` and "
        "`requiresPreApproval`.\n"
        "\n"
        "## Evidence and confidence\n"
        "\n"
        "17. Every rule needs `pageNumbers`: the page or pages its evidence appears on, using the "
        "`[Page N]` markers in the section text below.\n"
        "18. Every rule needs `sourceQuote`: a short verbatim quote from the document supporting "
        "the numbers. Quote, do not paraphrase -- the reviewer uses this to check you.\n"
        "19. Set `confidence` between 0 and 1. Use a low value when the table was malformed, the "
        "wording was ambiguous, or you had to infer which column a value belonged to. An honest "
        "low confidence is more useful than a confident guess.\n"
        "20. Put anything a reviewer must decide -- a conflict inside this section, a limit you "
        "could not classify, a value that seemed to be cut off -- in the top-level `reviewNotes`.\n"
        "21. Set `documentEffectiveDate` only if THIS section states the date the policy takes "
        "effect. If it shows a placeholder such as '[insert date]', leave it null.\n"
        "\n"
        "Document title: {{document_title}}\n"
        "Currency stated by the document: {{document_currency}}\n"
        "Section title: {{section_title}}\n"
        "Pages covered by this section: {{start_page}} to {{end_page}}\n"
        "\n"
        "--- PRECEDING CONTEXT (read-only, do not re-report rules stated entirely here) ---\n"
        "{{previous_context}}\n"
        "--- END PRECEDING CONTEXT ---\n"
        "\n"
        "--- BEGIN SECTION {{section_index}}: {{section_title}} ---\n"
        "{{section_text}}\n"
        "--- END SECTION ---\n"
    ),
    variables=[
        "known_categories",
        "document_title",
        "document_currency",
        "section_title",
        "section_index",
        "section_count",
        "start_page",
        "end_page",
        "previous_context",
        "section_text",
    ],
)

__all__ = ["POLICY_RULE_EXTRACTION_PROMPT"]
