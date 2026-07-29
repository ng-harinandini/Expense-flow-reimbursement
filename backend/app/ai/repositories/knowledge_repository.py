"""Repositories for the knowledge core.

Follows the T003 contract exactly: **all SQL lives here**, repositories accept a ``Session`` and
never commit (``flush`` only), so a route keeps one explicit transaction boundary via
``UnitOfWork``.

Two rules specific to the AI platform:

**Every read is tenant-scoped.** ``tenant_id`` is a required argument, not an optional filter, on
every method that can return rows. A default would eventually be relied on, and one tenant reading
another's corpus is the worst failure this schema can produce.

**Similarity search is not here.** Vector search belongs to ``app/ai/vector_store/pgvector.py``
(M6), because it is the part that must be swappable for Qdrant or OpenSearch. What lives here is
persistence plus the *lexical* leg, which is ordinary PostgreSQL full-text search over a table this
layer owns.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy import Select, func, or_, select

from app.ai.core.enums import DocumentStatus, IngestionStage, IngestionStatus
from app.ai.models.knowledge import (
    DEFAULT_TENANT_ID,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeEmbedding,
    KnowledgeIngestionRun,
)
from app.core.logging import get_logger
from app.repositories.base import BaseRepository

logger = get_logger(__name__)

# ``'simple'`` must match the generated column's regconfig in migration 0004. A mismatch would make
# queries silently miss rows, so it is named once here rather than repeated as a literal.
TS_CONFIG = "simple"


@dataclass(frozen=True, slots=True)
class EmbeddingWrite:
    """One vector to persist, as a value object.

    A parameter object rather than a dozen keyword arguments because
    :meth:`KnowledgeEmbeddingRepository.upsert_many` takes a *list* of these, and because it keeps
    provenance fields Task 4 requires travelling together — a vector separated from its model
    identity cannot be validated or invalidated later.
    """

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    vector: Sequence[float]
    provider: str
    model_name: str
    version: str
    spec_key: str
    dimensions: int
    latency_ms: Optional[int] = None
    cost_usd: Optional[float] = None
    input_tokens: Optional[int] = None
    created_by_sub: Optional[str] = None


class KnowledgeDocumentRepository(BaseRepository[KnowledgeDocument]):
    """Documents and their version chains."""

    model = KnowledgeDocument

    def get_scoped(
        self, document_id: uuid.UUID | str, *, tenant_id: str
    ) -> Optional[KnowledgeDocument]:
        """Primary-key lookup that cannot cross a tenant boundary."""
        key = self._coerce_uuid(document_id)
        if key is None:
            return None
        return self._one_or_none(
            select(KnowledgeDocument).where(
                KnowledgeDocument.id == key,
                KnowledgeDocument.tenant_id == tenant_id,
            )
        )

    def find_by_checksum(self, checksum: str, *, tenant_id: str) -> Optional[KnowledgeDocument]:
        """The idempotency lookup: identical bytes already ingested for this tenant?"""
        return self._one_or_none(
            select(KnowledgeDocument).where(
                KnowledgeDocument.tenant_id == tenant_id,
                KnowledgeDocument.checksum_sha256 == checksum,
            )
        )

    def next_version_for_title(self, title: str, *, tenant_id: str) -> int:
        """Version number a new revision of ``title`` should take.

        Versioning is per title rather than per checksum: a re-uploaded, edited policy is version
        N+1 of the same document, which is what lets ``supersedes_id`` build a readable chain.
        """
        current = self.session.execute(
            select(func.max(KnowledgeDocument.version)).where(
                KnowledgeDocument.tenant_id == tenant_id,
                KnowledgeDocument.title == title,
            )
        ).scalar()
        return int(current or 0) + 1

    def latest_for_title(self, title: str, *, tenant_id: str) -> Optional[KnowledgeDocument]:
        return self._one_or_none(
            select(KnowledgeDocument)
            .where(
                KnowledgeDocument.tenant_id == tenant_id,
                KnowledgeDocument.title == title,
            )
            .order_by(KnowledgeDocument.version.desc())
        )

    def search(
        self,
        *,
        tenant_id: str,
        status: Optional[DocumentStatus] = None,
        source_type: Optional[str] = None,
        category: Optional[str] = None,
        country: Optional[str] = None,
        effective_on: Optional[date] = None,
        include_superseded: bool = False,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> Sequence[KnowledgeDocument]:
        stmt = select(KnowledgeDocument).where(KnowledgeDocument.tenant_id == tenant_id)
        if status is not None:
            stmt = stmt.where(KnowledgeDocument.status == status)
        elif not include_superseded:
            # Superseded documents stay queryable, but only when asked for explicitly.
            stmt = stmt.where(KnowledgeDocument.status != DocumentStatus.SUPERSEDED)
        if source_type:
            stmt = stmt.where(KnowledgeDocument.source_type == source_type)
        if category:
            stmt = stmt.where(KnowledgeDocument.category == category)
        if country:
            stmt = stmt.where(KnowledgeDocument.country == country)
        if effective_on is not None:
            stmt = stmt.where(_effective_on(KnowledgeDocument, effective_on))
        stmt = stmt.order_by(
            KnowledgeDocument.created_at.desc(), KnowledgeDocument.id
        )
        return self._all(self._paginate(stmt, limit=limit, offset=offset))

    def mark_indexed(self, document: KnowledgeDocument) -> KnowledgeDocument:
        document.status = DocumentStatus.INDEXED
        document.indexed_at = datetime.now(timezone.utc)
        self.session.flush()
        return document

    def mark_failed(self, document: KnowledgeDocument) -> KnowledgeDocument:
        document.status = DocumentStatus.FAILED
        self.session.flush()
        return document

    def supersede(
        self, previous: KnowledgeDocument, replacement: KnowledgeDocument
    ) -> KnowledgeDocument:
        """Point ``replacement`` at ``previous`` and retire the latter.

        The old row and its chunks are kept, never deleted: a claim from the period the old policy
        covered must still be explainable against that policy.
        """
        if previous.tenant_id != replacement.tenant_id:
            raise ValueError("Cannot supersede a document belonging to another tenant.")
        replacement.supersedes_id = previous.id
        previous.status = DocumentStatus.SUPERSEDED
        self.session.flush()
        return replacement

    def version_chain(self, document: KnowledgeDocument) -> list[KnowledgeDocument]:
        """Oldest-to-newest chain reachable from ``document`` via ``supersedes_id``."""
        chain: list[KnowledgeDocument] = [document]
        seen = {document.id}
        current = document
        while current.supersedes_id and current.supersedes_id not in seen:
            previous = self.get(current.supersedes_id)
            if previous is None:
                break
            chain.append(previous)
            seen.add(previous.id)
            current = previous
        return list(reversed(chain))

    def count_for_tenant(self, *, tenant_id: str) -> int:
        return int(
            self.session.execute(
                select(func.count()).select_from(KnowledgeDocument).where(
                    KnowledgeDocument.tenant_id == tenant_id
                )
            ).scalar_one()
        )


class KnowledgeChunkRepository(BaseRepository[KnowledgeChunk]):
    """Chunks, plus the lexical (full-text) retrieval leg."""

    model = KnowledgeChunk

    def add_many(self, chunks: Iterable[KnowledgeChunk]) -> list[KnowledgeChunk]:
        return self.add_all(chunks)

    def list_for_document(
        self, document_id: uuid.UUID, *, tenant_id: str
    ) -> Sequence[KnowledgeChunk]:
        return self._all(
            select(KnowledgeChunk)
            .where(
                KnowledgeChunk.tenant_id == tenant_id,
                KnowledgeChunk.document_id == document_id,
            )
            .order_by(KnowledgeChunk.chunk_index)
        )

    def get_many(
        self, chunk_ids: Sequence[uuid.UUID], *, tenant_id: str
    ) -> Sequence[KnowledgeChunk]:
        """Hydrate chunks by id, preserving nothing about order — the caller ranks them.

        Used by the retrieval engine after the vector store returns ids: the store owns similarity,
        this layer owns the text and metadata.
        """
        if not chunk_ids:
            return []
        return self._all(
            select(KnowledgeChunk).where(
                KnowledgeChunk.tenant_id == tenant_id,
                KnowledgeChunk.id.in_(list(chunk_ids)),
            )
        )

    def delete_for_document(self, document_id: uuid.UUID, *, tenant_id: str) -> int:
        """Remove a document's chunks. Embeddings follow by ``ON DELETE CASCADE``."""
        rows = self.session.execute(
            select(KnowledgeChunk.id).where(
                KnowledgeChunk.tenant_id == tenant_id,
                KnowledgeChunk.document_id == document_id,
            )
        ).scalars().all()
        if not rows:
            return 0
        self.session.execute(
            KnowledgeChunk.__table__.delete().where(KnowledgeChunk.id.in_(rows))
        )
        self.session.flush()
        return len(rows)

    def lexical_search(
        self,
        query: str,
        *,
        tenant_id: str,
        limit: int = 50,
        category: Optional[str] = None,
        country: Optional[str] = None,
        currency: Optional[str] = None,
        source_type: Optional[str] = None,
        document_ids: Optional[Sequence[uuid.UUID]] = None,
        effective_on: Optional[date] = None,
        include_superseded: bool = False,
    ) -> list[tuple[KnowledgeChunk, float]]:
        """BM25-style ranking over the generated ``content_tsv`` column.

        ``ts_rank_cd`` (cover density) rather than ``ts_rank``: it accounts for how close the query
        terms are to each other, which matters for policy text where "meal" and "allowance" being
        adjacent is far more meaningful than both appearing paragraphs apart.

        Filters are applied in the same ``WHERE`` clause as the match, so PostgreSQL restricts the
        candidate set *before* ranking — post-filtering would silently return fewer than ``limit``.
        """
        if not query or not query.strip():
            return []

        tsquery = func.plainto_tsquery(TS_CONFIG, query)
        rank = func.ts_rank_cd(KnowledgeChunk.content_tsv, tsquery)

        stmt: Select = (
            select(KnowledgeChunk, rank.label("rank"))
            .where(
                KnowledgeChunk.tenant_id == tenant_id,
                KnowledgeChunk.content_tsv.op("@@")(tsquery),
            )
        )
        stmt = self._apply_filters(
            stmt,
            category=category,
            country=country,
            currency=currency,
            source_type=source_type,
            document_ids=document_ids,
            effective_on=effective_on,
            include_superseded=include_superseded,
        )
        # Deterministic tie-break, so page 2 never repeats a row from page 1.
        stmt = stmt.order_by(rank.desc(), KnowledgeChunk.id).limit(limit)

        return [(row[0], float(row[1] or 0.0)) for row in self.session.execute(stmt).unique()]

    def trigram_search(
        self, term: str, *, tenant_id: str, threshold: float = 0.3, limit: int = 20
    ) -> list[tuple[KnowledgeChunk, float]]:
        """Fuzzy match via ``pg_trgm`` similarity — for OCR noise and vendor-name variants.

        Complements the lexical leg: full-text search cannot match ``"UBER *TRIP"`` to ``"Uber"``,
        because tokenization has already separated them.
        """
        if not term or not term.strip():
            return []
        similarity = func.similarity(KnowledgeChunk.content, term)
        stmt = (
            select(KnowledgeChunk, similarity.label("sim"))
            .where(
                KnowledgeChunk.tenant_id == tenant_id,
                similarity > threshold,
            )
            .order_by(similarity.desc(), KnowledgeChunk.id)
            .limit(limit)
        )
        return [(row[0], float(row[1] or 0.0)) for row in self.session.execute(stmt).unique()]

    def _apply_filters(
        self,
        stmt: Select,
        *,
        category: Optional[str],
        country: Optional[str],
        currency: Optional[str],
        source_type: Optional[str],
        document_ids: Optional[Sequence[uuid.UUID]],
        effective_on: Optional[date],
        include_superseded: bool,
    ) -> Select:
        if category:
            stmt = stmt.where(KnowledgeChunk.category == category)
        if country:
            stmt = stmt.where(KnowledgeChunk.country == country)
        if currency:
            stmt = stmt.where(KnowledgeChunk.currency == currency)
        if source_type:
            stmt = stmt.where(KnowledgeChunk.source_type == source_type)
        if document_ids:
            stmt = stmt.where(KnowledgeChunk.document_id.in_(list(document_ids)))
        if effective_on is not None:
            stmt = stmt.where(_effective_on(KnowledgeChunk, effective_on))
        if not include_superseded:
            # Join to the parent document so a retired policy's chunks drop out by default.
            stmt = stmt.where(
                KnowledgeChunk.document_id.in_(
                    select(KnowledgeDocument.id).where(
                        KnowledgeDocument.status != DocumentStatus.SUPERSEDED
                    )
                )
            )
        return stmt

    def count_for_tenant(self, *, tenant_id: str) -> int:
        return int(
            self.session.execute(
                select(func.count()).select_from(KnowledgeChunk).where(
                    KnowledgeChunk.tenant_id == tenant_id
                )
            ).scalar_one()
        )


