"""Shared test fixtures.

**Database strategy.** Integration and repository tests run against a *real* PostgreSQL — the same
engine production uses — because the things Phase 1 relies on (native enums, JSONB, check
constraints, the transition-guard and audit-immutability triggers, ``nextval``) do not exist in
SQLite and would be untested by a stub.

A dedicated database (``TEST_DATABASE_URL``, else the configured database name + ``_test``) is
created if absent and migrated with ``alembic upgrade head``. The owner's development database is
never touched: nothing here drops, truncates, or writes to it, per the ADR-001 DB-safety policy.

**Isolation.** Each DB test runs inside a transaction that is rolled back afterwards, so tests
neither see nor leave each other's rows and the schema is migrated once per session.

**Offline.** If no PostgreSQL is reachable, DB-backed tests *skip* with a clear reason; the pure
unit tests (state machine, validators, mappers, token verification) still run.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterator, Optional

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core import deps
from app.core.config import settings
from app.core.database import get_db
from app.core.deps import CurrentUser
from app.domain.actor import Actor
from app.main import app
from app.models.claim import Claim
from app.models.enums import ClaimStatus, EmployeeGrade
from app.models.organization import Employee
from app.models.role import Role

# Seeded reference data from migration 0003 (deterministic across environments).
SEED_EMPLOYEE_CODE = "emp-101"
SEED_MANAGER_CODE = "emp-100"
SEED_OTHER_EMPLOYEE_CODE = "emp-104"


# --- database plumbing -------------------------------------------------------


def _test_database_url() -> Optional[str]:
    """Resolve the URL of the dedicated test database, or ``None`` if not configured."""
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit

    base = settings.database_url
    if not base:
        return None

    url = make_url(base)
    name = url.database or "postgres"
    if not name.endswith("_test"):
        url = url.set(database=f"{name}_test")
    # ``str(URL)`` masks the password with '***'; rendering must keep it usable.
    return url.render_as_string(hide_password=False)


def _ensure_database(url: str) -> None:
    """Create the test database if it does not exist. Never drops an existing one."""
    target = make_url(url)
    admin_url = target.set(database="postgres").render_as_string(hide_password=False)
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": target.database},
            ).scalar()
            if not exists:
                # Identifier cannot be parameterized; it comes from config, not user input.
                connection.execute(text(f'CREATE DATABASE "{target.database}"'))
    finally:
        admin.dispose()


def alembic_config():
    """The project's real Alembic config, so tests migrate exactly like production does.

    The URL is *not* passed via ``set_main_option``: ``alembic/env.py`` reads it from app settings
    (which the ``db_engine`` fixture points at the test database), and ConfigParser would try to
    interpolate a percent-encoded password.
    """
    from alembic.config import Config

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config = Config(os.path.join(backend_dir, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(backend_dir, "alembic"))
    return config


@pytest.fixture(scope="session", autouse=True)
def redirect_database_to_test_db() -> Iterator[Optional[str]]:
    """Point the application at the test database for the entire session.

    **Autouse and session-scoped on purpose.** Without it, any test that touches a route without
    overriding ``get_db`` would build an engine from the developer's real ``DATABASE_URL`` and write
    to their working database. Redirecting the setting once — and clearing the lazily built engine
    cache on both sides — makes that impossible rather than merely unlikely.
    """
    from app.core import database as database_module

    url = _test_database_url()
    original = settings.DATABASE_URL
    settings.DATABASE_URL = url
    database_module._engine = None
    database_module._SessionLocal = None
    try:
        yield url
    finally:
        settings.DATABASE_URL = original
        database_module._engine = None
        database_module._SessionLocal = None


@pytest.fixture(scope="session")
def db_engine(redirect_database_to_test_db: Optional[str]) -> Iterator[Engine]:
    """Session-scoped engine bound to the migrated test database."""
    from alembic import command

    url = redirect_database_to_test_db
    if not url:
        pytest.skip("No database configured (set DATABASE_URL/DB_* or TEST_DATABASE_URL).")

    try:
        _ensure_database(url)
        command.upgrade(alembic_config(), "head")
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL unavailable for integration tests: {exc}")

    engine = create_engine(url, future=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Iterator[Session]:
    """A session wrapped in a transaction that is rolled back after the test.

    The session joins an outer transaction on a single connection, so anything the code under test
    commits stays inside it and disappears on rollback — full isolation without truncating tables.
    """
    connection = db_engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(
        bind=connection, autoflush=False, autocommit=False, expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    session = factory()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


# --- reference data (roles are seeded by migration; employees are not) -------


def _role(db_session: Session, name: str) -> Role:
    return db_session.query(Role).filter_by(name=name).one()


def _make_employee(
    db_session: Session,
    *,
    code: str,
    name: str,
    grade: EmployeeGrade,
    role_name: str,
    manager_id: Optional[uuid.UUID] = None,
) -> Employee:
    employee = Employee(
        employee_code=code,
        full_name=name,
        email=f"{code}@test.local",
        grade=grade,
        role_id=_role(db_session, role_name).id,
        manager_id=manager_id,
        is_active=True,
    )
    db_session.add(employee)
    db_session.flush()
    return employee


@pytest.fixture
def role_id(db_session: Session) -> int:
    """The seeded 'employee' role's id — roles survive the migration, unlike seed employees."""
    return _role(db_session, "employee").id


