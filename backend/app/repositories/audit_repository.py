"""Audit log persistence — append only.

There is deliberately **no** update or delete method: the table is protected by the
``audit_logs_append_only`` trigger (migration ``0002``), and the repository does not offer an API
that would fail at the database. ``BaseRepository.delete``/``update`` are overridden to raise
immediately with a clear reason rather than surfacing a database error.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional, Sequence

from sqlalchemy import Select, func, select

from app.core.context import current_client_ip, current_correlation_id, current_request_id, get_context
from app.models.audit import AuditLog
from app.repositories.base import BaseRepository


class AuditLogRepository(BaseRepository[AuditLog]):
    model = AuditLog

    # --- write ---------------------------------------------------------------

    def record(
        self,
        *,
        action: str,
        entity_type: str,
        entity_id: str,
        actor_name: str,
        actor_role: str,
        actor_sub: Optional[str] = None,
        details: str = "",
        before: Optional[dict[str, Any]] = None,
        after: Optional[dict[str, Any]] = None,
    ) -> AuditLog:
        """Append one audit row, inheriting request provenance from the active context.

        Staged with ``flush`` in the caller's transaction: if the business change rolls back, the
        audit row disappears with it, so the trail can never claim something that did not happen.
        """
        context = get_context()
        entry = AuditLog(
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            actor_sub=actor_sub,
            actor_name=actor_name,
            actor_role=actor_role,
            details=details or "",
            before=before,
            after=after,
            request_id=current_request_id(),
            correlation_id=current_correlation_id(),
            ip_address=current_client_ip(),
            user_agent=(context.user_agent[:400] if context and context.user_agent else None),
        )
        self.session.add(entry)
        self.session.flush()
        return entry

    # --- read ----------------------------------------------------------------

    def search(
        self,
        *,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        action: Optional[str] = None,
        actor_sub: Optional[str] = None,
        correlation_id: Optional[str] = None,
        limit: Optional[int] = 100,
        offset: int = 0,
    ) -> Sequence[AuditLog]:
        """Newest-first audit query. Every filter is indexed."""
        stmt: Select = select(AuditLog).order_by(AuditLog.occurred_at.desc())
        if entity_type:
            stmt = stmt.where(AuditLog.entity_type == entity_type)
        if entity_id:
            stmt = stmt.where(AuditLog.entity_id == str(entity_id))
        if action:
            stmt = stmt.where(AuditLog.action == action)
        if actor_sub:
            stmt = stmt.where(AuditLog.actor_sub == actor_sub)
        if correlation_id:
            stmt = stmt.where(AuditLog.correlation_id == correlation_id)
        return self._all(self._paginate(stmt, limit=limit, offset=offset))

    def list_for_entity(
        self, entity_type: str, entity_id: uuid.UUID | str, *, limit: Optional[int] = None
    ) -> Sequence[AuditLog]:
        """Full timeline for one entity ("everything that happened to claim X")."""
        return self.search(entity_type=entity_type, entity_id=str(entity_id), limit=limit)

    def count_for_entity(self, entity_type: str, entity_id: uuid.UUID | str) -> int:
        return int(
            self.session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.entity_type == entity_type)
                .where(AuditLog.entity_id == str(entity_id))
            ).scalar_one()
        )

    # --- immutability --------------------------------------------------------

    def update(self, entity: AuditLog, **changes: Any) -> AuditLog:  # noqa: D102
        raise NotImplementedError(
            "audit_logs is append-only; rows cannot be modified (enforced by DB trigger)."
        )

    def delete(self, entity: AuditLog) -> None:  # noqa: D102
        raise NotImplementedError(
            "audit_logs is append-only; rows cannot be deleted (enforced by DB trigger)."
        )
