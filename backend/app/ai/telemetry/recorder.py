"""Telemetry implementation: nested timing spans and in-process metric aggregation (Task 14).

Task 14 asks for embedding time, search time, rerank time, inference time, latency, cost, recall,
precision, cache hit, failure reason and token usage. Rather than sprinkling timers at each of those
call sites, every stage opens a **span**; spans nest into a tree, and the individual figures fall
out of the tree. One mechanism, and adding a stage cannot forget to be measured.

The overriding rule, enforced by the ``fail_open`` behaviour throughout: **telemetry never changes
behaviour.** Every method swallows its own errors. Losing a metric is acceptable; failing a user's
retrieval because a counter overflowed or a log write broke is not.

Spans are held on a ``ContextVar``, matching how T003 already carries request/correlation ids
(:mod:`app.core.context`). That makes nesting automatic and thread/async-safe without threading a
span object through fifteen function signatures.
"""

from __future__ import annotations

import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Mapping, Optional

from app.ai.core.enums import TelemetryOperation
from app.ai.core.types import StageTiming
from app.core.context import current_correlation_id, current_request_id
from app.core.logging import get_logger

logger = get_logger(__name__)

# The innermost open span, so a child can attach itself without being passed one.
_current_span: ContextVar[Optional[TimingSpan]] = ContextVar("ai_current_span", default=None)


@dataclass
class TimingSpan:
    """One timed stage. Mutable by design — a span accumulates as its body runs."""

    operation: str
    parent: Optional[TimingSpan] = None
    attributes: dict[str, Any] = field(default_factory=dict)
    children: list[TimingSpan] = field(default_factory=list)
    candidates_in: int = 0
    candidates_out: int = 0
    failed: bool = False
    failure_reason: Optional[str] = None
    _start_ns: int = field(default_factory=time.perf_counter_ns)
    _end_ns: Optional[int] = None
    _token: Any = None
    _recorder: Optional[TelemetryRecorderImpl] = None

    # --- span protocol ------------------------------------------------------

    def set_attribute(self, key: str, value: Any) -> None:
        try:
            self.attributes[key] = value
        except Exception:  # pragma: no cover - defensive; telemetry must never raise
            pass

    def set_counts(self, *, candidates_in: int = 0, candidates_out: int = 0) -> None:
        """Record funnel counts: how many candidates entered and survived this stage."""
        try:
            if candidates_in:
                self.candidates_in = int(candidates_in)
            if candidates_out:
                self.candidates_out = int(candidates_out)
        except Exception:  # pragma: no cover - defensive
            pass

    def fail(self, reason: str) -> None:
        self.failed = True
        self.failure_reason = str(reason)[:500]

    @property
    def duration_ms(self) -> int:
        end = self._end_ns if self._end_ns is not None else time.perf_counter_ns()
        return max(0, (end - self._start_ns) // 1_000_000)

    def __enter__(self) -> TimingSpan:
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        """Close the span. Always runs, and records the exception as the failure reason.

        Returns ``None`` (never ``True``), so an exception in the measured body continues to
        propagate — telemetry observes failures, it does not suppress them.
        """
        self._end_ns = time.perf_counter_ns()
        if exc is not None and not self.failed:
            self.fail(f"{exc_type.__name__ if exc_type else 'Error'}: {exc}")
        try:
            if self._token is not None:
                _current_span.reset(self._token)
            if self._recorder is not None:
                self._recorder._close(self)
        except Exception:  # pragma: no cover - defensive
            pass

    # --- reporting ----------------------------------------------------------

    def to_timing(self) -> StageTiming:
        return StageTiming(
            stage=self.operation,
            duration_ms=self.duration_ms,
            candidates_in=self.candidates_in,
            candidates_out=self.candidates_out,
        )

    def flatten(self) -> list[TimingSpan]:
        """This span and all descendants, depth-first."""
        out = [self]
        for child in self.children:
            out.extend(child.flatten())
        return out

    def timings(self) -> tuple[StageTiming, ...]:
        """Per-stage timings for the whole subtree — what ``RetrievalResult`` carries."""
        return tuple(span.to_timing() for span in self.flatten())

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "durationMs": self.duration_ms,
            "candidatesIn": self.candidates_in,
            "candidatesOut": self.candidates_out,
            "failed": self.failed,
            "failureReason": self.failure_reason,
            "attributes": dict(self.attributes),
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class _Aggregate:
    """Running statistics for one operation."""

    count: int = 0
    failures: int = 0
    total_ms: int = 0
    max_ms: int = 0

    def observe(self, duration_ms: int, failed: bool) -> None:
        self.count += 1
        self.total_ms += duration_ms
        self.max_ms = max(self.max_ms, duration_ms)
        if failed:
            self.failures += 1

    @property
    def avg_ms(self) -> float:
        return (self.total_ms / self.count) if self.count else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "failures": self.failures,
            "avgMs": round(self.avg_ms, 2),
            "maxMs": self.max_ms,
            "totalMs": self.total_ms,
        }