@pytest.fixture
def manager_employee(db_session: Session) -> Employee:
    """A manager-role employee, created fresh per test (no seed employees ship anymore)."""
    return _make_employee(
        db_session, code=SEED_MANAGER_CODE, name="Test Manager",
        grade=EmployeeGrade.L5, role_name="manager",
    )


@pytest.fixture
def employee(db_session: Session, manager_employee: Employee) -> Employee:
    """The default claim owner, reporting to ``manager_employee``."""
    return _make_employee(
        db_session, code=SEED_EMPLOYEE_CODE, name="Test Employee",
        grade=EmployeeGrade.L3, role_name="employee", manager_id=manager_employee.id,
    )


@pytest.fixture
def other_employee(db_session: Session) -> Employee:
    """A different employee, for ownership-boundary tests."""
    return _make_employee(
        db_session, code=SEED_OTHER_EMPLOYEE_CODE, name="Other Employee",
        grade=EmployeeGrade.L1, role_name="employee",
    )


# --- actors ------------------------------------------------------------------


def make_actor(role: str, *, employee_code: Optional[str] = None,
               email: Optional[str] = None, sub: Optional[str] = None) -> Actor:
    return Actor(
        role=role,
        sub=sub or f"sub-{role}",
        email=email or f"{role}@corp.com",
        employee_code=employee_code,
    )


@pytest.fixture
def employee_actor() -> Actor:
    return make_actor("employee", employee_code=SEED_EMPLOYEE_CODE)


@pytest.fixture
def manager_actor() -> Actor:
    return make_actor("manager", employee_code=SEED_MANAGER_CODE)


@pytest.fixture
def finance_actor() -> Actor:
    return make_actor("finance")


@pytest.fixture
def admin_actor() -> Actor:
    return make_actor("admin")


# --- repositories / services -------------------------------------------------


@pytest.fixture
def repositories(db_session: Session) -> dict:
    """All repositories sharing the test session."""
    from app.repositories import (
        AIInferenceRepository,
        ApprovalWorkflowRepository,
        AuditLogRepository,
        ClaimRepository,
        EmployeeRepository,
        FraudResultRepository,
        PolicyRuleRepository,
        ReceiptRepository,
        RoleRepository,
    )

    return {
        "claims": ClaimRepository(db_session),
        "employees": EmployeeRepository(db_session),
        "roles": RoleRepository(db_session),
        "policy_rules": PolicyRuleRepository(db_session),
        "audit": AuditLogRepository(db_session),
        "fraud": FraudResultRepository(db_session),
        "workflows": ApprovalWorkflowRepository(db_session),
        "receipts": ReceiptRepository(db_session),
        "ai": AIInferenceRepository(db_session),
    }


@pytest.fixture
def claim_service(repositories: dict):
    """A fully wired ``ClaimService``, mirroring the production dependency graph."""
    from app.services.audit_service import AuditService
    from app.services.claim_service import ClaimService
    from app.services.employee_service import EmployeeService
    from app.services.policy_rule_service import PolicyRuleService

    audit = AuditService(repositories["audit"])
    employees = EmployeeService(repositories["employees"], repositories["roles"])
    policies = PolicyRuleService(repositories["policy_rules"], audit)

    return ClaimService(
        claim_repository=repositories["claims"],
        fraud_repository=repositories["fraud"],
        workflow_repository=repositories["workflows"],
        receipt_repository=repositories["receipts"],
        employee_service=employees,
        policy_rule_service=policies,
        audit_service=audit,
    )


@pytest.fixture
def audit_service(repositories: dict):
    from app.services.audit_service import AuditService

    return AuditService(repositories["audit"])


@pytest.fixture
def policy_rule_service(repositories: dict, audit_service):
    from app.services.policy_rule_service import PolicyRuleService

    return PolicyRuleService(repositories["policy_rules"], audit_service)


# --- claim factory -----------------------------------------------------------


@pytest.fixture
def claim_payload():
    """A minimal, valid ``POST /claims`` body. Override any key per test."""

    def _payload(**overrides) -> dict:
        payload = {
            "category": "Meals",
            "subCategory": "Team Lunch",
            "amount": Decimal("22.50"),
            "amountUSD": Decimal("22.50"),
            "currency": "USD",
            "merchantVendor": f"Test Vendor {uuid.uuid4().hex[:8]}",
            "expenseDate": date.today() - timedelta(days=1),
            "purposeDescription": "Team sync lunch.",
            "receiptAttached": True,
        }
        payload.update(overrides)
        return payload

    return _payload


