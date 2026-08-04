"""``DuplicateDetectionService`` — the one public entry point to this package.

Mirrors ``app.ai.knowledge.service.KnowledgeService``: everything under
``app/ai/duplicate_detection/*`` (hashing, image hashing, text/invoice similarity, thresholds,
signals, the engine itself) is an internal the architecture test forbids business code from
importing directly (see the ``internals`` tuple in
``test_business_layers_reach_retrieval_only_via_knowledge_service``). ``ClaimService`` depends on a
narrow, locally-defined ``DuplicateDetectionRecorder`` Protocol satisfied by this class — it never
imports this module, or anything else under ``app.ai.duplicate_detection``, itself.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.ai.duplicate_detection.engine import (
    DuplicateCandidateInput,
    DuplicateDetectionEngine,
    DuplicateReport,
)
from app.ai.duplicate_detection.thresholds import DuplicateThresholds, build_duplicate_thresholds
from app.ai.embeddings.service import EmbeddingService
from app.ai.models.duplicate_detection import VendorProfile
from app.ai.repositories.duplicate_detection_repository import (
    VendorRepository,
    VendorResolution,
)


class DuplicateDetectionService:
    """Advisory duplicate scanning plus vendor identity resolution, for one request's session."""

    def __init__(
        self,
        *,
        session: Session,
        tenant_id: str = "default",
        thresholds: Optional[DuplicateThresholds] = None,
        embedding_service: Optional[EmbeddingService] = None,
        recorder: Any = None,
    ) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._thresholds = thresholds or build_duplicate_thresholds()
        self._engine = DuplicateDetectionEngine(
            session, thresholds=self._thresholds, embedding_service=embedding_service,
            recorder=recorder,
        )
        self._vendors = VendorRepository(session)

    def scan_and_record(self, candidate: DuplicateCandidateInput) -> DuplicateReport:
        """Scan one claim and persist its fingerprint. Never raises for a "no match" outcome —
        callers that want best-effort, non-blocking behaviour (see
        ``ClaimService._scan_duplicates``) wrap this call themselves."""
        return self._engine.scan_and_record(candidate, tenant_id=self._tenant_id)

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
    ) -> DuplicateReport:
        """Primitive-argument adapter over :meth:`scan_and_record`, satisfying
        ``app.services.claim_service.DuplicateDetectionRecorder`` exactly — every argument is a
        primitive or stdlib type, so ``ClaimService`` never needs to import
        :class:`DuplicateCandidateInput` (or anything else in this package) to call it.
        """
        candidate = DuplicateCandidateInput(
            claim_id=claim_id,  # type: ignore[arg-type]
            employee_id=employee_id,  # type: ignore[arg-type]
            merchant_vendor=merchant_vendor,
            expense_date=expense_date,
            amount_usd=amount_usd,
            currency=currency,
            invoice_number=invoice_number,
        )
        return self.scan_and_record(candidate)

    def resolve_vendor(self, vendor_name: str) -> VendorProfile:
        """Resolve a raw vendor string to its canonical profile, creating one if this is the
        first time this vendor has been seen under any spelling."""
        resolution: VendorResolution = self._vendors.resolve(
            vendor_name, tenant_id=self._tenant_id,
            similarity_threshold=self._thresholds.vendor_alias_threshold,
        )
        return resolution.profile

    def describe(self) -> dict[str, Any]:
        """Active thresholds — what a future ``/metrics`` endpoint (M13) would expose."""
        return {
            "tenantId": self._tenant_id,
            "thresholds": {
                "sha256": self._thresholds.sha256_threshold,
                "perceptualHash": self._thresholds.perceptual_hash_threshold,
                "ocrSimilarity": self._thresholds.ocr_similarity_threshold,
                "embeddingSimilarity": self._thresholds.embedding_similarity_threshold,
                "vendorAlias": self._thresholds.vendor_alias_threshold,
                "invoiceSimilarity": self._thresholds.invoice_similarity_threshold,
                "nearDuplicate": self._thresholds.near_duplicate_threshold,
                "likelyScore": self._thresholds.likely_score,
                "confirmedScore": self._thresholds.confirmed_score,
            },
            "crossEmployeeEnabled": self._thresholds.cross_employee_enabled,
        }


__all__ = ["DuplicateDetectionService"]
