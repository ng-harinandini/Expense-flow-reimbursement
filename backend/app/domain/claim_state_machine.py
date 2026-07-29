"""The claim lifecycle state machine — single source of truth for legal transitions.

Canonical lifecycle (spec vocabulary on the left, this module's members on the right)::

    Draft            DRAFT
      ↓
    Submitted        SUBMITTED
      ↓
    Processing       PROCESSING          (policy evaluation + fraud screening)
      ↓
    Pending Review   MANAGER_REVIEW → FINANCE_REVIEW   (or AUTO_APPROVED / FLAGGED_FRAUD)
      ↓
    Approved         APPROVED
    Rejected         REJECTED            (terminal)
      ↓
    Reimbursed       REIMBURSED          (terminal)

**Two independent enforcement layers, deliberately.**

1. *Here* — :func:`assert_can_transition` is called by ``ClaimRepository.transition_status``,
   which is the only code path in the application that assigns ``Claim.status``. Services and
   routes cannot set the column directly.
2. *The database* — migration ``0002`` installs the ``claims_status_transition_guard`` trigger
   holding the same edge list, so even raw SQL or a future service that forgets the repository
   cannot write an illegal transition. ``tests/test_claim_state_machine.py`` asserts the two
   tables agree edge-for-edge, so drift fails the build rather than shipping.

Any change to :data:`ALLOWED_TRANSITIONS` therefore requires a matching migration.
"""

from __future__ import annotations

from typing import Optional

from app.domain.errors import ForbiddenError, InvalidStateTransitionError
from app.models.enums import ClaimStatus

# Role name used for transitions the *system* performs (submission pipeline, routing decisions),
# as opposed to a human decision. Not a Cognito role — never accepted from a token.
SYSTEM_ROLE = "system"


#: Legal ``current -> {allowed targets}`` edges. Absence of a key means "terminal".
ALLOWED_TRANSITIONS: dict[ClaimStatus, frozenset[ClaimStatus]] = {
    ClaimStatus.DRAFT: frozenset({ClaimStatus.SUBMITTED}),
    ClaimStatus.SUBMITTED: frozenset({ClaimStatus.PROCESSING}),
    ClaimStatus.PROCESSING: frozenset(
        {
            ClaimStatus.AUTO_APPROVED,
            ClaimStatus.MANAGER_REVIEW,
            ClaimStatus.FINANCE_REVIEW,
            ClaimStatus.FLAGGED_FRAUD,
        }
    ),
    # An auto-approved claim still needs payout, may be confirmed by a human approver, and may be
    # pulled back for review.
    ClaimStatus.AUTO_APPROVED: frozenset(
        {
            ClaimStatus.APPROVED,
            ClaimStatus.REIMBURSED,
            ClaimStatus.MANAGER_REVIEW,
            ClaimStatus.FLAGGED_FRAUD,
        }
    ),
    ClaimStatus.MANAGER_REVIEW: frozenset(
        {
            ClaimStatus.FINANCE_REVIEW,
            ClaimStatus.APPROVED,
            ClaimStatus.REJECTED,
            ClaimStatus.FLAGGED_FRAUD,
        }
    ),
    ClaimStatus.FINANCE_REVIEW: frozenset(
        {ClaimStatus.APPROVED, ClaimStatus.REJECTED, ClaimStatus.FLAGGED_FRAUD}
    ),
    ClaimStatus.APPROVED: frozenset({ClaimStatus.REIMBURSED, ClaimStatus.FLAGGED_FRAUD}),
    # A fraud investigation either clears the claim back into review, or ends it.
    ClaimStatus.FLAGGED_FRAUD: frozenset(
        {
            ClaimStatus.MANAGER_REVIEW,
            ClaimStatus.FINANCE_REVIEW,
            ClaimStatus.APPROVED,
            ClaimStatus.REJECTED,
        }
    ),
    ClaimStatus.REJECTED: frozenset(),
    ClaimStatus.REIMBURSED: frozenset(),
}

#: States from which nothing may move. Money has left, or the claim is closed.
TERMINAL_STATUSES: frozenset[ClaimStatus] = frozenset(
    status for status, targets in ALLOWED_TRANSITIONS.items() if not targets
)

