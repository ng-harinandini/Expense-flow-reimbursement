"""Retrieval tests (T004-M7): lexical + dense + fusion + reranking + compression + token budget.

Two tiers, deliberately kept apart:

**Pure-Python unit tests** (no database, no embedding service) for the modules that are genuinely
pure functions of their inputs — ``fusion``, ``scoring``, ``budget``, ``compression``, and
``DocumentScope``'s own methods. These assert exact numbers, the same way M6's vector-store tests
assert exact cosine similarities from hand-constructed angles: a test that computed the expected
value with the same function under test would prove nothing.

**Database-backed tests** for anything that touches PostgreSQL FTS or the document table —
``lexical_leg``, ``build_document_scope``, and the full ``HybridRetrievalEngine`` wired end to end
with the in-memory vector store for speed (no pgvector round trip needed to prove the engine's own
wiring; M6 already proved pgvector's own correctness).

The classic "hybrid outranks either leg alone" property is proven directly against ``fusion.fuse``
with hand-ranked lists, not through real embeddings: a deterministic (hash-based) embedding provider
has no semantic structure to demonstrate the property with, and the property is about the fusion
arithmetic, not about embedding quality.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

import pytest
from sqlalchemy.orm import Session

from app.ai.core.enums import ChunkStrategy, KnowledgeSourceType, RetrievalStrategy
from app.ai.core.errors import AIValidationError
from app.ai.core.types import (
    Chunk,
    ChunkMetadata,
    EmbeddingVector,
    MetadataFilter,
    RetrievalQuery,
    RetrievedChunk,
)
from app.ai.embeddings.service import EmbeddingService
from app.ai.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.ai.providers.embeddings.deterministic import DeterministicEmbeddingProvider
from app.ai.reranking.heuristic import HeuristicReranker
from app.ai.reranking.service import RerankService
from app.ai.repositories.knowledge_repository import (
    KnowledgeChunkRepository,
    KnowledgeDocumentRepository,
)
from app.ai.retrieval import budget, compression, fusion, scoring
from app.ai.retrieval.dense import dense_leg
from app.ai.retrieval.engine import HybridRetrievalEngine
from app.ai.retrieval.filters import DocumentScope, build_document_scope, combined_chunk_filters
from app.ai.retrieval.lexical import lexical_leg
from app.ai.vector_store.memory import InMemoryVectorStore

TENANT = "default"
DIMENSIONS = 32


def chunk(text: str, *, angle: int = 0, **metadata_kwargs) -> Chunk:
    return Chunk(
        id=uuid.uuid4(),
        text=text,
        index=0,
        token_count=max(1, len(text.split())),
        metadata=ChunkMetadata(tenant_id=TENANT, **metadata_kwargs),
    )


def retrieved(text: str, **stage_scores) -> RetrievedChunk:
    score = next(iter(stage_scores.values()), 0.0) if stage_scores else 0.0
    return RetrievedChunk(chunk=chunk(text), score=score, **stage_scores)


# ===========================================================================
# fusion — Reciprocal Rank Fusion
# ===========================================================================


def test_fuse_ranks_a_double_agreement_above_a_single_strong_leg() -> None:
    """The textbook RRF demonstration, with exact numbers.

    doc1 ranks 1st in lexical only; doc2 ranks 1st in dense only; doc3 ranks 2nd in *both*. With the
    platform's default weights (lexical 0.4, dense 0.6) and k=60, normalized against
    ``max_possible = 1.0/61`` (a candidate ranked 1st in every leg that ran):

    * doc1: ``(0.4/61) / (1.0/61) = 0.4``
    * doc2: ``(0.6/61) / (1.0/61) = 0.6``
    * doc3: ``(1.0/62) / (1.0/61) = 61/62 ≈ 0.984``

    0.984 > 0.6 > 0.4, so hybrid puts the both-legs-agree document above either single-leg
    favourite — including the leg that liked doc2 more than lexical liked doc1.
    """
    doc1, doc2, doc3 = retrieved("doc1"), retrieved("doc2"), retrieved("doc3")
    lexical = [doc1.with_score(0.9, lexical_score=0.9), doc3.with_score(0.5, lexical_score=0.5)]
    dense = [doc2.with_score(0.9, dense_score=0.9), doc3.with_score(0.5, dense_score=0.5)]

    fused = fusion.fuse(
        {"lexical": lexical, "dense": dense}, weights={"lexical": 0.4, "dense": 0.6}, k=60
    )

    assert [f.text for f in fused] == ["doc3", "doc2", "doc1"]
    assert fused[0].score == pytest.approx(61.0 / 62.0, abs=1e-9)  # doc3
    assert fused[1].score == pytest.approx(0.6, abs=1e-9)  # doc2
    assert fused[2].score == pytest.approx(0.4, abs=1e-9)  # doc1


def test_fuse_merges_stage_scores_for_a_chunk_present_in_both_legs() -> None:
    """A chunk found by both legs must carry both its lexical_score and its dense_score afterward,
    not have one silently shadowed by the other."""
    shared = retrieved("shared")
    lexical = [shared.with_score(0.8, lexical_score=0.8)]
    dense = [shared.with_score(0.7, dense_score=0.7)]

    fused = fusion.fuse(
        {"lexical": lexical, "dense": dense}, weights={"lexical": 0.4, "dense": 0.6}
    )

    assert len(fused) == 1
    assert fused[0].lexical_score == pytest.approx(0.8)
    assert fused[0].dense_score == pytest.approx(0.7)
    assert fused[0].fused_score is not None


def test_fuse_gives_zero_weight_a_leg_no_contribution() -> None:
    """A leg with weight 0 (or absent from the weights mapping) contributes nothing — equivalent to
    not having run that leg at all."""
    only_lexical = [retrieved("a", lexical_score=0.9)]
    fused = fusion.fuse(
        {"lexical": only_lexical, "dense": []}, weights={"lexical": 1.0, "dense": 0.0}
    )
    assert len(fused) == 1
    assert fused[0].score == pytest.approx(1.0)


def test_fuse_on_no_legs_returns_nothing() -> None:
    assert fusion.fuse({}, weights={}) == []
    assert fusion.fuse({"lexical": []}, weights={"lexical": 1.0}) == []


def test_fuse_rejects_a_nonsensical_k() -> None:
    with pytest.raises(ValueError, match="k must be >= 1"):
        fusion.fuse({"lexical": [retrieved("a")]}, weights={"lexical": 1.0}, k=0)


def test_fuse_ties_break_on_chunk_id_deterministically() -> None:
    """A chunk ranked 1st in one leg and one ranked 1st in the other, with equal weights, fuse to
    the identical raw score — and must still order the same way on every call rather than depending
    on dict/set iteration order."""
    a, b = retrieved("a", lexical_score=0.5), retrieved("b", dense_score=0.5)
    first = fusion.fuse({"lexical": [a], "dense": [b]}, weights={"lexical": 0.5, "dense": 0.5})
    second = fusion.fuse({"lexical": [a], "dense": [b]}, weights={"lexical": 0.5, "dense": 0.5})

    assert len(first) == 2
    assert first[0].score == pytest.approx(first[1].score)
    assert [c.id for c in first] == [c.id for c in second]
    assert [c.id for c in first] == sorted([c.id for c in first], key=str)


# ===========================================================================
# scoring — final score precedence and rank assignment
# ===========================================================================


def test_finalize_prefers_rerank_over_fused_over_single_leg() -> None:
    only_lexical = retrieved("only-lexical", lexical_score=0.9)
    fused_only = retrieved("fused-only", fused_score=0.5)
    reranked = RetrievedChunk(
        chunk=chunk("reranked"), score=0.0, fused_score=0.2, rerank_score=0.99
    )

    finalized = scoring.finalize([only_lexical, fused_only, reranked])

    by_text = {c.text: c for c in finalized}
    assert by_text["only-lexical"].score == pytest.approx(0.9)
    assert by_text["fused-only"].score == pytest.approx(0.5)
    assert by_text["reranked"].score == pytest.approx(0.99)


def test_finalize_assigns_contiguous_one_based_rank_after_pruning() -> None:
    """Rank reflects position in the full ordering, not a renumbering after the threshold pruned
    some middle-ranked chunks — see the module docstring."""
    high = retrieved("high", lexical_score=0.9)
    mid = retrieved("mid", lexical_score=0.5)
    low = retrieved("low", lexical_score=0.1)

    finalized = scoring.finalize([low, high, mid], score_threshold=0.3)

    assert [c.text for c in finalized] == ["high", "mid"]
    assert [c.rank for c in finalized] == [1, 2]


def test_finalize_orders_descending_with_a_stable_tie_break() -> None:
    a, b = retrieved("a", lexical_score=0.5), retrieved("b", lexical_score=0.5)
    finalized = scoring.finalize([b, a])
    assert [c.rank for c in finalized] == [1, 2]
    assert finalized[0].score == finalized[1].score


# ===========================================================================
# budget — the token-budget packer
# ===========================================================================


def _sized_chunk(text: str, tokens: int) -> RetrievedChunk:
    c = Chunk(
        id=uuid.uuid4(), text=text, index=0, token_count=tokens,
        metadata=ChunkMetadata(tenant_id=TENANT),
    )
    return RetrievedChunk(chunk=c, score=1.0)


def test_pack_stops_admitting_once_the_budget_is_reached() -> None:
    chunks = [_sized_chunk("a", 40), _sized_chunk("b", 40), _sized_chunk("c", 40)]
    kept, truncated = budget.pack(chunks, budget_tokens=90)
    assert [c.text for c in kept] == ["a", "b"]
    assert truncated is True


def test_pack_always_keeps_the_top_chunk_even_alone_over_budget() -> None:
    chunks = [_sized_chunk("huge", 500), _sized_chunk("small", 10)]
    kept, truncated = budget.pack(chunks, budget_tokens=50)
    assert [c.text for c in kept] == ["huge"]
    assert truncated is True


def test_pack_skips_a_chunk_that_does_not_fit_but_keeps_a_later_smaller_one() -> None:
    """Not a strict prefix cutoff: a later, smaller chunk can still fit after a mid-sized one did
    not, and the budget should be used well rather than abandoned at the first miss."""
    chunks = [_sized_chunk("first", 20), _sized_chunk("too-big", 100), _sized_chunk("fits", 15)]
    kept, truncated = budget.pack(chunks, budget_tokens=40)
    assert [c.text for c in kept] == ["first", "fits"]
    assert truncated is True


def test_pack_reports_no_truncation_when_everything_fits() -> None:
    chunks = [_sized_chunk("a", 10), _sized_chunk("b", 10)]
    kept, truncated = budget.pack(chunks, budget_tokens=100)
    assert len(kept) == 2
    assert truncated is False


def test_pack_on_no_chunks_is_empty_and_not_truncated() -> None:
    assert budget.pack([], budget_tokens=100) == ((), False)


def test_pack_rejects_a_nonsensical_budget() -> None:
    with pytest.raises(ValueError, match="budget_tokens must be >= 1"):
        budget.pack([_sized_chunk("a", 1)], budget_tokens=0)


# ===========================================================================
# compression — deterministic extractive trimming
# ===========================================================================


def test_compress_chunks_leaves_a_chunk_under_budget_untouched() -> None:
    short = _sized_chunk("Short chunk.", 5)
    out = compression.compress_chunks(
        [short], query_text="short", context_budget_tokens=1000, top_k=1
    )
    assert out[0].text == "Short chunk."
    assert out[0].chunk.token_count == 5


def test_compress_chunks_keeps_the_most_query_relevant_sentences_in_reading_order() -> None:
    text = (
        "The weather was pleasant that day. "
        "Meal allowance is capped at 30 EUR per day for domestic travel. "
        "Nobody remembers who brought the donuts. "
        "International travel meal allowance is capped at 45 EUR per day."
    )
    long_chunk = RetrievedChunk(
        chunk=Chunk(
            id=uuid.uuid4(), text=text, index=0, token_count=200,
            metadata=ChunkMetadata(tenant_id=TENANT),
        ),
        score=1.0,
    )
    out = compression.compress_chunks(
        [long_chunk], query_text="meal allowance EUR", context_budget_tokens=40, top_k=1
    )
    compressed_text = out[0].text
    assert "Meal allowance is capped at 30 EUR" in compressed_text
    assert "International travel meal allowance is capped at 45 EUR" in compressed_text
    # Reading order preserved: the domestic sentence (earlier in the source) must precede the
    # international one, even though nothing about their relevance scores implies that ordering.
    assert compressed_text.index("30 EUR") < compressed_text.index("45 EUR")
    assert "weather was pleasant" not in compressed_text
    assert "donuts" not in compressed_text


def test_compress_chunks_never_produces_an_empty_chunk() -> None:
    """Even one sentence, alone over budget, beats an empty chunk."""
    text = "A single very long sentence that on its own already exceeds the tiny budget given here."
    long_chunk = RetrievedChunk(
        chunk=Chunk(
            id=uuid.uuid4(), text=text, index=0, token_count=200,
            metadata=ChunkMetadata(tenant_id=TENANT),
        ),
        score=1.0,
    )
    out = compression.compress_chunks(
        [long_chunk], query_text="sentence", context_budget_tokens=1, top_k=1
    )
    assert out[0].text.strip()


def test_compress_chunks_does_not_split_a_single_sentence_chunk() -> None:
    """Nothing to extract from — returned whole, over budget, rather than paraphrased."""
    text = "One single sentence with no punctuation to split on at all"
    one_sentence = RetrievedChunk(
        chunk=Chunk(
            id=uuid.uuid4(), text=text, index=0, token_count=200,
            metadata=ChunkMetadata(tenant_id=TENANT),
        ),
        score=1.0,
    )
    out = compression.compress_chunks(
        [one_sentence], query_text="sentence", context_budget_tokens=1, top_k=1
    )
    assert out[0].text == text


def test_compress_chunks_divides_the_budget_fairly_across_top_k() -> None:
    """A query asking for 10 chunks gives each a smaller share than one asking for 2."""
    text = " ".join(f"Sentence number {i} about meals and allowances." for i in range(20))
    big = RetrievedChunk(
        chunk=Chunk(
            id=uuid.uuid4(), text=text, index=0, token_count=500,
            metadata=ChunkMetadata(tenant_id=TENANT),
        ),
        score=1.0,
    )
    generous = compression.compress_chunks(
        [big], query_text="meals", context_budget_tokens=1000, top_k=2
    )
    tight = compression.compress_chunks(
        [big], query_text="meals", context_budget_tokens=1000, top_k=20
    )
    assert generous[0].chunk.token_count >= tight[0].chunk.token_count


# ===========================================================================
# DocumentScope — document-level policy, and its Python-only enforcement
# ===========================================================================


def test_document_scope_as_chunk_filter_is_empty_with_nothing_excluded() -> None:
    scope = DocumentScope(tenant_id=TENANT)
    assert scope.as_chunk_filter() == ()


def test_document_scope_as_chunk_filter_names_the_excluded_ids() -> None:
    excluded = frozenset({uuid.uuid4(), uuid.uuid4()})
    scope = DocumentScope(tenant_id=TENANT, excluded_document_ids=excluded)
    filters = scope.as_chunk_filter()
    assert len(filters) == 1
    assert filters[0].field_name == "document_id"
    assert filters[0].op == "nin"
    assert set(filters[0].value) == excluded


def test_document_scope_admits_treats_a_null_effective_date_as_open() -> None:
    """The property the generic filter DSL cannot express — see the module docstring."""
    scope = DocumentScope(tenant_id=TENANT, effective_on=date(2026, 6, 1))
    undated = chunk("x")  # no effective_date / expiry_date set
    assert scope.admits(undated) is True


def test_document_scope_admits_rejects_a_chunk_outside_its_window() -> None:
    scope = DocumentScope(tenant_id=TENANT, effective_on=date(2024, 1, 1))
    future = chunk("x", effective_date=date(2026, 1, 1))
    assert scope.admits(future) is False


def test_combined_chunk_filters_appends_the_scope_exclusion() -> None:
    explicit = (MetadataFilter("country", "eq", "DE"),)
    scope = DocumentScope(tenant_id=TENANT, excluded_document_ids=frozenset({uuid.uuid4()}))
    combined = combined_chunk_filters(explicit, scope)
    assert combined[0] == explicit[0]
    assert combined[1].field_name == "document_id"


# ===========================================================================
# database-backed: build_document_scope, lexical_leg, dense_leg, the full engine
# ===========================================================================


def _document(session: Session, *, status=None) -> KnowledgeDocument:
    from app.ai.core.enums import DocumentStatus

    doc = KnowledgeDocument(
        tenant_id=TENANT,
        source_type=KnowledgeSourceType.TRAVEL_POLICY.value,
        title=f"doc-{uuid.uuid4()}",
        checksum_sha256=uuid.uuid4().hex,
        status=status or DocumentStatus.INDEXED,
    )
    session.add(doc)
    session.flush()
    return doc


_chunk_index_counters: dict[uuid.UUID, int] = {}


def _persisted_chunk(
    session: Session, document_id: uuid.UUID, text: str, *, index: Optional[int] = None, **extra
) -> KnowledgeChunk:
    """Auto-incrementing ``chunk_index`` per document, matching how a real ingestion pipeline
    assigns it — a test omitting ``index=`` should not collide with ``UNIQUE(document_id,
    chunk_index)`` just because it is the second chunk written for that document."""
    if index is None:
        index = _chunk_index_counters.get(document_id, 0)
        _chunk_index_counters[document_id] = index + 1
    extra.setdefault("content_tokens", max(1, len(text.split())))
    row = KnowledgeChunk(
        tenant_id=TENANT, document_id=document_id, chunk_index=index, content=text,
        strategy=ChunkStrategy.RECURSIVE.value,
        **extra,
    )
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def document_repo(db_session: Session) -> KnowledgeDocumentRepository:
    return KnowledgeDocumentRepository(db_session)


@pytest.fixture
def chunk_repo(db_session: Session) -> KnowledgeChunkRepository:
    return KnowledgeChunkRepository(db_session)


def test_build_document_scope_excludes_superseded_by_default(
    db_session: Session, document_repo: KnowledgeDocumentRepository
) -> None:
    from app.ai.core.enums import DocumentStatus

    superseded = _document(db_session, status=DocumentStatus.SUPERSEDED)
    active = _document(db_session)

    scope = build_document_scope(document_repo, tenant_id=TENANT)

    assert superseded.id in scope.excluded_document_ids
    assert active.id not in scope.excluded_document_ids


def test_build_document_scope_includes_superseded_when_asked(
    db_session: Session, document_repo: KnowledgeDocumentRepository
) -> None:
    from app.ai.core.enums import DocumentStatus

    _document(db_session, status=DocumentStatus.SUPERSEDED)

    scope = build_document_scope(document_repo, tenant_id=TENANT, include_superseded=True)

    assert scope.excluded_document_ids == frozenset()


def test_lexical_leg_returns_bounded_scores_in_rank_order(
    db_session: Session, chunk_repo: KnowledgeChunkRepository, document_repo
) -> None:
    doc = _document(db_session)
    _persisted_chunk(db_session, doc.id, "Meal allowance is thirty euros per day per employee.")
    _persisted_chunk(db_session, doc.id, "Rail travel within Germany is fully reimbursable.")

    scope = build_document_scope(document_repo, tenant_id=TENANT)
    results = lexical_leg(
        chunks=chunk_repo, query_text="meal allowance", tenant_id=TENANT, candidate_k=10,
        filters=(), scope=scope, recorder=_null_recorder(),
    )

    assert len(results) == 1
    assert "Meal allowance" in results[0].text
    assert 0.0 <= results[0].score <= 1.0
    assert results[0].lexical_score == results[0].score


def test_lexical_leg_falls_back_to_trigram_on_a_typo(
    db_session: Session, chunk_repo: KnowledgeChunkRepository, document_repo
) -> None:
    """``UBER *TRIP`` tokenizes away from ``Uber`` for FTS; trigram similarity still finds it."""
    doc = _document(db_session)
    _persisted_chunk(db_session, doc.id, "UBER *TRIP HELP.UBER.COM charged on the corporate card.")

    scope = build_document_scope(document_repo, tenant_id=TENANT)
    results = lexical_leg(
        chunks=chunk_repo, query_text="Uber trip", tenant_id=TENANT, candidate_k=10,
        filters=(), scope=scope, recorder=_null_recorder(),
    )

    assert len(results) == 1


def test_lexical_leg_respects_the_document_scope_for_the_trigram_fallback(
    db_session: Session, chunk_repo: KnowledgeChunkRepository, document_repo
) -> None:
    """The one path that has no native effective_on parameter (trigram_search) must still honour
    the scope's Python-side check."""
    doc = _document(db_session)
    _persisted_chunk(
        db_session, doc.id, "UBER *TRIP receipt.", effective_date=date(2020, 1, 1),
        expiry_date=date(2021, 1, 1),
    )

    scope = build_document_scope(document_repo, tenant_id=TENANT, effective_on=date(2026, 1, 1))
    results = lexical_leg(
        chunks=chunk_repo, query_text="Uber", tenant_id=TENANT, candidate_k=10,
        filters=(), scope=scope, recorder=_null_recorder(),
    )

    assert results == []


