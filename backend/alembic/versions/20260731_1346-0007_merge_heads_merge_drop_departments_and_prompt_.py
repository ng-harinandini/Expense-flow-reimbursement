"""merge drop_departments and prompt_governance

Two independent branches both descended from ``0003_seed_reference_data``: the core-domain line
(``0004_roles_and_employee_cleanup`` -> ``0005_drop_departments``) and the AI-platform line
(``0004_ai_knowledge_platform`` -> ``0005_duplicate_detection`` -> ``0006_prompt_governance``).
They never touched the same tables, so the branches are independent and this merge carries no DDL
of its own — it exists only to restore a single head so ``alembic upgrade head`` resolves.

Revision ID: 0007_merge_heads
Revises: 0005_drop_departments, 0006_prompt_governance
Create Date: 2026-07-31 13:46:12.179995
"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "0007_merge_heads"
down_revision: Union[str, Sequence[str], None] = (
    "0005_drop_departments",
    "0006_prompt_governance",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op: a merge point, not a schema change."""


def downgrade() -> None:
    """No-op: re-splits into the two heads this revision joined."""
