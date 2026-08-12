"""Persistence for AI-extracted candidate policy rules."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import select

from app.models.candidate_rule import CandidatePolicyRule
from app.models.policy import PolicyRule
from app.repositories.base import BaseRepository


class CandidateRuleRepository(BaseRepository[CandidatePolicyRule]):
    model = CandidatePolicyRule

    def list_for_document(
        self,
        document_id: uuid.UUID,
        *,
        status: Optional[str] = None,
    ) -> Sequence[CandidatePolicyRule]:
        """All candidates for one document, newest first."""
        stmt = (
            select(CandidatePolicyRule)
            .where(CandidatePolicyRule.document_id == document_id)
            .order_by(CandidatePolicyRule.created_at.desc())
        )
        if status is not None:
            stmt = stmt.where(CandidatePolicyRule.status == status)
        return self._all(stmt)

    def list_for_run(self, run_id: uuid.UUID) -> Sequence[CandidatePolicyRule]:
        return self._all(
            select(CandidatePolicyRule)
            .where(CandidatePolicyRule.extraction_run_id == run_id)
            .order_by(CandidatePolicyRule.created_at)
        )

    def approve(
        self,
        candidate: CandidatePolicyRule,
        *,
        published_rule: PolicyRule,
        actor_sub: Optional[str] = None,
    ) -> CandidatePolicyRule:
        candidate.status = "APPROVED"
        candidate.published_rule_id = published_rule.id
        candidate.reviewed_by_sub = actor_sub
        candidate.reviewed_at = datetime.now(timezone.utc)
        self.session.flush()
        return candidate

    def dismiss(
        self,
        candidate: CandidatePolicyRule,
        *,
        actor_sub: Optional[str] = None,
    ) -> CandidatePolicyRule:
        candidate.status = "DISMISSED"
        candidate.reviewed_by_sub = actor_sub
        candidate.reviewed_at = datetime.now(timezone.utc)
        self.session.flush()
        return candidate
