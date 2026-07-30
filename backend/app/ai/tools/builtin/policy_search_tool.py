"""Read-only tool wrapping ``KnowledgeService.retrieve_policy``."""

from __future__ import annotations

from typing import Any, Mapping

from app.ai.interfaces.planner import ToolSpec
from app.ai.knowledge.service import KnowledgeService
from app.ai.tools.base import BaseTool
from app.ai.tools.builtin.serialization import context_bundle_to_dict
from app.domain.actor import Actor

_SPEC = ToolSpec(
    name="search_policy",
    description="Search indexed policy documents for the answer to a policy question.",
    input_schema={
        "type": "object",
        "required": ["question"],
        "properties": {"question": {"type": "string"}},
    },
    allowed_roles=frozenset(),  # any authenticated role — a read-only knowledge lookup
    mutates_state=False,
)


class PolicySearchTool(BaseTool):
    def __init__(self, knowledge_service: KnowledgeService) -> None:
        self._knowledge_service = knowledge_service

    @property
    def spec(self) -> ToolSpec:
        return _SPEC

    def _execute(self, arguments: Mapping[str, Any], *, actor: Actor) -> dict[str, Any]:
        question = str(arguments["question"])
        bundle = self._knowledge_service.retrieve_policy(question, actor=actor)
        return context_bundle_to_dict(bundle)


__all__ = ["PolicySearchTool"]
