"""Asks where a section too large to extract in one prompt divides into sub-topics.

The narrowest LLM call in the extraction pipeline, and the only one used for structure rather than
content. It is reached **only** when a heuristically-detected section exceeds the configured prompt
budget — a document whose sections all fit never pays for it.

**The model proposes; Python disposes.** The answer is a list of titles paired with the verbatim
first line of each sub-topic. The caller then locates those lines among the section's own chunks and
performs the split itself (see
:func:`app.ai.policy_extraction.sectioning.splitting.split_oversized_sections`). Nothing the model
writes becomes section *content* — a line it invented simply fails to match and the whole proposal
is discarded in favour of a mechanical token-window split. That is why this prompt can afford to be
short and can tolerate a mediocre answer: a wrong one costs a fallback, never a corrupted section.

The instruction to return an empty array matters as much as the rest. A long section that genuinely
has no internal structure is a real and common case (a single enormous limits table), and a model
pressed to find boundaries anyway would invent them mid-table.
"""

from __future__ import annotations

from app.ai.prompts.builtin.definitions import BuiltinPrompt

POLICY_SECTION_BOUNDARY_PROMPT = BuiltinPrompt(
    code="POLICY_SECTION_BOUNDARY",
    name="Policy Section Boundary Detection",
    description=(
        "Identifies where a large policy section divides into sub-topics, returning a title and "
        "the verbatim opening line of each. Used only to split a section too large to extract in "
        "one prompt; the caller performs the actual split and discards unmatched proposals."
    ),
    template_text=(
        "You are dividing one oversized section of an expense policy into its sub-topics so each "
        "can be processed separately. You are NOT extracting rules here -- only finding where "
        "sub-topics begin.\n"
        "\n"
        "Return one object per sub-topic AFTER the first. Do not include the section's own opening "
        "-- the text before your first boundary is implicitly the first part.\n"
        "\n"
        "For each sub-topic give:\n"
        "  `title`      a short name for the sub-topic, in the document's own words if possible\n"
        "  `firstLine`  the FIRST LINE of that sub-topic, copied VERBATIM from the text below\n"
        "\n"
        "Rules:\n"
        "1. `firstLine` must be copied exactly as it appears. It is used to locate the boundary in "
        "the original text; a line you paraphrase or reconstruct will not be found and your answer "
        "will be discarded.\n"
        "2. Do not include the `[Page N]` markers in `firstLine`. Copy the content line itself.\n"
        "3. Split on topic changes only -- a new expense category, a new class of rule. Never "
        "split in the middle of a table: a header row and its data rows must stay together.\n"
        "4. If this section has no meaningful internal structure -- it is one continuous table, or "
        "one topic throughout -- return an empty array. That is a correct answer.\n"
        "5. Return at most {{max_subsections}} sub-topics. If the section has more, choose the "
        "most substantial topic changes.\n"
        "\n"
        "Section title: {{section_title}}\n"
        "Pages: {{start_page}} to {{end_page}}\n"
        "\n"
        "--- BEGIN SECTION ---\n"
        "{{section_text}}\n"
        "--- END SECTION ---\n"
    ),
    variables=[
        "section_title",
        "start_page",
        "end_page",
        "max_subsections",
        "section_text",
    ],
)

#: Structured-output schema for the boundary call. Constrained the same way as the extraction
#: schema: no ``minimum``/``pattern`` (the API's structured-output mode rejects them) and
#: ``additionalProperties: false`` everywhere.
POLICY_SECTION_BOUNDARY_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["subsections"],
    "properties": {
        "subsections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "firstLine"],
                "properties": {
                    "title": {"type": "string"},
                    "firstLine": {"type": "string"},
                },
            },
        },
    },
}

__all__ = ["POLICY_SECTION_BOUNDARY_PROMPT", "POLICY_SECTION_BOUNDARY_SCHEMA"]
