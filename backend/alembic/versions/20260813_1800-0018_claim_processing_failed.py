"""claims: add the Failed status for a background-processing pipeline that raised

Claim submission (``POST /claims``) is being split so the AI pipeline (policy evaluation, fraud
screening, classification) runs as a FastAPI background task after the response is already sent,
rather than blocking the request for ~20s. If that background task raises, the claim needs a
terminal status to land on other than silently staying in ``Processing`` forever — ``Failed``
is that status, reachable only from ``Processing`` and only by the system role. Same reasoning
and pattern as ``0010``'s ``Withdrawn`` addition.

``claims.hold_reason`` (added in ``0017``) carries the human-readable error message; no new column
needed.

Revision ID: 0018_claim_processing_failed
Revises: 0017_hold_reason_columns
Create Date: 2026-08-13
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0018_claim_processing_failed"
down_revision: Union[str, None] = "0017_hold_reason_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CLAIM_STATUS_ENUM = "claim_status"
FAILED = "Failed"

# 0011's edge list, plus one new edge: Processing -> Failed.
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

# 0011's edge list verbatim (no Failed edge) — restored by downgrade().
CLAIM_TRANSITIONS_0011: tuple[tuple[str, str], ...] = tuple(
    edge for edge in CLAIM_TRANSITIONS if edge != ("Processing", "Failed")
)


def _transition_guard_sql(transitions: tuple[tuple[str, str], ...]) -> str:
    """PL/pgSQL function body enforcing ``transitions`` on UPDATE OF status.

    Byte-identical in shape to 0002's/0010's/0011's generator — only the pair list differs — so the
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
    # --- 1. extend the enum ---------------------------------------------------
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block on PG < 12, and Alembic wraps
    # each migration in one. COMMIT first so the new label is durable and usable by the DDL below.
    op.execute("COMMIT")
    op.execute(f"ALTER TYPE {CLAIM_STATUS_ENUM} ADD VALUE IF NOT EXISTS '{FAILED}'")
    op.execute("BEGIN")

    # --- 2. rebuild the guard with the Processing -> Failed edge -----------
    # CREATE OR REPLACE: the trigger itself still points at this function, so it is not recreated.
    op.execute(_transition_guard_sql(CLAIM_TRANSITIONS))


def downgrade() -> None:
    """Reverse of :func:`upgrade`, with the same unavoidable asymmetry as ``0010``.

    PostgreSQL cannot drop a value from an enum type, so ``Failed`` remains a legal *label* on
    ``claim_status`` after a downgrade. It is no longer reachable: the guard is restored to the
    0011 edge list, which has no path to it. Any claim already ``Failed`` is moved to ``Rejected``
    first — the row would otherwise hold a status the restored application code cannot interpret.
    """
    op.execute(
        "UPDATE claims SET status = 'Rejected' WHERE status = 'Failed'"
    )
    op.execute(_transition_guard_sql(CLAIM_TRANSITIONS_0011))
