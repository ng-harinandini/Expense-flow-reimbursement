"""``DefaultToolExecutor`` — the one place a plan step's permission is actually checked.

Matches ``app.ai.interfaces.planner.ToolExecutor``'s own contract exactly: resolves the named tool
from a ``ToolRegistry``, raises ``ToolPermissionError`` (403) before ever calling the tool if
``actor``'s role is not on its allowlist, and only then calls ``tool.run()``.
"""

from __future__ import annotations

from app.ai.core.errors import ToolPermissionError
from app.ai.interfaces.planner import PlanStep, ToolResult
from app.ai.tools.registry import ToolRegistry
from app.domain.actor import Actor


class DefaultToolExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def execute(self, step: PlanStep, *, actor: Actor) -> ToolResult:
        tool = self._registry.resolve(step.tool)
        if not tool.spec.permits(actor.role):
            raise ToolPermissionError(
                tool.spec.name, actor.role, sorted(tool.spec.allowed_roles)
            )
        return tool.run(step.arguments, actor=actor)


__all__ = ["DefaultToolExecutor"]
