"""The extraction pipeline end to end: real chunks, a fake extractor, real Postgres.

The document and chunks are constructed directly rather than through ``ingest_document`` with
``InlineSource`` — inline text carries no page numbers, and page attribution is exactly the thing
under test. Simulating what ``PdfParser`` + ``HybridChunker`` actually produce (one chunk per page,
``page_number`` set, each page opening with a numbered heading) exercises the real detection,
execution, merge and persistence path without needing a PDF or a provider.

The extractor is faked at the :class:`PolicyExtractor` boundary rather than the LLM boundary. That
is the interface the orchestrator actually depends on, so faking it here also checks the boundary is
real: if anything upstream reached past it to a model or a prompt, these tests could not run at all.

What the pipeline is expected to do to the fixture below:

    page 1  4.1 Meals               -> one Meals rule
    page 2  4.3 Travel - flights    -> no rules (a class, not an amount)
    page 3  4.4 Lodging             -> two grade-band rules
    page 4  4.6 Communications      -> one rule outside the closed category set, flagged
"""

from __future__ import annotations

import uuid
from typing import Any, Iterator, Mapping, Optional, Sequence

import pytest
from sqlalchemy.orm import Session

from app.ai.core.enums import DocumentStatus, KnowledgeSourceType, ProposalStatus
from app.ai.core.errors import ProviderTimeoutError
from app.ai.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.ai.policy_extraction.extractor import (
    ExtractionContext,
    ExtractionMetrics,
    SectionExtractionResult,
)
from app.ai.policy_extraction.orchestrator import PolicyExtractionOrchestrator
from app.ai.policy_extraction.sectioning import DetectedSection, SectionStatus
from app.ai.policy_extraction.service import PolicyRuleExtractionService
from app.ai.repositories.knowledge_repository import KnowledgeChunkRepository
from app.ai.repositories.policy_proposal_repository import (
    PolicyDocumentPageRepository,
    PolicyDocumentSectionRepository,
    PolicyRuleProposalRepository,
)

TENANT_ID = "default"
KNOWN_CATEGORIES = (
    "Meals", "Ground Transport", "Flights", "Lodging", "Client Entertainment",
)

PAGES = [
    "4.1 Meals\nGrade Max amount Auto-approve limit Receipt required above\n"
    "All $40 / day $25 $25",
    "4.3 Travel - flights\nGrade Class Advance booking\n"
    "L1-L4 Economy Book >=7 days in advance",
    "4.4 Lodging\nGrade Max amount Auto-approve limit Receipt required above\n"
    "L1-L3 $120 / night - (always manual) $0\nL4+ $250 / night - (always manual) $0",
    "4.6 Communications & connectivity\n"
    "| Category | Grade | Max amount | Auto-approve limit | Receipt required above | "
    "|---|---|---|---|---| | Mobile phone reimbursement | All | $50 / month | $50 | $0 |",
]

_MEALS_RULE: dict[str, Any] = {
    "category": "Meals",
    "gradeTier": "All",
    "maxAmountUSD": 40.0,
    "autoApproveLimitUSD": 25.0,
    "receiptRequiredAboveUSD": 25.0,
    "basis": "PER_DAY",
    "pageNumbers": [1],
    "sourceQuote": "All $40 / day $25 $25",
    "confidence": 0.95,
    "representable": True,
}

# Both grade bands cite page 3, where their data rows are — not page 2, where the preceding
# section ends. Cross-section citation accuracy is what the previous_context block must not spoil.
_LODGING_RULES: list[dict[str, Any]] = [
    {
        "category": "Lodging", "gradeTier": "L1-L3", "maxAmountUSD": 120.0,
        "alwaysManual": True, "receiptRequiredAboveUSD": 0.0, "basis": "PER_NIGHT",
        "pageNumbers": [3], "sourceQuote": "L1-L3 $120 / night", "confidence": 0.9,
        "representable": True,
    },
    {
        "category": "Lodging", "gradeTier": "L4+", "maxAmountUSD": 250.0,
        "alwaysManual": True, "receiptRequiredAboveUSD": 0.0, "basis": "PER_NIGHT",
        "pageNumbers": [3], "sourceQuote": "L4+ $250 / night", "confidence": 0.9,
        "representable": True,
    },
]

