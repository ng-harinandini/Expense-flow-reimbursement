"""retire the DISBURSE action: Approve is now the final reviewer step

Removes the two ``-> Disbursed`` edges from ``claims_status_transition_guard``
(``Auto_Approved -> Disbursed`` and ``Approved -> Disbursed``), matching
``app.domain.claim_state_machine.ALLOWED_TRANSITIONS``. There is no longer a separate
disbursement action anywhere in the application — approving a claim (manager escalation aside)
is now the last reviewer step.

Deliberately **not** touched:

  * The ``Disbursed`` label on the ``claim_status`` enum. PostgreSQL cannot drop an enum value
    (same constraint noted in ``0010``'s downgrade), and even if it could, any claim already paid
    out before this revision legitimately carries that status — rewriting those rows to
    ``Approved`` would erase the fact that money actually moved, which is exactly the distinction
    a reimbursement system must not lose. ``Disbursed`` remains a valid, filterable, terminal
    status for existing rows; it is simply unreachable from any *new* transition.
  * ``claims.reimbursed_at`` / ``claims.reimbursement_reference``. Same reasoning — historical
    payment records stay intact and readable.

Revision ID: 0011_retire_disburse_action
Revises: 0010_claim_withdrawal
Create Date: 2026-08-06
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0011_retire_disburse_action"
down_revision: Union[str, None] = "0010_claim_withdrawal"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 0010's edge list, minus the two edges into Disbursed.
CLAIM_TRANSITIONS: tuple[tuple[str, str], ...] = (
    ("Draft", "Submitted"),
    ("Submitted", "Processing_AI"),
    ("Submitted", "Withdrawn"),
    ("Processing_AI", "Auto_Approved"),
    ("Processing_AI", "Manager_Review"),
    ("Processing_AI", "Finance_Review"),
    ("Processing_AI", "Flagged_Fraud"),
    ("Processing_AI", "Withdrawn"),
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

# 0010's edge list verbatim — restored by downgrade().
CLAIM_TRANSITIONS_0010: tuple[tuple[str, str], ...] = CLAIM_TRANSITIONS + (
    ("Auto_Approved", "Disbursed"),
    ("Approved", "Disbursed"),
)


def _transition_guard_sql(transitions: tuple[tuple[str, str], ...]) -> str:
    """PL/pgSQL function body enforcing ``transitions`` on UPDATE OF status.

    Byte-identical in shape to 0002's/0010's generator — only the pair list differs — so the
    parity test's ``('From','To')`` regex keeps matching.
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
    # CREATE OR REPLACE: the trigger itself still points at this function, so it is not recreated.
    op.execute(_transition_guard_sql(CLAIM_TRANSITIONS))


def downgrade() -> None:
    """Restore the two ``-> Disbursed`` edges. No data to reverse: this revision never rewrites or
    removes any row — it only narrows which *future* transitions are legal."""
    op.execute(_transition_guard_sql(CLAIM_TRANSITIONS_0010))
