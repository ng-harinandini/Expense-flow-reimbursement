"""Immutable audit trail.

Every business action writes exactly one row here, inside the *same transaction* as the change it
describes: if the business write rolls back, so does its audit record, and there is no way to
commit one without the other.

Immutability is enforced by the database, not by convention — migration ``0002`` installs a
``BEFORE UPDATE OR DELETE`` trigger that raises, so ``UPDATE audit_logs`` / ``DELETE FROM
audit_logs`` fail even for a superuser running raw SQL. The only supported way to remove rows is a
deliberate migration that drops the trigger first, which is reviewable.

``before`` / ``after`` hold the changed-field snapshots; ``request_id`` / ``correlation_id`` tie the
row back to the HTTP call (see :mod:`app.core.context`).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import UUIDPrimaryKeyMixin


class AuditLog(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        # Entity timeline: "everything that ever happened to claim X".
        Index("ix_audit_logs_entity", "entity_type", "entity_id", "occurred_at"),
        Index("ix_audit_logs_occurred_at", "occurred_at"),
        Index("ix_audit_logs_action", "action"),
        Index("ix_audit_logs_actor_sub", "actor_sub"),
        Index("ix_audit_logs_correlation_id", "correlation_id"),
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # --- actor (who) ---
    actor_sub: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    actor_name: Mapped[str] = mapped_column(String(200), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)

    # --- action + target (what) ---
    # VARCHAR rather than a PG enum: later phases add actions without a migration.
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # Free-form so it can hold a UUID, a claim number, or an external id.
    entity_id: Mapped[str] = mapped_column(String(200), nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    # --- change snapshots (state) ---
    before: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    after: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # --- request provenance (where from) ---
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    correlation_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(400), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AuditLog {self.action} {self.entity_type}:{self.entity_id}>"


class AIInferenceLog(UUIDPrimaryKeyMixin, Base):
    """Ledger of model invocations (OCR, policy reasoning, fraud scoring).

    Phase 1 only creates the table and the repository that writes to it — no AI call sites are
    added here (that is explicitly a later phase). It exists now so that when Bedrock/LLM work
    lands, cost, latency, and output attribution are recorded from the first request rather than
    retrofitted.

    Prompts are deliberately **not** stored: ``input_summary`` holds redacted, structured context
    only, so receipts and personal data are not duplicated into a log table.
    """

    __tablename__ = "ai_inference_logs"
    __table_args__ = (
        Index("ix_ai_inference_logs_claim_id", "claim_id"),
        Index("ix_ai_inference_logs_receipt_id", "receipt_id"),
        Index("ix_ai_inference_logs_created_at", "created_at"),
        Index("ix_ai_inference_logs_operation", "operation"),
    )

    # Both nullable: an inference may relate to a claim, a receipt, both, or neither.
    claim_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("claims.id", ondelete="SET NULL", name="fk_ai_inference_logs_claim_id"),
        nullable=True,
    )
    receipt_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("receipts.id", ondelete="SET NULL", name="fk_ai_inference_logs_receipt_id"),
        nullable=True,
    )

    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False)
    input_summary: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    output_summary: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    input_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    actor_sub: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    correlation_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AIInferenceLog {self.provider}/{self.model} {self.operation} {self.status}>"
