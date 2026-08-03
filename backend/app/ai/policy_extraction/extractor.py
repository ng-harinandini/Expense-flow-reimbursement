"""The boundary between "extract rules from this section" and "which model does it".

Everything upstream of this module — the orchestrator, the executor, the merge and validation
stages — depends on :class:`PolicyExtractor` and never on an ``LLMProvider``, a model id, or a
prompt. That is the whole reason the protocol exists. Swapping Claude for another model, or for a
document-understanding service that is not a chat model at all (Textract, Document AI), becomes a
new implementation of one method rather than a change threaded through the pipeline.

The protocol is deliberately narrow. It takes a section and returns that section's raw rule payload
plus the provenance needed to reproduce the call; it does not normalize, merge, validate, or persist
anything. Those are deterministic stages that must behave identically no matter which extractor ran,
so none of them may live behind a provider-specific implementation.

:class:`LLMPolicyExtractor` is the only implementation today. It also carries the subsection advisor
used when a section is too large to send whole, because that is the pipeline's only other model call
and keeping both here means every LLM-touching line in extraction sits in one file.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping, Optional, Protocol, Sequence

from app.ai.core.config import ai_settings
from app.ai.interfaces.llm import LLMProvider
from app.ai.policy_extraction.schema import POLICY_RULE_EXTRACTION_SCHEMA
from app.ai.policy_extraction.sectioning import DetectedSection, ProposedSubsection
from app.ai.prompts.builtin.policy_rule_extraction import POLICY_RULE_EXTRACTION_PROMPT
from app.ai.prompts.builtin.policy_section_boundary import (
    POLICY_SECTION_BOUNDARY_PROMPT,
    POLICY_SECTION_BOUNDARY_SCHEMA,
)
from app.ai.prompts.rendering import render

#: Bumped by hand when ``POLICY_RULE_EXTRACTION_SCHEMA`` changes shape. Stored beside every
#: section's result: a payload validated against v1 cannot be assumed comparable to one validated
#: against v2, and without the version there is no way to tell which a stored run used.
EXTRACTION_SCHEMA_VERSION = "v1"


@dataclass(frozen=True, slots=True)
class ExtractionContext:
    """Document-level facts every section's prompt needs, resolved once per run."""

    document_title: str
    document_currency: str
    known_categories: tuple[str, ...]
    section_count: int


@dataclass(frozen=True, slots=True)
class ExtractionMetrics:
    """What one section's model call cost and how to reproduce it.

    ``prompt_hash`` covers the **rendered** prompt, not the template: two runs against the same
    template but a re-ingested document produce different text, and only the hash of what was
    actually sent identifies the call. Together with the model id and schema version it is enough to
    replay a run exactly, which is what makes a disputed extracted limit investigable months later.
    """

    provider: str = ""
    model: str = ""
    prompt_version: Optional[str] = None
    prompt_hash: Optional[str] = None
    schema_version: str = EXTRACTION_SCHEMA_VERSION
    reasoning_effort: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    cost_usd: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class SectionExtractionResult:
    """One section's raw, schema-valid payload — not yet normalized."""

    section_index: int
    payload: Mapping[str, Any] = field(default_factory=dict)
    metrics: ExtractionMetrics = field(default_factory=ExtractionMetrics)

    @property
    def raw_rules(self) -> Sequence[Mapping[str, Any]]:
        rules = self.payload.get("rules")
        return rules if isinstance(rules, list) else []


class PolicyExtractor(Protocol):
    """Extracts one section's rules. The only place that knows which model is in use."""

    def extract_section(
        self, section: DetectedSection, *, context: ExtractionContext
    ) -> SectionExtractionResult: ...


