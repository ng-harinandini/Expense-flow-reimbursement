"""policy document sections: section-scoped extraction, provenance, conflicts and validation

Supports splitting rule extraction into one LLM call per logical section instead of one call per
document, which is what makes a 500-page policy extractable at all.

**Purely additive.** One new table plus new nullable columns on two existing ones. Nothing is
altered or dropped, ``policy_rules`` is untouched (as in ``0009``), and every added column is either
nullable or carries a server default — so an existing row is valid the moment this runs and the
approval/publication path behaves exactly as it did before.

    policy_document_sections            one row per logical section, and the call that processed it
    policy_rule_proposal_items.rule_id  stable slot id, for diffing and conflict grouping
    policy_rule_proposal_items.section_id / .validation_warnings   traceability and findings
    policy_rule_proposals.conflicts / .prompt_hash / .schema_version / section counts

Three deliberate choices worth stating, because each has a plausible alternative:

* ``policy_document_sections.knowledge_document_id`` **cascades**, unlike the proposal's
  ``RESTRICT``.
  A section is a recomputable index over chunks, not an audit record; there is nothing in it worth
  outliving the document it describes.
* ``policy_rule_proposal_items.section_id`` is **SET NULL**. Sections are replaced wholesale
  on every run, and losing one must never cascade into deleting a rule a human has reviewed.
* ``rule_id`` is indexed but **not unique per proposal**. When two sections disagree about the same
  rule, both readings are kept under one id and the disagreement is recorded in
  ``policy_rule_proposals.conflicts``. A unique constraint would force one side to be silently
  dropped — precisely the behaviour conflict detection exists to prevent.

Sections are recomputed and replaced on each extraction run rather than versioned. That is enough to
retry a failed section *within* a run (its text is already stored); skipping an unchanged section
*across* runs would need content hashing and is deliberately not built here.

``downgrade()`` is complete and removes only what ``upgrade()`` created.

Revision ID: 0010_policy_extraction_sections
Revises: 0009_policy_rule_proposals
Create Date: 2026-08-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0010_policy_extraction_sections"
down_revision: Union[str, None] = "0009_policy_rule_proposals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- 1. policy_document_sections -----------------------------------------
    op.create_table(
        "policy_document_sections",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        # CASCADE, unlike the proposal's RESTRICT: sections are derived data, regenerated on every
        # run, with nothing in them worth outliving their document.
        sa.Column(
            "knowledge_document_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "knowledge_documents.id", ondelete="CASCADE",
                name="fk_policy_document_sections_document_id",
            ),
            nullable=False,
        ),
        sa.Column("section_index", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("start_page", sa.Integer(), nullable=False),
        sa.Column("end_page", sa.Integer(), nullable=False),

        # --- content: the exact text sent to the model, so a disputed rule can be checked against
        # what was actually read rather than a re-derived approximation of it.
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("char_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunk_ids", postgresql.JSONB(), nullable=True),
        sa.Column("table_detected", sa.Boolean(), nullable=False, server_default="false"),

        # --- how the boundary was found. VARCHAR, not an enum: a new detector must not need a
        # migration.
        sa.Column("detection_method", sa.String(32), nullable=True),
        sa.Column("detection_confidence", sa.Numeric(4, 2), nullable=True),

        # --- provenance: enough to replay the call that produced this section's rules.
        sa.Column("llm_provider", sa.String(64), nullable=True),
        sa.Column("llm_model", sa.String(160), nullable=True),
        sa.Column("prompt_version", sa.String(64), nullable=True),
        sa.Column("prompt_hash", sa.String(64), nullable=True),
        sa.Column("schema_version", sa.String(16), nullable=True),
        # No `temperature`: current Claude models reject a non-default sampling parameter outright,
        # so a temperature column would record a value that was never sent.
        sa.Column("reasoning_effort", sa.String(16), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),

        # --- lifecycle: what makes a partially-failed extraction honest rather than silently short.
        sa.Column("status", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rules_extracted_count", sa.Integer(), nullable=False, server_default="0"),

        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(),
        ),

        sa.UniqueConstraint(
            "knowledge_document_id", "section_index", name="uq_policy_document_sections_index"
        ),
        sa.CheckConstraint(
            "section_index >= 0", name="ck_policy_document_sections_index_positive"
        ),
        sa.CheckConstraint(
            "start_page > 0 AND end_page >= start_page",
            name="ck_policy_document_sections_page_range",
        ),
        sa.CheckConstraint(
            "char_count >= 0 AND token_count >= 0 AND chunk_count >= 0 AND retry_count >= 0",
            name="ck_policy_document_sections_counts_non_negative",
        ),
        sa.CheckConstraint(
            "detection_confidence IS NULL "
            "OR (detection_confidence >= 0 AND detection_confidence <= 1)",
            name="ck_policy_document_sections_confidence_range",
        ),
        # A gap in the document must state its cause, or a provider outage is indistinguishable
        # from a section that legitimately held no rules.
        sa.CheckConstraint(
            "status <> 'FAILED' OR error_message IS NOT NULL",
            name="ck_policy_document_sections_failed_has_error",
        ),
    )
    op.create_index(
        "ix_policy_document_sections_tenant_id", "policy_document_sections", ["tenant_id"]
    )
    op.create_index(
        "ix_policy_document_sections_document",
        "policy_document_sections", ["knowledge_document_id"],
    )
    op.create_index(
        "ix_policy_document_sections_status", "policy_document_sections", ["status"]
    )

    # --- 2. traceability and findings on the existing item table --------------
    op.add_column(
        "policy_rule_proposal_items", sa.Column("rule_id", sa.String(160), nullable=True)
    )
    op.add_column(
        "policy_rule_proposal_items",
        sa.Column("section_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "policy_rule_proposal_items",
        sa.Column("validation_warnings", postgresql.JSONB(), nullable=True),
    )
    op.create_foreign_key(
        "fk_policy_rule_proposal_items_section_id",
        "policy_rule_proposal_items", "policy_document_sections",
        ["section_id"], ["id"], ondelete="SET NULL",
    )
    # Indexed, not unique: competing readings of one rule share an id on purpose.
    op.create_index(
        "ix_policy_rule_proposal_items_rule_id", "policy_rule_proposal_items", ["rule_id"]
    )
    op.create_index(
        "ix_policy_rule_proposal_items_section", "policy_rule_proposal_items", ["section_id"]
    )

    # --- 3. conflicts and run provenance on the existing proposal table -------
    op.add_column(
        "policy_rule_proposals", sa.Column("conflicts", postgresql.JSONB(), nullable=True)
    )
    op.add_column("policy_rule_proposals", sa.Column("prompt_hash", sa.String(64), nullable=True))
    op.add_column(
        "policy_rule_proposals", sa.Column("schema_version", sa.String(16), nullable=True)
    )
    op.add_column(
        "policy_rule_proposals",
        sa.Column("sections_total", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "policy_rule_proposals",
        sa.Column("sections_failed", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_policy_rule_proposals_section_counts",
        "policy_rule_proposals",
        "sections_total >= 0 AND sections_failed >= 0 AND sections_failed <= sections_total",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_policy_rule_proposals_section_counts", "policy_rule_proposals", type_="check"
    )
    op.drop_column("policy_rule_proposals", "sections_failed")
    op.drop_column("policy_rule_proposals", "sections_total")
    op.drop_column("policy_rule_proposals", "schema_version")
    op.drop_column("policy_rule_proposals", "prompt_hash")
    op.drop_column("policy_rule_proposals", "conflicts")

    # The FK must go before the table it points at.
    op.drop_index(
        "ix_policy_rule_proposal_items_section", table_name="policy_rule_proposal_items"
    )
    op.drop_index(
        "ix_policy_rule_proposal_items_rule_id", table_name="policy_rule_proposal_items"
    )
    op.drop_constraint(
        "fk_policy_rule_proposal_items_section_id",
        "policy_rule_proposal_items", type_="foreignkey",
    )
    op.drop_column("policy_rule_proposal_items", "validation_warnings")
    op.drop_column("policy_rule_proposal_items", "section_id")
    op.drop_column("policy_rule_proposal_items", "rule_id")

    op.drop_index("ix_policy_document_sections_status", table_name="policy_document_sections")
    op.drop_index("ix_policy_document_sections_document", table_name="policy_document_sections")
    op.drop_index("ix_policy_document_sections_tenant_id", table_name="policy_document_sections")
    op.drop_table("policy_document_sections")
