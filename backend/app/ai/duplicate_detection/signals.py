"""Individual duplicate-detection signals, each scored and reported independently.

Task 10's own constraint: *"a verdict must state which signal fired and at what score"* — a single
blended number would be unexplainable to a reviewer or an auditor. Every function here takes
already-fetched plain values (no session, no repository) so each is unit-testable with hand-built
fixtures; :mod:`app.ai.duplicate_detection.engine` is the only thing that fetches candidates and
calls these.

Seven of the eleven :class:`~app.ai.core.enums.DuplicateSignalKind` members are scored here —
straightforward "how similar is A to the most similar candidate in B" comparisons. The remaining
four (``HISTORICAL_CLAIM_SIMILARITY``, ``NEAR_DUPLICATE``, ``MULTI_RECEIPT``, ``CROSS_EMPLOYEE``)
are *case* signals that depend on cross-signal composition or a structurally different query (an
employee-scoped subset-sum search, a cross-employee match), and are built directly in the engine.

A signal's ``score`` is ``None`` when it could not be evaluated at all (no input on one side, or no
candidates to compare against) — distinct from a evaluated-but-low ``0.0``, which is a genuine "not
similar" finding. The engine reports ``None`` signals as *not applicable*, never as evidence.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from app.ai.core.enums import DuplicateSignalKind
from app.ai.duplicate_detection.hashing import hamming_similarity
from app.ai.duplicate_detection.invoice import InvoiceFields, invoice_similarity
from app.ai.duplicate_detection.text_similarity import text_similarity_ratio
from app.ai.embeddings.math import cosine_similarity
from app.ai.repositories.duplicate_detection_repository import normalize_vendor_name


class SignalResult:
    """One signal's outcome: its score, the threshold it was judged against, and whether it fired.

    A plain class (not a frozen dataclass) only because ``kind`` — an enum — and ``detail`` — a
    free-form mapping for the explanation — read more clearly with named construction via
    :func:`_result` below in every call site; the shape is otherwise a value object.
    """

    __slots__ = ("kind", "score", "threshold", "fired", "detail")

    def __init__(
        self, *, kind: DuplicateSignalKind, score: Optional[float], threshold: float,
        detail: Mapping[str, Any],
    ) -> None:
        self.kind = kind
        self.score = score
        self.threshold = threshold
        self.fired = score is not None and score >= threshold
        self.detail = dict(detail)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<SignalResult {self.kind.value} score={self.score} fired={self.fired}>"


def _result(
    kind: DuplicateSignalKind, score: Optional[float], threshold: float, **detail: Any
) -> SignalResult:
    return SignalResult(kind=kind, score=score, threshold=threshold, detail=detail)


def sha256_signal(
    candidate_checksum: Optional[str], other_checksums: Sequence[str], *, threshold: float
) -> SignalResult:
    """Exact byte-for-byte match: score is always 1.0 or 0.0, never partial."""
    if not candidate_checksum:
        return _result(DuplicateSignalKind.SHA256, None, threshold, reason="no_checksum")
    matched = candidate_checksum in other_checksums
    return _result(
        DuplicateSignalKind.SHA256, 1.0 if matched else 0.0, threshold, matched=matched
    )


def _perceptual_signal(
    kind: DuplicateSignalKind, candidate_hash: Optional[str], other_hashes: Sequence[str],
    *, threshold: float, bits: int = 64,
) -> SignalResult:
    if not candidate_hash or not other_hashes:
        return _result(kind, None, threshold, reason="no_hash_or_candidates")
    best = max(hamming_similarity(candidate_hash, h, bits=bits) for h in other_hashes)
    return _result(kind, best, threshold, candidateCount=len(other_hashes))


def image_hash_signal(
    candidate_hash: Optional[str], other_hashes: Sequence[str], *, threshold: float
) -> SignalResult:
    """Average-hash comparison — robust to recompression and resizing."""
    return _perceptual_signal(
        DuplicateSignalKind.IMAGE_HASH, candidate_hash, other_hashes, threshold=threshold
    )


def perceptual_hash_signal(
    candidate_hash: Optional[str], other_hashes: Sequence[str], *, threshold: float
) -> SignalResult:
    """Difference-hash comparison — a gradient-based signal independent of ``IMAGE_HASH``."""
    return _perceptual_signal(
        DuplicateSignalKind.PERCEPTUAL_HASH, candidate_hash, other_hashes, threshold=threshold
    )


def ocr_similarity_signal(
    candidate_text: Optional[str], other_texts: Sequence[str], *, threshold: float
) -> SignalResult:
    if not candidate_text or not other_texts:
        return _result(
            DuplicateSignalKind.OCR_SIMILARITY, None, threshold, reason="no_text_or_candidates"
        )
    ratios = [
        ratio for ratio in (text_similarity_ratio(candidate_text, t) for t in other_texts)
        if ratio is not None
    ]
    if not ratios:
        return _result(
            DuplicateSignalKind.OCR_SIMILARITY, None, threshold, reason="all_too_short"
        )
    return _result(
        DuplicateSignalKind.OCR_SIMILARITY, max(ratios), threshold, candidateCount=len(ratios)
    )


def embedding_similarity_signal(
    candidate_vector: Optional[Sequence[float]], other_vectors: Sequence[Sequence[float]],
    *, threshold: float,
) -> SignalResult:
    if candidate_vector is None or not other_vectors:
        return _result(
            DuplicateSignalKind.EMBEDDING_SIMILARITY, None, threshold,
            reason="no_vector_or_candidates",
        )
    best = max(cosine_similarity(candidate_vector, vector) for vector in other_vectors)
    return _result(
        DuplicateSignalKind.EMBEDDING_SIMILARITY, best, threshold,
        candidateCount=len(other_vectors),
    )


def vendor_alias_signal(
    candidate_vendor_profile_id: Optional[object],
    candidate_vendor_text: str,
    others: Sequence[tuple[object, str]],
    *, threshold: float,
) -> SignalResult:
    """Whether this claim's *resolved* vendor matches another claim's under a genuinely different
    spelling — catching "Uber" vs. "UBER *TRIP" as the same vendor, which no string-equality check
    would.

    Deliberately requires the raw text to *differ*. The candidate set this is scored against
    (``window_candidates`` in the engine) is already vendor-scoped by construction — every
    candidate that reaches this function already shares ``candidate_vendor_profile_id`` — so
    counting an identical-spelling match here too would make this signal fire on every claim in
    the window regardless of amount, date or employee, collapsing it into "was there any candidate
    at all" rather than genuine alias-resolution evidence. Two claims with the exact same vendor
    string need no alias resolution to know they match; that overlap is what
    ``INVOICE_SIMILARITY``/``CROSS_EMPLOYEE``/``MULTI_RECEIPT`` independently test for.
    """
    if candidate_vendor_profile_id is None:
        return _result(
            DuplicateSignalKind.VENDOR_ALIAS, None, threshold, reason="vendor_unresolved"
        )
    normalized_candidate = normalize_vendor_name(candidate_vendor_text)
    alias_matches = [
        other_text for other_id, other_text in others
        if other_id == candidate_vendor_profile_id
        and normalize_vendor_name(other_text) != normalized_candidate
    ]
    matched = bool(alias_matches)
    return _result(
        DuplicateSignalKind.VENDOR_ALIAS, 1.0 if matched else 0.0, threshold,
        matched=matched, aliasMatchCount=len(alias_matches),
    )


def invoice_similarity_signal(
    candidate: InvoiceFields, others: Sequence[InvoiceFields],
    *, threshold: float, amount_tolerance,
) -> SignalResult:
    if not others:
        return _result(
            DuplicateSignalKind.INVOICE_SIMILARITY, None, threshold, reason="no_candidates"
        )
    best = max(
        invoice_similarity(candidate, other, amount_tolerance=amount_tolerance)
        for other in others
    )
    return _result(
        DuplicateSignalKind.INVOICE_SIMILARITY, best, threshold, candidateCount=len(others)
    )


__all__ = [
    "SignalResult",
    "embedding_similarity_signal",
    "image_hash_signal",
    "invoice_similarity_signal",
    "ocr_similarity_signal",
    "perceptual_hash_signal",
    "sha256_signal",
    "vendor_alias_signal",
]
