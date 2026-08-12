"""expense_items: AI document classification verdict columns.

Adds the columns the new pre-extraction classification layer writes: the AI's suggested category
and document type, its confidence, whether it disagrees with the employee's own category selection,
whether the item needs manual review as a result, the category-specific fields it extracted (once
classification confirmed a match), and free-text notes.

These are a fourth, independent concern from the existing "three layers of receipt truth"
(``ocr_extracted_json`` / ``employee_corrected_data`` / the resolved value columns) documented on
``ExpenseItem``: an AI *verdict about the category*, not a value either engine resolves into.
``expense_items.category`` itself is never written by this migration or by anything that populates
these columns — it remains the sole authoritative, employee-selected value.

All columns are nullable or default to a safe "nothing happened yet" value, so existing rows and
any submission made before this AI capability is enabled stay valid.

Revision ID: 0014_item_ai_classification
Revises: 0013_merge_heads
Create Date: 2026-08-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_item_ai_classification"
down_revision: Union[str, None] = "0013_merge_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "expense_items"

_NULLABLE_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("ai_suggested_category", sa.String(64)),
    ("ai_document_type", sa.String(64)),
    ("ai_classification_confidence", sa.Numeric(4, 3)),
    ("ai_category_fields", postgresql.JSONB()),
    ("ai_classification_notes", sa.Text()),
)

_BOOLEAN_COLUMNS: tuple[str, ...] = ("category_mismatch", "category_review_required")


def upgrade() -> None:
    for name, type_ in _NULLABLE_COLUMNS:
        op.add_column(_TABLE, sa.Column(name, type_, nullable=True))

    for name in _BOOLEAN_COLUMNS:
        op.add_column(
            _TABLE,
            sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    op.create_check_constraint(
        "ck_expense_items_ai_classification_confidence_range",
        _TABLE,
        "ai_classification_confidence IS NULL OR "
        "(ai_classification_confidence >= 0 AND ai_classification_confidence <= 1)",
    )
    op.create_index(
        "ix_expense_items_category_review_required",
        _TABLE,
        ["category_review_required"],
    )


def downgrade() -> None:
    op.drop_index("ix_expense_items_category_review_required", table_name=_TABLE)
    op.drop_constraint(
        "ck_expense_items_ai_classification_confidence_range", _TABLE, type_="check"
    )
    for name in _BOOLEAN_COLUMNS:
        op.drop_column(_TABLE, name)
    for name, _type in reversed(_NULLABLE_COLUMNS):
        op.drop_column(_TABLE, name)
