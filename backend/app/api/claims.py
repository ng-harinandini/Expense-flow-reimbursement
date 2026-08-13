"""Claims API — thin HTTP adapter over :class:`~app.services.claim_service.ClaimService`.

The route layer does four things and nothing else: authorize the caller, validate the request
shape, call one service method, commit, and serialize. No SQL, no business rules, no status
assignment — those live in the service, repository, and state machine respectively.

Error mapping is handled centrally (``app.core.errors``), so no route catches domain errors:

    unknown claim / employee        -> 404
    not the caller's claim          -> 404 (employees) / 403 (explicit ownership failure)
    illegal lifecycle transition    -> 409  (with the legal next states in the body)
    withdrawal of a flagged claim   -> 403
    duplicate submission            -> 409
    receipt already claimed         -> 409
    concurrent modification         -> 409
    business-rule violation         -> 422

Response bodies keep the exact ``ExpenseClaim`` field names the frontend already consumes
(``app.services.mappers``); the new keys are additive.
"""

from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Query, status

from app.core.database import get_session_factory
from app.core.deps import (
    CurrentUser,
    build_claim_service,
    get_actor,
    get_claim_service,
    get_unit_of_work,
    require_roles,
)
from app.core.logging import get_logger
from app.core.unit_of_work import UnitOfWork
from app.domain.actor import Actor
from app.schemas.schemas import (
    ActionRequestSchema,
    AssignReviewerSchema,
    ClaimStatusHistorySchema,
    CommentCreateSchema,
    ExpenseClaimCreateSchema,
    ExpenseClaimUpdateSchema,
    ItemDecisionRequestSchema,
    WithdrawClaimSchema,
)
from app.services.claim_service import ClaimService
from app.services.mappers import claim_to_dict

logger = get_logger(__name__)

router = APIRouter(prefix="/claims", tags=["Claims"])

# Reviewer-only notes are hidden when the claim owner is the audience.
_INTERNAL_VISIBLE_ROLES = frozenset({"manager", "finance", "admin", "auditor"})


def _serialize(claim, actor: Actor) -> dict[str, Any]:
    return claim_to_dict(
        claim, include_internal_comments=actor.role in _INTERNAL_VISIBLE_ROLES
    )


@router.get("", response_model=List[dict])
def get_claims(
    category: Optional[str] = None,
    status: Optional[List[str]] = Query(None),
    employeeId: Optional[str] = None,
    riskLevel: Optional[str] = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    actor: Actor = Depends(get_actor),
    service: ClaimService = Depends(get_claim_service),
):
    """List claims. Employees are scoped to their own regardless of the filters they send."""
    claims = service.list_claims(
        actor=actor,
        category=category,
        status=status,
        employee_code=employeeId,
        risk_level=riskLevel,
        limit=limit,
        offset=offset,
    )
    return [_serialize(claim, actor) for claim in claims]


@router.get("/team", response_model=List[dict])
def get_team_claims(
    category: Optional[str] = None,
    status: Optional[str] = None,
    riskLevel: Optional[str] = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current: CurrentUser = Depends(require_roles("manager")),
    service: ClaimService = Depends(get_claim_service),
):
    """Claims filed by the caller's direct reports. Same shape as ``GET /claims``, scoped to the
    manager's own team instead of the caller's own claims.

    Declared ahead of ``/{claim_id}`` so this static path is never shadowed by that dynamic one.
    """
    actor = Actor.from_current_user(current)
    claims = service.list_claims_for_manager(
        actor=actor,
        category=category,
        status=status,
        risk_level=riskLevel,
        limit=limit,
        offset=offset,
    )
    return [_serialize(claim, actor) for claim in claims]


@router.get("/{claim_id}", response_model=dict)
def get_claim(
    claim_id: str,
    actor: Actor = Depends(get_actor),
    service: ClaimService = Depends(get_claim_service),
):
    """One claim by id or claim number."""
    claim = service.get_claim_for_actor(claim_id, actor=actor)
    return _serialize(claim, actor)


