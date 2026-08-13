"""Claim state machine: legal edges, role authorization, and DB-trigger parity.

The parity test is the important one — it is what stops the Python table and the database trigger
from drifting apart, since either alone would be a bypass route.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.domain import claim_state_machine as fsm
from app.domain.errors import ForbiddenError, InvalidStateTransitionError
from app.models.enums import ClaimStatus

S = ClaimStatus


# --- wire compatibility ------------------------------------------------------


def test_status_values_match_the_frontend_contract():
    """Renaming any of these silently breaks the existing UI."""
    assert set(ClaimStatus.values()) == {
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
        "Withdrawn",
        "Failed",
    }


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Draft", S.DRAFT),
        ("DRAFT", S.DRAFT),
        ("Processing", S.PROCESSING),           # canonical spec spelling and wire value (same string)
        ("PROCESSING", S.PROCESSING),
        ("Pending_Review", S.MANAGER_REVIEW),  # canonical spec spelling
        ("pending review", S.MANAGER_REVIEW),
        ("Reimbursed", S.REIMBURSED),          # canonical spec spelling
        ("Disbursed", S.REIMBURSED),           # wire value
        ("manager_review", S.MANAGER_REVIEW),
    ],
)
def test_coerce_accepts_both_vocabularies(raw, expected):
    assert ClaimStatus.coerce(raw) is expected


def test_coerce_rejects_unknown_status():
    with pytest.raises(ValueError, match="not a valid ClaimStatus"):
        ClaimStatus.coerce("Teleported")


# --- edges -------------------------------------------------------------------


@pytest.mark.parametrize(
    "current, target",
    [
        (S.DRAFT, S.SUBMITTED),
        (S.SUBMITTED, S.PROCESSING),
        (S.PROCESSING, S.AUTO_APPROVED),
        (S.PROCESSING, S.MANAGER_REVIEW),
        (S.PROCESSING, S.FLAGGED_FRAUD),
        (S.PROCESSING, S.FAILED),
        (S.MANAGER_REVIEW, S.FINANCE_REVIEW),
        (S.MANAGER_REVIEW, S.APPROVED),
        (S.MANAGER_REVIEW, S.REJECTED),
        (S.FINANCE_REVIEW, S.APPROVED),
        (S.APPROVED, S.FLAGGED_FRAUD),
        (S.FLAGGED_FRAUD, S.REJECTED),
        (S.FLAGGED_FRAUD, S.MANAGER_REVIEW),
    ],
)
def test_legal_transitions(current, target):
    assert fsm.can_transition(current, target)


@pytest.mark.parametrize(
    "current, target",
    [
        (S.DRAFT, S.APPROVED),          # cannot skip review
        (S.DRAFT, S.REIMBURSED),        # cannot pay an unsubmitted claim
        (S.SUBMITTED, S.APPROVED),      # must be processed first
        (S.SUBMITTED, S.REIMBURSED),
        (S.PROCESSING, S.REIMBURSED),   # no payout without a decision
        (S.PROCESSING, S.REJECTED),     # rejection is a human act
        (S.FAILED, S.PROCESSING),       # terminal
        (S.FAILED, S.SUBMITTED),
        (S.MANAGER_REVIEW, S.REIMBURSED),
        (S.APPROVED, S.REIMBURSED),     # retired: Approve is now the final reviewer step
        (S.AUTO_APPROVED, S.REIMBURSED),
        (S.REJECTED, S.APPROVED),       # terminal
        (S.REJECTED, S.SUBMITTED),
        (S.REIMBURSED, S.REJECTED),     # terminal
        (S.REIMBURSED, S.APPROVED),
        (S.APPROVED, S.REJECTED),       # reverse a decision only via fraud flow
        (S.DRAFT, S.DRAFT),             # a no-op is not a transition
    ],
)
def test_illegal_transitions(current, target):
    assert not fsm.can_transition(current, target)
    with pytest.raises(InvalidStateTransitionError):
        fsm.assert_can_transition(current, target)


def test_invalid_transition_error_reports_the_legal_alternatives():
    with pytest.raises(InvalidStateTransitionError) as raised:
        fsm.assert_can_transition(S.DRAFT, S.REIMBURSED)

    details = raised.value.details
    assert details["currentStatus"] == "Draft"
    assert details["requestedStatus"] == "Disbursed"
    assert details["allowedNextStatuses"] == ["Submitted"]


def test_terminal_statuses():
    assert fsm.TERMINAL_STATUSES == frozenset(
        {S.REJECTED, S.REIMBURSED, S.WITHDRAWN, S.FAILED}
    )
    assert fsm.is_terminal(S.REJECTED)
    assert fsm.is_terminal(S.FAILED)
    assert not fsm.is_terminal(S.APPROVED)


def test_every_non_terminal_status_can_reach_a_terminal_state():
    """No state may be a dead end: a claim must always be closable."""
    for status in ClaimStatus:
        reachable, frontier = set(), [status]
        while frontier:
            current = frontier.pop()
            for target in fsm.allowed_targets(current):
                if target not in reachable:
                    reachable.add(target)
                    frontier.append(target)
        if not fsm.is_terminal(status):
            assert reachable & fsm.TERMINAL_STATUSES, f"{status} cannot be closed"


def test_only_draft_is_editable():
    assert fsm.EDITABLE_STATUSES == frozenset({S.DRAFT})


# --- role authorization ------------------------------------------------------


@pytest.mark.parametrize(
    "role, target",
    [
        ("employee", S.SUBMITTED),
        ("manager", S.APPROVED),
        ("manager", S.REJECTED),
        ("finance", S.APPROVED),
        ("finance", S.REIMBURSED),
        ("admin", S.REIMBURSED),
        ("auditor", S.FLAGGED_FRAUD),
        (fsm.SYSTEM_ROLE, S.PROCESSING),
        (fsm.SYSTEM_ROLE, S.AUTO_APPROVED),
        (fsm.SYSTEM_ROLE, S.FAILED),
    ],
)
def test_roles_permitted_for_target(role, target):
    fsm.assert_actor_may_transition(role, target)  # must not raise


@pytest.mark.parametrize(
    "role, target",
    [
        ("employee", S.APPROVED),        # cannot approve their own claim
        ("employee", S.REIMBURSED),
        ("manager", S.REIMBURSED),       # only finance/admin move money
        ("auditor", S.APPROVED),         # read-only oversight role
        ("auditor", S.REIMBURSED),
        ("employee", S.PROCESSING),      # machine-only step
        ("manager", S.AUTO_APPROVED),    # auto-approval is not a human decision
        ("employee", S.FLAGGED_FRAUD),
        ("employee", S.FAILED),          # a failed pipeline run is not a human decision
        ("manager", S.FAILED),
    ],
)
def test_roles_forbidden_for_target(role, target):
    with pytest.raises(ForbiddenError):
        fsm.assert_actor_may_transition(role, target)


def test_system_role_is_not_a_cognito_role():
    """``system`` must never be assignable to a real user."""
    from app.core.deps import VALID_ROLES

    assert fsm.SYSTEM_ROLE not in VALID_ROLES


def test_timestamp_field_mapping():
    assert fsm.timestamp_field_for(S.SUBMITTED) == "submitted_at"
    assert fsm.timestamp_field_for(S.REIMBURSED) == "reimbursed_at"
    assert fsm.timestamp_field_for(S.DRAFT) is None


# --- database trigger parity -------------------------------------------------


def test_database_trigger_matches_python_transition_table(db_engine):
    """The trigger's edge list must equal ``ALLOWED_TRANSITIONS``, exactly.

    Reads the installed function body and extracts its ``('From','To')`` pairs. A mismatch means
    either the migration or the state machine changed without the other.
    """
    with db_engine.connect() as connection:
        body = connection.execute(
            text("SELECT prosrc FROM pg_proc WHERE proname = 'claims_status_transition_guard'")
        ).scalar_one()

    import re

    trigger_edges = sorted(
        (match.group(1), match.group(2))
        for match in re.finditer(r"\('([^']+)','([^']+)'\)", body)
    )
    assert trigger_edges == fsm.transition_edges()
