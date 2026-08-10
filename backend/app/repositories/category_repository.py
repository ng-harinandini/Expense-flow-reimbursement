"""Expense category persistence.

Unlike :class:`~app.repositories.policy_rule_repository.PolicyRuleRepository`, categories are not
versioned: editing a category's ``custom_fields`` in place is fine because
``expense_items.ocr_extracted_json`` is a frozen snapshot at submission time regardless of later
schema edits (see :mod:`app.models.category`). Writes therefore use the inherited
:class:`~app.repositories.base.BaseRepository` ``add``/``update`` directly.
"""

from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import select

from app.models.category import ExpenseCategory
from app.repositories.base import BaseRepository


class CategoryRepository(BaseRepository[ExpenseCategory]):
    model = ExpenseCategory

    def list_all(self, *, include_inactive: bool = False) -> Sequence[ExpenseCategory]:
        """All categories (including the common-fields row), ordered for the picker."""
        stmt = select(ExpenseCategory)
        if not include_inactive:
            stmt = stmt.where(ExpenseCategory.is_active.is_(True))
        return self._all(stmt.order_by(ExpenseCategory.display_order, ExpenseCategory.name))

    def get_by_code(self, code: str) -> Optional[ExpenseCategory]:
        return self._one_or_none(
            select(ExpenseCategory).where(ExpenseCategory.code == code)
        )

    def get_common(self) -> Optional[ExpenseCategory]:
        """The single ``is_common`` row holding the fields every invoice carries."""
        return self._one_or_none(
            select(ExpenseCategory).where(ExpenseCategory.is_common.is_(True))
        )
