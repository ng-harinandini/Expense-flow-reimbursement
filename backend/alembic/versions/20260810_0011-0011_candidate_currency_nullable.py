"""candidate_policy_rules: allow an unconfirmed currency on a draft.

The extractor previously stamped every candidate "USD" whenever the source page didn't state a
currency, because this column was NOT NULL. That silently invented a value a Finance reviewer
would trust. A draft may legitimately not know its currency yet; the published PolicyRule table
keeps its own NOT NULL constraint, since a live rule the policy engine enforces still needs one.

Revision ID: 0011_candidate_currency_nullable
Revises: 0010_candidate_review_metadata
Create Date: 2026-08-10
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0011_candidate_currency_nullable"
down_revision: Union[str, None] = "0010_candidate_review_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "candidate_policy_rules"
_COLUMN = "currency"


def upgrade() -> None:
    op.alter_column(_TABLE, _COLUMN, nullable=True, server_default=None)


def downgrade() -> None:
    op.execute(f"UPDATE {_TABLE} SET {_COLUMN} = 'USD' WHERE {_COLUMN} IS NULL")
    op.alter_column(_TABLE, _COLUMN, nullable=False, server_default="USD")
