"""Persisted fraud-screening outcomes.

Replaces the ``fraudScreening`` dictionary that used to live inside the in-memory claim. Each
screening run appends a row rather than overwriting, so re-screening a claim (after a correction,
or with a newer engine version) keeps the earlier verdict auditable. The newest row by
``evaluated_at`` is the effective one — ``Claim.latest_fraud_result``.

``engine_version`` records which detector produced the verdict, so a Phase 2 model change can be
compared against the deterministic Phase 1 rules on the same historical data.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import FraudRiskLevel, fraud_risk_level_enum

# Bumped whenever the deterministic screening rules change materially.
FRAUD_ENGINE_VERSION = "rules-1.0"


class FraudResult(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "fraud_results"
    __table_args__ = (
        CheckConstraint(
            "risk_score >= 0 AND risk_score <= 100", name="ck_fraud_results_risk_score_range"
        ),
        Index("ix_fraud_results_claim_id_evaluated_at", "claim_id", "evaluated_at"),
        Index("ix_fraud_results_risk_level", "risk_level"),
        Index("ix_fraud_results_is_flagged", "is_flagged"),
    )

    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("claims.id", ondelete="CASCADE", name="fk_fraud_results_claim_id"),
        nullable=False,
    )

    risk_score: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    risk_level: Mapped[FraudRiskLevel] = mapped_column(fraud_risk_level_enum, nullable=False)
    is_flagged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    recommended_action: Mapped[str] = mapped_column(String(32), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    # Array of anomaly flag objects (code/title/severity/description/evidence).
    flags: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    engine_version: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=FRAUD_ENGINE_VERSION
    )
    # ``clock_timestamp()``, not ``now()``: ``now()`` is fixed for the whole transaction, so two
    # screenings written in one request would tie and neither would be identifiable as the latest.
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )

    claim: Mapped["Claim"] = relationship("Claim", back_populates="fraud_results")  # noqa: F821

    @property
    def flag_list(self) -> list:
        return self.flags or []

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<FraudResult score={self.risk_score} {self.risk_level} flagged={self.is_flagged}>"
