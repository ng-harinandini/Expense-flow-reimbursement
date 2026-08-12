"""candidate_policy_rules / policy_rules: stop truncating limit_expression at 120 chars.

A variable/formula/reference-based limit ("published quarterly by Finance in the Global Travel
Portal, matching the IRS standard mileage rate") must be extracted exactly as stated. VARCHAR(120)
silently truncated any clause longer than that. Widen both the candidate (draft) and published
tables to unbounded text so approving a candidate never re-truncates it a second time.

Revision ID: 0012_limit_expression_unbounded
Revises: 0011_candidate_currency_nullable
Create Date: 2026-08-10
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_limit_expression_unbounded"
down_revision: Union[str, None] = "0011_candidate_currency_nullable"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMN = "limit_expression"
_TABLES = ("candidate_policy_rules", "policy_rules")


def upgrade() -> None:
    for table in _TABLES:
        op.alter_column(table, _COLUMN, existing_type=sa.String(120), type_=sa.Text())


def downgrade() -> None:
    for table in _TABLES:
        op.alter_column(table, _COLUMN, existing_type=sa.Text(), type_=sa.String(120))
