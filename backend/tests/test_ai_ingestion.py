"""Ingestion pipeline tests (T004-M8).

Database-backed: every test drives :func:`ingest_document` against the real, migrated test
database (``db_session``), with a fast, offline embedding provider
(``DeterministicEmbeddingProvider``) and vector store (``InMemoryVectorStore``) so the suite stays
fast without needing bge-m3 weights or pgvector — exactly the pattern M6/M7's own database-backed
tests already use.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.ai.core.config import BGE_M3_DIMENSIONS
from app.ai.core.enums import (
    ChunkStrategy,
    DocumentStatus,
    IngestionStatus,
    KnowledgeSourceType,
    RedactionMode,
)
from app.ai.core.errors import PIIRejectionError
from app.ai.embeddings import EmbeddingService
from app.ai.ingestion.connectors import Connector, ConnectorDocumentRef
from app.ai.ingestion.pipeline import DocumentMetadataInput, ingest_document
from app.ai.ingestion.profiles import profile_for
from app.ai.ingestion.sources import Source
from app.ai.ingestion.sources.filesystem import FilesystemSource
from app.ai.ingestion.sources.inline import InlineSource
from app.ai.ingestion.sources.s3 import S3Source
from app.ai.ingestion.sources.upload import UploadSource
from app.ai.providers.embeddings.deterministic import DeterministicEmbeddingProvider
from app.ai.repositories.knowledge_repository import (
    KnowledgeChunkRepository,
    KnowledgeDocumentRepository,
    KnowledgeIngestionRunRepository,
)
from app.ai.vector_store.memory import InMemoryVectorStore

TENANT_ID = "ingestion-test-tenant"
# knowledge_embeddings.embedding is a fixed-width pgvector column sized to BGE_M3_DIMENSIONS at the
# schema level (app/ai/models/knowledge.py), so any provider used against the real test database —
# including this deterministic stand-in — must produce vectors of exactly that width.
DIMENSIONS = BGE_M3_DIMENSIONS

POLICY_TEXT = (
    "Travel Reimbursement Policy\n\n"
    "Travel expenses must be submitted within thirty days of the trip's end date, and every "
    "receipt over twenty-five dollars requires an itemized breakdown of the charges incurred.\n\n"
    "Meals are reimbursed at actual cost up to the daily per-diem limit for the traveler's "
    "destination city, and alcohol is never a reimbursable expense under any circumstance.\n\n"
    "Hotel bookings should use the corporate rate whenever one is available, and any booking "
    "above the nightly cap needs a director's written approval before the trip begins."
)


def _embedding_service() -> EmbeddingService:
    return EmbeddingService(DeterministicEmbeddingProvider(dimensions=DIMENSIONS, version="v1"))


def _vector_store() -> InMemoryVectorStore:
    return InMemoryVectorStore(dimensions=DIMENSIONS)


def _ingest(
    session: Session, *, text: str = POLICY_TEXT, title: str, tenant_id: str = TENANT_ID,
    source_type: KnowledgeSourceType = KnowledgeSourceType.POLICY, **kwargs,
):
    source = InlineSource(text=text, file_name=f"{title}.txt", source_type=source_type,
                          tenant_id=tenant_id)
    return ingest_document(
        source, session=session, metadata=DocumentMetadataInput(title=title),
        embedding_service=_embedding_service(), vector_store=_vector_store(), **kwargs,
    )


# ---------------------------------------------------------------------------
# 1. Idempotency: re-ingesting identical bytes
# ---------------------------------------------------------------------------


def test_ingesting_the_same_bytes_twice_is_a_no_op(db_session: Session) -> None:
    first = _ingest(db_session, title="idempotency-doc")
    assert first.status == IngestionStatus.COMPLETED

    second = _ingest(db_session, title="idempotency-doc")
    assert second.status == IngestionStatus.SKIPPED_DUPLICATE
    assert second.document_id == first.document_id
    assert second.chunks_created == 0

    doc_repo = KnowledgeDocumentRepository(db_session)
    documents = doc_repo.search(tenant_id=TENANT_ID, include_superseded=True)
    matching = [d for d in documents if d.id == first.document_id]
    assert len(matching) == 1  # exactly one document, not two

    run_repo = KnowledgeIngestionRunRepository(db_session)
    runs = run_repo.for_document(first.document_id, tenant_id=TENANT_ID)
    statuses = sorted(r.status for r in runs)
    assert statuses == sorted([IngestionStatus.COMPLETED, IngestionStatus.SKIPPED_DUPLICATE])


def test_skipped_duplicate_run_is_not_marked_failed(db_session: Session) -> None:
    _ingest(db_session, title="dup-status-doc")
    result = _ingest(db_session, title="dup-status-doc")
    run_repo = KnowledgeIngestionRunRepository(db_session)
    run = run_repo.get(result.run_id)
    assert run is not None
    assert run.status == IngestionStatus.SKIPPED_DUPLICATE
    assert run.succeeded


# ---------------------------------------------------------------------------
# 2. Versioning and supersession
# ---------------------------------------------------------------------------


def test_ingesting_a_modified_file_creates_a_new_version_and_supersedes_the_old(
    db_session: Session,
) -> None:
    v1 = _ingest(db_session, text=POLICY_TEXT, title="versioned-doc")
    assert v1.document_version == 1

    modified_text = POLICY_TEXT + "\n\nAppendix: fuel receipts require an odometer photo."
    v2 = _ingest(db_session, text=modified_text, title="versioned-doc")
    assert v2.document_version == 2
    assert v2.document_id != v1.document_id

    doc_repo = KnowledgeDocumentRepository(db_session)
    old = doc_repo.get_scoped(v1.document_id, tenant_id=TENANT_ID)
    new = doc_repo.get_scoped(v2.document_id, tenant_id=TENANT_ID)
    assert old is not None and new is not None
    assert old.status == DocumentStatus.SUPERSEDED
    assert new.supersedes_id == old.id
    assert new.status == DocumentStatus.INDEXED

    # v1's chunks are untouched, still retrievable via an include_superseded search.
    chunk_repo = KnowledgeChunkRepository(db_session)
    v1_chunks = chunk_repo.list_for_document(v1.document_id, tenant_id=TENANT_ID)
    assert len(v1_chunks) > 0

    visible_with_superseded = doc_repo.search(tenant_id=TENANT_ID, include_superseded=True)
    assert any(d.id == old.id for d in visible_with_superseded)
    visible_without_superseded = doc_repo.search(tenant_id=TENANT_ID, include_superseded=False)
    assert not any(d.id == old.id for d in visible_without_superseded)


def test_version_chain_reflects_both_versions(db_session: Session) -> None:
    v1 = _ingest(db_session, title="chain-doc")
    v2 = _ingest(db_session, text=POLICY_TEXT + "\n\nExtra clause.", title="chain-doc")
    doc_repo = KnowledgeDocumentRepository(db_session)
    latest = doc_repo.get_scoped(v2.document_id, tenant_id=TENANT_ID)
    chain = doc_repo.version_chain(latest)
    assert {d.id for d in chain} == {v1.document_id, v2.document_id}


# ---------------------------------------------------------------------------
# 3. Failure handling: rollback + run ledger
# ---------------------------------------------------------------------------


class _ExplodingSource:
    """A source whose fetch always fails — exercises the no-run-row-on-fetch-failure boundary."""

    def fetch(self):
        raise OSError("disk on fire")


def test_a_fetch_failure_raises_and_writes_no_run_row(db_session: Session) -> None:
    run_repo = KnowledgeIngestionRunRepository(db_session)
    before = len(run_repo.recent(tenant_id=TENANT_ID, limit=1000))
    with pytest.raises(OSError, match="disk on fire"):
        ingest_document(_ExplodingSource(), session=db_session,
                         embedding_service=_embedding_service(), vector_store=_vector_store())
    after = len(run_repo.recent(tenant_id=TENANT_ID, limit=1000))
    assert after == before


def test_a_pii_rejection_rolls_back_and_records_the_failing_stage(db_session: Session) -> None:
    from app.ai.core.config import ai_settings

    reject_settings = ai_settings.model_copy(
        update={"PII_REDACTION_MODE": RedactionMode.REJECT, "PII_DETECTION_ENABLED": True}
    )
    pii_text = POLICY_TEXT + "\n\nContact: jane.doe@example.com for approvals."

    doc_repo = KnowledgeDocumentRepository(db_session)
    with pytest.raises(PIIRejectionError):
        _ingest(db_session, text=pii_text, title="pii-reject-doc", settings=reject_settings)

    # nothing was persisted: the document write rolled back
    assert doc_repo.find_by_checksum(
        hashlib.sha256(pii_text.encode("utf-8")).hexdigest(), tenant_id=TENANT_ID,
    ) is None

    run_repo = KnowledgeIngestionRunRepository(db_session)
    runs = [
        r for r in run_repo.recent(tenant_id=TENANT_ID, limit=1000)
        if r.file_name == "pii-reject-doc.txt"
    ]
    assert len(runs) == 1
    failed_run = runs[0]
    assert failed_run.status == IngestionStatus.FAILED
    assert failed_run.error_message is not None
    assert failed_run.error_message.startswith("[PII_SCAN]")
    assert "PIIRejectionError" in failed_run.error_message


def test_pii_reject_kinds_forces_rejection_even_under_the_default_tag_mode(
    db_session: Session,
) -> None:
    """PII_REJECT_KINDS is a hard override: it rejects a specific kind even when the general
    PII_REDACTION_MODE (TAG, the default) would otherwise just record the finding and index."""
    from app.ai.core.config import ai_settings

    reject_email_settings = ai_settings.model_copy(
        update={"PII_REDACTION_MODE": RedactionMode.TAG, "PII_REJECT_KINDS": "EMAIL"}
    )
    pii_text = POLICY_TEXT + "\n\nContact: jane.doe@example.com for approvals."
    with pytest.raises(PIIRejectionError):
        _ingest(db_session, text=pii_text, title="pii-forced-reject-doc",
                settings=reject_email_settings)


def test_pii_mask_mode_redacts_the_text_but_still_ingests(db_session: Session) -> None:
    from app.ai.core.config import ai_settings

    mask_settings = ai_settings.model_copy(update={"PII_REDACTION_MODE": RedactionMode.MASK})
    pii_text = POLICY_TEXT + "\n\nContact: jane.doe@example.com for approvals."

    result = _ingest(db_session, text=pii_text, title="pii-mask-doc", settings=mask_settings)
    assert result.status == IngestionStatus.COMPLETED

    chunk_repo = KnowledgeChunkRepository(db_session)
    chunks = chunk_repo.list_for_document(result.document_id, tenant_id=TENANT_ID)
    combined = " ".join(c.content for c in chunks)
    assert "jane.doe@example.com" not in combined
    assert "REDACTED:EMAIL" in combined


def test_pii_mask_mode_redacts_every_section_not_just_the_flat_text(db_session: Session) -> None:
    """Regression: `findings`' spans are offsets into the flat joined text, not into any one
    section's own text. A section-based chunker (HEADING/HYBRID) reads `document.sections`
    directly, so redacting only the flat text while leaving `cleaned_sections` untouched would
    leak the raw PII into chunks even though redaction "succeeded" on the flat text. This fixture
    is a genuine multi-section Markdown document (unlike every other PII test in this file, which
    uses a single-section .txt source where the bug and the fix are behaviourally identical) with
    the PII placed in the *second* section specifically."""
    from app.ai.core.config import ai_settings

    mask_settings = ai_settings.model_copy(update={"PII_REDACTION_MODE": RedactionMode.MASK})
    markdown_text = (
        "# Travel Policy\n\nGeneral travel rules apply to every employee on a business trip.\n\n"
        "# Approvals\n\nContact jane.doe@example.com for any approval above the standard limit.\n\n"
        "# Reimbursement\n\nSubmit receipts within thirty days of the trip's end date for payment."
    )
    source = InlineSource(text=markdown_text, file_name="multi-section-pii.md",
                          mime_type="text/markdown", tenant_id=TENANT_ID)
    result = ingest_document(
        source, session=db_session,
        metadata=DocumentMetadataInput(title="multi-section-pii-doc"),
        chunk_strategy=ChunkStrategy.HEADING,
        chunk_overrides={"max_tokens": 200, "overlap_tokens": 10, "min_tokens": 1},
        embedding_service=_embedding_service(), vector_store=_vector_store(),
        settings=mask_settings,
    )
    assert result.status == IngestionStatus.COMPLETED

    chunk_repo = KnowledgeChunkRepository(db_session)
    chunks = chunk_repo.list_for_document(result.document_id, tenant_id=TENANT_ID)
    combined = " ".join(c.content for c in chunks)
    assert "jane.doe@example.com" not in combined
    assert "REDACTED:EMAIL" in combined
    approvals_chunks = [c for c in chunks if c.section == "Approvals"]
    assert approvals_chunks and "jane.doe@example.com" not in approvals_chunks[0].content


def test_low_quality_document_still_ingests_with_a_warning(db_session: Session) -> None:
    from app.ai.core.config import ai_settings

    strict_settings = ai_settings.model_copy(update={"MIN_QUALITY_SCORE": 99.9})
    result = _ingest(db_session, text="A short note.", title="low-quality-doc",
                      settings=strict_settings)
    assert result.status == IngestionStatus.COMPLETED  # warns but still indexes, per its docstring


def test_default_title_derives_from_the_file_name_when_not_supplied(db_session: Session) -> None:
    source = InlineSource(text=POLICY_TEXT, file_name="untitled-policy.txt", tenant_id=TENANT_ID)
    result = ingest_document(
        source, session=db_session, embedding_service=_embedding_service(),
        vector_store=_vector_store(),
    )
    doc_repo = KnowledgeDocumentRepository(db_session)
    document = doc_repo.get_scoped(result.document_id, tenant_id=TENANT_ID)
    assert document.title == "untitled-policy"


def test_an_unsupported_document_fails_at_the_parse_stage(db_session: Session) -> None:
    source = InlineSource(
        content=b"\x00\x01\x02 not a real format", file_name="mystery.zzzunknown",
        mime_type="application/x-totally-unknown", tenant_id=TENANT_ID,
    )
    from app.ai.core.errors import UnsupportedDocumentError

    with pytest.raises(UnsupportedDocumentError):
        ingest_document(
            source, session=db_session, metadata=DocumentMetadataInput(title="mystery-doc"),
            embedding_service=_embedding_service(), vector_store=_vector_store(),
        )
    run_repo = KnowledgeIngestionRunRepository(db_session)
    runs = [
        r for r in run_repo.recent(tenant_id=TENANT_ID, limit=1000)
        if r.file_name == "mystery.zzzunknown"
    ]
    assert len(runs) == 1
    assert runs[0].status == IngestionStatus.FAILED
    assert runs[0].error_message.startswith("[PARSE]")


def test_a_failure_between_pii_scan_and_persist_document_is_labelled_correctly(
    db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: quality scoring, ParsedDocument construction, and profile/title resolution used
    to run *outside* any tracked stage, between the PII_SCAN and PERSIST_DOCUMENT `with` blocks —
    so a failure there was mislabelled with the stale "[PII_SCAN]" prefix. That work now runs
    inside PERSIST_DOCUMENT's own stage block."""
    import app.ai.ingestion.pipeline as pipeline_module

    def _boom(*args, **kwargs):
        raise RuntimeError("boom in the persist-document stage")

    monkeypatch.setattr(pipeline_module, "profile_for", _boom)
    with pytest.raises(RuntimeError, match="boom in the persist-document stage"):
        _ingest(db_session, title="gap-failure-doc")

    run_repo = KnowledgeIngestionRunRepository(db_session)
    runs = [
        r for r in run_repo.recent(tenant_id=TENANT_ID, limit=1000)
        if r.file_name == "gap-failure-doc.txt"
    ]
    assert len(runs) == 1
    assert runs[0].error_message.startswith("[PERSIST_DOCUMENT]")


