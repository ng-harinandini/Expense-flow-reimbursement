"""Prompt registry tests (T004-M11).

Database-backed, exactly like ``test_ai_duplicate_detection.py``'s vendor-resolution tests: real
Postgres, real ``ai_prompt_templates`` rows, real ``AuditService`` writes to ``audit_logs``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.core.enums import PromptStatus
from app.ai.core.errors import KnowledgeNotFoundError, PromptRenderError
from app.ai.models.prompt import PromptTemplate
from app.ai.prompts.builtin import BUILTIN_PROMPTS, register_builtin_prompts
from app.ai.prompts.registry import PromptRegistry
from app.ai.prompts.rendering import extract_placeholders, render, validate_declared_variables
from app.ai.prompts.testing import PromptTestCase, run_prompt_tests
from app.ai.repositories.prompt_repository import PromptTemplateRepository
from app.domain.actor import Actor


def _code() -> str:
    return f"TEST_PROMPT_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# 1. rendering.py — pure Python, no database
# ---------------------------------------------------------------------------


def test_extract_placeholders_finds_every_distinct_variable() -> None:
    text = "Hello {{name}}, you have {{count}} items, {{name}} again."
    assert extract_placeholders(text) == frozenset({"name", "count"})


def test_render_substitutes_declared_variables() -> None:
    assert render("t", "Hello {{name}}!", ["name"], name="World") == "Hello World!"


def test_render_raises_on_a_missing_variable() -> None:
    """T004-M11 Done Check: 'rendering with a missing variable raises'."""
    with pytest.raises(PromptRenderError) as exc_info:
        render("t", "Hello {{name}}!", ["name"])
    assert "missing" in str(exc_info.value)


def test_render_raises_on_an_unexpected_extra_variable() -> None:
    with pytest.raises(PromptRenderError) as exc_info:
        render("t", "Hello {{name}}!", ["name"], name="World", extra="oops")
    assert "unexpected" in str(exc_info.value)


def test_validate_declared_variables_passes_for_a_matching_list() -> None:
    validate_declared_variables("t", "Hi {{a}} and {{b}}", ["a", "b"])  # must not raise


def test_validate_declared_variables_raises_for_a_stale_list() -> None:
    with pytest.raises(PromptRenderError):
        validate_declared_variables("t", "Hi {{a}}", ["a", "b"])  # "b" declared but unused in text


@pytest.mark.parametrize(
    "malformed_text",
    [
        "Amount: {{123amount}} for {{name}}",  # identifier starting with a digit
        "Empty: {{}}",                          # nothing between the braces
        "Unmatched: {{name} and }}",            # mismatched brace counts
    ],
)
def test_malformed_placeholder_syntax_is_rejected_not_silently_passed_through(
    malformed_text: str,
) -> None:
    """Regression: a malformed ``{{...}}`` (e.g. an invalid identifier) previously fell outside
    the strict placeholder regex entirely, so it was invisible to both ``validate_declared_
    variables`` and ``render`` and leaked raw template syntax straight into rendered output —
    caught by a No-Slop Review."""
    with pytest.raises(PromptRenderError):
        validate_declared_variables("t", malformed_text, ["name"])
    with pytest.raises(PromptRenderError):
        render("t", malformed_text, ["name"], name="Ada")


def test_well_formed_placeholders_are_unaffected_by_the_malformed_syntax_check() -> None:
    text = "Hello {{name}}, you have {{count}} items."
    validate_declared_variables("t", text, ["name", "count"])  # must not raise
    assert render("t", text, ["name", "count"], name="Ada", count=3) == (
        "Hello Ada, you have 3 items."
    )


# ---------------------------------------------------------------------------
# 2. testing.py — the prompt test-case harness
# ---------------------------------------------------------------------------


def test_run_prompt_tests_reports_pass_and_fail_correctly() -> None:
    """T004-M11 Done Check: 'a prompt test case suite runs against a registered prompt'."""
    template_text = "Order {{order_id}} shipped to {{city}}."
    variables = ["order_id", "city"]
    cases = [
        PromptTestCase(
            "happy path", values={"order_id": "42", "city": "Austin"},
            expected_contains=("42", "Austin"),
        ),
        PromptTestCase(
            "wrong expectation", values={"order_id": "42", "city": "Austin"},
            expected_contains=("Nowhere",),
        ),
        PromptTestCase("missing variable", values={"order_id": "42"}, expect_render_error=True),
        PromptTestCase(
            "expected an error but got none", values={"order_id": "42", "city": "Austin"},
            expect_render_error=True,
        ),
    ]
    results = run_prompt_tests("t", template_text, variables, cases)
    by_name = {r.case_name: r for r in results}
    assert by_name["happy path"].passed is True
    assert by_name["wrong expectation"].passed is False
    assert by_name["missing variable"].passed is True
    assert by_name["expected an error but got none"].passed is False


# ---------------------------------------------------------------------------
# 2b. Schema constraints — database-backed
# ---------------------------------------------------------------------------


def test_blank_template_text_is_rejected(db_session: Session) -> None:
    db_session.add(
        PromptTemplate(
            code=_code(), version=1, status=PromptStatus.PUBLISHED, name="x",
            template_text="   ", variables=[],
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_non_positive_version_is_rejected(db_session: Session) -> None:
    db_session.add(
        PromptTemplate(
            code=_code(), version=0, status=PromptStatus.PUBLISHED, name="x",
            template_text="Hi", variables=[],
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


# ---------------------------------------------------------------------------
# 3. PromptTemplateRepository — database-backed
# ---------------------------------------------------------------------------


def test_publish_version_creates_version_1_and_marks_it_published(db_session: Session) -> None:
    repo = PromptTemplateRepository(db_session)
    code = _code()
    prompt = repo.publish_version(
        code=code, name="Test", template_text="Hi {{name}}.", variables=["name"],
    )
    assert prompt.version == 1
    assert prompt.status == PromptStatus.PUBLISHED


def test_publish_version_retires_the_previous_version(db_session: Session) -> None:
    """T004-M11 Done Check: 'publishing a prompt increments the version and leaves prior versions
    retrievable'."""
    repo = PromptTemplateRepository(db_session)
    code = _code()
    v1 = repo.publish_version(code=code, name="v1", template_text="A {{x}}", variables=["x"])
    v2 = repo.publish_version(code=code, name="v2", template_text="B {{y}}", variables=["y"])

    assert v2.version == 2
    assert v2.status == PromptStatus.PUBLISHED

    reloaded_v1 = repo.get_by_code(code, version=1)
    assert reloaded_v1 is not None
    assert reloaded_v1.id == v1.id
    assert reloaded_v1.status == PromptStatus.RETIRED
    assert reloaded_v1.template_text == "A {{x}}"  # never mutated, still retrievable


