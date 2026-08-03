"""Structured rule extraction from ingested policy documents.

The AI half of the propose-then-approve flow: read an already-ingested document, ask a model for the
rules it states, and write them as a **proposal** for a human to review.

Nothing here writes ``policy_rules``. That boundary is not stylistic — ADR-003 §5 settles on option
C2, *"deterministic decides, LLM explains"*, and ``tests/test_ai_architecture.py`` enforces it by
banning ``app/ai/**`` from importing the claim decision and write paths. Approval is therefore
performed by ``app/api/policy_rule_proposals.py``, which lives outside this package and calls
``PolicyRuleService`` itself.

The practical property that makes an LLM safe here: a proposal that is never approved changes
nothing about how any claim is judged.

## The pipeline

Extraction runs **one model call per logical section**, not one per document. A 500-page policy has
no single prompt that could hold it, and a single call has no failure granularity — one timeout and
every correctly-read rule is lost with it.

    sectioning/   chunks -> sections      heuristics first, a model only for oversized sections
    extractor     section -> raw rules    the only module that knows which model is in use
    executor      sections -> outcomes    isolates failures; one bad section costs one section
    normalization raw -> NormalizedRule   deterministic cleanup, unchanged by the redesign
    merge         sections -> one set     duplicates collapse; disagreements become conflicts
    validation    rules -> warnings       deterministic findings, never fatal
    orchestrator  the order of the above, and the only module that knows it

Two properties hold throughout, and both are load-bearing:

* **No vector search, ever.** Extraction reads the document it was handed. Embeddings exist for
  search, chat and retrieval; using them here would mean extracting rules from whatever text a
  similarity query happened to surface, which is not the same document.
* **Everything after the model call is deterministic.** Normalization, merging, conflict detection
  and validation involve no inference, so the same section outputs always produce the same proposal.
"""

from app.ai.policy_extraction.merge import (
    MergeResult,
    RuleConflict,
    merge_section_rules,
)
from app.ai.policy_extraction.normalization import (
    NormalizedRule,
    build_ruleset_payload,
    normalize_extraction,
    normalized_rule_from_item,
)
from app.ai.policy_extraction.rule_ids import generate_rule_id
from app.ai.policy_extraction.schema import POLICY_RULE_EXTRACTION_SCHEMA
from app.ai.policy_extraction.validation import ValidationWarning, validate_rules

__all__ = [
    "POLICY_RULE_EXTRACTION_SCHEMA",
    "MergeResult",
    "NormalizedRule",
    "RuleConflict",
    "ValidationWarning",
    "build_ruleset_payload",
    "generate_rule_id",
    "merge_section_rules",
    "normalize_extraction",
    "normalized_rule_from_item",
    "validate_rules",
]