class KnowledgeEmbeddingRepository(BaseRepository[KnowledgeEmbedding]):
    """Persistence for vectors. Similarity search lives in the vector-store adapter (M6)."""

    model = KnowledgeEmbedding

    def find_for_chunk(
        self, chunk_id: uuid.UUID, spec_key: str, *, tenant_id: str
    ) -> Optional[KnowledgeEmbedding]:
        return self._one_or_none(
            select(KnowledgeEmbedding).where(
                KnowledgeEmbedding.tenant_id == tenant_id,
                KnowledgeEmbedding.chunk_id == chunk_id,
                KnowledgeEmbedding.spec_key == spec_key,
            )
        )

    def upsert(
        self,
        *,
        chunk_id: uuid.UUID,
        document_id: uuid.UUID,
        tenant_id: str,
        vector: Sequence[float],
        provider: str,
        model_name: str,
        version: str,
        spec_key: str,
        dimensions: int,
        latency_ms: Optional[int] = None,
        cost_usd: Optional[float] = None,
        input_tokens: Optional[int] = None,
        created_by_sub: Optional[str] = None,
    ) -> KnowledgeEmbedding:
        """Insert or replace the vector for one chunk under one model version.

        Replacement rather than a second row: ``UNIQUE (chunk_id, spec_key)`` forbids
        duplicates, and
        re-embedding the same chunk under the same version should be idempotent so a resumed
        ingestion run does not fail halfway.
        """
        existing = self.find_for_chunk(chunk_id, spec_key, tenant_id=tenant_id)
        if existing is not None:
            existing.embedding = tuple(float(v) for v in vector)
            existing.dimensions = dimensions
            existing.latency_ms = latency_ms
            existing.cost_usd = cost_usd
            existing.input_tokens = input_tokens
            self.session.flush()
            return existing

        entity = KnowledgeEmbedding(
            tenant_id=tenant_id,
            chunk_id=chunk_id,
            document_id=document_id,
            embedding=tuple(float(v) for v in vector),
            embedding_provider=provider,
            embedding_model=model_name,
            embedding_version=version,
            spec_key=spec_key,
            dimensions=dimensions,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            input_tokens=input_tokens,
            created_by_sub=created_by_sub,
        )
        return self.add(entity)

    def upsert_many(self, writes: Sequence[EmbeddingWrite], *, tenant_id: str) -> int:
        """Upsert a batch of vectors sharing one model version, in a fixed number of statements.

        The per-chunk :meth:`upsert` issues a SELECT and then an INSERT or UPDATE for every vector,
        which is fine for a single re-embed and wrong for ingestion: a 400-chunk policy document
        would cost 800 round trips. This resolves the batch with one SELECT and lets the unit of
        work flush the writes together.

        Callers must pass one ``spec_key`` for the batch. Mixing versions is rejected rather than
        handled, because the existing-row lookup is keyed on ``(chunk_id, spec_key)`` and a batch
        spanning versions would need a second query to stay correct — a cost paid to support a call
        that is always a caller bug.
        """
        if not writes:
            return 0
        spec_keys = {w.spec_key for w in writes}
        if len(spec_keys) > 1:
            raise ValueError(
                f"upsert_many expects one spec_key per batch, received {sorted(spec_keys)}."
            )
        spec_key = spec_keys.pop()

        chunk_ids = [w.chunk_id for w in writes]
        if len(set(chunk_ids)) != len(chunk_ids):
            # Guarded here too, not only by the vector store's own batch validation upstream: this
            # method is public on the repository and callable directly (the ingestion pipeline will
            # do exactly that in M8). Two writes for the same chunk that is not yet in `existing`
            # both take the insert branch below and both attempt the same UNIQUE(chunk_id, spec_key)
            # row, surfacing as a raw IntegrityError instead of a clear caller error.
            counts = Counter(chunk_ids)
            duplicates = sorted(str(cid) for cid, n in counts.items() if n > 1)
            raise ValueError(
                f"upsert_many must not repeat a chunk id within one batch; found "
                f"{len(duplicates)} repeated: {duplicates[:20]}."
            )
        existing = {
            row.chunk_id: row
            for row in self.session.execute(
                select(KnowledgeEmbedding).where(
                    KnowledgeEmbedding.tenant_id == tenant_id,
                    KnowledgeEmbedding.spec_key == spec_key,
                    KnowledgeEmbedding.chunk_id.in_(chunk_ids),
                )
            ).scalars()
        }

        fresh: list[KnowledgeEmbedding] = []
        for write in writes:
            values = tuple(float(v) for v in write.vector)
            entity = existing.get(write.chunk_id)
            if entity is not None:
                entity.embedding = values
                entity.dimensions = write.dimensions
                entity.latency_ms = write.latency_ms
                entity.cost_usd = write.cost_usd
                entity.input_tokens = write.input_tokens
                continue
            fresh.append(
                KnowledgeEmbedding(
                    tenant_id=tenant_id,
                    chunk_id=write.chunk_id,
                    document_id=write.document_id,
                    embedding=values,
                    embedding_provider=write.provider,
                    embedding_model=write.model_name,
                    embedding_version=write.version,
                    spec_key=spec_key,
                    dimensions=write.dimensions,
                    latency_ms=write.latency_ms,
                    cost_usd=write.cost_usd,
                    input_tokens=write.input_tokens,
                    created_by_sub=write.created_by_sub,
                )
            )
        if fresh:
            self.session.add_all(fresh)
        self.session.flush()
        return len(writes)

    def delete_for_document(
        self, document_id: uuid.UUID, *, tenant_id: str, spec_key: Optional[str] = None
    ) -> int:
        """Delete a document's vectors, optionally only those of one model version.

        The ``spec_key`` form is what makes a model rollback possible: drop the new version's
        vectors and the previous version's are still there, untouched.
        """
        stmt = select(KnowledgeEmbedding.id).where(
            KnowledgeEmbedding.tenant_id == tenant_id,
            KnowledgeEmbedding.document_id == document_id,
        )
        if spec_key:
            stmt = stmt.where(KnowledgeEmbedding.spec_key == spec_key)
        rows = self.session.execute(stmt).scalars().all()
        if not rows:
            return 0
        self.session.execute(
            KnowledgeEmbedding.__table__.delete().where(KnowledgeEmbedding.id.in_(rows))
        )
        self.session.flush()
        return len(rows)

    def count_by_spec(self, *, tenant_id: str) -> dict[str, int]:
        """Vectors per model version — the answer to "is the re-index finished?"."""
        rows = self.session.execute(
            select(KnowledgeEmbedding.spec_key, func.count())
            .where(KnowledgeEmbedding.tenant_id == tenant_id)
            .group_by(KnowledgeEmbedding.spec_key)
            .order_by(KnowledgeEmbedding.spec_key)
        ).all()
        return {row[0]: int(row[1]) for row in rows}

    def chunks_missing_embedding(
        self, spec_key: str, *, tenant_id: str, limit: int = 500
    ) -> Sequence[uuid.UUID]:
        """Chunk ids with no vector under ``spec_key``.

        The work queue for a re-index: it makes the operation resumable, so an interrupted run picks
        up where it stopped instead of re-embedding the whole corpus.
        """
        embedded = select(KnowledgeEmbedding.chunk_id).where(
            KnowledgeEmbedding.tenant_id == tenant_id,
            KnowledgeEmbedding.spec_key == spec_key,
        )
        stmt = (
            select(KnowledgeChunk.id)
            .where(
                KnowledgeChunk.tenant_id == tenant_id,
                KnowledgeChunk.id.not_in(embedded),
            )
            .order_by(KnowledgeChunk.created_at, KnowledgeChunk.id)
            .limit(limit)
        )
        return list(self.session.execute(stmt).scalars().all())


