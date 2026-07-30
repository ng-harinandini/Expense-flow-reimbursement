"""``RegistryService`` — the shared versioning primitive ``model_registry.py``/
``provider_registry.py``/``embedding_registry.py`` all wrap in kind-scoped terms.

Publish/rollback/audit shape mirrors ``app.ai.prompts.registry.PromptRegistry`` exactly, applied to
:class:`~app.ai.models.governance.RegistryEntry` instead of a prompt template — the same versioning
mechanic (publish supersedes, rollback re-points without deleting) serves both artifacts, so it is
written once here rather than duplicated per kind.
"""

from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy.orm import Session

from app.ai.core.errors import KnowledgeNotFoundError
from app.ai.models.governance import RegistryEntry
from app.ai.repositories.governance_repository import RegistryEntryRepository
from app.domain.actor import Actor
from app.services.audit_service import AuditService

_ENTITY_TYPE = "RegistryEntry"


class RegistryService:
    def __init__(self, session: Session, *, audit_service: AuditService) -> None:
        self._repository = RegistryEntryRepository(session)
        self._audit = audit_service

    def publish(
        self,
        kind: str,
        name: str,
        *,
        actor: Actor,
        config: Optional[dict] = None,
        cost_per_unit_usd: Optional[object] = None,
        cost_unit: Optional[str] = None,
    ) -> RegistryEntry:
        entry = self._repository.publish_version(
            kind=kind, name=name, config=config, cost_per_unit_usd=cost_per_unit_usd,
            cost_unit=cost_unit, created_by_sub=actor.sub,
        )
        self._audit.record(
            actor=actor, action="AI_REGISTRY_ENTRY_PUBLISHED", entity_type=_ENTITY_TYPE,
            entity_id=f"{kind}/{name}",
            details=f"Published registry entry '{kind}/{name}' v{entry.version}.",
            after={"kind": kind, "name": name, "version": entry.version},
        )
        return entry

    def rollback(self, kind: str, name: str, *, to_version: int, actor: Actor) -> RegistryEntry:
        entry = self._repository.rollback(kind, name, to_version=to_version)
        self._audit.record(
            actor=actor, action="AI_REGISTRY_ENTRY_ROLLED_BACK", entity_type=_ENTITY_TYPE,
            entity_id=f"{kind}/{name}",
            details=f"Rolled registry entry '{kind}/{name}' back to v{to_version}.",
            after={"kind": kind, "name": name, "activeVersion": to_version},
        )
        return entry

    def get_active(self, kind: str, name: str) -> RegistryEntry:
        entry = self._repository.get_active(kind, name)
        if entry is None:
            raise KnowledgeNotFoundError(_ENTITY_TYPE, f"{kind}/{name}")
        return entry

    def list_active(self, kind: Optional[str] = None) -> Sequence[RegistryEntry]:
        return self._repository.list_active(kind)

    def list_versions(self, kind: str, name: str) -> Sequence[RegistryEntry]:
        return self._repository.list_versions(kind, name)


__all__ = ["RegistryService"]
