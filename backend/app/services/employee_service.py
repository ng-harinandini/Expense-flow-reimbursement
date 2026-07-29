"""Employee directory service.

Bridges the Cognito identity in a token to the business employee record every claim must belong
to. Cognito stays the identity provider; this service answers "which employee row is that?".
"""

from __future__ import annotations

from typing import Optional, Sequence

from app.core.logging import get_logger
from app.domain.actor import Actor
from app.domain.errors import NotFoundError
from app.domain.validators import require_employee
from app.models.organization import Department, Employee
from app.repositories.employee_repository import DepartmentRepository, EmployeeRepository

logger = get_logger(__name__)


class EmployeeService:
    def __init__(
        self,
        employee_repository: EmployeeRepository,
        department_repository: DepartmentRepository,
    ) -> None:
        self._employees = employee_repository
        self._departments = department_repository

    # --- resolution ----------------------------------------------------------

    def resolve_actor_employee(self, actor: Actor) -> Employee:
        """The employee record behind ``actor``, or 404 with a message that says how to fix it.

        On first successful resolution by ``custom:employeeId``, the Cognito ``sub`` is recorded on
        the row so later lookups work even if the attribute is later removed.
        """
        employee = self._employees.resolve(
            employee_code=actor.employee_code, cognito_sub=actor.sub
        )
        if employee is None:
            raise NotFoundError(
                "Employee",
                actor.employee_code or actor.sub or actor.email or "unknown",
            )

        require_employee(employee, actor.employee_code or "")
        if actor.sub and not employee.cognito_sub:
            self._employees.link_cognito_sub(employee, actor.sub)
            logger.info(
                "employee.cognito_linked",
                extra={"employeeCode": employee.employee_code},
            )
        return employee

    def find_actor_employee(self, actor: Actor) -> Optional[Employee]:
        """Non-raising variant, for read paths that merely scope a query."""
        return self._employees.resolve(
            employee_code=actor.employee_code, cognito_sub=actor.sub
        )

    def get_by_code(self, employee_code: str) -> Employee:
        employee = self._employees.get_by_code(employee_code)
        if employee is None:
            raise NotFoundError("Employee", employee_code)
        return employee

    def find_by_code(self, employee_code: Optional[str]) -> Optional[Employee]:
        return self._employees.get_by_code(employee_code) if employee_code else None

    # --- directory reads -----------------------------------------------------

    def list_active(self, *, limit: Optional[int] = None, offset: int = 0) -> Sequence[Employee]:
        return self._employees.list_active(limit=limit, offset=offset)

    def list_departments(self) -> Sequence[Department]:
        return self._departments.list_active()

    def manager_of(self, employee: Employee) -> Optional[Employee]:
        """The employee's manager — the default first-step approver."""
        return employee.manager
