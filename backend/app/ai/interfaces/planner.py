"""Planner / tool / reasoning contracts (Task 16).

**Interfaces only — no agent ships in T004.** These are the seams a future autonomous workflow will
plug into, defined now so that when agents arrive they inherit the platform's guarantees (auditing,
versioning, role checks, reproducibility) instead of growing a parallel set.

Two safety properties are built into the shapes themselves rather than left to an implementer:

* **Tools declare the roles that may invoke them.** Permission is a property of the tool, checked by
  the executor, so a future planner cannot reach a capability its caller lacks — it never gets the
  chance to try.
* **Tools declare whether they mutate.** T004 ships only read-only tools, and
  ``tests/test_ai_isolation.py`` asserts no registered tool can mutate claim state. When a mutating
  tool is eventually added, that assertion is where the decision gets reviewed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol, Sequence, runtime_checkable

from app.ai.core.types import ContextBundle, RetrievalQuery, RetrievalResult
from app.domain.actor import Actor


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Declarative description of a tool: its schema, its permissions, its blast radius."""

    name: str
    description: str
    input_schema: Mapping[str, Any]
    allowed_roles: frozenset[str] = frozenset()
    mutates_state: bool = False
    idempotent: bool = True

    def permits(self, role: str) -> bool:
        """Empty ``allowed_roles`` means any authenticated role — never "no check performed"."""
        return not self.allowed_roles or role in self.allowed_roles


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Outcome of one tool invocation."""

    tool: str
    ok: bool
    output: Any = None
    error: Optional[str] = None
    duration_ms: int = 0


@dataclass(frozen=True, slots=True)
class PlanStep:
    """One intended tool call."""

    tool: str
    arguments: Mapping[str, Any]
    rationale: str = ""


@dataclass(frozen=True, slots=True)
class Plan:
    """An ordered set of steps plus the goal that motivated them.

    ``max_steps`` is on the plan rather than the executor so a runaway loop is bounded by the
    artefact that gets audited, not by a setting somewhere else.
    """

    goal: str
    steps: tuple[PlanStep, ...] = ()
    max_steps: int = 8
    metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class Tool(Protocol):
    """A capability a planner may invoke."""

    @property
    def spec(self) -> ToolSpec:
        ...

    def run(self, arguments: Mapping[str, Any], *, actor: Actor) -> ToolResult:
        """Execute. Receives the domain ``Actor`` so the tool can enforce ownership, not just
        role.
        """
        ...


@runtime_checkable
class ToolExecutor(Protocol):
    """Runs plan steps, enforcing permissions and step limits."""

    def execute(self, step: PlanStep, *, actor: Actor) -> ToolResult:
        """Raises ``ToolPermissionError`` (403) if ``actor``'s role is not on the tool's
        allowlist.
        """
        ...


@runtime_checkable
class Planner(Protocol):
    """Turns a goal into a plan. No implementation ships in T004 beyond a deterministic
    reference.
    """

    name: str

    def plan(self, goal: str, *, context: Optional[ContextBundle] = None,
             actor: Optional[Actor] = None) -> Plan:
        ...


@runtime_checkable
class Retriever(Protocol):
    """The retrieval capability, as a planner sees it.

    Narrower than ``KnowledgeService`` on purpose: a planner should be able to search, and nothing
    else. Handing it the full facade would let a future agent reach ingestion and deletion.
    """

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        ...


@runtime_checkable
class Reasoner(Protocol):
    """Produces an explanation or recommendation from retrieved evidence.

    Returns a structure that always includes its citations, so a conclusion without evidence is not
    expressible through this interface.
    """

    name: str

    def reason(self, question: str, context: ContextBundle,
               *, actor: Optional[Actor] = None) -> Mapping[str, Any]:
        ...


@runtime_checkable
class Validator(Protocol):
    """Checks a model's structured output against a schema and the platform's invariants."""

    def validate(self, payload: Mapping[str, Any],
                 schema: Mapping[str, Any]) -> tuple[bool, Sequence[str]]:
        """Return ``(is_valid, errors)``. Never raises on invalid input — invalidity is data."""
        ...


@runtime_checkable
class Memory(Protocol):
    """Read access to historical decisions (Task 9), for retrieval-augmented reasoning."""

    def remember(self, record: Any) -> None:
        ...

    def recall(self, query: str, *, limit: int = 5,
               tenant_id: str = "default") -> Sequence[Mapping[str, Any]]:
        ...


__all__ = [
    "Memory",
    "Plan",
    "PlanStep",
    "Planner",
    "Reasoner",
    "Retriever",
    "Tool",
    "ToolExecutor",
    "ToolResult",
    "ToolSpec",
    "Validator",
]
