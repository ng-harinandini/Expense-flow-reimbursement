"""Tools (Task 16): declarative, permission-aware, typed capabilities a planner may invoke.

``ToolRegistry`` (name-keyed) + ``DefaultToolExecutor`` (permission-checks then runs) + ``BaseTool``
(the timing/error-catching shell every concrete tool builds on). ``builtin/`` holds the three
read-only tools T004 ships, each wrapping one ``KnowledgeService`` retrieval method.
"""

from app.ai.tools.base import BaseTool  # noqa: F401
from app.ai.tools.executor import DefaultToolExecutor  # noqa: F401
from app.ai.tools.registry import ToolRegistry  # noqa: F401

__all__ = ["BaseTool", "DefaultToolExecutor", "ToolRegistry"]
