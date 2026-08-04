"""drop departments entirely: claims.department_id, employees.department_id, departments table

Department is dropped everywhere, not just from ``employees`` — ``claims.department_id`` (a
point-in-time snapshot column) is dropped first so the FK to ``departments`` no longer exists,
then ``employees.department_id``, then the ``departments`` table itself.

Revision ID: 0005_drop_departments
Revises: 0004_roles_and_employee_cleanup
Create Date: 2026-07-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005_drop_departments"
down_revision: Union[str, None] = "0004_roles_and_employee_cleanup"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_claims_department_id", table_name="claims")
    op.drop_constraint("fk_claims_department_id", "claims", type_="foreignkey")
    op.drop_column("claims", "department_id")

    op.drop_index("ix_employees_department_id", table_name="employees")
    op.drop_constraint("fk_employees_department_id", "employees", type_="foreignkey")
    op.drop_column("employees", "department_id")

    op.drop_table("departments")


def downgrade() -> None:
    # Seed data is not restorable here — recreated empty, matching this project's existing
    # asymmetry (0003's own downgrade does not attempt to re-seed deleted employees either).
    op.create_table(
        "departments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("cost_center", sa.String(length=32), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("code", name="uq_departments_code"),
        sa.UniqueConstraint("name", name="uq_departments_name"),
    )

    op.add_column(
        "employees", sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_employees_department_id", "employees", "departments", ["department_id"], ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_employees_department_id", "employees", ["department_id"])

    op.add_column(
        "claims", sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_claims_department_id", "claims", "departments", ["department_id"], ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_claims_department_id", "claims", ["department_id"])
