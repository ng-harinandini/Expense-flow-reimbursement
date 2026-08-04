"""Read-only tool wrapping ``KnowledgeService.retrieve_similar_claims``."""

from __future__ import annotations

from typing import Any, Mapping

from app.ai.interfaces.planner import ToolSpec
from app.ai.knowledge.service import KnowledgeService
from app.ai.tools.base import BaseTool
from app.ai.tools.builtin.serialization import context_bundle_to_dict
from app.domain.actor import Actor

_SPEC = ToolSpec(
    name="search_similar_claims",
    description="Search decision memory for past claims, reviews or decisions similar to a "
                "given claim summary.",
    input_schema={
        "type": "object",
        "required": ["claimSummary"],
        "properties": {"claimSummary": {"type": "string"}},
    },
    allowed_roles=frozenset(),
    mutates_state=False,
)


class SimilarClaimsTool(BaseTool):
    def __init__(self, knowledge_service: KnowledgeService) -> None:
        self._knowledge_service = knowledge_service

    @property
    def spec(self) -> ToolSpec:
        return _SPEC

    def _execute(self, arguments: Mapping[str, Any], *, actor: Actor) -> dict[str, Any]:
        claim_summary = str(arguments["claimSummary"])
        bundle = self._knowledge_service.retrieve_similar_claims(claim_summary, actor=actor)
        return context_bundle_to_dict(bundle)


__all__ = ["SimilarClaimsTool"]
