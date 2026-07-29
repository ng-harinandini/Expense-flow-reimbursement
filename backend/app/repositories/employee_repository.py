"""Department and employee lookups."""

from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import select

from app.models.organization import Department, Employee
from app.repositories.base import BaseRepository


class DepartmentRepository(BaseRepository[Department]):
    model = Department

    def get_by_code(self, code: str) -> Optional[Department]:
        return self._one_or_none(select(Department).where(Department.code == code))

    def get_by_name(self, name: str) -> Optional[Department]:
        return self._one_or_none(select(Department).where(Department.name == name))

    def list_active(self) -> Sequence[Department]:
        return self._all(
            select(Department).where(Department.is_active.is_(True)).order_by(Department.name)
        )


class EmployeeRepository(BaseRepository[Employee]):
    model = Employee

    def get_by_code(self, employee_code: str) -> Optional[Employee]:
        """Look up by the external code carried in ``custom:employeeId``."""
        if not employee_code:
            return None
        return self._one_or_none(
            select(Employee).where(Employee.employee_code == employee_code)
        )

    def get_by_cognito_sub(self, sub: str) -> Optional[Employee]:
        if not sub:
            return None
        return self._one_or_none(select(Employee).where(Employee.cognito_sub == sub))

    def get_by_email(self, email: str) -> Optional[Employee]:
        if not email:
            return None
        return self._one_or_none(
            select(Employee).where(Employee.email == email.strip().lower())
        )

    def resolve(
        self, *, employee_code: Optional[str] = None, cognito_sub: Optional[str] = None
    ) -> Optional[Employee]:
        """Find the employee behind a token identity.

        ``custom:employeeId`` is checked first because it is the explicit business link; the
        Cognito ``sub`` is the fallback for users onboarded without that attribute.
        """
        if employee_code:
            found = self.get_by_code(employee_code)
            if found is not None:
                return found
        if cognito_sub:
            return self.get_by_cognito_sub(cognito_sub)
        return None

    def list_active(self, *, limit: Optional[int] = None, offset: int = 0) -> Sequence[Employee]:
        stmt = (
            select(Employee)
            .where(Employee.is_active.is_(True))
            .order_by(Employee.employee_code)
        )
        return self._all(self._paginate(stmt, limit=limit, offset=offset))

    def list_direct_reports(self, manager_id) -> Sequence[Employee]:
        return self._all(
            select(Employee)
            .where(Employee.manager_id == manager_id)
            .order_by(Employee.employee_code)
        )

    def link_cognito_sub(self, employee: Employee, sub: str) -> Employee:
        """Attach a Cognito ``sub`` to an employee record (first authenticated action)."""
        if sub and employee.cognito_sub != sub:
            employee.cognito_sub = sub
            self.session.flush()
        return employee
