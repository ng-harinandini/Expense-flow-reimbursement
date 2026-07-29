"""Claim persistence, including the single guarded entry point for status changes.

``transition_status`` is the **only** place in the application that assigns ``Claim.status``.
Every lifecycle move therefore always:

  1. validates the edge against :mod:`app.domain.claim_state_machine`,
  2. checks the actor's role may reach the target state,
  3. appends a ``claim_status_history`` row (gap-free ``sequence``, actor, request ids),
  4. stamps the matching lifecycle timestamp,
  5. lets the ``version`` column detect a concurrent writer.

Writing the audit record is the caller's responsibility (``ClaimService``) because only it knows
the business narrative; the transaction is shared, so the two commit together or not at all.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional, Sequence

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.core.context import current_correlation_id, current_request_id
from app.core.logging import get_logger
from app.domain import claim_state_machine as fsm
from app.domain.errors import ImmutableEntityError
from app.models.claim import Attachment, Claim, ClaimStatusHistory, Comment
from app.models.enums import AttachmentKind, ClaimStatus, FraudRiskLevel
from app.models.fraud import FraudResult
from app.repositories.base import BaseRepository

logger = get_logger(__name__)

# Amounts within this tolerance are "the same amount" for duplicate detection.
DUPLICATE_AMOUNT_TOLERANCE = Decimal("0.01")


@dataclass(frozen=True)
class ClaimQuery:
    """Filter set for claim listing. All fields optional; ``None`` means "no filter"."""

    employee_id: Optional[uuid.UUID] = None
    category: Optional[str] = None
    status: Optional[ClaimStatus] = None
    risk_level: Optional[FraudRiskLevel] = None
    assigned_reviewer_id: Optional[uuid.UUID] = None
    expense_date_from: Optional[date] = None
    expense_date_to: Optional[date] = None
    limit: Optional[int] = None
    offset: int = 0


class ClaimRepository(BaseRepository[Claim]):
    model = Claim

    def __init__(self, session: Session) -> None:
        super().__init__(session)

    # --- identity ------------------------------------------------------------

    def next_claim_number(self, *, year: Optional[int] = None) -> str:
        """Allocate a unique human-facing claim number from ``claim_number_seq``."""
        sequence_value = self.session.execute(
            text("SELECT nextval('claim_number_seq')")
        ).scalar_one()
        return f"EXP-{year or datetime.now(timezone.utc).year}-{int(sequence_value)}"

    def get_by_number(self, claim_number: str) -> Optional[Claim]:
        return self._one_or_none(select(Claim).where(Claim.claim_number == claim_number))

    def get_by_id_or_number(self, identifier: str) -> Optional[Claim]:
        """Resolve either a UUID or a claim number — the API accepts both."""
        as_uuid = self._coerce_uuid(identifier)
        if as_uuid is not None:
            found = self.session.get(Claim, as_uuid)
            if found is not None:
                return found
        return self.get_by_number(str(identifier))

    # --- creation ------------------------------------------------------------

    def create_claim(self, claim: Claim, *, actor_sub: Optional[str] = None,
                     actor_name: Optional[str] = None,
                     actor_role: Optional[str] = None) -> Claim:
        """Insert a new claim in ``Draft`` and open its history with a creation row."""
        claim.status = ClaimStatus.DRAFT
        claim.created_by_sub = actor_sub
        claim.updated_by_sub = actor_sub
        self.session.add(claim)
        self.session.flush()

        self._append_history(
            claim,
            from_status=None,
            to_status=ClaimStatus.DRAFT,
            actor_sub=actor_sub,
            actor_name=actor_name,
            actor_role=actor_role,
            step_name="Create Claim",
            action=f"Created draft claim {claim.claim_number}",
            notes=None,
        )
        self.session.flush()
        return claim

    # --- queries -------------------------------------------------------------

    def search(self, query: ClaimQuery) -> Sequence[Claim]:
        stmt = select(Claim).order_by(Claim.created_at.desc())

        if query.employee_id is not None:
            stmt = stmt.where(Claim.employee_id == query.employee_id)
        if query.category:
            stmt = stmt.where(Claim.category == query.category)
        if query.status is not None:
            stmt = stmt.where(Claim.status == query.status)
        if query.assigned_reviewer_id is not None:
            stmt = stmt.where(Claim.assigned_reviewer_id == query.assigned_reviewer_id)
        if query.expense_date_from is not None:
            stmt = stmt.where(Claim.expense_date >= query.expense_date_from)
        if query.expense_date_to is not None:
            stmt = stmt.where(Claim.expense_date <= query.expense_date_to)

        if query.risk_level is not None:
            # Match on the *latest* screening per claim, never a superseded one. ROW_NUMBER (rather
            # than MAX(evaluated_at)) keeps exactly one winner even if two verdicts share a
            # timestamp, so the filter can't match a stale verdict.
            ranked = (
                select(
                    FraudResult.claim_id.label("claim_id"),
                    FraudResult.risk_level.label("risk_level"),
                    func.row_number()
                    .over(
                        partition_by=FraudResult.claim_id,
                        order_by=(
                            FraudResult.evaluated_at.desc(),
                            FraudResult.created_at.desc(),
                            FraudResult.id.desc(),
                        ),
                    )
                    .label("rank"),
                )
                .subquery()
            )
            stmt = stmt.join(ranked, ranked.c.claim_id == Claim.id).where(
                ranked.c.rank == 1, ranked.c.risk_level == query.risk_level
            )

        return self._all(self._paginate(stmt, limit=query.limit, offset=query.offset))

    def count_for_employee(self, employee_id: uuid.UUID) -> int:
        return int(
            self.session.execute(
                select(func.count()).select_from(Claim).where(Claim.employee_id == employee_id)
            ).scalar_one()
        )

    def list_for_employee(
        self, employee_id: uuid.UUID, *, exclude_claim_id: Optional[uuid.UUID] = None,
        limit: Optional[int] = None,
    ) -> Sequence[Claim]:
        """An employee's claim history — the corpus fraud screening compares against."""
        stmt = select(Claim).where(Claim.employee_id == employee_id)
        if exclude_claim_id is not None:
            stmt = stmt.where(Claim.id != exclude_claim_id)
        stmt = stmt.order_by(Claim.expense_date.desc())
        return self._all(self._paginate(stmt, limit=limit))

    def find_duplicate_claims(
        self,
        *,
        employee_id: uuid.UUID,
        merchant_vendor: str,
        expense_date: date,
        amount_usd: Decimal,
        exclude_claim_id: Optional[uuid.UUID] = None,
        include_rejected: bool = False,
    ) -> Sequence[Claim]:
        """Claims that look like the same expense filed twice.

        Same employee, same vendor (case/whitespace-insensitive), same expense date, and the same
        amount within :data:`DUPLICATE_AMOUNT_TOLERANCE`. Rejected claims are excluded by default:
        re-filing a corrected version of a rejected claim is legitimate.
        """
        vendor = (merchant_vendor or "").strip().lower()
        amount = Decimal(amount_usd)

        stmt = select(Claim).where(
            Claim.employee_id == employee_id,
            Claim.expense_date == expense_date,
            func.lower(func.btrim(Claim.merchant_vendor)) == vendor,
            func.abs(Claim.amount_usd - amount) < DUPLICATE_AMOUNT_TOLERANCE,
        )
        if not include_rejected:
            stmt = stmt.where(Claim.status != ClaimStatus.REJECTED)
        if exclude_claim_id is not None:
            stmt = stmt.where(Claim.id != exclude_claim_id)

        return self._all(stmt.order_by(Claim.created_at.desc()))

    def find_same_day_vendor_claims(
        self,
        *,
        employee_id: uuid.UUID,
        merchant_vendor: str,
        expense_date: date,
        exclude_claim_id: Optional[uuid.UUID] = None,
    ) -> Sequence[Claim]:
        """Same employee + vendor + day, any amount — the split-transaction probe."""
        vendor = (merchant_vendor or "").strip().lower()
        stmt = select(Claim).where(
            Claim.employee_id == employee_id,
            Claim.expense_date == expense_date,
            func.lower(func.btrim(Claim.merchant_vendor)) == vendor,
            Claim.status != ClaimStatus.REJECTED,
        )
        if exclude_claim_id is not None:
            stmt = stmt.where(Claim.id != exclude_claim_id)
        return self._all(stmt.order_by(Claim.created_at.desc()))

    def find_by_receipt_id(self, receipt_id: uuid.UUID) -> Optional[Claim]:
        """The claim already using this receipt, if any (``receipt_id`` is UNIQUE)."""
        return self._one_or_none(select(Claim).where(Claim.receipt_id == receipt_id))

    def list_review_queue(
        self, *, reviewer_id: Optional[uuid.UUID] = None, limit: Optional[int] = None
    ) -> Sequence[Claim]:
        """Claims awaiting a human decision, oldest submission first."""
        stmt = select(Claim).where(
            Claim.status.in_(
                [
                    ClaimStatus.MANAGER_REVIEW,
                    ClaimStatus.FINANCE_REVIEW,
                    ClaimStatus.FLAGGED_FRAUD,
                ]
            )
        )
        if reviewer_id is not None:
            stmt = stmt.where(
                or_(
                    Claim.assigned_reviewer_id == reviewer_id,
                    Claim.assigned_reviewer_id.is_(None),
                )
            )
        return self._all(self._paginate(stmt.order_by(Claim.submitted_at.asc()), limit=limit))

    # --- mutations -----------------------------------------------------------

    def update_fields(self, claim: Claim, *, actor_sub: Optional[str] = None,
                      **changes) -> Claim:
        """Edit a claim's own expense fields.

        Permitted only while the claim is in an editable state
        (:data:`~app.domain.claim_state_machine.EDITABLE_STATUSES`) — this is the storage-level
        half of "a claim cannot be modified after approval".
        """
        if claim.status not in fsm.EDITABLE_STATUSES:
            raise ImmutableEntityError(
                f"Claim {claim.claim_number} is '{claim.status.value}' and can no longer be edited.",
                details={
                    "claimNumber": claim.claim_number,
                    "currentStatus": claim.status.value,
                    "editableStatuses": sorted(s.value for s in fsm.EDITABLE_STATUSES),
                },
            )
        # Guard against a caller sneaking a status change through the generic updater.
        changes.pop("status", None)
        claim.updated_by_sub = actor_sub
        return self.update(claim, **changes)

    def transition_status(
        self,
        claim: Claim,
        target: ClaimStatus,
        *,
        actor_role: str,
        actor_sub: Optional[str] = None,
        actor_name: Optional[str] = None,
        step_name: Optional[str] = None,
        action: Optional[str] = None,
        notes: Optional[str] = None,
        outcome: str = "SUCCESS",
    ) -> Claim:
        """Move ``claim`` to ``target``. The only sanctioned status writer.

        Raises :class:`~app.domain.errors.InvalidStateTransitionError` (409) for an illegal edge
        and :class:`~app.domain.errors.ForbiddenError` (403) when ``actor_role`` may not reach
        ``target``. The database trigger installed by migration ``0002`` enforces the same edge
        list, so a bypass attempt fails there too.
        """
        current = claim.status
        fsm.assert_can_transition(current, target)
        fsm.assert_actor_may_transition(actor_role, target)

        claim.status = target
        claim.updated_by_sub = actor_sub or claim.updated_by_sub

        now = datetime.now(timezone.utc)
        timestamp_field = fsm.timestamp_field_for(target)
        # Preserve the first time a state was reached (re-entering review keeps the original).
        if timestamp_field and getattr(claim, timestamp_field, None) is None:
            setattr(claim, timestamp_field, now)
        if target in (
            ClaimStatus.APPROVED,
            ClaimStatus.REJECTED,
            ClaimStatus.AUTO_APPROVED,
        ):
            claim.decided_at = now
            claim.decided_by_sub = actor_sub or claim.decided_by_sub

        self._append_history(
            claim,
            from_status=current,
            to_status=target,
            actor_sub=actor_sub,
            actor_name=actor_name,
            actor_role=actor_role,
            step_name=step_name or f"Status: {target.value}",
            action=action or f"Moved claim {claim.claim_number} to {target.value}",
            notes=notes,
            outcome=outcome,
        )
        self.session.flush()

        logger.info(
            "claim.status_changed",
            extra={
                "claimId": str(claim.id),
                "claimNumber": claim.claim_number,
                "fromStatus": current.value,
                "toStatus": target.value,
                "actorRole": actor_role,
            },
        )
        return claim

    def assign_reviewer(
        self,
        claim: Claim,
        reviewer_id: Optional[uuid.UUID],
        *,
        actor_sub: Optional[str] = None,
        actor_name: Optional[str] = None,
        actor_role: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Claim:
        """Set (or clear, with ``None``) the reviewer that owns the claim's next decision.

        Assignment is not a status change, so it records a history row with an unchanged
        ``from_status``/``to_status`` rather than going through ``transition_status``.
        """
        claim.assigned_reviewer_id = reviewer_id
        claim.assigned_at = datetime.now(timezone.utc) if reviewer_id else None
        claim.updated_by_sub = actor_sub or claim.updated_by_sub

        self._append_history(
            claim,
            from_status=claim.status,
            to_status=claim.status,
            actor_sub=actor_sub,
            actor_name=actor_name,
            actor_role=actor_role,
            step_name="Assign Reviewer",
            action=(
                f"Assigned reviewer {reviewer_id}" if reviewer_id else "Cleared reviewer assignment"
            ),
            notes=notes,
        )
        self.session.flush()
        return claim

    def approve_claim(
        self,
        claim: Claim,
        *,
        actor_role: str,
        actor_sub: Optional[str] = None,
        actor_name: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Claim:
        """Record an approval decision (``-> Approved``)."""
        claim.decision_notes = notes or claim.decision_notes
        return self.transition_status(
            claim,
            ClaimStatus.APPROVED,
            actor_role=actor_role,
            actor_sub=actor_sub,
            actor_name=actor_name,
            step_name="Approval Decision",
            action=f"Approved claim {claim.claim_number}",
            notes=notes,
        )

    def reject_claim(
        self,
        claim: Claim,
        *,
        actor_role: str,
        actor_sub: Optional[str] = None,
        actor_name: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Claim:
        """Record a rejection (``-> Rejected``, terminal)."""
        claim.rejection_reason = reason or claim.rejection_reason
        claim.decision_notes = reason or claim.decision_notes
        return self.transition_status(
            claim,
            ClaimStatus.REJECTED,
            actor_role=actor_role,
            actor_sub=actor_sub,
            actor_name=actor_name,
            step_name="Approval Decision",
            action=f"Rejected claim {claim.claim_number}",
            notes=reason,
            outcome="WARNING",
        )

    def mark_reimbursed(
        self,
        claim: Claim,
        *,
        actor_role: str,
        actor_sub: Optional[str] = None,
        actor_name: Optional[str] = None,
        reference: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Claim:
        """Record disbursement (``-> Disbursed``/Reimbursed, terminal)."""
        claim.reimbursement_reference = reference or claim.reimbursement_reference
        return self.transition_status(
            claim,
            ClaimStatus.REIMBURSED,
            actor_role=actor_role,
            actor_sub=actor_sub,
            actor_name=actor_name,
            step_name="Reimbursement",
            action=f"Reimbursed claim {claim.claim_number}",
            notes=notes,
        )

    def flag_fraud(
        self,
        claim: Claim,
        *,
        actor_role: str,
        actor_sub: Optional[str] = None,
        actor_name: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Claim:
        """Route the claim into fraud investigation (``-> Flagged_Fraud``)."""
        return self.transition_status(
            claim,
            ClaimStatus.FLAGGED_FRAUD,
            actor_role=actor_role,
            actor_sub=actor_sub,
            actor_name=actor_name,
            step_name="Fraud Investigation",
            action=f"Flagged claim {claim.claim_number} for fraud investigation",
            notes=notes,
            outcome="WARNING",
        )

    # --- children ------------------------------------------------------------

    def add_comment(
        self,
        claim: Claim,
        *,
        body: str,
        author_name: str,
        author_role: str,
        author_sub: Optional[str] = None,
        is_internal: bool = False,
    ) -> Comment:
        comment = Comment(
            claim_id=claim.id,
            author_sub=author_sub,
            author_name=author_name,
            author_role=author_role,
            body=body,
            is_internal=is_internal,
        )
        self.session.add(comment)
        self.session.flush()
        return comment

    def add_attachment(
        self,
        claim: Claim,
        *,
        file_name: str,
        kind: AttachmentKind = AttachmentKind.SUPPORTING,
        receipt_id: Optional[uuid.UUID] = None,
        content_type: Optional[str] = None,
        file_size_bytes: Optional[int] = None,
        s3_bucket: Optional[str] = None,
        s3_key: Optional[str] = None,
        s3_region: Optional[str] = None,
        uploaded_by_sub: Optional[str] = None,
    ) -> Attachment:
        attachment = Attachment(
            claim_id=claim.id,
            receipt_id=receipt_id,
            kind=kind,
            file_name=file_name,
            content_type=content_type,
            file_size_bytes=file_size_bytes,
            s3_bucket=s3_bucket,
            s3_key=s3_key,
            s3_region=s3_region,
            uploaded_by_sub=uploaded_by_sub,
        )
        self.session.add(attachment)
        self.session.flush()
        return attachment

    def record_step(
        self,
        claim: Claim,
        *,
        step_name: str,
        action: str,
        actor_name: Optional[str] = None,
        actor_role: Optional[str] = None,
        actor_sub: Optional[str] = None,
        notes: Optional[str] = None,
        outcome: str = "SUCCESS",
    ) -> ClaimStatusHistory:
        """Append a history step that is *not* a status change.

        Used for machine steps that belong on the claim's timeline without moving it — policy
        evaluation, fraud screening — so the visible history matches what actually ran.
        """
        entry = self._append_history(
            claim,
            from_status=claim.status,
            to_status=claim.status,
            step_name=step_name,
            action=action,
            actor_sub=actor_sub,
            actor_name=actor_name,
            actor_role=actor_role,
            notes=notes,
            outcome=outcome,
        )
        self.session.flush()
        return entry

    def list_history(self, claim_id: uuid.UUID) -> Sequence[ClaimStatusHistory]:
        return (
            self.session.execute(
                select(ClaimStatusHistory)
                .where(ClaimStatusHistory.claim_id == claim_id)
                .order_by(ClaimStatusHistory.sequence)
            )
            .scalars()
            .all()
        )

    # --- internals -----------------------------------------------------------

    def _next_history_sequence(self, claim_id: uuid.UUID) -> int:
        """Next gap-free sequence number, read from the table rather than a loaded collection."""
        current_max = self.session.execute(
            select(func.coalesce(func.max(ClaimStatusHistory.sequence), 0)).where(
                ClaimStatusHistory.claim_id == claim_id
            )
        ).scalar_one()
        return int(current_max) + 1

    def _append_history(
        self,
        claim: Claim,
        *,
        from_status: Optional[ClaimStatus],
        to_status: ClaimStatus,
        step_name: str,
        action: str,
        actor_sub: Optional[str] = None,
        actor_name: Optional[str] = None,
        actor_role: Optional[str] = None,
        notes: Optional[str] = None,
        outcome: str = "SUCCESS",
    ) -> ClaimStatusHistory:
        entry = ClaimStatusHistory(
            claim_id=claim.id,
            sequence=self._next_history_sequence(claim.id),
            from_status=from_status,
            to_status=to_status,
            actor_sub=actor_sub,
            actor_name=actor_name,
            actor_role=actor_role,
            step_name=step_name,
            action=action,
            outcome=outcome,
            notes=notes,
            request_id=current_request_id(),
            correlation_id=current_correlation_id(),
        )
        self.session.add(entry)
        return entry
