"""Vector store tests (T004-M6).

Built around a **shared contract suite**: the clauses of the ``VectorStore`` protocol are
parametrized across every adapter that can run without external infrastructure — ``memory``,
``postgres_native`` and ``pgvector``. Storage-engine independence is the claim Task 5 makes, and one
suite executed against three genuinely different engines is the only way to test it rather than
assert it. The M5 embedding provider suite is the template; this follows it deliberately.

**Vectors here are constructed, not generated.** Every test vector is
``cos(theta) * e1 + sin(theta) * e2`` — a unit vector at a known angle from the query. So the
expected cosine similarity of a chunk is ``cos(theta)``, exactly, and an assertion can name the
number rather than compare two implementations against each other. A test that computed the expected
score with the same function the store uses would pass just as happily if that function were wrong.

**The three testable stores are checked against each other, not only against the contract.** The
cross-store equivalence tests run identical filters through the in-memory reference and through
PostgreSQL and require the same chunk ids back. That is what catches a filter translation that is
individually plausible and inconsistent with the DSL's stated semantics.

**The five external adapters cannot be run here** — no Qdrant, OpenSearch, Pinecone, Milvus or
in CI. What is tested is everything that is not the network call: each filter translator (a pure
function), the payload mapping, point-id derivation, and that constructing an adapter without
credentials fails as a configuration error rather than an import crash. Their SDK call sequences
remain unverified and are recorded as such in the plan's debt list.
"""

from __future__ import annotations

import logging
import math
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional, Sequence

import pytest
from sqlalchemy.orm import Session

from app.ai.core.enums import ChunkStrategy, DistanceMetric, KnowledgeSourceType
from app.ai.core.errors import (
    AIValidationError,
    DimensionMismatchError,
    ProviderNotConfiguredError,
)
from app.ai.core.types import Chunk, ChunkMetadata, EmbeddedChunk, EmbeddingVector, MetadataFilter
from app.ai.models.knowledge import (
    EMBEDDING_DIMENSIONS,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeEmbedding,
)
from app.ai.vector_store.base import MAX_OFFSET, MAX_TOP_K, score_from_distance
from app.ai.vector_store.capabilities import CAPABILITIES, capabilities_for, capability_matrix
from app.ai.vector_store.external import (
    chunk_from_payload,
    payload_for,
    point_id_for,
)
from app.ai.vector_store.filters import (
    FILTERABLE_FIELDS,
    FilterError,
    compile_to_sql,
    describe_filters,
    matches,
    normalize_filter,
    normalize_filters,
    sql_where,
)
from app.ai.vector_store.memory import InMemoryVectorStore
from app.ai.vector_store.pgvector import PgVectorStore
from app.ai.vector_store.postgres_native import PostgresNativeVectorStore

TENANT = "default"
OTHER_TENANT = "globex"
SPEC = "bge_m3_onnx/BAAI/bge-m3@v1"
SPEC_V2 = "bge_m3_onnx/BAAI/bge-m3@v2"

# The adapters a CI host can actually run. Every contract test runs against all three.
LOCAL_STORES = ("memory", "postgres_native", "pgvector")


# ---------------------------------------------------------------------------
# vectors with known angles
# ---------------------------------------------------------------------------


def angled_vector(degrees: float) -> tuple[float, ...]:
    """A unit vector at ``degrees`` from the query vector, in the ``e1``/``e2`` plane.

    The cosine similarity to :func:`query_vector` is therefore ``cos(degrees)`` exactly, which is
    what lets the ranking tests assert numbers instead of comparing two implementations.
    """
    radians = math.radians(degrees)
    values = [0.0] * EMBEDDING_DIMENSIONS
    values[0] = math.cos(radians)
    values[1] = math.sin(radians)
    return tuple(values)


def query_vector() -> tuple[float, ...]:
    return angled_vector(0.0)


def expected_cosine_score(degrees: float) -> float:
    """What the contract's ``[0, 1]`` score must be for a chunk at ``degrees``."""
    return max(0.0, math.cos(math.radians(degrees)))


def as_embedding(values: Sequence[float], spec_key: str = SPEC) -> EmbeddingVector:
    return EmbeddingVector(
        values=tuple(values), spec_key=spec_key, dimensions=EMBEDDING_DIMENSIONS
    )


# ---------------------------------------------------------------------------
# the corpus every contract test shares
# ---------------------------------------------------------------------------


class Corpus:
    """A small, fully specified corpus, buildable as value objects or as database rows.

    One definition serving both is what makes the suite runnable against an in-memory dict and
    against PostgreSQL without the two drifting: the ``EmbeddedChunk`` objects handed to ``upsert``
    are the same objects in both cases, and for the SQL-backed stores the matching rows are written
    first (a vector store indexes chunks, it does not create them).
    """

    def __init__(self, *, tenant_id: str = TENANT) -> None:
        self.tenant_id = tenant_id
        self.document_id = uuid.uuid4()
        self.other_document_id = uuid.uuid4()
        self.specs: list[dict[str, Any]] = [
            {
                "angle": 0.0, "country": "DE", "currency": "EUR", "category": "TRAVEL",
                "tags": ["policy", "eu"], "page": 1, "extra": {"cost_centre": "CC-1"},
                "effective": date(2026, 1, 1), "text": "Rail travel in Germany is reimbursable.",
            },
            {
                "angle": 30.0, "country": "DE", "currency": "EUR", "category": "MEALS",
                "tags": ["policy"], "page": 2, "extra": {"cost_centre": "CC-2"},
                "effective": date(2026, 1, 1), "text": "Meal allowance is 30 EUR per day.",
            },
            {
                "angle": 60.0, "country": "US", "currency": "USD", "category": "TRAVEL",
                "tags": ["policy", "us"], "page": 3, "extra": {},
                "effective": date(2025, 1, 1), "text": "Domestic flights require pre-approval.",
            },
            {
                "angle": 85.0, "country": None, "currency": None, "category": "OTHER",
                "tags": [], "page": None, "extra": {"cost_centre": "CC-1"},
                "effective": None, "text": "Appendix: contact finance for exceptions.",
            },
        ]
        self.chunks = [self._chunk(index, spec) for index, spec in enumerate(self.specs)]
        # A fifth chunk belonging to a second document, so delete-by-document can be shown to be
        # surgical rather than a truncate.
        self.other_chunk = self._chunk(
            0,
            {
                "angle": 15.0, "country": "DE", "currency": "EUR", "category": "TRAVEL",
                "tags": ["other"], "page": 1, "extra": {},
                "effective": date(2026, 1, 1), "text": "Second document, first chunk.",
            },
            document_id=self.other_document_id,
        )

    def _chunk(
        self, index: int, spec: dict[str, Any], *, document_id: Optional[uuid.UUID] = None
    ) -> Chunk:
        return Chunk(
            id=uuid.uuid4(),
            text=spec["text"],
            index=index,
            strategy=ChunkStrategy.RECURSIVE,
            token_count=10 + index,
            heading_path=("Policy", f"Section {index}"),
            metadata=ChunkMetadata(
                document_id=document_id or self.document_id,
                country=spec["country"],
                currency=spec["currency"],
                category=spec["category"],
                language="en",
                effective_date=spec["effective"],
                section=f"Section {index}",
                page_number=spec["page"],
                tenant_id=self.tenant_id,
                source_type=KnowledgeSourceType.TRAVEL_POLICY,
                tags=tuple(spec["tags"]),
                extra=dict(spec["extra"]),
            ),
        )

    # --- value objects -------------------------------------------------------

    def embedded(self, *, spec_key: str = SPEC) -> list[EmbeddedChunk]:
        return [
            EmbeddedChunk(chunk=chunk, vector=as_embedding(angled_vector(spec["angle"]), spec_key))
            for chunk, spec in zip(self.chunks, self.specs, strict=True)
        ]

    def embedded_other_document(self, *, spec_key: str = SPEC) -> list[EmbeddedChunk]:
        return [
            EmbeddedChunk(
                chunk=self.other_chunk, vector=as_embedding(angled_vector(15.0), spec_key)
            )
        ]

    def all_embedded(self, *, spec_key: str = SPEC) -> list[EmbeddedChunk]:
        return self.embedded(spec_key=spec_key) + self.embedded_other_document(spec_key=spec_key)

    def id_at(self, index: int) -> uuid.UUID:
        return self.chunks[index].id

    def ids_in_score_order(self) -> list[uuid.UUID]:
        """Chunk ids ordered by their constructed angle — the ranking every store must reproduce."""
        return [chunk.id for chunk in self.chunks]

    # --- database rows -------------------------------------------------------

    def persist(self, session: Session) -> None:
        """Write the documents and chunks the SQL-backed stores index."""
        for document_id, title in (
            (self.document_id, "Travel Policy"), (self.other_document_id, "Finance Policy"),
        ):
            session.add(
                KnowledgeDocument(
                    id=document_id,
                    tenant_id=self.tenant_id,
                    source_type=KnowledgeSourceType.TRAVEL_POLICY.value,
                    title=title,
                    checksum_sha256=uuid.uuid4().hex + uuid.uuid4().hex[:0].ljust(0, "0"),
                )
            )
        session.flush()
        for chunk in [*self.chunks, self.other_chunk]:
            session.add(_chunk_row(chunk, tenant_id=self.tenant_id))
        session.flush()


def _chunk_row(chunk: Chunk, *, tenant_id: str) -> KnowledgeChunk:
    meta = chunk.metadata
    return KnowledgeChunk(
        id=chunk.id,
        tenant_id=tenant_id,
        document_id=meta.document_id,
        chunk_index=chunk.index,
        content=chunk.text,
        content_tokens=chunk.token_count,
        strategy=chunk.strategy.value,
        section=meta.section,
        page_number=meta.page_number,
        heading_path=list(chunk.heading_path),
        country=meta.country,
        currency=meta.currency,
        category=meta.category,
        language=meta.language,
        source_type=meta.source_type.value if meta.source_type else None,
        effective_date=meta.effective_date,
        tags=list(meta.tags),
        chunk_metadata=dict(meta.extra),
    )


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(params=LOCAL_STORES)
def store_and_corpus(request: pytest.FixtureRequest, db_session: Session):
    """One ``(store, corpus)`` pair per runnable adapter — the contract suite's parametrization.

    The SQL-backed stores get their chunk rows written first and are bound to the test's session, so
    everything they write rolls back with it.
    """
    name = request.param
    corpus = Corpus()
    if name == "memory":
        store: Any = InMemoryVectorStore(dimensions=EMBEDDING_DIMENSIONS)
    else:
        corpus.persist(db_session)
        builder = PgVectorStore if name == "pgvector" else PostgresNativeVectorStore
        store = builder(dimensions=EMBEDDING_DIMENSIONS).bind(db_session)
    return store, corpus