#: Which roles may *request* each target state. ``SYSTEM_ROLE`` marks machine-driven steps.
ROLES_BY_TARGET: dict[ClaimStatus, frozenset[str]] = {
    ClaimStatus.SUBMITTED: frozenset({"employee", "admin"}),
    ClaimStatus.PROCESSING: frozenset({SYSTEM_ROLE}),
    ClaimStatus.AUTO_APPROVED: frozenset({SYSTEM_ROLE}),
    ClaimStatus.MANAGER_REVIEW: frozenset({SYSTEM_ROLE, "manager", "finance", "admin"}),
    ClaimStatus.FINANCE_REVIEW: frozenset({SYSTEM_ROLE, "manager", "finance", "admin"}),
    ClaimStatus.APPROVED: frozenset({"manager", "finance", "admin"}),
    ClaimStatus.REJECTED: frozenset({"manager", "finance", "admin"}),
    # Only finance/admin move money.
    ClaimStatus.REIMBURSED: frozenset({"finance", "admin"}),
    ClaimStatus.FLAGGED_FRAUD: frozenset({SYSTEM_ROLE, "manager", "finance", "admin", "auditor"}),
}

#: Target status -> the ``Claim`` timestamp column stamped when it is reached.
TIMESTAMP_FIELD_BY_STATUS: dict[ClaimStatus, str] = {
    ClaimStatus.SUBMITTED: "submitted_at",
    ClaimStatus.PROCESSING: "processing_started_at",
    ClaimStatus.MANAGER_REVIEW: "review_started_at",
    ClaimStatus.FINANCE_REVIEW: "review_started_at",
    ClaimStatus.APPROVED: "approved_at",
    ClaimStatus.AUTO_APPROVED: "approved_at",
    ClaimStatus.REJECTED: "rejected_at",
    ClaimStatus.REIMBURSED: "reimbursed_at",
}

#: Statuses in which the claim's own expense fields may still be edited by its owner.
EDITABLE_STATUSES: frozenset[ClaimStatus] = frozenset({ClaimStatus.DRAFT})

#: Statuses that represent an approval outcome — used by the "no changes after approval" rule.
APPROVED_STATUSES: frozenset[ClaimStatus] = frozenset(
    {ClaimStatus.APPROVED, ClaimStatus.AUTO_APPROVED, ClaimStatus.REIMBURSED}
)

#: Statuses a reviewer may act on (assignment, decisions).
REVIEWABLE_STATUSES: frozenset[ClaimStatus] = frozenset(
    {
        ClaimStatus.MANAGER_REVIEW,
        ClaimStatus.FINANCE_REVIEW,
        ClaimStatus.FLAGGED_FRAUD,
        ClaimStatus.AUTO_APPROVED,
        ClaimStatus.APPROVED,
    }
)


def allowed_targets(current: ClaimStatus) -> frozenset[ClaimStatus]:
    """Statuses reachable in one step from ``current``."""
    return ALLOWED_TRANSITIONS.get(current, frozenset())


def is_terminal(status: ClaimStatus) -> bool:
    return status in TERMINAL_STATUSES


def can_transition(current: ClaimStatus, target: ClaimStatus) -> bool:
    return target in allowed_targets(current)


def assert_can_transition(current: ClaimStatus, target: ClaimStatus) -> None:
    """Raise :class:`InvalidStateTransitionError` (→ HTTP 409) if the edge is not legal."""
    if not can_transition(current, target):
        raise InvalidStateTransitionError(
            "Claim",
            current.value,
            target.value,
            sorted(s.value for s in allowed_targets(current)),
        )


def assert_actor_may_transition(actor_role: Optional[str], target: ClaimStatus) -> None:
    """Raise :class:`ForbiddenError` (→ HTTP 403) if ``actor_role`` may not reach ``target``.

    This is the *lifecycle* authorization check and is independent of route-level
    ``require_roles`` gating: both must pass.
    """
    permitted = ROLES_BY_TARGET.get(target, frozenset())
    if actor_role not in permitted:
        raise ForbiddenError(
            f"Role '{actor_role or 'unknown'}' may not move a claim to '{target.value}'.",
            details={
                "requestedStatus": target.value,
                "permittedRoles": sorted(permitted),
            },
        )


def timestamp_field_for(target: ClaimStatus) -> Optional[str]:
    return TIMESTAMP_FIELD_BY_STATUS.get(target)


def transition_edges() -> list[tuple[str, str]]:
    """Flat ``(from_value, to_value)`` edge list — used by the trigger-parity test."""
    return sorted(
        (current.value, target.value)
        for current, targets in ALLOWED_TRANSITIONS.items()
        for target in targets
    )