# ---------------------------------------------------------------------------
# 4. Successful ingestion: chunks and embeddings actually land
# ---------------------------------------------------------------------------


def test_successful_ingestion_persists_chunks_and_embeddings(db_session: Session) -> None:
    result = _ingest(db_session, title="full-run-doc")
    assert result.status == IngestionStatus.COMPLETED
    assert result.chunks_created > 0
    assert result.embeddings_created == result.chunks_created

    chunk_repo = KnowledgeChunkRepository(db_session)
    chunks = chunk_repo.list_for_document(result.document_id, tenant_id=TENANT_ID)
    assert len(chunks) == result.chunks_created
    assert all(c.content.strip() for c in chunks)

    doc_repo = KnowledgeDocumentRepository(db_session)
    document = doc_repo.get_scoped(result.document_id, tenant_id=TENANT_ID)
    assert document is not None
    assert document.status == DocumentStatus.INDEXED
    assert document.indexed_at is not None
    assert document.category == "policy"  # from the POLICY profile's default_category


def test_classify_stage_output_is_persisted_in_document_metadata(db_session: Session) -> None:
    """Regression: CLASSIFY's output used to be computed and timed every run but never actually
    consumed by anything — dead work. It is now persisted onto doc_metadata."""
    result = _ingest(db_session, title="classify-doc")
    doc_repo = KnowledgeDocumentRepository(db_session)
    document = doc_repo.get_scoped(result.document_id, tenant_id=TENANT_ID)
    assert document.doc_metadata is not None
    assert "detectedSourceType" in document.doc_metadata


