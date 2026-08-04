"""Prompt registry (Task 11): versioned, immutable, auditable prompt templates.

``PromptRegistry`` (``registry.py``) is the one public entry point — publish, roll back, retrieve,
render. ``rendering.py`` is the strict ``{{variable}}`` substitution engine it uses internally;
``testing.py`` is the example-case harness a caller runs against a template before publishing a
wording change; ``builtin/`` holds the platform's own pre-authored prompts.
"""

from app.ai.prompts.registry import PromptRegistry  # noqa: F401
from app.ai.prompts.testing import PromptTestCase, PromptTestResult, run_prompt_tests  # noqa: F401

__all__ = ["PromptRegistry", "PromptTestCase", "PromptTestResult", "run_prompt_tests"]
