"""The duplicate-detection engine: composes every signal into one explainable, advisory verdict.

**Advisory only.** Nothing here can reject, hold or otherwise change a claim — see
``tests/test_ai_architecture.py``, which structurally forbids this whole package from importing
``ClaimService``/``ClaimRepository``. The existing deterministic block in
``ClaimRepository.find_duplicate_claims`` remains the only thing that can refuse a resubmission;
this engine exists to catch the patterns that check cannot see (cross-employee, multi-receipt
splits, a re-encoded image, a near-duplicate OCR text) and report them for a human to weigh.

**A verdict is driven by the single strongest applicable signal, not an average.** Averaging in
every ``None`` (not-applicable) signal as a zero would silently punish a claim for missing inputs
(no receipt bytes, no OCR text) it was never expected to have; taking the max of whatever *did*
apply keeps a confident single signal (an exact SHA-256 match) from being diluted by several
inapplicable ones.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional, Sequence

from sqlalchemy.orm import Session

from app.ai.core.enums import DuplicateSignalKind, DuplicateVerdict, TelemetryOperation
from app.ai.duplicate_detection import image_hash
from app.ai.duplicate_detection.hashing import sha256_hex
from app.ai.duplicate_detection.invoice import InvoiceFields, find_multi_receipt_group
from app.ai.duplicate_detection.signals import (
    SignalResult,
    embedding_similarity_signal,
    image_hash_signal,
    invoice_similarity_signal,
    ocr_similarity_signal,
    perceptual_hash_signal,
    sha256_signal,
    vendor_alias_signal,
)
from app.ai.duplicate_detection.thresholds import DuplicateThresholds, build_duplicate_thresholds
from app.ai.embeddings.service import EmbeddingService
from app.ai.models.duplicate_detection import ClaimFingerprint
from app.ai.repositories.duplicate_detection_repository import (
    ClaimFingerprintRepository,
    VendorRepository,
)
from app.ai.telemetry.recorder import NullTelemetryRecorder
from app.core.logging import get_logger

logger = get_logger(__name__)

# Signals whose *continuous* similarity score (as opposed to the boolean case signals) feeds the
# NEAR_DUPLICATE case: "looks like the same receipt" evidence that is not an exact byte match.
_CONTINUOUS_SIGNAL_KINDS = frozenset({
    DuplicateSignalKind.IMAGE_HASH,
    DuplicateSignalKind.PERCEPTUAL_HASH,
    DuplicateSignalKind.OCR_SIMILARITY,
    DuplicateSignalKind.EMBEDDING_SIMILARITY,
    DuplicateSignalKind.INVOICE_SIMILARITY,
})


@dataclass(frozen=True, slots=True)
class DuplicateCandidateInput:
    """What the engine needs to scan one claim. Every content-derived field is optional: a signal
    with no input on this side is skipped, never treated as a mismatch."""

    claim_id: uuid.UUID
    employee_id: uuid.UUID
    merchant_vendor: str
    expense_date: date
    amount_usd: Decimal
    currency: str
    invoice_number: Optional[str] = None
    receipt_bytes: Optional[bytes] = None
    ocr_text: Optional[str] = None


@dataclass(frozen=True, slots=True)
class DuplicateReport:
    """The explainable outcome of one scan: a verdict, the signals behind it, and which other
    claims it implicates."""

    claim_id: uuid.UUID
    verdict: DuplicateVerdict
    score: float
    signals: tuple[SignalResult, ...]
    related_claim_ids: tuple[uuid.UUID, ...] = ()
    cross_employee: bool = False
    multi_receipt_claim_ids: tuple[uuid.UUID, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """A plain, JSON-serializable summary — for logging and a future API response."""
        return {
            "claimId": str(self.claim_id),
            "verdict": self.verdict.value,
            "score": round(self.score, 4),
            "crossEmployee": self.cross_employee,
            "relatedClaimIds": [str(cid) for cid in self.related_claim_ids],
            "multiReceiptClaimIds": [str(cid) for cid in self.multi_receipt_claim_ids],
            "signals": [
                {
                    "kind": result.kind.value,
                    "score": None if result.score is None else round(result.score, 4),
                    "threshold": result.threshold,
                    "fired": result.fired,
                }
                for result in self.signals
            ],
        }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DuplicateDetectionEngine:
    """Scans one claim against a bounded, vendor+date-windowed candidate set and, optionally,
    records its fingerprint for future scans to compare against."""

    def __init__(
        self,
        session: Session,
        *,
        thresholds: Optional[DuplicateThresholds] = None,
        embedding_service: Optional[EmbeddingService] = None,
        recorder: Any = None,
    ) -> None:
        self._thresholds = thresholds or build_duplicate_thresholds()
        self._fingerprints = ClaimFingerprintRepository(session)
        self._vendors = VendorRepository(session)
        self._embedding_service = embedding_service
        self._recorder = recorder or NullTelemetryRecorder()

    def scan_and_record(
        self, candidate: DuplicateCandidateInput, *, tenant_id: str = "default"
    ) -> DuplicateReport:
        """Scan ``candidate`` against existing fingerprints, then persist its own fingerprint.

        Persistence happens even on a ``NO_MATCH`` verdict: a future claim needs this one as a
        candidate too, or duplicate detection would only ever see pairs where the *second* claim
        happened to trigger a scan.
        """
        with self._recorder.span(TelemetryOperation.DUPLICATE_SCAN) as span:
            thresholds = self._thresholds
            amount_tolerance = Decimal(str(thresholds.amount_tolerance))

            resolution = self._vendors.resolve(
                candidate.merchant_vendor, tenant_id=tenant_id,
                similarity_threshold=thresholds.vendor_alias_threshold,
            )
            vendor_profile = resolution.profile

            checksum, avg_hash, diff_hash = self._content_hashes(candidate)

            window_candidates = self._fingerprints.find_candidates(
                tenant_id=tenant_id, vendor_profile_id=vendor_profile.id,
                expense_date=candidate.expense_date, date_window_days=thresholds.date_window_days,
                exclude_claim_id=candidate.claim_id, limit=thresholds.max_candidates,
            )

            signals = self._score_signals(
                candidate, window_candidates, checksum=checksum, avg_hash=avg_hash,
                diff_hash=diff_hash, vendor_profile_id=vendor_profile.id,
                amount_tolerance=amount_tolerance,
            )

            cross_employee_matches: Sequence[ClaimFingerprint] = ()
            if thresholds.cross_employee_enabled:
                cross_employee_matches = self._fingerprints.find_cross_employee_matches(
                    tenant_id=tenant_id, vendor_profile_id=vendor_profile.id,
                    expense_date=candidate.expense_date, amount_usd=candidate.amount_usd,
                    amount_tolerance=amount_tolerance, exclude_employee_id=candidate.employee_id,
                    exclude_claim_id=candidate.claim_id,
                )
            signals.append(self._cross_employee_signal(cross_employee_matches))

            multi_receipt_group = self._find_multi_receipt_group(
                candidate, vendor_profile_id=vendor_profile.id, tenant_id=tenant_id,
                amount_tolerance=amount_tolerance,
            )
            signals.append(self._multi_receipt_signal(multi_receipt_group))
            signals.append(self._near_duplicate_signal(signals, thresholds))

            # Only *fired* signals count toward the verdict: a raw similarity that did not cross
            # its own threshold is, by definition, not yet evidence — letting it leak into the
            # headline score would make per-signal thresholds unable to change the verdict, which
            # defeats Task 10's "every threshold ... reflected in the verdict" requirement.
            score = max((r.score for r in signals if r.fired), default=0.0)
            verdict = self._verdict_for(score, thresholds)

            checksum_matches = [
                c.claim_id for c in window_candidates
                if checksum and c.checksum_sha256 == checksum
            ]
            related_claim_ids = tuple(dict.fromkeys(
                checksum_matches
                + [c.claim_id for c in cross_employee_matches]
                + [c.claim_id for c in (multi_receipt_group or ())]
            ))

            report = DuplicateReport(
                claim_id=candidate.claim_id, verdict=verdict, score=score,
                signals=tuple(signals), related_claim_ids=related_claim_ids,
                cross_employee=bool(cross_employee_matches),
                multi_receipt_claim_ids=tuple(c.claim_id for c in (multi_receipt_group or ())),
            )

            span.set_attribute("verdict", verdict.value)
            span.set_attribute("score", score)
            span.set_attribute("signalCount", len(signals))

            self._persist(
                candidate, tenant_id=tenant_id, vendor_profile_id=vendor_profile.id,
                checksum=checksum, avg_hash=avg_hash, diff_hash=diff_hash,
            )
            self._vendors.record_claim(
                vendor_profile, amount_usd=candidate.amount_usd, occurred_at=_utcnow()
            )
            return report

    # --- signal scoring --------------------------------------------------------

    def _content_hashes(
        self, candidate: DuplicateCandidateInput
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        if not candidate.receipt_bytes:
            return None, None, None
        checksum = sha256_hex(candidate.receipt_bytes)
        avg_hash = image_hash.average_hash(candidate.receipt_bytes)
        diff_hash = image_hash.difference_hash(candidate.receipt_bytes)
        return checksum, avg_hash, diff_hash

    def _score_signals(
        self,
        candidate: DuplicateCandidateInput,
        window_candidates: Sequence[ClaimFingerprint],
        *,
        checksum: Optional[str],
        avg_hash: Optional[str],
        diff_hash: Optional[str],
        vendor_profile_id: uuid.UUID,
        amount_tolerance: Decimal,
    ) -> list[SignalResult]:
        thresholds = self._thresholds

        candidate_vector: Optional[Sequence[float]] = None
        other_vectors: list[Sequence[float]] = []
        if self._embedding_service is not None and candidate.ocr_text:
            candidate_vector = self._embedding_service.embed_query(candidate.ocr_text).values
            other_vectors = [c.ocr_embedding for c in window_candidates if c.ocr_embedding]

        candidate_invoice = InvoiceFields(
            vendor=candidate.merchant_vendor, expense_date=candidate.expense_date,
            amount_usd=candidate.amount_usd, invoice_number=candidate.invoice_number,
            claim_id=candidate.claim_id,
        )
        other_invoices = [
            InvoiceFields(
                vendor=c.merchant_vendor, expense_date=c.expense_date, amount_usd=c.amount_usd,
                invoice_number=c.invoice_number, claim_id=c.claim_id,
            )
            for c in window_candidates
        ]

        return [
            sha256_signal(
                checksum, [c.checksum_sha256 for c in window_candidates if c.checksum_sha256],
                threshold=thresholds.sha256_threshold,
            ),
            image_hash_signal(
                avg_hash, [c.average_hash for c in window_candidates if c.average_hash],
                threshold=thresholds.perceptual_hash_threshold,
            ),
            perceptual_hash_signal(
                diff_hash, [c.difference_hash for c in window_candidates if c.difference_hash],
                threshold=thresholds.perceptual_hash_threshold,
            ),
            ocr_similarity_signal(
                candidate.ocr_text,
                [c.ocr_text_excerpt for c in window_candidates if c.ocr_text_excerpt],
                threshold=thresholds.ocr_similarity_threshold,
            ),
            embedding_similarity_signal(
                candidate_vector, other_vectors,
                threshold=thresholds.embedding_similarity_threshold,
            ),
            vendor_alias_signal(
                vendor_profile_id, candidate.merchant_vendor,
                [
                    (c.vendor_profile_id, c.merchant_vendor) for c in window_candidates
                    if c.vendor_profile_id
                ],
                threshold=thresholds.vendor_alias_threshold,
            ),
            invoice_similarity_signal(
                candidate_invoice, other_invoices,
                threshold=thresholds.invoice_similarity_threshold,
                amount_tolerance=amount_tolerance,
            ),
        ]

    def _cross_employee_signal(
        self, matches: Sequence[ClaimFingerprint]
    ) -> SignalResult:
        fired = bool(matches)
        return SignalResult(
            kind=DuplicateSignalKind.CROSS_EMPLOYEE, score=1.0 if fired else 0.0, threshold=1.0,
            detail={"matchCount": len(matches)},
        )

    def _find_multi_receipt_group(
        self, candidate: DuplicateCandidateInput, *, vendor_profile_id: uuid.UUID,
        tenant_id: str, amount_tolerance: Decimal,
    ) -> Optional[tuple[ClaimFingerprint, ...]]:
        siblings = self._fingerprints.find_same_employee_vendor_window(
            tenant_id=tenant_id, employee_id=candidate.employee_id,
            vendor_profile_id=vendor_profile_id, expense_date=candidate.expense_date,
            date_window_days=self._thresholds.date_window_days, exclude_claim_id=candidate.claim_id,
        )
        if not siblings:
            return None
        by_claim_id = {s.claim_id: s for s in siblings}
        sibling_invoices = [
            InvoiceFields(
                vendor=s.merchant_vendor, expense_date=s.expense_date, amount_usd=s.amount_usd,
                invoice_number=s.invoice_number, claim_id=s.claim_id,
            )
            for s in siblings
        ]
        group = find_multi_receipt_group(
            candidate.amount_usd, sibling_invoices, amount_tolerance=amount_tolerance
        )
        if group is None:
            return None
        return tuple(by_claim_id[item.claim_id] for item in group)

    def _multi_receipt_signal(
        self, group: Optional[tuple[ClaimFingerprint, ...]]
    ) -> SignalResult:
        fired = bool(group)
        return SignalResult(
            kind=DuplicateSignalKind.MULTI_RECEIPT, score=1.0 if fired else 0.0, threshold=1.0,
            detail={"groupSize": len(group) if group else 0},
        )

    def _near_duplicate_signal(
        self, signals: Sequence[SignalResult], thresholds: DuplicateThresholds
    ) -> SignalResult:
        """Fires when the strongest *continuous* similarity signal crosses its threshold but the
        claim is not already an exact SHA-256 match — "looks like the same receipt, recompressed
        or resized" rather than "is the same bytes"."""
        continuous_scores = [
            r.score for r in signals if r.kind in _CONTINUOUS_SIGNAL_KINDS and r.score is not None
        ]
        sha_result = next(
            (r for r in signals if r.kind == DuplicateSignalKind.SHA256), None
        )
        exact_match = sha_result is not None and sha_result.score == 1.0
        if not continuous_scores or exact_match:
            return SignalResult(
                kind=DuplicateSignalKind.NEAR_DUPLICATE, score=None,
                threshold=thresholds.near_duplicate_threshold,
                detail={"reason": "exact_match" if exact_match else "no_continuous_signal"},
            )
        return SignalResult(
            kind=DuplicateSignalKind.NEAR_DUPLICATE, score=max(continuous_scores),
            threshold=thresholds.near_duplicate_threshold, detail={},
        )

    def _verdict_for(self, score: float, thresholds: DuplicateThresholds) -> DuplicateVerdict:
        """``score`` is already restricted to fired signals (see ``scan_and_record``), so any
        positive score means at least one signal cleared its own threshold."""
        if score <= 0.0:
            return DuplicateVerdict.NO_MATCH
        if score >= thresholds.confirmed_score:
            return DuplicateVerdict.CONFIRMED
        if score >= thresholds.likely_score:
            return DuplicateVerdict.LIKELY
        return DuplicateVerdict.POSSIBLE

    # --- persistence -------------------------------------------------------

    def _persist(
        self, candidate: DuplicateCandidateInput, *, tenant_id: str, vendor_profile_id: uuid.UUID,
        checksum: Optional[str], avg_hash: Optional[str], diff_hash: Optional[str],
    ) -> None:
        if self._fingerprints.get_by_claim_id(candidate.claim_id, tenant_id=tenant_id) is not None:
            logger.info(
                "duplicate_detection.fingerprint_already_recorded",
                extra={"claimId": str(candidate.claim_id)},
            )
            return

        ocr_embedding = None
        if self._embedding_service is not None and candidate.ocr_text:
            ocr_embedding = self._embedding_service.embed_query(candidate.ocr_text).values

        self._fingerprints.add(
            ClaimFingerprint(
                tenant_id=tenant_id,
                claim_id=candidate.claim_id,
                employee_id=candidate.employee_id,
                merchant_vendor=candidate.merchant_vendor,
                vendor_profile_id=vendor_profile_id,
                expense_date=candidate.expense_date,
                amount_usd=candidate.amount_usd,
                currency=candidate.currency,
                invoice_number=candidate.invoice_number,
                checksum_sha256=checksum,
                average_hash=avg_hash,
                difference_hash=diff_hash,
                ocr_text_excerpt=(candidate.ocr_text or None),
                ocr_embedding=ocr_embedding,
            )
        )


__all__ = ["DuplicateCandidateInput", "DuplicateDetectionEngine", "DuplicateReport"]
