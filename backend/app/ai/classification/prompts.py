"""Prompt templates for document classification and category-field extraction.

Placeholders are ``<<SENTINEL>>`` markers rather than ``str.format`` fields, matching
``app/ai/extraction/prompts/*.py`` — both prompts are mostly JSON, and doubling every brace to
survive ``format`` is a defect waiting to happen. Only one version of each prompt exists: unlike
rule extraction, there has been no need yet to A/B or roll back prompt wording, so no versioning
selector is built until that need actually shows up.
"""

from __future__ import annotations

CLASSIFICATION_PROMPT_TEMPLATE = """\
You are classifying an employee's uploaded expense receipt/document so it can be checked against
the expense category the employee themselves selected.

=== NON-NEGOTIABLE RULES ===

1. "suggestedCategory" MUST be exactly one of the categories listed below, copied character for
   character, or the JSON literal null. NEVER invent a category, and never return a category that
   is not in the list even if you believe it fits better than any listed option — in that case
   explain your reasoning in "reasoning" and still pick the closest listed category, or null if
   none fit at all.
2. Base your answer on the evidence given below (the OCR text and/or the document image). If the
   evidence is thin or ambiguous, say so honestly with a LOW "confidence" rather than guessing
   confidently.
3. "confidence" is a number from 0.0 to 1.0 for how sure you are that "suggestedCategory" is the
   correct category for this document.
4. "documentType" is a short free-text label for what kind of document this is (e.g. "restaurant
   receipt", "hotel invoice", "flight e-ticket", "taxi receipt", "handwritten note") — not
   restricted to the category list.

=== VALID CATEGORIES ===
<<CATEGORIES>>

=== EVIDENCE ===
<<OCR_TEXT>>

=== OUTPUT FORMAT ===
Respond with exactly one JSON object and nothing else — no markdown fence, no commentary:

{"documentType": <string or null>, "suggestedCategory": <one of the valid categories, or null>, \
"confidence": <number 0.0-1.0>, "reasoning": <short string or null>}
"""

FIELD_EXTRACTION_PROMPT_TEMPLATE = """\
You are extracting a fixed set of structured fields from an employee's expense receipt/document.
The document has already been confirmed to belong to a known expense category; your only job is to
fill in the fields below from the evidence given, or leave a field null when the evidence does not
state it.

=== NON-NEGOTIABLE RULES ===

1. Only ever use the field names listed below. Never invent a new field name.
2. Never guess a value that is not supported by the evidence. Leave a field null instead.
3. For a field with a fixed list of "options", the value MUST be one of those options, verbatim,
   or null.
4. Dates must be in YYYY-MM-DD format when you can determine one, else null.
5. Boolean fields must be true, false, or null — never a string.

=== FIELDS TO EXTRACT ===
<<FIELD_SCHEMA>>

=== EVIDENCE ===
<<OCR_TEXT>>

=== OUTPUT FORMAT ===
Respond with exactly one JSON object and nothing else — no markdown fence, no commentary:

{"fields": {<field name>: <value>, ...}}
"""

__all__ = ["CLASSIFICATION_PROMPT_TEMPLATE", "FIELD_EXTRACTION_PROMPT_TEMPLATE"]
