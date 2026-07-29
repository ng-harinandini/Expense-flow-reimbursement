"""ORM models for the knowledge core: documents, chunks, embeddings, ingestion runs.

Alembic remains the sole schema owner (ADR-001); these declarations are what migration ``0004`` is
authored from, and the application never issues DDL at runtime.

Four decisions here are load-bearing and are the ones to read before changing anything:

**Identity is the content checksum, so ingestion is idempotent.**
``UNIQUE (tenant_id, checksum_sha256)`` means re-uploading identical bytes cannot create a second
document — the database enforces it, not just the service. A *changed* file is a new row that
``supersedes_id`` points at its predecessor: a superseded policy is never deleted, because a 2025
claim must still be judged against 2025 policy.

**Metadata is split by whether it is filtered on** (plan decision D3).
Fields that appear in a ``WHERE`` clause are real, indexed columns. Everything else lives in a
GIN-indexed JSONB. Filters have to be indexable; the metadata set has to extend without a migration;
this is the only arrangement that gives both.

**The lexical leg is a generated column.**
``content_tsv`` is ``GENERATED ALWAYS AS ... STORED``, so full-text search cannot drift from the
text it indexes — there is no application code that could forget to refresh it. ``'simple'`` rather
than ``'english'``: bge-m3 is multilingual, and English stemming would degrade every other language.

**One vector per chunk per model version.**
``UNIQUE (chunk_id, spec_key)`` lets a re-index under a new embedding version land *alongside* the
old vectors rather than overwriting them, which is what makes a model migration reversible.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import (
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.ai.core.config import BGE_M3_DIMENSIONS
from app.ai.core.enums import (
    DocumentStatus,
    IngestionStatus,
    document_status_enum,
    ingestion_status_enum,
)
from app.ai.vector_store.pg_types import Vector
from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

# Default tenant for single-enterprise deployments. Present on every table from the first migration
# because tenancy cannot be retrofitted into a vector index later (plan decision D4).
DEFAULT_TENANT_ID = "default"

# The dimensionality baked into the DDL. Changing the embedding model to one with a different width
# is a migration, deliberately: the existing vectors would not be comparable to the new ones.
EMBEDDING_DIMENSIONS = BGE_M3_DIMENSIONS

# HNSW build parameters. Must match migration 0004; a parity test asserts they agree, because drift
# here would silently rebuild the index with different recall characteristics.
HNSW_M = 16
HNSW_EF_CONSTRUCTION = 64


class TenantMixin:
    """``tenant_id`` on every AI table, enforced in the repository layer."""

    tenant_id: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=DEFAULT_TENANT_ID, index=True
    )


class KnowledgeDocument(TenantMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One version of one source document."""

    __tablename__ = "knowledge_documents"
    __table_args__ = (
        # Idempotency, enforced by the database rather than by a service check.
        UniqueConstraint("tenant_id", "checksum_sha256", name="uq_knowledge_documents_checksum"),
        CheckConstraint("version >= 1", name="ck_knowledge_documents_version_positive"),
        CheckConstraint(
            "expiry_date IS NULL OR effective_date IS NULL OR expiry_date >= effective_date",
            name="ck_knowledge_documents_date_order",
        ),
        CheckConstraint(
            "quality_score IS NULL OR (quality_score >= 0 AND quality_score <= 100)",
            name="ck_knowledge_documents_quality_range",
        ),
        CheckConstraint("size_bytes IS NULL OR size_bytes >= 0",
                        name="ck_knowledge_documents_size_non_negative"),
        # The review/listing queries.
        Index("ix_knowledge_documents_tenant_status", "tenant_id", "status"),
        Index("ix_knowledge_documents_tenant_source_type", "tenant_id", "source_type"),
        Index("ix_knowledge_documents_effective", "tenant_id", "effective_date", "expiry_date"),
        Index("ix_knowledge_documents_metadata", "doc_metadata", postgresql_using="gin"),
    )

    # --- provenance ---
    source_type: Mapped[str] = mapped_column(String(48), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    source_uri: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- identity + integrity ---
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # --- versioning (mirrors the policy_rules pattern from T003) ---
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    supersedes_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_documents.id", ondelete="SET NULL",
                   name="fk_knowledge_documents_supersedes_id"),
        nullable=True,
    )
    status: Mapped[DocumentStatus] = mapped_column(
        document_status_enum, nullable=False, server_default=DocumentStatus.PENDING.value
    )

    # --- analysis (Task 2) ---
    language: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    quality_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    # Kinds only — never the matched values, which would copy the personal data into a second table.
    pii_kinds: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    parser: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # --- filterable metadata (Task 6), promoted to indexed columns ---
    department: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    policy_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    effective_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    owner: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # ``metadata`` is reserved on a Declarative class, hence the prefix.
    doc_metadata: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    created_by_sub: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    indexed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    chunks: Mapped[List["KnowledgeChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="KnowledgeChunk.chunk_index",
    )

    @property
    def is_retrievable(self) -> bool:
        """Whether retrieval may return this document's chunks by default.

        ``SUPERSEDED`` is excluded here but deliberately still queryable via an explicit
        effective-date filter, which is how a historical claim reaches the policy that applied then.
        """
        return self.status == DocumentStatus.INDEXED

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<KnowledgeDocument {self.title!r} v{self.version} {self.status}>"


class KnowledgeChunk(TenantMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A retrievable unit of text, carrying the metadata retrieval filters on."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_knowledge_chunks_document_index"),
        CheckConstraint("chunk_index >= 0", name="ck_knowledge_chunks_index_non_negative"),
        CheckConstraint("length(btrim(content)) > 0", name="ck_knowledge_chunks_content_not_blank"),
        CheckConstraint(
            "content_tokens IS NULL OR content_tokens >= 0",
            name="ck_knowledge_chunks_tokens_non_negative",
        ),
        CheckConstraint(
            "expiry_date IS NULL OR effective_date IS NULL OR expiry_date >= effective_date",
            name="ck_knowledge_chunks_date_order",
        ),
        # The lexical retrieval leg (BM25-style ranking via ts_rank_cd).
        Index("ix_knowledge_chunks_tsv", "content_tsv", postgresql_using="gin"),
        # Fuzzy matching for vendor aliases and OCR near-duplicates (pg_trgm).
        Index("ix_knowledge_chunks_content_trgm", "content",
              postgresql_using="gin", postgresql_ops={"content": "gin_trgm_ops"}),
        Index("ix_knowledge_chunks_metadata", "chunk_metadata", postgresql_using="gin"),
        Index("ix_knowledge_chunks_tags", "tags", postgresql_using="gin"),
        # Metadata pre-filtering, which must happen before scoring.
        Index("ix_knowledge_chunks_tenant_document", "tenant_id", "document_id"),
        Index("ix_knowledge_chunks_filters", "tenant_id", "category", "country", "currency"),
        Index("ix_knowledge_chunks_effective", "tenant_id", "effective_date", "expiry_date"),
        Index("ix_knowledge_chunks_parent", "parent_chunk_id"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_documents.id", ondelete="CASCADE",
                   name="fk_knowledge_chunks_document_id"),
        nullable=False,
    )
    # Parent-child chunking: children are embedded for precision, the parent supplies wider context.
    # SET NULL rather than CASCADE — losing a parent must not delete the retrievable children.
    parent_chunk_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_chunks.id", ondelete="SET NULL",
                   name="fk_knowledge_chunks_parent_chunk_id"),
        nullable=True,
    )

    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    strategy: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    checksum_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    # Generated, not application-maintained, so the index can never drift from the text.
    content_tsv: Mapped[Optional[str]] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', content)", persisted=True),
        nullable=True,
    )

    # --- citation anchors ---
    section: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    page_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    heading_path: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    # --- filterable metadata (Task 6) ---
    policy_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    department: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    language: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    source_type: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
    effective_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    owner: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    tags: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    chunk_metadata: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    document: Mapped["KnowledgeDocument"] = relationship(back_populates="chunks")
    embeddings: Mapped[List["KnowledgeEmbedding"]] = relationship(
        back_populates="chunk", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<KnowledgeChunk {self.document_id}#{self.chunk_index}>"


class KnowledgeEmbedding(TenantMixin, UUIDPrimaryKeyMixin, Base):
    """One chunk's vector under one embedding model version.

    Every column Task 4 asks to record — model, version, dimensions, latency, cost, created-by,
    timestamp — is here, because a vector whose provenance was lost cannot be validated, re-derived,
    or safely compared to anything.
    """

    __tablename__ = "knowledge_embeddings"
    __table_args__ = (
        # A chunk has at most one vector per model version; re-indexing updates in place.
        UniqueConstraint("chunk_id", "spec_key", name="uq_knowledge_embeddings_chunk_spec"),
        CheckConstraint("dimensions > 0", name="ck_knowledge_embeddings_dimensions_positive"),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0",
                        name="ck_knowledge_embeddings_latency_non_negative"),
        CheckConstraint("cost_usd IS NULL OR cost_usd >= 0",
                        name="ck_knowledge_embeddings_cost_non_negative"),
        # Every search filters by tenant and model version before it scores anything.
        Index("ix_knowledge_embeddings_tenant_spec", "tenant_id", "spec_key"),
        Index("ix_knowledge_embeddings_document", "document_id"),
        # Approximate-nearest-neighbour index. Declared here rather than left to raw SQL in the
        # migration so the model stays the single source of truth and `alembic --autogenerate`
        # produces an empty diff — otherwise every future autogenerate proposes dropping and
        # recreating it. Cosine, because every provider the platform ships returns L2-normalized
        # vectors, for which cosine and inner product rank identically.
        Index(
            "ix_knowledge_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": HNSW_M, "ef_construction": HNSW_EF_CONSTRUCTION},
        ),
    )

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_chunks.id", ondelete="CASCADE",
                   name="fk_knowledge_embeddings_chunk_id"),
        nullable=False,
    )
    # Denormalized from the chunk so delete-by-document and document-scoped filters do not need a
    # join. The FK keeps it honest.
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_documents.id", ondelete="CASCADE",
                   name="fk_knowledge_embeddings_document_id"),
        nullable=False,
    )

    # --- provenance (Task 4) ---
    embedding_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(160), nullable=False)
    embedding_version: Mapped[str] = mapped_column(String(64), nullable=False)
    # "provider/model@version" — the single value comparability is decided on.
    spec_key: Mapped[str] = mapped_column(String(320), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)

    embedding: Mapped[Optional[tuple]] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS), nullable=False
    )

    # --- cost + latency accounting (Task 14) ---
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 6), nullable=True)
    input_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_by_sub: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    chunk: Mapped["KnowledgeChunk"] = relationship(back_populates="embeddings")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<KnowledgeEmbedding {self.chunk_id} {self.spec_key}>"


