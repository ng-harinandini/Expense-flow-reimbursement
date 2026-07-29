"""initial receipts schema (receipts, receipt_fields, receipt_line_items + extraction_status enum)

Revision ID: 0001_initial_receipts
Revises:
Create Date: 2026-07-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial_receipts"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


extraction_status = postgresql.ENUM(
    "PENDING", "PROCESSING", "COMPLETED", "FAILED",
    name="extraction_status",
)


def upgrade() -> None:
    bind = op.get_bind()
    # Create the enum type once; safe if a prior partial run already created it.
    extraction_status.create(bind, checkfirst=True)

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
        sa.Column(
            "extraction_status",
            postgresql.ENUM(
                "PENDING", "PROCESSING", "COMPLETED", "FAILED",
                name="extraction_status",
                create_type=False,
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("extraction_source", sa.Text(), nullable=True),
        sa.Column("raw_textract", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("normalized_extraction", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("vendor_name", sa.Text(), nullable=True),
        sa.Column("transaction_date", sa.Date(), nullable=True),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_receipts_employee_id", "receipts", ["employee_id"])
    op.create_index("ix_receipts_extraction_status", "receipts", ["extraction_status"])

    op.create_table(
        "receipt_fields",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("receipt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_type", sa.Text(), nullable=True),
        sa.Column("field_label", sa.Text(), nullable=True),
        sa.Column("field_value", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 2), nullable=True),
        sa.ForeignKeyConstraint(["receipt_id"], ["receipts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
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
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["receipt_id"], ["receipts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_receipt_line_items_receipt_id", "receipt_line_items", ["receipt_id"])


def downgrade() -> None:
    op.drop_index("ix_receipt_line_items_receipt_id", table_name="receipt_line_items")
    op.drop_table("receipt_line_items")
    op.drop_index("ix_receipt_fields_receipt_id", table_name="receipt_fields")
    op.drop_table("receipt_fields")
    op.drop_index("ix_receipts_extraction_status", table_name="receipts")
    op.drop_index("ix_receipts_employee_id", table_name="receipts")
    op.drop_table("receipts")
    extraction_status.drop(op.get_bind(), checkfirst=True)
