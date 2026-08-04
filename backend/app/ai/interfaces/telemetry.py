"""Telemetry contract (Task 14).

Every timed stage of the platform opens a span. Spans nest, so one retrieval produces a tree —
``retrieve`` containing ``embed``, ``lexical_search``, ``vector_search``, ``fusion``, ``rerank`` —
and the per-stage numbers Task 14 asks for come out of the tree rather than from scattered ad-hoc
timers.

Contract:

1. **Telemetry never changes behaviour.** A recorder that fails must swallow its error. Losing a
   metric is acceptable; failing a user's retrieval because a metrics write failed is not.
2. A span always closes, including on exception, and records the failure reason when it does.
3. ``record_metric`` is for counters and gauges that are not spans (cache hit rate, recall).
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Mapping, Optional, Protocol, runtime_checkable

from app.ai.core.enums import TelemetryOperation


@runtime_checkable
class Span(Protocol):
    """An open timing scope. Used as a context manager."""

    def set_attribute(self, key: str, value: Any) -> None:
        ...

    def set_counts(self, *, candidates_in: int = 0, candidates_out: int = 0) -> None:
        """Record how many items entered and left the stage — the basis of funnel analysis."""
        ...

    def fail(self, reason: str) -> None:
        ...

    @property
    def duration_ms(self) -> int:
        ...

    def __enter__(self) -> Span:
        ...

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        ...


@runtime_checkable
class TelemetryRecorder(Protocol):
    """Creates spans and records metrics."""

    def span(
        self,
        operation: TelemetryOperation | str,
        *,
        attributes: Optional[Mapping[str, Any]] = None,
    ) -> Span:
        ...

    def record_metric(self, name: str, value: float,
                      *, attributes: Optional[Mapping[str, Any]] = None) -> None:
        ...

    def snapshot(self) -> dict[str, Any]:
        """Current aggregates, for the ``/metrics`` endpoint."""
        ...


__all__ = ["Span", "TelemetryRecorder"]
