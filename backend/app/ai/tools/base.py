"""``BaseTool`` — the timing/error-catching shell every concrete tool builds on.

Permission checking (``ToolSpec.permits``) deliberately lives in ``ToolExecutor.execute``, not
here — see ``app.ai.interfaces.planner.ToolExecutor``'s own docstring ("Raises
``ToolPermissionError``..."). A tool's ``run()`` is trusted to already be permission-checked by
whatever called it; ``BaseTool`` only owns turning a subclass's business logic into the uniform
``ToolResult`` shape (timed, never raising) every caller can rely on.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Mapping

from app.ai.interfaces.planner import ToolResult, ToolSpec
from app.domain.actor import Actor


class BaseTool(ABC):
    @property
    @abstractmethod
    def spec(self) -> ToolSpec:
        ...

    def run(self, arguments: Mapping[str, Any], *, actor: Actor) -> ToolResult:
        """Never raises: any exception from ``_execute`` becomes ``ToolResult(ok=False, ...)``, so
        a caller can always inspect the result rather than needing a try/except around every
        call."""
        start = time.monotonic()
        try:
            output = self._execute(arguments, actor=actor)
        except Exception as exc:  # noqa: BLE001 - deliberately broad: see docstring
            duration_ms = int((time.monotonic() - start) * 1000)
            return ToolResult(
                tool=self.spec.name, ok=False, error=str(exc), duration_ms=duration_ms
            )
        duration_ms = int((time.monotonic() - start) * 1000)
        return ToolResult(tool=self.spec.name, ok=True, output=output, duration_ms=duration_ms)

    @abstractmethod
    def _execute(self, arguments: Mapping[str, Any], *, actor: Actor) -> Any:
        """The tool's actual work. Raise on failure — ``run()`` turns it into a result."""
        ...


__all__ = ["BaseTool"]