_COMMS_RULE: dict[str, Any] = {
    "category": "Communications & connectivity",
    "gradeTier": "All",
    "maxAmountUSD": 50.0,
    "autoApproveLimitUSD": 50.0,
    "basis": "PER_MONTH",
    "pageNumbers": [4],
    "sourceQuote": "Mobile phone reimbursement $50 / month",
    "confidence": 0.6,
    "representable": False,
    "unrepresentableReason": "No matching expense category.",
}

#: Keyed by a distinctive fragment of the section title, so a fixture change that alters how the
#: document divides fails loudly rather than silently returning the wrong section's rules.
BY_SECTION: dict[str, dict[str, Any]] = {
    "Meals": {"rules": [_MEALS_RULE], "reviewNotes": None, "documentEffectiveDate": None},
    "flights": {"rules": [], "reviewNotes": None, "documentEffectiveDate": None},
    "Lodging": {"rules": _LODGING_RULES, "reviewNotes": None, "documentEffectiveDate": None},
    "Communications": {
        "rules": [_COMMS_RULE],
        "reviewNotes": "Effective date on the document is a placeholder.",
        "documentEffectiveDate": None,
    },
}


class FakeExtractor:
    """Returns a canned payload per section, matched on the section's title."""

    def __init__(
        self,
        by_section: Mapping[str, Mapping[str, Any]] = BY_SECTION,
        *,
        fails_on: Sequence[str] = (),
    ) -> None:
        self._by_section = dict(by_section)
        self._fails_on = tuple(fails_on)
        self.seen: list[str] = []

    def extract_section(
        self, section: DetectedSection, *, context: ExtractionContext
    ) -> SectionExtractionResult:
        self.seen.append(section.title)
        if any(fragment in section.title for fragment in self._fails_on):
            raise ProviderTimeoutError("fake", 45.0)
        payload = next(
            (body for key, body in self._by_section.items() if key in section.title),
            {"rules": []},
        )
        return SectionExtractionResult(
            section_index=section.index,
            payload=payload,
            metrics=ExtractionMetrics(
                provider="fake", model="fake-model", prompt_version="POLICY_RULE_EXTRACTION",
                prompt_hash="a" * 64, reasoning_effort="high",
                input_tokens=500, output_tokens=200, latency_ms=42,
            ),
        )


def _make_document_with_pages(
    session: Session, *, pages: Sequence[str], title: str = "expense-policy",
) -> KnowledgeDocument:
    """One HYBRID-style chunk per page with ``page_number`` set — the shape a real PDF produces."""
    document = KnowledgeDocument(
        tenant_id=TENANT_ID,
        source_type=KnowledgeSourceType.FINANCE_POLICY.value,
        title=title,
        checksum_sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        status=DocumentStatus.INDEXED,
        page_count=len(pages),
        currency="USD",
        version=1,
    )
    session.add(document)
    session.flush()
    for index, text in enumerate(pages, start=1):
        session.add(
            KnowledgeChunk(
                tenant_id=TENANT_ID, document_id=document.id, chunk_index=index - 1,
                content=text, page_number=index, strategy="HYBRID",
            )
        )
    session.flush()
    return document


def _build(db_session: Session, extractor: Any) -> PolicyRuleExtractionService:
    return PolicyRuleExtractionService(
        orchestrator=PolicyExtractionOrchestrator(
            chunk_repository=KnowledgeChunkRepository(db_session),
            proposal_repository=PolicyRuleProposalRepository(db_session),
            page_repository=PolicyDocumentPageRepository(db_session),
            section_repository=PolicyDocumentSectionRepository(db_session),
            extractor=extractor,
        )
    )


@pytest.fixture
def policy_document(db_session: Session) -> KnowledgeDocument:
    return _make_document_with_pages(db_session, pages=PAGES)


@pytest.fixture
def service(db_session: Session) -> Iterator[PolicyRuleExtractionService]:
    yield _build(db_session, FakeExtractor())


def _extract(
    service: PolicyRuleExtractionService,
    document: KnowledgeDocument,
    *,
    actor_sub: Optional[str] = None,
):
    return service.extract(
        document, tenant_id=TENANT_ID, actor_sub=actor_sub, known_categories=KNOWN_CATEGORIES,
    )


# --- one call per section --------------------------------------------------------


def test_each_section_is_extracted_independently(
    policy_document: KnowledgeDocument, db_session: Session,
) -> None:
    """The core change: four numbered sections mean four calls, not one prompt with the lot."""
    extractor = FakeExtractor()
    _extract(_build(db_session, extractor), policy_document)
    assert extractor.seen == [
        "4.1 Meals", "4.3 Travel - flights", "4.4 Lodging", "4.6 Communications & connectivity",
    ]


