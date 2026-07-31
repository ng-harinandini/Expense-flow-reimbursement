"""multi-item claims: expense_categories + expense_items, receipts removed

Reshapes a claim from "one claim = one expense = one receipt" into an expense **report header**
owning N ``expense_items``, each with its own receipt, category, and verdict.

  * ``expense_categories`` — new reference table, seeded with the same five categories
    ``policy_rules`` already carries. ``expense_items.category`` keeps a text snapshot of the name
    because the policy engine string-matches on it.
  * ``expense_items``      — new child table. Everything per-expense moves here from ``claims``,
    plus the receipt inline (``file_url``/``file_hash``/``ocr_extracted_json``) and the per-item
    policy + fraud verdict.
  * ``claims``             — loses every per-expense column, gains ``title``/``purpose``/date range
    and the trigger-maintained ``total_amount``/``total_amount_usd``/``item_count`` roll-ups.
  * ``receipts``, ``receipt_fields``, ``receipt_line_items`` and the ``extraction_status`` enum are
    **dropped**; ``attachments`` and ``ai_inference_logs`` are retargeted at ``expense_items``.

**This revision is destructive by design.** Per the migration plan there is no production data, so
the per-expense claim columns are dropped without a backfill and ``downgrade()`` restores them as
nullable — the values themselves are unrecoverable, the same asymmetry ``0005_drop_departments``
already accepts.

The ``claim_status`` enum is deliberately **untouched**: ``Draft`` and ``Processing_AI`` remain, so
the ``claims_status_transition_guard`` trigger, ``app.domain.claim_state_machine``, and the
frontend's status vocabulary all keep working unchanged.

Enum values are copy-pasted rather than imported from ``app.models.enums`` (repo convention): the
DDL a revision emits must stay frozen even if the Python enum later changes.

Revision ID: 0008_multi_item_claims
Revises: 0007_merge_heads
Create Date: 2026-07-31
"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0008_multi_item_claims"
down_revision: Union[str, None] = "0007_merge_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MONEY = sa.Numeric(14, 2)

EXPENSE_ITEM_STATUS_ENUM = "expense_item_status"
EXPENSE_ITEM_STATUS_VALUES: tuple[str, ...] = (
    "Submitted",
    "Auto_Approved",
    "Policy_Hold",
    "Fraud_Flag",
    "Manager_Approved",
    "Rejected",
)

# Existing type, created by 0002 — referenced here, never created or dropped.
FRAUD_RISK_LEVEL_VALUES: tuple[str, ...] = ("LOW", "MEDIUM", "HIGH", "CRITICAL")

EXTRACTION_STATUS_ENUM = "extraction_status"
EXTRACTION_STATUS_VALUES: tuple[str, ...] = (
    "PENDING",
    "PROCESSING",
    "COMPLETED",
    "FAILED",
)

# Must match ``policy_rules.category`` exactly — the policy engine compares these strings.
SEED_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")
EXPENSE_CATEGORIES: tuple[tuple[str, str, int], ...] = (
    # (code, name, display_order)
    ("MEALS", "Meals", 10),
    ("GROUND_TRANSPORT", "Ground Transport", 20),
    ("FLIGHTS", "Flights", 30),
    ("LODGING", "Lodging", 40),
    ("CLIENT_ENTERTAINMENT", "Client Entertainment", 50),
)

# Columns dropped from ``claims``; recreated as nullable on downgrade.
CLAIM_EXPENSE_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("expense_date", sa.Date()),
    ("category", sa.String(64)),
    ("sub_category", sa.String(120)),
    ("amount", MONEY),
    ("amount_usd", MONEY),
    ("fx_rate", sa.Numeric(18, 8)),
    ("merchant_vendor", sa.String(200)),
    ("purpose_description", sa.Text()),
    ("attendees", sa.Text()),
    ("trip_log", postgresql.JSONB()),
    ("has_pre_approval", sa.Boolean()),
    ("pre_approval_doc_ref", sa.String(200)),
    ("receipt_attached", sa.Boolean()),
    ("receipt_url", sa.Text()),
    ("extracted_receipt", postgresql.JSONB()),
    ("policy_validation", postgresql.JSONB()),
)


def _enum(name: str, values: tuple[str, ...]) -> postgresql.ENUM:
    """Reference an enum type the migration creates explicitly."""
    return postgresql.ENUM(*values, name=name, create_type=False)


def _totals_trigger_sql() -> str:
    """Keeps ``claims`` roll-ups in step with ``expense_items``.

    Only the three roll-up columns are written — never ``version``, or every item insert would
    invalidate a concurrent reviewer's optimistic lock for no business reason. The UPDATE does
    fire ``claims_status_transition_guard``, which returns immediately because ``status`` is
    unchanged.
    """
    return """
    CREATE OR REPLACE FUNCTION expense_items_recalculate_claim_totals() RETURNS trigger AS $$
    DECLARE
        target uuid;
    BEGIN
        -- COALESCE covers DELETE (NEW is null) as well as INSERT/UPDATE.
        target := COALESCE(NEW.claim_id, OLD.claim_id);

        UPDATE claims c
           SET total_amount     = COALESCE(t.sum_amount, 0),
               total_amount_usd = COALESCE(t.sum_amount_usd, 0),
               item_count       = COALESCE(t.n, 0)
          FROM (SELECT SUM(amount)     AS sum_amount,
                       SUM(amount_usd) AS sum_amount_usd,
                       COUNT(*)        AS n
                  FROM expense_items WHERE claim_id = target) t
         WHERE c.id = target;

        -- An UPDATE that re-parented the item must also correct the claim it left.
        IF TG_OP = 'UPDATE' AND NEW.claim_id IS DISTINCT FROM OLD.claim_id THEN
            UPDATE claims c
               SET total_amount     = COALESCE(t.sum_amount, 0),
                   total_amount_usd = COALESCE(t.sum_amount_usd, 0),
                   item_count       = COALESCE(t.n, 0)
              FROM (SELECT SUM(amount)     AS sum_amount,
                           SUM(amount_usd) AS sum_amount_usd,
                           COUNT(*)        AS n
                      FROM expense_items WHERE claim_id = OLD.claim_id) t
             WHERE c.id = OLD.claim_id;
        END IF;

        RETURN NULL;  -- AFTER trigger: the return value is ignored
    END;
    $$ LANGUAGE plpgsql;
    """


def upgrade() -> None:
    bind = op.get_bind()

    # --- 1. new enum type -----------------------------------------------------
    postgresql.ENUM(*EXPENSE_ITEM_STATUS_VALUES, name=EXPENSE_ITEM_STATUS_ENUM).create(
        bind, checkfirst=True
    )

    # --- 2. expense_categories ------------------------------------------------
    op.create_table(
        "expense_categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_expense_categories_code"),
        sa.UniqueConstraint("name", name="uq_expense_categories_name"),
        sa.CheckConstraint(
            "char_length(btrim(name)) > 0", name="ck_expense_categories_name_not_blank"
        ),
        sa.CheckConstraint(
            "display_order >= 0", name="ck_expense_categories_display_order_non_negative"
        ),
    )
    op.create_index(
        "ix_expense_categories_is_active", "expense_categories", ["is_active"]
    )

    # --- 3. seed the five categories -----------------------------------------
    # Deterministic ids and ON CONFLICT DO NOTHING, matching the 0003 seed convention: re-running
    # is safe and never overwrites an operator's own edits.
    categories_table = sa.table(
        "expense_categories",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("display_order", sa.Integer),
    )
    for code, name, order in EXPENSE_CATEGORIES:
        bind.execute(
            postgresql.insert(categories_table)
            .values(
                id=uuid.uuid5(SEED_NAMESPACE, f"expenseflow:expense_category:{code}"),
                code=code,
                name=name,
                display_order=order,
            )
            .on_conflict_do_nothing(index_elements=["code"])
        )

    # --- 4. expense_items -----------------------------------------------------
    op.create_table(
        "expense_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        # classification
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("sub_category", sa.String(120), nullable=True),
        # expense facts
        sa.Column("expense_date", sa.Date(), nullable=False),
        sa.Column("merchant_vendor", sa.String(200), nullable=False),
        sa.Column("purpose_description", sa.Text(), nullable=False, server_default=""),
        sa.Column("attendees", sa.Text(), nullable=True),
        sa.Column("trip_log", postgresql.JSONB(), nullable=True),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("amount_usd", MONEY, nullable=False),
        sa.Column("fx_rate", sa.Numeric(18, 8), nullable=True),
        sa.Column(
            "has_pre_approval", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("pre_approval_doc_ref", sa.String(200), nullable=True),
        # receipt, inline
        sa.Column(
            "receipt_attached", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("file_url", sa.Text(), nullable=True),
        sa.Column("file_name", sa.Text(), nullable=True),
        sa.Column("mime_type", sa.String(200), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("file_hash", sa.String(64), nullable=True),
        sa.Column("ocr_extracted_json", postgresql.JSONB(), nullable=True),
        sa.Column("employee_corrected_data", postgresql.JSONB(), nullable=True),
        sa.Column("ocr_source", sa.String(32), nullable=True),
        sa.Column("ocr_confidence", postgresql.JSONB(), nullable=True),
        # evaluation snapshots
        sa.Column("policy_validation", postgresql.JSONB(), nullable=True),
        sa.Column("fraud_risk_score", sa.Integer(), nullable=True),
        # Reuses the enum type 0002 already created.
        sa.Column(
            "fraud_risk_level",
            _enum("fraud_risk_level", FRAUD_RISK_LEVEL_VALUES),
            nullable=True,
        ),
        sa.Column("fraud_flags", postgresql.JSONB(), nullable=True),
        sa.Column(
            "is_fraud_flagged", sa.Boolean(), nullable=False, server_default="false"
        ),
        # lifecycle + decision
        sa.Column(
            "status",
            _enum(EXPENSE_ITEM_STATUS_ENUM, EXPENSE_ITEM_STATUS_VALUES),
            nullable=False,
            server_default="Submitted",
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_sub", sa.String(64), nullable=True),
        sa.Column(
            "decided_by_employee_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("decision_notes", sa.Text(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        # provenance
        sa.Column("created_by_sub", sa.String(64), nullable=True),
        sa.Column("updated_by_sub", sa.String(64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
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
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["claims.id"],
            name="fk_expense_items_claim_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["expense_categories.id"],
            name="fk_expense_items_category_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_employee_id"],
            ["employees.id"],
            name="fk_expense_items_decided_by_employee_id",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "claim_id", "line_number", name="uq_expense_items_claim_line_number"
        ),
        sa.CheckConstraint("line_number > 0", name="ck_expense_items_line_number_positive"),
        sa.CheckConstraint("amount > 0", name="ck_expense_items_amount_positive"),
        sa.CheckConstraint("amount_usd > 0", name="ck_expense_items_amount_usd_positive"),
        sa.CheckConstraint(
            "char_length(currency) = 3", name="ck_expense_items_currency_iso4217"
        ),
        sa.CheckConstraint(
            "fx_rate IS NULL OR fx_rate > 0", name="ck_expense_items_fx_rate_positive"
        ),
        sa.CheckConstraint(
            "file_size_bytes IS NULL OR file_size_bytes >= 0",
            name="ck_expense_items_file_size_non_negative",
        ),
        sa.CheckConstraint(
            "fraud_risk_score IS NULL OR (fraud_risk_score >= 0 AND fraud_risk_score <= 100)",
            name="ck_expense_items_fraud_risk_score_range",
        ),
    )
    op.create_index("ix_expense_items_claim_id", "expense_items", ["claim_id"])
    op.create_index("ix_expense_items_status", "expense_items", ["status"])
    op.create_index("ix_expense_items_expense_date", "expense_items", ["expense_date"])
    op.create_index("ix_expense_items_category", "expense_items", ["category"])
    op.create_index("ix_expense_items_category_id", "expense_items", ["category_id"])
    op.create_index("ix_expense_items_file_hash", "expense_items", ["file_hash"])
    op.create_index(
        "ix_expense_items_duplicate_probe",
        "expense_items",
        ["expense_date", "amount_usd"],
    )

    # --- 5. retarget the receipt-dependent foreign keys ------------------------
    op.drop_index("ix_attachments_receipt_id", table_name="attachments")
    op.drop_constraint("fk_attachments_receipt_id", "attachments", type_="foreignkey")
    op.drop_column("attachments", "receipt_id")
    op.add_column(
        "attachments",
        sa.Column("expense_item_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_attachments_expense_item_id",
        "attachments",
        "expense_items",
        ["expense_item_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_attachments_expense_item_id", "attachments", ["expense_item_id"]
    )

    op.drop_index("ix_ai_inference_logs_receipt_id", table_name="ai_inference_logs")
    op.drop_constraint(
        "fk_ai_inference_logs_receipt_id", "ai_inference_logs", type_="foreignkey"
    )
    op.drop_column("ai_inference_logs", "receipt_id")
    op.add_column(
        "ai_inference_logs",
        sa.Column("expense_item_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_ai_inference_logs_expense_item_id",
        "ai_inference_logs",
        "expense_items",
        ["expense_item_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_ai_inference_logs_expense_item_id", "ai_inference_logs", ["expense_item_id"]
    )

    op.drop_constraint("uq_claims_receipt_id", "claims", type_="unique")
    op.drop_constraint("fk_claims_receipt_id", "claims", type_="foreignkey")
    op.drop_column("claims", "receipt_id")

    # --- 6. drop the per-expense claim indexes and constraints -----------------
    op.drop_index("ix_claims_expense_date", table_name="claims")
    op.drop_index("ix_claims_category", table_name="claims")
    op.drop_index("ix_claims_duplicate_probe", table_name="claims")
    op.drop_constraint("ck_claims_amount_positive", "claims", type_="check")
    op.drop_constraint("ck_claims_amount_usd_positive", "claims", type_="check")
    op.drop_constraint("ck_claims_fx_rate_positive", "claims", type_="check")

    # --- 7. drop the per-expense claim columns (no backfill, by decision) ------
    for column_name, _ in CLAIM_EXPENSE_COLUMNS:
        op.drop_column("claims", column_name)

    # --- 8. add the header + roll-up columns ----------------------------------
    op.add_column("claims", sa.Column("title", sa.String(200), nullable=True))
    op.add_column("claims", sa.Column("purpose", sa.Text(), nullable=True))
    op.add_column("claims", sa.Column("from_date", sa.Date(), nullable=True))
    op.add_column("claims", sa.Column("to_date", sa.Date(), nullable=True))
    op.add_column(
        "claims",
        sa.Column("total_amount", MONEY, nullable=False, server_default="0"),
    )
    op.add_column(
        "claims",
        sa.Column("total_amount_usd", MONEY, nullable=False, server_default="0"),
    )
    op.add_column(
        "claims",
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_claims_total_amount_non_negative", "claims", "total_amount >= 0"
    )
    op.create_check_constraint(
        "ck_claims_total_amount_usd_non_negative", "claims", "total_amount_usd >= 0"
    )
    op.create_check_constraint(
        "ck_claims_item_count_non_negative", "claims", "item_count >= 0"
    )
    op.create_check_constraint(
        "ck_claims_date_range_ordered",
        "claims",
        "to_date IS NULL OR from_date IS NULL OR to_date >= from_date",
    )

    # --- 9. drop the receipts tables (children first) --------------------------
    op.drop_table("receipt_line_items")
    op.drop_table("receipt_fields")
    op.drop_table("receipts")
    postgresql.ENUM(name=EXTRACTION_STATUS_ENUM).drop(bind, checkfirst=True)

    # --- 10. install the totals trigger ---------------------------------------
    op.execute(_totals_trigger_sql())
    op.execute(
        """
        CREATE TRIGGER expense_items_recalculate_claim_totals
        AFTER INSERT OR UPDATE OR DELETE ON expense_items
        FOR EACH ROW EXECUTE FUNCTION expense_items_recalculate_claim_totals();
        """
    )


def downgrade() -> None:
    """Restores the single-expense shape.

    The dropped per-expense values are **not** recoverable — the columns come back nullable and
    empty. This mirrors ``0005_drop_departments``: the structure is reversible, the data is not.
    """
    bind = op.get_bind()

    op.execute(
        "DROP TRIGGER IF EXISTS expense_items_recalculate_claim_totals ON expense_items"
    )
    op.execute("DROP FUNCTION IF EXISTS expense_items_recalculate_claim_totals()")

    # --- recreate the receipts tables (0001 shape + the 0002 employee FK) ------
    postgresql.ENUM(*EXTRACTION_STATUS_VALUES, name=EXTRACTION_STATUS_ENUM).create(
        bind, checkfirst=True
    )
    op.create_table(
        "receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("s3_bucket", sa.Text(), nullable=True),
        sa.Column("s3_key", sa.Text(), nullable=True),
        sa.Column("s3_region", sa.Text(), nullable=True),
        sa.Column("employee_id", sa.Text(), nullable=True),
        sa.Column("employee_ref_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "extraction_status",
            _enum(EXTRACTION_STATUS_ENUM, EXTRACTION_STATUS_VALUES),
            nullable=False,
        ),
        sa.Column("extraction_source", sa.Text(), nullable=True),
        sa.Column("raw_textract", postgresql.JSONB(), nullable=True),
        sa.Column("normalized_extraction", postgresql.JSONB(), nullable=True),
        sa.Column("vendor_name", sa.Text(), nullable=True),
        sa.Column("transaction_date", sa.Date(), nullable=True),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(8), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["employee_ref_id"],
            ["employees.id"],
            name="fk_receipts_employee_ref_id",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_receipts_employee_id", "receipts", ["employee_id"])
    op.create_index("ix_receipts_employee_ref_id", "receipts", ["employee_ref_id"])
    op.create_index("ix_receipts_extraction_status", "receipts", ["extraction_status"])

    op.create_table(
        "receipt_fields",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("receipt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_type", sa.Text(), nullable=True),
        sa.Column("field_label", sa.Text(), nullable=True),
        sa.Column("field_value", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 2), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["receipt_id"], ["receipts.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_receipt_fields_receipt_id", "receipt_fields", ["receipt_id"])

    op.create_table(
        "receipt_line_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("receipt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("quantity", sa.Numeric(), nullable=True),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["receipt_id"], ["receipts.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_receipt_line_items_receipt_id", "receipt_line_items", ["receipt_id"]
    )

    # --- restore the claims columns (nullable: the data is gone) ---------------
    op.drop_constraint("ck_claims_date_range_ordered", "claims", type_="check")
    op.drop_constraint("ck_claims_item_count_non_negative", "claims", type_="check")
    op.drop_constraint("ck_claims_total_amount_usd_non_negative", "claims", type_="check")
    op.drop_constraint("ck_claims_total_amount_non_negative", "claims", type_="check")
    for column_name in (
        "item_count",
        "total_amount_usd",
        "total_amount",
        "to_date",
        "from_date",
        "purpose",
        "title",
    ):
        op.drop_column("claims", column_name)

    for column_name, column_type in CLAIM_EXPENSE_COLUMNS:
        op.add_column("claims", sa.Column(column_name, column_type, nullable=True))

    op.create_check_constraint(
        "ck_claims_amount_positive", "claims", "amount IS NULL OR amount > 0"
    )
    op.create_check_constraint(
        "ck_claims_amount_usd_positive", "claims", "amount_usd IS NULL OR amount_usd > 0"
    )
    op.create_check_constraint(
        "ck_claims_fx_rate_positive", "claims", "fx_rate IS NULL OR fx_rate > 0"
    )
    op.create_index("ix_claims_expense_date", "claims", ["expense_date"])
    op.create_index("ix_claims_category", "claims", ["category"])
    op.create_index(
        "ix_claims_duplicate_probe", "claims", ["employee_id", "expense_date", "amount_usd"]
    )

    op.add_column(
        "claims", sa.Column("receipt_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_claims_receipt_id",
        "claims",
        "receipts",
        ["receipt_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint("uq_claims_receipt_id", "claims", ["receipt_id"])

    # --- restore the receipt-facing foreign keys -------------------------------
    op.drop_index("ix_ai_inference_logs_expense_item_id", table_name="ai_inference_logs")
    op.drop_constraint(
        "fk_ai_inference_logs_expense_item_id", "ai_inference_logs", type_="foreignkey"
    )
    op.drop_column("ai_inference_logs", "expense_item_id")
    op.add_column(
        "ai_inference_logs",
        sa.Column("receipt_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_ai_inference_logs_receipt_id",
        "ai_inference_logs",
        "receipts",
        ["receipt_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_ai_inference_logs_receipt_id", "ai_inference_logs", ["receipt_id"]
    )

    op.drop_index("ix_attachments_expense_item_id", table_name="attachments")
    op.drop_constraint(
        "fk_attachments_expense_item_id", "attachments", type_="foreignkey"
    )
    op.drop_column("attachments", "expense_item_id")
    op.add_column(
        "attachments",
        sa.Column("receipt_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_attachments_receipt_id",
        "attachments",
        "receipts",
        ["receipt_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_attachments_receipt_id", "attachments", ["receipt_id"])

    # --- drop the new tables ---------------------------------------------------
    op.drop_table("expense_items")
    op.drop_index("ix_expense_categories_is_active", table_name="expense_categories")
    op.drop_table("expense_categories")
    postgresql.ENUM(name=EXPENSE_ITEM_STATUS_ENUM).drop(bind, checkfirst=True)
