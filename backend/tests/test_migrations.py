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
    "employees",
    "expense_categories",
    "expense_items",
    "fraud_results",
    "policy_rules",
    "roles",
}

EXPECTED_ENUMS = {
    "ai_inference_status",
    "approval_step_status",
    "approval_workflow_status",
    "attachment_kind",
    "claim_status",
    "employee_grade",
    "expense_item_status",
    "fraud_risk_level",
}


# --- revision graph ----------------------------------------------------------


def test_single_migration_head():
    """Two heads mean an unmerged branch and a broken ``alembic upgrade head``."""
    heads = ScriptDirectory.from_config(alembic_config()).get_heads()
    assert len(heads) == 1, f"expected one head, found {heads}"


def test_revision_graph_is_complete_and_joined():
    """Every revision, plus the shape of the one branch/merge pair in the graph.

    Deliberately hard-coded rather than computed: adding a revision should require updating this
    test, which is how a reviewer is forced to notice a new migration and confirm its place.

    The chain is not linear. ``0003_seed_reference_data`` forked into a core-domain line and an
    AI-platform line, which ``0007_merge_heads`` rejoins. ``0009_candidate_policy_rules`` forked the
    same way into a claim line and a candidate-policy line, which ``0013_merge_heads`` rejoins — so
    this asserts set membership plus the edges that define each fork, rather than a single ordered
    walk. ``0014_item_ai_classification`` is a plain linear continuation from the ``0013`` join, not
    another fork.
    """
    script = ScriptDirectory.from_config(alembic_config())
    revisions = {r.revision for r in script.walk_revisions()}
    assert revisions == {
        "0016_claim_policy_rules",
        "0015_database_policy_evaluator",
        "0014_item_ai_classification",
        "0013_merge_heads",
        "0012_limit_expression_unbounded",
        "0012_category_custom_fields",
        "0011_candidate_currency_nullable",
        "0011_retire_disburse_action",
        "0010_candidate_review_metadata",
        "0010_claim_withdrawal",
        "0009_candidate_policy_rules",
        "0008_multi_item_claims",
        "0007_merge_heads",
        "0006_prompt_governance",
        "0005_duplicate_detection",
        "0004_ai_knowledge_platform",
        "0005_drop_departments",
        "0004_roles_and_employee_cleanup",
        "0003_seed_reference_data",
        "0002_phase1_core_domain",
        "0001_initial_receipts",
    }

    # The fork: both 0004s descend from the same seed revision.
    for forked in ("0004_roles_and_employee_cleanup", "0004_ai_knowledge_platform"):
        assert script.get_revision(forked).down_revision == "0003_seed_reference_data"

    # The join: the merge revision has exactly the two branch tips as parents.
    merge = script.get_revision("0007_merge_heads")
    assert set(merge.down_revision) == {"0005_drop_departments", "0006_prompt_governance"}

    # The second fork: both 0010s descend from the same candidate-policy revision.
    for forked in ("0010_claim_withdrawal", "0010_candidate_review_metadata"):
        assert script.get_revision(forked).down_revision == "0009_candidate_policy_rules"

    # The second join: the merge revision has exactly the two branch tips as parents.
    merge_2 = script.get_revision("0013_merge_heads")
    assert set(merge_2.down_revision) == {
        "0012_category_custom_fields",
        "0012_limit_expression_unbounded",
    }

    # The current head is a plain continuation of the second join, not a third fork.
    assert script.get_revision("0014_item_ai_classification").down_revision == "0013_merge_heads"
    assert script.get_revision("0015_database_policy_evaluator").down_revision == "0014_item_ai_classification"
    assert script.get_revision("0016_claim_policy_rules").down_revision == "0015_database_policy_evaluator"


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
    assert {
        "claims_status_transition_guard",
        "audit_logs_append_only",
        "expense_items_recalculate_claim_totals",
    } <= triggers


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
        "ix_claims_assigned_reviewer_id",
    } <= indexes


def test_expected_indexes_on_expense_items(db_engine):
    """The per-expense indexes that moved off ``claims``, plus the file-hash probe.

    ``ix_expense_items_file_hash`` is what makes exact-duplicate receipt detection usable rather
    than a sequential scan on every upload.
    """
    indexes = {index["name"] for index in inspect(db_engine).get_indexes("expense_items")}
    assert {
        "ix_expense_items_claim_id",
        "ix_expense_items_status",
        "ix_expense_items_expense_date",
        "ix_expense_items_file_hash",
        "ix_expense_items_duplicate_probe",
    } <= indexes


