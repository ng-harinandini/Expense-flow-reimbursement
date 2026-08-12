"""Prompt template for the single-call receipt extractor.

Placeholders are ``<<SENTINEL>>`` markers rather than ``str.format`` fields, matching
``app/ai/classification/prompts.py`` and ``app/ai/extraction/prompts/*.py`` — the prompt is mostly
JSON, and doubling every brace to survive ``format`` is a defect waiting to happen.

Deliberately *not* registered in ``app/ai/extraction/prompts/__init__.py``: that ``_VERSIONS`` dict
and the ``AI_RULE_EXTRACTION_PROMPT_VERSION`` selector belong to policy-rule extraction, which is a
different capability with a different output contract. One version exists here until there is an
actual need to A/B or roll back wording.

The category list and the per-category field schema are **not** written into this file. They are
rendered from the database on every request by ``BedrockReceiptExtractor._build_prompt`` — a
category an admin adds or renames must reach the model without a code deploy.
"""

from __future__ import annotations

RECEIPT_EXTRACTION_PROMPT_TEMPLATE = """\
You are reading an employee's uploaded expense receipt/invoice. In ONE pass you must transcribe the
document's financial details AND decide which expense category it belongs to, so the employee's
submission form can be pre-filled for them to review.

=== NON-NEGOTIABLE RULES ===

1. Transcribe only what the document actually shows. Never invent a vendor, amount, date or tax
   figure. Any detail the document does not state must be null.
2. "suggestedCategory" MUST be exactly one of the categories listed below, copied character for
   character, or the JSON literal null. NEVER invent a category. If none fit, return null.
3. "confidence" is a number from 0.0 to 1.0 for how sure you are about "suggestedCategory".
   If the document is blurry, cropped or ambiguous, say so with a LOW confidence rather than
   guessing confidently.
4. "documentType" is a short free-text label for what kind of document this is (e.g. "restaurant
   receipt", "hotel invoice", "flight e-ticket", "taxi receipt") — not restricted to the category
   list.
5. "totalAmount" is the final amount payable, as a plain number: no currency symbol, no thousands
   separators. "currency" is the 3-letter ISO code (USD, INR, EUR, ...).
6. "transactionDate" is the date the expense was incurred, in YYYY-MM-DD format, or null.
7. "categoryFields" must contain ONLY field names from the COMMON list plus the field list of the
   ONE category you chose in "suggestedCategory". Never emit a field belonging to a category you
   did not choose, and never invent a field name. Leave a field out entirely rather than guessing.
8. For a field with a fixed list of "options", the value MUST be one of those options, verbatim.
   Dates must be YYYY-MM-DD. Boolean fields must be true or false, never a string.

=== COMMON FIELDS (extract these for every document, whatever the category) ===
<<COMMON_FIELDS>>

=== CATEGORIES AND THEIR OWN FIELDS ===
<<CATEGORY_SCHEMA>>

=== OUTPUT FORMAT ===
Respond with exactly one JSON object and nothing else — no markdown fence, no commentary:

{"documentType": <string or null>, "suggestedCategory": <one of the categories above, or null>, \
"confidence": <number 0.0-1.0>, "vendorName": <string or null>, \
"transactionDate": <"YYYY-MM-DD" or null>, "totalAmount": <number or null>, \
"subtotalAmount": <number or null>, "taxAmount": <number or null>, \
"currency": <"USD"|"INR"|... or null>, \
"lineItems": [{"description": <string>, "quantity": <number or null>, \
"unitPrice": <number or null>, "amount": <number or null>}], \
"categoryFields": {<field name>: <value>, ...}}
"""

__all__ = ["RECEIPT_EXTRACTION_PROMPT_TEMPLATE"]
