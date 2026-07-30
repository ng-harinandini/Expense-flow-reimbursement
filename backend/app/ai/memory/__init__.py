"""Decision memory (Task 9): the historical-case memory later reasoning retrieves from.

Reached only through :class:`app.ai.knowledge.service.KnowledgeService` — see
:mod:`app.ai.memory.decision_memory` for why this is not a parallel schema, and
:mod:`app.ai.memory.indexer` for the write path.
"""

from __future__ import annotations

from app.ai.memory.decision_memory import (
    DECISION_MEMORY_SOURCE_TYPES,
    similar_claims_query,
    source_type_for,
)
from app.ai.memory.indexer import record_decision

__all__ = [
    "DECISION_MEMORY_SOURCE_TYPES",
    "record_decision",
    "similar_claims_query",
    "source_type_for",
]
