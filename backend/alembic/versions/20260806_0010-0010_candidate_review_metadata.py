"""candidate_policy_rules: review metadata for the Finance approval workflow.

The extractor now returns enough context for a reviewer to approve or edit a candidate without
reopening the source document: the verbatim policy sentence and its section, explicit
non-reimbursable items, required supporting documents, per-field and overall confidence, and
free-text review notes whenever the model was unsure.

All columns are nullable — candidates extracted before this revision stay valid.

Revision ID: 0010_candidate_review_metadata
Revises: 0009_candidate_policy_rules
Create Date: 2026-08-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_candidate_review_metadata"
down_revision: Union[str, None] = "0009_candidate_policy_rules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "candidate_policy_rules"

_NEW_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("source_text", sa.Text()),
    ("source_section", sa.String(500)),
    ("exclusions", postgresql.JSONB()),
    ("required_documents", postgresql.JSONB()),
    ("field_confidence", postgresql.JSONB()),
    ("overall_confidence", sa.Numeric(4, 3)),
    ("review_notes", sa.Text()),
)


def upgrade() -> None:
    for name, type_ in _NEW_COLUMNS:
        op.add_column(_TABLE, sa.Column(name, type_, nullable=True))

    op.create_check_constraint(
        "ck_candidate_rules_confidence_range",
        _TABLE,
        "overall_confidence IS NULL OR (overall_confidence >= 0 AND overall_confidence <= 1)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_candidate_rules_confidence_range", _TABLE, type_="check")
    for name, _type in reversed(_NEW_COLUMNS):
        op.drop_column(_TABLE, name)
