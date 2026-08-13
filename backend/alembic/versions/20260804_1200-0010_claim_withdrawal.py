"""claim withdrawal: Withdrawn status, its timestamp/reason columns, and the new edge list

Adds the terminal ``Withdrawn`` claim status — the employee's own way to close a claim they no
longer want reviewed, as opposed to ``Rejected``, which is a reviewer's decision. Keeping the two
apart is the whole point of a new status: a withdrawn claim must not appear as a rejection in any
list, filter, or approval metric.

Three coordinated changes, all of which must land together or the guard and the application
disagree:

  * ``claim_status``   — gains the ``Withdrawn`` value.
  * ``claims``         — gains ``withdrawn_at`` and ``withdrawal_reason``, mirroring the
    ``rejected_at``/``rejection_reason`` pair. ``withdrawal_reason`` is deliberately its own column
    rather than a reuse of ``rejection_reason``: nothing was rejected.
  * ``claims_status_transition_guard`` — the frozen edge list from 0002 is replaced with one that
    includes the five ``-> Withdrawn`` edges. ``tests/test_claim_state_machine.py`` compares the
    installed function body against ``app.domain.claim_state_machine.ALLOWED_TRANSITIONS``, so this
    list and that table must stay identical.

``Flagged_Fraud -> Withdrawn`` is deliberately **absent**: the subject of a fraud investigation must
not be able to end it by withdrawing the claim. ``Approved``/``Disbursed`` are likewise absent —
once a payout decision is made it is too late to pull the claim back.

Enum values and the edge list are copy-pasted rather than imported from ``app.models.enums`` (repo
convention): the DDL a revision emits must stay frozen even if the Python enum later changes.

Revision ID: 0010_claim_withdrawal
Revises: 0009_candidate_policy_rules
Create Date: 2026-08-04
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_claim_withdrawal"
down_revision: Union[str, None] = "0009_candidate_policy_rules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CLAIM_STATUS_ENUM = "claim_status"
WITHDRAWN = "Withdrawn"

# Frozen copy of the legal lifecycle edges at this revision (0002's list + the withdrawal edges).
CLAIM_TRANSITIONS: tuple[tuple[str, str], ...] = (
    ("Draft", "Submitted"),
    ("Submitted", "Processing"),
    ("Submitted", "Withdrawn"),
    ("Processing", "Auto_Approved"),
    ("Processing", "Manager_Review"),
    ("Processing", "Finance_Review"),
    ("Processing", "Flagged_Fraud"),
    ("Processing", "Withdrawn"),
    ("Auto_Approved", "Approved"),
    ("Auto_Approved", "Disbursed"),
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
    ("Approved", "Disbursed"),
    ("Approved", "Flagged_Fraud"),
    ("Flagged_Fraud", "Manager_Review"),
    ("Flagged_Fraud", "Finance_Review"),
    ("Flagged_Fraud", "Approved"),
    ("Flagged_Fraud", "Rejected"),
)

# The 0002 edge list verbatim — restored by downgrade().
CLAIM_TRANSITIONS_0002: tuple[tuple[str, str], ...] = tuple(
    edge for edge in CLAIM_TRANSITIONS if WITHDRAWN not in edge
)


def _transition_guard_sql(transitions: tuple[tuple[str, str], ...]) -> str:
    """PL/pgSQL function body enforcing ``transitions`` on UPDATE OF status.

    Byte-identical in shape to 0002's generator — only the pair list differs — so the parity test's
    ``('From','To')`` regex keeps matching.
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
    # --- 1. extend the enum ---------------------------------------------------
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block on PG < 12, and Alembic wraps
    # each migration in one. COMMIT first so the new label is durable and usable by the DDL below.
    op.execute("COMMIT")
    op.execute(
        f"ALTER TYPE {CLAIM_STATUS_ENUM} ADD VALUE IF NOT EXISTS '{WITHDRAWN}'"
    )
    op.execute("BEGIN")

    # --- 2. the withdrawal columns -------------------------------------------
    op.add_column(
        "claims", sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("claims", sa.Column("withdrawal_reason", sa.Text(), nullable=True))

    # --- 3. rebuild the guard with the withdrawal edges ------------------------
    # CREATE OR REPLACE: the trigger itself still points at this function, so it is not recreated.
    op.execute(_transition_guard_sql(CLAIM_TRANSITIONS))


def downgrade() -> None:
    """Reverse of :func:`upgrade`, with one unavoidable asymmetry.

    PostgreSQL cannot drop a value from an enum type, so ``Withdrawn`` remains a legal *label* on
    ``claim_status`` after a downgrade. It is no longer reachable: the guard is restored to the 0002
    edge list, which has no path to it. Any claim already withdrawn is moved to ``Rejected`` first —
    the row would otherwise hold a status the restored application code cannot interpret.
    """
    op.execute(_transition_guard_sql(CLAIM_TRANSITIONS_0002))

    # The guard fires on UPDATE OF status and would reject Withdrawn -> Rejected under the restored
    # edge list, so disable it for this one corrective statement.
    op.execute("ALTER TABLE claims DISABLE TRIGGER claims_status_transition_guard")
    op.execute(
        f"""
        UPDATE claims
           SET status           = 'Rejected',
               rejection_reason = COALESCE(
                   rejection_reason,
                   'Withdrawn by employee (converted during 0010 downgrade).'
               ),
               rejected_at      = COALESCE(rejected_at, withdrawn_at)
         WHERE status = '{WITHDRAWN}'
        """
    )
    op.execute("ALTER TABLE claims ENABLE TRIGGER claims_status_transition_guard")

    op.drop_column("claims", "withdrawal_reason")
    op.drop_column("claims", "withdrawn_at")
