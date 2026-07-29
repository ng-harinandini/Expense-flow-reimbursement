"""seed reference data: departments, employees, policy rules

Data migration that moves the previously hardcoded Python constants out of
``app/services/store.py`` and into the database:

  * ``INITIAL_EMPLOYEES``      -> ``departments`` + ``employees``
  * ``DEFAULT_POLICY_RULES``   -> ``policy_rules`` (version 1, effective 2026-01-01)

The employee rows are *not* decorative: the deployed Cognito users carry
``custom:employeeId`` values (``emp-101`` …) that must resolve to a row, otherwise claim
submission has no owner to attach to.

Idempotency: every insert is ``ON CONFLICT DO NOTHING`` against the natural key, so re-running is
safe and an operator's own rows are never overwritten.

Safety on downgrade: seeded employees/departments are removed **only** when nothing references
them (``WHERE NOT EXISTS``), so business data is never cascade-deleted. Policy rules are removed by
code and version.

Revision ID: 0003_seed_reference_data
Revises: 0002_phase1_core_domain
Create Date: 2026-07-28
"""
import json
import uuid
from datetime import date
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_seed_reference_data"
down_revision: Union[str, None] = "0002_phase1_core_domain"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Deterministic UUIDs: the same logical row always gets the same id, in every environment,
# which keeps this migration re-runnable and makes fixtures predictable.
SEED_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


def _seed_id(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, f"expenseflow:{kind}:{key}")


DEPARTMENTS: tuple[tuple[str, str, str], ...] = (
    # (code, name, cost_center)
    ("ENG", "Engineering", "CC-1001"),
    ("PM", "Product Management", "CC-1002"),
    ("CS", "Customer Support", "CC-1003"),
)

EMPLOYEES: tuple[dict, ...] = (
    {
        "code": "emp-100",
        "name": "Marcus Vance",
        "email": "marcus.v@enterprise.com",
        "grade": "L5",
        "department": "ENG",
        "manager": None,
        "notes": None,
    },
    {
        "code": "emp-101",
        "name": "Sarah Jenkins",
        "email": "sarah.j@enterprise.com",
        "grade": "L3",
        "department": "ENG",
        "manager": "emp-100",
        "notes": None,
    },
    {
        "code": "emp-103",
        "name": "Elena Rostova",
        "email": "elena.r@enterprise.com",
        "grade": "Director",
        "department": "PM",
        "manager": None,
        # The original data recorded a title, not a person; preserved rather than invented.
        "notes": "Reports to VP Global Products.",
    },
    {
        "code": "emp-102",
        "name": "David Chen",
        "email": "david.c@enterprise.com",
        "grade": "L5",
        "department": "PM",
        "manager": "emp-103",
        "notes": None,
    },
    {
        "code": "emp-104",
        "name": "Michael Chang",
        "email": "michael.c@enterprise.com",
        "grade": "L1",
        "department": "CS",
        "manager": "emp-101",
        "notes": None,
    },
)

POLICY_EFFECTIVE_FROM = date(2026, 1, 1)

