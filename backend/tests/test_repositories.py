"""Remaining repository behaviour: the generic base, directory, receipts, workflow, AI ledger."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.core.unit_of_work import UnitOfWork
from app.domain.errors import NotFoundError
from app.models.enums import (
    AIInferenceStatus,
    ApprovalStepStatus,
    ApprovalWorkflowStatus,
    ClaimStatus,
)
from app.models.receipt import ExtractionStatus, Receipt


# --- generic base ------------------------------------------------------------


def test_base_get_accepts_uuid_and_string(repositories, employee):
    employees = repositories["employees"]
    assert employees.get(employee.id).id == employee.id
    assert employees.get(str(employee.id)).id == employee.id


def test_base_get_returns_none_for_malformed_id(repositories):
    assert repositories["employees"].get("not-a-uuid") is None
    assert repositories["employees"].get(None) is None


def test_base_exists_and_count(repositories, employee):
    employees = repositories["employees"]
    assert employees.exists(employee.id) is True
    assert employees.exists(uuid.uuid4()) is False
    assert employees.count() >= 1


def test_base_list_paginates(repositories, role_id):
    from app.models.enums import EmployeeGrade
    from app.models.organization import Employee

    employees = repositories["employees"]
    employees.add_all(
        [
            Employee(
                employee_code=f"emp-page-{index}", full_name=f"Page {index}",
                email=f"page{index}@enterprise.com", grade=EmployeeGrade.L1,
                role_id=role_id,
            )
            for index in range(4)
        ]
    )
    first = employees.list(limit=2)
    second = employees.list(limit=2, offset=2)
    assert len(first) == 2
    assert {e.id for e in first}.isdisjoint({e.id for e in second})


def test_base_get_or_raise_message_names_the_entity(repositories):
    with pytest.raises(NotFoundError) as raised:
        repositories["employees"].get_or_raise(uuid.uuid4())
    assert raised.value.entity == "Employee"


def test_base_add_all_flushes_every_row(repositories, role_id):
    from app.models.enums import EmployeeGrade
    from app.models.organization import Employee

    created = repositories["employees"].add_all(
        [
            Employee(
                employee_code=f"emp-bulk-{index}", full_name=f"Bulk {index}",
                email=f"bulk{index}@enterprise.com", grade=EmployeeGrade.L1,
                role_id=role_id,
            )
            for index in range(2)
        ]
    )
    assert all(row.id is not None for row in created)


def test_base_delete_removes_the_row(repositories, role_id):
    from app.models.enums import EmployeeGrade
    from app.models.organization import Employee

    employees = repositories["employees"]
    row = employees.add(
        Employee(
            employee_code="emp-temp", full_name="Temp", email="temp@enterprise.com",
            grade=EmployeeGrade.L1, role_id=role_id,
        )
    )
    row_id = row.id
    employees.delete(row)
    assert employees.get(row_id) is None


# --- employee directory --------------------------------------------------------


def test_employee_lookup_by_code_email_and_sub(repositories, employee):
    employees = repositories["employees"]
    assert employees.get_by_code(employee.employee_code).id == employee.id
    assert employees.get_by_email(employee.email).id == employee.id
    assert employees.get_by_email(employee.email.upper()).id == employee.id  # normalized

    employees.link_cognito_sub(employee, "sub-linked-1")
    assert employees.get_by_cognito_sub("sub-linked-1").id == employee.id


def test_employee_lookups_tolerate_empty_input(repositories):
    employees = repositories["employees"]
    assert employees.get_by_code("") is None
    assert employees.get_by_email("") is None
    assert employees.get_by_cognito_sub("") is None
    assert employees.resolve() is None


def test_resolve_prefers_the_employee_code_then_falls_back_to_sub(repositories, employee):
    employees = repositories["employees"]
    employees.link_cognito_sub(employee, "sub-fallback")

    assert employees.resolve(employee_code=employee.employee_code).id == employee.id
    # Unknown code -> falls through to the Cognito sub.
    assert employees.resolve(employee_code="emp-nope", cognito_sub="sub-fallback").id == employee.id


def test_link_cognito_sub_is_idempotent(repositories, employee):
    employees = repositories["employees"]
    employees.link_cognito_sub(employee, "sub-same")
    employees.link_cognito_sub(employee, "sub-same")
    assert employee.cognito_sub == "sub-same"


def test_list_active_and_direct_reports(repositories, employee):
    employees = repositories["employees"]
    assert employee.id in {e.id for e in employees.list_active()}

    reports = employees.list_direct_reports(employee.manager_id)
    assert employee.id in {e.id for e in reports}


def test_role_lookups(repositories):
    roles = repositories["roles"]
    employee_role = roles.get_by_name("employee")
    assert employee_role is not None
    assert roles.get_by_name("nope") is None
    assert "employee" in {r.name for r in roles.list_all()}


def test_employee_derived_properties(employee):
    assert employee.role_name == "employee"
    assert employee.manager_name == "Test Manager"


def test_next_employee_code_increments_and_defaults_to_emp_000(repositories, role_id):
    from app.models.enums import EmployeeGrade
    from app.models.organization import Employee

    employees = repositories["employees"]
    assert employees.next_employee_code() == "emp-000"

    employees.add(
        Employee(
            employee_code="emp-007", full_name="Seven", email="seven@enterprise.com",
            grade=EmployeeGrade.L1, role_id=role_id,
        )
    )
    assert employees.next_employee_code() == "emp-008"


def test_get_by_codes_batches_lookup(repositories, employee, other_employee):
    employees = repositories["employees"]
    found = employees.get_by_codes([employee.employee_code, other_employee.employee_code, "nope"])
    assert set(found) == {employee.employee_code, other_employee.employee_code}
    assert employees.get_by_codes([]) == {}


# --- receipts ----------------------------------------------------------------


@pytest.fixture
def stored_receipt(repositories, employee):
    return repositories["receipts"].add(
        Receipt(
            file_name="lunch.png", content_type="image/png", file_size_bytes=100,
            extraction_status=ExtractionStatus.COMPLETED, extraction_source="fallback",
            employee_id=employee.employee_code, employee_ref_id=employee.id,
            vendor_name="Sweetgreen", total_amount=Decimal("22.50"), currency="USD",
        )
    )


def test_receipts_listed_newest_first_and_scoped_by_code(
    repositories, stored_receipt, employee, other_employee
):
    receipts = repositories["receipts"]
    receipts.add(
        Receipt(
            file_name="theirs.png", extraction_status=ExtractionStatus.COMPLETED,
            employee_id=other_employee.employee_code, employee_ref_id=other_employee.id,
        )
    )

    mine = receipts.list_for_employee_code(employee.employee_code)
    assert stored_receipt.id in {r.id for r in mine}
    assert all(r.employee_id == employee.employee_code for r in mine)

    everyones = receipts.list_for_employee_code(None)
    timestamps = [r.created_at for r in everyones]
    assert timestamps == sorted(timestamps, reverse=True)


def test_receipts_filtered_by_extraction_status(repositories, stored_receipt):
    receipts = repositories["receipts"]
    receipts.add(Receipt(file_name="pending.png", extraction_status=ExtractionStatus.PENDING))

    completed = receipts.list_by_status(ExtractionStatus.COMPLETED)
    assert stored_receipt.id in {r.id for r in completed}
    assert all(r.extraction_status is ExtractionStatus.COMPLETED for r in completed)


def test_is_claimed_and_claim_using(repositories, stored_receipt, make_claim):
    receipts = repositories["receipts"]
    assert receipts.is_claimed(stored_receipt.id) is False
    assert receipts.claim_using(stored_receipt.id) is None

    claim = make_claim(receipt_id=stored_receipt.id)
    assert receipts.is_claimed(stored_receipt.id) is True
    assert receipts.claim_using(stored_receipt.id).id == claim.id


def test_link_employee_sets_the_uuid_reference(repositories, employee):
    receipts = repositories["receipts"]
    orphan = receipts.add(
        Receipt(file_name="orphan.png", extraction_status=ExtractionStatus.COMPLETED)
    )
    receipts.link_employee(orphan, employee.id)
    assert orphan.employee_ref_id == employee.id


# --- approval workflow -------------------------------------------------------


def test_standard_workflow_has_manager_then_finance(repositories, make_claim):
    workflows = repositories["workflows"]
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    workflow = workflows.create_standard_workflow(claim.id)

    assert [(s.step_order, s.required_role) for s in workflow.steps] == [
        (1, "manager"), (2, "finance")
    ]
    assert workflow.status is ApprovalWorkflowStatus.PENDING
    assert workflow.current_step_order == 0


def test_workflow_assignees_can_be_preset_by_role(repositories, make_claim, other_employee):
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    workflow = repositories["workflows"].create_standard_workflow(
        claim.id, assignee_by_role={"manager": other_employee.id}
    )
    manager_step = next(s for s in workflow.steps if s.required_role == "manager")
    assert manager_step.assignee_id == other_employee.id
    assert manager_step.assignee.full_name == other_employee.full_name


def test_start_step_marks_it_in_progress(repositories, make_claim):
    workflows = repositories["workflows"]
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    workflow = workflows.create_standard_workflow(claim.id)

    step = workflows.start_step(workflow, 1)
    assert step.status is ApprovalStepStatus.IN_PROGRESS
    assert step.started_at is not None
    assert workflow.status is ApprovalWorkflowStatus.IN_PROGRESS
    assert workflow.current_step_order == 1
    assert workflow.current_step.step_order == 1


def test_start_step_returns_none_for_an_unknown_order(repositories, make_claim):
    workflows = repositories["workflows"]
    workflow = workflows.create_standard_workflow(make_claim().id)
    assert workflows.start_step(workflow, 99) is None


def test_complete_step_by_role_then_advance(repositories, make_claim):
    workflows = repositories["workflows"]
    workflow = workflows.create_standard_workflow(make_claim(ClaimStatus.MANAGER_REVIEW).id)
    workflows.start_step(workflow, 1)

    completed = workflows.complete_step(
        workflow, required_role="manager", decided_by_sub="sub-mgr",
        decision_notes="approved",
    )
    assert completed.status is ApprovalStepStatus.APPROVED
    assert completed.completed_at is not None

    workflows.advance_or_complete(workflow)
    assert workflow.current_step_order == 2


def test_completing_the_last_step_completes_the_workflow(repositories, make_claim):
    workflows = repositories["workflows"]
    workflow = workflows.create_standard_workflow(make_claim(ClaimStatus.MANAGER_REVIEW).id)

    for role in ("manager", "finance"):
        workflows.complete_step(workflow, required_role=role)
    workflows.advance_or_complete(workflow)

    assert workflow.status is ApprovalWorkflowStatus.COMPLETED
    assert workflow.completed_at is not None


def test_complete_step_returns_none_when_nothing_is_open(repositories, make_claim):
    workflows = repositories["workflows"]
    workflow = workflows.create_standard_workflow(make_claim().id)
    workflows.complete_step(workflow, required_role="manager")
    assert workflows.complete_step(workflow, required_role="manager") is None


def test_cancel_open_steps_skips_the_remainder(repositories, make_claim):
    workflows = repositories["workflows"]
    workflow = workflows.create_standard_workflow(make_claim(ClaimStatus.MANAGER_REVIEW).id)

    workflows.cancel_open_steps(workflow, decided_by_sub="sub-fin", reason="Claim rejected")
    assert all(step.status is ApprovalStepStatus.SKIPPED for step in workflow.steps)
    assert workflow.status is ApprovalWorkflowStatus.COMPLETED


def test_reject_open_step_records_the_rejection_then_closes(repositories, make_claim):
    workflows = repositories["workflows"]
    workflow = workflows.create_standard_workflow(make_claim(ClaimStatus.MANAGER_REVIEW).id)

    workflows.reject_open_step(
        workflow, required_role="manager", decided_by_sub="sub-mgr", reason="Not eligible"
    )
    manager_step = next(s for s in workflow.steps if s.required_role == "manager")
    finance_step = next(s for s in workflow.steps if s.required_role == "finance")
    assert manager_step.status is ApprovalStepStatus.REJECTED
    assert finance_step.status is ApprovalStepStatus.SKIPPED
    assert workflow.status is ApprovalWorkflowStatus.COMPLETED


def test_get_active_and_list_for_claim(repositories, make_claim):
    workflows = repositories["workflows"]
    claim = make_claim(ClaimStatus.MANAGER_REVIEW)
    workflow = workflows.create_standard_workflow(claim.id)

    assert workflows.get_active_for_claim(claim.id).id == workflow.id
    assert [w.id for w in workflows.list_for_claim(claim.id)] == [workflow.id]

    workflows.complete_workflow(workflow)
    assert workflows.get_active_for_claim(claim.id) is None


def test_step_for_role_finds_only_open_steps(repositories, make_claim):
    workflows = repositories["workflows"]
    workflow = workflows.create_standard_workflow(make_claim().id)
    assert workflow.step_for_role("manager") is not None
    workflows.complete_step(workflow, required_role="manager")
    assert workflow.step_for_role("manager") is None


# --- AI inference ledger -----------------------------------------------------


def test_record_inference_captures_cost_and_provenance(repositories, make_claim):
    from app.core.context import build_context, reset_context, set_context

    ledger = repositories["ai"]
    claim = make_claim()

    token = set_context(build_context(request_id="req-ai", correlation_id="corr-ai"))
    try:
        entry = ledger.record(
            provider="anthropic", model="claude-opus-5", operation="policy_reasoning",
            claim_id=claim.id, input_summary={"category": "Meals"},
            output_summary={"verdict": "pass"}, input_tokens=120, output_tokens=45,
            latency_ms=880, actor_sub="sub-admin",
        )
    finally:
        reset_context(token)

    assert entry.status == AIInferenceStatus.SUCCESS.value
    assert entry.request_id == "req-ai"
    assert entry.correlation_id == "corr-ai"
    assert entry.input_tokens == 120
    assert entry.latency_ms == 880


def test_record_inference_accepts_a_failure_with_a_message(repositories):
    entry = repositories["ai"].record(
        provider="gemini", model="gemini-2.5-flash", operation="ocr_extract",
        status=AIInferenceStatus.FAILED, error_message="quota exceeded",
    )
    assert entry.status == "FAILED"
    assert entry.error_message == "quota exceeded"


def test_inference_queries_by_claim_receipt_and_operation(repositories, make_claim, employee):
    ledger = repositories["ai"]
    claim = make_claim()
    receipt = repositories["receipts"].add(
        Receipt(file_name="r.png", extraction_status=ExtractionStatus.COMPLETED)
    )

    ledger.record(provider="p", model="m", operation="ocr_extract", claim_id=claim.id)
    ledger.record(provider="p", model="m", operation="ocr_extract", receipt_id=receipt.id)

    assert len(ledger.list_for_claim(claim.id)) == 1
    assert len(ledger.list_for_receipt(receipt.id)) == 1
    assert len(ledger.list_recent(operation="ocr_extract")) == 2
    assert ledger.list_recent(operation="never_run") == []


# --- unit of work ------------------------------------------------------------


def test_unit_of_work_commit_persists(db_session, employee, make_claim):
    claim = make_claim()
    UnitOfWork(db_session).commit()
    db_session.expire(claim)
    assert claim.status is ClaimStatus.DRAFT  # row is readable after commit


def test_unit_of_work_rollback_discards(db_session, repositories, employee, make_claim):
    before = repositories["claims"].count()
    make_claim()
    UnitOfWork(db_session).rollback()
    assert repositories["claims"].count() == before


def test_unit_of_work_flush_sends_sql_without_ending_the_transaction(db_session, make_claim):
    uow = UnitOfWork(db_session)
    claim = make_claim()
    uow.flush()
    assert claim.id is not None


def test_unit_of_work_context_manager_commits_on_success(db_session, make_claim, repositories):
    before = repositories["claims"].count()
    with UnitOfWork(db_session):
        make_claim()
    assert repositories["claims"].count() == before + 1


def test_unit_of_work_context_manager_rolls_back_on_error(db_session, make_claim, repositories):
    before = repositories["claims"].count()
    with pytest.raises(RuntimeError):
        with UnitOfWork(db_session):
            make_claim()
            raise RuntimeError("boom")
    assert repositories["claims"].count() == before
