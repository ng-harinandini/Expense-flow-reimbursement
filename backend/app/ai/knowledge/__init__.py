"""``KnowledgeService`` and the pieces it composes: citations, context building, vendor knowledge.

The **only** public surface of the AI platform (see ``app/ai/__init__.py``). Nothing outside
``app/ai`` may import ``app.ai.retrieval``, ``app.ai.ingestion``, ``app.ai.chunking``,
``app.ai.embeddings``, ``app.ai.vector_store``, ``app.ai.reranking``, ``app.ai.parsing`` or
``app.ai.memory`` directly — only this package.
"""

from __future__ import annotations

from app.ai.knowledge.citations import attach_citations
from app.ai.knowledge.context_builder import build_context
from app.ai.knowledge.service import KnowledgeService
from app.ai.knowledge.vendor_knowledge import VENDOR_SOURCE_TYPES, vendor_context_query

__all__ = [
    "VENDOR_SOURCE_TYPES",
    "KnowledgeService",
    "attach_citations",
    "build_context",
    "vendor_context_query",
]