@pytest.fixture
def make_claim(db_session: Session, employee: Employee):
    """Insert a claim directly in a chosen state, bypassing the submission pipeline.

    For tests about a *later* stage of the lifecycle. It uses the repository's guarded transition
    for each hop, so the claim's history stays valid — it does not forge a status.
    """
    from app.repositories.claim_repository import ClaimRepository

    repository = ClaimRepository(db_session)

    # Ordered path from Draft to each reachable state.
    routes: dict[ClaimStatus, list[tuple[ClaimStatus, str]]] = {
        ClaimStatus.DRAFT: [],
        ClaimStatus.SUBMITTED: [(ClaimStatus.SUBMITTED, "employee")],
        ClaimStatus.PROCESSING: [
            (ClaimStatus.SUBMITTED, "employee"),
            (ClaimStatus.PROCESSING, "system"),
        ],
        ClaimStatus.AUTO_APPROVED: [
            (ClaimStatus.SUBMITTED, "employee"),
            (ClaimStatus.PROCESSING, "system"),
            (ClaimStatus.AUTO_APPROVED, "system"),
        ],
        ClaimStatus.MANAGER_REVIEW: [
            (ClaimStatus.SUBMITTED, "employee"),
            (ClaimStatus.PROCESSING, "system"),
            (ClaimStatus.MANAGER_REVIEW, "system"),
        ],
        ClaimStatus.FINANCE_REVIEW: [
            (ClaimStatus.SUBMITTED, "employee"),
            (ClaimStatus.PROCESSING, "system"),
            (ClaimStatus.MANAGER_REVIEW, "system"),
            (ClaimStatus.FINANCE_REVIEW, "manager"),
        ],
        ClaimStatus.APPROVED: [
            (ClaimStatus.SUBMITTED, "employee"),
            (ClaimStatus.PROCESSING, "system"),
            (ClaimStatus.MANAGER_REVIEW, "system"),
            (ClaimStatus.FINANCE_REVIEW, "manager"),
            (ClaimStatus.APPROVED, "finance"),
        ],
        ClaimStatus.REJECTED: [
            (ClaimStatus.SUBMITTED, "employee"),
            (ClaimStatus.PROCESSING, "system"),
            (ClaimStatus.MANAGER_REVIEW, "system"),
            (ClaimStatus.REJECTED, "manager"),
        ],
        ClaimStatus.FLAGGED_FRAUD: [
            (ClaimStatus.SUBMITTED, "employee"),
            (ClaimStatus.PROCESSING, "system"),
            (ClaimStatus.FLAGGED_FRAUD, "system"),
        ],
        ClaimStatus.REIMBURSED: [
            (ClaimStatus.SUBMITTED, "employee"),
            (ClaimStatus.PROCESSING, "system"),
            (ClaimStatus.MANAGER_REVIEW, "system"),
            (ClaimStatus.FINANCE_REVIEW, "manager"),
            (ClaimStatus.APPROVED, "finance"),
            (ClaimStatus.REIMBURSED, "finance"),
        ],
    }

    def _make(
        status: ClaimStatus = ClaimStatus.DRAFT,
        *,
        owner: Optional[Employee] = None,
        amount: Decimal = Decimal("42.00"),
        category: str = "Meals",
        vendor: Optional[str] = None,
        expense_date: Optional[date] = None,
        **columns,
    ) -> Claim:
        subject = owner or employee
        claim = Claim(
            claim_number=repository.next_claim_number(),
            employee_id=subject.id,
            employee_grade=subject.grade or EmployeeGrade.L3,
            expense_date=expense_date or (date.today() - timedelta(days=2)),
            category=category,
            sub_category="Test",
            amount=amount,
            currency="USD",
            amount_usd=amount,
            merchant_vendor=vendor or f"Vendor {uuid.uuid4().hex[:8]}",
            purpose_description="Fixture claim.",
            receipt_attached=True,
            **columns,
        )
        repository.create_claim(claim, actor_sub="sub-fixture", actor_name="Fixture",
                                actor_role="employee")

        for target, role in routes[status]:
            repository.transition_status(
                claim, target, actor_role=role, actor_sub=f"sub-{role}", actor_name=role,
            )
        return claim

    return _make


# --- HTTP client -------------------------------------------------------------


def as_role(role: str, *, employee_id: Optional[str] = None, email: Optional[str] = None) -> None:
    """Override authentication so the next request runs as ``role``."""
    app.dependency_overrides[deps.get_current_user] = lambda: CurrentUser(
        sub=f"sub-{role}", email=email or f"{role}@corp.com", role=role,
        employee_id=employee_id, claims={},
    )


@pytest.fixture
def client(
    db_session: Session, employee: Employee, other_employee: Employee
) -> Iterator[TestClient]:
    """``TestClient`` whose requests share the test transaction.

    ``get_db`` is overridden with the rolled-back session, so API tests exercise the real
    repository/service graph and commit paths without persisting anything. Depends on ``employee``/
    ``other_employee`` (and transitively ``manager_employee``) so ``SEED_EMPLOYEE_CODE`` /
    ``SEED_MANAGER_CODE`` / ``SEED_OTHER_EMPLOYEE_CODE`` always resolve to a real row for every API
    test, the same guarantee migration-seeded employees used to provide unconditionally.
    """
    app.dependency_overrides[get_db] = lambda: db_session
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def anon_client() -> Iterator[TestClient]:
    """Client with no database override — for auth/gating tests that never reach the DB."""
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()
