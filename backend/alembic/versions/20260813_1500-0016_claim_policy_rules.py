"""Local travel policy rules — first slice of Rules_Consolidated.xlsx.

Adds ``claim_policy_rules`` (a narrower, differently-scoped sibling of ``policy_rules`` — matched
on category + travel_type + grade_band + duration together, not category alone) and the travel
scope columns on ``expense_items`` the engine reads them against. Seeds the 3 rules (2 amount
caps split across 4 grade bands each, 1 flat prohibition) that survive filtering the source
spreadsheet's 38 rows down to ones expressible without a per-diem/advance/document/city-tier
domain this codebase doesn't have yet. See ``doc/travel-policy-rules.md`` for the full design and
scope rationale.

Revision ID: 0016_claim_policy_rules
Revises: 0015_database_policy_evaluator
Create Date: 2026-08-13
"""

import uuid
from datetime import date
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_claim_policy_rules"
down_revision: Union[str, None] = "0015_database_policy_evaluator"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TRAVEL_TYPE_VALUES = ("Local", "Domestic", "International")
EXPENSE_DURATION_VALUES = ("Day", "Month")
GRADE_BAND_VALUES = ("Band 1", "Band 2", "Band 3", "Band 4")
CLAIM_POLICY_RULE_TYPE_VALUES = ("AMOUNT_CAP", "PROHIBITED")

_ENUMS = (
    ("travel_type", TRAVEL_TYPE_VALUES),
    ("expense_duration", EXPENSE_DURATION_VALUES),
    ("grade_band", GRADE_BAND_VALUES),
    ("claim_policy_rule_type", CLAIM_POLICY_RULE_TYPE_VALUES),
)

MONEY = sa.Numeric(14, 2)

_SEED_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


def _enum(name: str, values: Sequence[str]) -> postgresql.ENUM:
    return postgresql.ENUM(*values, name=name, create_type=False)


