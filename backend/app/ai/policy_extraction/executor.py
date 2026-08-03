"""Runs the extractor over every section, and decides what happens when one of them fails.

**Failure isolation is the point of this layer.** With the whole document in one call, any
error lost everything. Section by section, a provider timeout on section 7 of 12 costs section 7
— not the eleven that already succeeded. So every call is wrapped, a failure becomes a recorded
outcome rather than a raised exception, and the run continues.

That choice is not free, and it is made deliberately: a proposal can now be *incomplete*. The
mitigation is that incompleteness is never silent — the failed section is persisted with status
``FAILED`` and its error, the count of failures is surfaced in the proposal's review notes, and a
reviewer approving a proposal with failed sections can see exactly which part of the document was
never read. An incomplete-but-labelled proposal is strictly more useful than no proposal; an
incomplete-and-unlabelled one would be worse than either.

**Why a protocol for a single sequential implementation.** Section calls are independent by
construction — no section's prompt depends on another's result — so the only thing standing between
this and parallel execution is where the calls are issued from. Behind an interface, a thread pool,
an async gather, or a queue of distributed workers later becomes one new class with the orchestrator
untouched. Parallelism is not implemented now: it needs rate-limit handling and a concurrency budget
that only matter at a document size nobody has run yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, Sequence

from app.ai.core.errors import ProviderError, ProviderNotConfiguredError, ProviderTimeoutError
from app.ai.policy_extraction.extractor import (
    ExtractionContext,
    PolicyExtractor,
    SectionExtractionResult,
)
from app.ai.policy_extraction.sectioning import DetectedSection
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SectionExtractionOutcome:
    """What happened to one section: its result, or why there isn't one."""

    section: DetectedSection
    result: Optional[SectionExtractionResult] = None
    error: Optional[str] = None
    attempts: int = 1

    @property
    def ok(self) -> bool:
        return self.result is not None


class SectionExtractionExecutor(Protocol):
    """Runs ``extractor`` over ``sections``, returning one outcome per section.

    Implementations must return outcomes in section order regardless of the order calls completed
    in, and must never raise for a per-section failure — that is what the ``error`` field is for.
    """

    def run(
        self,
        sections: Sequence[DetectedSection],
        *,
        extractor: PolicyExtractor,
        context: ExtractionContext,
    ) -> list[SectionExtractionOutcome]: ...


class SequentialSectionExecutor:
    """One section at a time, in document order. The only implementation today."""

    def run(
        self,
        sections: Sequence[DetectedSection],
        *,
        extractor: PolicyExtractor,
        context: ExtractionContext,
    ) -> list[SectionExtractionOutcome]:
        outcomes: list[SectionExtractionOutcome] = []
        for section in sections:
            outcomes.append(_run_one(section, extractor=extractor, context=context))
        return outcomes


def _run_one(
    section: DetectedSection, *, extractor: PolicyExtractor, context: ExtractionContext
) -> SectionExtractionOutcome:
    try:
        result = extractor.extract_section(section, context=context)
    except (ProviderError, ProviderTimeoutError, ProviderNotConfiguredError) as exc:
        return _failed(section, exc)
    except Exception as exc:  # noqa: BLE001 - see module docstring: isolation is the whole point
        # Broad on purpose. A provider SDK can raise anything, and an unanticipated exception type
        # taking down eleven healthy sections is exactly the outcome this layer exists to prevent.
        return _failed(section, exc)
    return SectionExtractionOutcome(section=section, result=result)


def _failed(section: DetectedSection, exc: BaseException) -> SectionExtractionOutcome:
    # The message is the exception type and text only — never the prompt, which carries the policy
    # document's contents and would leak into logs and the proposal's review notes.
    message = f"{type(exc).__name__}: {exc}"
    logger.warning(
        "ai.policy_extraction.section_failed",
        extra={
            "sectionIndex": section.index,
            "sectionTitle": section.title,
            "pages": section.page_range,
            "error": message,
        },
    )
    return SectionExtractionOutcome(section=section, error=message)


__all__ = [
    "SectionExtractionExecutor",
    "SectionExtractionOutcome",
    "SequentialSectionExecutor",
]
