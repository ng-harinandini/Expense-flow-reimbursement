"""``KnowledgeServiceReasoner`` — a thin adapter satisfying ``app.ai.interfaces.planner.Reasoner``.

``KnowledgeService.explain(question, context, *, actor=None)`` is already exactly the shape
``Reasoner`` wants, but the *method name* differs (``explain`` vs. ``reason``), so
``isinstance(knowledge_service, Reasoner)`` is ``False`` on the class itself — the same gap
``app.ai.planner.retriever_adapter.KnowledgeServiceRetriever`` closes for ``Retriever``. This
adapter is the ``Reasoner``-shaped front door; it does not reimplement anything — every explanation
is still "deterministic and extractive... always traceable to ``context.citations``," per M9's own
``explain()`` docstring, since this class only ever forwards to it.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from app.ai.core.types import ContextBundle
from app.ai.knowledge.service import KnowledgeService
from app.domain.actor import Actor


class KnowledgeServiceReasoner:
    name = "knowledge_service_explain"

    def __init__(self, knowledge_service: KnowledgeService) -> None:
        self._knowledge_service = knowledge_service

    def reason(
        self, question: str, context: ContextBundle, *, actor: Optional[Actor] = None
    ) -> Mapping[str, Any]:
        return self._knowledge_service.explain(question, context, actor=actor)


__all__ = ["KnowledgeServiceReasoner"]
