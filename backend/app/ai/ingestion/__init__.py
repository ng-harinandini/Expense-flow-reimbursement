"""Ingestion pipeline (Task 3 / T004-M8): raw bytes to indexed, versioned, embedded chunks.

:func:`ingest_document` is the entry point. :mod:`app.ai.ingestion.sources` holds the four ways a
document's bytes can arrive (upload, filesystem, S3, inline); :mod:`app.ai.ingestion.connectors`
is the interface-only contract for future scheduled/polling sources;
:mod:`app.ai.ingestion.profiles` maps every :class:`~app.ai.core.enums.KnowledgeSourceType` to a
chunking strategy.
"""

from __future__ import annotations

from app.ai.ingestion.pipeline import (
    DocumentMetadataInput,
    IngestionResult,
    ingest_document,
)
from app.ai.ingestion.profiles import IngestionProfile, profile_for

__all__ = [
    "DocumentMetadataInput",
    "IngestionProfile",
    "IngestionResult",
    "ingest_document",
    "profile_for",
]
