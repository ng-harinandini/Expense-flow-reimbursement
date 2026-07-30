"""Migration tests.

These guard the things that silently rot: a second head appearing, models drifting away from the
migrations that are supposed to create them, a downgrade path that was never run, and the seed data
the deployed Cognito users depend on.

The downgrade round-trip runs on a **throwaway database** created and dropped by the test, never on
the shared test database (and never on the developer's), so a failure cannot destroy anything.
"""

from __future__ import annotations

import uuid

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.core.config import settings
from tests.conftest import alembic_config

EXPECTED_TABLES = {
    "ai_inference_logs",
    "approval_steps",
    "approval_workflows",
    "attachments",
    "audit_logs",
    "claim_status_history",
    "claims",
    "comments",
    "departments",
    "employees",
    "fraud_results",
    "policy_rules",
    "receipts",
    "receipt_fields",
    "receipt_line_items",
}

EXPECTED_ENUMS = {
    "ai_inference_status",
    "approval_step_status",
    "approval_workflow_status",
    "attachment_kind",
    "claim_status",
    "employee_grade",
    "extraction_status",
    "fraud_risk_level",
}


# --- revision graph ----------------------------------------------------------


def test_single_migration_head():
    """Two heads mean an unmerged branch and a broken ``alembic upgrade head``."""
    heads = ScriptDirectory.from_config(alembic_config()).get_heads()
    assert len(heads) == 1, f"expected one head, found {heads}"


def test_revision_chain_is_linear_and_ordered():
    """Newest-first walk of the whole chain.

    Deliberately a hard-coded list rather than a computed one: adding a revision should require
    updating this test, which is how a reviewer is forced to notice a new migration and confirm its
    place in the order.
    """
    script = ScriptDirectory.from_config(alembic_config())
    revisions = list(script.walk_revisions())
    assert [r.revision for r in revisions] == [
        "0005_duplicate_detection",
        "0004_ai_knowledge_platform",
        "0003_seed_reference_data",
        "0002_phase1_core_domain",
        "0001_initial_receipts",
    ]


def test_every_revision_defines_a_downgrade():
    """A migration you cannot reverse is a migration you cannot safely deploy."""
    script = ScriptDirectory.from_config(alembic_config())
    for revision in script.walk_revisions():
        module = revision.module
        assert hasattr(module, "downgrade"), f"{revision.revision} has no downgrade()"


# --- applied schema ----------------------------------------------------------


def test_all_expected_tables_exist(db_engine):
    actual = set(inspect(db_engine).get_table_names())
    assert EXPECTED_TABLES <= actual, f"missing: {EXPECTED_TABLES - actual}"


def test_all_expected_enum_types_exist(db_engine):
    with db_engine.connect() as connection:
        actual = {
            row[0]
            for row in connection.execute(
                text("SELECT typname FROM pg_type WHERE typtype = 'e'")
            )
        }
    assert EXPECTED_ENUMS <= actual, f"missing: {EXPECTED_ENUMS - actual}"


def test_database_is_at_head(db_engine):
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    with db_engine.connect() as connection:
        applied = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert applied == head


def test_guard_triggers_installed(db_engine):
    with db_engine.connect() as connection:
        triggers = {
            row[0]
            for row in connection.execute(
                text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
            )
        }
    assert {"claims_status_transition_guard", "audit_logs_append_only"} <= triggers


def test_claim_number_sequence_exists(db_engine):
    with db_engine.connect() as connection:
        assert connection.execute(
            text("SELECT 1 FROM pg_sequences WHERE sequencename = 'claim_number_seq'")
        ).scalar() == 1


def test_expected_indexes_on_claims(db_engine):
    indexes = {index["name"] for index in inspect(db_engine).get_indexes("claims")}
    assert {
        "ix_claims_employee_id_status",
        "ix_claims_status_created_at",
        "ix_claims_expense_date",
        "ix_claims_duplicate_probe",
    } <= indexes


def test_receipts_gained_employee_fk_without_losing_columns(db_engine):
    """The additive alter must not have disturbed the pre-existing receipts columns."""
    columns = {c["name"] for c in inspect(db_engine).get_columns("receipts")}
    assert "employee_ref_id" in columns
    # Columns created by 0001 must all still be present.
    assert {
        "id", "file_name", "content_type", "file_size_bytes", "s3_bucket", "s3_key",
        "s3_region", "employee_id", "extraction_status", "extraction_source",
        "raw_textract", "normalized_extraction", "vendor_name", "transaction_date",
        "total_amount", "currency", "error_message", "created_at", "updated_at",
    } <= columns

    foreign_keys = {
        fk["name"] for fk in inspect(db_engine).get_foreign_keys("receipts")
    }
    assert "fk_receipts_employee_ref_id" in foreign_keys


# --- model / migration parity ------------------------------------------------


