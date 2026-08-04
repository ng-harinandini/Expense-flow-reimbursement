"""Audit trail service.

The single way business code writes audit records. It exists so that call sites need only say
*what happened* — actor identity, request ids, IP and user agent are pulled from the
:class:`~app.domain.actor.Actor` and the active request context automatically, which is what makes
"every business action is audited" achievable rather than aspirational.

The write joins the caller's transaction (``flush``, never ``commit``): the audit record and the
change it describes commit together or not at all.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional, Sequence

from app.core.logging import get_logger
from app.domain.actor import Actor
from app.models.audit import AuditLog
from app.models.enums import AuditAction, AuditEntity
from app.repositories.audit_repository import AuditLogRepository

logger = get_logger(__name__)


class AuditService:
    def __init__(self, audit_repository: AuditLogRepository) -> None:
        self._repository = audit_repository

    def record(
        self,
        *,
        actor: Actor,
        action: AuditAction | str,
        entity_type: AuditEntity | str,
        entity_id: uuid.UUID | str,
        details: str = "",
        before: Optional[dict[str, Any]] = None,
        after: Optional[dict[str, Any]] = None,
    ) -> AuditLog:
        """Append one immutable audit record."""
        action_value = action.value if isinstance(action, AuditAction) else str(action)
        entity_value = (
            entity_type.value if isinstance(entity_type, AuditEntity) else str(entity_type)
        )

        entry = self._repository.record(
            action=action_value,
            entity_type=entity_value,
            entity_id=str(entity_id),
            actor_name=actor.name,
            actor_role=actor.role,
            actor_sub=actor.sub,
            details=details,
            before=before,
            after=after,
        )
        logger.info(
            "audit.recorded",
            extra={
                "auditAction": action_value,
                "entityType": entity_value,
                "entityId": str(entity_id),
                "actorRole": actor.role,
            },
        )
        return entry

    def record_status_change(
        self,
        *,
        actor: Actor,
        claim_id: uuid.UUID,
        claim_number: str,
        from_status: str,
        to_status: str,
        details: Optional[str] = None,
    ) -> AuditLog:
        """Convenience wrapper for a lifecycle transition, with before/after snapshots."""
        return self.record(
            actor=actor,
            action=AuditAction.CLAIM_STATUS_CHANGE,
            entity_type=AuditEntity.CLAIM,
            entity_id=claim_number,
            details=details or f"Status changed from {from_status} to {to_status}.",
            before={"status": from_status},
            after={"status": to_status, "claimId": str(claim_id)},
        )

    # --- reads ---------------------------------------------------------------

    def list_recent(
        self, *, limit: int = 100, offset: int = 0, action: Optional[str] = None,
        entity_type: Optional[str] = None, entity_id: Optional[str] = None,
    ) -> Sequence[AuditLog]:
        return self._repository.search(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            limit=limit,
            offset=offset,
        )

    def list_for_entity(
        self, entity_type: AuditEntity | str, entity_id: uuid.UUID | str,
        *, limit: Optional[int] = None,
    ) -> Sequence[AuditLog]:
        value = entity_type.value if isinstance(entity_type, AuditEntity) else str(entity_type)
        return self._repository.list_for_entity(value, entity_id, limit=limit)