def test_list_versions_returns_every_version_newest_first(db_session: Session) -> None:
    repo = PromptTemplateRepository(db_session)
    code = _code()
    repo.publish_version(code=code, name="v1", template_text="A", variables=[])
    repo.publish_version(code=code, name="v2", template_text="B", variables=[])
    repo.publish_version(code=code, name="v3", template_text="C", variables=[])

    versions = repo.list_versions(code)
    assert [v.version for v in versions] == [3, 2, 1]


def test_rollback_restores_an_earlier_version_without_deleting_the_newer_one(
    db_session: Session,
) -> None:
    """T004-M11 Done Check: 'rollback restores an earlier version without deleting the newer
    one'."""
    repo = PromptTemplateRepository(db_session)
    code = _code()
    v1 = repo.publish_version(code=code, name="v1", template_text="A {{x}}", variables=["x"])
    v2 = repo.publish_version(code=code, name="v2", template_text="B {{y}}", variables=["y"])

    restored = repo.rollback(code, to_version=1)
    assert restored.id == v1.id
    assert restored.status == PromptStatus.PUBLISHED

    reloaded_v2 = repo.get_by_code(code, version=2)
    assert reloaded_v2 is not None
    assert reloaded_v2.id == v2.id
    assert reloaded_v2.status == PromptStatus.RETIRED  # demoted, not deleted
    assert reloaded_v2.template_text == "B {{y}}"       # content still intact

    assert repo.get_active(code).version == 1


