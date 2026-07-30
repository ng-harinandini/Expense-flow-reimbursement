"""Persistence for the model/provider/embedding registry and durable feature-flag overrides.

``RegistryEntryRepository`` mirrors ``app.repositories.policy_rule_repository.PolicyRuleRepository``
exactly (see ``app.ai.models.governance.RegistryEntry``'s docstring for why one table, discriminated
by ``kind``, serves all three of Task 11's "model/provider/embedding registry" atomics).
"""

from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import func, select

from app.ai.core.errors import KnowledgeNotFoundError
from app.ai.models.governance import FlagOverride, RegistryEntry
from app.repositories.base import BaseRepository


class RegistryEntryRepository(BaseRepository[RegistryEntry]):
    model = RegistryEntry

    # --- reads -----------------------------------------------------------------

    def get_active(self, kind: str, name: str) -> Optional[RegistryEntry]:
        """The currently active version of ``name`` under ``kind``, or ``None``."""
        return self._one_or_none(
            select(RegistryEntry).where(
                RegistryEntry.kind == kind, RegistryEntry.name == name,
                RegistryEntry.is_active.is_(True),
            )
        )

    def list_active(self, kind: Optional[str] = None) -> Sequence[RegistryEntry]:
        """Every active entry, optionally scoped to one ``kind`` — the effective governed
        configuration right now."""
        stmt = select(RegistryEntry).where(RegistryEntry.is_active.is_(True))
        if kind is not None:
            stmt = stmt.where(RegistryEntry.kind == kind)
        return self._all(stmt.order_by(RegistryEntry.kind, RegistryEntry.name))

    def list_versions(self, kind: str, name: str) -> Sequence[RegistryEntry]:
        """Every version of one entry, newest first — the change history."""
        return self._all(
            select(RegistryEntry)
            .where(RegistryEntry.kind == kind, RegistryEntry.name == name)
            .order_by(RegistryEntry.version.desc())
        )

    def latest_version_number(self, kind: str, name: str) -> int:
        return int(
            self.session.execute(
                select(func.coalesce(func.max(RegistryEntry.version), 0)).where(
                    RegistryEntry.kind == kind, RegistryEntry.name == name
                )
            ).scalar_one()
        )

    # --- writes ------------------------------------------------------------------

    def publish_version(
        self,
        *,
        kind: str,
        name: str,
        config: Optional[dict] = None,
        cost_per_unit_usd: Optional[object] = None,
        cost_unit: Optional[str] = None,
        created_by_sub: Optional[str] = None,
    ) -> RegistryEntry:
        """Publish the next version of ``(kind, name)``, deactivating the current one."""
        next_version = self.latest_version_number(kind, name) + 1

        current = self.get_active(kind, name)
        if current is not None:
            current.is_active = False

        entry = RegistryEntry(
            kind=kind,
            name=name,
            version=next_version,
            is_active=True,
            config=dict(config or {}),
            cost_per_unit_usd=cost_per_unit_usd,
            cost_unit=cost_unit,
            created_by_sub=created_by_sub,
        )
        self.session.add(entry)
        self.session.flush()
        return entry

    def rollback(self, kind: str, name: str, *, to_version: int) -> RegistryEntry:
        """Re-point the active pointer to ``to_version``, deactivating whichever version is
        currently active (if different). Never deletes or renumbers anything."""
        entries = self.list_versions(kind, name)
        target = next((e for e in entries if e.version == to_version), None)
        if target is None:
            raise KnowledgeNotFoundError("RegistryEntry", f"{kind}/{name}@v{to_version}")

        if not target.is_active:
            current = self.get_active(kind, name)
            if current is not None and current.id != target.id:
                current.is_active = False
            target.is_active = True
            self.session.flush()
        return target


class FlagOverrideRepository(BaseRepository[FlagOverride]):
    model = FlagOverride

    def get(self, flag_name: str) -> Optional[FlagOverride]:
        return self._one_or_none(
            select(FlagOverride).where(FlagOverride.flag_name == flag_name)
        )

    def list_all(self) -> Sequence[FlagOverride]:
        return self._all(select(FlagOverride).order_by(FlagOverride.flag_name))

    def upsert(
        self, flag_name: str, *, enabled: bool, updated_by_sub: Optional[str] = None
    ) -> FlagOverride:
        existing = self.get(flag_name)
        if existing is not None:
            existing.enabled = enabled
            existing.updated_by_sub = updated_by_sub
            self.session.flush()
            return existing

        override = FlagOverride(
            flag_name=flag_name, enabled=enabled, updated_by_sub=updated_by_sub
        )
        self.session.add(override)
        self.session.flush()
        return override

    def clear(self, flag_name: str) -> bool:
        """Remove a persisted override, reverting the flag to its configuration default."""
        existing = self.get(flag_name)
        if existing is None:
            return False
        self.session.delete(existing)
        self.session.flush()
        return True


__all__ = ["FlagOverrideRepository", "RegistryEntryRepository"]
