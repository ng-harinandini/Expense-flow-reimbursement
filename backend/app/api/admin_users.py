"""Admin user-management API — the Admin → FastAPI → boto3 → Cognito onboarding path.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.config import settings
from app.core.deps import (
    VALID_ROLES,
    CurrentUser,
    get_audit_service,
    get_employee_repository,
    get_employee_service,
    get_role_repository,
    get_unit_of_work,
    require_roles,
)
from app.core.unit_of_work import UnitOfWork
from app.domain.actor import Actor
from app.models.enums import AuditAction, AuditEntity, EmployeeGrade
from app.models.organization import Employee
from app.repositories.employee_repository import EmployeeRepository
from app.repositories.role_repository import RoleRepository
from app.schemas.schemas import (
    AdminChangeRoleSchema,
    AdminCreateUserSchema,
    AdminUpdateUserSchema,
    AdminUserListSchema,
    AdminUserSummarySchema,
)
from app.services.audit_service import AuditService
from app.services.cognito import cognito_client
from app.services.employee_service import EmployeeService

ROLE_ATTR = "custom:role_id"
EMPLOYEE_ID_ATTR = "custom:employeeId"

router = APIRouter(prefix="/admin/users", tags=["Admin · Users"])


# --- helpers -----------------------------------------------------------------

def _require_configured() -> None:
    if not settings.cognito_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cognito is not configured.",
        )


def _norm_email(email: str) -> str:
    return email.strip().lower()


def _validate_role(role: str) -> str:
    if role not in VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"role must be one of {sorted(VALID_ROLES)}.",
        )
    return role


def _attrs_to_dict(attrs: List[dict]) -> Dict[str, str]:
    return {a["Name"]: a.get("Value") for a in (attrs or [])}


def _summary(
    username: str,
    attrs: Dict[str, str],
    enabled=None,
    user_status=None,
    employee: Optional[Employee] = None,
) -> AdminUserSummarySchema:
    return AdminUserSummarySchema(
        username=username,
        sub=attrs.get("sub"),
        email=attrs.get("email"),
        role=attrs.get(ROLE_ATTR),
        employeeCode=attrs.get(EMPLOYEE_ID_ATTR),
        enabled=enabled,
        status=user_status,
        employeeId=str(employee.id) if employee else None,
        fullName=employee.full_name if employee else attrs.get("name"),
        grade=employee.grade.value if employee else None,
        managerId=str(employee.manager_id) if employee and employee.manager_id else None,
        managerName=employee.manager_name if employee else None,
        roleId=employee.role_id if employee else None,
        isActive=employee.is_active if employee else None,
    )


def _summary_from_employee(employee: Employee) -> AdminUserSummarySchema:
    """Build a user summary straight from the ``employees`` row, no Cognito round-trip.

    Used by ``get_user``: everything the summary needs (email, role, grade, manager, active state)
    is already persisted here, and the Cognito ``sub``/``username`` line up with ``cognito_sub``.
    """
    return AdminUserSummarySchema(
        sub=employee.cognito_sub,
        email=employee.email,
        role=employee.role_name,
        employeeCode=employee.employee_code,
        employeeId=str(employee.id),
        status=None,
        fullName=employee.full_name,
        grade=employee.grade.value,
        managerId=str(employee.manager_id) if employee.manager_id else None,
        managerName=employee.manager_name,
        roleId=employee.role_id,
        isActive=employee.is_active,
    )


def _handle_client_error(e: ClientError) -> HTTPException:
    code = e.response.get("Error", {}).get("Code", "UnknownError")
    mapping = {
        "UserNotFoundException": (404, "User not found."),
        "UsernameExistsException": (409, "A user with that email already exists."),
        "InvalidParameterException": (400, "Invalid parameter."),
        "InvalidPasswordException": (400, "Password does not meet the pool policy."),
    }
    http_status, detail = mapping.get(code, (502, f"Cognito error: {code}"))
    return HTTPException(status_code=http_status, detail=detail)


def _get_user_raw(client, email: str) -> dict:
    try:
        return client.admin_get_user(UserPoolId=settings.COGNITO_USER_POOL_ID, Username=email)
    except ClientError as e:
        raise _handle_client_error(e)
    except (NoCredentialsError, BotoCoreError) as e:
        raise HTTPException(status_code=502, detail=f"Cognito unreachable: {e}")


def _sub_of(client, email: str) -> Optional[str]:
    attrs = _attrs_to_dict(_get_user_raw(client, email).get("UserAttributes", []))
    return attrs.get("sub")


def _audit_user_change(
    audit: AuditService,
    uow: UnitOfWork,
    admin: CurrentUser,
    *,
    action: AuditAction,
    entity_id: str,
    details: str,
    before: Optional[dict] = None,
    after: Optional[dict] = None,
) -> None:
    """Persist one user-management audit record and commit it.

    Cognito is the system of record for the user itself, so the local transaction contains only
    this audit row — committed here because the Cognito call has already succeeded and must not be
    left unrecorded.
    """
    audit.record(
        actor=Actor.from_current_user(admin),
        action=action,
        entity_type=AuditEntity.USER,
        entity_id=entity_id,
        details=details,
        before=before,
        after=after,
    )
    uow.commit()


@router.post("", status_code=status.HTTP_201_CREATED, response_model=AdminUserSummarySchema)
def create_user(
    payload: AdminCreateUserSchema,
    admin: CurrentUser = Depends(require_roles("admin")),
    audit: AuditService = Depends(get_audit_service),
    uow: UnitOfWork = Depends(get_unit_of_work),
    employee_service: EmployeeService = Depends(get_employee_service),
):
    _require_configured()
    role = _validate_role(payload.role)
    email = _norm_email(payload.email)

    # NotFoundError (unknown managerId) is left to bubble — the global DomainError handler maps it
    # to 404. A bad grade raises a plain ValueError from EmployeeGrade.coerce(), caught here.
    try:
        employee = employee_service.provision_for_admin(
            full_name=payload.name,
            email=email,
            grade=payload.grade,
            role_name=role,
            manager_id=payload.managerId,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    user_attributes = [
        {"Name": "email", "Value": email},
        {"Name": "email_verified", "Value": "true"},
        {"Name": ROLE_ATTR, "Value": role},
        {"Name": EMPLOYEE_ID_ATTR, "Value": employee.employee_code},
        {"Name": "name", "Value": payload.name},
    ]

    client = cognito_client()
    try:
        # No TemporaryPassword: Cognito generates one and emails the invite (FORCE_CHANGE_PASSWORD).
        resp = client.admin_create_user(
            UserPoolId=settings.COGNITO_USER_POOL_ID,
            Username=email,
            UserAttributes=user_attributes,
            DesiredDeliveryMediums=["EMAIL"],
        )
    except ClientError as e:
        raise _handle_client_error(e)
    except (NoCredentialsError, BotoCoreError) as e:
        raise HTTPException(status_code=502, detail=f"Cognito unreachable: {e}")

    user = resp.get("User", {})
    attrs = _attrs_to_dict(user.get("Attributes", []))
    employee.cognito_sub = attrs.get("sub")

    _audit_user_change(
        audit, uow, admin,
        action=AuditAction.USER_CREATE,
        entity_id=attrs.get("sub") or email,
        details=f"Created user {email} with role {role}",
        after={"email": email, "role": role, "employeeId": employee.employee_code},
    )
    return _summary(
        user.get("Username"), attrs, user.get("Enabled"), user.get("UserStatus"), employee=employee,
    )


@router.get("", response_model=AdminUserListSchema)
def list_users(
    limit: int = Query(25, ge=1, le=60),
    nextToken: Optional[str] = None,
    _admin: CurrentUser = Depends(require_roles("admin")),
    employee_repository: EmployeeRepository = Depends(get_employee_repository),
):
    offset = int(nextToken) if nextToken else 0
    employees = employee_repository.list_page(limit=limit, offset=offset)

    users = [_summary_from_employee(employee) for employee in employees]
    next_token = str(offset + limit) if len(employees) == limit else None
    return AdminUserListSchema(users=users, nextToken=next_token)


@router.get("/{email}", response_model=AdminUserSummarySchema)
def get_user(
    email: str,
    _admin: CurrentUser = Depends(require_roles("admin")),
    employee_repository: EmployeeRepository = Depends(get_employee_repository),
):
    """Fetch a user's summary straight from Postgres — no Cognito call.

    The ``employees`` row is the system of record for everything this endpoint returns (role,
    grade, manager, active state); Cognito is only needed for auth-flow operations elsewhere.
    """
    employee = employee_repository.get_by_email(_norm_email(email))
    if employee is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return _summary_from_employee(employee)


@router.patch("/{employeeId}", response_model=AdminUserSummarySchema)
def update_user(
    employeeId: str,
    payload: AdminUpdateUserSchema,
    admin: CurrentUser = Depends(require_roles("admin")),
    audit: AuditService = Depends(get_audit_service),
    uow: UnitOfWork = Depends(get_unit_of_work),
    employee_repository: EmployeeRepository = Depends(get_employee_repository),
    role_repository: RoleRepository = Depends(get_role_repository),
):
    """Edit an employee's directory record in Postgres, keyed by ``employees.id`` (UUID).

    Cognito is only touched when the employee has a linked ``cognito_sub`` and ``role``/``name``
    changed — best-effort sync so a token minted before the next login still carries a role that
    matches Postgres closely, but a Cognito hiccup never blocks the Postgres write.
    """
    employee = employee_repository.get(employeeId)
    if employee is None:
        raise HTTPException(status_code=404, detail="User not found.")

    before = {
        "name": employee.full_name,
        "grade": employee.grade.value,
        "role": employee.role_name,
        "managerId": str(employee.manager_id) if employee.manager_id else None,
        "isActive": employee.is_active,
    }

    changes: dict = {}
    if payload.name is not None:
        changes["full_name"] = payload.name
    if payload.grade is not None:
        try:
            changes["grade"] = EmployeeGrade.coerce(payload.grade)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    new_role_name = None
    if payload.role is not None:
        new_role_name = _validate_role(payload.role)
        role = role_repository.get_by_name(new_role_name)
        if role is None:
            raise RuntimeError(f"Role '{new_role_name}' is not seeded in the roles table.")
        changes["role_id"] = role.id
    if payload.managerId is not None:
        manager_id = payload.managerId or None
        if manager_id:
            manager = employee_repository.get(manager_id)
            if manager is None:
                raise HTTPException(status_code=404, detail="Manager not found.")
            if str(manager.id) == str(employee.id):
                raise HTTPException(status_code=400, detail="An employee cannot manage themself.")
        changes["manager_id"] = manager_id
    if payload.isActive is not None:
        changes["is_active"] = payload.isActive

    if not changes:
        raise HTTPException(status_code=400, detail="No attributes to update.")

    employee_repository.update(employee, **changes)

    if employee.cognito_sub and (payload.name is not None or new_role_name is not None):
        client = cognito_client()
        cognito_updates = []
        if payload.name is not None:
            cognito_updates.append({"Name": "name", "Value": payload.name})
        if new_role_name is not None:
            cognito_updates.append({"Name": ROLE_ATTR, "Value": new_role_name})
        try:
            client.admin_update_user_attributes(
                UserPoolId=settings.COGNITO_USER_POOL_ID,
                Username=employee.email,
                UserAttributes=cognito_updates,
            )
        except (ClientError, NoCredentialsError, BotoCoreError):
            pass

    _audit_user_change(
        audit, uow, admin,
        action=AuditAction.USER_UPDATE,
        entity_id=employee.cognito_sub or employee.email,
        details=f"Updated employee {employee.employee_code}",
        before=before,
        after={
            "name": employee.full_name,
            "grade": employee.grade.value,
            "role": employee.role_name,
            "managerId": str(employee.manager_id) if employee.manager_id else None,
            "isActive": employee.is_active,
        },
    )
    return _summary_from_employee(employee)


@router.delete("/{employeeId}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    employeeId: str,
    admin: CurrentUser = Depends(require_roles("admin")),
    audit: AuditService = Depends(get_audit_service),
    uow: UnitOfWork = Depends(get_unit_of_work),
    employee_repository: EmployeeRepository = Depends(get_employee_repository),
):
    """Deactivate an employee, keyed by ``employees.id`` (UUID).

    Not a hard delete: ``claims.employee_id`` is ``ON DELETE RESTRICT``, so a claim history would
    block it anyway, and losing the row would break audit trails and manager-chain history. Setting
    ``is_active`` false is the directory's actual "remove" — direct reports become unmanaged
    (``manager_id`` is ``ON DELETE SET NULL``), same as before this endpoint existed.
    """
    employee = employee_repository.get(employeeId)
    if employee is None:
        raise HTTPException(status_code=404, detail="User not found.")

    employee_repository.update(employee, is_active=False)

    _audit_user_change(
        audit, uow, admin,
        action=AuditAction.USER_DISABLE,
        entity_id=employee.cognito_sub or employee.email,
        details=f"Deactivated employee {employee.employee_code}",
        before={"isActive": True},
        after={"isActive": False},
    )


@router.post("/{email}/role", response_model=AdminUserSummarySchema)
def change_role(email: str, payload: AdminChangeRoleSchema,
                admin: CurrentUser = Depends(require_roles("admin")),
                audit: AuditService = Depends(get_audit_service),
                uow: UnitOfWork = Depends(get_unit_of_work)):
    _require_configured()
    role = _validate_role(payload.role)
    email = _norm_email(email)

    client = cognito_client()
    # Read the current role first so the audit record carries a real before/after.
    previous_role = _attrs_to_dict(_get_user_raw(client, email).get("UserAttributes", [])).get(
        ROLE_ATTR
    )
    try:
        client.admin_update_user_attributes(
            UserPoolId=settings.COGNITO_USER_POOL_ID,
            Username=email,
            UserAttributes=[{"Name": ROLE_ATTR, "Value": role}],
        )
    except ClientError as e:
        raise _handle_client_error(e)
    except (NoCredentialsError, BotoCoreError) as e:
        raise HTTPException(status_code=502, detail=f"Cognito unreachable: {e}")

    resp = _get_user_raw(client, email)
    attrs = _attrs_to_dict(resp.get("UserAttributes", []))
    _audit_user_change(
        audit, uow, admin,
        action=AuditAction.USER_ROLE_CHANGE,
        entity_id=attrs.get("sub") or email,
        details=f"Set custom:role_id={role} for {email}",
        before={"role": previous_role},
        after={"role": role},
    )
    return _summary(resp.get("Username"), attrs, resp.get("Enabled"), resp.get("UserStatus"))


@router.post("/{email}/enable", response_model=AdminUserSummarySchema)
def enable_user(email: str, admin: CurrentUser = Depends(require_roles("admin")),
                audit: AuditService = Depends(get_audit_service),
                uow: UnitOfWork = Depends(get_unit_of_work)):
    return _set_enabled(email, True, admin, audit, uow)


@router.post("/{email}/disable", response_model=AdminUserSummarySchema)
def disable_user(email: str, admin: CurrentUser = Depends(require_roles("admin")),
                 audit: AuditService = Depends(get_audit_service),
                 uow: UnitOfWork = Depends(get_unit_of_work)):
    return _set_enabled(email, False, admin, audit, uow)


def _set_enabled(email: str, enabled: bool, admin: CurrentUser,
                 audit: AuditService, uow: UnitOfWork) -> AdminUserSummarySchema:
    _require_configured()
    email = _norm_email(email)
    client = cognito_client()
    op = client.admin_enable_user if enabled else client.admin_disable_user
    try:
        op(UserPoolId=settings.COGNITO_USER_POOL_ID, Username=email)
    except ClientError as e:
        raise _handle_client_error(e)
    except (NoCredentialsError, BotoCoreError) as e:
        raise HTTPException(status_code=502, detail=f"Cognito unreachable: {e}")

    resp = _get_user_raw(client, email)
    attrs = _attrs_to_dict(resp.get("UserAttributes", []))
    _audit_user_change(
        audit, uow, admin,
        action=AuditAction.USER_ENABLE if enabled else AuditAction.USER_DISABLE,
        entity_id=attrs.get("sub") or email,
        details=f"{'Enabled' if enabled else 'Disabled'} user {email}",
        before={"enabled": not enabled},
        after={"enabled": enabled},
    )
    return _summary(resp.get("Username"), attrs, resp.get("Enabled"), resp.get("UserStatus"))
