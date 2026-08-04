"""Prompt registry & AI governance: prompt templates, model/provider registry, flag overrides.

Revision ID: 0006_prompt_governance
Revises: 0005_duplicate_detection
Create Date: 2026-07-30

Strictly **additive**: no existing table, column, constraint or index is altered (ADR-001
STOP-on-conflict policy).

Three tables:

1. **``ai_prompt_templates``** — versioned, immutable prompt content. ``ai_prompt_status``
   (``DRAFT``/``PUBLISHED``/``RETIRED``) was declared as a Python enum back in migration ``0004``'s
   era of work (``app/ai/core/enums.py``, M1) but never actually created as a database type until
   now — this is the first table to use it.
2. **``ai_registry_entries``** — one versioned, auditable row per governed model/provider/embedding
   configuration, discriminated by ``kind`` rather than three near-identical tables.
3. **``ai_flag_overrides``** — the durable layer above ``app.ai.registry.flags.FeatureFlags``'
   in-process-only override store.

**Downgrade drops the ``ai_prompt_status`` enum type it creates** — unlike migration ``0004``'s
``vector``/``pg_trgm`` extensions (shared, third-party, never this revision's to destroy), this type
is owned outright by this revision and has no other consumer.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0006_prompt_governance"
down_revision: Union[str, None] = "0005_duplicate_detection"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PROMPT_STATUS_ENUM_NAME = "ai_prompt_status"
_PROMPT_STATUS_VALUES = ("DRAFT", "PUBLISHED", "RETIRED")


def upgrade() -> None:
    bind = op.get_bind()

    postgresql.ENUM(*_PROMPT_STATUS_VALUES, name=_PROMPT_STATUS_ENUM_NAME).create(
        bind, checkfirst=True
    )

    # 1. ai_prompt_templates ----------------------------------------------------
    op.create_table(
        "ai_prompt_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(*_PROMPT_STATUS_VALUES, name=_PROMPT_STATUS_ENUM_NAME,
                            create_type=False),
            server_default="PUBLISHED", nullable=False,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("template_text", sa.Text(), nullable=False),
        sa.Column("variables", postgresql.JSONB(astext_type=sa.Text()),
                  server_default="[]", nullable=False),
        sa.Column("created_by_sub", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", "version", name="uq_ai_prompt_templates_code_version"),
        sa.CheckConstraint("version > 0", name="ck_ai_prompt_templates_version_positive"),
        sa.CheckConstraint("length(btrim(template_text)) > 0",
                          name="ck_ai_prompt_templates_text_not_blank"),
    )
    op.create_index("ix_ai_prompt_templates_code_status", "ai_prompt_templates",
                    ["code", "status"])

    # 2. ai_registry_entries -----------------------------------------------------
    op.create_table(
        "ai_registry_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()),
                  server_default="{}", nullable=False),
        sa.Column("cost_per_unit_usd", sa.Numeric(precision=14, scale=10), nullable=True),
        sa.Column("cost_unit", sa.String(length=32), nullable=True),
        sa.Column("created_by_sub", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "name", "version",
                            name="uq_ai_registry_entries_kind_name_version"),
        sa.CheckConstraint("version > 0", name="ck_ai_registry_entries_version_positive"),
        sa.CheckConstraint(
            "cost_per_unit_usd IS NULL OR cost_per_unit_usd >= 0",
            name="ck_ai_registry_entries_cost_non_negative",
        ),
    )
    op.create_index("ix_ai_registry_entries_kind_active", "ai_registry_entries",
                    ["kind", "is_active"])

    # 3. ai_flag_overrides --------------------------------------------------------
    op.create_table(
        "ai_flag_overrides",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("flag_name", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_by_sub", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("flag_name", name="uq_ai_flag_overrides_flag_name"),
    )


def downgrade() -> None:
    """Drop everything this revision created, including the ``ai_prompt_status`` enum type it
    owns outright (no other table uses it)."""
    bind = op.get_bind()

    op.drop_table("ai_flag_overrides")
    op.drop_table("ai_registry_entries")
    op.drop_table("ai_prompt_templates")

    postgresql.ENUM(*_PROMPT_STATUS_VALUES, name=_PROMPT_STATUS_ENUM_NAME).drop(
        bind, checkfirst=True
    )