def test_sections_are_persisted_with_how_they_were_detected(
    db_session: Session, policy_document: KnowledgeDocument,
    service: PolicyRuleExtractionService,
) -> None:
    _extract(service, policy_document)
    sections = PolicyDocumentSectionRepository(db_session).list_for_document(
        policy_document.id, tenant_id=TENANT_ID
    )
    assert [s.section_index for s in sections] == [0, 1, 2, 3]
    assert all(s.detection_method == "NUMBERING" for s in sections)
    assert all(s.status == SectionStatus.EXTRACTED.value for s in sections)


def test_section_rows_carry_the_provenance_needed_to_replay_the_call(
    db_session: Session, policy_document: KnowledgeDocument,
    service: PolicyRuleExtractionService,
) -> None:
    """Model, prompt hash and schema version per section: an extracted limit is only auditable if
    you can say which model read which text under which prompt."""
    _extract(service, policy_document)
    section = PolicyDocumentSectionRepository(db_session).list_for_document(
        policy_document.id, tenant_id=TENANT_ID
    )[0]
    assert section.llm_model == "fake-model"
    assert section.prompt_hash and section.schema_version
    assert section.input_tokens == 500


def test_a_section_that_states_no_rules_is_a_success_not_a_failure(
    db_session: Session, policy_document: KnowledgeDocument,
    service: PolicyRuleExtractionService,
) -> None:
    """The flights section states a cabin class, not an amount. Zero rules is a correct answer."""
    _extract(service, policy_document)
    flights = next(
        s for s in PolicyDocumentSectionRepository(db_session).list_for_document(
            policy_document.id, tenant_id=TENANT_ID
        )
        if "flights" in s.title
    )
    assert flights.status == SectionStatus.EXTRACTED.value
    assert flights.rules_extracted_count == 0


# --- the proposal ----------------------------------------------------------------


def test_extract_writes_a_draft_proposal_with_every_item(
    db_session: Session, policy_document: KnowledgeDocument,
    service: PolicyRuleExtractionService,
) -> None:
    proposal = _extract(service, policy_document, actor_sub="sub-finance")
    db_session.flush()

    assert proposal.status is ProposalStatus.DRAFT
    assert proposal.rules_extracted == 4
    assert proposal.rules_representable == 3  # Communications is flagged unrepresentable
    assert proposal.created_by_sub == "sub-finance"
    assert proposal.sections_total == 4
    assert proposal.sections_failed == 0


def test_run_level_cost_sums_every_section(
    db_session: Session, policy_document: KnowledgeDocument,
    service: PolicyRuleExtractionService,
) -> None:
    proposal = _extract(service, policy_document)
    assert proposal.input_tokens == 4 * 500
    assert proposal.output_tokens == 4 * 200


def test_meals_rule_cites_page_one(
    db_session: Session, policy_document: KnowledgeDocument,
    service: PolicyRuleExtractionService,
) -> None:
    proposal = _extract(service, policy_document)
    meals = next(i for i in proposal.items if i.category == "Meals")
    assert meals.page_numbers == [1]
    assert meals.max_amount == 40


def test_lodging_bands_survive_as_separate_rows_citing_their_own_page(
    db_session: Session, policy_document: KnowledgeDocument,
    service: PolicyRuleExtractionService,
) -> None:
    """Two grade bands are two rules, not one — and both cite page 3, where their data rows are,
    not page 2 where the preceding section ends."""
    proposal = _extract(service, policy_document)
    lodging = [i for i in proposal.items if i.category == "Lodging"]
    assert len(lodging) == 2
    assert all(i.page_numbers == [3] for i in lodging)
    assert {i.grade_tier for i in lodging} == {"L1-L3", "L4+"}
    assert all(i.always_manual and i.auto_approve_limit is None for i in lodging)


def test_unrepresentable_item_is_kept_and_flagged_not_dropped(
    db_session: Session, policy_document: KnowledgeDocument,
    service: PolicyRuleExtractionService,
) -> None:
    proposal = _extract(service, policy_document)
    comms = next(i for i in proposal.items if "Communications" in i.category)
    assert comms.representable is False
    assert comms.unrepresentable_reason


# --- traceability ----------------------------------------------------------------


