"""Audit trail: content, provenance capture, immutability, and transactional coupling."""

from __future__ import annotations

import uuid

import pytest

from app.core.context import build_context, reset_context, set_context
from app.models.enums import AuditAction, AuditEntity


@pytest.fixture
def audit(repositories):
    return repositories["audit"]


# --- required content --------------------------------------------------------


def test_record_captures_every_required_field(audit_service, employee_actor):
    entry = audit_service.record(
        actor=employee_actor,
        action=AuditAction.SUBMIT_CLAIM,
        entity_type=AuditEntity.CLAIM,
        entity_id="EXP-2026-1234",
        details="Submitted claim for USD 22.50 (Meals)",
        before={"status": "Draft"},
        after={"status": "Submitted"},
    )

    assert entry.id is not None
    assert entry.occurred_at is not None                  # timestamp
    assert entry.actor_sub == employee_actor.sub          # user
    assert entry.actor_name == employee_actor.name
    assert entry.actor_role == "employee"
    assert entry.action == "SUBMIT_CLAIM"                 # action
    assert entry.entity_type == "Claim"                   # entity
    assert entry.entity_id == "EXP-2026-1234"             # entity_id
    assert entry.before == {"status": "Draft"}            # before
    assert entry.after == {"status": "Submitted"}         # after


def test_action_and_entity_accept_plain_strings(audit_service, admin_actor):
    """New actions must not require a code change to the enum."""
    entry = audit_service.record(
        actor=admin_actor, action="FUTURE_PHASE_ACTION", entity_type="FutureEntity",
        entity_id="x-1",
    )
    assert entry.action == "FUTURE_PHASE_ACTION"
    assert entry.entity_type == "FutureEntity"


def test_actor_name_falls_back_through_email_then_role(audit_service):
    from app.domain.actor import Actor

    named = audit_service.record(
        actor=Actor(role="admin", email="a@corp.com", display_name="Ada Admin"),
        action=AuditAction.POLICY_UPDATE, entity_type=AuditEntity.POLICY_RULE, entity_id="r",
    )
    assert named.actor_name == "Ada Admin"

    emailed = audit_service.record(
        actor=Actor(role="admin", email="a@corp.com"),
        action=AuditAction.POLICY_UPDATE, entity_type=AuditEntity.POLICY_RULE, entity_id="r",
    )
    assert emailed.actor_name == "a@corp.com"

    bare = audit_service.record(
        actor=Actor(role="auditor"),
        action=AuditAction.POLICY_UPDATE, entity_type=AuditEntity.POLICY_RULE, entity_id="r",
    )
    assert bare.actor_name == "auditor"


# --- request provenance ------------------------------------------------------


def test_request_and_correlation_ids_are_captured_from_the_context(audit_service, employee_actor):
    context = build_context(
        request_id="req-abc", correlation_id="corr-xyz", method="POST",
        path="/api/claims", client_ip="203.0.113.7", user_agent="pytest/1.0",
    )
    token = set_context(context)
    try:
        entry = audit_service.record(
            actor=employee_actor, action=AuditAction.SUBMIT_CLAIM,
            entity_type=AuditEntity.CLAIM, entity_id="EXP-1",
        )
    finally:
        reset_context(token)

    assert entry.request_id == "req-abc"
    assert entry.correlation_id == "corr-xyz"
    assert entry.ip_address == "203.0.113.7"
    assert entry.user_agent == "pytest/1.0"


def test_correlation_id_defaults_to_the_request_id(audit_service, employee_actor):
    context = build_context(request_id="req-only")
    token = set_context(context)
    try:
        entry = audit_service.record(
            actor=employee_actor, action=AuditAction.SUBMIT_CLAIM,
            entity_type=AuditEntity.CLAIM, entity_id="EXP-1",
        )
    finally:
        reset_context(token)
    assert entry.correlation_id == "req-only"


def test_records_written_outside_a_request_still_persist(audit_service, employee_actor):
    """Batch/CLI writes must not fail merely because there is no HTTP context."""
    entry = audit_service.record(
        actor=employee_actor, action=AuditAction.SUBMIT_CLAIM,
        entity_type=AuditEntity.CLAIM, entity_id="EXP-1",
    )
    assert entry.request_id is None
    assert entry.correlation_id is None


def test_long_user_agent_is_truncated_to_the_column_width(audit_service, employee_actor):
    token = set_context(build_context(request_id="r", user_agent="u" * 900))
    try:
        entry = audit_service.record(
            actor=employee_actor, action=AuditAction.SUBMIT_CLAIM,
            entity_type=AuditEntity.CLAIM, entity_id="EXP-1",
        )
    finally:
        reset_context(token)
    assert len(entry.user_agent) == 400


