"""Small reference-data lookups used to populate admin form dropdowns.

Admin-gated, same as the rest of the admin user-management surface.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUser, get_role_repository, require_roles
from app.repositories.role_repository import RoleRepository
from app.schemas.schemas import RoleOptionSchema

router = APIRouter(prefix="/directory", tags=["Admin · Directory"])


@router.get("/roles", response_model=List[RoleOptionSchema])
def list_roles(
    _admin: CurrentUser = Depends(require_roles("admin")),
    role_repository: RoleRepository = Depends(get_role_repository),
):
    return [
        RoleOptionSchema(id=role.id, name=role.name, description=role.description)
        for role in role_repository.list_all()
    ]