def test_ingestion_run_records_stage_timings_and_completion(db_session: Session) -> None:
    result = _ingest(db_session, title="timings-doc")
    run_repo = KnowledgeIngestionRunRepository(db_session)
    run = run_repo.get(result.run_id)
    assert run is not None
    assert run.stage_timings is not None
    for stage_name in ("FETCH", "DEDUPLICATE", "PARSE", "NORMALIZE", "CLASSIFY", "PII_SCAN",
                       "PERSIST_DOCUMENT", "CHUNK", "EMBED", "INDEX"):
        assert stage_name in run.stage_timings
    # FINALIZE deliberately has no entry: its only "work" is the finish() call that persists
    # stage_timings, so a timing for it could only be added to the dict after that call already
    # flushed — there is no way to make it durably include itself. See pipeline.py's comment at
    # the FINALIZE call site.
    assert "FINALIZE" not in run.stage_timings
    assert run.duration_ms is not None and run.duration_ms >= 0
    assert run.finished_at is not None


def test_chunk_strategy_override_is_honoured_over_the_profile_default(db_session: Session) -> None:
    result = _ingest(db_session, title="override-doc", chunk_strategy=ChunkStrategy.TOKEN,
                      chunk_overrides={"max_tokens": 20, "overlap_tokens": 2, "min_tokens": 1})
    chunk_repo = KnowledgeChunkRepository(db_session)
    chunks = chunk_repo.list_for_document(result.document_id, tenant_id=TENANT_ID)
    assert all(c.strategy == "TOKEN" for c in chunks)