# --- queries -----------------------------------------------------------------


def test_search_returns_newest_first(audit_service, audit, employee_actor):
    for index in range(3):
        audit_service.record(
            actor=employee_actor, action=AuditAction.SUBMIT_CLAIM,
            entity_type=AuditEntity.CLAIM, entity_id=f"EXP-{index}",
            details=f"entry {index}",
        )
    entries = audit.search(entity_type="Claim", limit=3)
    timestamps = [entry.occurred_at for entry in entries]
    assert timestamps == sorted(timestamps, reverse=True)


def test_search_filters_by_action_entity_and_correlation(audit_service, audit, employee_actor):
    token = set_context(build_context(request_id="r-1", correlation_id="trace-99"))
    try:
        audit_service.record(
            actor=employee_actor, action=AuditAction.APPROVAL_ACTION,
            entity_type=AuditEntity.CLAIM, entity_id="EXP-FILTER",
        )
    finally:
        reset_context(token)

    assert all(e.action == "APPROVAL_ACTION" for e in audit.search(action="APPROVAL_ACTION"))
    assert [e.entity_id for e in audit.search(entity_id="EXP-FILTER")] == ["EXP-FILTER"]
    assert audit.search(correlation_id="trace-99")
    assert audit.search(action="NEVER_EMITTED") == []


def test_entity_timeline_and_count(audit_service, audit, employee_actor, manager_actor):
    claim_number = f"EXP-{uuid.uuid4().hex[:8]}"
    audit_service.record(
        actor=employee_actor, action=AuditAction.SUBMIT_CLAIM,
        entity_type=AuditEntity.CLAIM, entity_id=claim_number,
    )
    audit_service.record(
        actor=manager_actor, action=AuditAction.APPROVAL_ACTION,
        entity_type=AuditEntity.CLAIM, entity_id=claim_number,
    )

    timeline = audit_service.list_for_entity(AuditEntity.CLAIM, claim_number)
    assert len(timeline) == 2
    assert {entry.action for entry in timeline} == {"SUBMIT_CLAIM", "APPROVAL_ACTION"}
    assert audit.count_for_entity("Claim", claim_number) == 2


def test_search_paginates(audit_service, audit, employee_actor):
    for index in range(4):
        audit_service.record(
            actor=employee_actor, action="PAGING_TEST",
            entity_type=AuditEntity.CLAIM, entity_id=f"EXP-P{index}",
        )
    first = audit.search(action="PAGING_TEST", limit=2)
    second = audit.search(action="PAGING_TEST", limit=2, offset=2)
    assert len(first) == len(second) == 2
    assert {e.id for e in first}.isdisjoint({e.id for e in second})


def test_status_change_helper_snapshots_both_states(audit_service, employee_actor):
    entry = audit_service.record_status_change(
        actor=employee_actor, claim_id=uuid.uuid4(), claim_number="EXP-2026-1",
        from_status="Manager_Review", to_status="Approved",
    )
    assert entry.action == "CLAIM_STATUS_CHANGE"
    assert entry.before == {"status": "Manager_Review"}
    assert entry.after["status"] == "Approved"


# --- immutability ------------------------------------------------------------


def test_repository_refuses_to_offer_an_update_or_delete(audit, audit_service, employee_actor):
    """The API surface itself excludes mutation, so no call site can be tempted."""
    entry = audit_service.record(
        actor=employee_actor, action=AuditAction.SUBMIT_CLAIM,
        entity_type=AuditEntity.CLAIM, entity_id="EXP-1",
    )
    with pytest.raises(NotImplementedError, match="append-only"):
        audit.update(entry, details="tampered")
    with pytest.raises(NotImplementedError, match="append-only"):
        audit.delete(entry)


# --- transactional coupling --------------------------------------------------


def test_audit_row_rolls_back_with_its_business_change(db_session, audit_service, employee_actor):
    """An audit record must never survive a rolled-back action, or it would assert a lie."""
    entry = audit_service.record(
        actor=employee_actor, action=AuditAction.SUBMIT_CLAIM,
        entity_type=AuditEntity.CLAIM, entity_id="EXP-ROLLBACK",
    )
    entry_id = entry.id
    db_session.rollback()

    from app.models.audit import AuditLog

    assert db_session.get(AuditLog, entry_id) is None
