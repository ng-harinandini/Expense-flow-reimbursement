"""phase 1 core domain: org, claims, policy, workflow, fraud, audit, AI-inference

Adds the durable domain model that replaces the in-memory stores:

  departments, employees, claims, claim_status_history, comments, attachments,
  policy_rules, approval_workflows, approval_steps, fraud_results, audit_logs,
  ai_inference_logs

plus two database-level guards that make the Phase 1 invariants unbypassable:

  * ``claims_status_transition_guard``  — rejects any illegal claim lifecycle transition, so even
    raw SQL cannot corrupt the state machine. The edge list is a frozen copy of
    ``app.domain.claim_state_machine.ALLOWED_TRANSITIONS`` at this revision;
    ``tests/test_claim_state_machine.py`` fails if the two ever drift.
  * ``audit_logs_append_only``          — rejects UPDATE and DELETE on ``audit_logs``.

``receipts`` gains ``employee_ref_id`` (FK to the new ``employees`` table), backfilled from the
existing ``employee_id`` text column by matching ``employees.employee_code``. No receipt data is
read, rewritten, or dropped.

Revision ID: 0002_phase1_core_domain
Revises: 0001_initial_receipts
Create Date: 2026-07-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_phase1_core_domain"
down_revision: Union[str, None] = "0001_initial_receipts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# --- enum type definitions ---------------------------------------------------------------
# Values are the API wire strings (see app/models/enums.py); member names differ by design.

CLAIM_STATUS_VALUES = (
    "Draft",
    "Submitted",
    "Processing",
    "Auto_Approved",
    "Manager_Review",
    "Finance_Review",
    "Approved",
    "Rejected",
    "Disbursed",
    "Flagged_Fraud",
)
EMPLOYEE_GRADE_VALUES = ("L1", "L2", "L3", "L4", "L5", "Director", "VP")
FRAUD_RISK_LEVEL_VALUES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
ATTACHMENT_KIND_VALUES = ("RECEIPT", "PRE_APPROVAL", "SUPPORTING")
APPROVAL_WORKFLOW_STATUS_VALUES = ("PENDING", "IN_PROGRESS", "COMPLETED", "CANCELLED")
APPROVAL_STEP_STATUS_VALUES = ("PENDING", "IN_PROGRESS", "APPROVED", "REJECTED", "SKIPPED")
AI_INFERENCE_STATUS_VALUES = ("SUCCESS", "FAILED", "FALLBACK")

_ENUMS = (
    ("claim_status", CLAIM_STATUS_VALUES),
    ("employee_grade", EMPLOYEE_GRADE_VALUES),
    ("fraud_risk_level", FRAUD_RISK_LEVEL_VALUES),
    ("attachment_kind", ATTACHMENT_KIND_VALUES),
    ("approval_workflow_status", APPROVAL_WORKFLOW_STATUS_VALUES),
    ("approval_step_status", APPROVAL_STEP_STATUS_VALUES),
    ("ai_inference_status", AI_INFERENCE_STATUS_VALUES),
)


def _enum(name: str, values: Sequence[str]) -> postgresql.ENUM:
    """Reference an already-created PG enum type (no implicit CREATE TYPE)."""
    return postgresql.ENUM(*values, name=name, create_type=False)


# Frozen copy of the legal lifecycle edges at this revision.
CLAIM_TRANSITIONS: tuple[tuple[str, str], ...] = (
    ("Draft", "Submitted"),
    ("Submitted", "Processing"),
    ("Processing", "Auto_Approved"),
    ("Processing", "Manager_Review"),
    ("Processing", "Finance_Review"),
    ("Processing", "Flagged_Fraud"),
    ("Auto_Approved", "Approved"),
    ("Auto_Approved", "Disbursed"),
    ("Auto_Approved", "Manager_Review"),
    ("Auto_Approved", "Flagged_Fraud"),
    ("Manager_Review", "Finance_Review"),
    ("Manager_Review", "Approved"),
    ("Manager_Review", "Rejected"),
    ("Manager_Review", "Flagged_Fraud"),
    ("Finance_Review", "Approved"),
    ("Finance_Review", "Rejected"),
    ("Finance_Review", "Flagged_Fraud"),
    ("Approved", "Disbursed"),
    ("Approved", "Flagged_Fraud"),
    ("Flagged_Fraud", "Manager_Review"),
    ("Flagged_Fraud", "Finance_Review"),
    ("Flagged_Fraud", "Approved"),
    ("Flagged_Fraud", "Rejected"),
)

MONEY = sa.Numeric(14, 2)


def _transition_guard_sql() -> str:
    """PL/pgSQL function body enforcing CLAIM_TRANSITIONS on UPDATE OF status."""
    pairs = ",\n            ".join(
        f"('{src}','{dst}')" for src, dst in CLAIM_TRANSITIONS
    )
    return f"""
    CREATE OR REPLACE FUNCTION claims_status_transition_guard() RETURNS trigger AS $$
    DECLARE
        is_allowed boolean;
    BEGIN
        IF NEW.status = OLD.status THEN
            RETURN NEW;
        END IF;

        SELECT EXISTS (
            SELECT 1 FROM (VALUES
            {pairs}
            ) AS t(src, dst)
            WHERE t.src = OLD.status::text AND t.dst = NEW.status::text
        ) INTO is_allowed;

        IF NOT is_allowed THEN
            RAISE EXCEPTION
                'illegal claim status transition: % -> % (claim %)',
                OLD.status, NEW.status, OLD.id
                USING ERRCODE = 'check_violation';
        END IF;

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