@router.get("/{claim_id}/history", response_model=List[ClaimStatusHistorySchema])
def get_claim_history(
    claim_id: str,
    actor: Actor = Depends(get_actor),
    service: ClaimService = Depends(get_claim_service),
):
    """The claim's durable lifecycle history, oldest first."""
    claim = service.get_claim_for_actor(claim_id, actor=actor)
    return [
        ClaimStatusHistorySchema(
            sequence=entry.sequence,
            fromStatus=entry.from_status.value if entry.from_status else None,
            toStatus=entry.to_status.value,
            stepName=entry.step_name,
            action=entry.action,
            outcome=entry.outcome,
            notes=entry.notes,
            actorName=entry.actor_name,
            actorRole=entry.actor_role,
            occurredAt=entry.occurred_at.isoformat() if entry.occurred_at else None,
            requestId=entry.request_id,
            correlationId=entry.correlation_id,
        )
        for entry in service.list_history(claim)
    ]


def _run_claim_pipeline(claim_id: Any, actor: Actor) -> None:
    """Background task: Submitted → Processing → routed, in its own session/transaction.

    Runs after ``POST /claims`` has already responded, so it cannot reuse the request's session
    (closed by then) — it opens a fresh one via the same ``sessionmaker`` used elsewhere for
    out-of-request DB access. On any unhandled exception the claim is routed to ``Failed`` (with
    ``hold_reason`` set to a short message) in a second, fresh transaction, so a pipeline bug never
    leaves a claim stuck in ``Processing`` forever — see ``ClaimStatus.FAILED`` /
    ``ClaimService.mark_claim_failed``.
    """
    session_factory = get_session_factory()
    session = session_factory()
    try:
        service = build_claim_service(session)
        claim = service.get_claim_for_actor(str(claim_id), actor=actor)
        # Committed on its own, before the evaluation body: if that body then raises and its
        # transaction rolls back, the claim is left at `Processing` (not `Submitted`), which is the
        # only state `mark_claim_failed`'s `Processing -> Failed` edge can move it from.
        service.begin_processing(claim, actor=actor)
        session.commit()

        service.process_submitted_claim(claim, actor=actor)
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.error(
            "claim.pipeline.failed",
            extra={"claimId": str(claim_id)},
            exc_info=True,
        )
        try:
            failure_session = session_factory()
            try:
                failure_service = build_claim_service(failure_session)
                failed_claim = failure_service.get_claim_for_actor(str(claim_id), actor=actor)
                failure_service.mark_claim_failed(
                    failed_claim,
                    actor=actor,
                    reason=f"Automated processing failed: {exc}"[:500],
                )
                failure_session.commit()
            finally:
                failure_session.close()
        except Exception:
            logger.error(
                "claim.pipeline.failed_status_write_failed",
                extra={"claimId": str(claim_id)},
                exc_info=True,
            )
    finally:
        session.close()


@router.post("", status_code=status.HTTP_201_CREATED, response_model=dict)
def create_claim(
    payload: ExpenseClaimCreateSchema,
    background_tasks: BackgroundTasks,
    current: CurrentUser = Depends(require_roles("employee")),
    service: ClaimService = Depends(get_claim_service),
    uow: UnitOfWork = Depends(get_unit_of_work),
):
    """Create and submit a claim.

    Writes Draft → Submitted in this request's transaction and responds immediately with that
    status; the AI pipeline (Processing → routed) then runs as a background task in its own
    session, so the caller no longer waits out the ~20s of policy/fraud/classification calls. The
    owner is the authenticated employee; any ``employeeId`` in the body is ignored.

    Poll ``GET /claims/{id}`` for the terminal status (``Auto_Approved``/``Manager_Review``/
    ``Finance_Review``/``Flagged_Fraud``), or ``Failed`` if the pipeline raised.
    """
    actor = Actor.from_current_user(current)
    claim, actor = service.create_and_submit_claim(
        payload.model_dump(exclude_none=False), actor=actor
    )
    uow.commit()
    background_tasks.add_task(_run_claim_pipeline, claim.id, actor)
    return _serialize(claim, actor)