def _seed_id(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(_SEED_NAMESPACE, f"expenseflow:{kind}:{key}")


# (code, name, category, grade_band, amount) — travel_type=Local, duration=Day, currency=INR for
# every row here. Amounts are a linear interpolation of the sheet's stated range (Band 1 = top,
# Band 4 = bottom): Local Conveyance Entitlement / Local Cab-Ride-Hailing Booking merge into one
# rule (identical Rs 300-1,200/day cap); Local Meal Allowance is Rs 150-300/day.
_CONVEYANCE_CAPS = (
    ("LOCAL_CONVEYANCE_CAP_BAND_1", "Local Conveyance Entitlement — Band 1", "Band 1", "1200.00"),
    ("LOCAL_CONVEYANCE_CAP_BAND_2", "Local Conveyance Entitlement — Band 2", "Band 2", "900.00"),
    ("LOCAL_CONVEYANCE_CAP_BAND_3", "Local Conveyance Entitlement — Band 3", "Band 3", "600.00"),
    ("LOCAL_CONVEYANCE_CAP_BAND_4", "Local Conveyance Entitlement — Band 4", "Band 4", "300.00"),
)
_MEAL_CAPS = (
    ("LOCAL_MEAL_ALLOWANCE_CAP_BAND_1", "Local Meal Allowance — Band 1", "Band 1", "300.00"),
    ("LOCAL_MEAL_ALLOWANCE_CAP_BAND_2", "Local Meal Allowance — Band 2", "Band 2", "250.00"),
    ("LOCAL_MEAL_ALLOWANCE_CAP_BAND_3", "Local Meal Allowance — Band 3", "Band 3", "200.00"),
    ("LOCAL_MEAL_ALLOWANCE_CAP_BAND_4", "Local Meal Allowance — Band 4", "Band 4", "150.00"),
)

_CONVEYANCE_DESCRIPTION = (
    "Mode of local conveyance and daily cap are set by grade (Rs 300-1,200/day). Merges the "
    "sheet's 'Local Conveyance Entitlement' and 'Local Cab/Ride-Hailing Booking' rows, which "
    "state the same cap. Band amount is a linear interpolation of the stated range; Finance "
    "should confirm exact per-band figures."
)
_MEAL_DESCRIPTION = (
    "One meal allowance is payable per employee per calendar day for local trips "
    "(Rs 150-300/day by grade). Band amount is a linear interpolation of the stated range; "
    "Finance should confirm exact per-band figures."
)
_HOTEL_DESCRIPTION = (
    "No hotel/lodging expense is reimbursable on a local trip; only conveyance and meal "
    "allowance apply. Merges the sheet's 'Local Hotel Booking Restriction' and 'Local "
    "Per-Diem/Lodging Exclusion' rows, which state the same restriction."
)

_EFFECTIVE_DATE = date(2026, 1, 1)


def upgrade() -> None:
    bind = op.get_bind()

    for name, values in _ENUMS:
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    op.create_table(
        "claim_policy_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column("travel_type", _enum("travel_type", TRAVEL_TYPE_VALUES), nullable=True),
        sa.Column("grade_band", _enum("grade_band", GRADE_BAND_VALUES), nullable=True),
        sa.Column("duration", _enum("expense_duration", EXPENSE_DURATION_VALUES), nullable=True),
        sa.Column(
            "rule_type",
            _enum("claim_policy_rule_type", CLAIM_POLICY_RULE_TYPE_VALUES),
            nullable=False,
        ),
        sa.Column("amount", MONEY, nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("expiration_date", sa.Date(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by_sub", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_claim_policy_rules"),
        sa.UniqueConstraint("code", "version", name="uq_claim_policy_rules_code_version"),
        sa.CheckConstraint(
            "expiration_date IS NULL OR expiration_date >= effective_date",
            name="ck_claim_policy_rules_effective_window",
        ),
        sa.CheckConstraint(
            "amount IS NULL OR amount >= 0", name="ck_claim_policy_rules_amount_non_negative"
        ),
        sa.CheckConstraint("version > 0", name="ck_claim_policy_rules_version_positive"),
        sa.CheckConstraint(
            "char_length(currency) = 3", name="ck_claim_policy_rules_currency_iso4217"
        ),
        sa.CheckConstraint(
            "(rule_type = 'PROHIBITED' AND amount IS NULL) OR "
            "(rule_type = 'AMOUNT_CAP' AND amount IS NOT NULL)",
            name="ck_claim_policy_rules_amount_matches_rule_type",
        ),
    )
    op.create_index("ix_claim_policy_rules_is_active", "claim_policy_rules", ["is_active"])
    op.create_index(
        "ix_claim_policy_rules_effective_window",
        "claim_policy_rules",
        ["effective_date", "expiration_date"],
    )
    op.create_index(
        "ix_claim_policy_rules_scope",
        "claim_policy_rules",
        ["category", "travel_type", "grade_band", "duration", "is_active"],
    )

    op.add_column(
        "expense_items",
        sa.Column("travel_type", _enum("travel_type", TRAVEL_TYPE_VALUES), nullable=True),
    )
    op.add_column(
        "expense_items",
        sa.Column("duration", _enum("expense_duration", EXPENSE_DURATION_VALUES), nullable=True),
    )
    op.add_column(
        "expense_items",
        sa.Column(
            "travel_policy_validation", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )

    insert_stmt = sa.text(
        """
        INSERT INTO claim_policy_rules (
            id, code, version, name, description, category, travel_type, grade_band, duration,
            rule_type, amount, currency, effective_date, is_active
        )
        VALUES (
            :id, :code, 1, :name, :description, :category, 'Local', :grade_band, 'Day',
            'AMOUNT_CAP', :amount, 'INR', :effective_date, true
        )
        ON CONFLICT (code, version) DO NOTHING
        """
    )
    for code, name, grade_band, amount in _CONVEYANCE_CAPS:
        bind.execute(
            insert_stmt,
            {
                "id": _seed_id("claim_policy_rule", f"{code}:1"),
                "code": code,
                "name": name,
                "description": _CONVEYANCE_DESCRIPTION,
                "category": "Taxi / Cab / Ride-hailing",
                "grade_band": grade_band,
                "amount": amount,
                "effective_date": _EFFECTIVE_DATE,
            },
        )
    for code, name, grade_band, amount in _MEAL_CAPS:
        bind.execute(
            insert_stmt,
            {
                "id": _seed_id("claim_policy_rule", f"{code}:1"),
                "code": code,
                "name": name,
                "description": _MEAL_DESCRIPTION,
                "category": "Meals",
                "grade_band": grade_band,
                "amount": amount,
                "effective_date": _EFFECTIVE_DATE,
            },
        )

    bind.execute(
        sa.text(
            """
            INSERT INTO claim_policy_rules (
                id, code, version, name, description, category, travel_type, grade_band, duration,
                rule_type, amount, currency, effective_date, is_active
            )
            VALUES (
                :id, 'LOCAL_HOTEL_PROHIBITED', 1, 'Local Hotel Booking Restriction', :description,
                'Hotel / Lodging', 'Local', NULL, NULL, 'PROHIBITED', NULL, 'INR',
                :effective_date, true
            )
            ON CONFLICT (code, version) DO NOTHING
            """
        ),
        {
            "id": _seed_id("claim_policy_rule", "LOCAL_HOTEL_PROHIBITED:1"),
            "description": _HOTEL_DESCRIPTION,
            "effective_date": _EFFECTIVE_DATE,
        },
    )


def downgrade() -> None:
    op.drop_column("expense_items", "travel_policy_validation")
    op.drop_column("expense_items", "duration")
    op.drop_column("expense_items", "travel_type")

    op.drop_index("ix_claim_policy_rules_scope", table_name="claim_policy_rules")
    op.drop_index("ix_claim_policy_rules_effective_window", table_name="claim_policy_rules")
    op.drop_index("ix_claim_policy_rules_is_active", table_name="claim_policy_rules")
    op.drop_table("claim_policy_rules")

    bind = op.get_bind()
    for name, _values in reversed(_ENUMS):
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)
