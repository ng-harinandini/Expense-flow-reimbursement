"""Duplicate detection & vendor intelligence (Task 10).

``DuplicateDetectionService`` (see ``service.py``) is the only public entry point. Everything else
in this package — hashing, image hashing, text/invoice similarity, thresholds, signals, the engine
— is an internal, exactly like ``app.ai.retrieval``/``app.ai.ingestion``/``app.ai.memory`` are
internals of ``app.ai.knowledge``. The architecture test enforces this.
"""

from app.ai.duplicate_detection.engine import (  # noqa: F401
    DuplicateCandidateInput,
    DuplicateDetectionEngine,
    DuplicateReport,
)
from app.ai.duplicate_detection.service import DuplicateDetectionService  # noqa: F401

__all__ = [
    "DuplicateCandidateInput",
    "DuplicateDetectionEngine",
    "DuplicateDetectionService",
    "DuplicateReport",
]
