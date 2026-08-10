"""Expense category reference data.

``expense_items`` carries **both** a ``category_id`` foreign key into this table *and* a
denormalized ``category`` text snapshot. That is deliberate, not redundancy:

  * ``category_id`` is the referential link — it is what an admin screen edits and what keeps the
    set of selectable categories closed.
  * ``expense_items.category`` is the string :mod:`app.services.policy_engine` literally compares
    against (``"Meals"``, ``"Hotel / Lodging"``, …). It is frozen as of submission so that renaming
    a row here cannot retroactively change how a historical item was judged.

``name`` must therefore stay in lockstep with ``policy_rules.category``; the seed data and
``tests/test_migrations.py`` assert both sides carry the same values.

One row is the *common-fields* bucket, not a real category: ``code="COMMON"``, ``is_common=True``.
It is never selectable as an expense item's category and is excluded from anything that lists
"categories" for classification purposes — it exists only so ``custom_fields`` (see below) has one
place to hold the extraction fields every invoice carries regardless of category.

``custom_fields`` is the JSON schema of extraction fields for this category (or, for the common
row, the fields shared by every category): a list of
``{name, label, description, data_type, options, required}`` objects. It is JSONB, not a child
table, because it is curated content edited as a whole set per category and never individually
queried/joined/indexed — the same reasoning as ``PolicyRule.special_rules``
(:mod:`app.models.policy`). It defines the *shape* invoice extraction should fill in
(``ExpenseItem.ocr_extracted_json``); it does not itself validate or populate anything yet.

Schema is owned by Alembic — this declaration is the source migration ``0008`` was authored from,
extended by migration ``0012`` (``custom_fields`` / ``is_common`` + the 15-category reseed).
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class ExpenseCategory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "expense_categories"
    __table_args__ = (
        UniqueConstraint("code", name="uq_expense_categories_code"),
        UniqueConstraint("name", name="uq_expense_categories_name"),
        CheckConstraint(
            "char_length(btrim(name)) > 0", name="ck_expense_categories_name_not_blank"
        ),
        CheckConstraint(
            "display_order >= 0", name="ck_expense_categories_display_order_non_negative"
        ),
        Index("ix_expense_categories_is_active", "is_active"),
        Index("ix_expense_categories_is_common", "is_common"),
    )

    # Stable machine key, so ``name`` can be reworded without breaking anything that joins.
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    # Display string — and the exact value the policy engine string-matches on.
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Deterministic ordering for the picker; every other reference table here has one.
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default="100"
    )
    # Retiring a category must not delete rows historical items still reference.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    # True only for the single "COMMON" bucket row — not a selectable expense category.
    is_common: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Extraction field schema: [{name, label, description, data_type, options, required}, ...].
    custom_fields: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ExpenseCategory {self.code} {self.name}>"