@pytest.fixture
def memory_store() -> InMemoryVectorStore:
    return InMemoryVectorStore(dimensions=EMBEDDING_DIMENSIONS)


@contextmanager
def captured_logs(logger_name: str, level: int = logging.WARNING):
    """Capture one logger's records by attaching a handler directly to it.

    Two reasons this exists rather than ``caplog``, both learned the hard way and both invisible
    until a test is run in company:

    * ``app.core.logging.configure_logging`` removes every handler from the root logger, including
      the one pytest installs. Attaching to the named logger is unaffected.
    * The session fixtures run Alembic, which calls ``logging.config.fileConfig``. That defaults to
      ``disable_existing_loggers=True``, so **every logger created before the migrations ran is left
      with ``disabled = True``** for the rest of the session. A module imported at the top of this
      file is silenced; one imported inside a test body is not. That is why re-enabling is part of
      the setup here rather than something a test could reasonably be expected to know.
    """
    records: list[logging.LogRecord] = []

    class Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger(logger_name)
    handler = Collector(level=level)
    previous_level, previously_disabled = logger.level, logger.disabled
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.disabled = False
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        logger.disabled = previously_disabled


@pytest.fixture
def persisted_corpus(db_session: Session) -> Corpus:
    corpus = Corpus()
    corpus.persist(db_session)
    return corpus


# ===========================================================================
# THE CONTRACT SUITE — every clause, against every runnable adapter
# ===========================================================================


def test_upsert_returns_the_number_written(store_and_corpus) -> None:
    store, corpus = store_and_corpus
    assert store.upsert(corpus.embedded(), tenant_id=TENANT) == len(corpus.chunks)


def test_upsert_is_idempotent_on_chunk_id(store_and_corpus) -> None:
    """Clause 1. Re-upserting replaces in place: same count, same ids, new vector.

    The failure this guards is a duplicate row per re-index, which does not raise and does not
    corrupt anything visibly — it just returns the same chunk twice in every result set from then
    on.
    """
    store, corpus = store_and_corpus
    store.upsert(corpus.embedded(), tenant_id=TENANT)
    first = store.count(tenant_id=TENANT)

    # Re-upsert the same chunks with the nearest one moved further away.
    moved = [
        EmbeddedChunk(chunk=item.chunk, vector=as_embedding(angled_vector(70.0)))
        if index == 0 else item
        for index, item in enumerate(corpus.embedded())
    ]
    store.upsert(moved, tenant_id=TENANT)

    assert store.count(tenant_id=TENANT) == first
    hits = store.search(as_embedding(query_vector()), top_k=10, tenant_id=TENANT)
    assert len(hits) == len(corpus.chunks)
    assert len({hit.chunk_id for hit in hits}) == len(corpus.chunks)
    # The replaced vector is the one now being scored, not the original.
    replaced = next(hit for hit in hits if hit.chunk_id == corpus.id_at(0))
    assert replaced.score == pytest.approx(expected_cosine_score(70.0), abs=1e-4)


def test_search_orders_by_descending_similarity_with_scores_in_range(store_and_corpus) -> None:
    """Clause 3, against the constructed angles rather than against another implementation."""
    store, corpus = store_and_corpus
    store.upsert(corpus.embedded(), tenant_id=TENANT)

    hits = store.search(as_embedding(query_vector()), top_k=10, tenant_id=TENANT)

    assert [hit.chunk_id for hit in hits] == corpus.ids_in_score_order()
    assert all(0.0 <= hit.score <= 1.0 for hit in hits)
    for hit, spec in zip(hits, corpus.specs, strict=True):
        assert hit.score == pytest.approx(expected_cosine_score(spec["angle"]), abs=1e-4)


def test_search_hydrates_the_chunk_and_its_document(store_and_corpus) -> None:
    store, corpus = store_and_corpus
    store.upsert(corpus.embedded(), tenant_id=TENANT)

    top = store.search(as_embedding(query_vector()), top_k=1, tenant_id=TENANT)[0]

    assert top.chunk is not None
    assert top.chunk.text == corpus.specs[0]["text"]
    assert top.chunk.metadata.country == "DE"
    assert top.chunk.metadata.tags == ("policy", "eu")
    assert top.document_id == corpus.document_id


def test_filters_are_applied_before_scoring(store_and_corpus) -> None:
    """Clause 2, expressed as the observable consequence of getting it wrong.

    Two chunks match ``category == TRAVEL``. A store that filtered *after* taking the top 2 by
    similarity would return one, because the second-nearest chunk is a MEALS chunk. Returning two is
    only possible if the filter narrowed the candidate set before the ranking did.
    """
    store, corpus = store_and_corpus
    store.upsert(corpus.embedded(), tenant_id=TENANT)

    hits = store.search(
        as_embedding(query_vector()),
        top_k=2,
        tenant_id=TENANT,
        filters=[MetadataFilter("category", "eq", "TRAVEL")],
    )

    assert [hit.chunk_id for hit in hits] == [corpus.id_at(0), corpus.id_at(2)]


def test_tenant_isolation_is_absolute(store_and_corpus) -> None:
    """Clause 4. The worst failure this layer can produce, so it is tested from both directions."""
    store, corpus = store_and_corpus
    store.upsert(corpus.embedded(), tenant_id=TENANT)

    assert store.search(as_embedding(query_vector()), top_k=10, tenant_id=OTHER_TENANT) == []
    assert store.count(tenant_id=OTHER_TENANT) == 0
    assert store.get(corpus.id_at(0), tenant_id=OTHER_TENANT) is None
    assert store.get(corpus.id_at(0), tenant_id=TENANT) is not None


def test_a_vector_of_the_wrong_width_is_rejected(store_and_corpus) -> None:
    """Clause 5. Rejected, never padded or truncated."""
    store, corpus = store_and_corpus
    narrow = EmbeddingVector(values=(1.0, 0.0), spec_key=SPEC, dimensions=2)

    with pytest.raises(DimensionMismatchError):
        store.upsert(
            [EmbeddedChunk(chunk=corpus.chunks[0], vector=narrow)], tenant_id=TENANT
        )
    with pytest.raises(DimensionMismatchError):
        store.search(narrow, tenant_id=TENANT)


def test_delete_by_document_is_surgical_and_idempotent(store_and_corpus) -> None:
    """Clause 6. Removes one document's vectors, leaves the other's, and is safe to call twice."""
    store, corpus = store_and_corpus
    store.upsert(corpus.all_embedded(), tenant_id=TENANT)
    before = store.count(tenant_id=TENANT)

    removed = store.delete_by_document(corpus.document_id, tenant_id=TENANT)

    assert removed == len(corpus.chunks)
    assert store.count(tenant_id=TENANT) == before - len(corpus.chunks)
    assert store.delete_by_document(corpus.document_id, tenant_id=TENANT) == 0
    # The other document survived.
    survivors = store.search(as_embedding(query_vector()), top_k=10, tenant_id=TENANT)
    assert [hit.chunk_id for hit in survivors] == [corpus.other_chunk.id]


def test_pagination_never_repeats_or_skips_a_row(store_and_corpus) -> None:
    """Clause 7, over chunks whose vectors are *identical* so every score ties.

    Ties are the only case where paging can go wrong, so testing it with distinct scores would prove
    nothing. Four chunks at the same angle have the same score, and only a stable secondary sort key
    makes two pages of two partition them.
    """
    store, corpus = store_and_corpus
    tied = [
        EmbeddedChunk(chunk=chunk, vector=as_embedding(angled_vector(20.0)))
        for chunk in corpus.chunks
    ]
    store.upsert(tied, tenant_id=TENANT)

    first = store.search(as_embedding(query_vector()), top_k=2, offset=0, tenant_id=TENANT)
    second = store.search(as_embedding(query_vector()), top_k=2, offset=2, tenant_id=TENANT)

    page_one = [hit.chunk_id for hit in first]
    page_two = [hit.chunk_id for hit in second]
    assert len(page_one) == len(page_two) == 2
    assert not set(page_one) & set(page_two)
    assert set(page_one) | set(page_two) == {chunk.id for chunk in corpus.chunks}
    # And the same query twice gives the same page.
    assert [
        hit.chunk_id
        for hit in store.search(as_embedding(query_vector()), top_k=2, tenant_id=TENANT)
    ] == page_one


def test_get_returns_the_chunk_with_its_vector(store_and_corpus) -> None:
    store, corpus = store_and_corpus
    store.upsert(corpus.embedded(), tenant_id=TENANT)

    found = store.get(corpus.id_at(1), tenant_id=TENANT)

    assert found is not None
    assert found.chunk.id == corpus.id_at(1)
    assert found.chunk.text == corpus.specs[1]["text"]
    assert found.vector.dimensions == EMBEDDING_DIMENSIONS
    assert found.vector.spec_key == SPEC
    assert found.vector.values[0] == pytest.approx(math.cos(math.radians(30.0)), abs=1e-5)


def test_get_returns_none_for_an_unknown_chunk(store_and_corpus) -> None:
    store, _ = store_and_corpus
    assert store.get(uuid.uuid4(), tenant_id=TENANT) is None


def test_score_threshold_prunes_and_keeps_the_page_meaningful(store_and_corpus) -> None:
    """A threshold of 0.6 admits the chunks at 0 and 30 degrees (1.0 and 0.87) and no others."""
    store, corpus = store_and_corpus
    store.upsert(corpus.embedded(), tenant_id=TENANT)

    hits = store.search(
        as_embedding(query_vector()), top_k=10, tenant_id=TENANT, score_threshold=0.6
    )

    assert [hit.chunk_id for hit in hits] == [corpus.id_at(0), corpus.id_at(1)]
    assert all(hit.score >= 0.6 for hit in hits)


def test_a_reindex_keeps_two_versions_apart(store_and_corpus) -> None:
    """The property that makes a model upgrade survivable.

    Both versions' vectors coexist, and a query only ever sees the version its own query vector came
    from. Without it, a mid-re-index search compares a v2 query against v1 vectors — valid
    arithmetic, meaningless semantics, and no error anywhere.
    """
    store, corpus = store_and_corpus
    store.upsert(corpus.embedded(spec_key=SPEC), tenant_id=TENANT)
    # Under v2 the ordering is deliberately reversed.
    reversed_angles = [85.0, 60.0, 30.0, 0.0]
    store.upsert(
        [
            EmbeddedChunk(chunk=chunk, vector=as_embedding(angled_vector(angle), SPEC_V2))
            for chunk, angle in zip(corpus.chunks, reversed_angles, strict=True)
        ],
        tenant_id=TENANT,
    )

    v1 = store.search(as_embedding(query_vector(), SPEC), top_k=4, tenant_id=TENANT)
    v2 = store.search(as_embedding(query_vector(), SPEC_V2), top_k=4, tenant_id=TENANT)

    assert [hit.chunk_id for hit in v1] == corpus.ids_in_score_order()
    assert [hit.chunk_id for hit in v2] == list(reversed(corpus.ids_in_score_order()))
    assert store.count(tenant_id=TENANT) == 2 * len(corpus.chunks)