def test_every_item_traces_back_to_the_section_and_chunks_it_came_from(
    db_session: Session, policy_document: KnowledgeDocument,
    service: PolicyRuleExtractionService,
) -> None:
    """item -> section -> chunks -> pages -> source text. Without the trail a reviewer challenging
    a limit has nothing to read but the whole document again."""
    proposal = _extract(service, policy_document)
    db_session.flush()

    sections = {
        s.id: s
        for s in PolicyDocumentSectionRepository(db_session).list_for_document(
            policy_document.id, tenant_id=TENANT_ID
        )
    }
    meals = next(i for i in proposal.items if i.category == "Meals")
    assert meals.section_id in sections
    assert sections[meals.section_id].title == "4.1 Meals"
    assert meals.source_chunk_ids


def test_every_item_carries_a_stable_rule_id(
    db_session: Session, policy_document: KnowledgeDocument,
    service: PolicyRuleExtractionService,
) -> None:
    proposal = _extract(service, policy_document)
    meals = next(i for i in proposal.items if i.category == "Meals")
    assert meals.rule_id == "RULE_MEALS_ALLSTAFF_PER_DAY"


def test_a_rule_with_a_contradiction_carries_its_validation_findings(
    db_session: Session, policy_document: KnowledgeDocument,
) -> None:
    """An auto-approve threshold above the cap would approve every in-policy claim automatically.
    The proposal is still written; the row says why to look at it."""
    broken = {
        **_MEALS_RULE, "maxAmountUSD": 40.0, "autoApproveLimitUSD": 100.0,
    }
    service = _build(
        db_session,
        FakeExtractor({"Meals": {"rules": [broken], "reviewNotes": None,
                                 "documentEffectiveDate": None}}),
    )
    proposal = _extract(service, policy_document)

    meals = next(i for i in proposal.items if i.category == "Meals")
    assert any(
        w["code"] == "AUTO_APPROVE_ABOVE_LIMIT" for w in (meals.validation_warnings or [])
    )


# --- failure isolation -----------------------------------------------------------


def test_one_failed_section_does_not_lose_the_others(
    db_session: Session, policy_document: KnowledgeDocument,
) -> None:
    """The reason for section-scoped calls: a timeout on Lodging must not cost Meals."""
    service = _build(db_session, FakeExtractor(fails_on=("Lodging",)))
    proposal = _extract(service, policy_document)
    db_session.flush()

    assert any(i.category == "Meals" for i in proposal.items)
    assert not any(i.category == "Lodging" for i in proposal.items)
    assert proposal.sections_failed == 1
    assert proposal.sections_total == 4


def test_a_failed_section_is_recorded_with_its_error(
    db_session: Session, policy_document: KnowledgeDocument,
) -> None:
    service = _build(db_session, FakeExtractor(fails_on=("Lodging",)))
    _extract(service, policy_document)

    failed = PolicyDocumentSectionRepository(db_session).list_failed(
        policy_document.id, tenant_id=TENANT_ID
    )
    assert [s.title for s in failed] == ["4.4 Lodging"]
    assert "ProviderTimeoutError" in failed[0].error_message


def test_an_incomplete_proposal_says_so_in_its_review_notes(
    db_session: Session, policy_document: KnowledgeDocument,
) -> None:
    """The single most important thing to know about a proposal, and the one thing reading its
    extracted rules would never reveal."""
    service = _build(db_session, FakeExtractor(fails_on=("Lodging",)))
    proposal = _extract(service, policy_document)

    assert "INCOMPLETE" in (proposal.review_notes or "")
    assert "4.4 Lodging" in proposal.review_notes


# --- conflicts -------------------------------------------------------------------


def test_two_sections_disagreeing_produce_a_conflict_and_keep_both_readings(
    db_session: Session, policy_document: KnowledgeDocument,
) -> None:
    """A summary table saying $40 and a detail section saying $50 must not silently resolve to
    either. Both rows survive and the disagreement is on the proposal."""
    service = _build(
        db_session,
        FakeExtractor(
            {
                "Meals": {"rules": [_MEALS_RULE], "reviewNotes": None,
                          "documentEffectiveDate": None},
                "Lodging": {
                    "rules": [{**_MEALS_RULE, "maxAmountUSD": 50.0, "pageNumbers": [3],
                               "sourceQuote": "Meals are capped at $50 per day"}],
                    "reviewNotes": None, "documentEffectiveDate": None,
                },
            }
        ),
    )
    proposal = _extract(service, policy_document)
    db_session.flush()

    assert len(proposal.conflicts) == 1
    assert proposal.conflicts[0]["category"] == "Meals"
    meals = [i for i in proposal.items if i.category == "Meals"]
    assert {float(i.max_amount) for i in meals} == {40.0, 50.0}
    assert "stated inconsistently" in (proposal.review_notes or "")


