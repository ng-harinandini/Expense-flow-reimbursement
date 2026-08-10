"""Claim lifecycle service — the only orchestrator of claim business logic.

Routes call this; it calls repositories. Nothing here builds HTTP responses and nothing in the
routes touches the ORM, which is the layering Phase 1 introduces:

    route  ->  service  ->  repository  ->  database

The submission pipeline reproduces the behaviour the in-memory implementation had (policy
evaluation, then fraud screening, then routing), with two differences that matter:

* every step is a *guarded* lifecycle transition, so the claim's own history is a complete and
  gap-free record of how it reached its current state, and
* the whole pipeline is one transaction: a claim, its history, its fraud result, its workflow, and
  its audit records either all commit or none do. A partially processed claim cannot exist.

Routing decisions (which state a processed claim lands in) match the previous thresholds exactly —
this phase moves state into the database, it does not change policy.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional, Protocol, Sequence, Union, runtime_checkable

from app.ai.core.enums import DecisionMemoryKind
from app.core.logging import get_logger
from app.domain import validators
from app.domain.actor import Actor
from app.domain.claim_state_machine import SYSTEM_ROLE
from app.domain.errors import (
    ConcurrentUpdateError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from sqlalchemy import func, select

from app.models.category import ExpenseCategory
from app.models.claim import Claim
from app.models.enums import (
    ApprovalStepStatus,
    AuditAction,
    AuditEntity,
    ClaimStatus,
    ExpenseItemStatus,
    FraudRiskLevel,
)
from app.models.expense_item import ExpenseItem
from app.models.organization import Employee
from app.repositories.claim_repository import ClaimQuery, ClaimRepository
from app.repositories.fraud_repository import FraudResultRepository
from app.repositories.workflow_repository import ApprovalWorkflowRepository
from app.services import receipt_extraction
from app.services.audit_service import AuditService
from app.services.employee_service import EmployeeService
from app.services.fraud_engine import screen_for_anomalies
from app.services.mappers import item_to_engine_input, items_to_engine_corpus
from app.services.policy_engine import evaluate_expense_policy
from app.services.policy_rule_service import PolicyRuleService

logger = get_logger(__name__)

#: Risk score at or above which a claim is routed to fraud investigation instead of review.
FRAUD_ROUTING_THRESHOLD = 40

#: Categories whose claims require an attendee listing.
ATTENDEE_REQUIRED_CATEGORIES = frozenset({"Client Entertainment"})

CLAIM_ACTIONS = frozenset({"APPROVE", "REJECT", "FLAG_FRAUD"})

#: How many peer claims to compare against during fraud screening.
FRAUD_CORPUS_LIMIT = 200

# Which decision-memory kind a claim's resulting status is recorded as, from the outer `action()`
# hook. A status with no entry here is not recorded from there: Draft/Submitted/Processing are not
# yet a "decision" worth remembering, and FLAGGED_FRAUD is deliberately absent because
# `_record_manual_fraud_flag` already records that event itself, with a more precise summary —
# including it here too would record the same FLAG_FRAUD action twice.
_STATUS_TO_MEMORY_KIND: dict[ClaimStatus, DecisionMemoryKind] = {
    ClaimStatus.MANAGER_REVIEW: DecisionMemoryKind.REVIEW,
    ClaimStatus.FINANCE_REVIEW: DecisionMemoryKind.REVIEW,
    ClaimStatus.AUTO_APPROVED: DecisionMemoryKind.APPROVAL,
    ClaimStatus.APPROVED: DecisionMemoryKind.APPROVAL,
    ClaimStatus.REIMBURSED: DecisionMemoryKind.APPROVAL,
    ClaimStatus.REJECTED: DecisionMemoryKind.REJECTION,
}


@runtime_checkable
class DecisionMemoryRecorder(Protocol):
    """The one AI-platform capability ``ClaimService`` depends on: writing a lifecycle event into
    decision memory. Deliberately narrower than the full ``KnowledgeService`` — this service has no
    business retrieving anything, only recording what happened. See
    ``app.ai.knowledge.service.KnowledgeService.record_decision``, which satisfies this shape.
    """

    def record_decision(
        self, kind: DecisionMemoryKind, subject_id: object, summary: str, *, actor: Actor
    ) -> None:
        ...


@runtime_checkable
class DuplicateDetectionRecorder(Protocol):
    """The one AI-platform capability ``ClaimService`` depends on for duplicate detection: scanning
    a newly submitted claim's identifying facts and recording its fingerprint for future scans to
    compare against. Deliberately narrower than the full ``DuplicateDetectionService`` — this
    service has no business resolving a vendor's document corpus, only scanning what it is given.
    Every argument is a primitive or a stdlib type, never an AI-platform type, so this Protocol
    (and therefore this whole module) never needs to import anything under ``app.ai`` beyond
    ``app.ai.core.enums`` — see ``app.ai.duplicate_detection.service.DuplicateDetectionService.
    scan_claim``, which satisfies this shape. The return value is read only via ``getattr`` for the
    same reason: this module must never depend on ``DuplicateReport``'s concrete type.
    """

    def scan_claim(
        self,
        claim_id: object,
        employee_id: object,
        merchant_vendor: str,
        expense_date: date,
        amount_usd: Decimal,
        currency: str,
        *,
        invoice_number: Optional[str] = None,
        checksum_sha256: Optional[str] = None,
    ) -> object:
        ...


class ClaimService:
    """Business operations on claims. One instance per request (see ``app.core.deps``)."""

    def __init__(
        self,
        *,
        claim_repository: ClaimRepository,
        fraud_repository: FraudResultRepository,
        workflow_repository: ApprovalWorkflowRepository,
        employee_service: EmployeeService,
        policy_rule_service: PolicyRuleService,
        audit_service: AuditService,
        decision_memory: Optional[DecisionMemoryRecorder] = None,
        duplicate_detection: Optional[DuplicateDetectionRecorder] = None,
    ) -> None:
        self._claims = claim_repository
        self._fraud = fraud_repository
        self._workflows = workflow_repository
        self._employees = employee_service
        self._policies = policy_rule_service
        self._audit = audit_service
        # None (the default, and what every existing caller/test still constructs with) means the
        # claim pipeline is byte-identical to before this dependency existed — no capability is
        # silently disabled here that was not disabled already by whoever chose not to wire one in.
        self._decision_memory = decision_memory
        self._duplicate_detection = duplicate_detection

    def _remember(
        self, kind: DecisionMemoryKind, subject_id: object, summary: str, *, actor: Actor
    ) -> None:
        """Best-effort decision-memory write. Never raises: a memory-indexing failure must not
        fail the claim transaction it is merely observing."""
        if self._decision_memory is None:
            return
        try:
            self._decision_memory.record_decision(kind, subject_id, summary, actor=actor)
        except Exception:
            logger.warning(
                "claim.decision_memory_write_failed",
                extra={"subjectId": str(subject_id), "kind": kind.value},
                exc_info=True,
            )

    def _scan_duplicates(self, claim: Claim) -> None:
        """Best-effort advisory duplicate scan, once per item.

        Never raises: a duplicate-detection failure must not fail the claim transaction it is
        merely observing, and its result can never change the claim's status — the deterministic
        block in ``ClaimRepository.find_duplicate_items`` (see ``_build_item``) is the only thing
        that can refuse a resubmission. This only *logs* a flag for a human to weigh, catching
        patterns (cross-employee, split receipts) the structural check cannot see.

        ``file_hash`` is passed through so the scanner can populate
        ``ai_claim_fingerprints.checksum_sha256`` — the column has always existed but nothing fed
        it, because the pre-split pipeline never had the receipt bytes to hand.
        """
        if self._duplicate_detection is None:
            return
        for item in claim.items:
            try:
                report = self._duplicate_detection.scan_claim(
                    claim.id, claim.employee_id, item.merchant_vendor, item.expense_date,
                    item.amount_usd, item.currency, checksum_sha256=item.file_hash,
                )
                verdict = getattr(getattr(report, "verdict", None), "value", None)
                if verdict in ("LIKELY", "CONFIRMED"):
                    logger.warning(
                        "claim.duplicate_scan_flagged",
                        extra={
                            "claimId": str(claim.id), "lineNumber": item.line_number,
                            "verdict": verdict, "score": getattr(report, "score", None),
                        },
                    )
            except Exception:
                logger.warning(
                    "claim.duplicate_scan_failed",
                    extra={"claimId": str(claim.id), "lineNumber": item.line_number},
                    exc_info=True,
                )

    # --- reads ---------------------------------------------------------------

    def list_claims(
        self,
        *,
        actor: Actor,
        category: Optional[str] = None,
        status: Union[str, Sequence[str], None] = None,
        employee_code: Optional[str] = None,
        risk_level: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> Sequence[Claim]:
        """Claims visible to ``actor``, filtered.

        An employee is always scoped to their own claims regardless of the filters they send; a
        non-existent ``employee_code`` filter yields an empty list rather than an error, so a
        dashboard filter cannot 404.

        ``status`` accepts either one value or several (OR'd) — a caller that needs claims across
        multiple statuses (e.g. finance's Finance_Review + Disbursed + Rejected view) makes one
        request instead of one per status.
        """
        query_employee_id: Optional[uuid.UUID] = None

        if actor.role == "employee":
            own = self._employees.find_actor_employee(actor)
            if own is None:
                return []
            query_employee_id = own.id
        elif employee_code:
            requested = self._employees.find_by_code(employee_code)
            if requested is None:
                return []
            query_employee_id = requested.id

        return self._claims.search(
            ClaimQuery(
                employee_id=query_employee_id,
                category=category or None,
                statuses=self._coerce_statuses(status),
                risk_level=self._coerce_risk_level(risk_level),
                limit=limit,
                offset=offset,
            )
        )

    def list_claims_for_manager(
        self,
        *,
        actor: Actor,
        category: Optional[str] = None,
        status: Optional[str] = None,
        risk_level: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> Sequence[Claim]:
        """Claims filed by ``actor``'s direct reports — the manager's own team, not the whole
        review queue (which may hold claims from anyone, assigned or unassigned).

        Same filter set and the same response shape as :meth:`list_claims`, just scoped by
        reporting line instead of by a single ``employee_id``. An actor with no employee record,
        or with no direct reports, gets an empty list rather than an error — an empty team is not
        a failure.
        """
        manager = self._employees.find_actor_employee(actor)
        if manager is None:
            return []

        reports = self._employees.list_direct_reports(manager)
        if not reports:
            return []

        return self._claims.search(
            ClaimQuery(
                employee_ids=[report.id for report in reports],
                category=category or None,
                status=self._coerce_status(status),
                risk_level=self._coerce_risk_level(risk_level),
                limit=limit,
                offset=offset,
            )
        )

    def get_claim_for_actor(self, identifier: str, *, actor: Actor) -> Claim:
        """One claim, enforcing ownership for employees.

        A non-owner employee gets 404 rather than 403 so the endpoint does not confirm that a
        claim id exists.
        """
        claim = self._claims.get_by_id_or_number(identifier)
        if claim is None:
            raise NotFoundError("Claim", identifier)

        if actor.role == "employee":
            own = self._employees.find_actor_employee(actor)
            if own is None or claim.employee_id != own.id:
                raise NotFoundError("Claim", identifier)
        return claim

    def list_history(self, claim: Claim):
        return self._claims.list_history(claim.id)

    # --- submission ----------------------------------------------------------

    def submit_claim(self, payload: dict[str, Any], *, actor: Actor) -> Claim:
        """Create and fully process a claim: Draft → Submitted → Processing → routed.

        Mirrors ``POST /claims``, which has always been a create-and-submit call. All of it runs in
        the caller's single transaction.

        The employee must have an active reporting manager: the manager is the first approval step
        (see :meth:`_start_workflow`), so without one the claim has no approver. That precondition
        is checked before anything is written, so a rejected submission leaves no draft behind.
        """
        employee = self._employees.resolve_actor_employee(actor)
        validators.require_reporting_manager(employee)
        actor = actor.with_name(employee.full_name)

        claim = self._build_draft(payload, employee=employee, actor=actor)

        # Draft → Submitted (the employee's own act)
        self._claims.transition_status(
            claim,
            ClaimStatus.SUBMITTED,
            actor_role="employee" if actor.role == "employee" else actor.role,
            actor_sub=actor.sub,
            actor_name=actor.name,
            step_name="Submit Claim",
            action=f"Submitted expense claim {claim.claim_number}",
        )

        # Submitted → Processing → routed (machine-driven)
        self._process(claim, actor=actor)

        categories = ", ".join(sorted({i.category for i in claim.items}))
        self._audit.record(
            actor=actor,
            action=AuditAction.SUBMIT_CLAIM,
            entity_type=AuditEntity.CLAIM,
            entity_id=claim.claim_number,
            details=(
                f"Submitted claim with {len(claim.items)} item(s) for "
                f"{claim.currency} {claim.total_amount_usd:.2f} "
                f"({categories}) -> Route: {claim.status.value}"
            ),
            after={
                "claimId": str(claim.id),
                "status": claim.status.value,
                "totalAmountUsd": str(claim.total_amount_usd),
                "itemCount": len(claim.items),
                "itemStatuses": [i.status.value for i in claim.items],
            },
        )
        self._remember(
            DecisionMemoryKind.CLAIM, claim.id,
            f"Claim {claim.claim_number}: {claim.currency} {claim.total_amount_usd:.2f} "
            f"across {len(claim.items)} item(s) ({categories}), submitted by {actor.name}. "
            f"Routed to {claim.status.value}.",
            actor=actor,
        )
        # self._scan_duplicates(claim)
        # History, comments, fraud result and workflow were inserted during this transaction;
        # expire so the serialized aggregate reflects all of them.
        return self._claims.refresh(claim)

    def _build_draft(
        self, payload: dict[str, Any], *, employee: Employee, actor: Actor
    ) -> Claim:
        """Validate the request and insert the ``Draft`` header plus its items.

        The header carries only report-level facts; every expense fact is validated and written by
        :meth:`_build_item`. A claim with no items would roll up to nothing and sit in ``Submitted``
        forever, so an empty ``items`` list is rejected before anything is written.
        """
        items_payload = payload.get("items")
        if not isinstance(items_payload, list) or not items_payload:
            raise ValidationError(
                "A claim must contain at least one expense item.",
                details={"field": "items"},
            )

        currency = validators.validate_currency(payload.get("currency"))
        from_date = self._coerce_date(payload.get("fromDate"))
        to_date = self._coerce_date(payload.get("toDate"))
        if from_date and to_date and to_date < from_date:
            raise ValidationError(
                "'toDate' cannot be earlier than 'fromDate'.",
                details={"field": "toDate"},
            )

        claim = Claim(
            claim_number=self._claims.next_claim_number(),
            employee_id=employee.id,
            # Snapshot: a later promotion must not re-judge a historical claim. The policy engine
            # reads it per item, from here.
            employee_grade=employee.grade,
            title=(payload.get("title") or "").strip() or None,
            purpose=(payload.get("purpose") or "").strip() or None,
            from_date=from_date,
            to_date=to_date,
            currency=currency,
        )

        self._claims.create_claim(
            claim, actor_sub=actor.sub, actor_name=actor.name, actor_role=actor.role
        )

        for index, item_payload in enumerate(items_payload, start=1):
            self._build_item(
                item_payload,
                claim=claim,
                line_number=index,
                employee=employee,
                actor=actor,
                default_currency=currency,
            )

        # The totals trigger wrote to ``claims`` outside the unit of work; re-read so the header
        # reflects the items just inserted.
        self._claims.refresh_totals(claim)

        self._audit.record(
            actor=actor,
            action=AuditAction.CLAIM_CREATE,
            entity_type=AuditEntity.CLAIM,
            entity_id=claim.claim_number,
            details=(
                f"Created draft claim {claim.claim_number} with {len(items_payload)} item(s) "
                f"for {employee.employee_code}."
            ),
            after={
                "claimId": str(claim.id),
                "status": claim.status.value,
                "itemCount": len(items_payload),
            },
        )
        return claim

    def _build_item(
        self,
        payload: dict[str, Any],
        *,
        claim: Claim,
        line_number: int,
        employee: Employee,
        actor: Actor,
        default_currency: str,
    ) -> ExpenseItem:
        """Validate one expense line and append it to ``claim``.

        Field values are resolved *correction -> submitted -> extraction*: the employee's edit of
        an OCR guess wins, then whatever they typed, then the raw extraction. Both JSON blobs are
        stored unmodified so a reviewer can see what the AI said and what was changed.
        """
        if not isinstance(payload, dict):
            raise ValidationError(
                f"Item {line_number} must be an object.",
                details={"field": f"items[{line_number - 1}]"},
            )

        extracted = payload.get("ocrExtractedJson") or payload.get("extractedReceipt")
        corrections = payload.get("employeeCorrectedData")
        if corrections is not None and not isinstance(corrections, dict):
            raise ValidationError(
                "'employeeCorrectedData' must be an object.",
                details={"field": "employeeCorrectedData"},
            )

        def resolve(field: str, *, extracted_key: Optional[str] = None):
            return receipt_extraction.resolve_field(
                extracted_key or field,
                submitted=payload.get(field),
                corrections=corrections,
                extracted=extracted if isinstance(extracted, dict) else None,
            )

        raw_amount = resolve("amount", extracted_key="totalAmount")
        raw_vendor = resolve("merchantVendor", extracted_key="vendorName")
        if raw_amount is None or raw_vendor is None or not str(raw_vendor).strip():
            missing = "amount" if raw_amount is None else "merchantVendor"
            raise ValidationError(
                f"Item {line_number} is missing '{missing}'.",
                details={"field": missing, "lineNumber": line_number},
            )

        amount = validators.validate_amount(raw_amount, field="amount")
        amount_usd = (
            validators.validate_amount(payload["amountUSD"], field="amountUSD")
            if payload.get("amountUSD") is not None
            else amount
        )
        currency = validators.validate_currency(payload.get("currency") or default_currency)
        expense_date = validators.validate_expense_date(
            self._coerce_date(resolve("expenseDate", extracted_key="transactionDate"))
            or date.today()
        )
        category = (payload.get("category") or "Misc / Other").strip()
        attendees = validators.validate_attendees(
            payload.get("attendees"), required=category in ATTENDEE_REQUIRED_CATEGORIES
        )
        merchant_vendor = str(raw_vendor).strip()

        # The client echoes ``fileUrl`` back from the upload response, so verify the object it
        # points at was uploaded by this employee before attaching it.
        file_url = payload.get("fileUrl")
        validators.require_receipt_ownership(file_url, employee)

        # Hard-block an exact resubmission before writing anything.
        validators.require_no_duplicate(
            self._claims.find_duplicate_items(
                employee_id=employee.id,
                merchant_vendor=merchant_vendor,
                expense_date=expense_date,
                amount_usd=amount_usd,
                exclude_claim_id=claim.id,
            )
        )

        return self._claims.add_item(
            claim,
            line_number=line_number,
            category_id=self._resolve_category_id(category),
            category=category,
            sub_category=(payload.get("subCategory") or "General Expense").strip(),
            expense_date=expense_date,
            merchant_vendor=merchant_vendor,
            purpose_description=(payload.get("purposeDescription") or "").strip(),
            attendees=attendees,
            trip_log=payload.get("tripLog"),
            amount=amount,
            currency=currency,
            amount_usd=amount_usd,
            fx_rate=self._coerce_decimal(payload.get("fxRate")),
            has_pre_approval=bool(payload.get("hasPreApproval", False)),
            pre_approval_doc_ref=payload.get("preApprovalDocRef"),
            receipt_attached=bool(payload.get("receiptAttached", bool(file_url))),
            file_url=file_url,
            file_name=payload.get("fileName"),
            mime_type=payload.get("mimeType"),
            file_size_bytes=payload.get("fileSizeBytes"),
            file_hash=payload.get("fileHash"),
            ocr_extracted_json=extracted,
            employee_corrected_data=corrections,
            ocr_source=payload.get("ocrSource"),
            ocr_confidence=payload.get("ocrConfidence"),
            created_by_sub=actor.sub,
            updated_by_sub=actor.sub,
        )

    def _resolve_category_id(self, category: str) -> Optional[uuid.UUID]:
        """Link the item to its reference row, when the category name is a known one.

        ``None`` for anything unrecognized: ``expense_items.category`` (the text the policy engine
        matches on) is authoritative, and an unfamiliar category must not block a submission.
        """
        row = self._claims.session.execute(
            select(ExpenseCategory.id).where(
                func.lower(ExpenseCategory.name) == category.strip().lower()
            )
        ).scalar_one_or_none()
        return row

    def _process(self, claim: Claim, *, actor: Actor) -> Claim:
        """Submitted → Processing → routed. Evaluation and screening happen here."""
        system = Actor.system()

        self._claims.transition_status(
            claim,
            ClaimStatus.PROCESSING,
            actor_role=SYSTEM_ROLE,
            actor_sub=actor.sub,
            actor_name=system.name,
            step_name="Processing",
            action="Started automated policy and fraud evaluation",
        )

        # One effective-dated ruleset for the whole claim, chosen by the earliest item date.
        # Fetching per item would mean two lines of one report judged under different rule
        # versions — surprising to a reviewer reading a single decision.
        earliest = min((i.expense_date for i in claim.items), default=date.today())
        rules = self._policies.rules_for_engine(earliest)

        # The peer corpus is read once, then grown as each item is judged: without appending, the
        # split-transaction check could not see that two items of *this* claim share a vendor and
        # date. Catching that is the point of holding several receipts on one claim.
        corpus = items_to_engine_corpus(
            self._claims.list_items_for_employee(
                claim.employee_id, exclude_claim_id=claim.id, limit=FRAUD_CORPUS_LIMIT
            )
        )

        policy_reports: list[dict[str, Any]] = []
        fraud_reports: list[dict[str, Any]] = []
        for item in claim.items:
            engine_input = item_to_engine_input(item, claim=claim)
            policy_report = self._evaluate_item_policy(item, claim=claim, rules=rules)
            fraud_report = self._screen_item_fraud(item, claim=claim, corpus=corpus)
            item.status = self._route_item(policy_report, fraud_report)
            policy_reports.append(policy_report)
            fraud_reports.append(fraud_report)
            corpus.append(engine_input)

        self._record_claim_fraud_result(claim, fraud_reports)

        target = self._roll_up_status(claim)
        held = [i.line_number for i in claim.items if i.status != ExpenseItemStatus.AUTO_APPROVED]
        self._claims.transition_status(
            claim,
            target,
            actor_role=SYSTEM_ROLE,
            actor_sub=actor.sub,
            actor_name=system.name,
            step_name="Routing Decision",
            action=f"Routed claim {claim.claim_number} to {target.value}",
            notes=(
                f"Items requiring attention: {held}" if held
                else "All items cleared automatically."
            ),
            outcome="SUCCESS" if target != ClaimStatus.FLAGGED_FRAUD else "WARNING",
        )

        self._start_workflow(claim, target)

        self._claims.add_comment(
            claim,
            body=f"Claim processed. Route status set to: {target.value.replace('_', ' ')}.",
            author_name=system.name,
            author_role="admin",
            author_sub=None,
        )
        return claim

    def _evaluate_item_policy(
        self, item: ExpenseItem, *, claim: Claim, rules: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Run the category policy engine over one item and snapshot the report on it.

        The engine itself is unchanged — it has always judged a single expense. The report is
        stored per item because the claim's roll-up is per item: a reviewer must be able to see
        *which* line held the claim, not just that something did.
        """
        report = evaluate_expense_policy(item_to_engine_input(item, claim=claim), rules)
        item.policy_validation = report

        self._claims.record_step(
            claim,
            actor_name="ExpenseFlow Policy Engine",
            actor_role="admin",
            step_name=f"Policy Validation (item {item.line_number})",
            action=(
                "Passed policy constraints"
                if report.get("overallPassed")
                else "Flagged policy violations"
            ),
            notes=report.get("reasoningSummary"),
            outcome="SUCCESS" if report.get("overallPassed") else "WARNING",
        )
        return report

    def _screen_item_fraud(
        self, item: ExpenseItem, *, claim: Claim, corpus: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Screen one item against the employee's expense history and record the verdict on it."""
        report = screen_for_anomalies(item_to_engine_input(item, claim=claim), corpus)

        item.fraud_risk_score = report["riskScore"]
        item.fraud_risk_level = FraudRiskLevel.coerce(report["riskLevel"])
        item.fraud_flags = report["flags"]
        item.is_fraud_flagged = bool(report["isFlagged"])

        self._claims.record_step(
            claim,
            actor_name="ExpenseFlow Anomaly Engine",
            actor_role="admin",
            step_name=f"Fraud Screening (item {item.line_number})",
            action=f"Risk Score: {report['riskScore']}/100 ({report['riskLevel']})",
            notes=report["rationale"],
            outcome="FAILED" if report["isFlagged"] else "SUCCESS",
        )
        return report

    def _record_claim_fraud_result(
        self, claim: Claim, reports: list[dict[str, Any]]
    ) -> None:
        """Persist one aggregate ``fraud_results`` row for the claim.

        The per-item detail lives on ``expense_items``; this row is still written because
        ``ClaimQuery.risk_level`` joins ``fraud_results`` to serve ``GET /claims?riskLevel=HIGH``,
        and ``claim_to_dict`` exposes ``fraudScreening`` from it. The claim inherits the
        worst-scoring item, and each flag is tagged with the line it came from.
        """
        if not reports:
            return

        worst = max(reports, key=lambda r: r["riskScore"])
        flags: list[dict[str, Any]] = []
        for item, report in zip(claim.items, reports):
            for flag in report.get("flags") or []:
                flags.append({**flag, "lineNumber": item.line_number})

        self._fraud.record(
            claim_id=claim.id,
            risk_score=worst["riskScore"],
            risk_level=worst["riskLevel"],
            is_flagged=any(r["isFlagged"] for r in reports),
            recommended_action=worst["recommendedAction"],
            rationale=worst["rationale"],
            flags=flags,
        )

    @staticmethod
    def _route_item(
        policy_report: dict[str, Any], fraud_report: dict[str, Any]
    ) -> ExpenseItemStatus:
        """Destination for one evaluated item. Thresholds unchanged from the claim-level routing."""
        if fraud_report["isFlagged"] and fraud_report["riskScore"] >= FRAUD_ROUTING_THRESHOLD:
            return ExpenseItemStatus.FRAUD_FLAG
        if not policy_report.get("overallPassed"):
            return ExpenseItemStatus.POLICY_HOLD
        if policy_report.get("requiresManualReview"):
            return ExpenseItemStatus.POLICY_HOLD
        return ExpenseItemStatus.AUTO_APPROVED

    @staticmethod
    def _roll_up_status(claim: Claim) -> ClaimStatus:
        """The claim's status, derived from its items. Fraud outranks a policy hold.

        Total by construction: an empty item set or any unexpected mix lands on
        ``MANAGER_REVIEW``. Never fall through to ``AUTO_APPROVED`` — the safe default when the
        state is not understood is that a human looks at it.
        """
        statuses = {item.status for item in claim.items}
        if ExpenseItemStatus.FRAUD_FLAG in statuses:
            return ClaimStatus.FLAGGED_FRAUD
        if ExpenseItemStatus.POLICY_HOLD in statuses:
            return ClaimStatus.MANAGER_REVIEW
        if statuses == {ExpenseItemStatus.AUTO_APPROVED}:
            return ClaimStatus.AUTO_APPROVED
        return ClaimStatus.MANAGER_REVIEW

    @staticmethod
    def _roll_up_after_decisions(claim: Claim) -> Optional[ClaimStatus]:
        """Target after per-item human decisions, or ``None`` while any item is still pending.

        A claim resolves only once every item has an outcome: all rejected means the claim is
        rejected, anything else means at least one item was approved and the claim is approved.
        """
        pending = {
            ExpenseItemStatus.SUBMITTED,
            ExpenseItemStatus.POLICY_HOLD,
            ExpenseItemStatus.FRAUD_FLAG,
        }
        statuses = [item.status for item in claim.items]
        if not statuses or any(s in pending for s in statuses):
            return None
        if all(s == ExpenseItemStatus.REJECTED for s in statuses):
            return ClaimStatus.REJECTED
        return ClaimStatus.APPROVED

    def _start_workflow(self, claim: Claim, routed_to: ClaimStatus) -> None:
        """Materialise the approval workflow that matches the routing outcome."""
        manager = self._employees.manager_of(claim.employee)
        workflow = self._workflows.create_standard_workflow(
            claim.id,
            assignee_by_role={"manager": manager.id} if manager else None,
        )

        if routed_to == ClaimStatus.AUTO_APPROVED:
            # Policy cleared it without a human: manager step is skipped, finance still pays out.
            self._workflows.complete_step(
                workflow,
                required_role="manager",
                status=ApprovalStepStatus.SKIPPED,
                decision_notes="Auto-approved by policy engine.",
            )
            self._workflows.start_step(workflow, 2)
        elif routed_to == ClaimStatus.FINANCE_REVIEW:
            self._workflows.complete_step(
                workflow, required_role="manager", status=ApprovalStepStatus.SKIPPED
            )
            self._workflows.start_step(workflow, 2)
        else:
            self._workflows.start_step(workflow, 1)

        if manager is not None and routed_to in (
            ClaimStatus.MANAGER_REVIEW,
            ClaimStatus.FLAGGED_FRAUD,
        ):
            self._claims.assign_reviewer(
                claim,
                manager.id,
                actor_name=Actor.system().name,
                actor_role=SYSTEM_ROLE,
                notes=f"Auto-assigned to reporting manager {manager.full_name}.",
            )

    # --- decisions -----------------------------------------------------------

    def execute_action(
        self,
        identifier: str,
        *,
        action: str,
        actor: Actor,
        notes: Optional[str] = None,
        expected_version: Optional[int] = None,
    ) -> Claim:
        """Apply a reviewer decision (``APPROVE`` / ``REJECT`` / ``FLAG_FRAUD``).

        The claim's current state decides the target: approving a claim in manager review escalates
        it to finance review; approving anywhere else (finance review, auto-approved, or a cleared
        fraud flag) is the final reviewer step — there is no separate disbursement action. An
        action that is not legal from the current state raises ``409`` naming the states that are.
        """
        normalized = (action or "").strip().upper()
        if normalized not in CLAIM_ACTIONS:
            raise ValidationError(
                f"Unsupported action '{action}'.",
                details={"action": action, "supported": sorted(CLAIM_ACTIONS)},
            )

        claim = self._claims.get_by_id_or_number(identifier)
        if claim is None:
            raise NotFoundError("Claim", identifier)

        # Client-supplied version turns a lost race into 409 instead of a silent overwrite.
        if expected_version is not None and claim.version != expected_version:
            raise ConcurrentUpdateError("Claim", claim.claim_number)

        validators.require_not_terminal(claim)

        before_status = claim.status
        workflow = self._workflows.get_active_for_claim(claim.id)

        if normalized == "APPROVE":
            claim = self._approve(claim, actor=actor, notes=notes, workflow=workflow)
        elif normalized == "REJECT":
            claim = self._claims.reject_claim(
                claim, actor_role=actor.role, actor_sub=actor.sub,
                actor_name=actor.name, reason=notes,
            )
            if workflow is not None:
                self._workflows.reject_open_step(
                    workflow, required_role=actor.role, decided_by_sub=actor.sub, reason=notes
                )
        else:  # FLAG_FRAUD
            claim = self._claims.flag_fraud(
                claim, actor_role=actor.role, actor_sub=actor.sub,
                actor_name=actor.name, notes=notes,
            )
            self._record_manual_fraud_flag(claim, actor=actor, notes=notes)

        if notes:
            self._claims.add_comment(
                claim,
                body=notes,
                author_name=actor.name,
                author_role=actor.role,
                author_sub=actor.sub,
            )

        self._audit.record(
            actor=actor,
            action=AuditAction.APPROVAL_ACTION,
            entity_type=AuditEntity.CLAIM,
            entity_id=claim.claim_number,
            details=f"Action {normalized} executed. Status updated to {claim.status.value}",
            before={"status": before_status.value},
            after={"status": claim.status.value, "claimId": str(claim.id)},
        )
        memory_kind = _STATUS_TO_MEMORY_KIND.get(claim.status)
        if memory_kind is not None:
            # Deliberately excludes `notes`: decision memory has no role-based visibility
            # filtering yet (see the same reasoning on the `add_comment` guard below), and a
            # reviewer's free-text notes on an approval/rejection can carry exactly the kind of
            # internal-only reasoning that guard exists to keep out of a broadly retrievable
            # surface. Only structured, already-non-sensitive fields are recorded here.
            categories = ", ".join(sorted({i.category for i in claim.items}))
            summary = (
                f"Claim {claim.claim_number} ({categories}, {claim.currency} "
                f"{claim.total_amount_usd:.2f} across {claim.item_count} item(s)): "
                f"{normalized} executed by {actor.role}. Status "
                f"{before_status.value} -> {claim.status.value}."
            )
            self._remember(memory_kind, claim.id, summary, actor=actor)
        logger.info(
            "claim.action_executed",
            extra={
                "claimNumber": claim.claim_number,
                "claimAction": normalized,
                "fromStatus": before_status.value,
                "toStatus": claim.status.value,
                "actorRole": actor.role,
            },
        )
        return self._claims.refresh(claim)

    def _approve(
        self, claim: Claim, *, actor: Actor, notes: Optional[str], workflow
    ) -> Claim:
        """Approval semantics: manager review escalates to finance; anything else approves."""
        if claim.status == ClaimStatus.MANAGER_REVIEW:
            if workflow is not None:
                self._workflows.complete_step(
                    workflow,
                    required_role="manager",
                    status=ApprovalStepStatus.APPROVED,
                    decided_by_sub=actor.sub,
                    decision_notes=notes,
                )
                self._workflows.advance_or_complete(workflow)
            return self._claims.transition_status(
                claim,
                ClaimStatus.FINANCE_REVIEW,
                actor_role=actor.role,
                actor_sub=actor.sub,
                actor_name=actor.name,
                step_name="Manager Approval",
                action=f"Manager approved claim {claim.claim_number}; escalated to finance review",
                notes=notes,
            )

        if workflow is not None:
            self._workflows.complete_step(
                workflow,
                required_role=actor.role if actor.role != "admin" else None,
                status=ApprovalStepStatus.APPROVED,
                decided_by_sub=actor.sub,
                decision_notes=notes,
            )
            # Approve is now the final reviewer action reaching this branch (finance approving
            # from Finance_Review, or a direct approval of an Auto_Approved/cleared-fraud claim) —
            # there is no later DISBURSE call to close the workflow out, so this has to do it.
            self._workflows.advance_or_complete(workflow)
        return self._claims.approve_claim(
            claim, actor_role=actor.role, actor_sub=actor.sub,
            actor_name=actor.name, notes=notes,
        )

    def _record_manual_fraud_flag(
        self, claim: Claim, *, actor: Actor, notes: Optional[str]
    ) -> None:
        """Persist a human fraud flag as a screening result, so the verdict trail is complete."""
        previous = self._fraud.latest_for_claim(claim.id)
        flags = list(previous.flag_list) if previous else []
        flags.append(
            {
                "code": "MANUAL_FRAUD_FLAG",
                "title": "Manually Flagged for Investigation",
                "severity": "HIGH",
                "description": notes or f"Flagged by {actor.role} for fraud audit.",
                "evidence": f"Raised by {actor.name} ({actor.role}).",
            }
        )
        self._fraud.record(
            claim_id=claim.id,
            risk_score=max(previous.risk_score if previous else 0, FRAUD_ROUTING_THRESHOLD),
            risk_level=FraudRiskLevel.HIGH,
            is_flagged=True,
            recommended_action="FINANCE_AUDIT",
            rationale=notes or f"Manually flagged for fraud investigation by {actor.name}.",
            flags=flags,
        )
        self._audit.record(
            actor=actor,
            action=AuditAction.FRAUD_FLAG,
            entity_type=AuditEntity.CLAIM,
            entity_id=claim.claim_number,
            details=notes or "Claim manually flagged for fraud investigation.",
        )
        self._remember(
            DecisionMemoryKind.FRAUD_FINDING, claim.id,
            # Excludes `notes` for the same reason as the `action()` hook above: fraud rationale is
            # exactly the kind of internal-only content decision memory should not broadly expose
            # until it has role-based visibility filtering.
            f"Claim {claim.claim_number} manually flagged for fraud investigation by "
            f"{actor.name} ({actor.role}).",
            actor=actor,
        )

    # --- withdrawal ----------------------------------------------------------

    def withdraw_claim(
        self,
        identifier: str,
        *,
        actor: Actor,
        reason: Optional[str] = None,
        expected_version: Optional[int] = None,
    ) -> Claim:
        """Withdraw an entire claim on its owner's behalf (``-> Withdrawn``, terminal).

        Withdrawal is the employee's counterpart to a reviewer's rejection: it closes the claim and
        every open approval step in one transaction, but records *no* decision — nothing was judged.
        The whole claim goes at once; there is no per-item withdrawal, because a partially withdrawn
        report has no meaning to the approver looking at it.

        Ownership is enforced through :meth:`get_claim_for_actor`, so an employee withdrawing
        somebody else's claim gets 404 rather than a hint that it exists. Which states allow it is
        :func:`validators.require_withdrawable` — notably not ``Flagged_Fraud``.
        """
        claim = self.get_claim_for_actor(identifier, actor=actor)

        # An employee may only ever withdraw their own; the ownership check above already
        # guarantees it, but admins resolve any claim, so re-assert explicitly for them.
        if actor.role == "employee":
            employee = self._employees.resolve_actor_employee(actor)
            validators.require_claim_ownership(claim, employee)

        # Client-supplied version turns a lost race (a manager approving while the employee
        # withdraws) into 409 instead of one silently overwriting the other.
        if expected_version is not None and claim.version != expected_version:
            raise ConcurrentUpdateError("Claim", claim.claim_number)

        validators.require_withdrawable(claim)

        before_status = claim.status
        note = (reason or "").strip() or None

        workflow = self._workflows.get_active_for_claim(claim.id)
        claim = self._claims.withdraw_claim(
            claim,
            actor_role=actor.role,
            actor_sub=actor.sub,
            actor_name=actor.name,
            reason=note,
        )
        if workflow is not None:
            self._workflows.cancel_open_steps(
                workflow,
                decided_by_sub=actor.sub,
                reason=note or "Claim withdrawn by the employee.",
            )

        self._claims.add_comment(
            claim,
            body=(
                f"Claim withdrawn by {actor.name}."
                if note is None
                else f"Claim withdrawn by {actor.name}: {note}"
            ),
            author_name=actor.name,
            author_role=actor.role,
            author_sub=actor.sub,
        )

        self._audit.record(
            actor=actor,
            action=AuditAction.CLAIM_WITHDRAW,
            entity_type=AuditEntity.CLAIM,
            entity_id=claim.claim_number,
            details=(
                f"Withdrew claim {claim.claim_number} from {before_status.value}"
                + (f": {note}" if note else ".")
            ),
            before={"status": before_status.value},
            after={"status": claim.status.value, "claimId": str(claim.id)},
        )
        # Deliberately excludes `reason`, consistent with the approval/rejection hook above:
        # decision memory has no role-based visibility filtering yet, and an employee's free-text
        # withdrawal reason is not something to make broadly retrievable.
        self._remember(
            DecisionMemoryKind.CLAIM, claim.id,
            f"Claim {claim.claim_number} ({claim.currency} {claim.total_amount_usd:.2f} across "
            f"{claim.item_count} item(s)) withdrawn by {actor.name} ({actor.role}) from "
            f"{before_status.value}.",
            actor=actor,
        )
        logger.info(
            "claim.withdrawn",
            extra={
                "claimNumber": claim.claim_number,
                "fromStatus": before_status.value,
                "actorRole": actor.role,
            },
        )
        return self._claims.refresh(claim)

    # --- reviewer assignment -------------------------------------------------

    def assign_reviewer(
        self, identifier: str, *, reviewer_code: Optional[str], actor: Actor,
        notes: Optional[str] = None,
    ) -> Claim:
        """Assign (or clear) the reviewer responsible for the claim's next decision."""
        claim = self._claims.get_by_id_or_number(identifier)
        if claim is None:
            raise NotFoundError("Claim", identifier)
        validators.require_not_terminal(claim)

        reviewer = self._employees.get_by_code(reviewer_code) if reviewer_code else None
        before = (
            claim.assigned_reviewer.employee_code if claim.assigned_reviewer else None
        )

        self._claims.assign_reviewer(
            claim,
            reviewer.id if reviewer else None,
            actor_sub=actor.sub,
            actor_name=actor.name,
            actor_role=actor.role,
            notes=notes,
        )
        self._audit.record(
            actor=actor,
            action=AuditAction.REVIEWER_ASSIGNED,
            entity_type=AuditEntity.CLAIM,
            entity_id=claim.claim_number,
            details=(
                f"Assigned reviewer {reviewer.employee_code}"
                if reviewer
                else "Cleared reviewer assignment"
            ),
            before={"assignedReviewerId": before},
            after={"assignedReviewerId": reviewer.employee_code if reviewer else None},
        )
        return self._claims.refresh(claim)

    # --- comments ------------------------------------------------------------

    def add_comment(
        self, identifier: str, *, body: str, actor: Actor, is_internal: bool = False
    ) -> Claim:
        """Attach a comment. Employees may only comment on their own claims."""
        text = (body or "").strip()
        if not text:
            raise ValidationError("Comment text is required.", details={"field": "text"})
        if is_internal and actor.role == "employee":
            raise ForbiddenError("Employees cannot create internal comments.")

        claim = self.get_claim_for_actor(identifier, actor=actor)

        self._claims.add_comment(
            claim,
            body=text,
            author_name=actor.name,
            author_role=actor.role,
            author_sub=actor.sub,
            is_internal=is_internal,
        )
        self._audit.record(
            actor=actor,
            action=AuditAction.COMMENT_ADDED,
            entity_type=AuditEntity.CLAIM,
            entity_id=claim.claim_number,
            details=f"Comment added to claim {claim.claim_number}.",
        )
        if not is_internal:
            # Internal-only comments are deliberately never remembered here: decision memory has
            # no role-based visibility filtering yet (that is a future access-control feature, not
            # part of T004), so recording one would make reviewer-only content retrievable by an
            # employee through `retrieve_similar_claims`/`search`.
            self._remember(
                DecisionMemoryKind.COMMENT, claim.id,
                f"Comment on claim {claim.claim_number} by {actor.name} ({actor.role}): {text}",
                actor=actor,
            )
        return self._claims.refresh(claim)

    # --- per-item decisions --------------------------------------------------

    def decide_item(
        self,
        identifier: str,
        item_id: str,
        *,
        action: str,
        actor: Actor,
        notes: Optional[str] = None,
        expected_version: Optional[int] = None,
    ) -> Claim:
        """Approve or reject a single expense item, then re-derive the claim's status.

        The claim only moves once **every** item has an outcome — see
        :meth:`_roll_up_after_decisions`. Status is still written exclusively through
        ``transition_status``, so the state machine and the database guard both validate the edge.
        """
        claim = self.get_claim_for_actor(identifier, actor=actor)
        validators.require_not_terminal(claim)

        try:
            parsed_id = uuid.UUID(str(item_id))
        except (ValueError, TypeError):
            raise ValidationError(
                "'itemId' must be a valid UUID.", details={"field": "itemId"}
            )

        item = validators.require_expense_item(self._claims.get_item(parsed_id), str(item_id))
        if item.claim_id != claim.id:
            raise NotFoundError("ExpenseItem", str(item_id))

        if expected_version is not None and item.version != expected_version:
            raise ConcurrentUpdateError("ExpenseItem", item.id)

        normalized = (action or "").strip().lower()
        if normalized not in ("approve", "reject"):
            raise ValidationError(
                f"'{action}' is not a valid item action. Expected 'approve' or 'reject'.",
                details={"field": "action", "allowed": ["approve", "reject"]},
            )
        if normalized == "reject" and not (notes or "").strip():
            raise ValidationError(
                "A rejection reason is required.", details={"field": "notes"}
            )

        decider = self._employees.find_actor_employee(actor)
        before_status = item.status
        item.status = (
            ExpenseItemStatus.MANAGER_APPROVED
            if normalized == "approve"
            else ExpenseItemStatus.REJECTED
        )
        item.decided_at = datetime.now(timezone.utc)
        item.decided_by_sub = actor.sub
        item.decided_by_employee_id = decider.id if decider else None
        item.decision_notes = notes
        item.rejection_reason = notes if normalized == "reject" else None
        item.updated_by_sub = actor.sub
        self._claims.session.flush()

        self._claims.record_step(
            claim,
            actor_name=actor.name,
            actor_role=actor.role,
            actor_sub=actor.sub,
            step_name=f"Item Decision (item {item.line_number})",
            action=f"{normalized.capitalize()}d item {item.line_number} ({item.category})",
            notes=notes,
            outcome="SUCCESS" if normalized == "approve" else "WARNING",
        )
        self._audit.record(
            actor=actor,
            action=AuditAction.EXPENSE_ITEM_DECISION,
            entity_type=AuditEntity.EXPENSE_ITEM,
            entity_id=str(item.id),
            details=(
                f"Item {item.line_number} of claim {claim.claim_number} "
                f"{normalized}d by {actor.role}."
            ),
            before={"status": before_status.value},
            after={
                "status": item.status.value,
                "claimId": str(claim.id),
                "lineNumber": item.line_number,
            },
        )

        target = self._roll_up_after_decisions(claim)
        if target is not None and target != claim.status:
            self._claims.transition_status(
                claim,
                target,
                actor_role=actor.role,
                actor_sub=actor.sub,
                actor_name=actor.name,
                step_name="Routing Decision",
                action=f"All items decided; claim {claim.claim_number} -> {target.value}",
                outcome="SUCCESS" if target != ClaimStatus.REJECTED else "WARNING",
            )

        return self._claims.refresh(claim)

    # --- draft editing -------------------------------------------------------

    def update_draft(
        self, identifier: str, *, changes: dict[str, Any], actor: Actor
    ) -> Claim:
        """Edit an unsubmitted claim's header. Rejected once the claim has left ``Draft``.

        Only report-level fields live on the claim now; per-expense edits go through
        :meth:`update_item`.
        """
        claim = self.get_claim_for_actor(identifier, actor=actor)
        employee = self._employees.resolve_actor_employee(actor)
        if actor.role == "employee":
            validators.require_claim_ownership(claim, employee)
        validators.require_editable(claim)

        updates: dict[str, Any] = {}
        before: dict[str, Any] = {}

        if "currency" in changes:
            updates["currency"] = validators.validate_currency(changes["currency"])
            before["currency"] = claim.currency
        for wire_name, column in (
            ("title", "title"),
            ("purpose", "purpose"),
        ):
            if wire_name in changes:
                before[wire_name] = getattr(claim, column)
                updates[column] = changes[wire_name]
        for wire_name, column in (("fromDate", "from_date"), ("toDate", "to_date")):
            if wire_name in changes:
                existing = getattr(claim, column)
                before[wire_name] = existing.isoformat() if existing else None
                updates[column] = self._coerce_date(changes[wire_name])

        if not updates:
            raise ValidationError("No editable fields supplied.")

        self._claims.update_fields(claim, actor_sub=actor.sub, **updates)
        self._audit.record(
            actor=actor,
            action=AuditAction.CLAIM_UPDATE,
            entity_type=AuditEntity.CLAIM,
            entity_id=claim.claim_number,
            details=f"Updated draft claim {claim.claim_number}: {', '.join(sorted(before))}.",
            before={key: str(value) for key, value in before.items()},
            after={key: str(value) for key, value in updates.items()},
        )
        return self._claims.refresh(claim)

    # --- coercion helpers ----------------------------------------------------

    @staticmethod
    def _coerce_status(value: Optional[str]) -> Optional[ClaimStatus]:
        if not value:
            return None
        try:
            return ClaimStatus.coerce(value)
        except ValueError as exc:
            raise ValidationError(str(exc), details={"field": "status"})

    @staticmethod
    def _coerce_statuses(value: Union[str, Sequence[str], None]) -> Optional[list[ClaimStatus]]:
        """Like :meth:`_coerce_status`, but for the ``status`` filter's OR-list form.

        Accepts a bare string (the common single-value case) or a sequence of strings (repeated
        ``?status=`` query params) — a bare string is *not* iterated character-by-character, which
        ``list(value)`` would otherwise silently do.
        """
        if not value:
            return None
        raw_values = [value] if isinstance(value, str) else list(value)
        coerced: list[ClaimStatus] = []
        for raw in raw_values:
            if not raw:
                continue
            try:
                coerced.append(ClaimStatus.coerce(raw))
            except ValueError as exc:
                raise ValidationError(str(exc), details={"field": "status"})
        return coerced or None

    @staticmethod
    def _coerce_risk_level(value: Optional[str]) -> Optional[FraudRiskLevel]:
        if not value:
            return None
        try:
            return FraudRiskLevel.coerce(value)
        except ValueError as exc:
            raise ValidationError(str(exc), details={"field": "riskLevel"})

    @staticmethod
    def _coerce_date(value: Any) -> Optional[date]:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        try:
            return datetime.fromisoformat(str(value)[:10]).date()
        except ValueError:
            raise ValidationError(
                "Date must be an ISO-8601 value (YYYY-MM-DD).",
                details={"value": str(value)},
            )

    @staticmethod
    def _coerce_decimal(value: Any) -> Optional[Decimal]:
        if value is None or value == "":
            return None
        try:
            return Decimal(str(value))
        except (ArithmeticError, ValueError, TypeError):
            return None
