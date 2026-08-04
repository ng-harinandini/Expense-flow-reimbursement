"""Audit log API — read-only queries over the immutable trail.

There is intentionally no write, update, or delete endpoint: records are written as a side effect
of the business action that caused them, and the table is append-only at the database level
(``audit_logs_append_only`` trigger, migration ``0002``).

The list response stays a plain array of ``AuditLogEntry`` for the existing frontend; paging is via
``limit``/``offset`` query parameters with a sane default rather than a change of shape.
"""

from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Query

from app.core.deps import get_audit_service, require_roles
from app.services.audit_service import AuditService
from app.services.mappers import audit_to_dict

router = APIRouter(prefix="/audit-logs", tags=["Audit Logs"])

_READ_ROLES = ("finance", "admin", "auditor")


@router.get("", response_model=List[dict], dependencies=[Depends(require_roles(*_READ_ROLES))])
def get_audit_logs(
    action: Optional[str] = Query(None, description="Filter by audit action, e.g. SUBMIT_CLAIM."),
    entityType: Optional[str] = Query(None, description="Filter by entity type, e.g. Claim."),
    entityId: Optional[str] = Query(None, description="Filter by entity id or claim number."),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    service: AuditService = Depends(get_audit_service),
) -> list[dict[str, Any]]:
    """Audit records, newest first."""
    entries = service.list_recent(
        action=action,
        entity_type=entityType,
        entity_id=entityId,
        limit=limit,
        offset=offset,
    )
    return [audit_to_dict(entry) for entry in entries]


@router.get(
    "/{entity_type}/{entity_id}",
    response_model=List[dict],
    dependencies=[Depends(require_roles(*_READ_ROLES))],
)
def get_entity_audit_trail(
    entity_type: str,
    entity_id: str,
    limit: int = Query(200, ge=1, le=1000),
    service: AuditService = Depends(get_audit_service),
) -> list[dict[str, Any]]:
    """Complete trail for one entity — e.g. ``/audit-logs/Claim/EXP-2026-1001``."""
    entries = service.list_for_entity(entity_type, entity_id, limit=limit)
    return [audit_to_dict(entry) for entry in entries]
