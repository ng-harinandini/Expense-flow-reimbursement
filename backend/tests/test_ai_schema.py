"""Schema and repository tests for the AI knowledge core (T004-M2).

Runs against real PostgreSQL with pgvector, for the same reason T003's tests do: native enums,
JSONB, check constraints, generated ``tsvector`` columns, ``pg_trgm`` and the ``vector`` type do not
exist in SQLite, so a stub would test nothing that matters here.

Vectors in this module are **hand-authored**, not model-generated. That is deliberate — it keeps the
schema and ranking assertions independent of embedding quality, and keeps the suite fast and green
without the 2.3 GB bge-m3 weights. Real end-to-end semantic retrieval is exercised separately once
the embedding provider lands (M5).
"""

from __future__ import annotations

import math
import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError, StatementError
from sqlalchemy.orm import Session

from app.ai.core.enums import DocumentStatus, IngestionStage, IngestionStatus
from app.ai.core.ids import chunk_id_for, content_checksum, document_id_for, text_checksum
from app.ai.models.knowledge import (
    EMBEDDING_DIMENSIONS,
    HNSW_EF_CONSTRUCTION,
    HNSW_M,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeEmbedding,
)
from app.ai.repositories import (
    KnowledgeChunkRepository,
    KnowledgeDocumentRepository,
    KnowledgeEmbeddingRepository,
    KnowledgeIngestionRunRepository,
)

TENANT = "default"
OTHER_TENANT = "globex"
SPEC = "bge_m3_onnx/BAAI/bge-m3@v1"
SPEC_V2 = "bge_m3_onnx/BAAI/bge-m3@v2"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def unit_vector(*, seed: int) -> list[float]:
    """A deterministic unit vector, distinct per ``seed``.

    Built from sine waves rather than random numbers so a failure is reproducible, and normalized
    because every provider the platform ships returns unit vectors — cosine and inner product then
    rank identically.
    """
    raw = [math.sin(seed * 0.7 + i * 0.013) for i in range(EMBEDDING_DIMENSIONS)]
    norm = math.sqrt(sum(v * v for v in raw))
    return [v / norm for v in raw]


@pytest.fixture
def documents(db_session: Session) -> KnowledgeDocumentRepository:
    return KnowledgeDocumentRepository(db_session)


@pytest.fixture
def chunks(db_session: Session) -> KnowledgeChunkRepository:
    return KnowledgeChunkRepository(db_session)


@pytest.fixture
def embeddings(db_session: Session) -> KnowledgeEmbeddingRepository:
    return KnowledgeEmbeddingRepository(db_session)


@pytest.fixture
def runs(db_session: Session) -> KnowledgeIngestionRunRepository:
    return KnowledgeIngestionRunRepository(db_session)


@pytest.fixture
def make_document(documents: KnowledgeDocumentRepository):
    """Insert a document, defaulting to an indexed travel policy."""

    def _make(
        *,
        title: str = "Travel Policy",
        body: bytes | None = None,
        tenant_id: str = TENANT,
        status: DocumentStatus = DocumentStatus.INDEXED,
        source_type: str = "TRAVEL_POLICY",
        **columns,
    ) -> KnowledgeDocument:
        payload = body if body is not None else f"{title}-{uuid.uuid4().hex}".encode()
        checksum = content_checksum(payload)
        document = KnowledgeDocument(
            id=document_id_for(tenant_id, checksum),
            tenant_id=tenant_id,
            source_type=source_type,
            title=title,
            checksum_sha256=checksum,
            size_bytes=len(payload),
            status=status,
            **columns,
        )
        return documents.add(document)

    return _make


