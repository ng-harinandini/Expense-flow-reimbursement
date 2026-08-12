"""candidate_policy_rules: AI-extracted rule candidates pending human approval.

Adds four provenance columns to ``policy_rules`` so every approved rule traces back to
the exact document page and chunk from which Gemini extracted the candidate.

Creates ``candidate_policy_rules`` to store the AI-extracted drafts in a ``PENDING``
state until a Finance/Admin user either approves (→ published to ``policy_rules``) or
dismisses them.

Revision ID: 0009_candidate_policy_rules
Revises: 0008_multi_item_claims
Create Date: 2026-08-04
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_candidate_policy_rules"
down_revision: Union[str, None] = "0008_multi_item_claims"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MONEY = sa.Numeric(14, 2)


def upgrade() -> None:
    # --- provenance columns on policy_rules ---
    # Nullable so existing manually-authored rules are unaffected.
    op.add_column(
        "policy_rules",
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "policy_rules",
        sa.Column("source_page_number", sa.Integer(), nullable=True),
    )
    op.add_column(
        "policy_rules",
        sa.Column("source_chunk_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "policy_rules",
        sa.Column("extracted_by", sa.String(64), nullable=True),
    )

    op.create_foreign_key(
        "fk_policy_rules_source_document",
        "policy_rules", "knowledge_documents",
        ["source_document_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_policy_rules_source_chunk",
        "policy_rules", "knowledge_chunks",
        ["source_chunk_id"], ["id"],
        ondelete="SET NULL",
    )

    # --- candidate_policy_rules table ---
    op.create_table(
        "candidate_policy_rules",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # --- source provenance ---
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "knowledge_documents.id",
                ondelete="SET NULL",
                name="fk_candidate_rules_document",
            ),
            nullable=True,
        ),
        sa.Column("source_page_number", sa.Integer(), nullable=True),
        sa.Column(
            "source_chunk_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "knowledge_chunks.id",
                ondelete="SET NULL",
                name="fk_candidate_rules_chunk",
            ),
            nullable=True,
        ),
        sa.Column(
            "extracted_by",
            sa.String(64),
            nullable=False,
            server_default="GEMINI-2.5-FLASH",
        ),
        # Groups all candidates from one extraction call so a batch can be dismissed together.
        sa.Column(
            "extraction_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        # --- rule payload (mirrors policy_rules columns) ---
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("code", sa.String(64), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("country", sa.String(2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("grade_tier", sa.String(64), nullable=True),
        sa.Column("expense_limit", MONEY, nullable=True),
        sa.Column("limit_expression", sa.String(120), nullable=True),
        sa.Column("auto_approve_limit", MONEY, nullable=True),
        sa.Column("receipt_required_above", MONEY, nullable=True),
        sa.Column(
            "requires_pre_approval",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("conditions", postgresql.JSONB(), nullable=True),
        sa.Column("actions", postgresql.JSONB(), nullable=True),
        sa.Column("special_rules", postgresql.JSONB(), nullable=True),
        # --- candidate workflow ---
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("reviewed_by_sub", sa.String(64), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "published_rule_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "policy_rules.id",
                ondelete="SET NULL",
                name="fk_candidate_rules_published",
            ),
            nullable=True,
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'DISMISSED')",
            name="ck_candidate_rules_status",
        ),
        sa.CheckConstraint(
            "expense_limit IS NULL OR expense_limit >= 0",
            name="ck_candidate_rules_limit_positive",
        ),
    )

    op.create_index(
        "ix_candidate_rules_document",
        "candidate_policy_rules",
        ["document_id"],
    )
    op.create_index(
        "ix_candidate_rules_run",
        "candidate_policy_rules",
        ["extraction_run_id"],
    )
    op.create_index(
        "ix_candidate_rules_status",
        "candidate_policy_rules",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_candidate_rules_status", table_name="candidate_policy_rules")
    op.drop_index("ix_candidate_rules_run", table_name="candidate_policy_rules")
    op.drop_index("ix_candidate_rules_document", table_name="candidate_policy_rules")
    op.drop_table("candidate_policy_rules")

    op.drop_constraint("fk_policy_rules_source_chunk", "policy_rules", type_="foreignkey")
    op.drop_constraint("fk_policy_rules_source_document", "policy_rules", type_="foreignkey")
    op.drop_column("policy_rules", "extracted_by")
    op.drop_column("policy_rules", "source_chunk_id")
    op.drop_column("policy_rules", "source_page_number")
    op.drop_column("policy_rules", "source_document_id")