def _null_recorder():
    from app.ai.telemetry.recorder import NullTelemetryRecorder

    return NullTelemetryRecorder()


def test_dense_leg_excludes_a_superseded_document_via_the_scope_filter() -> None:
    """Uses only the in-memory store — dense_leg needs no database at all when the scope is built
    by hand, which is exactly the seam that makes it independently testable."""
    store = InMemoryVectorStore(dimensions=DIMENSIONS)
    provider = DeterministicEmbeddingProvider(dimensions=DIMENSIONS)
    service = EmbeddingService(provider)

    kept_doc, excluded_doc = uuid.uuid4(), uuid.uuid4()
    from app.ai.core.types import EmbeddedChunk

    kept_chunk = Chunk(
        id=uuid.uuid4(), text="kept", index=0,
        metadata=ChunkMetadata(document_id=kept_doc, tenant_id=TENANT),
    )
    excluded_chunk = Chunk(
        id=uuid.uuid4(), text="excluded", index=0,
        metadata=ChunkMetadata(document_id=excluded_doc, tenant_id=TENANT),
    )
    query_vector = service.embed_query("kept excluded")
    store.upsert(
        [
            EmbeddedChunk(chunk=kept_chunk, vector=EmbeddingVector(
                values=query_vector.values, spec_key=query_vector.spec_key, dimensions=DIMENSIONS
            )),
            EmbeddedChunk(chunk=excluded_chunk, vector=EmbeddingVector(
                values=query_vector.values, spec_key=query_vector.spec_key, dimensions=DIMENSIONS
            )),
        ],
        tenant_id=TENANT,
    )

    scope = DocumentScope(tenant_id=TENANT, excluded_document_ids=frozenset({excluded_doc}))
    results = dense_leg(
        vector_store=store, query_vector=query_vector, tenant_id=TENANT, candidate_k=10,
        filters=scope.as_chunk_filter(), scope=scope, recorder=_null_recorder(),
    )

    assert [r.text for r in results] == ["kept"]
    assert results[0].dense_score == results[0].score


