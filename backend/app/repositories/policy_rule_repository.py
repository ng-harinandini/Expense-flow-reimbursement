"""Policy rule persistence.

Rules are versioned and effective-dated rather than edited in place: :meth:`publish_version`
supersedes the previous version by setting ``is_active = false`` and stamping an
``expiration_date``, then inserts ``version + 1``. A historical claim can therefore always be
explained by the rule text that was live on its expense date (:meth:`list_effective`).

Phase 1 reads these rows to *feed* the existing category engine. Evaluating ``conditions`` /
``actions`` is explicitly out of scope here.
"""

from __future__ import annotations

from datetime import date
from typing import Optional, Sequence

from sqlalchemy import func, select

from app.models.policy import PolicyRule
from app.repositories.base import BaseRepository


class PolicyRuleRepository(BaseRepository[PolicyRule]):
    model = PolicyRule

    # --- reads ---------------------------------------------------------------

    def list_active(self) -> Sequence[PolicyRule]:
        """All active rules, priority first — the ruleset the API exposes today."""
        return self._all(
            select(PolicyRule)
            .where(PolicyRule.is_active.is_(True))
            .order_by(PolicyRule.priority, PolicyRule.category)
        )

    def list_effective(self, on_date: Optional[date] = None) -> Sequence[PolicyRule]:
        """Active rules whose effective window contains ``on_date`` (default: today)."""
        as_of = on_date or date.today()
        return self._all(
            select(PolicyRule)
            .where(
                PolicyRule.is_active.is_(True),
                PolicyRule.effective_date <= as_of,
                (PolicyRule.expiration_date.is_(None)) | (PolicyRule.expiration_date >= as_of),
            )
            .order_by(PolicyRule.priority, PolicyRule.category)
        )

    def get_active_for_category(
        self, category: str, *, on_date: Optional[date] = None
    ) -> Optional[PolicyRule]:
        """Highest-priority rule governing ``category`` on ``on_date``."""
        as_of = on_date or date.today()
        return self._one_or_none(
            select(PolicyRule)
            .where(
                PolicyRule.category == category,
                PolicyRule.is_active.is_(True),
                PolicyRule.effective_date <= as_of,
                (PolicyRule.expiration_date.is_(None)) | (PolicyRule.expiration_date >= as_of),
            )
            .order_by(PolicyRule.priority, PolicyRule.version.desc())
        )

    def get_by_code(self, code: str, *, version: Optional[int] = None) -> Optional[PolicyRule]:
        stmt = select(PolicyRule).where(PolicyRule.code == code)
        if version is not None:
            stmt = stmt.where(PolicyRule.version == version)
        else:
            stmt = stmt.order_by(PolicyRule.version.desc())
        return self._one_or_none(stmt)

    def list_versions(self, code: str) -> Sequence[PolicyRule]:
        """Every version of one rule, newest first — the change history."""
        return self._all(
            select(PolicyRule)
            .where(PolicyRule.code == code)
            .order_by(PolicyRule.version.desc())
        )

    def latest_version_number(self, code: str) -> int:
        """Highest existing version for ``code``, or ``0`` when the rule is new."""
        return int(
            self.session.execute(
                select(func.coalesce(func.max(PolicyRule.version), 0)).where(
                    PolicyRule.code == code
                )
            ).scalar_one()
        )

    # --- writes --------------------------------------------------------------

    def publish_version(
        self,
        *,
        code: str,
        name: str,
        category: str,
        effective_date: Optional[date] = None,
        created_by_sub: Optional[str] = None,
        is_active: bool = True,
        **fields,
    ) -> PolicyRule:
        """Publish the next version of ``code``, retiring the current one.

        Existing rows are never mutated beyond being marked inactive and expiry-stamped, so the
        superseded text stays queryable.
        """
        effective_from = effective_date or date.today()
        next_version = self.latest_version_number(code) + 1

        for previous in self._all(
            select(PolicyRule).where(
                PolicyRule.code == code, PolicyRule.is_active.is_(True)
            )
        ):
            previous.is_active = False
            if previous.expiration_date is None:
                # A rule cannot expire before it took effect (DB check constraint).
                previous.expiration_date = max(effective_from, previous.effective_date)

        rule = PolicyRule(
            code=code,
            version=next_version,
            name=name,
            category=category,
            effective_date=effective_from,
            is_active=is_active,
            created_by_sub=created_by_sub,
            **fields,
        )
        self.session.add(rule)
        self.session.flush()
        return rule

    def deactivate(self, rule: PolicyRule, *, on_date: Optional[date] = None) -> PolicyRule:
        """Retire a rule without publishing a replacement."""
        rule.is_active = False
        if rule.expiration_date is None:
            rule.expiration_date = max(on_date or date.today(), rule.effective_date)
        self.session.flush()
        return rule

    def deactivate_all_active(self, *, on_date: Optional[date] = None) -> int:
        """Retire every active rule. Used when a caller replaces the whole ruleset."""
        active = list(self.list_active())
        for rule in active:
            self.deactivate(rule, on_date=on_date)
        return len(active)