def test_a_batch_mixing_embedding_versions_is_rejected(store_and_corpus) -> None:
    """Unrecoverable once written, so it is refused rather than partially handled."""
    store, corpus = store_and_corpus
    mixed = [
        EmbeddedChunk(chunk=corpus.chunks[0], vector=as_embedding(angled_vector(0.0), SPEC)),
        EmbeddedChunk(chunk=corpus.chunks[1], vector=as_embedding(angled_vector(30.0), SPEC_V2)),
    ]
    with pytest.raises(AIValidationError, match="exactly one embedding version"):
        store.upsert(mixed, tenant_id=TENANT)


def test_an_empty_batch_is_rejected(store_and_corpus) -> None:
    store, _ = store_and_corpus
    with pytest.raises(AIValidationError, match="at least one chunk"):
        store.upsert([], tenant_id=TENANT)


def test_a_batch_repeating_a_chunk_id_is_rejected(store_and_corpus) -> None:
    """A single batch is one write, not a sequence — the last of two entries winning silently
    is exactly the ambiguity clause 1's idempotency guarantee is not meant to cover.

    On a PostgreSQL-backed store, two entries for a chunk not yet indexed both miss the
    already-exists check and both attempt the same insert, which is a raw unique-constraint
    violation without this guard.
    """
    store, corpus = store_and_corpus
    chunk = corpus.chunks[0]
    repeated = [
        EmbeddedChunk(chunk=chunk, vector=as_embedding(angled_vector(0.0))),
        EmbeddedChunk(chunk=chunk, vector=as_embedding(angled_vector(30.0))),
    ]
    with pytest.raises(AIValidationError, match="must not repeat a chunk id"):
        store.upsert(repeated, tenant_id=TENANT)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"top_k": 0}, "top_k must be >= 1"),
        ({"top_k": MAX_TOP_K + 1}, f"top_k must be <= {MAX_TOP_K}"),
        ({"offset": -1}, "offset must be >= 0"),
        ({"offset": MAX_OFFSET + 1}, f"offset must be <= {MAX_OFFSET}"),
        ({"score_threshold": 1.5}, r"within \[0, 1\]"),
        ({"score_threshold": -0.1}, r"within \[0, 1\]"),
        ({"tenant_id": "  "}, "tenant id is required"),
    ],
    ids=[
        "top_k below one", "top_k above the cap", "negative offset", "offset above the cap",
        "threshold above one", "negative threshold", "blank tenant",
    ],
)
def test_search_arguments_are_validated(store_and_corpus, kwargs, message) -> None:
    store, _ = store_and_corpus
    arguments = {"tenant_id": TENANT, **kwargs}
    with pytest.raises(AIValidationError, match=message):
        store.search(as_embedding(query_vector()), **arguments)


def test_ensure_ready_is_idempotent(store_and_corpus) -> None:
    store, _ = store_and_corpus
    store.ensure_ready()
    store.ensure_ready()


def test_is_available_and_describe_never_raise(store_and_corpus) -> None:
    store, _ = store_and_corpus
    assert store.is_available() is True
    described = store.describe()
    assert described["store"] == store.name
    assert described["dimensions"] == EMBEDDING_DIMENSIONS
    assert described["metric"] == "COSINE"


def test_capabilities_come_from_the_declared_matrix(store_and_corpus) -> None:
    """The matrix and the running adapter are the same object, so they cannot disagree."""
    store, _ = store_and_corpus
    assert store.capabilities is capabilities_for(store.name)


# ===========================================================================
# cross-store equivalence — the reference implementation versus PostgreSQL
# ===========================================================================


EQUIVALENCE_FILTERS: tuple[tuple[str, list[MetadataFilter]], ...] = (
    ("eq on a promoted column", [MetadataFilter("country", "eq", "DE")]),
    ("ne includes rows with no value", [MetadataFilter("country", "ne", "DE")]),
    ("in", [MetadataFilter("category", "in", ["TRAVEL", "MEALS"])]),
    ("nin includes rows with no value", [MetadataFilter("currency", "nin", ["EUR"])]),
    ("gte on an integer", [MetadataFilter("page_number", "gte", 2)]),
    ("lt on an integer", [MetadataFilter("page_number", "lt", 3)]),
    ("gte on a date", [MetadataFilter("effective_date", "gte", "2026-01-01")]),
    ("exists true", [MetadataFilter("country", "exists", True)]),
    ("exists false", [MetadataFilter("country", "exists", False)]),
    ("contains on a tag array", [MetadataFilter("tags", "contains", "policy")]),
    ("contains on text", [MetadataFilter("content", "contains", "allowance")]),
    ("a metadata blob key", [MetadataFilter("extra.cost_centre", "eq", "CC-1")]),
    (
        "two filters are ANDed",
        [MetadataFilter("country", "eq", "DE"), MetadataFilter("category", "eq", "MEALS")],
    ),
    ("uuid equality", [MetadataFilter("index", "eq", 0)]),
)


@pytest.mark.parametrize(
    ("label", "filters"), EQUIVALENCE_FILTERS, ids=[case[0] for case in EQUIVALENCE_FILTERS]
)
@pytest.mark.parametrize("sql_store", ["pgvector", "postgres_native"])
def test_sql_and_python_filters_select_the_same_chunks(
    db_session: Session, label: str, filters: list[MetadataFilter], sql_store: str
) -> None:
    """The SQL translation and the Python one must agree, filter by filter.

    This is where a dialect bug surfaces. Each translation is individually plausible; the DSL's
    stated semantics — particularly that ``ne`` and ``nin`` match absent values — are only actually
    enforced by requiring the two to return the same rows.
    """
    corpus = Corpus()
    corpus.persist(db_session)
    builder = PgVectorStore if sql_store == "pgvector" else PostgresNativeVectorStore
    sql = builder(dimensions=EMBEDDING_DIMENSIONS).bind(db_session)
    memory = InMemoryVectorStore(dimensions=EMBEDDING_DIMENSIONS)

    sql.upsert(corpus.embedded(), tenant_id=TENANT)
    memory.upsert(corpus.embedded(), tenant_id=TENANT)

    from_sql = sql.search(
        as_embedding(query_vector()), top_k=10, tenant_id=TENANT, filters=filters
    )
    from_memory = memory.search(
        as_embedding(query_vector()), top_k=10, tenant_id=TENANT, filters=filters
    )

    assert [hit.chunk_id for hit in from_sql] == [hit.chunk_id for hit in from_memory], label


def test_the_equivalence_cases_are_not_all_vacuous() -> None:
    """A guard on the test above: a filter matching nothing in both stores proves nothing.

    Without this, a typo in a field name would make both sides return zero rows and the equivalence
    test would pass while testing the empty set.
    """
    corpus = Corpus()
    memory = InMemoryVectorStore(dimensions=EMBEDDING_DIMENSIONS)
    memory.upsert(corpus.embedded(), tenant_id=TENANT)

    empty = [
        label
        for label, filters in EQUIVALENCE_FILTERS
        if not memory.search(
            as_embedding(query_vector()), top_k=10, tenant_id=TENANT, filters=filters
        )
    ]
    assert not empty, f"these equivalence cases match nothing and test nothing: {empty}"


def test_delete_by_document_only_touches_its_own_tenants_bucket() -> None:
    """Two tenants share one process-wide store; deleting one tenant's document must never even
    look at, let alone remove, a same-id document belonging to the other."""
    corpus_a = Corpus()
    corpus_b = Corpus()
    store = InMemoryVectorStore(dimensions=EMBEDDING_DIMENSIONS)
    store.upsert(corpus_a.embedded(), tenant_id="tenant-a")
    store.upsert(corpus_b.embedded(), tenant_id="tenant-b")

    removed = store.delete_by_document(corpus_a.document_id, tenant_id="tenant-a")

    assert removed == len(corpus_a.chunks)
    assert store.count(tenant_id="tenant-b") == len(corpus_b.chunks)


@pytest.mark.parametrize(
    "metric", [DistanceMetric.EUCLIDEAN, DistanceMetric.INNER_PRODUCT, DistanceMetric.COSINE]
)
def test_memory_store_search_works_under_every_distance_metric(metric: DistanceMetric) -> None:
    corpus = Corpus()
    store = InMemoryVectorStore(dimensions=EMBEDDING_DIMENSIONS, metric=metric)
    store.upsert(corpus.embedded(), tenant_id=TENANT)
    hits = store.search(as_embedding(query_vector()), top_k=10, tenant_id=TENANT)
    assert hits


def test_memory_store_records_a_search_span_when_a_recorder_is_wired_up() -> None:
    from app.ai.telemetry import build_recorder

    corpus = Corpus()
    recorder = build_recorder(enabled=True)
    store = InMemoryVectorStore(dimensions=EMBEDDING_DIMENSIONS, recorder=recorder)
    store.upsert(corpus.embedded(), tenant_id=TENANT)
    store.search(as_embedding(query_vector()), top_k=10, tenant_id=TENANT)
    assert recorder.snapshot()["operations"]["VECTOR_SEARCH"]["count"] == 1


def test_delete_by_document_accepts_a_document_id_as_a_string() -> None:
    corpus = Corpus()
    store = InMemoryVectorStore(dimensions=EMBEDDING_DIMENSIONS)
    store.upsert(corpus.embedded(), tenant_id=TENANT)
    removed = store.delete_by_document(str(corpus.document_id), tenant_id=TENANT)
    assert removed == len(corpus.chunks)


def test_delete_by_document_rejects_a_malformed_id() -> None:
    store = InMemoryVectorStore(dimensions=EMBEDDING_DIMENSIONS)
    with pytest.raises(ValueError, match="not a valid UUID"):
        store.delete_by_document("not-a-uuid", tenant_id=TENANT)


# ===========================================================================
# the filter DSL
# ===========================================================================


def test_an_unknown_field_is_refused_with_the_alternatives_listed() -> None:
    with pytest.raises(FilterError, match="Unknown filter field 'contry'"):
        normalize_filter(MetadataFilter("contry", "eq", "DE"))


def test_tenant_id_is_not_filterable() -> None:
    """A ``ne`` on the tenant would widen a search across tenants, so the field is refused."""
    with pytest.raises(FilterError, match="Tenancy is a mandatory argument"):
        normalize_filter(MetadataFilter("tenant_id", "ne", "acme"))


@pytest.mark.parametrize(
    "field", ["spec_key", "embedding", "status", "document_version"]
)
def test_the_other_refused_fields_explain_themselves(field: str) -> None:
    with pytest.raises(FilterError, match="not filterable"):
        normalize_filter(MetadataFilter(field, "eq", "x"))


def test_an_unknown_operator_is_refused() -> None:
    with pytest.raises(FilterError, match="not a valid FilterOp"):
        normalize_filter(MetadataFilter("country", "approximately", "DE"))


