"""Expense category service.

Owns the selectable category list and each category's extraction field schema
(``custom_fields``). Unlike policy rules, categories are not versioned — editing a category's
fields in place is safe because ``expense_items.ocr_extracted_json`` snapshots the extraction
result at submission time regardless of later schema edits (see :mod:`app.models.category`).
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from app.domain.actor import Actor
from app.domain.errors import NotFoundError, ValidationError
from app.models.category import ExpenseCategory
from app.models.enums import AuditAction, AuditEntity
from app.repositories.category_repository import CategoryRepository
from app.services.audit_service import AuditService
from app.services.mappers import category_to_dict

_ALLOWED_DATA_TYPES = {"text", "number", "date", "boolean", "enum"}


def _validate_custom_fields(fields: Any, *, category_label: str) -> list[dict[str, Any]]:
    if fields is None:
        return []
    if not isinstance(fields, list):
        raise ValidationError(f"'customFields' for '{category_label}' must be a list.")

    cleaned: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for index, raw in enumerate(fields):
        if not isinstance(raw, dict):
            raise ValidationError(
                f"Custom field at index {index} for '{category_label}' must be an object.",
                details={"index": index},
            )
        name = str(raw.get("name") or "").strip()
        label = str(raw.get("label") or "").strip()
        if not name or not label:
            raise ValidationError(
                f"Custom field at index {index} for '{category_label}' needs a 'name' and 'label'.",
                details={"index": index},
            )
        if name in seen_names:
            raise ValidationError(
                f"Duplicate custom field name '{name}' for '{category_label}'.",
                details={"index": index, "field": "name"},
            )
        seen_names.add(name)

        data_type = str(raw.get("data_type") or raw.get("dataType") or "text").strip()
        if data_type not in _ALLOWED_DATA_TYPES:
            raise ValidationError(
                f"Custom field '{name}' has unsupported data_type '{data_type}'.",
                details={"index": index, "field": "data_type"},
            )
        options = raw.get("options")
        if data_type == "enum" and not options:
            raise ValidationError(
                f"Custom field '{name}' is type 'enum' but has no 'options'.",
                details={"index": index, "field": "options"},
            )

        cleaned.append(
            {
                "name": name,
                "label": label,
                "description": raw.get("description"),
                "data_type": data_type,
                "options": list(options) if options else None,
                "required": bool(raw.get("required", False)),
            }
        )
    return cleaned


class CategoryService:
    def __init__(
        self, category_repository: CategoryRepository, audit_service: AuditService
    ) -> None:
        self._categories = category_repository
        self._audit = audit_service

    # --- reads -----------------------------------------------------------------

    def list_categories(self, *, include_inactive: bool = False) -> Sequence[ExpenseCategory]:
        return self._categories.list_all(include_inactive=include_inactive)

    def list_categories_as_dicts(self, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        return [category_to_dict(c) for c in self.list_categories(include_inactive=include_inactive)]

    def get_category(self, code: str) -> ExpenseCategory:
        category = self._categories.get_by_code(code)
        if category is None:
            raise NotFoundError("ExpenseCategory", code)
        return category

    # --- writes ------------------------------------------------------------------

    def create_category(self, payload: dict[str, Any], *, actor: Actor) -> ExpenseCategory:
        code = str(payload.get("code") or "").strip().upper()
        name = str(payload.get("name") or "").strip()
        if not code or not name:
            raise ValidationError("A category needs both 'code' and 'name'.")
        if self._categories.get_by_code(code) is not None:
            raise ValidationError(f"Category code '{code}' already exists.")

        custom_fields = _validate_custom_fields(payload.get("customFields"), category_label=name)

        category = self._categories.add(
            ExpenseCategory(
                code=code,
                name=name,
                description=payload.get("description"),
                display_order=int(payload.get("displayOrder") or 100),
                is_active=bool(payload.get("isActive", True)),
                is_common=False,
                custom_fields=custom_fields,
            )
        )

        self._audit.record(
            actor=actor,
            action=AuditAction.CATEGORY_CREATE,
            entity_type=AuditEntity.EXPENSE_CATEGORY,
            entity_id=category.id,
            details=f"Created category '{category.name}' ({category.code}).",
            after={"category": category_to_dict(category)},
        )
        return category

    def update_category(
        self, code: str, payload: dict[str, Any], *, actor: Actor
    ) -> ExpenseCategory:
        category = self.get_category(code)
        before = category_to_dict(category)

        name = str(payload.get("name") or category.name).strip()
        custom_fields = _validate_custom_fields(
            payload.get("customFields", category.custom_fields), category_label=name
        )

        self._categories.update(
            category,
            name=name,
            description=payload.get("description", category.description),
            display_order=int(payload.get("displayOrder") or category.display_order),
            is_active=bool(payload.get("isActive", category.is_active)),
            custom_fields=custom_fields,
        )

        self._audit.record(
            actor=actor,
            action=AuditAction.CATEGORY_UPDATE,
            entity_type=AuditEntity.EXPENSE_CATEGORY,
            entity_id=category.id,
            details=f"Updated category '{category.name}' ({category.code}).",
            before={"category": before},
            after={"category": category_to_dict(category)},
        )
        return category

    def deactivate_category(self, code: str, *, actor: Actor) -> ExpenseCategory:
        category = self.get_category(code)
        if category.is_common:
            raise ValidationError("The common-fields category cannot be deleted.")

        before = category_to_dict(category)
        self._categories.update(category, is_active=False)

        self._audit.record(
            actor=actor,
            action=AuditAction.CATEGORY_DELETE,
            entity_type=AuditEntity.EXPENSE_CATEGORY,
            entity_id=category.id,
            details=f"Deactivated category '{category.name}' ({category.code}).",
            before={"category": before},
            after={"category": category_to_dict(category)},
        )
        return category
