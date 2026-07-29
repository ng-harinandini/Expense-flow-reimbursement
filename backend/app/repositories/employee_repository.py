"""Employee lookups."""

from __future__ import annotations

import re
from typing import Dict, Optional, Sequence

from sqlalchemy import select

from app.models.organization import Employee
from app.repositories.base import BaseRepository

_EMPLOYEE_CODE_RE = re.compile(r"^emp-(\d+)$")


class EmployeeRepository(BaseRepository[Employee]):
    model = Employee

    def get_by_code(self, employee_code: str) -> Optional[Employee]:
        """Look up by the external code carried in ``custom:employeeId``."""
        if not employee_code:
            return None
        return self._one_or_none(
            select(Employee).where(Employee.employee_code == employee_code)
        )

    def get_by_codes(self, employee_codes: Sequence[str]) -> Dict[str, Employee]:
        """Batch lookup by external code, keyed by the code — avoids N+1 when enriching a page."""
        codes = [c for c in employee_codes if c]
        if not codes:
            return {}
        rows = self._all(select(Employee).where(Employee.employee_code.in_(codes)))
        return {row.employee_code: row for row in rows}

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

    def list_page(self, *, limit: Optional[int] = None, offset: int = 0) -> Sequence[Employee]:
        """Ordered page over every employee (active and inactive), for admin listings."""
        stmt = select(Employee).order_by(Employee.employee_code)
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

    def next_employee_code(self) -> str:
        """Allocate the next ``emp-NNN`` code, based on the highest existing numeric suffix.

        ``emp-001`` when no employee rows exist yet. Parsed in Python (not a SQL regex cast) so
        the digit width can grow past 3 without ever mis-ordering lexicographically.
        """
        codes = self.session.execute(select(Employee.employee_code)).scalars().all()
        max_suffix = 0
        for code in codes:
            match = _EMPLOYEE_CODE_RE.match(code)
            if match:
                max_suffix = max(max_suffix, int(match.group(1)))
        return f"emp-{max_suffix + 1:03d}"