def test_an_operator_that_does_not_suit_the_field_is_refused() -> None:
    """``contains`` on an integer has no meaning, so it is an error rather than a coercion."""
    with pytest.raises(FilterError, match="does not apply to 'page_number'"):
        normalize_filter(MetadataFilter("page_number", "contains", 2))


def test_an_empty_in_list_is_refused_rather_than_matching_nothing() -> None:
    with pytest.raises(FilterError, match="empty list"):
        normalize_filter(MetadataFilter("category", "in", []))


def test_in_requires_a_list_not_a_string() -> None:
    """A bare string is iterable, and accepting it would filter on its characters."""
    with pytest.raises(FilterError, match="expects a list"):
        normalize_filter(MetadataFilter("category", "in", "TRAVEL"))


def test_a_filter_with_no_value_points_at_the_exists_operator() -> None:
    with pytest.raises(FilterError, match="Use the 'exists' operator"):
        normalize_filter(MetadataFilter("country", "eq", None))


@pytest.mark.parametrize(
    ("field", "given", "expected"),
    [
        ("effective_date", "2026-03-01", date(2026, 3, 1)),
        ("page_number", "7", 7),
        ("source_type", KnowledgeSourceType.POLICY, "POLICY"),
        ("country", 42, "42"),
    ],
)
def test_values_are_coerced_to_the_field_type(field: str, given: Any, expected: Any) -> None:
    """Filters arrive from JSON as often as from Python, so ``"2026-03-01"`` must become a date."""
    assert normalize_filter(MetadataFilter(field, "eq", given)).value == expected


def test_a_value_that_will_not_coerce_names_the_field_and_the_type() -> None:
    with pytest.raises(FilterError, match="'effective_date' expects date"):
        normalize_filter(MetadataFilter("effective_date", "eq", "not-a-date"))


def test_a_uuid_filter_accepts_a_string() -> None:
    identifier = uuid.uuid4()
    assert normalize_filter(
        MetadataFilter("document_id", "eq", str(identifier))
    ).value == identifier


def test_a_metadata_blob_filter_needs_a_key() -> None:
    with pytest.raises(FilterError, match="names no key"):
        normalize_filter(MetadataFilter("extra.", "eq", "x"))


def test_contains_is_refused_on_a_metadata_blob_key() -> None:
    """The element type of an untyped JSON value is unknown, so containment has no meaning."""
    with pytest.raises(FilterError, match="does not apply to a metadata blob key"):
        normalize_filter(MetadataFilter("extra.tags", "contains", "x"))


def test_describe_filters_reports_what_was_applied() -> None:
    """Every retrieval response says which filters ran; a short result set is else a mystery."""
    described = describe_filters([
        MetadataFilter("country", "eq", "DE"),
        MetadataFilter("extra.cost_centre", "eq", "CC-1"),
    ])
    assert described == ["country eq 'DE'", "extra.cost_centre eq 'CC-1'"]


def test_normalize_is_all_or_nothing() -> None:
    """One bad filter fails the list; the valid subset would answer a different question."""
    with pytest.raises(FilterError):
        normalize_filters([
            MetadataFilter("country", "eq", "DE"),
            MetadataFilter("nonsense", "eq", "x"),
        ])


def test_already_normalized_filters_pass_through_once() -> None:
    """Adapters may hand either form to a translator, and normalizing twice must be harmless."""
    normalized = normalize_filters([MetadataFilter("country", "eq", "DE")])
    assert compile_to_sql(normalized) and compile_to_sql(
        [MetadataFilter("country", "eq", "DE")]
    )


def test_the_filterable_field_list_is_reported_for_discoverability() -> None:
    assert "country" in FILTERABLE_FIELDS
    assert "tenant_id" not in FILTERABLE_FIELDS


def test_contains_on_text_escapes_wildcards() -> None:
    """A value containing ``%`` must match literally, not act as a wildcard."""
    corpus = Corpus()
    literal_chunk = Chunk(
        id=uuid.uuid4(), text="Reimburse 100% of the fare", index=0,
        metadata=ChunkMetadata(document_id=corpus.document_id, tenant_id=TENANT),
    )
    assert matches(literal_chunk, [MetadataFilter("content", "contains", "100%")])
    assert not matches(literal_chunk, [MetadataFilter("content", "contains", "100%zzz")])


def test_python_filter_reads_an_enum_as_its_wire_value() -> None:
    chunk = Chunk(
        id=uuid.uuid4(), text="x", index=0,
        metadata=ChunkMetadata(source_type=KnowledgeSourceType.TAX_RULE, tenant_id=TENANT),
    )
    assert matches(chunk, [MetadataFilter("source_type", "eq", "TAX_RULE")])
    assert matches(chunk, [MetadataFilter("source_type", "eq", KnowledgeSourceType.TAX_RULE)])


def test_python_filter_treats_a_nested_json_container_as_absent() -> None:
    """Matches the SQL side, where ``astext`` on a JSON object is not a comparable scalar."""
    chunk = Chunk(
        id=uuid.uuid4(), text="x", index=0,
        metadata=ChunkMetadata(tenant_id=TENANT, extra={"nested": {"a": 1}, "flag": True}),
    )
    assert not matches(chunk, [MetadataFilter("extra.nested", "eq", "anything")])
    assert matches(chunk, [MetadataFilter("extra.flag", "eq", "true")])


def test_a_filter_must_name_a_field() -> None:
    with pytest.raises(FilterError, match="must name a field"):
        normalize_filter(MetadataFilter("", "eq", "x"))


@pytest.mark.parametrize(
    ("given", "expected"),
    [("true", True), ("1", True), ("yes", True), ("false", False), ("0", False), ("no", False)],
)
def test_exists_coerces_every_documented_string_spelling(given: str, expected: bool) -> None:
    assert normalize_filter(MetadataFilter("country", "exists", given)).value is expected


def test_exists_rejects_an_unrecognized_string() -> None:
    with pytest.raises(FilterError, match="'exists' expects a boolean"):
        normalize_filter(MetadataFilter("country", "exists", "maybe"))


def test_an_int_field_refuses_a_bool_value() -> None:
    """``bool`` is an ``int`` subclass in Python; accepting it would silently coerce ``True`` to 1
    for a filter that means something else entirely (a page number, a version)."""
    with pytest.raises(FilterError, match="'page_number' expects int"):
        normalize_filter(MetadataFilter("page_number", "eq", True))


def test_a_date_field_accepts_a_datetime_by_taking_its_date_part() -> None:
    when = datetime(2026, 3, 1, 12, 30, tzinfo=timezone.utc)
    assert normalize_filter(
        MetadataFilter("effective_date", "eq", when)
    ).value == date(2026, 3, 1)


def test_sql_where_is_none_for_an_empty_filter_list() -> None:
    assert sql_where([]) is None


def test_sql_where_ands_every_clause() -> None:
    clause = sql_where([MetadataFilter("country", "eq", "DE")])
    assert clause is not None


@pytest.mark.parametrize(
    ("op", "threshold", "expected"),
    [("gt", 3, True), ("gte", 5, True), ("lt", 10, True), ("lte", 5, True)],
)
def test_python_filter_orders_numeric_comparisons(op: str, threshold: int, expected: bool) -> None:
    chunk = Chunk(
        id=uuid.uuid4(), text="x", index=0,
        metadata=ChunkMetadata(page_number=5, tenant_id=TENANT),
    )
    assert matches(chunk, [MetadataFilter("page_number", op, threshold)]) is expected


# ===========================================================================
# score normalization
# ===========================================================================


@pytest.mark.parametrize("degrees", [0.0, 15.0, 45.0, 75.0, 90.0])
def test_every_metric_scores_a_unit_vector_pair_identically(degrees: float) -> None:
    """Switching ``AI_VECTOR_DISTANCE_METRIC`` must not change what any caller sees.

    The three formulas in ``score_from_distance`` exist to make this true. If it were false, the
    retrieval engine could not fuse scores from stores configured with different metrics, and a
    threshold would mean something different per deployment.
    """
    cosine = math.cos(math.radians(degrees))
    scores = {
        DistanceMetric.COSINE: score_from_distance(1.0 - cosine, DistanceMetric.COSINE),
        DistanceMetric.INNER_PRODUCT: score_from_distance(-cosine, DistanceMetric.INNER_PRODUCT),
        DistanceMetric.EUCLIDEAN: score_from_distance(
            math.sqrt(max(0.0, 2.0 - 2.0 * cosine)), DistanceMetric.EUCLIDEAN
        ),
    }
    assert scores[DistanceMetric.COSINE] == pytest.approx(cosine, abs=1e-9)
    for metric, score in scores.items():
        assert score == pytest.approx(cosine, abs=1e-6), metric


@pytest.mark.parametrize("metric", list(DistanceMetric))
def test_score_normalization_is_monotone_for_every_metric(metric: DistanceMetric) -> None:
    """Adapters order by the engine's ascending distance and convert afterwards.

    A conversion with a bump in it would return results whose scores contradict their own ordering.
    """
    span = {
        DistanceMetric.COSINE: (0.0, 2.0),
        DistanceMetric.EUCLIDEAN: (0.0, 2.0),
        DistanceMetric.INNER_PRODUCT: (-1.0, 1.0),
    }[metric]
    step = (span[1] - span[0]) / 200.0
    scores = [score_from_distance(span[0] + index * step, metric) for index in range(201)]
    assert all(a >= b for a, b in zip(scores[:-1], scores[1:], strict=True))
    assert all(0.0 <= score <= 1.0 for score in scores)


# ===========================================================================
# store-specific behaviour
# ===========================================================================


def test_the_memory_store_reports_the_versions_it_holds(memory_store) -> None:
    corpus = Corpus()
    memory_store.upsert(corpus.embedded(spec_key=SPEC), tenant_id=TENANT)
    memory_store.upsert(corpus.embedded(spec_key=SPEC_V2), tenant_id=TENANT)
    assert memory_store.spec_keys(tenant_id=TENANT) == (SPEC, SPEC_V2)


def test_the_memory_store_can_be_cleared(memory_store) -> None:
    memory_store.upsert(Corpus().embedded(), tenant_id=TENANT)
    memory_store.clear()
    assert memory_store.count() == 0


def test_the_memory_store_needs_no_database() -> None:
    """One of its three reasons to exist: retrieval unit tests must not require PostgreSQL."""
    store = InMemoryVectorStore(dimensions=4)
    vector = EmbeddingVector(values=(1.0, 0.0, 0.0, 0.0), spec_key=SPEC, dimensions=4)
    chunk = Chunk(id=uuid.uuid4(), text="t", index=0, metadata=ChunkMetadata(tenant_id=TENANT))
    store.upsert([EmbeddedChunk(chunk=chunk, vector=vector)], tenant_id=TENANT)
    assert store.search(vector, tenant_id=TENANT)[0].score == pytest.approx(1.0)


