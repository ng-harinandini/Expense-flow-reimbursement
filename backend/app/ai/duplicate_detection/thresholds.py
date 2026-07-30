"""Bridge between global :class:`AISettings` and a per-call :class:`DuplicateThresholds`.

Mirrors :func:`app.ai.chunking.config.build_chunking_config`: thresholds are configuration, never
constants in code (Task 10's own constraint), so tuning them is an operational change, not a
deploy — but the engine itself takes a small frozen dataclass rather than the ~60-field
:class:`AISettings` object, which keeps it testable with hand-built thresholds and independent of
which settings fields exist.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ai.core.config import AISettings, ai_settings


@dataclass(frozen=True, slots=True)
class DuplicateThresholds:
    """Every knob :mod:`app.ai.duplicate_detection.engine` reads, seeded from ``AISettings``."""

    sha256_threshold: float = 1.0
    perceptual_hash_threshold: float = 0.90
    ocr_similarity_threshold: float = 0.85
    embedding_similarity_threshold: float = 0.93
    vendor_alias_threshold: float = 0.85
    invoice_similarity_threshold: float = 0.90
    near_duplicate_threshold: float = 0.80
    likely_score: float = 0.75
    confirmed_score: float = 0.95
    amount_tolerance: float = 0.01
    date_window_days: int = 7
    max_candidates: int = 200
    cross_employee_enabled: bool = True


def build_duplicate_thresholds(
    settings: AISettings | None = None, **overrides: object
) -> DuplicateThresholds:
    """``DuplicateThresholds`` seeded from ``settings`` (default: the process singleton).

    Any field name may be overridden per call, e.g.
    ``build_duplicate_thresholds(near_duplicate_threshold=0.7)`` for a tenant that wants a more
    sensitive scan.
    """
    s = settings or ai_settings
    fields = {
        "sha256_threshold": s.DUP_SHA256_THRESHOLD,
        "perceptual_hash_threshold": s.DUP_PERCEPTUAL_HASH_THRESHOLD,
        "ocr_similarity_threshold": s.DUP_OCR_SIMILARITY_THRESHOLD,
        "embedding_similarity_threshold": s.DUP_EMBEDDING_SIMILARITY_THRESHOLD,
        "vendor_alias_threshold": s.DUP_VENDOR_ALIAS_THRESHOLD,
        "invoice_similarity_threshold": s.DUP_INVOICE_SIMILARITY_THRESHOLD,
        "near_duplicate_threshold": s.DUP_NEAR_DUPLICATE_THRESHOLD,
        "likely_score": s.DUP_LIKELY_SCORE,
        "confirmed_score": s.DUP_CONFIRMED_SCORE,
        "amount_tolerance": s.DUP_AMOUNT_TOLERANCE,
        "date_window_days": s.DUP_DATE_WINDOW_DAYS,
        "max_candidates": s.DUP_MAX_CANDIDATES,
        "cross_employee_enabled": s.DUP_CROSS_EMPLOYEE_ENABLED,
    }
    fields.update(overrides)
    return DuplicateThresholds(**fields)


__all__ = ["DuplicateThresholds", "build_duplicate_thresholds"]
