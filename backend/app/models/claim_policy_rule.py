"""Local-travel policy rule storage — a first, narrow slice of
``backend/assets/Rules_Consolidated.xlsx``.

Deliberately separate from :class:`app.models.policy.PolicyRule`: that table matches an item to a
rule on ``category`` alone; this one matches on ``category`` + ``travel_type`` + ``grade_band`` +
``duration`` together, a different shape of rule entirely. See ``doc/travel-policy-rules.md`` for
the full design and why only 3 of the source spreadsheet's 38 rows are modeled here.

Same versioned, effective-dated idiom as ``PolicyRule``: a row is never edited in place, only
superseded by a new ``version``. Each scope column uses ``NULL`` as an explicit wildcard — e.g.
``travel_type IS NULL`` means "applies to every travel type" — so the engine's match is a plain
``rule.value IS NULL OR rule.value = item.value`` per column.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    ClaimPolicyRuleType,
    ExpenseDuration,
    GradeBand,
    TravelType,
    claim_policy_rule_type_enum,
    expense_duration_enum,
    grade_band_enum,
    travel_type_enum,
)

MONEY = Numeric(14, 2)


class ClaimPolicyRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "claim_policy_rules"
    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_claim_policy_rules_code_version"),
        CheckConstraint(
            "expiration_date IS NULL OR expiration_date >= effective_date",
            name="ck_claim_policy_rules_effective_window",
        ),
        CheckConstraint("amount IS NULL OR amount >= 0", name="ck_claim_policy_rules_amount_non_negative"),
        CheckConstraint("version > 0", name="ck_claim_policy_rules_version_positive"),
        CheckConstraint("char_length(currency) = 3", name="ck_claim_policy_rules_currency_iso4217"),
        # A prohibition carries no amount; an amount cap must carry one — never silently 0.
        CheckConstraint(
            "(rule_type = 'PROHIBITED' AND amount IS NULL) OR "
            "(rule_type = 'AMOUNT_CAP' AND amount IS NOT NULL)",
            name="ck_claim_policy_rules_amount_matches_rule_type",
        ),
        Index("ix_claim_policy_rules_is_active", "is_active"),
        Index("ix_claim_policy_rules_effective_window", "effective_date", "expiration_date"),
        Index(
            "ix_claim_policy_rules_scope",
            "category", "travel_type", "grade_band", "duration", "is_active",
        ),
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Each nullable scope column is an explicit wildcard: NULL matches every value of that axis.
    category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    travel_type: Mapped[Optional[TravelType]] = mapped_column(travel_type_enum, nullable=True)
    grade_band: Mapped[Optional[GradeBand]] = mapped_column(grade_band_enum, nullable=True)
    duration: Mapped[Optional[ExpenseDuration]] = mapped_column(expense_duration_enum, nullable=True)

    rule_type: Mapped[ClaimPolicyRuleType] = mapped_column(
        claim_policy_rule_type_enum, nullable=False
    )
    amount: Mapped[Optional[Decimal]] = mapped_column(MONEY, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="INR")

    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    expiration_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Lower number = evaluated first when several rules match the same item.
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100, server_default="100")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    created_by_sub: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    def is_effective_on(self, on_date: date) -> bool:
        """Whether this rule governs an expense dated ``on_date``."""
        if not self.is_active:
            return False
        if on_date < self.effective_date:
            return False
        return self.expiration_date is None or on_date <= self.expiration_date

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<ClaimPolicyRule {self.code} v{self.version} "
            f"{self.rule_type.value} active={self.is_active}>"
        )