def test_models_match_migrations_no_pending_autogenerate(db_engine):
    """``alembic revision --autogenerate`` must produce nothing.

    A non-empty diff means a model changed without a migration — the drift that eventually breaks a
    deployment.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    import app.models  # noqa: F401  (registers every model on Base.metadata)
    from app.core.database import Base

    with db_engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        diff = compare_metadata(context, Base.metadata)

    assert diff == [], f"models and migrations disagree: {diff}"


# --- seed data ---------------------------------------------------------------


def test_seeded_departments_and_employees(db_engine):
    with db_engine.connect() as connection:
        departments = {
            row[0] for row in connection.execute(text("SELECT code FROM departments"))
        }
        employees = {
            row[0] for row in connection.execute(text("SELECT employee_code FROM employees"))
        }
    assert {"ENG", "PM", "CS"} <= departments
    # The deployed Cognito users carry these codes in custom:employeeId.
    assert {"emp-100", "emp-101", "emp-102", "emp-103", "emp-104"} <= employees


def test_seeded_manager_chain_resolves(db_engine):
    with db_engine.connect() as connection:
        manager = connection.execute(
            text(
                """
                SELECT m.full_name FROM employees e
                JOIN employees m ON m.id = e.manager_id
                WHERE e.employee_code = 'emp-101'
                """
            )
        ).scalar_one()
    assert manager == "Marcus Vance"


def test_seeded_policy_rules_carry_typed_limits_and_json(db_engine):
    with db_engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT expense_limit, auto_approve_limit, receipt_required_above,
                       version, is_active, special_rules, conditions, actions
                  FROM policy_rules WHERE code = 'MEALS_STANDARD'
                """
            )
        ).one()

    assert float(row[0]) == 40.0
    assert float(row[1]) == 25.0
    assert float(row[2]) == 25.0
    assert row[3] == 1
    assert row[4] is True
    assert any("Alcohol" in clause for clause in row[5])
    assert row[6]["category"] == "Meals"
    assert row[7]["autoApproveBelowUsd"] == 25.0


def test_all_five_policy_categories_seeded(db_engine):
    with db_engine.connect() as connection:
        categories = {
            row[0]
            for row in connection.execute(
                text("SELECT category FROM policy_rules WHERE is_active")
            )
        }
    assert {
        "Meals",
        "Ground Transport",
        "Flights",
        "Lodging",
        "Client Entertainment",
    } <= categories


def test_seed_ids_are_deterministic():
    """Seed UUIDs must be stable across environments, or re-running the seed duplicates rows."""
    seed_module = ScriptDirectory.from_config(alembic_config()).get_revision(
        "0003_seed_reference_data"
    ).module

    assert seed_module._seed_id("employee", "emp-101") == seed_module._seed_id(
        "employee", "emp-101"
    )
    assert seed_module._seed_id("employee", "emp-101") != seed_module._seed_id(
        "employee", "emp-102"
    )


def test_seed_inserts_are_idempotent(db_engine):
    """Replaying a seed insert must be a no-op (``ON CONFLICT DO NOTHING``), not a duplicate."""
    seed_module = ScriptDirectory.from_config(alembic_config()).get_revision(
        "0003_seed_reference_data"
    ).module

    with db_engine.connect() as connection:
        before = connection.execute(text("SELECT count(*) FROM departments")).scalar_one()

    code, name, cost_center = seed_module.DEPARTMENTS[0]
    with db_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO departments (id, code, name, cost_center, is_active)
                VALUES (:id, :code, :name, :cc, true)
                ON CONFLICT (code) DO NOTHING
                """
            ),
            {
                "id": seed_module._seed_id("department", code),
                "code": code,
                "name": name,
                "cc": cost_center,
            },
        )

    with db_engine.connect() as connection:
        after = connection.execute(text("SELECT count(*) FROM departments")).scalar_one()
    assert after == before


# --- downgrade round trip ----------------------------------------------------


@pytest.fixture
def throwaway_database():
    """A database created for one test and dropped afterwards."""
    base = settings.database_url
    if not base:
        pytest.skip("No database configured.")

    name = f"expenseflow_migrationtest_{uuid.uuid4().hex[:8]}"
    target = make_url(base).set(database=name)
    admin = create_engine(
        make_url(base).set(database="postgres").render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
    )
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        yield target.render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def test_full_upgrade_downgrade_upgrade_round_trip(throwaway_database):
    """base -> head -> base -> head must all succeed, on a database that starts empty."""
    original = settings.DATABASE_URL
    settings.DATABASE_URL = throwaway_database
    config = alembic_config()
    try:
        command.upgrade(config, "head")

        engine = create_engine(throwaway_database)
        with engine.connect() as connection:
            assert EXPECTED_TABLES <= set(inspect(connection).get_table_names())

        command.downgrade(config, "base")
        with engine.connect() as connection:
            remaining = set(inspect(connection).get_table_names())
        # Every table this project creates is gone (only alembic's own bookkeeping may remain).
        assert not (EXPECTED_TABLES & remaining), f"downgrade left {EXPECTED_TABLES & remaining}"

        # And the whole chain re-applies cleanly on the emptied database.
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert EXPECTED_TABLES <= set(inspect(connection).get_table_names())
        engine.dispose()
    finally:
        settings.DATABASE_URL = original


def test_downgrade_one_step_leaves_receipts_intact(throwaway_database):
    """Rolling back the seed and core-domain revisions must not touch T001's receipts data."""
    original = settings.DATABASE_URL
    settings.DATABASE_URL = throwaway_database
    config = alembic_config()
    try:
        command.upgrade(config, "head")

        engine = create_engine(throwaway_database)
        receipt_id = uuid.uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO receipts (id, file_name, extraction_status, employee_id) "
                    "VALUES (:id, 'keep-me.png', 'COMPLETED', 'emp-101')"
                ),
                {"id": receipt_id},
            )

        command.downgrade(config, "0001_initial_receipts")

        with engine.connect() as connection:
            surviving = connection.execute(
                text("SELECT file_name FROM receipts WHERE id = :id"), {"id": receipt_id}
            ).scalar_one()
            columns = {c["name"] for c in inspect(connection).get_columns("receipts")}
        assert surviving == "keep-me.png"
        assert "employee_ref_id" not in columns  # the additive column is what was removed
        engine.dispose()
    finally:
        settings.DATABASE_URL = original
