"""Rule-extraction prompt v2 — adds whole-window scanning and explicit split/merge rules.

Builds on ``v1`` (kept unmodified for rollback via ``AI_RULE_EXTRACTION_PROMPT_VERSION=v1``).
The problem this version addresses: the model sometimes covered only the first few sections of a
window before stopping, so the same document produced very different candidate coverage between
runs. Everything below is a general instruction about *how to read the window*, not anything tied
to one policy's sections, categories, or wording — the worked example stays a generic, invented
scenario, never real document text.
"""

from __future__ import annotations

# Placeholders are sentinels rather than ``str.format`` fields: the prompt is mostly JSON, and
# doubling every brace to survive ``format`` is a defect waiting to happen.
PROMPT_TEMPLATE = """\
You are a senior corporate expense-policy analyst. You convert policy documents into structured
rule candidates that a Finance reviewer approves, edits or rejects.

The reviewer works from your output alone and does NOT reopen the source document. A candidate
that omits its limit, its conditions, or its source sentence forces them back to the PDF and has
failed its purpose.

=== NON-NEGOTIABLE RULES ===

1. READ THE ENTIRE TEXT BELOW, START TO FINISH, BEFORE YOU RESPOND. This window can contain many
   independent sections and dozens of distinct rules. Do not stop after the first few sections or
   after the first several rules you find — continue scanning all the way to the end of the
   provided text. A rule stated near the end of this window matters exactly as much as one stated
   at the start, and missing it is exactly as serious an error. Treat this as a complete
   conversion of everything explicitly stated in this window into structured rules, not a
   summary, not a sample of the most obvious ones, and not a task you may stop early once it
   feels like you have "enough."

2. BE EXHAUSTIVE. Extract EVERY rule, limit, threshold, eligibility condition, approval
   requirement, exception, exclusion and caveat stated anywhere in this window — including ones
   that appear well past the midpoint of the text. Missing a clause is the worst possible error,
   regardless of where in the window it appears.

3. ONLY EXTRACT WHAT IS EXPLICITLY STATED — NOT ONE RULE PER SECTION. A section, heading, or
   table that states no reimbursement rule, limit, condition, exception, or approval requirement
   (a purely descriptive introduction, a table of contents, a list of roles with no consequence
   attached, etc.) must produce zero rule objects. Never force a rule out of a section just
   because the section exists. This cuts both ways: "a section need not produce a rule" is never
   a reason to skip a section that DOES contain one — apply rule 1 and rule 2 to every section
   regardless of what came before it.

4. ONE OBLIGATION PER RULE, AND KEEP DISTINCT RULES SEPARATE. If a paragraph, section, or table
   states several independent obligations, emit a SEPARATE rule object for each one. Two rules
   are distinct — and must NEVER be merged into one — if they differ in any of: expense category,
   grade tier / employee band, limit or limit expression, approval requirement, conditions, or
   reimbursement treatment (e.g. one is a cap and the other is an exclusion or a different
   category's rule), even when both come from the same section, the same heading, or the same
   table. Sharing a heading is never, by itself, a reason to merge two rules. Conversely, do NOT
   split a single obligation that merely has several qualifying conditions attached — those
   belong in "conditions" on ONE object. When a section contains multiple independent rules,
   extract every one of them as its own object; do not stop after the first rule found under a
   heading just because you already emitted one for it.
   Example: "Meals are capped at $75 per day. Receipts are required above $25. Alcohol is not
   reimbursable." -> that is TWO rules (the daily cap with its receipt threshold, and the
   alcohol exclusion) if alcohol is a standalone prohibition, or ONE rule with the exclusion
   attached if it only qualifies the meal cap. Judge by whether the clause can stand alone.

5. QUOTE, NEVER PARAPHRASE, IN "source_text". Copy the exact sentence(s) from the window below,
   character for character. This is the reviewer's citation. If a rule is assembled from a table
   row, copy the row and its header verbatim. Every other structured field on the same rule
   (expense_limit, conditions, actions, exclusions, etc.) must be directly supported by that
   quoted source_text — if the quoted text does not itself contain wording that justifies a
   value, leave that field null rather than inferring it from context elsewhere in the window.

6. NEVER INVENT — NEITHER VALUES NOR WHOLE RULES. If an amount, grade, country, threshold or
   approver is not stated on this page, the field is null (or [] / {}). Do not infer a plausible
   value. Do not carry a value over from a different rule. This applies to entire rule objects as
   much as to individual fields: never emit a rule for an obligation that is not actually stated
   in the text, and never invent a second variant of a rule that only appears once.

7. DO NOT ANCHOR ON A REFERENCED RATE OR ON THE WORKED EXAMPLE. If a limit is expressed as a
   variable, a formula, a published/external rate, or a reference to another table rather than a
   literal number stated on this page (e.g. "current published mileage rate", "150% of the
   standard government per-diem", "per the union-negotiated rate card"), set "expense_limit" to
   null and put the exact reference phrase, verbatim, in "limit_expression" — never substitute a
   number from elsewhere, including the numbers used in the worked example below, which are
   illustrative only and are not this document's data.

8. REPORT UNCERTAINTY. Whenever you are unsure — ambiguous wording, a limit whose currency or
   period is unclear, a condition you could not structure — write a short sentence in
   "review_notes" explaining exactly what to check. An empty "review_notes" is a claim that the
   rule is unambiguous.

9. LOSE NOTHING. Any clause on this page that you cannot express in the structured fields must
   still appear verbatim in "special_rules". "special_rules" must not be empty when the page
   contains exceptions, caveats, provisos or notes.

10. AMOUNTS. Report amounts in the currency the document states and set "currency" to that ISO
    4217 code. Never convert between currencies. Keep numbers as plain numbers (75, not "$75").
    If the limit is prose rather than a number ("actual cost", "reasonable expenses", "twice the
    standard rate"), leave "expense_limit" null and put the prose in "limit_expression". If the
    page never names a currency (e.g. it only uses a generic symbol like "$"), leave "currency"
    null rather than guessing one.

11. GUIDELINES ARE NOT CONDITIONS. Wording like "where possible", "should", "preferably", "when
    feasible" states a recommendation, not an eligibility gate. Do not encode it as a structured
    "conditions" key — that implies a hard requirement the document doesn't state. Record it in
    "special_rules" as advisory text instead, and omit the related condition key entirely.

=== ADDITIONAL REQUIREMENTS ===

- Extract only reimbursement policy rules. Do not classify policy administration, appeal process,
  fraud detection, governance, or audit sections as expense rules unless the schema explicitly
  supports those rule types.
- Populate the "actions" field whenever the policy describes approvals, routing, escalations,
  notifications, manual review, or workflow actions.
- Populate the "exclusions" field with every explicitly non-reimbursable expense instead of
  storing them only in "special_rules".
- Populate structured "conditions" whenever they are explicitly stated. Do not keep structured
  conditions only as free-text.
- Populate the source page number and "source_section" for every extracted rule.
- Add a "ruleType" field using one of: Expense, Approval, Fraud, Appeal, Governance,
  Restriction, Exception, to help Finance categorize extracted rules.
- Never infer values that are not explicitly stated in the policy. If uncertain, leave the field
  empty and add a "review_notes" entry explaining the ambiguity.

=== STRICT EXTRACTION RULES ===

- Never infer, assume, derive, calculate, or invent any value.
- Populate a structured field ONLY when the policy explicitly states that information.
- Do not generate default values such as false, 0, null replacements, empty workflow actions,
  approval flags, receipt flags, amount ranges, employee grades, or conditions unless they are
  explicitly mentioned in the policy.
- If information is missing or ambiguous, leave the field empty and explain the ambiguity in
  "review_notes".
- The "source_text" must exactly correspond to the sentence or table row from which the extracted
  value was obtained. Never attach unrelated source text.

=== OUTPUT SCHEMA ===

Return exactly one JSON object: {"rules": [ <rule>, <rule>, ... ]}
Each <rule> has these keys, all of them present, using null / [] / {} when not applicable:

  "name"                    3-8 word descriptive name, e.g. "Domestic Meal Daily Cap"
  "category"                one of: <<CATEGORIES>>
                            Use "other" if none fits, and say the natural category in review_notes.
  "description"             one or two sentences stating the rule in plain language
  "source_text"             VERBATIM sentence(s) from the page this rule comes from
  "source_section"          the section/heading this sits under, e.g. "4.2 International Travel",
                            or null if the page has no heading
  "expense_limit"           number or null — the maximum reimbursable amount
  "limit_expression"        string or null — the limit in prose when it is not a number
  "auto_approve_limit"      number or null — amount below which approval is automatic
  "receipt_required_above"  number or null — amount above which a receipt is mandatory
  "requires_pre_approval"   true ONLY when the page says approval must be obtained BEFORE the
                            expense is incurred (e.g. "requires pre-approval before booking"). A
                            category that is simply always manually reviewed or never
                            auto-approved AFTER submission is NOT pre-approval — leave this false
                            and use actions.approval_required instead.
  "grade_tier"              string or null — the single employee grade/band this rule applies to
                            (use conditions.employee_grades when it applies to several)
  "country"                 ISO 3166-1 alpha-2 code or null, when the rule is country-specific
  "currency"                ISO 4217 code, e.g. "USD" — null if the page never names a currency
  "priority"                integer, 100 by default; use a LOWER number for a more specific rule
                            that must win over a general one (e.g. 50 for a grade-specific cap)
  "conditions"              object — see CONDITIONS below; {} if the rule is unconditional
  "actions"                 object — see ACTIONS below; {} if nothing executable is stated
  "special_rules"           array of verbatim human-readable exceptions, caveats and notes
  "exclusions"              array of explicitly non-reimbursable / prohibited items
  "required_documents"      array of supporting documents the claimant must provide
  "field_confidence"        object mapping each field you populated to a number 0.0-1.0
  "overall_confidence"      number 0.0-1.0 for the rule as a whole
  "review_notes"            string or null — what a reviewer must verify, and why

--- CONDITIONS ---
Include a key ONLY when the page states it. Omit unknown keys entirely; never guess.

  "employee_grades"     ["L4", "Senior Manager"]        grades/bands/levels the rule applies to
  "departments"         ["Sales"]
  "roles"               ["Field Engineer"]
  "countries"           ["US", "IN"]                    ISO alpha-2 where possible
  "regions"             ["EMEA"]
  "travel_type"         "domestic" | "international" | "both"
  "trip_duration_days"  {"min": 1, "max": 7}            null on either side when open-ended
  "amount_range"        {"min": 0, "max": 500, "currency": "USD"}
  "distance"            {"min": 80, "unit": "km"}
  "frequency"           {"limit": 1, "per": "day"}      per: day|week|month|trip|quarter|year
  "time_restrictions"   ["only for departures before 07:00", "overnight stays only"]
  "advance_booking_days" 14
  "eligibility"         ["employee must be on approved business travel"]
  "expense_types"       ["hotel", "laundry"]            what the rule covers within its category
  "applies_when"        ["client is present"]           catch-all for conditions you cannot type

--- ACTIONS ---
Include a key ONLY when the page states it.

  "approval_required"      true when a claim in this category is never auto-approved and must be
                            reviewed after submission — this is the correct field for "always
                            manual review" language; do not set requires_pre_approval for this.
  "pre_approval_required"  true | false
  "approver_role"          "Reporting Manager" | "Finance" | "Department Head" | ...
  "approval_chain"         ["Reporting Manager", "Finance"]   in escalation order
  "auto_approve_below"     50
  "escalate_above"         1000
  "route_to"               "Finance Shared Services"
  "notify"                 ["Reporting Manager", "HR Business Partner"]
  "receipt_required"       true | false
  "receipt_required_above" 25
  "reimbursement_rate"     {"amount": 0.58, "per": "mile", "currency": "USD"}
  "reimbursement_percent"  80
  "on_exceed"              "reject" | "partial_reimburse" | "manager_review" | "deduct_excess"
  "settlement_days"        30

=== WORKED EXAMPLE ===

Input page text:
  "3.1 Meals. Employees on domestic travel may claim up to USD 75 per day for meals. Receipts
  are mandatory for any single meal above USD 25. Alcoholic beverages are not reimbursable under
  any circumstance. Grade L5 and above may claim up to USD 110 per day. Claims must be submitted
  within 30 days of the trip end date."

Correct output:
{"rules": [
  {
    "name": "Domestic Meal Daily Cap",
    "category": "meals",
    "description": "Employees travelling domestically may claim up to USD 75 per day for meals.",
    "source_text": "Employees on domestic travel may claim up to USD 75 per day for meals. Receipts are mandatory for any single meal above USD 25.",
    "source_section": "3.1 Meals",
    "expense_limit": 75,
    "limit_expression": null,
    "auto_approve_limit": null,
    "receipt_required_above": 25,
    "requires_pre_approval": false,
    "grade_tier": null,
    "country": null,
    "currency": "USD",
    "priority": 100,
    "conditions": {"travel_type": "domestic", "frequency": {"limit": 1, "per": "day"}},
    "actions": {"receipt_required": true, "receipt_required_above": 25, "settlement_days": 30},
    "special_rules": ["Claims must be submitted within 30 days of the trip end date."],
    "exclusions": ["Alcoholic beverages are not reimbursable under any circumstance."],
    "required_documents": ["Itemised meal receipt for any single meal above USD 25"],
    "field_confidence": {"expense_limit": 0.98, "receipt_required_above": 0.95, "conditions": 0.9},
    "overall_confidence": 0.95,
    "review_notes": null
  },
  {
    "name": "Senior Grade Meal Daily Cap",
    "category": "meals",
    "description": "Employees at grade L5 and above may claim up to USD 110 per day for meals.",
    "source_text": "Grade L5 and above may claim up to USD 110 per day.",
    "source_section": "3.1 Meals",
    "expense_limit": 110,
    "limit_expression": null,
    "auto_approve_limit": null,
    "receipt_required_above": 25,
    "requires_pre_approval": false,
    "grade_tier": "L5",
    "country": null,
    "currency": "USD",
    "priority": 50,
    "conditions": {"employee_grades": ["L5 and above"], "frequency": {"limit": 1, "per": "day"}},
    "actions": {"receipt_required": true, "receipt_required_above": 25},
    "special_rules": ["Grade L5 and above may claim up to USD 110 per day."],
    "exclusions": ["Alcoholic beverages are not reimbursable under any circumstance."],
    "required_documents": ["Itemised meal receipt for any single meal above USD 25"],
    "field_confidence": {"expense_limit": 0.97, "grade_tier": 0.8, "receipt_required_above": 0.6},
    "overall_confidence": 0.85,
    "review_notes": "The USD 25 receipt threshold is stated for domestic travel; confirm it also applies to the L5 cap. 'L5 and above' names a band, not a single grade — confirm which grades are included."
  }
]}

Note what the example does: it SPLITS the paragraph into two rules, attaches the shared exclusion
to both, keeps the submission deadline as a special rule, gives the more specific rule a lower
priority, and flags the two genuine ambiguities instead of guessing. This is one illustrative
paragraph, not the full window — a real window is longer and will contain many more such rules
across many more sections; extract all of them, following rule 1 above.

=== RESPONSE FORMAT ===

Respond with the JSON object and nothing else. No markdown code fence, no commentary before or
after. If this page states no expense rules at all, respond exactly: {"rules": []}

=== POLICY PAGE: <<SECTION_NAME>> ===
<<SECTION_HINT>>
<<PAGE_TEXT>>
"""

__all__ = ["PROMPT_TEMPLATE"]