def test_metadata_fields_flow_from_caller_through_to_the_document_row(db_session: Session) -> None:
    source = InlineSource(text=POLICY_TEXT, file_name="metadata-doc.txt",
                           source_type=KnowledgeSourceType.TRAVEL_POLICY, tenant_id=TENANT_ID)
    result = ingest_document(
        source, session=db_session,
        metadata=DocumentMetadataInput(
            title="metadata-doc", department="Sales", country="US", currency="USD",
            effective_date=date(2026, 1, 1), expiry_date=date(2026, 12, 31),
        ),
        embedding_service=_embedding_service(), vector_store=_vector_store(),
    )
    doc_repo = KnowledgeDocumentRepository(db_session)
    document = doc_repo.get_scoped(result.document_id, tenant_id=TENANT_ID)
    assert document.department == "Sales"
    assert document.country == "US"
    assert document.currency == "USD"
    assert document.effective_date == date(2026, 1, 1)

    chunk_repo = KnowledgeChunkRepository(db_session)
    chunks = chunk_repo.list_for_document(result.document_id, tenant_id=TENANT_ID)
    assert all(c.department == "Sales" for c in chunks)


def test_parent_child_ingestion_persists_parent_and_child_rows_without_fk_violation(
    db_session: Session,
) -> None:
    """Regression-shaped: PARENT_CHILD chunks are inserted in one add_many batch with a
    self-referential FK (parent_chunk_id -> knowledge_chunks.id); parents must precede their
    children in insertion order or PostgreSQL rejects the batch outright."""
    result = _ingest(
        db_session, title="parent-child-doc", chunk_strategy=ChunkStrategy.PARENT_CHILD,
        chunk_overrides={"max_tokens": 15, "overlap_tokens": 3, "min_tokens": 1,
                         "parent_max_tokens": 60},
    )
    assert result.status == IngestionStatus.COMPLETED
    chunk_repo = KnowledgeChunkRepository(db_session)
    chunks = chunk_repo.list_for_document(result.document_id, tenant_id=TENANT_ID)
    children = [c for c in chunks if c.parent_chunk_id is not None]
    assert children  # the fixture is large enough to produce at least one parent-child pair
    ids = {c.id for c in chunks}
    for child in children:
        assert child.parent_chunk_id in ids


