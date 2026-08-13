"""expense_items/claims: system-authored hold_reason columns.

Neither table had a plain-text explanation of *why* an item/claim landed on Policy_Hold or
Fraud_Flag — only nested JSON (``policy_validation``/``travel_policy_validation``.reasoningSummary,
``fraud_flags``) that a client has to know to dig into. ``ClaimService._build_hold_reason`` now
writes a single human-readable sentence per item, combining whichever engine(s) actually drove the
routing decision; ``claims.hold_reason`` is a roll-up across the claim's held items. Both are NULL
for a clean item/claim — nothing to explain. Distinct from the existing ``decision_notes``/
``rejection_reason`` columns, which are human-authored at review time, not system-generated.

Revision ID: 0017_hold_reason_columns
Revises: 0016_claim_policy_rules
Create Date: 2026-08-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017_hold_reason_columns"
down_revision: Union[str, None] = "0016_claim_policy_rules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("expense_items", sa.Column("hold_reason", sa.Text(), nullable=True))
    op.add_column("claims", sa.Column("hold_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("claims", "hold_reason")
    op.drop_column("expense_items", "hold_reason")