@router.patch("/{claim_id}", response_model=dict)
def update_claim(
    claim_id: str,
    payload: ExpenseClaimUpdateSchema,
    actor: Actor = Depends(get_actor),
    service: ClaimService = Depends(get_claim_service),
    uow: UnitOfWork = Depends(get_unit_of_work),
):
    """Edit a claim that is still a draft. Returns 409 once it has been submitted."""
    claim = service.update_draft(
        claim_id, changes=payload.model_dump(exclude_unset=True), actor=actor
    )
    uow.commit()
    return _serialize(claim, actor)


@router.post("/{claim_id}/action", response_model=dict)
def execute_claim_action(
    claim_id: str,
    payload: ActionRequestSchema,
    current: CurrentUser = Depends(require_roles("manager", "finance", "admin")),
    service: ClaimService = Depends(get_claim_service),
    uow: UnitOfWork = Depends(get_unit_of_work),
):
    """Execute ``APPROVE``, ``REJECT``, or ``FLAG_FRAUD``.

    The actor is the authenticated caller — ``actorName``/``actorRole`` in the body are ignored.
    ``APPROVE`` is the final reviewer step (no separate disbursement action). An action that is
    illegal from the claim's current state returns 409 listing the states that are reachable.
    """
    actor = Actor.from_current_user(current)
    claim = service.execute_action(
        claim_id,
        action=payload.action,
        actor=actor,
        notes=payload.notes,
        expected_version=payload.expectedVersion,
    )
    uow.commit()
    return _serialize(claim, actor)


@router.post("/{claim_id}/withdraw", response_model=dict)
def withdraw_claim(
    claim_id: str,
    # Defaulted so a bodyless POST is valid — a withdrawal needs no justification.
    payload: WithdrawClaimSchema,
    actor: Actor = Depends(get_actor),
    service: ClaimService = Depends(get_claim_service),
    uow: UnitOfWork = Depends(get_unit_of_work),
):
    "Withdraw claim by employee"
    claim = service.withdraw_claim(
        claim_id,
        actor=actor,
        reason=payload.reason,
        expected_version=payload.expectedVersion,
    )
    uow.commit()
    return _serialize(claim, actor)


@router.post("/{claim_id}/items/{item_id}/decision", response_model=dict)
def decide_expense_item(
    claim_id: str,
    item_id: str,
    payload: ItemDecisionRequestSchema,
    current: CurrentUser = Depends(require_roles("manager", "finance", "admin")),
    service: ClaimService = Depends(get_claim_service),
    uow: UnitOfWork = Depends(get_unit_of_work),
):
    """Approve or reject one expense item of a claim.

    The claim itself moves only once every item has been decided: approved if any item survived,
    rejected if all were rejected. A rejection requires a reason.
    """
    actor = Actor.from_current_user(current)
    claim = service.decide_item(
        claim_id,
        item_id,
        action=payload.action,
        actor=actor,
        notes=payload.notes,
        expected_version=payload.expectedVersion,
    )
    uow.commit()
    return _serialize(claim, actor)


@router.post("/{claim_id}/assign", response_model=dict)
def assign_reviewer(
    claim_id: str,
    payload: AssignReviewerSchema,
    current: CurrentUser = Depends(require_roles("manager", "finance", "admin")),
    service: ClaimService = Depends(get_claim_service),
    uow: UnitOfWork = Depends(get_unit_of_work),
):
    """Assign the reviewer responsible for the next decision (``null`` clears it)."""
    actor = Actor.from_current_user(current)
    claim = service.assign_reviewer(
        claim_id, reviewer_code=payload.reviewerId, actor=actor, notes=payload.notes
    )
    uow.commit()
    return _serialize(claim, actor)


@router.post("/{claim_id}/comments", status_code=status.HTTP_201_CREATED, response_model=dict)
def add_comment(
    claim_id: str,
    payload: CommentCreateSchema,
    actor: Actor = Depends(get_actor),
    service: ClaimService = Depends(get_claim_service),
    uow: UnitOfWork = Depends(get_unit_of_work),
):
    """Comment on a claim. Employees may only comment on their own; internal notes are staff-only."""
    claim = service.add_comment(
        claim_id, body=payload.text, actor=actor, is_internal=payload.isInternal
    )
    uow.commit()
    return _serialize(claim, actor)