def test_a_postgres_store_refuses_to_work_unbound() -> None:
    """Names the fix, because the alternative is a ``None`` dereference in a repository."""
    store = PgVectorStore(dimensions=EMBEDDING_DIMENSIONS)
    assert store.is_bound is False
    with pytest.raises(AIValidationError, match=r"store\.bind\(session\)"):
        store.count()


def test_binding_returns_a_new_instance_rather_than_mutating(db_session: Session) -> None:
    """The registry's copy is shared across requests; binding onto it would leak a transaction."""
    unbound = PgVectorStore(dimensions=EMBEDDING_DIMENSIONS)
    bound = unbound.bind(db_session)
    assert bound is not unbound
    assert unbound.is_bound is False
    assert bound.is_bound is True
    assert bound.dimensions == unbound.dimensions
    assert bound.metric == unbound.metric


def test_a_postgres_store_reports_unavailable_rather_than_raising() -> None:
    """A health check asking "can we retrieve?" must get an answer, not a stack trace."""
    def explode() -> Any:
        raise RuntimeError("no database")

    store = PgVectorStore(dimensions=EMBEDDING_DIMENSIONS, session_provider=explode)
    assert store.is_available() is False
    assert store.describe()["available"] is False


def test_upserting_a_vector_for_a_chunk_that_does_not_exist_names_the_chunk(
    db_session: Session,
) -> None:
    """A foreign-key error would say "violates constraint"; this says which chunk is missing."""
    corpus = Corpus()  # deliberately not persisted
    store = PgVectorStore(dimensions=EMBEDDING_DIMENSIONS).bind(db_session)
    with pytest.raises(AIValidationError, match="not present in knowledge_chunks") as caught:
        store.upsert(corpus.embedded(), tenant_id=TENANT)
    assert caught.value.details["missingCount"] == len(corpus.chunks)


def test_pgvector_writes_provenance_alongside_every_vector(
    db_session: Session, persisted_corpus: Corpus
) -> None:
    """Task 4: a vector whose model identity was lost cannot be validated or invalidated."""
    store = PgVectorStore(dimensions=EMBEDDING_DIMENSIONS).bind(db_session)
    store.upsert(persisted_corpus.embedded(), tenant_id=TENANT)

    row = db_session.query(KnowledgeEmbedding).filter_by(
        chunk_id=persisted_corpus.id_at(0)
    ).one()
    assert row.spec_key == SPEC
    assert row.embedding_provider == "bge_m3_onnx"
    assert row.embedding_model == "BAAI/bge-m3"
    assert row.embedding_version == "v1"
    assert row.dimensions == EMBEDDING_DIMENSIONS
    assert row.document_id == persisted_corpus.document_id


def test_pgvector_reads_the_document_id_from_the_chunk_row_not_the_caller(
    db_session: Session, persisted_corpus: Corpus
) -> None:
    """``delete_by_document`` searches the denormalized column, so it must match the real parent.

    A caller passing a wrong ``document_id`` in chunk metadata would otherwise write vectors that
    delete-by-document can never find — an orphan set that no operation cleans up.
    """
    store = PgVectorStore(dimensions=EMBEDDING_DIMENSIONS).bind(db_session)
    lying = [
        EmbeddedChunk(
            chunk=Chunk(
                id=persisted_corpus.id_at(0),
                text=persisted_corpus.specs[0]["text"],
                index=0,
                metadata=ChunkMetadata(document_id=uuid.uuid4(), tenant_id=TENANT),
            ),
            vector=as_embedding(angled_vector(0.0)),
        )
    ]
    store.upsert(lying, tenant_id=TENANT)

    row = db_session.query(KnowledgeEmbedding).filter_by(
        chunk_id=persisted_corpus.id_at(0)
    ).one()
    assert row.document_id == persisted_corpus.document_id


def test_upsert_many_resolves_a_batch_without_a_query_per_row(
    db_session: Session, persisted_corpus: Corpus
) -> None:
    """The reason ``upsert_many`` exists: a 400-chunk document must not cost 800 round trips."""
    from sqlalchemy import event

    store = PgVectorStore(dimensions=EMBEDDING_DIMENSIONS).bind(db_session)
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    event.listen(db_session.get_bind(), "before_cursor_execute", record)
    try:
        store.upsert(persisted_corpus.embedded(), tenant_id=TENANT)
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", record)

    selects = [s for s in statements if s.strip().upper().startswith("SELECT")]
    # One SELECT to resolve the chunks' document ids, one to find existing vectors. Not one per
    # chunk.
    assert len(selects) <= 3, statements


def test_postgres_native_refuses_a_query_larger_than_it_can_score(
    db_session: Session, persisted_corpus: Corpus
) -> None:
    """A truncated exact search is no longer exact, so it raises rather than ranking a subset."""
    store = PostgresNativeVectorStore(
        dimensions=EMBEDDING_DIMENSIONS, max_candidates=2
    ).bind(db_session)
    store.upsert(persisted_corpus.embedded(), tenant_id=TENANT)

    with pytest.raises(AIValidationError, match="above its limit of 2"):
        store.search(as_embedding(query_vector()), top_k=1, tenant_id=TENANT)


def test_postgres_native_measures_pgvector_recall(
    db_session: Session, persisted_corpus: Corpus
) -> None:
    """The measurement the exact store exists for, as a method rather than a runbook note."""
    exact = PostgresNativeVectorStore(dimensions=EMBEDDING_DIMENSIONS).bind(db_session)
    approximate = PgVectorStore(dimensions=EMBEDDING_DIMENSIONS).bind(db_session)
    exact.upsert(persisted_corpus.embedded(), tenant_id=TENANT)

    hits = approximate.search(as_embedding(query_vector()), top_k=3, tenant_id=TENANT)
    recall = exact.recall_against(
        hits, as_embedding(query_vector()), top_k=3, tenant_id=TENANT
    )
    # On a corpus this small PostgreSQL scans rather than using the index, so recall is total. The
    # value of the assertion is that the measurement itself works, not the number.
    assert recall == pytest.approx(1.0)


def test_recall_against_an_empty_corpus_is_total(db_session: Session) -> None:
    """Two stores that both found nothing agreed completely; 0.0 would read as a failure."""
    exact = PostgresNativeVectorStore(dimensions=EMBEDDING_DIMENSIONS).bind(db_session)
    assert exact.recall_against([], as_embedding(query_vector()), top_k=3) == 1.0


def test_postgres_native_rejects_a_non_positive_max_candidates() -> None:
    with pytest.raises(AIValidationError, match="max_candidates must be >= 1"):
        PostgresNativeVectorStore(dimensions=EMBEDDING_DIMENSIONS, max_candidates=0)


@pytest.mark.parametrize(
    "metric", [DistanceMetric.EUCLIDEAN, DistanceMetric.INNER_PRODUCT, DistanceMetric.COSINE]
)
def test_postgres_native_search_works_under_every_distance_metric(
    db_session: Session, persisted_corpus: Corpus, metric: DistanceMetric
) -> None:
    store = PostgresNativeVectorStore(dimensions=EMBEDDING_DIMENSIONS, metric=metric)
    store = store.bind(db_session)
    store.upsert(persisted_corpus.embedded(), tenant_id=TENANT)
    hits = store.search(as_embedding(query_vector()), top_k=3, tenant_id=TENANT)
    assert hits


def test_pgvector_warns_when_the_metric_cannot_use_the_index() -> None:
    """A silent sequential scan at scale is worse than a warning nobody reads."""
    with captured_logs("app.ai.vector_store.pgvector") as records:
        PgVectorStore(dimensions=EMBEDDING_DIMENSIONS, metric=DistanceMetric.EUCLIDEAN)
    assert any("metric_without_index" in record.getMessage() for record in records)


def test_pgvector_rejects_a_nonsensical_ef_search() -> None:
    with pytest.raises(AIValidationError, match="ef_search must be >= 1"):
        PgVectorStore(dimensions=EMBEDDING_DIMENSIONS, ef_search=0)


def test_pgvector_clamps_ef_search_at_the_engine_ceiling_instead_of_failing_the_query(
    db_session: Session, persisted_corpus: Corpus
) -> None:
    """``top_k + offset`` can reach 10,500 within the validated bounds; pgvector's own ceiling on
    ``hnsw.ef_search`` is 1000. Setting it above that is a PostgreSQL error, and without a clamp the
    whole ``SET LOCAL`` would fail and silently discard the tuning even for the part that would fit.
    """
    store = PgVectorStore(dimensions=EMBEDDING_DIMENSIONS).bind(db_session)
    store.upsert(persisted_corpus.embedded(), tenant_id=TENANT)

    with captured_logs("app.ai.vector_store.pgvector") as records:
        hits = store.search(
            as_embedding(query_vector()), top_k=500, offset=9999, tenant_id=TENANT
        )

    assert hits == []  # the corpus has 4 rows; a page this deep is simply empty, not an error
    assert any("ef_search_clamped" in r.getMessage() for r in records)


def test_pgvector_store_verify_extension_does_not_raise(db_session: Session) -> None:
    PgVectorStore(dimensions=EMBEDDING_DIMENSIONS).bind(db_session).verify_extension()


@pytest.mark.parametrize(
    ("metric", "threshold"),
    [
        (DistanceMetric.INNER_PRODUCT, 0.5),
        (DistanceMetric.EUCLIDEAN, 0.5),
        (DistanceMetric.COSINE, 0.0),
    ],
)
def test_pgvector_search_works_under_every_metric_and_a_real_threshold(
    db_session: Session, persisted_corpus: Corpus, metric: DistanceMetric, threshold: float
) -> None:
    """Exercises each metric's own distance-bound formula (the inverse of
    ``score_from_distance``), not only the default, unbounded cosine path most other tests use."""
    from app.ai.telemetry import build_recorder

    recorder = build_recorder(enabled=True)
    store = PgVectorStore(dimensions=EMBEDDING_DIMENSIONS, metric=metric, recorder=recorder)
    store = store.bind(db_session)
    store.upsert(persisted_corpus.embedded(), tenant_id=TENANT)
    store.search(
        as_embedding(query_vector()), top_k=3, tenant_id=TENANT, score_threshold=threshold
    )
    assert recorder.snapshot()["operations"]["VECTOR_SEARCH"]["count"] == 1


def test_a_dimensionality_above_the_engine_limit_is_caught_at_construction() -> None:
    """pgvector cannot index a vector wider than 2000, so a 4096-d model fails here, not in a
    migration that half-applied."""
    with pytest.raises(DimensionMismatchError):
        PgVectorStore(dimensions=4096)
    # The exact store has no index and therefore a much higher ceiling.
    assert PostgresNativeVectorStore(dimensions=4096).dimensions == 4096