def test_agreeing_readings_across_sections_collapse_to_one_row(
    db_session: Session, policy_document: KnowledgeDocument,
) -> None:
    service = _build(
        db_session,
        FakeExtractor(
            {
                "Meals": {"rules": [_MEALS_RULE], "reviewNotes": None,
                          "documentEffectiveDate": None},
                "Lodging": {
                    "rules": [{**_MEALS_RULE, "pageNumbers": [3]}],
                    "reviewNotes": None, "documentEffectiveDate": None,
                },
            }
        ),
    )
    proposal = _extract(service, policy_document)

    meals = [i for i in proposal.items if i.category == "Meals"]
    assert len(meals) == 1
    assert meals[0].page_numbers == [1, 3], "the merged row cites both pages"
    assert proposal.conflicts is None


# --- re-extraction ---------------------------------------------------------------


def test_re_extraction_supersedes_the_prior_draft_rather_than_doubling_it(
    db_session: Session, policy_document: KnowledgeDocument,
    service: PolicyRuleExtractionService,
) -> None:
    first = _extract(service, policy_document)
    db_session.flush()
    second = _extract(service, policy_document)
    db_session.flush()

    repo = PolicyRuleProposalRepository(db_session)
    assert repo.get(first.id, tenant_id=TENANT_ID).status is ProposalStatus.SUPERSEDED
    assert second.status is ProposalStatus.DRAFT
    assert [p.id for p in repo.list_recent(tenant_id=TENANT_ID, status=ProposalStatus.DRAFT)] == [
        second.id
    ]


def test_re_extraction_replaces_section_rows_rather_than_duplicating(
    db_session: Session, policy_document: KnowledgeDocument,
    service: PolicyRuleExtractionService,
) -> None:
    _extract(service, policy_document)
    _extract(service, policy_document)
    sections = PolicyDocumentSectionRepository(db_session).list_for_document(
        policy_document.id, tenant_id=TENANT_ID
    )
    assert len(sections) == 4


# --- per-page metadata (unchanged by sectioning) ---------------------------------


def test_one_page_row_per_non_empty_page(
    db_session: Session, policy_document: KnowledgeDocument,
    service: PolicyRuleExtractionService,
) -> None:
    _extract(service, policy_document)
    pages = PolicyDocumentPageRepository(db_session).list_for_document(
        policy_document.id, tenant_id=TENANT_ID
    )
    assert [p.page_number for p in pages] == [1, 2, 3, 4]


def test_page_rows_record_how_many_rules_they_yielded(
    db_session: Session, policy_document: KnowledgeDocument,
    service: PolicyRuleExtractionService,
) -> None:
    _extract(service, policy_document)
    pages = {
        p.page_number: p
        for p in PolicyDocumentPageRepository(db_session).list_for_document(
            policy_document.id, tenant_id=TENANT_ID
        )
    }
    assert pages[1].rules_extracted_count == 1   # Meals
    assert pages[2].rules_extracted_count == 0   # flights: a class, not an amount
    assert pages[3].rules_extracted_count == 2   # both Lodging bands
    assert pages[4].rules_extracted_count == 1   # the flagged Communications rule


def test_malformed_inline_table_page_is_flagged_table_detected(
    db_session: Session, policy_document: KnowledgeDocument,
    service: PolicyRuleExtractionService,
) -> None:
    """Page 4's text is the malformed inline-pipe shape a PDF extractor can leave a table in."""
    _extract(service, policy_document)
    pages = {
        p.page_number: p
        for p in PolicyDocumentPageRepository(db_session).list_for_document(
            policy_document.id, tenant_id=TENANT_ID
        )
    }
    assert pages[4].table_detected is True


def test_re_extraction_replaces_page_rows_rather_than_duplicating(
    db_session: Session, policy_document: KnowledgeDocument,
    service: PolicyRuleExtractionService,
) -> None:
    _extract(service, policy_document)
    _extract(service, policy_document)
    assert PolicyDocumentPageRepository(db_session).count_for_document(
        policy_document.id, tenant_id=TENANT_ID
    ) == 4
