"""roles table, employees.role_id, drop employees.notes, remove stale seed employees

Adds the ``roles`` reference table (5 rows, matching ``VALID_ROLES`` in ``app/core/deps.py``) and
an ``employees.role_id`` FK. ``employees.notes`` is dropped: confirmed dead (no schema, repository,
service, or UI ever read or wrote it).

The 5 employees seeded by ``0003_seed_reference_data`` (``emp-100``..``emp-104``) predate the role
system entirely (no ``cognito_sub``, never invited) and are removed here so ``role_id`` can be made
``NOT NULL`` without a fabricated backfill. The deletion reuses the exact guarded routine already
written in that migration's own ``downgrade()`` — rows are skipped, not force-deleted, if anything
now genuinely references them (real claims, approval assignments, or another employee's manager),
so this can never destroy business data.

Revision ID: 0007_roles_and_employee_cleanup
Revises: 0006_prompt_governance
Create Date: 2026-07-29

Renumbered from ``0004`` when this branch was merged into the T004 AI platform work, which had
already taken ``0004``–``0006``. This revision is independent of those three (they only add
``knowledge_*``/``ai_*`` tables, none of which reference ``employees``), so it simply re-chains onto
``0006_prompt_governance`` with no change to its own upgrade/downgrade bodies.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_roles_and_employee_cleanup"
down_revision: Union[str, None] = "0006_prompt_governance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ROLES: tuple[tuple[str, str], ...] = (
    ("employee", "Submits and tracks their own expense claims."),
    ("manager", "Reviews and approves or rejects direct reports' expense claims."),
    ("finance", "Performs final review, disbursement, and financial oversight of approved claims."),
    ("admin", "Manages user accounts, roles, and platform configuration."),
    ("auditor", "Read-only access to audit trails and compliance reporting across all claims."),
)

# emp-100..emp-104, reverse seed order (reports before managers) — mirrors the manager chain in
# 0003_seed_reference_data.EMPLOYEES so a report is always deleted before its manager.
STALE_EMPLOYEE_CODES = ("emp-104", "emp-102", "emp-101", "emp-103", "emp-100")


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.UniqueConstraint("name", name="uq_roles_name"),
    )

    for name, description in ROLES:
        bind.execute(
            sa.text(
                "INSERT INTO roles (name, description) VALUES (:name, :description) "
                "ON CONFLICT (name) DO NOTHING"
            ),
            {"name": name, "description": description},
        )

    # Detach receipts uploaded before an employee row existed, same as 0003's own downgrade.
    bind.execute(
        sa.text(
            """
            UPDATE receipts
               SET employee_ref_id = NULL
             WHERE employee_ref_id IN (
                   SELECT id FROM employees WHERE employee_code = ANY(:codes))
            """
        ),
        {"codes": list(STALE_EMPLOYEE_CODES)},
    )
    for code in STALE_EMPLOYEE_CODES:
        bind.execute(
            sa.text(
                """
                DELETE FROM employees e
                 WHERE e.employee_code = :code
                   AND NOT EXISTS (SELECT 1 FROM claims c WHERE c.employee_id = e.id
                                    OR c.assigned_reviewer_id = e.id)
                   AND NOT EXISTS (SELECT 1 FROM employees m WHERE m.manager_id = e.id)
                   AND NOT EXISTS (SELECT 1 FROM approval_steps s WHERE s.assignee_id = e.id)
                """
            ),
            {"code": code},
        )

    op.add_column("employees", sa.Column("role_id", sa.Integer(), nullable=True))
    # NOT NULL is only safe because the deletions above cleared every row that predates the role
    # system. If this fails, some other employee row exists with no role assigned — that needs a
    # real decision, not a silent backfill.
    op.alter_column("employees", "role_id", nullable=False)
    op.create_foreign_key(
        "fk_employees_role_id", "employees", "roles", ["role_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_index("ix_employees_role_id", "employees", ["role_id"])

    op.drop_column("employees", "notes")


def downgrade() -> None:
    op.add_column("employees", sa.Column("notes", sa.Text(), nullable=True))

    op.drop_index("ix_employees_role_id", table_name="employees")
    op.drop_constraint("fk_employees_role_id", "employees", type_="foreignkey")
    op.drop_column("employees", "role_id")

    bind = op.get_bind()
    for name, _description in ROLES:
        bind.execute(sa.text("DELETE FROM roles WHERE name = :name"), {"name": name})

    op.drop_table("roles")