def test_claims_lost_the_per_expense_columns(db_engine):
    """0008 moved every expense fact to ``expense_items``; the claim keeps only the roll-ups."""
    columns = {c["name"] for c in inspect(db_engine).get_columns("claims")}
    assert {"total_amount", "total_amount_usd", "item_count", "title"} <= columns
    assert not ({
        "amount", "amount_usd", "category", "merchant_vendor", "expense_date",
        "receipt_id", "receipt_url", "extracted_receipt", "policy_validation",
    } & columns)


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


def test_seeded_roles(db_engine):
    with db_engine.connect() as connection:
        roles = {
            row[0] for row in connection.execute(text("SELECT name FROM roles"))
        }
    assert roles == {"employee", "manager", "finance", "admin", "auditor"}


def test_seed_employees_and_departments_are_gone_at_head(db_engine):
    """0004/0005 remove the pre-role-system seed employees and the departments table entirely."""
    with db_engine.connect() as connection:
        employees = {
            row[0] for row in connection.execute(text("SELECT employee_code FROM employees"))
        }
    assert not ({"emp-100", "emp-101", "emp-102", "emp-103", "emp-104"} & employees)
    assert "departments" not in set(inspect(db_engine).get_table_names())


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
        "Taxi / Cab / Ride-hailing",
        "Air Travel",
        "Hotel / Lodging",
        "Client / Business Entertainment",
    } <= categories


def test_expense_categories_match_policy_rule_categories(db_engine):
    """``expense_categories.name`` and ``policy_rules.category`` must not drift apart.

    ``app.services.policy_engine`` dispatches on the literal category *string* an item carries, and
    an item's category text is copied from this table. If the two vocabularies diverge, items file
    under a name no policy rule matches and silently fall through to the generic branch.
    """
    with db_engine.connect() as connection:
        seeded = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM expense_categories WHERE is_active AND NOT is_common")
            )
        }
        policy = {
            row[0]
            for row in connection.execute(
                text("SELECT category FROM policy_rules WHERE is_active")
            )
        }
    assert {
        "Meals",
        "Taxi / Cab / Ride-hailing",
        "Air Travel",
        "Hotel / Lodging",
        "Client / Business Entertainment",
    } <= seeded
    # Only the five categories with bespoke policy_engine branches carry a seeded policy_rules row
    # (see app.services.policy_engine's module docstring); the other ten fall through to that
    # engine's generic else-branch default. So the invariant is narrower post-0012: every *seeded
    # policy rule's* category must still resolve to a real, active expense_categories row — not
    # the reverse.
    # ``*`` is the database-backed global policy category (for example, claim age).
    assert (policy - {"*"}) <= seeded, f"policy rules with no matching category: {policy - seeded}"


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
        "0004_roles_and_employee_cleanup"
    ).module

    with db_engine.connect() as connection:
        before = connection.execute(text("SELECT count(*) FROM roles")).scalar_one()

    name, description = seed_module.ROLES[0]
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO roles (name, description) VALUES (:name, :description) "
                "ON CONFLICT (name) DO NOTHING"
            ),
            {"name": name, "description": description},
        )

    with db_engine.connect() as connection:
        after = connection.execute(text("SELECT count(*) FROM roles")).scalar_one()
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


def test_downgrade_to_0007_restores_the_receipts_tables(throwaway_database):
    """0008's downgrade must rebuild what it dropped.

    The receipt *data* is gone for good — 0008 drops the tables outright — but the structure has to
    come back, or the revision cannot be rolled back on a deployed database at all.
    """
    original = settings.DATABASE_URL
    settings.DATABASE_URL = throwaway_database
    config = alembic_config()
    try:
        command.upgrade(config, "head")
        command.downgrade(config, "0007_merge_heads")

        engine = create_engine(throwaway_database)
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
        assert {"receipts", "receipt_fields", "receipt_line_items"} <= tables
        assert not ({"expense_items", "expense_categories"} & tables)
        engine.dispose()
    finally:
        settings.DATABASE_URL = original