AUDIT_IMMUTABILITY_SQL = """
CREATE OR REPLACE FUNCTION audit_logs_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_logs is append-only: % is not permitted', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    bind = op.get_bind()

    # 1) Enum types (checkfirst so a partially applied run is resumable).
    for name, values in _ENUMS:
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    # 2) Organisation directory ------------------------------------------------------------
    op.create_table(
        "departments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("cost_center", sa.String(32), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_departments"),
        sa.UniqueConstraint("code", name="uq_departments_code"),
        sa.UniqueConstraint("name", name="uq_departments_name"),
    )

    op.create_table(
        "employees",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_code", sa.String(64), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("grade", _enum("employee_grade", EMPLOYEE_GRADE_VALUES), nullable=False),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("manager_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cognito_sub", sa.String(64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_employees"),
        sa.UniqueConstraint("employee_code", name="uq_employees_employee_code"),
        sa.UniqueConstraint("email", name="uq_employees_email"),
        sa.UniqueConstraint("cognito_sub", name="uq_employees_cognito_sub"),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"],
            ondelete="RESTRICT", name="fk_employees_department_id",
        ),
        sa.ForeignKeyConstraint(
            ["manager_id"], ["employees.id"],
            ondelete="SET NULL", name="fk_employees_manager_id",
        ),
    )
    op.create_index("ix_employees_department_id", "employees", ["department_id"])
    op.create_index("ix_employees_manager_id", "employees", ["manager_id"])
    op.create_index("ix_employees_is_active", "employees", ["is_active"])

    # 3) Policy rules ----------------------------------------------------------------------
    op.create_table(
        "policy_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("country", sa.String(2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("grade_tier", sa.String(64), nullable=True),
        sa.Column("expense_limit", MONEY, nullable=True),
        sa.Column("auto_approve_limit", MONEY, nullable=True),
        sa.Column("receipt_required_above", MONEY, nullable=True),
        sa.Column("requires_pre_approval", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("limit_expression", sa.String(120), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("expiration_date", sa.Date(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("conditions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("actions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("special_rules", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by_sub", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_policy_rules"),
        sa.UniqueConstraint("code", "version", name="uq_policy_rules_code_version"),
        sa.CheckConstraint(
            "expiration_date IS NULL OR expiration_date >= effective_date",
            name="ck_policy_rules_effective_window",
        ),
        sa.CheckConstraint(
            "expense_limit IS NULL OR expense_limit >= 0",
            name="ck_policy_rules_expense_limit_non_negative",
        ),
        sa.CheckConstraint(
            "auto_approve_limit IS NULL OR auto_approve_limit >= 0",
            name="ck_policy_rules_auto_approve_limit_non_negative",
        ),
        sa.CheckConstraint(
            "receipt_required_above IS NULL OR receipt_required_above >= 0",
            name="ck_policy_rules_receipt_threshold_non_negative",
        ),
        sa.CheckConstraint("version > 0", name="ck_policy_rules_version_positive"),
        sa.CheckConstraint("char_length(currency) = 3", name="ck_policy_rules_currency_iso4217"),
    )
    op.create_index(
        "ix_policy_rules_category_active_priority",
        "policy_rules",
        ["category", "is_active", "priority"],
    )
    op.create_index(
        "ix_policy_rules_effective_window", "policy_rules", ["effective_date", "expiration_date"]
    )
    op.create_index("ix_policy_rules_is_active", "policy_rules", ["is_active"])
    op.create_index("ix_policy_rules_country", "policy_rules", ["country"])

    # 4) Claims ----------------------------------------------------------------------------
    # Human-facing claim numbers come from a sequence, not a random suffix: gap tolerance is
    # fine, collisions are not (``claim_number`` is UNIQUE and appears on reimbursement records).
    op.execute("CREATE SEQUENCE IF NOT EXISTS claim_number_seq AS bigint START WITH 1000")

    op.create_table(
        "claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("claim_number", sa.String(32), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_grade", _enum("employee_grade", EMPLOYEE_GRADE_VALUES), nullable=False),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expense_date", sa.Date(), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("sub_category", sa.String(120), nullable=True),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("amount_usd", MONEY, nullable=False),
        sa.Column("fx_rate", sa.Numeric(18, 8), nullable=True),
        sa.Column("merchant_vendor", sa.String(200), nullable=False),
        sa.Column("purpose_description", sa.Text(), nullable=False, server_default=""),
        sa.Column("attendees", sa.Text(), nullable=True),
        sa.Column("trip_log", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("has_pre_approval", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("pre_approval_doc_ref", sa.String(200), nullable=True),
        sa.Column("receipt_attached", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("receipt_url", sa.Text(), nullable=True),
        sa.Column("receipt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("extracted_receipt", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("policy_validation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "status",
            _enum("claim_status", CLAIM_STATUS_VALUES),
            nullable=False,
            server_default="Draft",
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reimbursed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assigned_reviewer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_sub", sa.String(64), nullable=True),
        sa.Column("decision_notes", sa.Text(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("reimbursement_reference", sa.String(120), nullable=True),
        sa.Column("created_by_sub", sa.String(64), nullable=True),
        sa.Column("updated_by_sub", sa.String(64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_claims"),
        sa.UniqueConstraint("claim_number", name="uq_claims_claim_number"),
        sa.UniqueConstraint("receipt_id", name="uq_claims_receipt_id"),
        sa.CheckConstraint("amount > 0", name="ck_claims_amount_positive"),
        sa.CheckConstraint("amount_usd > 0", name="ck_claims_amount_usd_positive"),
        sa.CheckConstraint("char_length(currency) = 3", name="ck_claims_currency_iso4217"),
        sa.CheckConstraint("fx_rate IS NULL OR fx_rate > 0", name="ck_claims_fx_rate_positive"),
        sa.CheckConstraint(
            "(status = 'Draft' AND submitted_at IS NULL) "
            "OR (status <> 'Draft' AND submitted_at IS NOT NULL)",
            name="ck_claims_submitted_at_matches_status",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"], ["employees.id"], ondelete="RESTRICT", name="fk_claims_employee_id"
        ),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"],
            ondelete="RESTRICT", name="fk_claims_department_id",
        ),
        sa.ForeignKeyConstraint(
            ["receipt_id"], ["receipts.id"], ondelete="SET NULL", name="fk_claims_receipt_id"
        ),
        sa.ForeignKeyConstraint(
            ["assigned_reviewer_id"], ["employees.id"],
            ondelete="SET NULL", name="fk_claims_assigned_reviewer_id",
        ),
    )
    op.create_index("ix_claims_employee_id_status", "claims", ["employee_id", "status"])
    op.create_index("ix_claims_status_created_at", "claims", ["status", "created_at"])
    op.create_index("ix_claims_expense_date", "claims", ["expense_date"])
    op.create_index("ix_claims_category", "claims", ["category"])
    op.create_index("ix_claims_assigned_reviewer_id", "claims", ["assigned_reviewer_id"])
    op.create_index("ix_claims_department_id", "claims", ["department_id"])
    op.create_index(
        "ix_claims_duplicate_probe", "claims", ["employee_id", "expense_date", "amount_usd"]
    )

    # 5) Claim children --------------------------------------------------------------------
    op.create_table(
        "claim_status_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("from_status", _enum("claim_status", CLAIM_STATUS_VALUES), nullable=True),
        sa.Column("to_status", _enum("claim_status", CLAIM_STATUS_VALUES), nullable=False),
        sa.Column("actor_sub", sa.String(64), nullable=True),
        sa.Column("actor_name", sa.String(200), nullable=True),
        sa.Column("actor_role", sa.String(32), nullable=True),
        sa.Column("step_name", sa.String(120), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False, server_default="SUCCESS"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_claim_status_history"),
        sa.UniqueConstraint("claim_id", "sequence", name="uq_claim_status_history_claim_sequence"),
        sa.CheckConstraint("sequence > 0", name="ck_claim_status_history_sequence_positive"),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claims.id"],
            ondelete="CASCADE", name="fk_claim_status_history_claim_id",
        ),
    )
    op.create_index("ix_claim_status_history_claim_id", "claim_status_history", ["claim_id"])
    op.create_index("ix_claim_status_history_occurred_at", "claim_status_history", ["occurred_at"])

    op.create_table(
        "comments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("author_sub", sa.String(64), nullable=True),
        sa.Column("author_name", sa.String(200), nullable=False),
        sa.Column("author_role", sa.String(32), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_internal", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_comments"),
        sa.CheckConstraint("char_length(btrim(body)) > 0", name="ck_comments_body_not_blank"),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claims.id"], ondelete="CASCADE", name="fk_comments_claim_id"
        ),
    )
    op.create_index("ix_comments_claim_id", "comments", ["claim_id"])
    op.create_index("ix_comments_created_at", "comments", ["created_at"])

    op.create_table(
        "attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("receipt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "kind",
            _enum("attachment_kind", ATTACHMENT_KIND_VALUES),
            nullable=False,
            server_default="SUPPORTING",
        ),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(200), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("s3_bucket", sa.Text(), nullable=True),
        sa.Column("s3_key", sa.Text(), nullable=True),
        sa.Column("s3_region", sa.String(32), nullable=True),
        sa.Column("uploaded_by_sub", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_attachments"),
        sa.CheckConstraint(
            "file_size_bytes IS NULL OR file_size_bytes >= 0",
            name="ck_attachments_file_size_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claims.id"], ondelete="CASCADE", name="fk_attachments_claim_id"
        ),
        sa.ForeignKeyConstraint(
            ["receipt_id"], ["receipts.id"], ondelete="SET NULL", name="fk_attachments_receipt_id"
        ),
    )
    op.create_index("ix_attachments_claim_id", "attachments", ["claim_id"])
    op.create_index("ix_attachments_receipt_id", "attachments", ["receipt_id"])
    op.create_index("ix_attachments_created_at", "attachments", ["created_at"])

    # 6) Approval workflow ------------------------------------------------------------------
    op.create_table(
        "approval_workflows",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("definition_code", sa.String(64), nullable=False, server_default="STANDARD_TWO_STAGE"),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "status",
            _enum("approval_workflow_status", APPROVAL_WORKFLOW_STATUS_VALUES),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("current_step_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_approval_workflows"),
        sa.CheckConstraint(
            "current_step_order >= 0", name="ck_approval_workflows_current_step_non_negative"
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claims.id"],
            ondelete="CASCADE", name="fk_approval_workflows_claim_id",
        ),
    )
    op.create_index("ix_approval_workflows_claim_id", "approval_workflows", ["claim_id"])
    op.create_index("ix_approval_workflows_status", "approval_workflows", ["status"])

    op.create_table(
        "approval_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_order", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("required_role", sa.String(32), nullable=False),
        sa.Column("assignee_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            _enum("approval_step_status", APPROVAL_STEP_STATUS_VALUES),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("decided_by_sub", sa.String(64), nullable=True),
        sa.Column("decision_notes", sa.Text(), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_approval_steps"),
        sa.UniqueConstraint("workflow_id", "step_order", name="uq_approval_steps_workflow_order"),
        sa.CheckConstraint("step_order > 0", name="ck_approval_steps_order_positive"),
        sa.ForeignKeyConstraint(
            ["workflow_id"], ["approval_workflows.id"],
            ondelete="CASCADE", name="fk_approval_steps_workflow_id",
        ),
        sa.ForeignKeyConstraint(
            ["assignee_id"], ["employees.id"],
            ondelete="SET NULL", name="fk_approval_steps_assignee_id",
        ),
    )
    op.create_index("ix_approval_steps_workflow_id", "approval_steps", ["workflow_id"])
    op.create_index("ix_approval_steps_assignee_id", "approval_steps", ["assignee_id"])
    op.create_index("ix_approval_steps_status", "approval_steps", ["status"])

    # 7) Fraud results ----------------------------------------------------------------------
    op.create_table(
        "fraud_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("risk_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "risk_level", _enum("fraud_risk_level", FRAUD_RISK_LEVEL_VALUES), nullable=False
        ),
        sa.Column("is_flagged", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("recommended_action", sa.String(32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column("flags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("engine_version", sa.String(32), nullable=False, server_default="rules-1.0"),
        # clock_timestamp(), not now(): now() is constant within a transaction, so two screenings
        # written in one request would tie and "latest verdict" would be ambiguous.
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_fraud_results"),
        sa.CheckConstraint(
            "risk_score >= 0 AND risk_score <= 100", name="ck_fraud_results_risk_score_range"
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claims.id"], ondelete="CASCADE", name="fk_fraud_results_claim_id"
        ),
    )
    op.create_index(
        "ix_fraud_results_claim_id_evaluated_at", "fraud_results", ["claim_id", "evaluated_at"]
    )
    op.create_index("ix_fraud_results_risk_level", "fraud_results", ["risk_level"])
    op.create_index("ix_fraud_results_is_flagged", "fraud_results", ["is_flagged"])
    op.create_index("ix_fraud_results_created_at", "fraud_results", ["created_at"])

    # 8) Audit + AI inference ---------------------------------------------------------------
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("actor_sub", sa.String(64), nullable=True),
        sa.Column("actor_name", sa.String(200), nullable=False),
        sa.Column("actor_role", sa.String(32), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.String(200), nullable=False),
        sa.Column("details", sa.Text(), nullable=False, server_default=""),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(400), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
    )
    op.create_index("ix_audit_logs_entity", "audit_logs", ["entity_type", "entity_id", "occurred_at"])
    op.create_index("ix_audit_logs_occurred_at", "audit_logs", ["occurred_at"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_actor_sub", "audit_logs", ["actor_sub"])
    op.create_index("ix_audit_logs_correlation_id", "audit_logs", ["correlation_id"])

    op.create_table(
        "ai_inference_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("receipt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("input_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("output_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("actor_sub", sa.String(64), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ai_inference_logs"),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claims.id"], ondelete="SET NULL", name="fk_ai_inference_logs_claim_id"
        ),
        sa.ForeignKeyConstraint(
            ["receipt_id"], ["receipts.id"],
            ondelete="SET NULL", name="fk_ai_inference_logs_receipt_id",
        ),
    )
    op.create_index("ix_ai_inference_logs_claim_id", "ai_inference_logs", ["claim_id"])
    op.create_index("ix_ai_inference_logs_receipt_id", "ai_inference_logs", ["receipt_id"])
    op.create_index("ix_ai_inference_logs_created_at", "ai_inference_logs", ["created_at"])
    op.create_index("ix_ai_inference_logs_operation", "ai_inference_logs", ["operation"])

    # 9) Link existing receipts to employees (additive; existing data preserved) -------------
    op.add_column(
        "receipts", sa.Column("employee_ref_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_receipts_employee_ref_id",
        "receipts",
        "employees",
        ["employee_ref_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_receipts_employee_ref_id", "receipts", ["employee_ref_id"])
    # Backfill by external code. No-op on a fresh database; safe to re-run.
    op.execute(
        """
        UPDATE receipts r
           SET employee_ref_id = e.id
          FROM employees e
         WHERE r.employee_ref_id IS NULL
           AND r.employee_id IS NOT NULL
           AND e.employee_code = r.employee_id
        """
    )

    # 10) Guard triggers ---------------------------------------------------------------------
    op.execute(_transition_guard_sql())
    op.execute(
        """
        CREATE TRIGGER claims_status_transition_guard
        BEFORE UPDATE OF status ON claims
        FOR EACH ROW EXECUTE FUNCTION claims_status_transition_guard();
        """
    )

    op.execute(AUDIT_IMMUTABILITY_SQL)
    op.execute(
        """
        CREATE TRIGGER audit_logs_append_only
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION audit_logs_append_only();
        """
    )


def downgrade() -> None:
    """Reverse of :func:`upgrade`.

    Drops only objects this revision created. ``receipts`` keeps every row and every pre-existing
    column; it loses just the additive ``employee_ref_id`` link.
    """
    bind = op.get_bind()

    op.execute("DROP TRIGGER IF EXISTS audit_logs_append_only ON audit_logs")
    op.execute("DROP FUNCTION IF EXISTS audit_logs_append_only()")
    op.execute("DROP TRIGGER IF EXISTS claims_status_transition_guard ON claims")
    op.execute("DROP FUNCTION IF EXISTS claims_status_transition_guard()")

    op.drop_index("ix_receipts_employee_ref_id", table_name="receipts")
    op.drop_constraint("fk_receipts_employee_ref_id", "receipts", type_="foreignkey")
    op.drop_column("receipts", "employee_ref_id")

    op.drop_table("ai_inference_logs")
    op.drop_table("audit_logs")
    op.drop_table("fraud_results")
    op.drop_table("approval_steps")
    op.drop_table("approval_workflows")
    op.drop_table("attachments")
    op.drop_table("comments")
    op.drop_table("claim_status_history")
    op.drop_table("claims")
    op.execute("DROP SEQUENCE IF EXISTS claim_number_seq")
    op.drop_table("policy_rules")
    op.drop_table("employees")
    op.drop_table("departments")

    for name, values in reversed(_ENUMS):
        postgresql.ENUM(*values, name=name).drop(bind, checkfirst=True)
