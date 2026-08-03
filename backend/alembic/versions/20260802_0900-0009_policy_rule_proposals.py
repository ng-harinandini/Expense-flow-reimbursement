"""policy rule proposals: extracted rules awaiting review, plus per-page document metadata

Adds three tables and one enum type. **``policy_rules`` is deliberately not touched** — no column
added, none altered, none dropped. The live table the policy engine reads keeps exactly the shape it
had after ``0008``, so this migration cannot change how any existing claim is judged. Extracted
rules land in a staging table and reach ``policy_rules`` only through
``PolicyRuleService.replace_ruleset`` after a human approves them.

    policy_rule_proposals        one extraction run over one knowledge_documents version
    policy_rule_proposal_items   one row per rule read out of that document
    policy_document_pages        one row per page, with its extraction metadata

The two columns that justify a staging table rather than writing straight through:

* ``basis`` — ``policy_rules.expense_limit`` is a scalar, so "$40 per day" and "$40 per claim"
  collapse to the same row. A reviewer has to see the difference; the engine cannot.
* ``representable`` — a policy document states rules for categories ``expense_categories`` does not
  have. Inventing rows there would break the lockstep invariant ``tests/test_migrations.py``
  asserts, and dropping the rules would hide real policy. So they are stored and flagged.

``downgrade()`` is complete and drops only what ``upgrade()`` created.

Revision ID: 0009_policy_rule_proposals
Revises: 0008_multi_item_claims
Create Date: 2026-08-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0009_policy_rule_proposals"
down_revision: Union[str, None] = "0008_multi_item_claims"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MONEY = sa.Numeric(14, 2)

PROPOSAL_STATUS_ENUM = "ai_proposal_status"
PROPOSAL_STATUS_VALUES: tuple[str, ...] = ("DRAFT", "APPROVED", "REJECTED", "SUPERSEDED")


def _enum(name: str, values: tuple[str, ...]) -> postgresql.ENUM:
    """A named PG enum bound to no metadata, so only this migration creates or drops it."""
    return postgresql.ENUM(*values, name=name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()

    # --- 1. proposal status enum ---------------------------------------------
    _enum(PROPOSAL_STATUS_ENUM, PROPOSAL_STATUS_VALUES).create(bind, checkfirst=True)
    status = _enum(PROPOSAL_STATUS_ENUM, PROPOSAL_STATUS_VALUES)

    # --- 2. policy_rule_proposals -------------------------------------------
    op.create_table(
        "policy_rule_proposals",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        # RESTRICT: a proposal is the audit record of what was proposed, so archiving the source
        # document must not erase the reason a limit was published.
        sa.Column(
            "knowledge_document_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "knowledge_documents.id", ondelete="RESTRICT",
                name="fk_policy_rule_proposals_document_id",
            ),
            nullable=False,
        ),
        sa.Column("document_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("document_title", sa.Text(), nullable=True),
        sa.Column("status", status, nullable=False, server_default="DRAFT"),
        # provenance — an extracted limit is only auditable if you can say which model read which
        # version of which document under which prompt.
        sa.Column("llm_provider", sa.String(64), nullable=True),
        sa.Column("llm_model", sa.String(160), nullable=True),
        sa.Column("prompt_code", sa.String(64), nullable=True),
        sa.Column("prompt_version", sa.String(64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("rules_extracted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rules_representable", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("document_effective_date", sa.Date(), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("created_by_sub", sa.String(64), nullable=True),
        sa.Column("approved_by_sub", sa.String(64), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_effective_date", sa.Date(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(),
        ),
        # An approval with no approver would defeat the point of requiring one.
        sa.CheckConstraint(
            "(status <> 'APPROVED') OR (approved_by_sub IS NOT NULL AND approved_at IS NOT NULL)",
            name="ck_policy_rule_proposals_approved_has_approver",
        ),
        sa.CheckConstraint(
            "(status <> 'REJECTED') OR (review_notes IS NOT NULL)",
            name="ck_policy_rule_proposals_rejected_has_reason",
        ),
        sa.CheckConstraint(
            "rules_extracted >= 0 AND rules_representable >= 0 "
            "AND rules_representable <= rules_extracted",
            name="ck_policy_rule_proposals_counts_consistent",
        ),
    )
    op.create_index(
        "ix_policy_rule_proposals_tenant_id", "policy_rule_proposals", ["tenant_id"]
    )
    op.create_index(
        "ix_policy_rule_proposals_document", "policy_rule_proposals", ["knowledge_document_id"]
    )
    op.create_index("ix_policy_rule_proposals_status", "policy_rule_proposals", ["status"])
    op.create_index(
        "ix_policy_rule_proposals_tenant_status",
        "policy_rule_proposals", ["tenant_id", "status"],
    )

    # --- 3. policy_rule_proposal_items --------------------------------------
    op.create_table(
        "policy_rule_proposal_items",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column(
            "proposal_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "policy_rule_proposals.id", ondelete="CASCADE",
                name="fk_policy_rule_proposal_items_proposal_id",
            ),
            nullable=False,
        ),
        sa.Column("line_number", sa.Integer(), nullable=False),
        # --- mirrors PolicyRuleDefinitionSchema ---
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("sub_category", sa.String(120), nullable=True),
        sa.Column("grade_tier", sa.String(64), nullable=False, server_default="All Staff"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("max_amount", MONEY, nullable=True),
        sa.Column("auto_approve_limit", MONEY, nullable=True),
        sa.Column("receipt_required_above", MONEY, nullable=True),
        sa.Column(
            "requires_pre_approval", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("special_rules", postgresql.JSONB(), nullable=True),
        # --- the dimensions policy_rules cannot express ---
        sa.Column("basis", sa.String(32), nullable=False, server_default="OTHER"),
        sa.Column("limit_expression", sa.String(120), nullable=True),
        sa.Column("always_manual", sa.Boolean(), nullable=False, server_default="false"),
        # --- evidence ---
        sa.Column("page_numbers", postgresql.JSONB(), nullable=True),
        sa.Column("source_chunk_ids", postgresql.JSONB(), nullable=True),
        sa.Column("source_quote", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 2), nullable=True),
        # --- publishability ---
        sa.Column("representable", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("unrepresentable_reason", sa.Text(), nullable=True),
        # --- reviewer edits ---
        sa.Column("reviewer_edited", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("reviewer_notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "proposal_id", "line_number", name="uq_policy_rule_proposal_items_line"
        ),
        sa.CheckConstraint(
            "line_number > 0", name="ck_policy_rule_proposal_items_line_positive"
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_policy_rule_proposal_items_confidence_range",
        ),
        sa.CheckConstraint(
            "max_amount IS NULL OR max_amount >= 0",
            name="ck_policy_rule_proposal_items_max_amount_non_negative",
        ),
        sa.CheckConstraint(
            "auto_approve_limit IS NULL OR auto_approve_limit >= 0",
            name="ck_policy_rule_proposal_items_auto_approve_non_negative",
        ),
        sa.CheckConstraint(
            "receipt_required_above IS NULL OR receipt_required_above >= 0",
            name="ck_policy_rule_proposal_items_receipt_non_negative",
        ),
        # An excluded rule with no stated reason is indistinguishable from an extraction bug.
        sa.CheckConstraint(
            "representable OR unrepresentable_reason IS NOT NULL",
            name="ck_policy_rule_proposal_items_unrepresentable_has_reason",
        ),
        # "always manual" says never auto-approve; a threshold names the amount below which it
        # happens automatically. Holding both would publish the more permissive reading.
        sa.CheckConstraint(
            "NOT (always_manual AND auto_approve_limit IS NOT NULL)",
            name="ck_policy_rule_proposal_items_manual_excludes_threshold",
        ),
        sa.CheckConstraint(
            "char_length(currency) = 3", name="ck_policy_rule_proposal_items_currency_iso4217"
        ),
    )
    op.create_index(
        "ix_policy_rule_proposal_items_tenant_id", "policy_rule_proposal_items", ["tenant_id"]
    )
    op.create_index(
        "ix_policy_rule_proposal_items_proposal", "policy_rule_proposal_items", ["proposal_id"]
    )
    op.create_index(
        "ix_policy_rule_proposal_items_category", "policy_rule_proposal_items", ["category"]
    )
    op.create_index(
        "ix_policy_rule_proposal_items_representable",
        "policy_rule_proposal_items", ["representable"],
    )

    # --- 4. policy_document_pages -------------------------------------------
    op.create_table(
        "policy_document_pages",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column(
            "knowledge_document_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "knowledge_documents.id", ondelete="CASCADE",
                name="fk_policy_document_pages_document_id",
            ),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("section_heading", sa.String(500), nullable=True),
        sa.Column("char_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("table_detected", sa.Boolean(), nullable=False, server_default="false"),
        # The PDF parser's Textract fallback fires per page, so one bad page in an otherwise clean
        # document is both possible and invisible without recording it.
        sa.Column("ocr_used", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("rules_extracted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunk_ids", postgresql.JSONB(), nullable=True),
        sa.Column("page_metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "knowledge_document_id", "page_number", name="uq_policy_document_pages_page"
        ),
        sa.CheckConstraint("page_number > 0", name="ck_policy_document_pages_page_positive"),
        sa.CheckConstraint(
            "char_count >= 0 AND token_count >= 0 AND chunk_count >= 0 "
            "AND rules_extracted_count >= 0",
            name="ck_policy_document_pages_counts_non_negative",
        ),
    )
    op.create_index(
        "ix_policy_document_pages_tenant_id", "policy_document_pages", ["tenant_id"]
    )
    op.create_index(
        "ix_policy_document_pages_document", "policy_document_pages", ["knowledge_document_id"]
    )
    op.create_index(
        "ix_policy_document_pages_tables", "policy_document_pages", ["table_detected"]
    )


def downgrade() -> None:
    bind = op.get_bind()

    # Children before parents; indexes go with their tables.
    op.drop_index("ix_policy_document_pages_tables", table_name="policy_document_pages")
    op.drop_index("ix_policy_document_pages_document", table_name="policy_document_pages")
    op.drop_index("ix_policy_document_pages_tenant_id", table_name="policy_document_pages")
    op.drop_table("policy_document_pages")

    op.drop_index(
        "ix_policy_rule_proposal_items_representable", table_name="policy_rule_proposal_items"
    )
    op.drop_index(
        "ix_policy_rule_proposal_items_category", table_name="policy_rule_proposal_items"
    )
    op.drop_index(
        "ix_policy_rule_proposal_items_proposal", table_name="policy_rule_proposal_items"
    )
    op.drop_index(
        "ix_policy_rule_proposal_items_tenant_id", table_name="policy_rule_proposal_items"
    )
    op.drop_table("policy_rule_proposal_items")

    op.drop_index("ix_policy_rule_proposals_tenant_status", table_name="policy_rule_proposals")
    op.drop_index("ix_policy_rule_proposals_status", table_name="policy_rule_proposals")
    op.drop_index("ix_policy_rule_proposals_document", table_name="policy_rule_proposals")
    op.drop_index("ix_policy_rule_proposals_tenant_id", table_name="policy_rule_proposals")
    op.drop_table("policy_rule_proposals")

    # Dropped last: the tables above referenced this type.
    _enum(PROPOSAL_STATUS_ENUM, PROPOSAL_STATUS_VALUES).drop(bind, checkfirst=True)