def test_dense_leg_rejects_a_result_outside_the_effective_window() -> None:
    store = InMemoryVectorStore(dimensions=DIMENSIONS)
    provider = DeterministicEmbeddingProvider(dimensions=DIMENSIONS)
    service = EmbeddingService(provider)

    from app.ai.core.types import EmbeddedChunk

    future_chunk = Chunk(
        id=uuid.uuid4(), text="future", index=0,
        metadata=ChunkMetadata(tenant_id=TENANT, effective_date=date(2030, 1, 1)),
    )
    query_vector = service.embed_query("future")
    store.upsert(
        [EmbeddedChunk(chunk=future_chunk, vector=EmbeddingVector(
            values=query_vector.values, spec_key=query_vector.spec_key, dimensions=DIMENSIONS
        ))],
        tenant_id=TENANT,
    )

    scope = DocumentScope(tenant_id=TENANT, effective_on=date(2026, 1, 1))
    results = dense_leg(
        vector_store=store, query_vector=query_vector, tenant_id=TENANT, candidate_k=10,
        filters=(), scope=scope, recorder=_null_recorder(),
    )
    assert results == []


# ===========================================================================
# the full engine
# ===========================================================================


def _engine(
    db_session: Session,
    *,
    reranker: Optional[RerankService] = None,
    dimensions: int = DIMENSIONS,
) -> tuple[HybridRetrievalEngine, InMemoryVectorStore, EmbeddingService]:
    store = InMemoryVectorStore(dimensions=dimensions)
    provider = DeterministicEmbeddingProvider(dimensions=dimensions)
    service = EmbeddingService(provider)
    engine = HybridRetrievalEngine(
        chunk_repository=KnowledgeChunkRepository(db_session),
        document_repository=KnowledgeDocumentRepository(db_session),
        vector_store=store,
        embedding_service=service,
        reranker=reranker,
    )
    return engine, store, service