def test_rollback_raises_for_a_version_that_does_not_exist(db_session: Session) -> None:
    repo = PromptTemplateRepository(db_session)
    code = _code()
    repo.publish_version(code=code, name="v1", template_text="A", variables=[])
    with pytest.raises(KnowledgeNotFoundError):
        repo.rollback(code, to_version=99)


def test_rollback_to_the_already_active_version_is_a_harmless_no_op(db_session: Session) -> None:
    repo = PromptTemplateRepository(db_session)
    code = _code()
    v1 = repo.publish_version(code=code, name="v1", template_text="A", variables=[])
    restored = repo.rollback(code, to_version=1)
    assert restored.id == v1.id
    assert restored.status == PromptStatus.PUBLISHED


# ---------------------------------------------------------------------------
# 4. PromptRegistry — audited service facade
# ---------------------------------------------------------------------------


def test_publish_validates_variables_against_the_template_text(
    db_session: Session, audit_service, admin_actor: Actor,
) -> None:
    registry = PromptRegistry(db_session, audit_service=audit_service)
    with pytest.raises(PromptRenderError):
        registry.publish(
            _code(), name="Bad", template_text="Hi {{name}}",
            variables=["name", "unused"],  # "unused" is not in the template text
            actor=admin_actor,
        )


def test_publish_and_render_round_trip(
    db_session: Session, audit_service, admin_actor: Actor,
) -> None:
    registry = PromptRegistry(db_session, audit_service=audit_service)
    code = _code()
    registry.publish(
        code, name="Greeting", template_text="Hello {{name}}, welcome to {{place}}.",
        variables=["name", "place"], actor=admin_actor,
    )
    rendered = registry.render(code, name="Ada", place="Expenseflow")
    assert rendered == "Hello Ada, welcome to Expenseflow."


def test_get_active_raises_for_an_unknown_code(db_session: Session, audit_service) -> None:
    registry = PromptRegistry(db_session, audit_service=audit_service)
    with pytest.raises(KnowledgeNotFoundError):
        registry.get_active(_code())


def test_publish_writes_an_audit_record(
    db_session: Session, repositories: dict, audit_service, admin_actor: Actor,
) -> None:
    registry = PromptRegistry(db_session, audit_service=audit_service)
    code = _code()
    registry.publish(
        code, name="Audited", template_text="Hi {{x}}", variables=["x"], actor=admin_actor,
    )
    logs = repositories["audit"].list_for_entity(entity_type="PromptTemplate", entity_id=code)
    assert any(log.action == "AI_PROMPT_PUBLISHED" for log in logs)


def test_rollback_writes_an_audit_record(
    db_session: Session, repositories: dict, audit_service, admin_actor: Actor,
) -> None:
    registry = PromptRegistry(db_session, audit_service=audit_service)
    code = _code()
    registry.publish(code, name="v1", template_text="A", variables=[], actor=admin_actor)
    registry.publish(code, name="v2", template_text="B", variables=[], actor=admin_actor)
    registry.rollback(code, to_version=1, actor=admin_actor)

    logs = repositories["audit"].list_for_entity(entity_type="PromptTemplate", entity_id=code)
    assert any(log.action == "AI_PROMPT_ROLLED_BACK" for log in logs)


# ---------------------------------------------------------------------------
# 5. Built-in prompts
# ---------------------------------------------------------------------------


def test_builtin_prompts_have_declared_variables_matching_their_text() -> None:
    for prompt in BUILTIN_PROMPTS:
        validate_declared_variables(prompt.code, prompt.template_text, prompt.variables)


def test_register_builtin_prompts_publishes_every_one(
    db_session: Session, audit_service, admin_actor: Actor,
) -> None:
    registry = PromptRegistry(db_session, audit_service=audit_service)
    published = register_builtin_prompts(registry, actor=admin_actor)
    assert len(published) == len(BUILTIN_PROMPTS)
    for prompt, builtin in zip(published, BUILTIN_PROMPTS, strict=True):
        assert prompt.code == builtin.code
        assert prompt.status == PromptStatus.PUBLISHED