def test_euclidean_and_cosine_rank_identically_on_unit_vectors(
    db_session: Session, persisted_corpus: Corpus
) -> None:
    """The practical form of the metric-agreement property, through real SQL operators."""
    cosine = PgVectorStore(dimensions=EMBEDDING_DIMENSIONS).bind(db_session)
    cosine.upsert(persisted_corpus.embedded(), tenant_id=TENANT)
    euclidean = PgVectorStore(
        dimensions=EMBEDDING_DIMENSIONS, metric=DistanceMetric.EUCLIDEAN
    ).bind(db_session)

    by_cosine = cosine.search(as_embedding(query_vector()), top_k=4, tenant_id=TENANT)
    by_euclidean = euclidean.search(as_embedding(query_vector()), top_k=4, tenant_id=TENANT)

    assert [h.chunk_id for h in by_cosine] == [h.chunk_id for h in by_euclidean]
    for left, right in zip(by_cosine, by_euclidean, strict=True):
        assert left.score == pytest.approx(right.score, abs=1e-4)


# ===========================================================================
# the capability matrix
# ===========================================================================


def test_the_matrix_covers_exactly_the_configurable_stores() -> None:
    """``AI_VECTOR_STORE``'s vocabulary and the matrix must be the same set.

    Adding a store to one and forgetting the other is how a configuration value becomes selectable
    with no declared capabilities behind it.
    """
    from typing import get_args

    from app.ai.core.config import AISettings

    configurable = set(get_args(AISettings.model_fields["VECTOR_STORE"].annotation))
    assert configurable == set(CAPABILITIES)


def test_no_adapter_claims_native_lexical_search() -> None:
    """The ``VectorStore`` protocol has no lexical method, so no adapter can honestly claim one.

    The flag is not dead: the retrieval engine reads it and, because every value is false, always
    runs its lexical leg against PostgreSQL. Pinning it here means adding a lexical capability
    requires adding a lexical method at the same time.
    """
    assert not any(entry["nativeLexicalSearch"] for entry in capability_matrix())
    assert not any(entry["nativeHybridSearch"] for entry in capability_matrix())


def test_only_the_exact_stores_claim_exact_search() -> None:
    exact = {entry["store"] for entry in capability_matrix() if entry["exactSearch"]}
    assert exact == {"memory", "postgres_native"}


def test_pinecone_is_the_only_store_without_paging() -> None:
    without = {
        entry["store"] for entry in capability_matrix() if not entry["supportsPagination"]
    }
    assert without == {"pinecone"}


def test_capabilities_for_an_unknown_store_lists_the_known_ones() -> None:
    with pytest.raises(KeyError, match="Known:"):
        capabilities_for("chroma")


# ===========================================================================
# the factory
# ===========================================================================


@pytest.fixture
def clean_registry():
    """A registry reset around each factory test, so ordering between them cannot matter."""
    from app.ai.registry.registry import vector_store_registry

    vector_store_registry.reset()
    yield vector_store_registry
    vector_store_registry.reset()


def test_all_eight_adapters_register_on_a_host_with_no_drivers(clean_registry) -> None:
    """The normal state of a CI host, and of most production hosts. Registration must not care."""
    from app.ai.vector_store.factory import register_vector_stores

    register_vector_stores()
    assert set(clean_registry.names()) == set(CAPABILITIES)


def test_describing_the_registry_constructs_nothing(clean_registry) -> None:
    """Introspection must have no side effects: no client built, no service dialled."""
    from app.ai.vector_store.factory import describe_vector_stores

    described = describe_vector_stores()
    assert {entry["name"] for entry in described} == set(CAPABILITIES)
    assert all(entry["instantiated"] is False for entry in described)
    assert all(entry["available"] is None for entry in described)


def test_registration_metadata_comes_from_the_capability_matrix(clean_registry) -> None:
    from app.ai.vector_store.factory import describe_vector_stores

    entry = next(e for e in describe_vector_stores() if e["name"] == "pinecone")
    assert entry["supportsPagination"] is False
    assert entry["requiresExternalService"] is True


def test_resolving_a_postgres_store_without_a_session_explains_why(clean_registry) -> None:
    from app.ai.vector_store.factory import resolve_vector_store

    with pytest.raises(ProviderNotConfiguredError, match="caller's transaction"):
        resolve_vector_store(name="pgvector")


def test_resolving_a_postgres_store_returns_it_bound(
    clean_registry, db_session: Session
) -> None:
    from app.ai.vector_store.factory import resolve_vector_store

    store = resolve_vector_store(name="postgres_native", session=db_session)
    assert store.is_bound is True
    assert store.count(tenant_id=TENANT) == 0


def test_resolving_the_memory_store_needs_no_session(clean_registry) -> None:
    from app.ai.vector_store.factory import resolve_vector_store

    assert resolve_vector_store(name="memory").name == "memory"


def test_an_unconfigured_external_store_is_a_configuration_error(clean_registry) -> None:
    """Not an ``ImportError``, and not a ``None`` dereference three frames into a search."""
    from app.ai.vector_store.factory import resolve_vector_store

    with pytest.raises(ProviderNotConfiguredError) as caught:
        resolve_vector_store(name="qdrant")
    assert "AI_QDRANT_URL" in caught.value.details["remedy"]


@pytest.mark.parametrize(
    "name, remedy_substring",
    [
        ("opensearch", "AI_OPENSEARCH_URL"),
        ("pinecone", "AI_PINECONE_API_KEY"),
        ("milvus", "AI_MILVUS_URI"),
        ("weaviate", "AI_WEAVIATE_URL"),
    ],
)
def test_every_unconfigured_external_store_is_a_configuration_error(
    clean_registry, name: str, remedy_substring: str
) -> None:
    """Same guarantee as the qdrant case above, for the other four external adapters — each
    factory lambda in ``app/ai/vector_store/factory.py`` must reach its adapter's own
    configuration check rather than failing earlier (an ``ImportError``) or later (a ``None``
    client used three frames into a search)."""
    from app.ai.vector_store.factory import resolve_vector_store

    with pytest.raises(ProviderNotConfiguredError) as caught:
        resolve_vector_store(name=name)
    assert remedy_substring in caught.value.details["remedy"]


def test_there_is_no_silent_fallback_to_the_memory_store(clean_registry) -> None:
    """Falling back to an empty in-process dict would make an outage look like an empty corpus."""
    from app.ai.vector_store.factory import PGVECTOR_FALLBACK

    assert "memory" not in PGVECTOR_FALLBACK
    assert PGVECTOR_FALLBACK == ("postgres_native",)


# ===========================================================================
# the external adapters — everything except the network call
# ===========================================================================


def test_the_payload_round_trips_a_chunk() -> None:
    """The external stores' equivalent of the row mapper, and the part of them that is testable."""
    corpus = Corpus()
    original = corpus.chunks[0]

    payload = payload_for(original, spec_key=SPEC, tenant_id=TENANT)
    restored = chunk_from_payload(payload)

    assert restored.id == original.id
    assert restored.text == original.text
    assert restored.index == original.index
    assert restored.strategy == original.strategy
    assert restored.heading_path == original.heading_path
    assert restored.token_count == original.token_count
    assert restored.metadata.country == original.metadata.country
    assert restored.metadata.effective_date == original.metadata.effective_date
    assert restored.metadata.tags == original.metadata.tags
    assert restored.metadata.extra == original.metadata.extra
    assert restored.metadata.source_type == original.metadata.source_type
    assert restored.metadata.tenant_id == TENANT


def test_the_payload_omits_absent_values_rather_than_storing_null() -> None:
    """``exists`` has to distinguish absent from present-and-empty, so nulls are not written."""
    corpus = Corpus()
    payload = payload_for(corpus.chunks[3], spec_key=SPEC, tenant_id=TENANT)
    assert "country" not in payload
    assert "effective_date" not in payload


def test_dates_are_stored_as_iso_strings_that_sort_chronologically() -> None:
    """Why a range filter on a date works as a string comparison in every external store."""
    corpus = Corpus()
    early = payload_for(corpus.chunks[2], spec_key=SPEC, tenant_id=TENANT)["effective_date"]
    late = payload_for(corpus.chunks[0], spec_key=SPEC, tenant_id=TENANT)["effective_date"]
    assert early == "2025-01-01"
    assert late == "2026-01-01"
    assert early < late


def test_a_point_id_is_stable_and_version_specific() -> None:
    """Stable so upsert is idempotent; version-specific so a re-index cannot overwrite the old."""
    chunk_id = uuid.uuid4()
    assert point_id_for(chunk_id, SPEC) == point_id_for(chunk_id, SPEC)
    assert point_id_for(chunk_id, SPEC) != point_id_for(chunk_id, SPEC_V2)
    assert point_id_for(chunk_id, SPEC) != point_id_for(uuid.uuid4(), SPEC)


def test_an_older_payload_still_reads() -> None:
    """A corpus in an external service outlives any one deployment; a rollback must not break it."""
    restored = chunk_from_payload({"chunk_id": str(uuid.uuid4()), "content": "text only"})
    assert restored.text == "text only"
    assert restored.metadata.country is None
    assert restored.strategy is ChunkStrategy.RECURSIVE


def test_an_unknown_source_type_is_dropped_rather_than_raising() -> None:
    """``source_type`` is deliberately open, so a newer writer's value must not break retrieval."""
    restored = chunk_from_payload(
        {"chunk_id": str(uuid.uuid4()), "content": "x", "source_type": "CARBON_POLICY"}
    )
    assert restored.metadata.source_type is None


# --- Qdrant ---


def test_qdrant_filter_always_scopes_tenant_and_version() -> None:
    from app.ai.vector_store.qdrant import to_qdrant_filter

    built = to_qdrant_filter([], tenant_id=TENANT, spec_key=SPEC)
    assert {"key": "tenant_id", "match": {"value": TENANT}} in built["must"]
    assert {"key": "spec_key", "match": {"value": SPEC}} in built["must"]


def test_qdrant_translates_each_operator() -> None:
    from app.ai.vector_store.qdrant import to_qdrant_filter

    built = to_qdrant_filter(
        [
            MetadataFilter("country", "eq", "DE"),
            MetadataFilter("currency", "ne", "USD"),
            MetadataFilter("category", "in", ["TRAVEL", "MEALS"]),
            MetadataFilter("page_number", "gte", 2),
            MetadataFilter("tags", "contains", "policy"),
            MetadataFilter("extra.cost_centre", "eq", "CC-1"),
        ],
        tenant_id=TENANT,
        spec_key=SPEC,
    )
    assert {"key": "country", "match": {"value": "DE"}} in built["must"]
    assert {"key": "currency", "match": {"value": "USD"}} in built["must_not"]
    assert {"key": "category", "match": {"any": ["TRAVEL", "MEALS"]}} in built["must"]
    assert {"key": "page_number", "range": {"gte": 2}} in built["must"]
    assert {"key": "tags", "match": {"value": "policy"}} in built["must"]
    assert {"key": "extra.cost_centre", "match": {"value": "CC-1"}} in built["must"]


