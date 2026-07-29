"""AI inference ledger persistence.

Phase 1 provides the durable write path only — no AI call site is wired to it yet (adding model
reasoning is explicitly a later phase). It exists now so that when Bedrock/LLM work lands, every
invocation is attributable from the first request instead of being retrofitted.

Prompts are never stored: pass redacted, structured context in ``input_summary``.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional, Sequence

from sqlalchemy import select

from app.core.context import current_correlation_id, current_request_id
from app.models.audit import AIInferenceLog
from app.models.enums import AIInferenceStatus
from app.repositories.base import BaseRepository


class AIInferenceRepository(BaseRepository[AIInferenceLog]):
    model = AIInferenceLog

    def record(
        self,
        *,
        provider: str,
        model: str,
        operation: str,
        status: AIInferenceStatus | str = AIInferenceStatus.SUCCESS,
        claim_id: Optional[uuid.UUID] = None,
        receipt_id: Optional[uuid.UUID] = None,
        input_summary: Optional[dict[str, Any]] = None,
        output_summary: Optional[dict[str, Any]] = None,
        error_message: Optional[str] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        latency_ms: Optional[int] = None,
        actor_sub: Optional[str] = None,
    ) -> AIInferenceLog:
        entry = AIInferenceLog(
            claim_id=claim_id,
            receipt_id=receipt_id,
            provider=provider,
            model=model,
            operation=operation,
            status=AIInferenceStatus.coerce(status).value,
            input_summary=input_summary,
            output_summary=output_summary,
            error_message=error_message,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            actor_sub=actor_sub,
            request_id=current_request_id(),
            correlation_id=current_correlation_id(),
        )
        self.session.add(entry)
        self.session.flush()
        return entry

    def list_for_claim(
        self, claim_id: uuid.UUID, *, limit: Optional[int] = None
    ) -> Sequence[AIInferenceLog]:
        return self._all(
            self._paginate(
                select(AIInferenceLog)
                .where(AIInferenceLog.claim_id == claim_id)
                .order_by(AIInferenceLog.created_at.desc()),
                limit=limit,
            )
        )

    def list_for_receipt(
        self, receipt_id: uuid.UUID, *, limit: Optional[int] = None
    ) -> Sequence[AIInferenceLog]:
        return self._all(
            self._paginate(
                select(AIInferenceLog)
                .where(AIInferenceLog.receipt_id == receipt_id)
                .order_by(AIInferenceLog.created_at.desc()),
                limit=limit,
            )
        )

    def list_recent(
        self, *, operation: Optional[str] = None, limit: Optional[int] = 100
    ) -> Sequence[AIInferenceLog]:
        stmt = select(AIInferenceLog).order_by(AIInferenceLog.created_at.desc())
        if operation:
            stmt = stmt.where(AIInferenceLog.operation == operation)
        return self._all(self._paginate(stmt, limit=limit))
