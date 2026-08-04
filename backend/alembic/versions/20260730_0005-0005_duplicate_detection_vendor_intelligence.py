"""Duplicate detection & vendor intelligence: vendor identity + claim fingerprints.

Revision ID: 0005_duplicate_detection
Revises: 0004_ai_knowledge_platform
Create Date: 2026-07-30

Strictly **additive**: no existing table, column, constraint or index is altered (ADR-001
STOP-on-conflict policy). ``vector`` and ``pg_trgm`` are already installed by migration ``0004``;
this revision only creates ``IF NOT EXISTS`` as a defensive no-op in case a downgrade of ``0004``
ever ran without dropping them, never assuming the prior migration's state.

Two tables:

1. **``ai_vendor_profiles`` / ``ai_vendor_aliases``** — canonical vendor identity. A raw string like
   ``merchant_vendor`` on a claim resolves to one profile via an exact alias lookup first, falling
   back to a ``pg_trgm`` word-similarity scan (built by the engine, not this migration — see
   ``app/ai/repositories/duplicate_detection_repository.py``).
2. **``ai_claim_fingerprints``** — one row per claim, carrying the relational facts (vendor, date,
   amount, employee) always, and the content-derived signals (checksum, perceptual hashes, OCR
   text/embedding) only when receipt bytes were available to compute them.

**Downgrade leaves the two extensions installed**, for the same reason migration ``0004`` does:
neither is this revision's to destroy.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.types import UserDefinedType

# revision identifiers, used by Alembic.
revision: str = "0005_duplicate_detection"
down_revision: Union[str, None] = "0004_ai_knowledge_platform"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Must match app.ai.core.config.BGE_M3_DIMENSIONS / app.ai.models.knowledge.EMBEDDING_DIMENSIONS.
EMBEDDING_DIMENSIONS = 1024


class _Vector(UserDefinedType):
    """Minimal ``vector(n)`` type, local to this revision — see migration 0004 for why."""

    cache_ok = True

    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **_: object) -> str:
        return f"vector({self.dimensions})"


def upgrade() -> None:
    # Defensive only: both are already installed by 0004. IF NOT EXISTS makes this a no-op there.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # 1. ai_vendor_profiles ---------------------------------------------------
    op.create_table(
        "ai_vendor_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), server_default="default", nullable=False),
        sa.Column("canonical_name", sa.String(length=200), nullable=False),
        sa.Column("risk_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("claim_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_spend_usd", sa.Numeric(precision=14, scale=2),
                  server_default="0", nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "canonical_name",
                            name="uq_vendor_profiles_canonical_name"),
        sa.CheckConstraint("claim_count >= 0", name="ck_vendor_profiles_claim_count_non_negative"),
        sa.CheckConstraint("total_spend_usd >= 0",
                          name="ck_vendor_profiles_total_spend_non_negative"),
        sa.CheckConstraint(
            "risk_score IS NULL OR (risk_score >= 0 AND risk_score <= 100)",
            name="ck_vendor_profiles_risk_score_range",
        ),
    )
    op.create_index("ix_ai_vendor_profiles_tenant_id", "ai_vendor_profiles", ["tenant_id"])
    op.create_index("ix_vendor_profiles_tenant_risk", "ai_vendor_profiles",
                    ["tenant_id", "risk_score"])

    # 2. ai_vendor_aliases -----------------------------------------------------
    op.create_table(
        "ai_vendor_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), server_default="default", nullable=False),
        sa.Column("vendor_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alias_raw", sa.String(length=200), nullable=False),
        sa.Column("alias_normalized", sa.String(length=200), nullable=False),
        sa.Column("source", sa.String(length=16), server_default="AUTO", nullable=False),
        sa.Column("confidence", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["vendor_profile_id"], ["ai_vendor_profiles.id"],
            name="fk_vendor_aliases_vendor_profile_id", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "alias_normalized",
                            name="uq_vendor_aliases_tenant_normalized"),
    )
    op.create_index("ix_ai_vendor_aliases_tenant_id", "ai_vendor_aliases", ["tenant_id"])
    op.create_index("ix_vendor_aliases_profile", "ai_vendor_aliases", ["vendor_profile_id"])
    # Word-similarity / trigram alias resolution fallback.
    op.create_index("ix_vendor_aliases_normalized_trgm", "ai_vendor_aliases", ["alias_normalized"],
                    postgresql_using="gin", postgresql_ops={"alias_normalized": "gin_trgm_ops"})

    # 3. ai_claim_fingerprints --------------------------------------------------
    op.create_table(
        "ai_claim_fingerprints",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), server_default="default", nullable=False),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("merchant_vendor", sa.String(length=200), nullable=False),
        sa.Column("vendor_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expense_date", sa.Date(), nullable=False),
        sa.Column("amount_usd", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("invoice_number", sa.String(length=120), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("average_hash", sa.String(length=32), nullable=True),
        sa.Column("difference_hash", sa.String(length=32), nullable=True),
        sa.Column("ocr_text_excerpt", sa.Text(), nullable=True),
        sa.Column("ocr_embedding", _Vector(EMBEDDING_DIMENSIONS), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["vendor_profile_id"], ["ai_vendor_profiles.id"],
            name="fk_claim_fingerprints_vendor_profile_id", ondelete="SET NULL",
        ),
        sa.UniqueConstraint("claim_id", name="uq_claim_fingerprints_claim_id"),
        sa.CheckConstraint("amount_usd >= 0", name="ck_claim_fingerprints_amount_non_negative"),
    )
    op.create_index("ix_ai_claim_fingerprints_tenant_id", "ai_claim_fingerprints", ["tenant_id"])
    op.create_index("ix_claim_fingerprints_tenant_employee", "ai_claim_fingerprints",
                    ["tenant_id", "employee_id"])
    op.create_index("ix_claim_fingerprints_tenant_vendor_date", "ai_claim_fingerprints",
                    ["tenant_id", "vendor_profile_id", "expense_date"])
    op.create_index("ix_claim_fingerprints_checksum", "ai_claim_fingerprints",
                    ["tenant_id", "checksum_sha256"])


def downgrade() -> None:
    """Drop only what this revision created. The extensions are left installed (0004 owns them)."""
    op.drop_table("ai_claim_fingerprints")
    op.drop_table("ai_vendor_aliases")
    op.drop_table("ai_vendor_profiles")