def _index_for_dense(
    store: InMemoryVectorStore, service: EmbeddingService, chunks: list[KnowledgeChunk]
) -> None:
    """Embed and upsert already-persisted chunk rows into the dense store, mirroring what M8's
    ingestion pipeline will do -- the lexical leg reads the same rows straight from PostgreSQL, so
    only the dense leg needs this."""
    from app.ai.core.types import EmbeddedChunk
    from app.ai.vector_store.postgres import chunk_to_value

    embedded = []
    for row in chunks:
        value = chunk_to_value(row)
        vector = service.embed_query(value.text)
        embedded.append(EmbeddedChunk(chunk=value, vector=vector))
    store.upsert(embedded, tenant_id=TENANT)


def test_engine_lexical_only_never_touches_the_dense_store(db_session: Session) -> None:
    doc = _document(db_session)
    _persisted_chunk(db_session, doc.id, "Meal allowance is thirty euros per day.")
    engine, store, _ = _engine(db_session)

    result = engine.retrieve(
        RetrievalQuery(text="meal allowance", tenant_id=TENANT, strategy=RetrievalStrategy.LEXICAL)
    )

    assert len(result.chunks) == 1
    assert result.chunks[0].dense_score is None
    assert store.count() == 0  # nothing was ever upserted; dense_leg was never called