class TelemetryRecorderImpl:
    """In-process span factory and metric aggregator.

    Aggregates live in memory. That is deliberate for T004: it gives the ``/metrics`` endpoint real
    numbers with no new infrastructure, and the interface is the seam where a Prometheus or
    OpenTelemetry exporter later attaches without touching a single call site.
    """

    def __init__(self, *, enabled: bool = True, slow_operation_ms: int = 2000) -> None:
        self._enabled = enabled
        self._slow_ms = slow_operation_ms
        self._lock = threading.RLock()
        self._operations: dict[str, _Aggregate] = {}
        self._metrics: dict[str, float] = {}
        self._counters: dict[str, int] = {}
        self._completed_roots: int = 0

    # --- span protocol ------------------------------------------------------

    def span(
        self,
        operation: TelemetryOperation | str,
        *,
        attributes: Optional[Mapping[str, Any]] = None,
    ) -> TimingSpan:
        """Open a span, nested under whichever span is currently open."""
        name = operation.value if isinstance(operation, TelemetryOperation) else str(operation)
        parent = _current_span.get()
        span = TimingSpan(
            operation=name,
            parent=parent,
            attributes=dict(attributes or {}),
            _recorder=self if self._enabled else None,
        )
        if parent is not None:
            parent.children.append(span)
        span._token = _current_span.set(span)
        return span

    def _close(self, span: TimingSpan) -> None:
        """Fold a finished span into the aggregates. Called from ``TimingSpan.__exit__``."""
        try:
            with self._lock:
                aggregate = self._operations.setdefault(span.operation, _Aggregate())
                aggregate.observe(span.duration_ms, span.failed)
                if span.parent is None:
                    self._completed_roots += 1

            if span.failed:
                logger.warning(
                    "ai.span.failed",
                    extra={
                        "operation": span.operation,
                        "durationMs": span.duration_ms,
                        "failureReason": span.failure_reason,
                        "requestId": current_request_id(),
                        "correlationId": current_correlation_id(),
                    },
                )
            elif span.duration_ms >= self._slow_ms:
                # Surfaced at WARNING because a slow AI stage is the usual first symptom of a
                # misconfigured provider or a cold model, and it is otherwise invisible.
                logger.warning(
                    "ai.span.slow",
                    extra={
                        "operation": span.operation,
                        "durationMs": span.duration_ms,
                        "thresholdMs": self._slow_ms,
                        "requestId": current_request_id(),
                    },
                )
        except Exception:  # pragma: no cover - telemetry must never raise
            pass

    # --- metrics ------------------------------------------------------------

    def record_metric(self, name: str, value: float,
                      *, attributes: Optional[Mapping[str, Any]] = None) -> None:
        """Record a gauge. Last write wins — used for ratios like cache hit rate."""
        try:
            with self._lock:
                self._metrics[name] = float(value)
            if attributes:
                logger.debug(
                    "ai.metric",
                    extra={"metric": name, "value": value, **dict(attributes)},
                )
        except Exception:  # pragma: no cover - defensive
            pass

    def increment(self, name: str, amount: int = 1) -> None:
        """Increment a counter (tokens, cost in micro-USD, cache hits, documents indexed)."""
        try:
            with self._lock:
                self._counters[name] = self._counters.get(name, 0) + int(amount)
        except Exception:  # pragma: no cover - defensive
            pass

    def snapshot(self) -> dict[str, Any]:
        """Everything the ``/metrics`` endpoint reports."""
        with self._lock:
            return {
                "enabled": self._enabled,
                "completedOperations": self._completed_roots,
                "operations": {
                    name: aggregate.to_dict()
                    for name, aggregate in sorted(self._operations.items())
                },
                "counters": dict(sorted(self._counters.items())),
                "gauges": {k: round(v, 4) for k, v in sorted(self._metrics.items())},
            }

    def reset(self) -> None:
        """Test helper."""
        with self._lock:
            self._operations.clear()
            self._metrics.clear()
            self._counters.clear()
            self._completed_roots = 0


class NullTelemetryRecorder:
    """No-op recorder for when telemetry is disabled or in pure unit tests.

    Exists so call sites never need ``if recorder is not None`` — a null object keeps the measured
    code free of telemetry branching.
    """

    def span(self, operation: TelemetryOperation | str,
             *, attributes: Optional[Mapping[str, Any]] = None) -> TimingSpan:
        name = operation.value if isinstance(operation, TelemetryOperation) else str(operation)
        return TimingSpan(operation=name, attributes=dict(attributes or {}), _recorder=None)

    def record_metric(self, name: str, value: float,
                      *, attributes: Optional[Mapping[str, Any]] = None) -> None:
        return None

    def increment(self, name: str, amount: int = 1) -> None:
        return None

    def snapshot(self) -> dict[str, Any]:
        return {"enabled": False, "operations": {}, "counters": {}, "gauges": {}}

    def reset(self) -> None:
        return None


def build_recorder(*, enabled: bool, slow_operation_ms: int = 2000):
    """Factory used by the composition root."""
    if not enabled:
        return NullTelemetryRecorder()
    return TelemetryRecorderImpl(enabled=True, slow_operation_ms=slow_operation_ms)


def current_span() -> Optional[TimingSpan]:
    """The innermost open span, if any. For code that wants to annotate without opening one."""
    return _current_span.get()


__all__ = [
    "NullTelemetryRecorder",
    "TelemetryRecorderImpl",
    "TimingSpan",
    "build_recorder",
    "current_span",
]
