"""Planner (Task 16): a deterministic reference implementation of the seams
``app.ai.interfaces.planner`` declares.

No separate ``app/ai/planner/interfaces.py``/``plan_types.py`` — ``Plan``, ``PlanStep``,
``Planner``, ``Retriever`` and friends are all already declared in
``app.ai.interfaces.planner`` (M1), and a second, differently-located copy would only invite drift.
This package is the concrete, no-op reference implementation those interfaces call for, plus a thin
adapter closing a real naming gap M9 left behind (see ``retriever_adapter.py``).
"""

from app.ai.planner.noop_planner import NoOpPlanner  # noqa: F401
from app.ai.planner.retriever_adapter import KnowledgeServiceRetriever  # noqa: F401

__all__ = ["KnowledgeServiceRetriever", "NoOpPlanner"]