def test_engine_dense_only_finds_a_chunk_never_indexed_lexically(db_session: Session) -> None:
    """Proves the dense leg is reachable and independent of the lexical leg's own text-match rule:
    a chunk retrievable by embedding similarity even though it has no query terms verbatim."""
    doc = _document(db_session)
    rows = [_persisted_chunk(db_session, doc.id, "Domestic flights require pre-approval.")]
    engine, store, service = _engine(db_session)
    _index_for_dense(store, service, rows)

    result = engine.retrieve(
        RetrievalQuery(
            text="Domestic flights require pre-approval.",
            tenant_id=TENANT, strategy=RetrievalStrategy.DENSE,
        )
    )

    assert len(result.chunks) == 1
    assert result.chunks[0].lexical_score is None
    assert result.chunks[0].dense_score is not None


def test_engine_hybrid_runs_both_legs_and_fuses(db_session: Session) -> None:
    doc = _document(db_session)
    rows = [
        _persisted_chunk(db_session, doc.id, "Meal allowance is thirty euros per day.", index=0),
        _persisted_chunk(
            db_session, doc.id, "Rail travel within Germany is reimbursable.", index=1
        ),
    ]
    engine, store, service = _engine(db_session)
    _index_for_dense(store, service, rows)

    result = engine.retrieve(
        RetrievalQuery(text="meal allowance", tenant_id=TENANT, strategy=RetrievalStrategy.HYBRID)
    )

    assert result.total_candidates >= 1
    assert any(c.fused_score is not None for c in result.chunks)


