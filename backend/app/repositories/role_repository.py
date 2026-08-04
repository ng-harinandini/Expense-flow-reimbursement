"""Role lookups (RBAC reference data)."""

from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import select

from app.models.role import Role
from app.repositories.base import BaseRepository


class RoleRepository(BaseRepository[Role]):
    model = Role

    def get_by_name(self, name: str) -> Optional[Role]:
        if not name:
            return None
        return self._one_or_none(select(Role).where(Role.name == name))

    def list_all(self) -> Sequence[Role]:
        return self._all(select(Role).order_by(Role.id))
