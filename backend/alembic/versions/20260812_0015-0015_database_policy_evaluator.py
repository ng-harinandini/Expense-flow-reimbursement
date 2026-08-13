"""Make global claim-age and attendee validation policy-configurable.

The policy evaluator reads these values from ``policy_rules.actions``.  This migration adds the
global age rule and backfills the attendee-text setting for already-seeded client-entertainment
rules, so existing environments retain their previous behaviour while allowing Finance to change
both values through the policy rules API.
"""

import uuid
from datetime import date
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0015_database_policy_evaluator"
down_revision: Union[str, None] = "0014_item_ai_classification"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SEED_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


def _seed_id(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(_SEED_NAMESPACE, f"expenseflow:{kind}:{key}")


def upgrade() -> None:
    bind = op.get_bind()

    bind.execute(
        sa.text(
            """
            UPDATE policy_rules
               SET actions = jsonb_set(
                   COALESCE(actions, '{}'::jsonb),
                   '{minimumAttendeeTextLength}', '5'::jsonb, true
               )
             WHERE code = 'CLIENT_ENTERTAINMENT_STANDARD'
               AND version = 1
            """
        )
    )

    bind.execute(
        sa.text(
            """
            INSERT INTO policy_rules (
                id, code, version, name, description, category, country, currency,
                grade_tier, expense_limit, auto_approve_limit, receipt_required_above,
                requires_pre_approval, effective_date, expiration_date, priority, is_active,
                conditions, actions, special_rules
            )
            VALUES (
                :id, 'CLAIM_AGE_LIMIT', 1, 'Claim Submission Age Limit',
                'Maximum number of days between an expense and its submission.', '*', NULL, 'USD',
                'All Staff', NULL, NULL, NULL, false, :effective_date, NULL, 1, true,
                CAST(:conditions AS jsonb), CAST(:actions AS jsonb), CAST(:special_rules AS jsonb)
            )
            ON CONFLICT (code, version) DO NOTHING
            """
        ),
        {
            "id": _seed_id("policy_rule", "CLAIM_AGE_LIMIT:1"),
            "effective_date": date(2026, 1, 1),
            "conditions": "{\"submissionAgeDays\":{\"lte\":90}}",
            "actions": "{\"maxClaimAgeDays\":90,\"routeOnBreach\":\"Finance_Director_Approval\"}",
            "special_rules": "[\"Claims beyond this age require Finance Director approval.\"]",
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM policy_rules WHERE code = 'CLAIM_AGE_LIMIT' AND version = 1"
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE policy_rules
               SET actions = actions - 'minimumAttendeeTextLength'
             WHERE code = 'CLIENT_ENTERTAINMENT_STANDARD'
               AND version = 1
            """
        )
    )