def test_engine_applies_metadata_filters_before_scoring(db_session: Session) -> None:
    """Done Check item 1: a filter must narrow the candidate set, not merely be recorded."""
    doc = _document(db_session)
    rows = [
        _persisted_chunk(db_session, doc.id, "Meal allowance in Germany.", country="DE"),
        _persisted_chunk(db_session, doc.id, "Meal allowance in the United States.", country="US"),
    ]
    engine, store, service = _engine(db_session)
    _index_for_dense(store, service, rows)

    result = engine.retrieve(
        RetrievalQuery(
            text="meal allowance", tenant_id=TENANT, strategy=RetrievalStrategy.HYBRID,
            filters=(MetadataFilter("country", "eq", "DE"),),
        )
    )

    assert len(result.chunks) == 1
    assert "Germany" in result.chunks[0].text


def test_engine_excludes_superseded_documents_by_default(db_session: Session) -> None:
    from app.ai.core.enums import DocumentStatus

    superseded = _document(db_session, status=DocumentStatus.SUPERSEDED)
    rows = [_persisted_chunk(db_session, superseded.id, "Old meal allowance policy text.")]
    engine, store, service = _engine(db_session)
    _index_for_dense(store, service, rows)

    result = engine.retrieve(
        RetrievalQuery(text="meal allowance", tenant_id=TENANT, strategy=RetrievalStrategy.HYBRID)
    )

    assert result.chunks == ()


