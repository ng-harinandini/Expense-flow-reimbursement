"""``NoOpPlanner`` — the deterministic reference ``Planner`` this macro's own spec calls for.

Deliberately not "intelligent": it does not read ``goal`` at all, it replays a fixed sequence of
steps supplied at construction. Its only job is to prove the rest of the platform — tool registry,
executor, permission checks, structured-output validation — composes end to end without needing a
real (LLM-backed) planner to exist yet. A future real planner satisfies the exact same
``app.ai.interfaces.planner.Planner`` protocol and is a drop-in replacement.
"""

from __future__ import annotations

from typing import Optional, Sequence

from app.ai.core.types import ContextBundle
from app.ai.interfaces.planner import Plan, PlanStep
from app.domain.actor import Actor


class NoOpPlanner:
    """Always returns the same steps, regardless of ``goal``, ``context`` or ``actor``."""

    name = "noop"

    def __init__(self, steps: Sequence[PlanStep] = (), *, max_steps: int = 8) -> None:
        self._steps = tuple(steps)
        self._max_steps = max_steps

    def plan(
        self, goal: str, *, context: Optional[ContextBundle] = None, actor: Optional[Actor] = None
    ) -> Plan:
        return Plan(goal=goal, steps=self._steps, max_steps=self._max_steps)


__all__ = ["NoOpPlanner"]