class KnowledgeIngestionRunRepository(BaseRepository[KnowledgeIngestionRun]):
    """The ingestion audit ledger."""

    model = KnowledgeIngestionRun

    def start(
        self,
        *,
        tenant_id: str,
        source_type: Optional[str] = None,
        source_uri: Optional[str] = None,
        file_name: Optional[str] = None,
        checksum: Optional[str] = None,
        actor_sub: Optional[str] = None,
        request_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> KnowledgeIngestionRun:
        run = KnowledgeIngestionRun(
            tenant_id=tenant_id,
            status=IngestionStatus.RUNNING,
            stage=IngestionStage.FETCH.value,
            source_type=source_type,
            source_uri=source_uri,
            file_name=file_name,
            checksum_sha256=checksum,
            actor_sub=actor_sub,
            request_id=request_id,
            correlation_id=correlation_id,
        )
        return self.add(run)

    def set_stage(self, run: KnowledgeIngestionRun, stage: IngestionStage | str) -> None:
        run.stage = stage.value if isinstance(stage, IngestionStage) else str(stage)
        self.session.flush()

    def finish(
        self,
        run: KnowledgeIngestionRun,
        *,
        status: IngestionStatus,
        document_id: Optional[uuid.UUID] = None,
        chunks_created: int = 0,
        chunks_skipped: int = 0,
        embeddings_created: int = 0,
        embedding_spec_key: Optional[str] = None,
        duration_ms: Optional[int] = None,
        error_message: Optional[str] = None,
        stage_timings: Optional[dict[str, Any]] = None,
    ) -> KnowledgeIngestionRun:
        run.status = status
        run.document_id = document_id
        run.chunks_created = chunks_created
        run.chunks_skipped = chunks_skipped
        run.embeddings_created = embeddings_created
        run.embedding_spec_key = embedding_spec_key
        run.duration_ms = duration_ms
        # Truncated: a driver traceback can be enormous, and the stage plus the first lines are what
        # a diagnosis actually needs.
        run.error_message = error_message[:4000] if error_message else None
        run.stage_timings = stage_timings
        run.stage = IngestionStage.FINALIZE.value
        run.finished_at = datetime.now(timezone.utc)
        self.session.flush()
        return run

    def recent(
        self, *, tenant_id: str, limit: int = 50, status: Optional[IngestionStatus] = None
    ) -> Sequence[KnowledgeIngestionRun]:
        stmt = select(KnowledgeIngestionRun).where(
            KnowledgeIngestionRun.tenant_id == tenant_id
        )
        if status is not None:
            stmt = stmt.where(KnowledgeIngestionRun.status == status)
        stmt = stmt.order_by(
            KnowledgeIngestionRun.started_at.desc(), KnowledgeIngestionRun.id
        ).limit(limit)
        return self._all(stmt)

    def for_document(
        self, document_id: uuid.UUID, *, tenant_id: str
    ) -> Sequence[KnowledgeIngestionRun]:
        return self._all(
            select(KnowledgeIngestionRun)
            .where(
                KnowledgeIngestionRun.tenant_id == tenant_id,
                KnowledgeIngestionRun.document_id == document_id,
            )
            .order_by(KnowledgeIngestionRun.started_at.desc())
        )


def _effective_on(entity: Any, when: date):
    """``effective_date <= when <= expiry_date``, treating NULL bounds as open.

    Shared by the document and chunk queries so the two cannot drift — an inconsistency here would
    mean a document is in scope while its own chunks are not.
    """
    return (
        or_(entity.effective_date.is_(None), entity.effective_date <= when)
    ) & (
        or_(entity.expiry_date.is_(None), entity.expiry_date >= when)
    )


__all__ = [
    "DEFAULT_TENANT_ID",
    "EmbeddingWrite",
    "KnowledgeChunkRepository",
    "KnowledgeDocumentRepository",
    "KnowledgeEmbeddingRepository",
    "KnowledgeIngestionRunRepository",
    "TS_CONFIG",
]
