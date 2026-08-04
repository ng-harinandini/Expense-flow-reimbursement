"""Built-in read-only tools. All three wrap a `KnowledgeService` retrieval method and declare
``mutates_state=False`` — T004 ships no tool that can change anything (see
``tests/test_ai_planner.py::test_no_builtin_tool_mutates_state``).
"""

from __future__ import annotations

from app.ai.knowledge.service import KnowledgeService
from app.ai.tools.builtin.policy_search_tool import PolicySearchTool
from app.ai.tools.builtin.similar_claims_tool import SimilarClaimsTool
from app.ai.tools.builtin.vendor_context_tool import VendorContextTool
from app.ai.tools.registry import ToolRegistry

ALL_BUILTIN_TOOL_CLASSES = (PolicySearchTool, SimilarClaimsTool, VendorContextTool)


def register_builtin_tools(
    registry: ToolRegistry, *, knowledge_service: KnowledgeService
) -> ToolRegistry:
    """Register every built-in tool, all bound to the same ``knowledge_service``."""
    for tool_class in ALL_BUILTIN_TOOL_CLASSES:
        registry.register(tool_class(knowledge_service))
    return registry


__all__ = [
    "ALL_BUILTIN_TOOL_CLASSES",
    "PolicySearchTool",
    "SimilarClaimsTool",
    "VendorContextTool",
    "register_builtin_tools",
]
