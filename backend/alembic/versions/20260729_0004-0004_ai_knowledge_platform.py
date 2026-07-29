"""AI knowledge platform: documents, chunks, embeddings, ingestion runs.

Revision ID: 0004_ai_knowledge_platform
Revises: 0003_seed_reference_data
Create Date: 2026-07-29

Strictly **additive**: no existing table, column, constraint or index is altered, so the T003 domain
model and T001's receipts are untouched (ADR-001 STOP-on-conflict policy).

Three things this migration does that a plain ``autogenerate`` would not:

1. **Installs the extensions.** ``vector`` and ``pg_trgm`` are created here because Alembic is the
   sole schema owner — a runtime path must never issue DDL. Both are ``IF NOT EXISTS``, so applying
   to a database where an operator already enabled them is a no-op.
2. **Builds the HNSW index.** ``hnsw (embedding vector_cosine_ops)`` with ``m``/``ef_construction``
   matching the ``AI_VECTOR_HNSW_*`` defaults. Expressed through ``op.create_index`` rather than raw
   SQL, and mirrored by an ``Index`` declaration on the model, so ``--autogenerate`` reconciles the
   two and produces an empty diff — raw SQL here made every future autogenerate propose dropping
   and recreating it.
3. **Creates the trigram index** on ``knowledge_chunks.content``, which needs the ``gin_trgm_ops``
   operator class and therefore depends on step 1.

The generated ``content_tsv`` column is declared in the model rather than added here, so full-text
search cannot drift from the text it indexes.

**Downgrade leaves the two extensions installed.** Dropping ``vector`` would cascade into any other
schema using the type, and dropping ``pg_trgm`` could break unrelated indexes; neither is this
revision's to destroy. Everything this revision *created* is removed.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.types import UserDefinedType

# revision identifiers, used by Alembic.
revision: str = "0004_ai_knowledge_platform"
down_revision: Union[str, None] = "0003_seed_reference_data"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# --- enums ------------------------------------------------------------------
# Only the closed vocabularies get a native type; open ones (source_type, strategy) stay VARCHAR so
# later phases can extend them without a migration — the reasoning T003 applied to
# audit_logs.action.

_ENUMS: tuple[tuple[str, Sequence[str]], ...] = (
    ("ai_document_status",
     ("PENDING", "PROCESSING", "INDEXED", "FAILED", "SUPERSEDED", "ARCHIVED")),
    ("ai_ingestion_status",
     ("RUNNING", "COMPLETED", "FAILED", "SKIPPED_DUPLICATE")),
)

# Vector width. Must match app.ai.core.config.BGE_M3_DIMENSIONS; a different model width is a new
# migration, because the existing vectors would not be comparable to the new ones anyway.
EMBEDDING_DIMENSIONS = 1024

# HNSW build parameters — mirror the AI_VECTOR_HNSW_* defaults.
HNSW_M = 16
HNSW_EF_CONSTRUCTION = 64


def _enum(name: str, values: Sequence[str]) -> postgresql.ENUM:
    """Reference an already-created type (``create_type=False``) inside a table definition."""
    return postgresql.ENUM(*values, name=name, create_type=False)


class _Vector(UserDefinedType):
    """Minimal ``vector(n)`` type, local to this revision.

    Deliberately **not** imported from ``app.ai.vector_store.pg_types``. A migration must keep
    producing the same DDL forever, so it cannot depend on application code that will be refactored
    — the same reason the T003 revisions declare their enums inline instead of importing
    ``app.models.enums``. Only DDL generation is needed here; no bind/result processing.
    """

    cache_ok = True

    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **_: object) -> str:
        return f"vector({self.dimensions})"


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Extensions. Required before the vector column type and the trigram operator class exist.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # 2. Enum types.
    for name, values in _ENUMS:
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    # 3. knowledge_documents ------------------------------------------------
    op.create_table(
        "knowledge_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), server_default="default", nullable=False),
        sa.Column("source_type", sa.String(length=48), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=True),
        sa.Column("mime_type", sa.String(length=160), nullable=True),
        sa.Column("source_uri", sa.Text(), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", _enum(*_ENUMS[0]), server_default="PENDING", nullable=False),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("quality_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("pii_kinds", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("parser", sa.String(length=64), nullable=True),
        sa.Column("department", sa.String(length=120), nullable=True),
        sa.Column("country", sa.String(length=8), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("category", sa.String(length=120), nullable=True),
        sa.Column("policy_version", sa.String(length=64), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("owner", sa.String(length=200), nullable=True),
        sa.Column("doc_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by_sub", sa.String(length=64), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["supersedes_id"], ["knowledge_documents.id"],
            name="fk_knowledge_documents_supersedes_id", ondelete="SET NULL",
        ),
        # Idempotent ingestion, enforced by the database rather than by a service check.
        sa.UniqueConstraint("tenant_id", "checksum_sha256",
                            name="uq_knowledge_documents_checksum"),
        sa.CheckConstraint("version >= 1", name="ck_knowledge_documents_version_positive"),
        sa.CheckConstraint(
            "expiry_date IS NULL OR effective_date IS NULL OR expiry_date >= effective_date",
            name="ck_knowledge_documents_date_order",
        ),
        sa.CheckConstraint(
            "quality_score IS NULL OR (quality_score >= 0 AND quality_score <= 100)",
            name="ck_knowledge_documents_quality_range",
        ),
        sa.CheckConstraint("size_bytes IS NULL OR size_bytes >= 0",
                          name="ck_knowledge_documents_size_non_negative"),
    )
    op.create_index("ix_knowledge_documents_tenant_id", "knowledge_documents", ["tenant_id"])
    op.create_index("ix_knowledge_documents_checksum_sha256", "knowledge_documents",
                    ["checksum_sha256"])
    op.create_index("ix_knowledge_documents_tenant_status", "knowledge_documents",
                    ["tenant_id", "status"])
    op.create_index("ix_knowledge_documents_tenant_source_type", "knowledge_documents",
                    ["tenant_id", "source_type"])
    op.create_index("ix_knowledge_documents_effective", "knowledge_documents",
                    ["tenant_id", "effective_date", "expiry_date"])
    op.create_index("ix_knowledge_documents_metadata", "knowledge_documents",
                    ["doc_metadata"], postgresql_using="gin")

    # 4. knowledge_chunks ---------------------------------------------------
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), server_default="default", nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_chunk_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_tokens", sa.Integer(), nullable=True),
        sa.Column("strategy", sa.String(length=32), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        # Generated so the lexical index can never drift from the text it indexes. 'simple' rather
        # than 'english': bge-m3 is multilingual and English stemming would penalise every other
        # language. An explicit regconfig keeps to_tsvector IMMUTABLE, which a generated
        # column requires.
        sa.Column(
            "content_tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple', content)", persisted=True),
            nullable=True,
        ),
        sa.Column("section", sa.String(length=500), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("heading_path", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("policy_version", sa.String(length=64), nullable=True),
        sa.Column("department", sa.String(length=120), nullable=True),
        sa.Column("country", sa.String(length=8), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("category", sa.String(length=120), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("source_type", sa.String(length=48), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("owner", sa.String(length=200), nullable=True),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("chunk_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["document_id"], ["knowledge_documents.id"],
            name="fk_knowledge_chunks_document_id", ondelete="CASCADE",
        ),
        # SET NULL, not CASCADE: losing a parent must not delete the retrievable children.
        sa.ForeignKeyConstraint(
            ["parent_chunk_id"], ["knowledge_chunks.id"],
            name="fk_knowledge_chunks_parent_chunk_id", ondelete="SET NULL",
        ),
        sa.UniqueConstraint("document_id", "chunk_index",
                            name="uq_knowledge_chunks_document_index"),
        sa.CheckConstraint("chunk_index >= 0", name="ck_knowledge_chunks_index_non_negative"),
        sa.CheckConstraint("length(btrim(content)) > 0",
                          name="ck_knowledge_chunks_content_not_blank"),
        sa.CheckConstraint("content_tokens IS NULL OR content_tokens >= 0",
                          name="ck_knowledge_chunks_tokens_non_negative"),
        sa.CheckConstraint(
            "expiry_date IS NULL OR effective_date IS NULL OR expiry_date >= effective_date",
            name="ck_knowledge_chunks_date_order",
        ),
    )
    op.create_index("ix_knowledge_chunks_tenant_id", "knowledge_chunks", ["tenant_id"])
    op.create_index("ix_knowledge_chunks_checksum_sha256", "knowledge_chunks",
                    ["checksum_sha256"])
    op.create_index("ix_knowledge_chunks_tenant_document", "knowledge_chunks",
                    ["tenant_id", "document_id"])
    op.create_index("ix_knowledge_chunks_filters", "knowledge_chunks",
                    ["tenant_id", "category", "country", "currency"])
    op.create_index("ix_knowledge_chunks_effective", "knowledge_chunks",
                    ["tenant_id", "effective_date", "expiry_date"])
    op.create_index("ix_knowledge_chunks_parent", "knowledge_chunks", ["parent_chunk_id"])
    op.create_index("ix_knowledge_chunks_metadata", "knowledge_chunks",
                    ["chunk_metadata"], postgresql_using="gin")
    op.create_index("ix_knowledge_chunks_tags", "knowledge_chunks",
                    ["tags"], postgresql_using="gin")
    # The lexical retrieval leg.
    op.create_index("ix_knowledge_chunks_tsv", "knowledge_chunks",
                    ["content_tsv"], postgresql_using="gin")
    # Fuzzy matching (vendor aliases, OCR near-duplicates). Needs pg_trgm from step 1.
    op.create_index("ix_knowledge_chunks_content_trgm", "knowledge_chunks", ["content"],
                    postgresql_using="gin", postgresql_ops={"content": "gin_trgm_ops"})

    # 5. knowledge_embeddings ----------------------------------------------
    op.create_table(
        "knowledge_embeddings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), server_default="default", nullable=False),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("embedding_provider", sa.String(length=64), nullable=False),
        sa.Column("embedding_model", sa.String(length=160), nullable=False),
        sa.Column("embedding_version", sa.String(length=64), nullable=False),
        sa.Column("spec_key", sa.String(length=320), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("embedding", _Vector(EMBEDDING_DIMENSIONS), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("created_by_sub", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["chunk_id"], ["knowledge_chunks.id"],
            name="fk_knowledge_embeddings_chunk_id", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["knowledge_documents.id"],
            name="fk_knowledge_embeddings_document_id", ondelete="CASCADE",
        ),
        # One vector per chunk per model version, so a re-index under a new version lands alongside
        # the old vectors instead of overwriting them.
        sa.UniqueConstraint("chunk_id", "spec_key", name="uq_knowledge_embeddings_chunk_spec"),
        sa.CheckConstraint("dimensions > 0", name="ck_knowledge_embeddings_dimensions_positive"),
        sa.CheckConstraint("latency_ms IS NULL OR latency_ms >= 0",
                          name="ck_knowledge_embeddings_latency_non_negative"),
        sa.CheckConstraint("cost_usd IS NULL OR cost_usd >= 0",
                          name="ck_knowledge_embeddings_cost_non_negative"),
    )
    op.create_index("ix_knowledge_embeddings_tenant_id", "knowledge_embeddings", ["tenant_id"])
    op.create_index("ix_knowledge_embeddings_tenant_spec", "knowledge_embeddings",
                    ["tenant_id", "spec_key"])
    op.create_index("ix_knowledge_embeddings_document", "knowledge_embeddings", ["document_id"])
    # Approximate-nearest-neighbour index, expressed through Alembic (not raw SQL) so the model's
    # matching Index declaration reconciles and `--autogenerate` stays empty.
    op.create_index(
        "ix_knowledge_embeddings_hnsw",
        "knowledge_embeddings",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_with={"m": HNSW_M, "ef_construction": HNSW_EF_CONSTRUCTION},
    )

    # 6. knowledge_ingestion_runs ------------------------------------------
    op.create_table(
        "knowledge_ingestion_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), server_default="default", nullable=False),
        # SET NULL: a run's history survives deletion of the document it produced.
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", _enum(*_ENUMS[1]), server_default="RUNNING", nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=True),
        sa.Column("source_type", sa.String(length=48), nullable=True),
        sa.Column("source_uri", sa.Text(), nullable=True),
        sa.Column("file_name", sa.Text(), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("chunks_created", sa.Integer(), server_default="0", nullable=False),
        sa.Column("chunks_skipped", sa.Integer(), server_default="0", nullable=False),
        sa.Column("embeddings_created", sa.Integer(), server_default="0", nullable=False),
        sa.Column("embedding_spec_key", sa.String(length=320), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("stage_timings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("actor_sub", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["document_id"], ["knowledge_documents.id"],
            name="fk_ingestion_runs_document_id", ondelete="SET NULL",
        ),
        sa.CheckConstraint("chunks_created >= 0", name="ck_ingestion_runs_chunks_non_negative"),
        sa.CheckConstraint("embeddings_created >= 0",
                          name="ck_ingestion_runs_embeddings_non_negative"),
        sa.CheckConstraint("duration_ms IS NULL OR duration_ms >= 0",
                          name="ck_ingestion_runs_duration_non_negative"),
    )
    op.create_index("ix_knowledge_ingestion_runs_tenant_id", "knowledge_ingestion_runs",
                    ["tenant_id"])
    op.create_index("ix_knowledge_ingestion_runs_checksum_sha256", "knowledge_ingestion_runs",
                    ["checksum_sha256"])
    op.create_index("ix_ingestion_runs_tenant_status", "knowledge_ingestion_runs",
                    ["tenant_id", "status"])
    op.create_index("ix_ingestion_runs_document", "knowledge_ingestion_runs", ["document_id"])
    op.create_index("ix_ingestion_runs_started_at", "knowledge_ingestion_runs", ["started_at"])
    op.create_index("ix_ingestion_runs_correlation_id", "knowledge_ingestion_runs",
                    ["correlation_id"])


def downgrade() -> None:
    """Drop only what this revision created.

    The ``vector`` and ``pg_trgm`` extensions are deliberately left installed: dropping ``vector``
    would cascade into anything else using the type, and dropping ``pg_trgm`` could take unrelated
    indexes with it. Neither is this revision's to destroy.
    """
    bind = op.get_bind()

    op.drop_table("knowledge_ingestion_runs")
    # Indexes and constraints go with their tables; the HNSW index needs no separate drop.
    op.drop_table("knowledge_embeddings")
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_documents")

    for name, values in reversed(_ENUMS):
        postgresql.ENUM(*values, name=name).drop(bind, checkfirst=True)
