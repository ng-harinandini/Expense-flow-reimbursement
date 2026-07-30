"""A small test harness for a registered prompt: hand-authored cases run against its template.

Distinct from ``tests/test_ai_prompts.py`` (this repo's own pytest suite covering the registry's
code) — this module is the *platform capability* Task 11 asks for: a way to attach a suite of
example inputs and expected-substring assertions to a prompt, so an editor changing a published
template's wording can prove the new version still produces output containing what callers expect,
without needing a live LLM to check it against.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from app.ai.core.errors import PromptRenderError
from app.ai.prompts.rendering import render


@dataclass(frozen=True, slots=True)
class PromptTestCase:
    """One example: variable values, and what the rendered text must (or must not) do."""

    name: str
    values: dict[str, object] = field(default_factory=dict)
    expected_contains: tuple[str, ...] = ()
    expect_render_error: bool = False


@dataclass(frozen=True, slots=True)
class PromptTestResult:
    case_name: str
    passed: bool
    detail: str = ""


def run_prompt_tests(
    code: str, template_text: str, declared_variables: list[str], cases: Sequence[PromptTestCase]
) -> list[PromptTestResult]:
    """Run every case against ``template_text``, returning one result per case."""
    results: list[PromptTestResult] = []
    for case in cases:
        try:
            rendered = render(code, template_text, declared_variables, **case.values)
        except PromptRenderError as exc:
            if case.expect_render_error:
                results.append(PromptTestResult(case.name, True))
            else:
                results.append(
                    PromptTestResult(case.name, False, f"unexpected render error: {exc}")
                )
            continue

        if case.expect_render_error:
            results.append(
                PromptTestResult(case.name, False, "expected a render error, none was raised")
            )
            continue

        missing = [needle for needle in case.expected_contains if needle not in rendered]
        if missing:
            results.append(
                PromptTestResult(case.name, False, f"rendered text is missing: {missing}")
            )
        else:
            results.append(PromptTestResult(case.name, True))
    return results


__all__ = ["PromptTestCase", "PromptTestResult", "run_prompt_tests"]
