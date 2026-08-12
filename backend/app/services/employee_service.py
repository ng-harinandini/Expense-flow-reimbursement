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
from app.models.enums import EmployeeGrade
from app.models.organization import Employee
from app.repositories.employee_repository import EmployeeRepository
from app.repositories.role_repository import RoleRepository

logger = get_logger(__name__)


class EmployeeService:
    def __init__(
        self,
        employee_repository: EmployeeRepository,
        role_repository: RoleRepository,
    ) -> None:
        self._employees = employee_repository
        self._roles = role_repository

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

    def manager_of(self, employee: Employee) -> Optional[Employee]:
        """The employee's manager — the default first-step approver."""
        return employee.manager

    def list_direct_reports(self, manager: Employee) -> Sequence[Employee]:
        """Every employee whose ``manager_id`` points at ``manager`` — one level, not the whole
        reporting chain beneath them."""
        return self._employees.list_direct_reports(manager.id)

    def provision_for_admin(
        self,
        *,
        full_name: str,
        email: str,
        grade: str,
        role_name: str,
        manager_id: Optional[str] = None,
    ) -> Employee:
        """Build (flush, not commit) the Postgres ``Employee`` row for a new admin-created user.

        Called before the Cognito account is created, so its generated ``employee_code`` can be
        used as the ``custom:employeeId`` attribute. The caller (the route) owns the transaction —
        if the subsequent Cognito call fails, the request-scoped session rolls back and this insert
        is discarded automatically.
        """
        manager = None
        if manager_id:
            manager = self._employees.get(manager_id)
            if manager is None:
                raise NotFoundError("Employee", manager_id)

        role = self._roles.get_by_name(role_name)
        if role is None:
            # Roles are seeded 1:1 with VALID_ROLES; a miss here is a data-integrity bug.
            raise RuntimeError(f"Role '{role_name}' is not seeded in the roles table.")

        employee = Employee(
            employee_code=self._employees.next_employee_code(),
            full_name=full_name,
            email=email.strip().lower(),
            grade=EmployeeGrade.coerce(grade),
            role_id=role.id,
            manager_id=manager.id if manager else None,
            is_active=True,
        )
        return self._employees.add(employee)
