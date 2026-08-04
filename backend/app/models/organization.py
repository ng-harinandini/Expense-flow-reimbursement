"""Organisation directory: employees.

This replaces the ``INITIAL_EMPLOYEES`` Python list that previously lived in
``app/services/store.py``. Identity and credentials still belong to Cognito — this table is the
*business* record of an employee (grade, role, manager chain) and is linked to the token identity
by ``cognito_sub`` and, for legacy callers, by ``employee_code``.

``employee_code`` (e.g. ``emp-101``) is the stable external identifier: it is the value carried in
the ``custom:employeeId`` token claim and the value the existing API exposes as ``employeeId``.
The UUID ``id`` is the internal key every foreign key points at.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import EmployeeGrade, employee_grade_enum

if TYPE_CHECKING:
    from app.models.role import Role


class Employee(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "employees"
    __table_args__ = (
        UniqueConstraint("employee_code", name="uq_employees_employee_code"),
        UniqueConstraint("email", name="uq_employees_email"),
        UniqueConstraint("cognito_sub", name="uq_employees_cognito_sub"),
        Index("ix_employees_role_id", "role_id"),
        Index("ix_employees_manager_id", "manager_id"),
        Index("ix_employees_is_active", "is_active"),
    )

    employee_code: Mapped[str] = mapped_column(String(64), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    grade: Mapped[EmployeeGrade] = mapped_column(employee_grade_enum, nullable=False)

    role_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("roles.id", ondelete="RESTRICT", name="fk_employees_role_id"),
        nullable=False,
    )
    manager_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="SET NULL", name="fk_employees_manager_id"),
        nullable=True,
    )

    # Link to the Cognito identity (``sub``). Nullable: an employee record may exist before the
    # user is invited, and vice versa.
    cognito_sub: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    role: Mapped["Role"] = relationship(back_populates="employees", lazy="joined")
    manager: Mapped[Optional["Employee"]] = relationship(
        remote_side="Employee.id", lazy="selectin"
    )

    @property
    def role_name(self) -> Optional[str]:
        return self.role.name if self.role else None

    @property
    def manager_name(self) -> Optional[str]:
        return self.manager.full_name if self.manager else None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Employee {self.employee_code} {self.full_name!r} {self.grade}>"
