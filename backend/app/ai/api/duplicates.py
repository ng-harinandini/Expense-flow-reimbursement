"""Duplicate detection scan + vendor resolution.

``scan`` writes a fingerprint row (:class:`~app.ai.models.duplicate_detection.ClaimFingerprint`),
so it is a mutation and audited exactly like the knowledge-document routes; ``resolve`` is advisory
lookup work an investigator or finance reviewer performs while working a claim, gated the same as
scan rather than opened to every authenticated role, since both surface one employee's spend
history and vendor risk signal.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.ai.api.schemas import DuplicateScanRequestSchema
from app.ai.duplicate_detection.engine import DuplicateReport
from app.ai.duplicate_detection.service import DuplicateDetectionService
from app.ai.models.duplicate_detection import VendorProfile
from app.core.deps import (
    CurrentUser,
    get_audit_service,
    get_duplicate_detection_service,
    get_unit_of_work,
    require_roles,
)
from app.core.unit_of_work import UnitOfWork
from app.domain.actor import Actor
from app.services.audit_service import AuditService

router = APIRouter(prefix="/ai/duplicates", tags=["AI Knowledge Platform"])


def _serialize_report(report: DuplicateReport) -> dict:
    """``DuplicateReport.to_dict()`` already returns the full, JSON-serializable shape — this
    route is advisory-only, never a source of a claim's status, matching the class's own
    docstring."""
    return report.to_dict()


def _serialize_vendor(vendor: VendorProfile) -> dict:
    return {
        "id": str(vendor.id),
        "canonicalName": vendor.canonical_name,
        "riskScore": float(vendor.risk_score) if vendor.risk_score is not None else None,
        "claimCount": vendor.claim_count,
        "totalSpendUsd": float(vendor.total_spend_usd),
    }


@router.post("/scan", status_code=201)
def scan(
    payload: DuplicateScanRequestSchema,
    current: CurrentUser = Depends(require_roles("finance", "admin")),
    duplicate_detection: DuplicateDetectionService = Depends(get_duplicate_detection_service),
    audit: AuditService = Depends(get_audit_service),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> dict:
    """Advisory-only: the returned verdict never sets or changes a claim's status. The
    deterministic duplicate block inside ``ClaimService`` remains the only thing that can do
    that; this endpoint only enriches a reviewer's view."""
    actor = Actor.from_current_user(current)
    report = duplicate_detection.scan_claim(
        payload.claimId, payload.employeeId, payload.merchantVendor, payload.expenseDate,
        payload.amountUsd, payload.currency, invoice_number=payload.invoiceNumber,
    )
    audit.record(
        actor=actor, action="AI_DUPLICATE_SCAN", entity_type="ClaimFingerprint",
        entity_id=payload.claimId,
        details=f"Scanned claim '{payload.claimId}': verdict {report.verdict.value}.",
        after={"verdict": report.verdict.value, "score": round(report.score, 4)},
    )
    uow.commit()
    return _serialize_report(report)


@router.get("/vendors/{vendor_name}")
def resolve_vendor(
    vendor_name: str,
    current: CurrentUser = Depends(require_roles("finance", "admin", "auditor")),
    duplicate_detection: DuplicateDetectionService = Depends(get_duplicate_detection_service),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> dict:
    """Resolve a raw vendor string to its canonical profile, creating one on first sight.

    ``uow.commit()`` is still needed here even though the caller sees this as a read: resolution
    may insert a new :class:`VendorProfile`/:class:`~app.ai.models.duplicate_detection.VendorAlias`
    row the first time a spelling is seen.
    """
    vendor = duplicate_detection.resolve_vendor(vendor_name)
    uow.commit()
    return _serialize_vendor(vendor)