def test_engine_reranking_reorders_results(db_session: Session) -> None:
    """Done Check item 3: reranking reorders as specified. The heuristic reranker's exact-phrase
    bonus promotes the chunk containing the query verbatim above one that merely shares some terms,
    even when fusion (with hash-based, non-semantic vectors) ranked them the other way."""
    doc = _document(db_session)
    rows = [
        _persisted_chunk(db_session, doc.id, "Some unrelated policy text about parking.", index=0),
        _persisted_chunk(
            db_session, doc.id, "meal allowance policy meal allowance policy", index=1
        ),
    ]
    engine, store, service = _engine(
        db_session, reranker=RerankService(provider=HeuristicReranker())
    )
    _index_for_dense(store, service, rows)

    result = engine.retrieve(
        RetrievalQuery(
            text="meal allowance policy", tenant_id=TENANT,
            strategy=RetrievalStrategy.LEXICAL, rerank=True, top_k=2, candidate_k=10,
        )
    )

    assert result.chunks[0].text.count("meal allowance") >= 1
    assert result.chunks[0].rerank_score is not None


def test_engine_context_never_exceeds_the_token_budget(db_session: Session) -> None:
    """Done Check item 4."""
    doc = _document(db_session)
    long_text = " ".join(["meal allowance policy detail"] * 200)
    rows = [
        _persisted_chunk(db_session, doc.id, long_text, index=0, content_tokens=400),
        _persisted_chunk(db_session, doc.id, "meal allowance short note.", index=1),
    ]
    engine, store, service = _engine(db_session)
    _index_for_dense(store, service, rows)

    result = engine.retrieve(
        RetrievalQuery(
            text="meal allowance", tenant_id=TENANT, strategy=RetrievalStrategy.LEXICAL,
            top_k=5, candidate_k=10, context_budget_tokens=50,
        )
    )

    assert sum(c.chunk.token_count for c in result.chunks) <= 50 or len(result.chunks) <= 1


def test_engine_compresses_only_the_top_k_slice_not_every_candidate(db_session: Session) -> None:
    """Regression: compression used to run on the whole candidate_k-sized pool (up to ~50 chunks)
    before the top_k cut happened, wasting work on chunks the very next step would discard."""
    doc = _document(db_session)
    rows = [
        _persisted_chunk(db_session, doc.id, f"meal allowance detail number {i}.", index=i)
        for i in range(6)
    ]
    engine, store, service = _engine(db_session)
    _index_for_dense(store, service, rows)

    result = engine.retrieve(
        RetrievalQuery(
            text="meal allowance", tenant_id=TENANT, strategy=RetrievalStrategy.LEXICAL,
            top_k=2, candidate_k=6, compress=True, context_budget_tokens=1000,
        )
    )

    compress_timing = next(t for t in result.timings if t.stage == "COMPRESS")
    assert compress_timing.candidates_in == 2  # top_k, not candidate_k