# Typed columns drive the existing category engine; conditions/actions are stored for the
# Phase 2+ declarative evaluator (not read yet).
POLICY_RULES: tuple[dict, ...] = (
    {
        "code": "MEALS_STANDARD",
        "name": "Meals — Daily Cap",
        "category": "Meals",
        "grade_tier": "All Staff",
        "expense_limit": "40.00",
        "auto_approve_limit": "25.00",
        "receipt_required_above": "25.00",
        "requires_pre_approval": False,
        "priority": 100,
        "description": "Daily meal allowance with itemised-receipt threshold; alcohol excluded.",
        "special_rules": [
            "Maximum $40.00 per day for meals.",
            "Itemized receipt required above $25.00.",
            "Alcohol is NOT reimbursable under Meals.",
        ],
        "conditions": {"category": "Meals", "gradeTiers": ["*"], "amountUsd": {"lte": 40.0}},
        "actions": {
            "autoApproveBelowUsd": 25.0,
            "requireReceiptAboveUsd": 25.0,
            "routeOnBreach": "Manager_Review",
            "prohibitedItems": ["alcohol"],
        },
    },
    {
        "code": "GROUND_TRANSPORT_STANDARD",
        "name": "Ground Transport — Per Trip Cap",
        "category": "Ground Transport",
        "grade_tier": "All Staff",
        "expense_limit": "150.00",
        "auto_approve_limit": "50.00",
        "receipt_required_above": "0.00",
        "requires_pre_approval": False,
        "priority": 100,
        "description": "Taxi/rideshare per-trip cap; receipt always required.",
        "special_rules": [
            "Taxi / Rideshare max $150 per trip.",
            "Itemized receipt ALWAYS required regardless of amount.",
            "Personal mileage reimbursed at $0.67/mile.",
        ],
        "conditions": {
            "category": "Ground Transport",
            "gradeTiers": ["*"],
            "amountUsd": {"lte": 150.0},
        },
        "actions": {
            "autoApproveBelowUsd": 50.0,
            "requireReceiptAboveUsd": 0.0,
            "routeOnBreach": "Manager_Review",
            "mileageRateUsdPerMile": 0.67,
        },
    },
    {
        "code": "FLIGHTS_STANDARD",
        "name": "Flights — Always Manual",
        "category": "Flights",
        "grade_tier": "Grade Dependent",
        "expense_limit": "10000.00",
        "auto_approve_limit": None,
        "receipt_required_above": "0.00",
        "requires_pre_approval": True,
        "priority": 90,
        "description": "Air travel: pre-approval mandatory, never auto-approved, class by grade.",
        "special_rules": [
            "Flights are NEVER auto-approved. Mandatory Manager approval.",
            "Economy class mandatory for L1-L4.",
            "Premium Economy permitted for L5/Director on >6hr flights.",
        ],
        "conditions": {"category": "Flights", "gradeTiers": ["*"], "amountUsd": {"lte": 10000.0}},
        "actions": {
            "autoApprove": False,
            "requirePreApproval": True,
            "routeOnBreach": "Manager_Review",
            "cabinClassByGrade": {
                "L1": "Economy",
                "L2": "Economy",
                "L3": "Economy",
                "L4": "Economy",
                "L5": "Premium Economy >6h",
                "Director": "Premium Economy >6h",
                "VP": "Business >6h",
            },
        },
    },
    {
        "code": "LODGING_STANDARD",
        "name": "Lodging — Nightly Cap by Grade",
        "category": "Lodging",
        "grade_tier": "Grade Dependent",
        "expense_limit": "250.00",
        "auto_approve_limit": None,
        "receipt_required_above": "0.00",
        "requires_pre_approval": False,
        "priority": 95,
        "description": "Nightly ceiling depends on grade; always manager-reviewed.",
        "special_rules": [
            "L1-L3 capped at $120/night. L4+ capped at $250/night.",
            "Itemized folio showing zero balance required.",
            "Lodging always requires manager approval.",
        ],
        "conditions": {"category": "Lodging", "gradeTiers": ["*"], "amountUsd": {"lte": 250.0}},
        "actions": {
            "autoApprove": False,
            "routeOnBreach": "Manager_Review",
            "nightlyCapUsdByGrade": {
                "L1": 120.0,
                "L2": 120.0,
                "L3": 120.0,
                "L4": 250.0,
                "L5": 250.0,
                "Director": 250.0,
                "VP": 250.0,
            },
        },
    },
    {
        "code": "CLIENT_ENTERTAINMENT_STANDARD",
        "name": "Client Entertainment — Manager+ Only",
        "category": "Client Entertainment",
        "grade_tier": "Manager+ (L5+)",
        "expense_limit": "500.00",
        "auto_approve_limit": None,
        "receipt_required_above": "50.00",
        "requires_pre_approval": True,
        "priority": 90,
        "description": "Restricted to L5+; per-event cap; attendee listing mandatory.",
        "special_rules": [
            "Restricted to Grade L5+ (Manager / Director / VP).",
            "Itemized list of all internal and external attendees required.",
            "Capped at $500 per event.",
        ],
        "conditions": {
            "category": "Client Entertainment",
            "gradeTiers": ["L5", "Director", "VP"],
            "amountUsd": {"lte": 500.0},
        },
        "actions": {
            "autoApprove": False,
            "requirePreApproval": True,
            "requireAttendees": True,
            "requireReceiptAboveUsd": 50.0,
            "routeOnBreach": "Manager_Review",
        },
    },
)