@pytest.fixture
def make_chunk(chunks: KnowledgeChunkRepository):
    def _make(
        document: KnowledgeDocument,
        content: str,
        index: int = 0,
        **columns,
    ) -> KnowledgeChunk:
        chunk = KnowledgeChunk(
            id=chunk_id_for(document.id, index, content),
            tenant_id=document.tenant_id,
            document_id=document.id,
            chunk_index=index,
            content=content,
            content_tokens=max(1, len(content) // 4),
            checksum_sha256=text_checksum(content),
            strategy="RECURSIVE",
            **columns,
        )
        return chunks.add(chunk)

    return _make


# ---------------------------------------------------------------------------
# schema objects
# ---------------------------------------------------------------------------


def test_embedding_column_is_a_real_pgvector_column(db_session: Session) -> None:
    """Not ``bytea`` or ``float[]`` — the HNSW index and ``<=>`` require the native type."""
    column_type = db_session.execute(text("""
        SELECT format_type(atttypid, atttypmod)
        FROM pg_attribute
        WHERE attrelid = 'knowledge_embeddings'::regclass AND attname = 'embedding'
    """)).scalar()
    assert column_type == f"vector({EMBEDDING_DIMENSIONS})"


def test_content_tsv_is_a_stored_generated_column(db_session: Session) -> None:
    """Generated, so full-text search cannot drift from the text it indexes."""
    generated = db_session.execute(text("""
        SELECT attgenerated
        FROM pg_attribute
        WHERE attrelid = 'knowledge_chunks'::regclass AND attname = 'content_tsv'
    """)).scalar()
    assert generated == "s", "content_tsv must be GENERATED ... STORED"


def test_hnsw_index_exists_with_the_configured_parameters(db_session: Session) -> None:
    """Guards against the index silently becoming ivfflat, L2, or differently tuned."""
    definition = db_session.execute(text("""
        SELECT indexdef FROM pg_indexes
        WHERE tablename = 'knowledge_embeddings' AND indexname = 'ix_knowledge_embeddings_hnsw'
    """)).scalar()
    assert definition is not None, "the HNSW index is missing"
    assert "USING hnsw" in definition
    assert "vector_cosine_ops" in definition
    assert f"m='{HNSW_M}'" in definition
    assert f"ef_construction='{HNSW_EF_CONSTRUCTION}'" in definition


def test_model_and_migration_agree_on_hnsw_parameters() -> None:
    """Parity check in the spirit of T003's Python/trigger transition test.

    The migration hard-codes these values (it must not import application code), so the two copies
    can drift. Drift would rebuild the index with different recall characteristics and nothing else
    would notice.
    """
    import importlib.util
    from pathlib import Path

    path = next(
        (Path(__file__).resolve().parent.parent / "alembic" / "versions").glob(
            "*0004_ai_knowledge_platform.py"
        )
    )
    spec = importlib.util.spec_from_file_location("_m0004", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    assert module.HNSW_M == HNSW_M
    assert module.HNSW_EF_CONSTRUCTION == HNSW_EF_CONSTRUCTION
    assert module.EMBEDDING_DIMENSIONS == EMBEDDING_DIMENSIONS


def test_required_extensions_are_installed(db_session: Session) -> None:
    installed = {
        row[0] for row in db_session.execute(text("SELECT extname FROM pg_extension"))
    }
    assert {"vector", "pg_trgm"} <= installed


def test_trigram_and_fulltext_indexes_exist(db_session: Session) -> None:
    names = {
        row[0] for row in db_session.execute(text(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'knowledge_chunks'"
        ))
    }
    assert "ix_knowledge_chunks_tsv" in names
    assert "ix_knowledge_chunks_content_trgm" in names


# ---------------------------------------------------------------------------
# constraints
# ---------------------------------------------------------------------------


def test_identical_bytes_cannot_be_ingested_twice(
    db_session: Session, make_document
) -> None:
    """Idempotency is a database constraint, not a service-layer check that could be bypassed."""
    first = make_document(body=b"identical policy bytes")
    db_session.flush()
    duplicate = KnowledgeDocument(
        tenant_id=first.tenant_id,
        source_type="POLICY",
        title="A different title, same bytes",
        checksum_sha256=first.checksum_sha256,
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_two_tenants_may_hold_the_same_bytes(db_session: Session, make_document) -> None:
    """A shared public PDF must not be a cross-tenant conflict."""
    payload = b"public tax guidance 2025"
    make_document(body=payload, tenant_id=TENANT)
    make_document(body=payload, tenant_id=OTHER_TENANT)
    db_session.flush()   # no IntegrityError


def test_chunk_index_is_unique_within_a_document(
    db_session: Session, make_document, make_chunk
) -> None:
    document = make_document()
    make_chunk(document, "first", 0)
    db_session.add(
        KnowledgeChunk(
            tenant_id=document.tenant_id, document_id=document.id,
            chunk_index=0, content="clashing index",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_blank_chunk_content_is_rejected(db_session: Session, make_document) -> None:
    """An empty chunk is never useful and would pollute both retrieval legs."""
    document = make_document()
    db_session.add(
        KnowledgeChunk(
            tenant_id=document.tenant_id, document_id=document.id,
            chunk_index=0, content="   ",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_expiry_before_effective_date_is_rejected(db_session: Session, make_document) -> None:
    with pytest.raises(IntegrityError):
        make_document(
            effective_date=date(2025, 6, 1), expiry_date=date(2025, 1, 1)
        )
        db_session.flush()


def test_quality_score_outside_zero_to_hundred_is_rejected(
    db_session: Session, make_document
) -> None:
    with pytest.raises(IntegrityError):
        make_document(quality_score=101)
        db_session.flush()


def test_one_vector_per_chunk_per_model_version(
    db_session: Session, make_document, make_chunk, embeddings: KnowledgeEmbeddingRepository
) -> None:
    document = make_document()
    chunk = make_chunk(document, "meal allowance is 50 USD", 0)
    embeddings.upsert(
        chunk_id=chunk.id, document_id=document.id, tenant_id=TENANT,
        vector=unit_vector(seed=1), provider="p", model_name="m", version="v1",
        spec_key=SPEC, dimensions=EMBEDDING_DIMENSIONS,
    )
    db_session.add(
        KnowledgeEmbedding(
            tenant_id=TENANT, chunk_id=chunk.id, document_id=document.id,
            embedding=unit_vector(seed=2), embedding_provider="p", embedding_model="m",
            embedding_version="v1", spec_key=SPEC, dimensions=EMBEDDING_DIMENSIONS,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_a_wrong_width_vector_is_rejected_before_it_reaches_the_database(
    db_session: Session, make_document, make_chunk
) -> None:
    """The first of two independent guards.

    ``app.ai.vector_store.pg_types.Vector`` checks the width in its bind processor, so a mismatched
    vector fails without a database round trip. SQLAlchemy wraps the ``ValueError`` in
    ``StatementError``.
    """
    document = make_document()
    chunk = make_chunk(document, "some policy text", 0)
    db_session.add(
        KnowledgeEmbedding(
            tenant_id=TENANT, chunk_id=chunk.id, document_id=document.id,
            embedding=[0.1, 0.2, 0.3], embedding_provider="p", embedding_model="m",
            embedding_version="v1", spec_key=SPEC, dimensions=3,
        )
    )
    with pytest.raises(StatementError, match="1024-dimensional"):
        db_session.flush()


def test_the_database_also_rejects_a_wrong_width_vector(
    db_session: Session, make_document, make_chunk
) -> None:
    """The second guard: defence in depth.

    Bypasses the ORM type entirely with raw SQL, proving ``vector(1024)`` itself refuses a 3-dim
    value. Without this, a future code path that built its own INSERT could pad or truncate silently
    and corrupt every later comparison.
    """
    document = make_document()
    chunk = make_chunk(document, "some policy text", 0)
    db_session.flush()

    with pytest.raises(DataError):
        db_session.execute(text("""
            INSERT INTO knowledge_embeddings
                (id, tenant_id, chunk_id, document_id, embedding_provider, embedding_model,
                 embedding_version, spec_key, dimensions, embedding)
            VALUES (:id, :t, :cid, :d, 'p', 'm', 'v1', :spec, 3, CAST('[1,2,3]' AS vector(3)))
        """), {"id": uuid.uuid4(), "t": TENANT, "cid": chunk.id, "d": document.id,
               "spec": SPEC})


def test_deleting_a_document_cascades_to_chunks_and_embeddings(
    db_session: Session, make_document, make_chunk, embeddings: KnowledgeEmbeddingRepository
) -> None:
    document = make_document()
    for i in range(3):
        chunk = make_chunk(document, f"clause number {i}", i)
        embeddings.upsert(
            chunk_id=chunk.id, document_id=document.id, tenant_id=TENANT,
            vector=unit_vector(seed=i), provider="p", model_name="m", version="v1",
            spec_key=SPEC, dimensions=EMBEDDING_DIMENSIONS,
        )
    db_session.flush()

    db_session.delete(document)
    db_session.flush()

    assert db_session.query(KnowledgeChunk).filter_by(document_id=document.id).count() == 0
    assert db_session.query(KnowledgeEmbedding).filter_by(document_id=document.id).count() == 0


def test_losing_a_parent_chunk_does_not_delete_its_children(
    db_session: Session, make_document, make_chunk
) -> None:
    """``SET NULL``, not ``CASCADE`` — the children are the retrievable units."""
    document = make_document()
    parent = make_chunk(document, "the whole section, for context", 0)
    child = make_chunk(document, "one precise clause", 1, parent_chunk_id=parent.id)
    db_session.flush()

    db_session.delete(parent)
    db_session.flush()
    db_session.refresh(child)

    assert child.parent_chunk_id is None
    assert db_session.get(KnowledgeChunk, child.id) is not None


# ---------------------------------------------------------------------------
# document repository
# ---------------------------------------------------------------------------


def test_find_by_checksum_is_the_idempotency_lookup(
    documents: KnowledgeDocumentRepository, make_document
) -> None:
    document = make_document(body=b"policy v1")
    found = documents.find_by_checksum(document.checksum_sha256, tenant_id=TENANT)
    assert found is not None and found.id == document.id


def test_find_by_checksum_is_tenant_scoped(
    documents: KnowledgeDocumentRepository, make_document
) -> None:
    document = make_document(body=b"policy v1", tenant_id=TENANT)
    assert documents.find_by_checksum(
        document.checksum_sha256, tenant_id=OTHER_TENANT
    ) is None


def test_get_scoped_refuses_to_cross_a_tenant_boundary(
    documents: KnowledgeDocumentRepository, make_document
) -> None:
    """The worst failure this schema can produce; asserted rather than assumed."""
    document = make_document(tenant_id=TENANT)
    assert documents.get_scoped(document.id, tenant_id=TENANT) is not None
    assert documents.get_scoped(document.id, tenant_id=OTHER_TENANT) is None


def test_get_scoped_tolerates_a_malformed_id(
    documents: KnowledgeDocumentRepository
) -> None:
    assert documents.get_scoped("not-a-uuid", tenant_id=TENANT) is None


def test_superseding_retires_the_old_version_without_deleting_it(
    db_session: Session, documents: KnowledgeDocumentRepository, make_document, make_chunk
) -> None:
    """A 2025 claim must remain explainable against the 2025 policy."""
    old = make_document(title="Travel Policy", body=b"v1 bytes", version=1)
    make_chunk(old, "the 2025 meal limit is 40 USD", 0)
    new = make_document(title="Travel Policy", body=b"v2 bytes", version=2)

    documents.supersede(old, new)
    db_session.flush()

    assert old.status == DocumentStatus.SUPERSEDED
    assert new.supersedes_id == old.id
    # The retired version and its chunks are still present.
    assert db_session.get(KnowledgeDocument, old.id) is not None
    assert db_session.query(KnowledgeChunk).filter_by(document_id=old.id).count() == 1


def test_supersede_across_tenants_is_refused(
    documents: KnowledgeDocumentRepository, make_document
) -> None:
    old = make_document(tenant_id=TENANT)
    new = make_document(tenant_id=OTHER_TENANT)
    with pytest.raises(ValueError, match="another tenant"):
        documents.supersede(old, new)


def test_version_chain_is_ordered_oldest_first(
    db_session: Session, documents: KnowledgeDocumentRepository, make_document
) -> None:
    v1 = make_document(title="P", body=b"a", version=1)
    v2 = make_document(title="P", body=b"b", version=2)
    v3 = make_document(title="P", body=b"c", version=3)
    documents.supersede(v1, v2)
    documents.supersede(v2, v3)
    db_session.flush()

    assert [d.version for d in documents.version_chain(v3)] == [1, 2, 3]


def test_next_version_for_title_increments(
    documents: KnowledgeDocumentRepository, make_document
) -> None:
    assert documents.next_version_for_title("Fresh Policy", tenant_id=TENANT) == 1
    make_document(title="Fresh Policy", body=b"a", version=1)
    assert documents.next_version_for_title("Fresh Policy", tenant_id=TENANT) == 2


def test_search_hides_superseded_documents_by_default(
    db_session: Session, documents: KnowledgeDocumentRepository, make_document
) -> None:
    make_document(title="Current", body=b"cur")
    make_document(title="Retired", body=b"ret", status=DocumentStatus.SUPERSEDED)
    db_session.flush()

    titles = {d.title for d in documents.search(tenant_id=TENANT)}
    assert "Current" in titles
    assert "Retired" not in titles

    with_retired = {
        d.title for d in documents.search(tenant_id=TENANT, include_superseded=True)
    }
    assert "Retired" in with_retired


def test_search_filters_by_effective_date(
    db_session: Session, documents: KnowledgeDocumentRepository, make_document
) -> None:
    make_document(
        title="2025 rules", body=b"2025",
        effective_date=date(2025, 1, 1), expiry_date=date(2025, 12, 31),
    )
    db_session.flush()

    assert len(documents.search(tenant_id=TENANT, effective_on=date(2025, 6, 1))) == 1
    assert len(documents.search(tenant_id=TENANT, effective_on=date(2026, 6, 1))) == 0


def test_mark_indexed_stamps_a_timestamp(
    documents: KnowledgeDocumentRepository, make_document
) -> None:
    document = make_document(status=DocumentStatus.PENDING)
    documents.mark_indexed(document)
    assert document.status == DocumentStatus.INDEXED
    assert document.indexed_at is not None


# ---------------------------------------------------------------------------
# chunk repository: the lexical leg
# ---------------------------------------------------------------------------


def test_lexical_search_ranks_matching_chunks(
    db_session: Session, chunks: KnowledgeChunkRepository, make_document, make_chunk
) -> None:
    document = make_document()
    make_chunk(document, "The daily meal allowance for domestic travel is 50 USD.", 0)
    make_chunk(document, "Lodging is capped at 200 USD per night.", 1)
    make_chunk(document, "The coffee machine is serviced on Tuesdays.", 2)
    db_session.flush()

    results = chunks.lexical_search("meal allowance", tenant_id=TENANT)
    assert results, "full-text search returned nothing"
    assert results[0][0].chunk_index == 0
    assert all(score >= 0 for _, score in results)


def test_lexical_search_is_tenant_scoped(
    db_session: Session, chunks: KnowledgeChunkRepository, make_document, make_chunk
) -> None:
    document = make_document(tenant_id=TENANT)
    make_chunk(document, "meal allowance is 50 USD", 0)
    db_session.flush()
    assert chunks.lexical_search("meal allowance", tenant_id=OTHER_TENANT) == []


def test_lexical_search_applies_metadata_filters_before_ranking(
    db_session: Session, chunks: KnowledgeChunkRepository, make_document, make_chunk
) -> None:
    """Post-filtering would silently return fewer than ``limit`` rows."""
    document = make_document()
    make_chunk(document, "meal allowance in India is 2000 INR", 0, country="IN", currency="INR")
    make_chunk(document, "meal allowance in the US is 50 USD", 1, country="US", currency="USD")
    db_session.flush()

    indian = chunks.lexical_search("meal allowance", tenant_id=TENANT, country="IN")
    assert len(indian) == 1
    assert indian[0][0].country == "IN"


def test_lexical_search_excludes_superseded_documents_by_default(
    db_session: Session, chunks: KnowledgeChunkRepository, make_document, make_chunk
) -> None:
    retired = make_document(title="Old", body=b"old", status=DocumentStatus.SUPERSEDED)
    make_chunk(retired, "the meal allowance used to be 40 USD", 0)
    db_session.flush()

    assert chunks.lexical_search("meal allowance", tenant_id=TENANT) == []
    assert chunks.lexical_search(
        "meal allowance", tenant_id=TENANT, include_superseded=True
    )


def test_lexical_search_on_blank_input_returns_nothing(
    chunks: KnowledgeChunkRepository
) -> None:
    assert chunks.lexical_search("", tenant_id=TENANT) == []
    assert chunks.lexical_search("   ", tenant_id=TENANT) == []


def test_lexical_search_handles_a_multilingual_chunk(
    db_session: Session, chunks: KnowledgeChunkRepository, make_document, make_chunk
) -> None:
    """The ``'simple'`` regconfig must not choke on non-Latin text."""
    document = make_document()
    make_chunk(document, "従業員の国内出張の食事手当は1日50米ドルです。", 0, language="ja")
    db_session.flush()
    chunks.lexical_search("食事手当", tenant_id=TENANT)   # must not raise


def test_trigram_search_matches_a_noisy_vendor_string(
    db_session: Session, chunks: KnowledgeChunkRepository, make_document, make_chunk
) -> None:
    """Full-text search cannot connect ``UBER *TRIP`` to ``Uber``; trigrams can."""
    document = make_document()
    make_chunk(document, "UBER *TRIP HELP.UBER.CO payment receipt", 0)
    db_session.flush()

    results = chunks.trigram_search("UBER TRIP HELP UBER CO payment", tenant_id=TENANT,
                                    threshold=0.2)
    assert results, "trigram search found nothing"


def test_chunk_deletion_by_document_removes_embeddings_too(
    db_session: Session, chunks: KnowledgeChunkRepository, make_document, make_chunk,
    embeddings: KnowledgeEmbeddingRepository,
) -> None:
    document = make_document()
    for i in range(2):
        chunk = make_chunk(document, f"clause {i}", i)
        embeddings.upsert(
            chunk_id=chunk.id, document_id=document.id, tenant_id=TENANT,
            vector=unit_vector(seed=i), provider="p", model_name="m", version="v1",
            spec_key=SPEC, dimensions=EMBEDDING_DIMENSIONS,
        )
    db_session.flush()

    assert chunks.delete_for_document(document.id, tenant_id=TENANT) == 2
    assert db_session.query(KnowledgeEmbedding).filter_by(document_id=document.id).count() == 0


def test_get_many_hydrates_chunks_by_id(
    db_session: Session, chunks: KnowledgeChunkRepository, make_document, make_chunk
) -> None:
    document = make_document()
    a = make_chunk(document, "alpha", 0)
    b = make_chunk(document, "beta", 1)
    db_session.flush()

    found = chunks.get_many([a.id, b.id], tenant_id=TENANT)
    assert {c.id for c in found} == {a.id, b.id}
    assert chunks.get_many([], tenant_id=TENANT) == []


# ---------------------------------------------------------------------------
# embedding repository
# ---------------------------------------------------------------------------


def test_vector_round_trips_through_pgvector(
    db_session: Session, make_document, make_chunk,
    embeddings: KnowledgeEmbeddingRepository,
) -> None:
    """Values must survive the text encoding pgvector uses on the wire."""
    document = make_document()
    chunk = make_chunk(document, "round trip", 0)
    original = unit_vector(seed=7)

    embeddings.upsert(
        chunk_id=chunk.id, document_id=document.id, tenant_id=TENANT,
        vector=original, provider="bge_m3_onnx", model_name="BAAI/bge-m3", version="v1",
        spec_key=SPEC, dimensions=EMBEDDING_DIMENSIONS,
    )
    db_session.flush()
    db_session.expire_all()

    stored = embeddings.find_for_chunk(chunk.id, SPEC, tenant_id=TENANT)
    assert stored is not None
    assert len(stored.embedding) == EMBEDDING_DIMENSIONS
    # strict=True: a length mismatch must fail, not silently compare only the shorter prefix.
    for got, want in zip(stored.embedding, original, strict=True):
        assert got == pytest.approx(want, abs=1e-6)


def test_upsert_replaces_rather_than_duplicating(
    db_session: Session, make_document, make_chunk,
    embeddings: KnowledgeEmbeddingRepository,
) -> None:
    """Re-embedding under the same version must be idempotent so a resumed run does not fail."""
    document = make_document()
    chunk = make_chunk(document, "idempotent", 0)
    for seed in (1, 2):
        embeddings.upsert(
            chunk_id=chunk.id, document_id=document.id, tenant_id=TENANT,
            vector=unit_vector(seed=seed), provider="p", model_name="m", version="v1",
            spec_key=SPEC, dimensions=EMBEDDING_DIMENSIONS, latency_ms=seed,
        )
    db_session.flush()

    rows = db_session.query(KnowledgeEmbedding).filter_by(chunk_id=chunk.id).all()
    assert len(rows) == 1
    assert rows[0].latency_ms == 2, "the later write should win"


def test_two_model_versions_coexist_for_one_chunk(
    db_session: Session, make_document, make_chunk,
    embeddings: KnowledgeEmbeddingRepository,
) -> None:
    """What makes a model migration reversible: v1 vectors survive the v2 re-index."""
    document = make_document()
    chunk = make_chunk(document, "two versions", 0)
    for spec in (SPEC, SPEC_V2):
        embeddings.upsert(
            chunk_id=chunk.id, document_id=document.id, tenant_id=TENANT,
            vector=unit_vector(seed=3), provider="p", model_name="m",
            version=spec.rsplit("@", 1)[1], spec_key=spec,
            dimensions=EMBEDDING_DIMENSIONS,
        )
    db_session.flush()

    assert embeddings.count_by_spec(tenant_id=TENANT) == {SPEC: 1, SPEC_V2: 1}


def test_delete_by_spec_key_rolls_back_one_model_version_only(
    db_session: Session, make_document, make_chunk,
    embeddings: KnowledgeEmbeddingRepository,
) -> None:
    document = make_document()
    chunk = make_chunk(document, "rollback", 0)
    for spec in (SPEC, SPEC_V2):
        embeddings.upsert(
            chunk_id=chunk.id, document_id=document.id, tenant_id=TENANT,
            vector=unit_vector(seed=4), provider="p", model_name="m",
            version="v", spec_key=spec, dimensions=EMBEDDING_DIMENSIONS,
        )
    db_session.flush()

    removed = embeddings.delete_for_document(document.id, tenant_id=TENANT, spec_key=SPEC_V2)
    assert removed == 1
    assert embeddings.count_by_spec(tenant_id=TENANT) == {SPEC: 1}


def test_chunks_missing_embedding_is_the_reindex_work_queue(
    db_session: Session, make_document, make_chunk,
    embeddings: KnowledgeEmbeddingRepository,
) -> None:
    """Makes a re-index resumable rather than all-or-nothing."""
    document = make_document()
    embedded = make_chunk(document, "already embedded", 0)
    pending = make_chunk(document, "not yet embedded", 1)
    embeddings.upsert(
        chunk_id=embedded.id, document_id=document.id, tenant_id=TENANT,
        vector=unit_vector(seed=5), provider="p", model_name="m", version="v1",
        spec_key=SPEC, dimensions=EMBEDDING_DIMENSIONS,
    )
    db_session.flush()

    missing = embeddings.chunks_missing_embedding(SPEC, tenant_id=TENANT)
    assert pending.id in missing
    assert embedded.id not in missing


def test_cosine_ordering_finds_the_nearest_vector(
    db_session: Session, make_document, make_chunk,
    embeddings: KnowledgeEmbeddingRepository,
) -> None:
    """Exercises the ``<=>`` operator and the tenant/spec pre-filter with known vectors.

    Hand-authored vectors, so this asserts the *plumbing and ordering*, independent of any model's
    embedding quality.
    """
    document = make_document()
    target_vector = unit_vector(seed=10)
    for i, seed in enumerate((10, 40, 80)):
        chunk = make_chunk(document, f"candidate {i}", i)
        embeddings.upsert(
            chunk_id=chunk.id, document_id=document.id, tenant_id=TENANT,
            vector=unit_vector(seed=seed), provider="p", model_name="m", version="v1",
            spec_key=SPEC, dimensions=EMBEDDING_DIMENSIONS,
        )
    db_session.flush()

    rows = db_session.execute(text("""
        SELECT c.chunk_index, 1 - (e.embedding <=> CAST(:q AS vector(1024))) AS similarity
        FROM knowledge_embeddings e
        JOIN knowledge_chunks c ON c.id = e.chunk_id
        WHERE e.tenant_id = :t AND e.spec_key = :spec
        ORDER BY e.embedding <=> CAST(:q AS vector(1024))
    """), {
        "q": "[" + ",".join(repr(v) for v in target_vector) + "]",
        "t": TENANT, "spec": SPEC,
    }).fetchall()

    assert rows[0][0] == 0, "the identical vector must rank first"
    assert rows[0][1] == pytest.approx(1.0, abs=1e-5)
    similarities = [row[1] for row in rows]
    assert similarities == sorted(similarities, reverse=True)


# ---------------------------------------------------------------------------
# ingestion run ledger
# ---------------------------------------------------------------------------


def test_run_records_a_successful_ingestion(
    db_session: Session, runs: KnowledgeIngestionRunRepository, make_document
) -> None:
    document = make_document()
    run = runs.start(
        tenant_id=TENANT, source_type="TRAVEL_POLICY", file_name="policy.pdf",
        checksum=document.checksum_sha256, actor_sub="sub-finance",
    )
    assert run.status == IngestionStatus.RUNNING

    runs.set_stage(run, IngestionStage.EMBED)
    assert run.stage == "EMBED"

    runs.finish(
        run, status=IngestionStatus.COMPLETED, document_id=document.id,
        chunks_created=12, embeddings_created=12, embedding_spec_key=SPEC, duration_ms=850,
    )
    assert run.succeeded
    assert run.finished_at is not None
    assert run.chunks_created == 12


def test_run_records_the_stage_a_failure_happened_in(
    db_session: Session, runs: KnowledgeIngestionRunRepository
) -> None:
    """"Parsing failed" is actionable; "ingestion failed" is not."""
    run = runs.start(tenant_id=TENANT, file_name="broken.pdf")
    runs.set_stage(run, IngestionStage.PARSE)
    runs.finish(run, status=IngestionStatus.FAILED, error_message="PdfReadError: xref broken")

    assert run.status == IngestionStatus.FAILED
    assert not run.succeeded
    assert "PdfReadError" in (run.error_message or "")


def test_run_truncates_an_enormous_error_message(
    runs: KnowledgeIngestionRunRepository
) -> None:
    """A driver traceback can be megabytes; the stage plus the first lines are what a diagnosis
    actually needs."""
    run = runs.start(tenant_id=TENANT)
    runs.finish(run, status=IngestionStatus.FAILED, error_message="x" * 10_000)
    assert run.error_message is not None
    assert len(run.error_message) == 4_000


def test_duplicate_ingestion_is_recorded_as_skipped(
    runs: KnowledgeIngestionRunRepository, make_document
) -> None:
    document = make_document()
    run = runs.start(tenant_id=TENANT, checksum=document.checksum_sha256)
    runs.finish(run, status=IngestionStatus.SKIPPED_DUPLICATE, document_id=document.id)
    assert run.succeeded, "a detected duplicate is a success, not a failure"


def test_runs_are_listed_newest_first_and_tenant_scoped(
    db_session: Session, runs: KnowledgeIngestionRunRepository
) -> None:
    for i in range(3):
        runs.finish(
            runs.start(tenant_id=TENANT, file_name=f"doc{i}.pdf"),
            status=IngestionStatus.COMPLETED,
        )
    runs.start(tenant_id=OTHER_TENANT, file_name="other.pdf")
    db_session.flush()

    listed = runs.recent(tenant_id=TENANT)
    assert len(listed) == 3
    assert all(r.tenant_id == TENANT for r in listed)


def test_run_history_survives_deletion_of_its_document(
    db_session: Session, runs: KnowledgeIngestionRunRepository, make_document
) -> None:
    """``SET NULL``: the audit trail outlives the artefact it describes."""
    document = make_document()
    run = runs.start(tenant_id=TENANT)
    runs.finish(run, status=IngestionStatus.COMPLETED, document_id=document.id)
    db_session.flush()

    db_session.delete(document)
    db_session.flush()
    db_session.refresh(run)

    assert run.document_id is None
    assert run.status == IngestionStatus.COMPLETED