class KnowledgeIngestionRun(TenantMixin, UUIDPrimaryKeyMixin, Base):
    """Ledger of one ingestion attempt.

    Written for every run including the no-ops, so "why is this document not searchable?" is always
    answerable from the database. ``stage`` is what turns a failure from "ingestion broke" into
    "parsing failed on page 4" — the difference between a usable and an unusable audit trail.
    """

    __tablename__ = "knowledge_ingestion_runs"
    __table_args__ = (
        CheckConstraint("chunks_created >= 0", name="ck_ingestion_runs_chunks_non_negative"),
        CheckConstraint("embeddings_created >= 0",
                        name="ck_ingestion_runs_embeddings_non_negative"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0",
                        name="ck_ingestion_runs_duration_non_negative"),
        Index("ix_ingestion_runs_tenant_status", "tenant_id", "status"),
        Index("ix_ingestion_runs_document", "document_id"),
        Index("ix_ingestion_runs_started_at", "started_at"),
        Index("ix_ingestion_runs_correlation_id", "correlation_id"),
    )

    # SET NULL: a run's history survives the deletion of the document it produced.
    document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_documents.id", ondelete="SET NULL",
                   name="fk_ingestion_runs_document_id"),
        nullable=True,
    )

    status: Mapped[IngestionStatus] = mapped_column(
        ingestion_status_enum, nullable=False, server_default=IngestionStatus.RUNNING.value
    )
    stage: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    source_type: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
    source_uri: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    checksum_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    chunks_created: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    chunks_skipped: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    embeddings_created: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    embedding_spec_key: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)

    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stage_timings: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    actor_sub: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    correlation_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def succeeded(self) -> bool:
        return self.status in (IngestionStatus.COMPLETED, IngestionStatus.SKIPPED_DUPLICATE)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<KnowledgeIngestionRun {self.status} stage={self.stage}>"


__all__ = [
    "DEFAULT_TENANT_ID",
    "EMBEDDING_DIMENSIONS",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "KnowledgeEmbedding",
    "KnowledgeIngestionRun",
    "TenantMixin",
]
