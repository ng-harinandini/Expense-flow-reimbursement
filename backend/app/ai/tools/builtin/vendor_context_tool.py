"""Read-only tool wrapping ``KnowledgeService.get_vendor_context``."""

from __future__ import annotations

from typing import Any, Mapping

from app.ai.interfaces.planner import ToolSpec
from app.ai.knowledge.service import KnowledgeService
from app.ai.tools.base import BaseTool
from app.ai.tools.builtin.serialization import context_bundle_to_dict
from app.domain.actor import Actor

_SPEC = ToolSpec(
    name="get_vendor_context",
    description="Retrieve indexed vendor knowledge (contracts, manuals) for one vendor.",
    input_schema={
        "type": "object",
        "required": ["vendorName"],
        "properties": {"vendorName": {"type": "string"}},
    },
    allowed_roles=frozenset(),
    mutates_state=False,
)


class VendorContextTool(BaseTool):
    def __init__(self, knowledge_service: KnowledgeService) -> None:
        self._knowledge_service = knowledge_service

    @property
    def spec(self) -> ToolSpec:
        return _SPEC

    def _execute(self, arguments: Mapping[str, Any], *, actor: Actor) -> dict[str, Any]:
        vendor_name = str(arguments["vendorName"])
        bundle = self._knowledge_service.get_vendor_context(vendor_name, actor=actor)
        return context_bundle_to_dict(bundle)


__all__ = ["VendorContextTool"]
