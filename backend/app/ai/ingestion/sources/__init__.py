"""Source abstraction: how a raw document's bytes arrive at the pipeline.

Every source implements the same tiny contract — ``fetch() -> RawDocument`` — so
:func:`app.ai.ingestion.pipeline.ingest_document` never needs to know whether the bytes came from an
API upload already in memory, a local path, S3, or a test fixture. Four concrete sources ship here;
:mod:`app.ai.ingestion.connectors` defines the (currently interface-only) protocol for future
scheduled/polling connectors such as SharePoint or Confluence.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.ai.core.types import RawDocument


@runtime_checkable
class Source(Protocol):
    """Produces one :class:`RawDocument` on demand."""

    def fetch(self) -> RawDocument:
        """Return the document's bytes and identifying metadata. May raise on I/O failure."""
        ...


__all__ = ["Source"]
