"""Planner / tool / reasoning interface tests (T004-M12).

Pure-Python unit tests for the schema validator, registry, executor and no-op planner; database-
backed end-to-end tests for the built-in tools (which wrap a real `KnowledgeService`), exactly like
`test_ai_knowledge.py`'s `_Stack` pattern.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.ai.core.enums import KnowledgeSourceType
from app.ai.core.errors import ToolPermissionError
from app.ai.embeddings import EmbeddingService
from app.ai.core.config import BGE_M3_DIMENSIONS
from app.ai.ingestion.pipeline import DocumentMetadataInput, ingest_document
from app.ai.ingestion.sources.inline import InlineSource
from app.ai.interfaces.planner import Plan, PlanStep, ToolResult, ToolSpec
from app.ai.knowledge.service import KnowledgeService
from app.ai.planner.noop_planner import NoOpPlanner
from app.ai.planner.retriever_adapter import KnowledgeServiceRetriever
from app.ai.providers.embeddings.deterministic import DeterministicEmbeddingProvider
from app.ai.reasoning.explanation import KnowledgeServiceReasoner
from app.ai.reasoning.structured_output import validate
from app.ai.reasoning.validators import SchemaValidator
from app.ai.tools.base import BaseTool
from app.ai.tools.builtin import ALL_BUILTIN_TOOL_CLASSES, register_builtin_tools
from app.ai.tools.builtin.policy_search_tool import PolicySearchTool
from app.ai.tools.builtin.schemas import CONTEXT_BUNDLE_SCHEMA
from app.ai.tools.executor import DefaultToolExecutor
from app.ai.tools.registry import ToolRegistry
from app.ai.vector_store.memory import InMemoryVectorStore
from app.domain.actor import Actor

TENANT_ID = "planner-test-tenant"
DIMENSIONS = BGE_M3_DIMENSIONS


def _embedding_service() -> EmbeddingService:
    return EmbeddingService(DeterministicEmbeddingProvider(dimensions=DIMENSIONS, version="v1"))


def _vector_store() -> InMemoryVectorStore:
    return InMemoryVectorStore(dimensions=DIMENSIONS)


def _actor(role: str = "employee") -> Actor:
    return Actor(role=role, sub=f"sub-{role}", email=f"{role}@corp.com")


class _Stack:
    """One shared embedding service + vector store, so an ingested document is actually visible
    to a `KnowledgeService` built on the same pair."""

    def __init__(self) -> None:
        self.embedding_service = _embedding_service()
        self.vector_store = _vector_store()

    def service(self, session: Session) -> KnowledgeService:
        return KnowledgeService(
            session=session, embedding_service=self.embedding_service,
            vector_store=self.vector_store, tenant_id=TENANT_ID,
        )

    def ingest_policy(self, session: Session, *, title: str = "policy-doc") -> None:
        source = InlineSource(
            text=(
                "Travel Reimbursement Policy\n\nMeals are reimbursed at actual cost up to the "
                "daily per-diem limit for the traveler's destination city."
            ),
            file_name=f"{title}.txt", source_type=KnowledgeSourceType.POLICY, tenant_id=TENANT_ID,
        )
        ingest_document(
            source, session=session, metadata=DocumentMetadataInput(title=title),
            embedding_service=self.embedding_service, vector_store=self.vector_store,
        )


# ---------------------------------------------------------------------------
# 1. structured_output.validate — pure Python
# ---------------------------------------------------------------------------


def test_validate_accepts_a_matching_payload() -> None:
    schema = {
        "type": "object", "required": ["name"], "properties": {"name": {"type": "string"}},
    }
    is_valid, errors = validate({"name": "Ada"}, schema)
    assert is_valid is True
    assert errors == []


def test_validate_reports_a_missing_required_field() -> None:
    schema = {"type": "object", "required": ["name"], "properties": {}}
    is_valid, errors = validate({}, schema)
    assert is_valid is False
    assert any("name" in e for e in errors)


def test_validate_reports_a_wrong_type() -> None:
    schema = {"type": "object", "properties": {"count": {"type": "integer"}}}
    is_valid, errors = validate({"count": "not a number"}, schema)
    assert is_valid is False


def test_validate_rejects_a_bool_as_an_integer() -> None:
    """``bool`` is an ``int`` subclass in Python; a naive isinstance check would wrongly accept
    it."""
    schema = {"type": "object", "properties": {"count": {"type": "integer"}}}
    is_valid, _ = validate({"count": True}, schema)
    assert is_valid is False


def test_validate_rejects_a_bool_as_a_number() -> None:
    """Regression: the original guard excluded ``bool`` from ``'integer'`` but not ``'number'``
    (``bool`` is an ``int`` subclass, so it silently satisfied ``(int, float)``) — caught by a
    No-Slop Review."""
    is_valid, _ = validate(True, {"type": "number"})
    assert is_valid is False


def test_validate_recurses_into_array_items() -> None:
    schema = {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {"type": "string"}}},
    }
    is_valid, errors = validate({"items": ["a", 2, "c"]}, schema)
    assert is_valid is False
    assert any("[1]" in e for e in errors)


def test_validate_checks_enum_membership() -> None:
    schema = {"type": "string", "enum": ["a", "b"]}
    assert validate("a", schema)[0] is True
    assert validate("z", schema)[0] is False


def test_validate_ignores_an_unrecognized_type_name() -> None:
    """Not this validator's job to reject a schema authoring mistake in ``type`` itself — it only
    validates the payload against what it does recognize."""
    is_valid, errors = validate("anything", {"type": "not-a-real-type"})
    assert is_valid is True
    assert errors == []


def test_validate_array_without_an_items_schema_only_checks_the_outer_type() -> None:
    is_valid, errors = validate([1, "mixed", None], {"type": "array"})
    assert is_valid is True
    assert errors == []


def test_context_bundle_schema_matches_the_real_tool_output_shape() -> None:
    from app.ai.tools.builtin.serialization import context_bundle_to_dict
    from app.ai.core.types import ContextBundle

    bundle = ContextBundle(text="hi", citations=(), token_count=1, chunk_count=1)
    is_valid, errors = validate(context_bundle_to_dict(bundle), CONTEXT_BUNDLE_SCHEMA)
    assert is_valid, errors


def test_schema_validator_satisfies_the_validator_protocol() -> None:
    validator = SchemaValidator()
    is_valid, errors = validator.validate({"a": 1}, {"type": "object"})
    assert is_valid is True
    assert errors == []


# ---------------------------------------------------------------------------
# 2. NoOpPlanner — pure Python
# ---------------------------------------------------------------------------


def test_noop_planner_replays_its_fixed_steps_regardless_of_goal() -> None:
    steps = (PlanStep(tool="search_policy", arguments={"question": "x"}),)
    planner = NoOpPlanner(steps)
    plan_a = planner.plan("goal A")
    plan_b = planner.plan("an entirely different goal")
    assert plan_a.steps == steps
    assert plan_b.steps == steps
    assert isinstance(plan_a, Plan)


# ---------------------------------------------------------------------------
# 3. ToolRegistry / DefaultToolExecutor — pure Python (a fake tool, no database)
# ---------------------------------------------------------------------------


class _EchoTool(BaseTool):
    def __init__(self, *, allowed_roles: frozenset[str] = frozenset()) -> None:
        self._spec = ToolSpec(
            name="echo", description="echoes its input", input_schema={},
            allowed_roles=allowed_roles, mutates_state=False,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def _execute(self, arguments, *, actor):
        return dict(arguments)


class _BoomTool(BaseTool):
    spec = ToolSpec(name="boom", description="always fails", input_schema={})

    def _execute(self, arguments, *, actor):
        raise RuntimeError("boom")


def test_tool_registry_resolves_a_registered_tool() -> None:
    registry = ToolRegistry()
    tool = _EchoTool()
    registry.register(tool)
    assert registry.resolve("echo") is tool
    assert registry.names() == ("echo",)


def test_tool_registry_rejects_a_duplicate_name() -> None:
    registry = ToolRegistry()
    registry.register(_EchoTool())
    with pytest.raises(ValueError):
        registry.register(_EchoTool())


def test_tool_registry_raises_for_an_unknown_tool() -> None:
    registry = ToolRegistry()
    with pytest.raises(KeyError):
        registry.resolve("nope")


def test_base_tool_turns_an_exception_into_a_failed_result_not_a_raise() -> None:
    result = _BoomTool().run({}, actor=_actor())
    assert isinstance(result, ToolResult)
    assert result.ok is False
    assert "boom" in result.error


def test_executor_runs_a_permitted_tool() -> None:
    registry = ToolRegistry()
    registry.register(_EchoTool())
    executor = DefaultToolExecutor(registry)
    result = executor.execute(
        PlanStep(tool="echo", arguments={"x": 1}), actor=_actor("employee")
    )
    assert result.ok is True
    assert result.output == {"x": 1}


def test_executor_refuses_a_tool_when_the_actors_role_is_not_on_the_allowlist() -> None:
    """T004-M12 Done Check: 'a tool invoked by a role not on its allowlist is refused'."""
    registry = ToolRegistry()
    registry.register(_EchoTool(allowed_roles=frozenset({"admin"})))
    executor = DefaultToolExecutor(registry)
    with pytest.raises(ToolPermissionError):
        executor.execute(PlanStep(tool="echo", arguments={}), actor=_actor("employee"))


def test_executor_permits_an_allowed_role() -> None:
    registry = ToolRegistry()
    registry.register(_EchoTool(allowed_roles=frozenset({"admin"})))
    executor = DefaultToolExecutor(registry)
    result = executor.execute(PlanStep(tool="echo", arguments={}), actor=_actor("admin"))
    assert result.ok is True


# ---------------------------------------------------------------------------
# 4. No builtin tool can mutate claim state
# ---------------------------------------------------------------------------


def test_no_builtin_tool_mutates_state() -> None:
    """T004-M12 Done Check: 'a test asserts no tool can mutate claim state'."""
    # knowledge_service=None is safe here: .spec is a pure property returning a module-level
    # constant, never touching self._knowledge_service.
    for tool_class in ALL_BUILTIN_TOOL_CLASSES:
        instance = tool_class(knowledge_service=None)  # type: ignore[arg-type]
        assert instance.spec.mutates_state is False


# ---------------------------------------------------------------------------
# 5. Built-in tools + adapters — database-backed end to end
# ---------------------------------------------------------------------------


def test_policy_search_tool_returns_schema_valid_context(db_session: Session) -> None:
    stack = _Stack()
    stack.ingest_policy(db_session)
    knowledge_service = stack.service(db_session)
    tool = PolicySearchTool(knowledge_service)

    result = tool.run({"question": "what is the meal per diem limit"}, actor=_actor())
    assert result.ok is True
    is_valid, errors = validate(result.output, CONTEXT_BUNDLE_SCHEMA)
    assert is_valid, errors
    assert result.output["chunkCount"] > 0


def test_similar_claims_tool_returns_schema_valid_context(db_session: Session) -> None:
    from app.ai.core.enums import DecisionMemoryKind
    from app.ai.tools.builtin.similar_claims_tool import SimilarClaimsTool

    stack = _Stack()
    knowledge_service = stack.service(db_session)
    knowledge_service.record_decision(
        DecisionMemoryKind.CLAIM, "claim-001",
        "Employee submitted a travel claim for 500 USD covering hotel and meals.",
    )
    tool = SimilarClaimsTool(knowledge_service)

    result = tool.run({"claimSummary": "travel claim for hotel and meals"}, actor=_actor())
    assert result.ok is True
    is_valid, errors = validate(result.output, CONTEXT_BUNDLE_SCHEMA)
    assert is_valid, errors


def test_vendor_context_tool_returns_schema_valid_context(db_session: Session) -> None:
    from app.ai.tools.builtin.vendor_context_tool import VendorContextTool

    stack = _Stack()
    source = InlineSource(
        text="Acme Corp master service agreement: net-30 payment terms.",
        file_name="acme-contract.txt", source_type=KnowledgeSourceType.VENDOR_CONTRACT,
        tenant_id=TENANT_ID,
    )
    ingest_document(
        source, session=db_session, metadata=DocumentMetadataInput(title="acme-contract"),
        embedding_service=stack.embedding_service, vector_store=stack.vector_store,
    )
    knowledge_service = stack.service(db_session)
    tool = VendorContextTool(knowledge_service)

    result = tool.run({"vendorName": "Acme Corp"}, actor=_actor())
    assert result.ok is True
    is_valid, errors = validate(result.output, CONTEXT_BUNDLE_SCHEMA)
    assert is_valid, errors


def test_register_builtin_tools_registers_all_three(db_session: Session) -> None:
    stack = _Stack()
    knowledge_service = stack.service(db_session)
    registry = ToolRegistry()
    register_builtin_tools(registry, knowledge_service=knowledge_service)
    assert set(registry.names()) == {"search_policy", "search_similar_claims", "get_vendor_context"}


def test_knowledge_service_retriever_adapts_search_to_retrieve(db_session: Session) -> None:
    from app.ai.core.types import RetrievalQuery

    stack = _Stack()
    stack.ingest_policy(db_session)
    knowledge_service = stack.service(db_session)
    retriever = KnowledgeServiceRetriever(knowledge_service)

    result = retriever.retrieve(
        RetrievalQuery(text="meal per diem", tenant_id=TENANT_ID, top_k=5)
    )
    assert result.chunks


def test_knowledge_service_reasoner_adapts_explain_to_reason(db_session: Session) -> None:
    stack = _Stack()
    stack.ingest_policy(db_session)
    knowledge_service = stack.service(db_session)
    reasoner = KnowledgeServiceReasoner(knowledge_service)

    bundle = knowledge_service.retrieve_policy("what is the meal per diem limit")
    explanation = reasoner.reason("what is the meal per diem limit", bundle)
    assert isinstance(explanation, dict)


# ---------------------------------------------------------------------------
# 6. End-to-end Done Check: a reference plan executes against the no-op planner using only
#    registered tools and returns schema-valid structured output
# ---------------------------------------------------------------------------


def test_reference_plan_executes_end_to_end_with_schema_valid_output(db_session: Session) -> None:
    """T004-M12 Done Check: 'a reference plan executes end to end against the no-op planner using
    only registered tools and returns schema-valid structured output'."""
    stack = _Stack()
    stack.ingest_policy(db_session)
    knowledge_service = stack.service(db_session)

    registry = ToolRegistry()
    register_builtin_tools(registry, knowledge_service=knowledge_service)
    executor = DefaultToolExecutor(registry)

    planner = NoOpPlanner(
        (PlanStep(tool="search_policy", arguments={"question": "meal per diem limit"}),)
    )
    plan = planner.plan("Explain the meal reimbursement policy", actor=_actor())
    assert len(plan.steps) <= plan.max_steps

    validator = SchemaValidator()
    for step in plan.steps:
        result = executor.execute(step, actor=_actor())
        assert result.ok is True
        is_valid, errors = validator.validate(result.output, CONTEXT_BUNDLE_SCHEMA)
        assert is_valid, errors
