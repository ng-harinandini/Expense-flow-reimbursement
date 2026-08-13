"""``ClaimPolicyRule`` persistence — the local-travel rule reads ``policy_engine.evaluate_travel_policy``
needs. See ``doc/travel-policy-rules.md``.
"""

from __future__ import annotations

from datetime import date
from typing import Optional, Sequence

from sqlalchemy import select

from app.models.claim_policy_rule import ClaimPolicyRule
from app.repositories.base import BaseRepository


class ClaimPolicyRuleRepository(BaseRepository[ClaimPolicyRule]):
    model = ClaimPolicyRule

    def list_effective(self, on_date: Optional[date] = None) -> Sequence[ClaimPolicyRule]:
        """Active rules whose effective window contains ``on_date`` (default: today)."""
        as_of = on_date or date.today()
        return self._all(
            select(ClaimPolicyRule)
            .where(
                ClaimPolicyRule.is_active.is_(True),
                ClaimPolicyRule.effective_date <= as_of,
                (ClaimPolicyRule.expiration_date.is_(None))
                | (ClaimPolicyRule.expiration_date >= as_of),
            )
            .order_by(ClaimPolicyRule.priority, ClaimPolicyRule.code)
        )
