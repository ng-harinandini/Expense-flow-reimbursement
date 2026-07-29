"""Fraud screening result persistence (append-only per claim)."""

from __future__ import annotations

import uuid
from typing import Optional, Sequence

from sqlalchemy import select

from app.models.enums import FraudRiskLevel
from app.models.fraud import FRAUD_ENGINE_VERSION, FraudResult
from app.repositories.base import BaseRepository


class FraudResultRepository(BaseRepository[FraudResult]):
    model = FraudResult

    def record(
        self,
        *,
        claim_id: uuid.UUID,
        risk_score: int,
        risk_level: FraudRiskLevel | str,
        is_flagged: bool,
        recommended_action: str,
        rationale: str = "",
        flags: Optional[list] = None,
        engine_version: str = FRAUD_ENGINE_VERSION,
    ) -> FraudResult:
        """Append a screening verdict. Earlier verdicts for the claim are kept."""
        result = FraudResult(
            claim_id=claim_id,
            risk_score=int(risk_score),
            risk_level=FraudRiskLevel.coerce(risk_level),
            is_flagged=bool(is_flagged),
            recommended_action=recommended_action,
            rationale=rationale or "",
            flags=flags or [],
            engine_version=engine_version,
        )
        self.session.add(result)
        self.session.flush()
        return result

    def latest_for_claim(self, claim_id: uuid.UUID) -> Optional[FraudResult]:
        """The effective verdict. The ``id`` tie-break keeps the result deterministic."""
        return self._one_or_none(
            select(FraudResult)
            .where(FraudResult.claim_id == claim_id)
            .order_by(
                FraudResult.evaluated_at.desc(),
                FraudResult.created_at.desc(),
                FraudResult.id.desc(),
            )
        )

    def list_for_claim(self, claim_id: uuid.UUID) -> Sequence[FraudResult]:
        """Full screening history for a claim, newest first."""
        return self._all(
            select(FraudResult)
            .where(FraudResult.claim_id == claim_id)
            .order_by(
                FraudResult.evaluated_at.desc(),
                FraudResult.created_at.desc(),
                FraudResult.id.desc(),
            )
        )

    def list_flagged(self, *, limit: Optional[int] = None) -> Sequence[FraudResult]:
        return self._all(
            self._paginate(
                select(FraudResult)
                .where(FraudResult.is_flagged.is_(True))
                .order_by(FraudResult.evaluated_at.desc()),
                limit=limit,
            )
        )
