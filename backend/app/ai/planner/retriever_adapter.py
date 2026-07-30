"""``KnowledgeServiceRetriever`` — a thin adapter satisfying
``app.ai.interfaces.planner.Retriever``.

``KnowledgeService.search()``'s own docstring claims "this service can be handed to a future
planner directly" as a ``Retriever`` — but ``Retriever`` is a ``@runtime_checkable`` ``Protocol``
whose ``isinstance`` check matches on the literal method name ``retrieve``, and
``KnowledgeService`` names its method ``search``. `isinstance(knowledge_service, Retriever)` is
therefore ``False`` today, contradicting that docstring. Rather than touch M9's already-shipped
file for a one-line rename (which would also widen ``KnowledgeService``'s own surface beyond its
documented 8 methods), this adapter closes the gap from the outside: it holds a
``KnowledgeService`` and exposes exactly the one method ``Retriever`` needs.
"""

from __future__ import annotations

from app.ai.core.types import RetrievalQuery, RetrievalResult
from app.ai.knowledge.service import KnowledgeService


class KnowledgeServiceRetriever:
    """Narrows a ``KnowledgeService`` down to the one capability a planner should see: search."""

    def __init__(self, knowledge_service: KnowledgeService) -> None:
        self._knowledge_service = knowledge_service

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        return self._knowledge_service.search(query)


__all__ = ["KnowledgeServiceRetriever"]