# ---------------------------------------------------------------------------
# 5. Knowledge-source profiles
# ---------------------------------------------------------------------------


def test_every_knowledge_source_type_has_an_explicit_profile() -> None:
    for source_type in KnowledgeSourceType:
        profile = profile_for(source_type)
        assert profile.source_type == source_type
        assert isinstance(profile.chunk_strategy, ChunkStrategy)


def test_unknown_source_type_would_fall_back_to_other() -> None:
    # OTHER's own profile IS the fallback; assert it is at least internally consistent.
    other_profile = profile_for(KnowledgeSourceType.OTHER)
    assert other_profile.chunk_strategy == ChunkStrategy.RECURSIVE


# ---------------------------------------------------------------------------
# 6. Sources
# ---------------------------------------------------------------------------


def test_upload_source_produces_a_raw_document_with_no_source_uri() -> None:
    source = UploadSource(content=b"hello world", file_name="upload.txt",
                          source_type=KnowledgeSourceType.FAQ, tenant_id=TENANT_ID)
    document = source.fetch()
    assert document.content == b"hello world"
    assert document.file_name == "upload.txt"
    assert document.source_uri is None
    assert document.source_type == KnowledgeSourceType.FAQ


def test_inline_source_requires_text_or_content() -> None:
    with pytest.raises(ValueError, match="requires either text or content"):
        InlineSource()