def test_qdrant_renders_a_date_range_as_iso() -> None:
    from app.ai.vector_store.qdrant import to_qdrant_filter

    built = to_qdrant_filter(
        [MetadataFilter("effective_date", "gte", "2026-01-01")],
        tenant_id=TENANT, spec_key=SPEC,
    )
    assert {"key": "effective_date", "range": {"gte": "2026-01-01"}} in built["must"]


def test_qdrant_needs_a_url() -> None:
    from app.ai.vector_store.qdrant import QdrantVectorStore

    with pytest.raises(ProviderNotConfiguredError, match="AI_QDRANT_URL"):
        QdrantVectorStore(dimensions=EMBEDDING_DIMENSIONS, collection="k")


# --- OpenSearch ---


def test_opensearch_puts_the_filter_inside_the_knn_clause() -> None:
    """The single most important line in that adapter: an outer filter would post-filter."""
    from app.ai.vector_store.opensearch import VECTOR_FIELD, OpenSearchVectorStore

    captured: dict[str, Any] = {}

    class Client:
        def search(self, *, index: str, body: dict[str, Any]) -> dict[str, Any]:
            captured.update(body)
            return {"hits": {"hits": []}}

    store = OpenSearchVectorStore(
        dimensions=EMBEDDING_DIMENSIONS, collection="k", client=Client()
    )
    store.search(
        as_embedding(query_vector()),
        top_k=5, tenant_id=TENANT,
        filters=[MetadataFilter("country", "eq", "DE")],
    )

    knn = captured["query"]["knn"][VECTOR_FIELD]
    assert "filter" in knn, "the filter must be inside the knn clause, not beside it"
    assert knn["k"] >= 5
    assert {"term": {"country": "DE"}} in knn["filter"]["bool"]["filter"]


def test_opensearch_maps_filtered_fields_as_keywords_not_text() -> None:
    """A ``text`` mapping is analyzed, so ``"Travel & Meals"`` could match on either word."""
    from app.ai.vector_store.opensearch import index_mapping

    properties = index_mapping(EMBEDDING_DIMENSIONS)["mappings"]["properties"]
    assert properties["category"]["type"] == "keyword"
    assert properties["effective_date"]["type"] == "date"
    assert properties["page_number"]["type"] == "integer"
    assert properties["content"]["type"] == "text"
    assert properties["embedding"]["dimension"] == EMBEDDING_DIMENSIONS


def test_opensearch_reports_a_bulk_error_instead_of_a_silent_partial_write() -> None:
    """A bulk response carries per-item errors and a 200 status; ignoring them loses vectors."""
    from app.ai.core.errors import ProviderError
    from app.ai.vector_store.opensearch import OpenSearchVectorStore

    class Client:
        def bulk(self, *, body, refresh):  # noqa: ANN001, ARG002
            return {"errors": True, "items": [{"index": {"error": "mapper_parsing_exception"}}]}

    store = OpenSearchVectorStore(
        dimensions=EMBEDDING_DIMENSIONS, collection="k", client=Client()
    )
    with pytest.raises(ProviderError, match="mapper_parsing_exception"):
        store.upsert(Corpus().embedded(), tenant_id=TENANT)


def test_opensearch_inverts_its_documented_cosine_score() -> None:
    """``cosinesimil`` scores as ``1 / (1 + cosine_distance)``, so 1.0 must map back to 1.0."""
    from app.ai.vector_store.opensearch import OpenSearchVectorStore

    store = OpenSearchVectorStore(dimensions=4, collection="k", client=object())
    assert store._similarity_from_score(1.0) == pytest.approx(1.0)
    assert store._similarity_from_score(0.5) == pytest.approx(0.0)
    assert store._similarity_from_score(0.0) == 0.0


# --- Pinecone ---


def test_pinecone_flattening_round_trips_nested_metadata() -> None:
    from app.ai.vector_store.pinecone import flatten_metadata, unflatten_metadata

    payload = payload_for(Corpus().chunks[0], spec_key=SPEC, tenant_id=TENANT)
    flat = flatten_metadata(payload)
    assert flat["extra.cost_centre"] == "CC-1"
    assert "extra" not in flat
    assert unflatten_metadata(flat)["extra"] == {"cost_centre": "CC-1"}


def test_pinecone_drops_a_value_it_cannot_hold_rather_than_coercing_it() -> None:
    from app.ai.vector_store.pinecone import flatten_metadata

    with captured_logs("app.ai.vector_store.pinecone") as records:
        flat = flatten_metadata({"country": "DE", "weird": {1, 2}})
    assert flat == {"country": "DE"}
    assert any("metadata_dropped" in record.getMessage() for record in records)


def test_pinecone_refuses_the_operators_it_cannot_express() -> None:
    """Refusing beats returning a filter that means something adjacent but different."""
    from app.ai.vector_store.pinecone import to_pinecone_filter

    with pytest.raises(AIValidationError, match="no 'exists' operator"):
        to_pinecone_filter([MetadataFilter("country", "exists", True)], spec_key=SPEC)
    with pytest.raises(AIValidationError, match="cannot match a substring"):
        to_pinecone_filter([MetadataFilter("content", "contains", "x")], spec_key=SPEC)


def test_pinecone_filter_scopes_the_embedding_version_but_not_the_tenant() -> None:
    """Tenancy is the namespace, which a malformed filter cannot cross."""
    from app.ai.vector_store.pinecone import to_pinecone_filter

    built = to_pinecone_filter([MetadataFilter("country", "eq", "DE")], spec_key=SPEC)
    assert {"spec_key": {"$eq": SPEC}} in built["$and"]
    assert {"country": {"$eq": "DE"}} in built["$and"]
    assert not any("tenant_id" in clause for clause in built["$and"])


def test_pinecone_refuses_paging_because_it_cannot_do_it_stably() -> None:
    from app.ai.vector_store.pinecone import PineconeVectorStore

    store = PineconeVectorStore(dimensions=EMBEDDING_DIMENSIONS, collection="k", client=object())
    with pytest.raises(AIValidationError, match="does not support paging"):
        store.search(as_embedding(query_vector()), offset=10, tenant_id=TENANT)