def upgrade() -> None:
    bind = op.get_bind()

    # --- departments ---
    for code, name, cost_center in DEPARTMENTS:
        bind.execute(
            sa.text(
                """
                INSERT INTO departments (id, code, name, cost_center, is_active)
                VALUES (:id, :code, :name, :cost_center, true)
                ON CONFLICT (code) DO NOTHING
                """
            ),
            {
                "id": _seed_id("department", code),
                "code": code,
                "name": name,
                "cost_center": cost_center,
            },
        )

    # --- employees (managers inserted before their reports; see EMPLOYEES ordering) ---
    for row in EMPLOYEES:
        bind.execute(
            sa.text(
                """
                INSERT INTO employees (
                    id, employee_code, full_name, email, grade,
                    department_id, manager_id, is_active, notes
                )
                VALUES (
                    :id, :code, :name, :email, CAST(:grade AS employee_grade),
                    :department_id, :manager_id, true, :notes
                )
                ON CONFLICT (employee_code) DO NOTHING
                """
            ),
            {
                "id": _seed_id("employee", row["code"]),
                "code": row["code"],
                "name": row["name"],
                "email": row["email"],
                "grade": row["grade"],
                "department_id": _seed_id("department", row["department"]),
                "manager_id": _seed_id("employee", row["manager"]) if row["manager"] else None,
                "notes": row["notes"],
            },
        )

    # --- policy rules ---
    for rule in POLICY_RULES:
        bind.execute(
            sa.text(
                """
                INSERT INTO policy_rules (
                    id, code, version, name, description, category, country, currency,
                    grade_tier, expense_limit, auto_approve_limit, receipt_required_above,
                    requires_pre_approval, effective_date, expiration_date, priority, is_active,
                    conditions, actions, special_rules
                )
                VALUES (
                    :id, :code, 1, :name, :description, :category, NULL, 'USD',
                    :grade_tier, :expense_limit, :auto_approve_limit, :receipt_required_above,
                    :requires_pre_approval, :effective_date, NULL, :priority, true,
                    CAST(:conditions AS jsonb), CAST(:actions AS jsonb),
                    CAST(:special_rules AS jsonb)
                )
                ON CONFLICT (code, version) DO NOTHING
                """
            ),
            {
                "id": _seed_id("policy_rule", f"{rule['code']}:1"),
                "code": rule["code"],
                "name": rule["name"],
                "description": rule["description"],
                "category": rule["category"],
                "grade_tier": rule["grade_tier"],
                "expense_limit": rule["expense_limit"],
                "auto_approve_limit": rule["auto_approve_limit"],
                "receipt_required_above": rule["receipt_required_above"],
                "requires_pre_approval": rule["requires_pre_approval"],
                "effective_date": POLICY_EFFECTIVE_FROM,
                "priority": rule["priority"],
                "conditions": json.dumps(rule["conditions"]),
                "actions": json.dumps(rule["actions"]),
                "special_rules": json.dumps(rule["special_rules"]),
            },
        )

    # Link any receipt rows that were uploaded before the employees table existed.
    bind.execute(
        sa.text(
            """
            UPDATE receipts r
               SET employee_ref_id = e.id
              FROM employees e
             WHERE r.employee_ref_id IS NULL
               AND r.employee_id IS NOT NULL
               AND e.employee_code = r.employee_id
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()

    for rule in POLICY_RULES:
        bind.execute(
            sa.text("DELETE FROM policy_rules WHERE code = :code AND version = 1"),
            {"code": rule["code"]},
        )

    # Detach receipts first so the employee rows are no longer referenced.
    codes = [row["code"] for row in EMPLOYEES]
    bind.execute(
        sa.text(
            """
            UPDATE receipts
               SET employee_ref_id = NULL
             WHERE employee_ref_id IN (
                   SELECT id FROM employees WHERE employee_code = ANY(:codes))
            """
        ),
        {"codes": codes},
    )

    # Reports before managers (self-FK), and only when nothing references the row.
    for code in reversed(codes):
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

    for code, _name, _cc in DEPARTMENTS:
        bind.execute(
            sa.text(
                """
                DELETE FROM departments d
                 WHERE d.code = :code
                   AND NOT EXISTS (SELECT 1 FROM employees e WHERE e.department_id = d.id)
                   AND NOT EXISTS (SELECT 1 FROM claims c WHERE c.department_id = d.id)
                """
            ),
            {"code": code},
        )
