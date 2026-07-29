"""What both PostgreSQL-backed stores share: session handling, writes, and row mapping.

``pgvector`` and ``postgres_native`` read and write the same rows — ``knowledge_embeddings`` joined
to ``knowledge_chunks``. They differ in one respect: where the similarity arithmetic happens.
pgvector pushes it into the database as an indexed ``ORDER BY``; postgres_native pulls the filtered
candidates back and scores them in Python. Everything else is identical, so everything else lives
here — otherwise the two would drift and the contract suite would be validating two subtly different
stores under one name.

**The repository owns persistence; the store owns similarity.** That split is why ``upsert``,
``get``, ``count`` and ``delete_by_document`` below are thin: they delegate to
:class:`~app.ai.repositories.knowledge_repository.KnowledgeEmbeddingRepository`, which is where all
SQL for these tables belongs under the T003 contract. Vector *search* is the deliberate exception —
it is the part that has to be swappable for Qdrant or OpenSearch, so it cannot live in a repository
that only PostgreSQL will ever have.

**Sessions are explicit.** A store is constructed cheaply and then ``bind(session)`` produces the
instance that actually reads and writes, sharing the caller's transaction. It is not given a session
factory to open its own, because an ingestion run writing vectors in a transaction of its own would
commit them independently of the chunks they describe: a crash between the two leaves vectors
pointing at chunks that were rolled back. The unbound instance is still useful — it answers
``is_available()`` and ``describe()`` for the registry and the health endpoint — and every data
operation on it raises a message naming ``bind``.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional, Sequence

from sqlalchemy import Select, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.ai.core.enums import DistanceMetric, TelemetryOperation
from app.ai.core.errors import AIValidationError, ProviderError, ProviderNotConfiguredError
from app.ai.core.types import Chunk, ChunkMetadata, EmbeddedChunk, EmbeddingVector
from app.ai.interfaces.vector_store import VectorMatch, VectorStoreCapabilities
from app.ai.models.knowledge import KnowledgeChunk, KnowledgeEmbedding
from app.ai.repositories.knowledge_repository import EmbeddingWrite, KnowledgeEmbeddingRepository
from app.ai.vector_store.base import BaseVectorStore, coerce_source_type, coerce_strategy
from app.ai.vector_store.filters import NormalizedFilter, compile_to_sql
from app.core.logging import get_logger

logger = get_logger(__name__)


class PostgresVectorStoreBase(BaseVectorStore):
    """Common implementation for the PostgreSQL-backed stores. ``search`` stays abstract."""

    def __init__(
        self,
        *,
        name: str,
        dimensions: int,
        capabilities: VectorStoreCapabilities,
        metric: DistanceMetric = DistanceMetric.COSINE,
        session: Optional[Session] = None,
        session_provider: Any = None,
        recorder: Any = None,
    ) -> None:
        super().__init__(
            name=name, dimensions=dimensions, capabilities=capabilities,
            metric=metric, recorder=recorder,
        )
        self._session = session
        # Used only by ``is_available``, which needs a connection it is allowed to throw away.
        self._session_provider = session_provider

    # --- session plumbing ----------------------------------------------------

    def bind(self, session: Session) -> "PostgresVectorStoreBase":
        """A copy of this store bound to ``session``.

        A new instance rather than mutation: the unbound one is a registry singleton shared across
        requests, and binding a session onto it would leak one request's transaction into the next.
        """
        clone = self._clone()
        clone._session = session
        return clone

    def _clone(self) -> "PostgresVectorStoreBase":
        """Hook for subclasses whose constructors take extra arguments."""
        return type(self)(
            dimensions=self.dimensions,
            metric=self.metric,
            session_provider=self._session_provider,
            recorder=self._recorder,
        )

    @property
    def is_bound(self) -> bool:
        return self._session is not None

    def _require_session(self) -> Session:
        if self._session is None:
            raise AIValidationError(
                f"The '{self.name}' store needs a database session. Call "
                "store.bind(session) and use the returned instance, so its writes share the "
                "caller's transaction.",
                details={"store": self.name},
            )
        return self._session

    def _embeddings(self) -> KnowledgeEmbeddingRepository:
        return KnowledgeEmbeddingRepository(self._require_session())

    # --- lifecycle -----------------------------------------------------------

    def is_available(self) -> bool:
        """Whether the database answers and has the ``vector`` extension.

        Never raises: the registry treats an exception as unavailable, and a health endpoint asking
        "can we retrieve?" must get an answer rather than a stack trace. A bound session is reused;
        otherwise a throwaway one is opened, which is safe because this reads nothing.
        """
        session = self._session
        try:
            if session is not None:
                return self._probe(session)
            if self._session_provider is None:
                return False
            probe_session = self._session_provider()
            try:
                return self._probe(probe_session)
            finally:
                probe_session.close()
        except Exception as exc:
            logger.warning(
                "ai.vector_store.unavailable",
                extra={"store": self.name, "error": f"{type(exc).__name__}: {exc}"},
            )
            return False

    def _probe(self, session: Session) -> bool:
        """One cheap read that proves the connection works *and* the table is present.

        ``LIMIT 1`` rather than ``COUNT(*)``: a health check that gets slower as the corpus grows is
        a health check that eventually reports an outage it caused.
        """
        session.execute(select(KnowledgeEmbedding.id).limit(1)).first()
        return True

    def ensure_ready(self) -> None:
        """Deliberately a no-op.

        Alembic is the sole owner of the schema (ADR-001), so a runtime path must never issue DDL.
        The tables, the ``vector`` extension and the HNSW index are created by migration
        ``0004_ai_knowledge_platform``. A store that created its own index here would produce a
        database whose shape depends on which code path ran first, and an autogenerate diff that can
        never be empty.
        """

    # --- writes --------------------------------------------------------------

    def upsert(self, chunks: Sequence[EmbeddedChunk], *, tenant_id: str = "default") -> int:
        """Persist vectors for chunks that already exist in ``knowledge_chunks``.

        The chunk rows are *not* created here. A vector store indexes chunks; it does not get to
        invent their text, and the foreign key would reject it. Missing chunks are reported by
        id rather than left to surface as an integrity error, because "which chunk?" is the only
        question worth answering at that point.
        """
        spec_key = self._validate_batch(chunks)
        session = self._require_session()

        with self._span(TelemetryOperation.VECTOR_UPSERT, count=len(chunks)):
            known = self._existing_chunk_document_ids(
                session, [c.id for c in chunks], tenant_id=tenant_id
            )
            missing = [str(c.id) for c in chunks if c.id not in known]
            if missing:
                raise AIValidationError(
                    f"{len(missing)} chunk(s) are not present in knowledge_chunks for tenant "
                    f"'{tenant_id}', so their vectors cannot be indexed. Persist the chunks first.",
                    details={"missingChunkIds": missing[:20], "missingCount": len(missing)},
                )

            writes = [self._to_write(chunk, known[chunk.id], spec_key) for chunk in chunks]
            try:
                return self._embeddings().upsert_many(writes, tenant_id=tenant_id)
            except SQLAlchemyError as exc:
                raise ProviderError(self.name, f"vector upsert failed: {exc}") from exc

    def _to_write(
        self, chunk: EmbeddedChunk, document_id: uuid.UUID, spec_key: str
    ) -> EmbeddingWrite:
        provider, model_name, version = _split_spec_key(spec_key)
        return EmbeddingWrite(
            chunk_id=chunk.id,
            document_id=document_id,
            vector=chunk.vector.values,
            provider=provider,
            model_name=model_name,
            version=version,
            spec_key=spec_key,
            dimensions=chunk.vector.dimensions,
        )

    def _existing_chunk_document_ids(
        self, session: Session, chunk_ids: Sequence[uuid.UUID], *, tenant_id: str
    ) -> dict[uuid.UUID, uuid.UUID]:
        """Chunk id -> document id, for the chunks that exist in this tenant.

        Read from the chunk row, not from ``chunk.metadata.document_id``: the denormalized
        ``knowledge_embeddings.document_id`` is what ``delete_by_document`` searches on, so it must
        agree with the chunk's actual parent, not with whatever the caller believed it was.
        """
        rows = session.execute(
            select(KnowledgeChunk.id, KnowledgeChunk.document_id).where(
                KnowledgeChunk.tenant_id == tenant_id,
                KnowledgeChunk.id.in_(list(chunk_ids)),
            )
        ).all()
        return {row[0]: row[1] for row in rows}

    def delete_by_document(self, document_id: Any, *, tenant_id: str = "default") -> int:
        """Delete every vector of a document, all versions. Idempotent: 0 the second time."""
        return self._embeddings().delete_for_document(
            _as_uuid(document_id), tenant_id=tenant_id
        )

    # --- reads ---------------------------------------------------------------

    def get(self, chunk_id: Any, *, tenant_id: str = "default") -> Optional[EmbeddedChunk]:
        """One chunk with its vector. Version-agnostic; ``search`` isolates versions."""
        key = _as_uuid(chunk_id)
        stmt = (
            select(KnowledgeChunk, KnowledgeEmbedding)
            .join(KnowledgeEmbedding, KnowledgeEmbedding.chunk_id == KnowledgeChunk.id)
            .where(
                KnowledgeChunk.tenant_id == tenant_id,
                KnowledgeChunk.id == key,
            )
            # Deterministic pick when a chunk has vectors under two versions mid-re-index.
            .order_by(KnowledgeEmbedding.spec_key)
            .limit(1)
        )
        try:
            row = self._require_session().execute(stmt).first()
        except SQLAlchemyError as exc:
            raise ProviderError(self.name, f"get failed: {exc}") from exc
        if row is None:
            return None
        chunk_row, embedding_row = row
        return EmbeddedChunk(
            chunk=chunk_to_value(chunk_row),
            vector=EmbeddingVector(
                values=tuple(embedding_row.embedding or ()),
                spec_key=embedding_row.spec_key,
                dimensions=embedding_row.dimensions,
            ),
        )

    def count(self, *, tenant_id: Optional[str] = None) -> int:
        stmt = select(func.count()).select_from(KnowledgeEmbedding)
        if tenant_id is not None:
            stmt = stmt.where(KnowledgeEmbedding.tenant_id == tenant_id)
        try:
            return int(self._require_session().execute(stmt).scalar_one())
        except SQLAlchemyError as exc:
            raise ProviderError(self.name, f"count failed: {exc}") from exc

    # --- shared query construction -------------------------------------------

    def _candidate_query(
        self,
        *,
        spec_key: str,
        tenant_id: str,
        filters: Sequence[NormalizedFilter],
    ) -> Select:
        """``knowledge_embeddings`` joined to its chunk, restricted to one tenant and one version.

        The two ``WHERE`` clauses that are never optional:

        * ``tenant_id`` — on the embedding row, which is the row being ranked. Filtering the joined
          chunk instead would leave a mis-tenanted embedding row able to surface its chunk.
        * ``spec_key`` — taken from the query vector, so a search during a re-index compares only
          against vectors from the same model. Without it a chunk appears twice with incomparable
          distances, and the better-scoring *stale* vector wins.

        Metadata filters join the same ``WHERE``, which is what puts filtering before scoring: the
        planner restricts the candidate set and only then ranks it.
        """
        stmt = (
            select(KnowledgeChunk, KnowledgeEmbedding)
            .join(KnowledgeEmbedding, KnowledgeEmbedding.chunk_id == KnowledgeChunk.id)
            .where(
                KnowledgeEmbedding.tenant_id == tenant_id,
                KnowledgeEmbedding.spec_key == spec_key,
            )
        )
        for clause in compile_to_sql(filters, model=KnowledgeChunk):
            stmt = stmt.where(clause)
        return stmt

    def _match(self, chunk_row: KnowledgeChunk, distance: float) -> VectorMatch:
        """Build a contract-shaped match, carrying the hydrated chunk and the raw distance.

        ``raw_distance`` is kept alongside the normalized score because a recall investigation needs
        the engine's own number: a score of 0.0 could be a distant vector or a normalization
        bug, and the two are indistinguishable once the distance is discarded.
        """
        return VectorMatch(
            chunk_id=chunk_row.id,
            document_id=chunk_row.document_id,
            score=self.score_from_distance(distance),
            chunk=chunk_to_value(chunk_row),
            raw_distance=float(distance),
        )


# ---------------------------------------------------------------------------
# row -> value type
# ---------------------------------------------------------------------------


def chunk_to_value(row: KnowledgeChunk) -> Chunk:
    """Map a ``knowledge_chunks`` row to the platform's :class:`Chunk` value type.

    Retrieval results cross out of the storage layer as value types, not ORM entities. Two reasons
    that matters more than it looks: a detached ORM entity lazily loading a relationship inside a
    response serializer is a class of bug that only appears under load, and an immutable ``Chunk``
    cannot be edited by a reranker or a context builder on its way to a prompt.
    """
    metadata = ChunkMetadata(
        document_id=row.document_id,
        policy_version=row.policy_version,
        department=row.department,
        country=row.country,
        currency=row.currency,
        category=row.category,
        language=row.language,
        effective_date=row.effective_date,
        expiry_date=row.expiry_date,
        section=row.section,
        page_number=row.page_number,
        owner=row.owner,
        checksum=row.checksum_sha256,
        tenant_id=row.tenant_id,
        source_type=coerce_source_type(row.source_type),
        tags=tuple(row.tags or ()),
        extra=dict(row.chunk_metadata or {}),
    )
    return Chunk(
        id=row.id,
        text=row.content,
        index=row.chunk_index,
        strategy=coerce_strategy(row.strategy),
        parent_id=row.parent_chunk_id,
        token_count=row.content_tokens or 0,
        heading_path=tuple(row.heading_path or ()),
        metadata=metadata,
    )


def _split_spec_key(spec_key: str) -> tuple[str, str, str]:
    """``provider/model@version`` into its three parts.

    Duplicated deliberately rather than imported from ``app.ai.embeddings.versioning``: the vector
    store cannot depend on the embedding package to write a row, and the format is pinned by a test
    that parses the same key through both, so a change to one is caught rather than silently
    tolerated.
    """
    try:
        head, version = spec_key.rsplit("@", 1)
        provider, model = head.split("/", 1)
    except ValueError as exc:
        raise AIValidationError(
            f"'{spec_key}' is not a valid embedding spec key. Expected 'provider/model@version'.",
            details={"specKey": spec_key},
        ) from exc
    return provider, model, version


def _as_uuid(value: Any) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise AIValidationError(f"'{value}' is not a valid UUID.") from exc


def default_session_provider() -> Any:
    """The application's session factory, imported lazily.

    Lazy so that importing this module does not build an engine — which would make a unit test that
    never touches the database fail on a machine with no ``DATABASE_URL``.
    """
    from app.core.database import SessionLocal

    return SessionLocal


def has_pgvector_extension(session: Session) -> bool:
    """Whether the ``vector`` extension is installed in this database."""
    return (
        session.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        ).first()
        is not None
    )


def require_pgvector_extension(session: Session) -> None:
    """Raise a configuration error if the ``vector`` extension is absent.

    Checked at the point of use, not at import, and reported as a configuration error with the
    remedy in it: the alternative is a SQL error about an unrecognized ``<=>`` operator, which reads
    like a bug in the query rather than a missing extension.
    """
    if not has_pgvector_extension(session):
        raise ProviderNotConfiguredError(
            provider="pgvector",
            kind="VECTOR_STORE",
            remedy=(
                "The 'vector' extension is not installed in this database. Run "
                "'CREATE EXTENSION vector;' (migration 0004 does this) or set "
                "AI_VECTOR_STORE=postgres_native."
            ),
        )


__all__ = [
    "PostgresVectorStoreBase",
    "chunk_to_value",
    "default_session_provider",
    "has_pgvector_extension",
    "require_pgvector_extension",
]