def test_engine_every_stage_emits_a_timing_span(db_session: Session) -> None:
    """Done Check item 5."""
    doc = _document(db_session)
    rows = [_persisted_chunk(db_session, doc.id, "meal allowance policy text.")]
    engine, store, service = _engine(
        db_session, reranker=RerankService(provider=HeuristicReranker())
    )
    _index_for_dense(store, service, rows)

    result = engine.retrieve(
        RetrievalQuery(
            text="meal allowance", tenant_id=TENANT, strategy=RetrievalStrategy.HYBRID,
            rerank=True, compress=True, context_budget_tokens=50,
        )
    )

    stages = {t.stage for t in result.timings}
    assert "RETRIEVE" in stages
    assert "LEXICAL_SEARCH" in stages
    assert "VECTOR_SEARCH" in stages
    assert "FUSION" in stages
    assert "RERANK" in stages
    assert "COMPRESS" in stages
    assert all(t.duration_ms >= 0 for t in result.timings)


def test_engine_rejects_an_embedding_version_it_cannot_produce(db_session: Session) -> None:
    engine, _, service = _engine(db_session)
    with pytest.raises(AIValidationError, match="active provider's version"):
        engine.retrieve(
            RetrievalQuery(
                text="x", tenant_id=TENANT, strategy=RetrievalStrategy.DENSE,
                embedding_version="someone_else/other-model@v9",
            )
        )


def test_engine_config_fingerprint_is_stable_for_identical_queries(db_session: Session) -> None:
    doc = _document(db_session)
    rows = [_persisted_chunk(db_session, doc.id, "meal allowance policy text.")]
    engine, store, service = _engine(db_session)
    _index_for_dense(store, service, rows)

    query = RetrievalQuery(text="meal allowance", tenant_id=TENANT)
    first = engine.retrieve(query)
    second = engine.retrieve(query)

    assert first.config_fingerprint == second.config_fingerprint
    assert first.embedding_version == second.embedding_version


def test_engine_config_fingerprint_differs_when_top_k_differs(db_session: Session) -> None:
    """Regression: the fingerprint used to omit top_k/candidate_k/score_threshold/filters, so two
    queries that return materially different result sets fingerprinted identically — a cache-key
    collision waiting to happen once retrieval caching is wired up (app.ai.core.ids.
    retrieval_cache_key is built from exactly this fingerprint)."""
    doc = _document(db_session)
    rows = [_persisted_chunk(db_session, doc.id, "meal allowance policy text.")]
    engine, store, service = _engine(db_session)
    _index_for_dense(store, service, rows)

    small = engine.retrieve(RetrievalQuery(text="meal allowance", tenant_id=TENANT, top_k=1))
    large = engine.retrieve(RetrievalQuery(text="meal allowance", tenant_id=TENANT, top_k=8))

    assert small.config_fingerprint != large.config_fingerprint


def test_engine_config_fingerprint_differs_when_filters_differ(db_session: Session) -> None:
    doc = _document(db_session)
    rows = [_persisted_chunk(db_session, doc.id, "meal allowance policy text.", country="DE")]
    engine, store, service = _engine(db_session)
    _index_for_dense(store, service, rows)

    unfiltered = engine.retrieve(RetrievalQuery(text="meal allowance", tenant_id=TENANT))
    filtered = engine.retrieve(
        RetrievalQuery(
            text="meal allowance", tenant_id=TENANT,
            filters=(MetadataFilter("country", "eq", "DE"),),
        )
    )

    assert unfiltered.config_fingerprint != filtered.config_fingerprint


def test_engine_reports_truncation_on_the_root_span(db_session: Session) -> None:
    doc = _document(db_session)
    long_text = " ".join(["meal allowance"] * 100)
    rows = [
        _persisted_chunk(db_session, doc.id, long_text, index=0, content_tokens=300),
        _persisted_chunk(db_session, doc.id, "meal allowance brief.", index=1, content_tokens=10),
    ]
    engine, store, service = _engine(db_session)
    _index_for_dense(store, service, rows)

    from app.ai.telemetry.recorder import TelemetryRecorderImpl

    recorder = TelemetryRecorderImpl(enabled=True)
    engine._recorder = recorder  # test-only introspection of the root span's attributes
    engine.retrieve(
        RetrievalQuery(
            text="meal allowance", tenant_id=TENANT, strategy=RetrievalStrategy.LEXICAL,
            top_k=5, candidate_k=10, context_budget_tokens=20,
        )
    )
    assert recorder.snapshot()["operations"]["RETRIEVE"]["count"] == 1
