"""``ToolRegistry`` — a name-keyed lookup of already-constructed tool instances.

Deliberately simpler than ``app.ai.registry.registry.ComponentRegistry``: tools are cheap,
stateless-per-request objects (typically just holding a ``KnowledgeService``), so there is no lazy
factory/instance-cache machinery to build — a plain dict registered once per request is enough.
"""

from __future__ import annotations

from typing import Sequence

from app.ai.interfaces.planner import Tool, ToolSpec


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        name = tool.spec.name
        if name in self._tools:
            raise ValueError(f"A tool named '{name}' is already registered.")
        self._tools[name] = tool

    def resolve(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise KeyError(f"No tool named '{name}' is registered.") from None

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def specs(self) -> Sequence[ToolSpec]:
        return tuple(tool.spec for tool in self._tools.values())


__all__ = ["ToolRegistry"]
