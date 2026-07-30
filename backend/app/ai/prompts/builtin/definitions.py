"""The value type every built-in prompt is expressed as."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BuiltinPrompt:
    code: str
    name: str
    description: str
    template_text: str
    variables: list[str]


__all__ = ["BuiltinPrompt"]