def test_pinecone_scopes_a_query_to_the_tenant_namespace() -> None:
    from app.ai.vector_store.pinecone import PineconeVectorStore

    captured: dict[str, Any] = {}

    class Client:
        def query(self, **kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return {"matches": []}

    store = PineconeVectorStore(
        dimensions=EMBEDDING_DIMENSIONS, collection="k", client=Client()
    )
    store.search(as_embedding(query_vector()), tenant_id=OTHER_TENANT)
    assert captured["namespace"] == OTHER_TENANT


# --- Milvus ---


def test_milvus_escapes_a_value_containing_a_quote() -> None:
    """The expression is a string, so this is the injection boundary."""
    from app.ai.vector_store.milvus import to_milvus_expression

    expression = to_milvus_expression(
        [MetadataFilter("category", "eq", 'TRAVEL" or tenant_id != "')],
        tenant_id=TENANT, spec_key=SPEC,
    )
    assert r'\"' in expression
    # The injected clause is inert: it sits inside the quoted literal.
    assert expression.count("tenant_id ==") == 1


def test_milvus_expression_scopes_tenant_and_version() -> None:
    from app.ai.vector_store.milvus import to_milvus_expression

    expression = to_milvus_expression([], tenant_id=TENANT, spec_key=SPEC)
    assert f'tenant_id == "{TENANT}"' in expression
    assert f'spec_key == "{SPEC}"' in expression


def test_milvus_addresses_unpromoted_fields_through_the_json_column() -> None:
    from app.ai.vector_store.milvus import to_milvus_expression

    expression = to_milvus_expression(
        [MetadataFilter("country", "eq", "DE"), MetadataFilter("extra.cost_centre", "eq", "CC-1")],
        tenant_id=TENANT, spec_key=SPEC,
    )
    assert 'metadata["country"] == "DE"' in expression
    assert 'metadata["cost_centre"] == "CC-1"' in expression


def test_milvus_renders_a_boolean_as_a_boolean_not_an_integer() -> None:
    """``bool`` subclasses ``int``; rendering ``True`` as ``1`` is a type error on a bool field."""
    from app.ai.vector_store.milvus import _literal

    assert _literal(True) == "true"
    assert _literal(1) == "1"


def test_milvus_refuses_exists_on_a_scalar_column() -> None:
    from app.ai.vector_store.milvus import to_milvus_expression

    with pytest.raises(AIValidationError, match="no null test for a scalar"):
        to_milvus_expression(
            [MetadataFilter("country", "exists", True)], tenant_id=TENANT, spec_key=SPEC
        )


# --- Weaviate ---


def test_weaviate_similarity_conversion_is_correct_per_metric() -> None:
    """The three distance conventions Weaviate actually returns, each round-tripped to a similarity.

    Regression for a bug where the shared ``score_from_distance`` was applied uniformly: it expects
    pgvector's conventions (raw Euclidean, and ``-(a·b)`` for inner product), but Weaviate returns
    *squared* Euclidean (there is no unsquared option) and ``1 - dot`` for its dot metric — the same
    shape as cosine, not pgvector's. Applying the shared formula directly scored a perfect Euclidean
    match near zero and scored dot-metric matches negative.
    """
    from app.ai.vector_store.weaviate import WeaviateVectorStore

    cosine = WeaviateVectorStore(dimensions=4, collection="k", client=object())
    assert cosine._similarity_of(0.0) == pytest.approx(1.0)  # identical: distance 1 - cos = 0

    dot = WeaviateVectorStore(
        dimensions=4, collection="k", metric=DistanceMetric.INNER_PRODUCT, client=object()
    )
    assert dot._similarity_of(0.0) == pytest.approx(1.0)  # 1 - dot = 0 => dot = 1, a perfect match

    euclidean = WeaviateVectorStore(
        dimensions=4, collection="k", metric=DistanceMetric.EUCLIDEAN, client=object()
    )
    # A perfect match is squared-distance 0 -- must score 1.0, not near 0 from double-squaring.
    assert euclidean._similarity_of(0.0) == pytest.approx(1.0)
    # For unit vectors, squared Euclidean distance 2.0 <=> cosine similarity 0.0.
    assert euclidean._similarity_of(2.0) == pytest.approx(0.0, abs=1e-9)


def test_weaviate_chooses_the_value_key_from_the_declared_field_type() -> None:
    """Passing a date as ``valueText`` matches nothing, silently — so this is pinned."""
    from app.ai.vector_store.weaviate import value_key_for

    assert value_key_for(normalize_filter(MetadataFilter("country", "eq", "DE"))) == "valueText"
    assert value_key_for(normalize_filter(MetadataFilter("page_number", "eq", 2))) == "valueInt"
    assert value_key_for(
        normalize_filter(MetadataFilter("effective_date", "gte", "2026-01-01"))
    ) == "valueDate"


def test_weaviate_widens_a_date_to_rfc_3339() -> None:
    from app.ai.vector_store.weaviate import to_weaviate_where

    built = to_weaviate_where(
        [MetadataFilter("effective_date", "gte", "2026-01-01")],
        tenant_id=TENANT, spec_key=SPEC,
    )
    clause = next(op for op in built["operands"] if op.get("path") == ["effective_date"])
    assert clause["valueDate"].startswith("2026-01-01T00:00:00")


def test_weaviate_expands_in_to_an_or_of_equals() -> None:
    """No native list operator, so the expansion happens here and means the same thing."""
    from app.ai.vector_store.weaviate import to_weaviate_where

    built = to_weaviate_where(
        [MetadataFilter("category", "in", ["TRAVEL", "MEALS"])],
        tenant_id=TENANT, spec_key=SPEC,
    )
    expanded = next(op for op in built["operands"] if op.get("operator") == "Or")
    assert [clause["valueText"] for clause in expanded["operands"]] == ["TRAVEL", "MEALS"]


def test_weaviate_property_flattening_round_trips() -> None:
    from app.ai.vector_store.weaviate import flatten_properties, unflatten_properties

    payload = payload_for(Corpus().chunks[0], spec_key=SPEC, tenant_id=TENANT)
    flat = flatten_properties(payload)
    assert flat["extra_cost_centre"] == "CC-1"
    assert unflatten_properties(flat)["extra"] == {"cost_centre": "CC-1"}


def test_every_external_adapter_needs_its_endpoint_setting() -> None:
    """All five fail as configuration errors, never as an ``ImportError`` for a missing driver."""
    from app.ai.vector_store.milvus import MilvusVectorStore
    from app.ai.vector_store.opensearch import OpenSearchVectorStore
    from app.ai.vector_store.pinecone import PineconeVectorStore
    from app.ai.vector_store.qdrant import QdrantVectorStore
    from app.ai.vector_store.weaviate import WeaviateVectorStore

    for builder in (
        QdrantVectorStore, OpenSearchVectorStore, PineconeVectorStore,
        MilvusVectorStore, WeaviateVectorStore,
    ):
        with pytest.raises(ProviderNotConfiguredError):
            builder(dimensions=EMBEDDING_DIMENSIONS, collection="k")


def test_an_external_adapter_reports_unavailable_when_its_client_will_not_build() -> None:
    """``is_available`` must answer, so a missing driver is a ``False`` rather than a raise."""
    from app.ai.vector_store.qdrant import QdrantVectorStore

    store = QdrantVectorStore(
        dimensions=EMBEDDING_DIMENSIONS, collection="k", url="http://127.0.0.1:1"
    )
    assert store.is_available() is False


def test_an_external_adapter_needs_a_collection_name() -> None:
    from app.ai.vector_store.qdrant import QdrantVectorStore

    with pytest.raises(ProviderNotConfiguredError, match="AI_VECTOR_COLLECTION"):
        QdrantVectorStore(
            dimensions=EMBEDDING_DIMENSIONS, collection="  ", url="http://localhost:6333"
        )


# ===========================================================================
# architecture
# ===========================================================================


def test_no_module_outside_the_package_imports_an_adapter() -> None:
    """*"Nothing should directly query vector databases."*

    The architecture suite forbids importing a vector *driver* outside this package. This is the
    complementary rule: business code must not import an *adapter* either, because naming
    ``PgVectorStore`` is how a store stops being swappable even with the driver rule intact.
    """
    import ast
    from pathlib import Path

    app_root = Path(__file__).resolve().parents[1] / "app"
    adapters = {
        "app.ai.vector_store.pgvector", "app.ai.vector_store.postgres_native",
        "app.ai.vector_store.memory", "app.ai.vector_store.qdrant",
        "app.ai.vector_store.opensearch", "app.ai.vector_store.pinecone",
        "app.ai.vector_store.milvus", "app.ai.vector_store.weaviate",
    }
    violations: list[str] = []
    for path in app_root.rglob("*.py"):
        if "vector_store" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            imported: Iterable[str] = ()
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = [node.module]
            for name in imported:
                if name in adapters:
                    violations.append(f"{path.name}:{node.lineno} imports {name}")
    assert not violations, (
        "only app/ai/vector_store may name an adapter; everything else resolves one "
        "through the factory:\n" + "\n".join(violations)
    )


def test_the_spec_key_format_agrees_with_the_embedding_package() -> None:
    """``postgres.py`` parses the spec key itself rather than importing the embedding package.

    That duplication is deliberate — the store must not depend on the embedding layer to write a row
    — so this test is what keeps the two parsers in agreement.
    """
    from app.ai.embeddings.versioning import parse_spec_key
    from app.ai.vector_store.postgres import _split_spec_key

    assert _split_spec_key(SPEC) == parse_spec_key(SPEC)
    assert _split_spec_key(SPEC) == ("bge_m3_onnx", "BAAI/bge-m3", "v1")


def test_a_query_vector_reaches_postgres_as_a_typed_bound_parameter(db_session: Session) -> None:
    """``as_vector_param`` renders a bound parameter cast to ``vector(n)``, and the round trip
    works.

    Added because it did not work. M2 shipped ``as_vector_param`` with no test — the schema tests
    built their query vectors as raw SQL — and it applied the ``Vector`` bind processor to an
    already-rendered ``"[1.0,...]"`` string, iterating its characters and failing on ``"["``.
    Nothing exercised it until the first adapter did.
    """
    from sqlalchemy import select

    from app.ai.vector_store.pg_types import as_vector_param

    left = as_vector_param([1.0, 0.0, 0.0], 3)
    right = as_vector_param([0.0, 1.0, 0.0], 3)
    # Through the comparator the adapter uses, which declares a float return type. A bare
    # ``op("<=>")`` would inherit the Vector type and run the *result* processor over the distance,
    # handing back a one-element tuple.
    distance = db_session.execute(select(left.cosine_distance(right))).scalar_one()
    assert float(distance) == pytest.approx(1.0)


def test_l2_and_negative_inner_product_comparators_round_trip(db_session: Session) -> None:
    """The other two of the three pgvector distance operators, exercised the same way as the
    cosine one above."""
    from sqlalchemy import select

    from app.ai.vector_store.pg_types import as_vector_param

    left = as_vector_param([1.0, 0.0, 0.0], 3)
    right = as_vector_param([0.0, 1.0, 0.0], 3)
    l2 = db_session.execute(select(left.l2_distance(right))).scalar_one()
    assert float(l2) == pytest.approx(math.sqrt(2.0))
    inner = db_session.execute(select(left.negative_inner_product(right))).scalar_one()
    assert float(inner) == pytest.approx(0.0)


def test_vector_type_rejects_a_non_positive_dimension() -> None:
    from app.ai.vector_store.pg_types import Vector

    with pytest.raises(ValueError, match="must be >= 1"):
        Vector(0)


def test_vector_result_processor_accepts_a_list_or_tuple_directly() -> None:
    """Defensive branch: most reads see pgvector's text wire form, but a driver or test stub that
    already deserialized to a Python sequence must round-trip unchanged."""
    from app.ai.vector_store.pg_types import Vector

    process = Vector(3).result_processor(None, None)
    assert process([1, 2, 3]) == (1.0, 2.0, 3.0)
    assert process((1, 2, 3)) == (1.0, 2.0, 3.0)


def test_vector_result_processor_handles_the_empty_vector_literal() -> None:
    from app.ai.vector_store.pg_types import Vector

    process = Vector(3).result_processor(None, None)
    assert process("[]") == ()


def test_the_vector_extension_is_present_in_this_database(db_session: Session) -> None:
    """The check the pgvector adapter turns into an actionable configuration error."""
    from app.ai.vector_store.postgres import has_pgvector_extension, require_pgvector_extension

    assert has_pgvector_extension(db_session) is True
    require_pgvector_extension(db_session)  # does not raise


def test_upsert_many_refuses_a_batch_spanning_two_model_versions(db_session: Session) -> None:
    """The repository's own guard, below the store's. Its lookup is keyed on one ``spec_key``."""
    from app.ai.repositories import EmbeddingWrite, KnowledgeEmbeddingRepository

    def write(spec_key: str) -> EmbeddingWrite:
        return EmbeddingWrite(
            chunk_id=uuid.uuid4(), document_id=uuid.uuid4(), vector=[0.0] * EMBEDDING_DIMENSIONS,
            provider="p", model_name="m", version="v", spec_key=spec_key,
            dimensions=EMBEDDING_DIMENSIONS,
        )

    with pytest.raises(ValueError, match="one spec_key per batch"):
        KnowledgeEmbeddingRepository(db_session).upsert_many(
            [write(SPEC), write(SPEC_V2)], tenant_id=TENANT
        )


def test_upsert_many_on_an_empty_batch_is_a_no_op(db_session: Session) -> None:
    from app.ai.repositories import KnowledgeEmbeddingRepository

    assert KnowledgeEmbeddingRepository(db_session).upsert_many([], tenant_id=TENANT) == 0


def test_upsert_many_refuses_a_batch_repeating_a_chunk_id(db_session: Session) -> None:
    """The repository's own copy of the guard: it is public and callable directly, not only
    reachable through a vector store's ``upsert``."""
    from app.ai.repositories import EmbeddingWrite, KnowledgeEmbeddingRepository

    chunk_id = uuid.uuid4()

    def write() -> EmbeddingWrite:
        return EmbeddingWrite(
            chunk_id=chunk_id, document_id=uuid.uuid4(), vector=[0.0] * EMBEDDING_DIMENSIONS,
            provider="p", model_name="m", version="v", spec_key=SPEC,
            dimensions=EMBEDDING_DIMENSIONS,
        )

    with pytest.raises(ValueError, match="must not repeat a chunk id"):
        KnowledgeEmbeddingRepository(db_session).upsert_many([write(), write()], tenant_id=TENANT)


def test_a_malformed_spec_key_is_reported_as_a_validation_error() -> None:
    from app.ai.vector_store.postgres import _split_spec_key

    with pytest.raises(AIValidationError, match="not a valid embedding spec key"):
        _split_spec_key("nonsense")


def test_ensure_ready_never_issues_ddl_on_postgres(db_session: Session) -> None:
    """ADR-001: Alembic owns the schema, so a runtime path must not create an index.

    Enforced by watching the connection rather than by reading the method, because the failure this
    prevents — a database whose shape depends on which code path ran first — is invisible in review.
    """
    from sqlalchemy import event

    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    store = PgVectorStore(dimensions=EMBEDDING_DIMENSIONS).bind(db_session)
    event.listen(db_session.get_bind(), "before_cursor_execute", record)
    try:
        store.ensure_ready()
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", record)

    assert statements == []
