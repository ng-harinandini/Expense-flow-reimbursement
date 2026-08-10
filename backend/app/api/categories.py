"""Expense categories API — the selectable category list and each one's extraction field schema.

Reads are open to any authenticated user (the claim submission form needs the full list, including
the common-fields row, to render dynamic extraction fields per category). Writes are restricted to
finance/admin, matching the ``policy-rules`` router's role gate.
"""

from __future__ import annotations

from typing import Any, List

from fastapi import APIRouter, Depends

from app.core.deps import (
    CurrentUser,
    get_category_service,
    get_current_user,
    get_unit_of_work,
    require_roles,
)
from app.core.unit_of_work import UnitOfWork
from app.domain.actor import Actor
from app.schemas.schemas import ExpenseCategoryWriteSchema
from app.services.category_service import CategoryService
from app.services.mappers import category_to_dict

router = APIRouter(prefix="/categories", tags=["Categories"])


@router.get("", response_model=List[dict], dependencies=[Depends(get_current_user)])
def get_categories(
    service: CategoryService = Depends(get_category_service),
) -> list[dict[str, Any]]:
    """Active categories plus the common-fields row, display order first."""
    return service.list_categories_as_dicts()


@router.get(
    "/{code}", response_model=dict, dependencies=[Depends(get_current_user)]
)
def get_category(
    code: str,
    service: CategoryService = Depends(get_category_service),
) -> dict[str, Any]:
    return category_to_dict(service.get_category(code))


@router.post("", response_model=dict)
def create_category(
    payload: ExpenseCategoryWriteSchema,
    current: CurrentUser = Depends(require_roles("finance", "admin")),
    service: CategoryService = Depends(get_category_service),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> dict[str, Any]:
    category = service.create_category(
        payload.model_dump(), actor=Actor.from_current_user(current)
    )
    uow.commit()
    return category_to_dict(category)


@router.put("/{code}", response_model=dict)
def update_category(
    code: str,
    payload: ExpenseCategoryWriteSchema,
    current: CurrentUser = Depends(require_roles("finance", "admin")),
    service: CategoryService = Depends(get_category_service),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> dict[str, Any]:
    category = service.update_category(
        code, payload.model_dump(), actor=Actor.from_current_user(current)
    )
    uow.commit()
    return category_to_dict(category)


@router.delete("/{code}", response_model=dict)
def delete_category(
    code: str,
    current: CurrentUser = Depends(require_roles("finance", "admin")),
    service: CategoryService = Depends(get_category_service),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> dict[str, Any]:
    """Soft delete (``is_active = false``); the common-fields row cannot be deleted."""
    category = service.deactivate_category(code, actor=Actor.from_current_user(current))
    uow.commit()
    return category_to_dict(category)
