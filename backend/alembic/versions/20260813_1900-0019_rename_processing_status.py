"""claims: rename the Processing_AI status label to Processing

``app.models.enums.ClaimStatus.PROCESSING`` was renamed from ``"Processing_AI"`` to
``"Processing"`` (shorter, and no longer AI-specific now that the same state also covers the
plain policy/fraud evaluation step). The Python state machine and ``0018``'s trigger rebuild
already used the new spelling, but the ``claim_status`` Postgres enum type itself still carried
the old label — ``ALTER TYPE ... ADD VALUE``/``CREATE OR REPLACE FUNCTION`` cannot rename an
existing label, so nothing before this migration actually did that part.

Until this runs, any row already sitting in ``Processing_AI`` fails to deserialize (SQLAlchemy's
enum has no member mapped to that label), and the transition-guard trigger rejects
``Submitted -> Processing`` because it only recognizes the DB's real label, not the app's new one.

``ALTER TYPE ... RENAME VALUE`` (PG >= 10) updates the label in place — no data rewrite, and any
row currently in ``Processing_AI`` transparently reads back as ``Processing`` afterward.

Revision ID: 0019_rename_processing_status
Revises: 0018_claim_processing_failed
Create Date: 2026-08-13
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0019_rename_processing_status"
down_revision: Union[str, None] = "0018_claim_processing_failed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CLAIM_STATUS_ENUM = "claim_status"
OLD_LABEL = "Processing_AI"
NEW_LABEL = "Processing"

# Identical to 0018's CLAIM_TRANSITIONS — the trigger already used "Processing", this migration
# only needs to re-apply the same edge list so the CREATE OR REPLACE below stays byte-identical
# in shape to every prior rebuild (0002/0010/0011/0018).
CLAIM_TRANSITIONS: tuple[tuple[str, str], ...] = (
    ("Draft", "Submitted"),
    ("Submitted", "Processing"),
    ("Submitted", "Withdrawn"),
    ("Processing", "Auto_Approved"),
    ("Processing", "Manager_Review"),
    ("Processing", "Finance_Review"),
    ("Processing", "Flagged_Fraud"),
    ("Processing", "Withdrawn"),
    ("Processing", "Failed"),
    ("Auto_Approved", "Approved"),
    ("Auto_Approved", "Manager_Review"),
    ("Auto_Approved", "Flagged_Fraud"),
    ("Auto_Approved", "Withdrawn"),
    ("Manager_Review", "Finance_Review"),
    ("Manager_Review", "Approved"),
    ("Manager_Review", "Rejected"),
    ("Manager_Review", "Flagged_Fraud"),
    ("Manager_Review", "Withdrawn"),
    ("Finance_Review", "Approved"),
    ("Finance_Review", "Rejected"),
    ("Finance_Review", "Flagged_Fraud"),
    ("Finance_Review", "Withdrawn"),
    ("Approved", "Flagged_Fraud"),
    ("Flagged_Fraud", "Manager_Review"),
    ("Flagged_Fraud", "Finance_Review"),
    ("Flagged_Fraud", "Approved"),
    ("Flagged_Fraud", "Rejected"),
)

# 0018's edge list, with the old label — restored by downgrade().
CLAIM_TRANSITIONS_OLD_LABEL: tuple[tuple[str, str], ...] = tuple(
    (OLD_LABEL if src == NEW_LABEL else src, OLD_LABEL if dst == NEW_LABEL else dst)
    for src, dst in CLAIM_TRANSITIONS
)


def _transition_guard_sql(transitions: tuple[tuple[str, str], ...]) -> str:
    """PL/pgSQL function body enforcing ``transitions`` on UPDATE OF status.

    Byte-identical in shape to every prior rebuild (0002/0010/0011/0018) — only the pair list
    differs — so the parity test's ``('From','To')`` regex keeps matching.
    """
    pairs = ",\n            ".join(f"('{src}','{dst}')" for src, dst in transitions)
    return f"""
    CREATE OR REPLACE FUNCTION claims_status_transition_guard() RETURNS trigger AS $$
    DECLARE
        is_allowed boolean;
    BEGIN
        IF NEW.status = OLD.status THEN
            RETURN NEW;
        END IF;

        SELECT EXISTS (
            SELECT 1 FROM (VALUES
            {pairs}
            ) AS t(src, dst)
            WHERE t.src = OLD.status::text AND t.dst = NEW.status::text
        ) INTO is_allowed;

        IF NOT is_allowed THEN
            RAISE EXCEPTION
                'illegal claim status transition: % -> % (claim %)',
                OLD.status, NEW.status, OLD.id
                USING ERRCODE = 'check_violation';
        END IF;

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def upgrade() -> None:
    # ALTER TYPE ... RENAME VALUE cannot run inside a transaction block on PG < 12, and Alembic
    # wraps each migration in one. COMMIT first so the rename is durable before the DDL below.
    op.execute("COMMIT")
    op.execute(f"ALTER TYPE {CLAIM_STATUS_ENUM} RENAME VALUE '{OLD_LABEL}' TO '{NEW_LABEL}'")
    op.execute("BEGIN")

    # The guard already used "Processing" as of 0018 (written ahead of this rename landing), so
    # this is a no-op in content — re-applied only so the function's edge list and the enum's
    # actual labels are asserted in the same migration that fixes the mismatch between them.
    op.execute(_transition_guard_sql(CLAIM_TRANSITIONS))


def downgrade() -> None:
    """Reverse of :func:`upgrade`: rename the label back, then restore the old-label edge list."""
    op.execute("COMMIT")
    op.execute(f"ALTER TYPE {CLAIM_STATUS_ENUM} RENAME VALUE '{NEW_LABEL}' TO '{OLD_LABEL}'")
    op.execute("BEGIN")
    op.execute(_transition_guard_sql(CLAIM_TRANSITIONS_OLD_LABEL))