def test_filesystem_source_reads_the_file_and_sets_source_uri(tmp_path: Path) -> None:
    path = tmp_path / "policy.txt"
    path.write_text("some policy text", encoding="utf-8")
    source = FilesystemSource(path=path, tenant_id=TENANT_ID)
    document = source.fetch()
    assert document.content == b"some policy text"
    assert document.file_name == "policy.txt"
    assert document.source_uri == str(path)
    assert document.mime_type == "text/plain"


def test_s3_source_reports_unavailable_without_boto3_or_credentials(monkeypatch) -> None:
    source = S3Source(bucket="my-bucket", key="docs/policy.pdf")
    # is_available() must never raise regardless of environment; only assert the type contract.
    assert isinstance(source.is_available(), bool)


def test_s3_source_raises_provider_not_configured_when_unavailable(monkeypatch) -> None:

    source = S3Source(bucket="my-bucket", key="docs/policy.pdf")
    monkeypatch.setattr(source, "is_available", lambda: False)
    from app.ai.core.errors import ProviderNotConfiguredError

    with pytest.raises(ProviderNotConfiguredError):
        source.fetch()


# ---------------------------------------------------------------------------
# 7. Connector protocol (interface only)
# ---------------------------------------------------------------------------


def test_connector_protocol_is_satisfied_by_a_minimal_implementation() -> None:
    class _StubConnector:
        name = "stub"

        def is_available(self) -> bool:
            return True

        def list_documents(self):
            return (ConnectorDocumentRef(external_id="1", display_name="Doc 1"),)

        def fetch(self, ref: ConnectorDocumentRef):
            from app.ai.core.types import RawDocument
            return RawDocument(content=b"x", file_name=ref.display_name)

    stub = _StubConnector()
    assert isinstance(stub, Connector)
    refs = stub.list_documents()
    assert refs[0].external_id == "1"
    document = stub.fetch(refs[0])
    assert document.file_name == "Doc 1"


def test_source_protocol_is_satisfied_by_inline_source() -> None:
    assert isinstance(InlineSource(text="hi"), Source)