class LLMPolicyExtractor:
    """Extracts a section by prompting an :class:`LLMProvider` for schema-constrained JSON.

    Errors are allowed to propagate. Deciding whether one section's failure should abort the run or
    be recorded and stepped over is the executor's call, not the extractor's — an extractor that
    swallowed its own failures would make the section's ``FAILED`` status unreachable.
    """

    def __init__(
        self,
        *,
        llm_provider: LLMProvider,
        max_output_tokens: Optional[int] = None,
    ) -> None:
        self._llm = llm_provider
        self._max_output_tokens = max_output_tokens or ai_settings.LLM_MAX_OUTPUT_TOKENS

    def extract_section(
        self, section: DetectedSection, *, context: ExtractionContext
    ) -> SectionExtractionResult:
        prompt = render(
            POLICY_RULE_EXTRACTION_PROMPT.code,
            POLICY_RULE_EXTRACTION_PROMPT.template_text,
            POLICY_RULE_EXTRACTION_PROMPT.variables,
            known_categories="\n".join(f"    - {name}" for name in context.known_categories),
            document_title=context.document_title,
            document_currency=context.document_currency,
            section_title=section.title,
            section_index=str(section.index + 1),
            section_count=str(context.section_count),
            start_page=str(section.start_page),
            end_page=str(section.end_page),
            previous_context=section.previous_context or "(none — this is the first section)",
            section_text=section.text,
        )

        started = time.perf_counter()
        response = self._llm.generate_structured(
            prompt,
            POLICY_RULE_EXTRACTION_SCHEMA,
            max_output_tokens=self._max_output_tokens,
            prompt_version=POLICY_RULE_EXTRACTION_PROMPT.code,
        )
        wall_ms = int((time.perf_counter() - started) * 1000)

        return SectionExtractionResult(
            section_index=section.index,
            payload=response.structured or {},
            metrics=ExtractionMetrics(
                provider=response.provider,
                model=response.model,
                prompt_version=response.prompt_version,
                prompt_hash=prompt_hash(prompt),
                schema_version=EXTRACTION_SCHEMA_VERSION,
                reasoning_effort=ai_settings.LLM_EFFORT,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                # The provider's own latency when it reports one; the wall clock otherwise, so this
                # column is never zero and never silently excludes retry or transport time.
                latency_ms=response.latency_ms or wall_ms,
                cost_usd=Decimal(str(response.cost_usd or 0)),
            ),
        )


class LLMSubsectionAdvisor:
    """Asks the model where an oversized section divides. Advisory only — see the prompt module.

    Deliberately not part of :class:`PolicyExtractor`: an extractor that cannot answer this question
    (a Textract-style service, say) is still a perfectly good extractor, and the splitter already
    treats a missing advisor as normal by falling back to a mechanical split.
    """

    def __init__(self, *, llm_provider: LLMProvider, max_subsections: int = 12) -> None:
        self._llm = llm_provider
        self._max_subsections = max_subsections

    def propose(self, section: DetectedSection) -> list[ProposedSubsection]:
        prompt = render(
            POLICY_SECTION_BOUNDARY_PROMPT.code,
            POLICY_SECTION_BOUNDARY_PROMPT.template_text,
            POLICY_SECTION_BOUNDARY_PROMPT.variables,
            section_title=section.title,
            start_page=str(section.start_page),
            end_page=str(section.end_page),
            max_subsections=str(self._max_subsections),
            section_text=section.text,
        )
        response = self._llm.generate_structured(
            prompt,
            POLICY_SECTION_BOUNDARY_SCHEMA,
            prompt_version=POLICY_SECTION_BOUNDARY_PROMPT.code,
        )
        raw = (response.structured or {}).get("subsections") or []
        proposals = [
            ProposedSubsection(
                title=str(entry.get("title") or "").strip() or "Untitled",
                first_line=str(entry.get("firstLine") or "").strip(),
            )
            for entry in raw
            if isinstance(entry, Mapping) and str(entry.get("firstLine") or "").strip()
        ]
        return proposals[: self._max_subsections]


def prompt_hash(rendered_prompt: str) -> str:
    """SHA-256 of the exact text sent to the model, hex-encoded."""
    return hashlib.sha256(rendered_prompt.encode("utf-8")).hexdigest()


__all__ = [
    "EXTRACTION_SCHEMA_VERSION",
    "ExtractionContext",
    "ExtractionMetrics",
    "LLMPolicyExtractor",
    "LLMSubsectionAdvisor",
    "PolicyExtractor",
    "SectionExtractionResult",
    "prompt_hash",
]
