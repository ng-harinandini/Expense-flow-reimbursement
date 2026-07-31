"""Expense category reference data.

``expense_items`` carries **both** a ``category_id`` foreign key into this table *and* a
denormalized ``category`` text snapshot. That is deliberate, not redundancy:

  * ``category_id`` is the referential link — it is what an admin screen edits and what keeps the
    set of selectable categories closed.
  * ``expense_items.category`` is the string :mod:`app.services.policy_engine` literally compares
    against (``"Meals"``, ``"Lodging"``, …). It is frozen as of submission so that renaming a row
    here cannot retroactively change how a historical item was judged.

``name`` must therefore stay in lockstep with ``policy_rules.category``; the seed data and
``tests/test_migrations.py`` assert both sides carry the same five values.

Schema is owned by Alembic — this declaration is the source migration ``0008`` was authored from.
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

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ExpenseCategory {self.code} {self.name}>"
