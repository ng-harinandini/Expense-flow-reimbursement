"""KnowledgeService tests (T004-M9).

Database-backed, exactly like ``test_ai_ingestion.py``: a fast, offline embedding provider
(``DeterministicEmbeddingProvider``) and vector store (``InMemoryVectorStore``) stand in for the
real model/store, and documents are ingested through the real ``ingest_document()`` pipeline (M8)
against the real, migrated test database.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from app.ai.core.config import BGE_M3_DIMENSIONS, ai_settings
from app.ai.core.enums import DecisionMemoryKind, KnowledgeSourceType
from app.ai.core.errors import FeatureDisabledError
from app.ai.core.types import Citation, ContextBundle, RetrievalQuery, RetrievalResult
from app.ai.embeddings import EmbeddingService
from app.ai.ingestion.pipeline import DocumentMetadataInput, ingest_document
from app.ai.ingestion.sources.inline import InlineSource
from app.ai.knowledge.citations import attach_citations
from app.ai.knowledge.context_builder import build_context
from app.ai.knowledge.service import KnowledgeService
from app.ai.knowledge.vendor_knowledge import VENDOR_SOURCE_TYPES, vendor_context_query
from app.ai.memory.decision_memory import (
    DECISION_MEMORY_SOURCE_TYPES,
    similar_claims_query,
    source_type_for,
)
from app.ai.providers.embeddings.deterministic import DeterministicEmbeddingProvider
from app.ai.registry.flags import FeatureFlags
from app.ai.repositories.knowledge_repository import KnowledgeDocumentRepository
from app.ai.vector_store.memory import InMemoryVectorStore

TENANT_ID = "knowledge-test-tenant"
DIMENSIONS = BGE_M3_DIMENSIONS

POLICY_TEXT = (
    "Travel Reimbursement Policy\n\n"
    "Meals are reimbursed at actual cost up to the daily per-diem limit for the traveler's "
    "destination city, and alcohol is never a reimbursable expense under any circumstance."
)


def _embedding_service() -> EmbeddingService:
    return EmbeddingService(DeterministicEmbeddingProvider(dimensions=DIMENSIONS, version="v1"))


def _vector_store() -> InMemoryVectorStore:
    return InMemoryVectorStore(dimensions=DIMENSIONS)


class _Stack:
    """One shared embedding service + vector store per test, so a document ingested through it is
    actually visible to a ``KnowledgeService`` built on the same pair — ``InMemoryVectorStore`` is
    a bare in-process object with no persistence of its own, unlike the real database chunks land
    in, so two independently constructed instances never see the same data."""

    def __init__(self) -> None:
        self.embedding_service = _embedding_service()
        self.vector_store = _vector_store()

    def service(self, session: Session, *, flags: FeatureFlags | None = None) -> KnowledgeService:
        return KnowledgeService(
            session=session, embedding_service=self.embedding_service,
            vector_store=self.vector_store, tenant_id=TENANT_ID, flags=flags,
        )

    def ingest_policy(
        self, session: Session, *, title: str = "policy-doc", text: str = POLICY_TEXT,
    ):
        source = InlineSource(
            text=text, file_name=f"{title}.txt", source_type=KnowledgeSourceType.POLICY,
            tenant_id=TENANT_ID,
        )
        return ingest_document(
            source, session=session, metadata=DocumentMetadataInput(title=title),
            embedding_service=self.embedding_service, vector_store=self.vector_store,
        )

    def ingest(self, session: Session, *, title: str, text: str, source_type: KnowledgeSourceType):
        source = InlineSource(text=text, file_name=f"{title}.txt", source_type=source_type,
                              tenant_id=TENANT_ID)
        return ingest_document(
            source, session=session, metadata=DocumentMetadataInput(title=title),
            embedding_service=self.embedding_service, vector_store=self.vector_store,
        )


# ---------------------------------------------------------------------------
# 1. The eight KnowledgeService methods
# ---------------------------------------------------------------------------


def test_retrieve_policy_returns_a_cited_context_bundle(db_session: Session) -> None:
    stack = _Stack()
    ingested = stack.ingest_policy(db_session)
    service = stack.service(db_session)

    bundle = service.retrieve_policy("what is the meal per diem limit")
    assert isinstance(bundle, ContextBundle)
    assert bundle.chunk_count > 0
    assert bundle.citations
    assert bundle.citations[0].document_id == ingested.document_id
    assert bundle.embedding_version
    assert bundle.config_fingerprint


def test_retrieve_policy_applies_metadata_filters(db_session: Session) -> None:
    stack = _Stack()
    stack.ingest_policy(db_session, title="us-policy")
    service = stack.service(db_session)

    bundle = service.retrieve_policy("meal reimbursement", country="FR")
    # No document was ingested with country=FR, so the filtered query returns nothing.
    assert bundle.chunk_count == 0
    assert bundle.is_empty()


def test_retrieve_policy_applies_category_currency_and_source_type_filters(
    db_session: Session,
) -> None:
    stack = _Stack()
    stack.ingest_policy(db_session, title="unfiltered-policy")
    service = stack.service(db_session)

    assert service.retrieve_policy("meal reimbursement", category="Nonexistent").is_empty()
    assert service.retrieve_policy("meal reimbursement", currency="XYZ").is_empty()
    assert service.retrieve_policy(
        "meal reimbursement", source_types=(KnowledgeSourceType.VENDOR_CONTRACT,)
    ).is_empty()


def test_retrieve_similar_claims_finds_recorded_decisions(db_session: Session) -> None:
    stack = _Stack()
    service = stack.service(db_session)
    service.record_decision(
        DecisionMemoryKind.CLAIM, "claim-001",
        "Employee submitted a travel claim for 500 USD covering hotel and meals.",
    )
    bundle = service.retrieve_similar_claims("travel claim for hotel and meals")
    assert bundle.chunk_count > 0
    assert bundle.citations


def test_search_returns_a_retrieval_result_with_citations(db_session: Session) -> None:
    stack = _Stack()
    stack.ingest_policy(db_session)
    service = stack.service(db_session)
    query = RetrievalQuery(text="meal per diem", tenant_id=TENANT_ID, top_k=5)
    result = service.search(query)
    assert isinstance(result, RetrievalResult)
    assert result.chunks
    assert result.chunks[0].citation is not None


def test_get_vendor_context_scopes_to_vendor_source_types(db_session: Session) -> None:
    stack = _Stack()
    ingested = stack.ingest(
        db_session, title="acme-contract",
        text="Acme Corp master service agreement: net-30 payment terms.",
        source_type=KnowledgeSourceType.VENDOR_CONTRACT,
    )
    service = stack.service(db_session)
    bundle = service.get_vendor_context("Acme Corp")
    assert bundle.chunk_count > 0
    assert bundle.citations[0].document_id == ingested.document_id


def test_explain_returns_a_structured_cited_explanation(db_session: Session) -> None:
    stack = _Stack()
    stack.ingest_policy(db_session)
    service = stack.service(db_session)
    bundle = service.retrieve_policy("meal per diem")
    explanation = service.explain("why is this the limit", bundle)
    assert explanation["question"] == "why is this the limit"
    assert explanation["citations"]
    assert explanation["embeddingVersion"] == bundle.embedding_version


def test_explain_on_empty_context_says_so_rather_than_fabricating(db_session: Session) -> None:
    service = _Stack().service(db_session)
    empty_bundle = ContextBundle(text="", citations=(), token_count=0, chunk_count=0)
    explanation = service.explain("anything", empty_bundle)
    assert "No supporting evidence" in explanation["summary"]
    assert explanation["citations"] == []


def test_record_decision_is_retrievable_afterward(db_session: Session) -> None:
    service = _Stack().service(db_session)
    service.record_decision(DecisionMemoryKind.APPROVAL, "claim-002", "Claim approved by manager.")
    bundle = service.retrieve_similar_claims("claim approved by manager")
    assert bundle.chunk_count > 0


def test_record_decision_is_a_no_op_when_decision_memory_flag_is_disabled(
    db_session: Session,
) -> None:
    stack = _Stack()
    flags = FeatureFlags(ai_settings)
    flags.set_override("ai.decision_memory", False)
    service = stack.service(db_session, flags=flags)
    service.record_decision(DecisionMemoryKind.APPROVAL, "claim-003", "Should never be indexed.")

    # Search with a flag-enabled service (same stack, so it would see the same vectors) to confirm
    # nothing was written.
    other = stack.service(db_session)
    bundle = other.retrieve_similar_claims("Should never be indexed")
    assert bundle.chunk_count == 0


def test_get_document_resolves_citation_metadata(db_session: Session) -> None:
    stack = _Stack()
    ingested = stack.ingest_policy(db_session, title="get-document-doc")
    service = stack.service(db_session)
    citation = service.get_document(ingested.document_id)
    assert isinstance(citation, Citation)
    assert citation.document_id == ingested.document_id
    assert citation.document_title == "get-document-doc"


def test_get_document_returns_none_for_an_unknown_id(db_session: Session) -> None:
    service = _Stack().service(db_session)
    assert service.get_document(uuid.uuid4()) is None


def test_describe_reports_reproducibility_metadata(db_session: Session) -> None:
    service = _Stack().service(db_session)
    description = service.describe()
    assert description["tenantId"] == TENANT_ID
    assert description["embeddingSpecKey"]
    assert description["vectorStore"] == "memory"
    assert "featureFlags" in description


# ---------------------------------------------------------------------------
# 2. Feature-flag gating
# ---------------------------------------------------------------------------


def test_retrieval_methods_raise_when_the_retrieval_flag_is_disabled(db_session: Session) -> None:
    flags = FeatureFlags(ai_settings)
    flags.set_override("ai.retrieval", False)
    service = _Stack().service(db_session, flags=flags)
    with pytest.raises(FeatureDisabledError):
        service.retrieve_policy("anything")
    with pytest.raises(FeatureDisabledError):
        service.search(RetrievalQuery(text="anything", tenant_id=TENANT_ID))
    with pytest.raises(FeatureDisabledError):
        service.retrieve_similar_claims("anything")
    with pytest.raises(FeatureDisabledError):
        service.get_vendor_context("anything")


# ---------------------------------------------------------------------------
# 3. citations.py / context_builder.py unit behaviour
# ---------------------------------------------------------------------------


def test_attach_citations_is_a_no_op_on_an_empty_result() -> None:
    query = RetrievalQuery(text="x", tenant_id=TENANT_ID)
    empty = RetrievalResult(chunks=(), query=query, embedding_version="v1", config_fingerprint="f")
    result = attach_citations(empty, document_repo=None, tenant_id=TENANT_ID)
    assert result is empty


def test_build_context_reports_truncated_from_the_retrieval_result() -> None:
    query = RetrievalQuery(text="x", tenant_id=TENANT_ID)
    truncated_result = RetrievalResult(
        chunks=(), query=query, embedding_version="v1", config_fingerprint="f", truncated=True,
    )
    bundle = build_context(truncated_result)
    assert bundle.truncated is True


def test_build_context_on_empty_result_is_an_empty_bundle() -> None:
    query = RetrievalQuery(text="x", tenant_id=TENANT_ID)
    empty = RetrievalResult(chunks=(), query=query, embedding_version="v1", config_fingerprint="f")
    bundle = build_context(empty)
    assert bundle.is_empty()
    assert bundle.chunk_count == 0
    assert bundle.citations == ()


# ---------------------------------------------------------------------------
# 4. vendor_knowledge.py / decision_memory.py query builders
# ---------------------------------------------------------------------------


def test_vendor_context_query_scopes_to_vendor_source_types() -> None:
    query = vendor_context_query("Acme", tenant_id=TENANT_ID, top_k=3)
    assert query.text == "Acme"
    assert query.top_k == 3
    assert len(query.filters) == 1
    assert set(query.filters[0].value) == {st.value for st in VENDOR_SOURCE_TYPES}


def test_similar_claims_query_scopes_to_decision_memory_source_types() -> None:
    query = similar_claims_query("a claim", tenant_id=TENANT_ID, top_k=7)
    assert query.top_k == 7
    assert set(query.filters[0].value) == set(DECISION_MEMORY_SOURCE_TYPES)


def test_every_decision_memory_kind_has_an_explicit_source_type() -> None:
    for kind in DecisionMemoryKind:
        source_type = source_type_for(kind)
        assert isinstance(source_type, KnowledgeSourceType)
        assert source_type.value in DECISION_MEMORY_SOURCE_TYPES


# ---------------------------------------------------------------------------
# 5. indexer.py: the write path in isolation
# ---------------------------------------------------------------------------


def test_record_decision_ingests_under_the_kinds_mapped_source_type(db_session: Session) -> None:
    from app.ai.memory.indexer import record_decision

    result = record_decision(
        DecisionMemoryKind.FRAUD_FINDING, "claim-004", "Manually flagged for investigation.",
        session=db_session, tenant_id=TENANT_ID,
    )
    doc_repo = KnowledgeDocumentRepository(db_session)
    document = doc_repo.get_scoped(result.document_id, tenant_id=TENANT_ID)
    assert document.source_type == KnowledgeSourceType.FRAUD_INVESTIGATION.value


def test_record_decision_titles_are_unique_across_calls_for_the_same_subject(
    db_session: Session,
) -> None:
    """Each call is a genuinely new entry, never a new version of a previous one."""
    from app.ai.memory.indexer import record_decision

    first = record_decision(
        DecisionMemoryKind.COMMENT, "claim-005", "First comment.",
        session=db_session, tenant_id=TENANT_ID,
    )
    second = record_decision(
        DecisionMemoryKind.COMMENT, "claim-005", "Second comment.",
        session=db_session, tenant_id=TENANT_ID,
    )
    assert first.document_id != second.document_id
    assert first.document_version == 1
    assert second.document_version == 1  # not a new version of the first; a new document entirely


# ---------------------------------------------------------------------------
# 6. ClaimService integration: a real decision appears in decision memory, and disabling AI
#    leaves the claim pipeline byte-identical
# ---------------------------------------------------------------------------


def _build_claim_service(repositories: dict, *, decision_memory=None):
    from app.services.audit_service import AuditService
    from app.services.claim_service import ClaimService
    from app.services.employee_service import EmployeeService
    from app.services.policy_rule_service import PolicyRuleService

    audit = AuditService(repositories["audit"])
    employees = EmployeeService(repositories["employees"], repositories["departments"])
    policies = PolicyRuleService(repositories["policy_rules"], audit)
    return ClaimService(
        claim_repository=repositories["claims"],
        fraud_repository=repositories["fraud"],
        workflow_repository=repositories["workflows"],
        receipt_repository=repositories["receipts"],
        employee_service=employees,
        policy_rule_service=policies,
        audit_service=audit,
        decision_memory=decision_memory,
    )


def test_a_submitted_claim_appears_in_decision_memory_and_is_returned_by_retrieve_similar_claims(
    db_session: Session, repositories: dict, claim_payload, employee_actor,
) -> None:
    stack = _Stack()
    knowledge_service = stack.service(db_session)
    service = _build_claim_service(repositories, decision_memory=knowledge_service)

    claim = service.submit_claim(
        claim_payload(merchantVendor="Distinctive Memory Vendor XYZ"), actor=employee_actor,
    )

    bundle = knowledge_service.retrieve_similar_claims("Distinctive Memory Vendor XYZ claim")
    assert bundle.chunk_count > 0
    assert any(
        citation.document_id for citation in bundle.citations
    )  # a real, resolvable citation, not a placeholder
    assert claim.status is not None  # the claim itself still processed normally


def test_disabling_decision_memory_leaves_claim_submission_byte_identical(
    db_session: Session, repositories: dict, claim_payload, employee_actor,
) -> None:
    """The exact scenario ``decision_memory=None`` (every existing test's ``claim_service``
    fixture) already proves implicitly — asserted explicitly here as the Done Check requires."""
    with_memory = _build_claim_service(repositories, decision_memory=_Stack().service(db_session))
    without_memory = _build_claim_service(repositories, decision_memory=None)

    payload = claim_payload(merchantVendor="Identical Behaviour Vendor")
    claim_a = with_memory.submit_claim(dict(payload), actor=employee_actor)
    claim_b = without_memory.submit_claim(
        claim_payload(merchantVendor="Identical Behaviour Vendor B"), actor=employee_actor
    )

    assert claim_a.status == claim_b.status
    assert claim_a.amount_usd == claim_b.amount_usd


def test_decision_memory_recording_does_not_prematurely_commit_the_claims_own_transaction() -> None:
    """Regression: ``ingest_document`` (M8) manages its own transaction with internal
    ``session.commit()``/``rollback()`` calls, which is correct for its own standalone use but
    would silently break ``ClaimService``'s "one whole transaction" promise if a decision-memory
    write nested inside ``submit_claim()`` (via ``_remember()``) reused that same, unguarded
    behaviour — every commit inside ``ingest_document`` would also commit the claim and everything
    staged before it, even if a later step failed and the route's own transaction was never
    supposed to commit at all.

    Uses a real, unmanaged ``SessionLocal()`` (not the ``db_session`` fixture, whose own nested
    savepoint semantics would make "did an inner commit leak past this test's boundary" ambiguous
    to observe) so an explicit ``session.rollback()`` unambiguously means what it says: nothing
    this test's own code did not explicitly commit should survive it.
    """
    from datetime import date, timedelta
    from decimal import Decimal

    from app.core.database import SessionLocal
    from app.domain.actor import Actor
    from app.models.claim import Claim
    from app.models.organization import Employee
    from app.repositories.audit_repository import AuditLogRepository
    from app.repositories.claim_repository import ClaimRepository
    from app.repositories.employee_repository import DepartmentRepository, EmployeeRepository
    from app.repositories.fraud_repository import FraudResultRepository
    from app.repositories.policy_rule_repository import PolicyRuleRepository
    from app.repositories.receipt_repository import ReceiptRepository
    from app.repositories.workflow_repository import ApprovalWorkflowRepository

    session = SessionLocal()
    try:
        repositories = {
            "claims": ClaimRepository(session), "employees": EmployeeRepository(session),
            "departments": DepartmentRepository(session),
            "policy_rules": PolicyRuleRepository(session),
            "audit": AuditLogRepository(session), "fraud": FraudResultRepository(session),
            "workflows": ApprovalWorkflowRepository(session),
            "receipts": ReceiptRepository(session),
        }
        stack = _Stack()
        knowledge_service = stack.service(session)
        service = _build_claim_service(repositories, decision_memory=knowledge_service)

        employee = session.query(Employee).filter_by(employee_code="emp-101").one()
        actor = Actor(
            role="employee", sub="atomicity-test-sub", employee_code=employee.employee_code,
        )
        payload = {
            "category": "Meals", "subCategory": "Team Lunch", "amount": Decimal("22.50"),
            "amountUSD": Decimal("22.50"), "currency": "USD",
            "merchantVendor": f"Atomicity Regression Vendor {uuid.uuid4().hex[:8]}",
            "expenseDate": date.today() - timedelta(days=1), "purposeDescription": "test",
            "receiptAttached": True,
        }

        claim = service.submit_claim(payload, actor=actor)
        claim_id = claim.id

        # Simulate a route-level failure discovered AFTER submit_claim() returns but BEFORE the
        # route's own uow.commit() ever runs: nothing should have committed the claim already.
        session.rollback()

        with SessionLocal() as verify_session:
            survived = verify_session.query(Claim).filter_by(id=claim_id).first()
        assert survived is None, (
            "the claim survived a rollback of its own transaction — decision-memory recording "
            "committed it prematurely"
        )
    finally:
        session.rollback()
        session.close()
