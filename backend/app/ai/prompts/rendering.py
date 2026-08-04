"""Strict prompt-variable rendering: ``{{variable_name}}`` placeholders, no silent blanks.

Two distinct checks, deliberately kept separate:

* :func:`validate_declared_variables` — an **authoring-time** check, run once when a prompt is
  published (see ``app.ai.prompts.registry.PromptRegistry.publish``), that a template's declared
  ``variables`` list matches exactly what ``{{...}}`` placeholders the template text actually
  contains. Catches a stale variable list before it ever reaches a caller.
* :func:`render` — a **call-time** check that the caller supplied exactly the declared variables,
  no more, no fewer. A prompt that renders with a blank where a policy limit should be is worse
  than one that fails loudly (see :class:`~app.ai.core.errors.PromptRenderError`).
"""

from __future__ import annotations

import re
from typing import Mapping

from app.ai.core.errors import PromptRenderError

_PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
# Any `{{`/`}}` left over after every well-formed placeholder has been stripped out is malformed
# placeholder syntax (an empty `{{}}`, an identifier starting with a digit, a stray unmatched
# brace, ...) — this is always an authoring bug, never intentional content, so it is caught rather
# than silently left in the rendered text (see _check_no_malformed_placeholders).
_STRAY_BRACE_PATTERN = re.compile(r"\{\{|\}\}")


def extract_placeholders(template_text: str) -> frozenset[str]:
    """Every distinct ``{{variable_name}}`` placeholder actually present in the text."""
    return frozenset(_PLACEHOLDER_PATTERN.findall(template_text))


def _check_no_malformed_placeholders(code: str, template_text: str) -> None:
    """Raise if the text contains ``{{``/``}}`` that :data:`_PLACEHOLDER_PATTERN` did not
    recognize as a well-formed placeholder — e.g. ``{{}}`` or ``{{123abc}}``. Left unchecked, such
    text is invisible to :func:`extract_placeholders` and passes through ``render`` unsubstituted,
    leaking raw template syntax into output — exactly the "silent wrong output" this module exists
    to prevent.
    """
    residual = _PLACEHOLDER_PATTERN.sub("", template_text)
    if _STRAY_BRACE_PATTERN.search(residual):
        raise PromptRenderError(code, [], ["<malformed placeholder syntax>"])


def validate_declared_variables(
    code: str, template_text: str, declared_variables: list[str]
) -> None:
    """Raise unless ``declared_variables`` matches the template text's placeholders exactly, and
    the text contains no malformed ``{{...}}`` syntax that would silently fail to be recognized."""
    _check_no_malformed_placeholders(code, template_text)
    actual = extract_placeholders(template_text)
    declared = set(declared_variables)
    missing = sorted(actual - declared)   # in the text but not declared
    unexpected = sorted(declared - actual)  # declared but not in the text
    if missing or unexpected:
        raise PromptRenderError(code, missing, unexpected)


def render(code: str, template_text: str, declared_variables: list[str], **values: object) -> str:
    """Substitute every ``{{variable_name}}`` in ``template_text`` with ``values``.

    Raises :class:`~app.ai.core.errors.PromptRenderError` if a declared variable was not supplied,
    if an extra keyword was supplied that the template does not declare, or if the text contains
    malformed ``{{...}}`` syntax (defense in depth alongside ``validate_declared_variables``, for a
    template row that predates this check or was inserted outside the registry's own publish path).
    """
    _check_no_malformed_placeholders(code, template_text)
    declared = set(declared_variables)
    provided = set(values.keys())
    missing = sorted(declared - provided)
    unexpected = sorted(provided - declared)
    if missing or unexpected:
        raise PromptRenderError(code, missing, unexpected)
    return _substitute(template_text, values)


def _substitute(template_text: str, values: Mapping[str, object]) -> str:
    return _PLACEHOLDER_PATTERN.sub(lambda match: str(values[match.group(1)]), template_text)


__all__ = ["extract_placeholders", "render", "validate_declared_variables"]
