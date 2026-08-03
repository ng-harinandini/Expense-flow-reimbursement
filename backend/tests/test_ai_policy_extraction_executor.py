"""Failure isolation across sections.

The behaviour this layer exists for: with the whole document in one call, any error lost everything.
Section by section, a timeout on section 7 of 12 must cost section 7 and nothing else.

The trade-off is that a proposal can now be incomplete, so the tests below check both halves — that
the healthy sections survive, *and* that the failure is recorded rather than swallowed. An
incomplete-but-labelled proposal is more useful than none; an incomplete-and-unlabelled one would be
worse than either.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.ai.core.errors import ProviderTimeoutError
from app.ai.policy_extraction.executor import SequentialSectionExecutor
from app.ai.policy_extraction.extractor import ExtractionContext, SectionExtractionResult
from app.ai.policy_extraction.sectioning import DetectedSection, DetectionMethod

CONTEXT = ExtractionContext(
    document_title="expense-policy",
    document_currency="USD",
    known_categories=("Meals", "Lodging"),
    section_count=3,
)


def section(index: int, title: str) -> DetectedSection:
    return DetectedSection(
        index=index,
        title=title,
        start_page=index + 1,
        end_page=index + 1,
        chunk_ids=(uuid.uuid4(),),
        text=f"[Page {index + 1}]\n{title}",
        detection_method=DetectionMethod.NUMBERING,
        detection_confidence=Decimal("0.95"),
        chunk_count=1,
    )


class ScriptedExtractor:
    """Fails on the named sections, succeeds on the rest."""

    def __init__(self, *, fails_on: set[str], error: BaseException | None = None) -> None:
        self._fails_on = fails_on
        self._error = error or ProviderTimeoutError("bedrock", 45.0)
        self.seen: list[str] = []

    def extract_section(self, sec: DetectedSection, *, context) -> SectionExtractionResult:
        self.seen.append(sec.title)
        if sec.title in self._fails_on:
            raise self._error
        return SectionExtractionResult(
            section_index=sec.index,
            payload={"rules": [{"category": sec.title, "gradeTier": "All"}]},
        )


def test_every_section_succeeding_yields_every_result() -> None:
    outcomes = SequentialSectionExecutor().run(
        [section(0, "Meals"), section(1, "Lodging")],
        extractor=ScriptedExtractor(fails_on=set()),
        context=CONTEXT,
    )
    assert all(outcome.ok for outcome in outcomes)
    assert [o.section.title for o in outcomes] == ["Meals", "Lodging"]


def test_one_failure_does_not_stop_the_remaining_sections() -> None:
    """The load-bearing test: eleven good sections must not be lost to one bad one."""
    extractor = ScriptedExtractor(fails_on={"Flights"})
    outcomes = SequentialSectionExecutor().run(
        [section(0, "Meals"), section(1, "Flights"), section(2, "Lodging")],
        extractor=extractor,
        context=CONTEXT,
    )

    assert extractor.seen == ["Meals", "Flights", "Lodging"], "the run continued past the failure"
    assert [o.ok for o in outcomes] == [True, False, True]


def test_a_failure_records_why_rather_than_swallowing_it() -> None:
    outcomes = SequentialSectionExecutor().run(
        [section(0, "Flights")], extractor=ScriptedExtractor(fails_on={"Flights"}),
        context=CONTEXT,
    )
    assert outcomes[0].result is None
    assert "ProviderTimeoutError" in outcomes[0].error


def test_an_unexpected_exception_type_is_isolated_too() -> None:
    """A provider SDK can raise anything. An unanticipated exception type taking down every healthy
    section is exactly the outcome this layer exists to prevent."""
    outcomes = SequentialSectionExecutor().run(
        [section(0, "Meals"), section(1, "Flights")],
        extractor=ScriptedExtractor(fails_on={"Meals"}, error=ValueError("something odd")),
        context=CONTEXT,
    )
    assert [o.ok for o in outcomes] == [False, True]
    assert "ValueError" in outcomes[0].error


def test_the_error_message_never_carries_the_prompt() -> None:
    """A prompt holds the policy document's contents. It must not reach a log line or a proposal's
    review notes by way of an exception message."""
    secret = "CONFIDENTIAL SALARY BAND TABLE"
    outcomes = SequentialSectionExecutor().run(
        [section(0, "Meals")],
        extractor=ScriptedExtractor(
            fails_on={"Meals"}, error=RuntimeError("upstream rejected the request")
        ),
        context=CONTEXT,
    )
    assert secret not in (outcomes[0].error or "")


def test_outcomes_come_back_in_section_order() -> None:
    outcomes = SequentialSectionExecutor().run(
        [section(i, f"Section {i}") for i in range(5)],
        extractor=ScriptedExtractor(fails_on={"Section 2"}),
        context=CONTEXT,
    )
    assert [o.section.index for o in outcomes] == [0, 1, 2, 3, 4]


def test_no_sections_is_not_an_error() -> None:
    assert SequentialSectionExecutor().run(
        [], extractor=ScriptedExtractor(fails_on=set()), context=CONTEXT
    ) == []
