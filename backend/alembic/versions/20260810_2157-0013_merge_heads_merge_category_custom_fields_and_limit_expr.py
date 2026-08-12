"""merge category_custom_fields and limit_expression_unbounded heads

Two independent branches both descended from ``0009_candidate_policy_rules``: the claim line
(``0010_claim_withdrawal`` -> ``0011_retire_disburse_action`` -> ``0012_category_custom_fields``)
and the candidate-policy line (``0010_candidate_review_metadata`` ->
``0011_candidate_currency_nullable`` -> ``0012_limit_expression_unbounded``). They never touched
the same tables, so the branches are independent and this merge carries no DDL of its own — it
exists only to restore a single head so ``alembic upgrade head`` resolves.

Revision ID: 0013_merge_heads
Revises: 0012_category_custom_fields, 0012_limit_expression_unbounded
Create Date: 2026-08-10 21:57:52.203992
"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "0013_merge_heads"
down_revision: Union[str, Sequence[str], None] = (
    "0012_category_custom_fields",
    "0012_limit_expression_unbounded",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op: a merge point, not a schema change."""


def downgrade() -> None:
    """No-op: re-splits into the two heads this revision joined."""
