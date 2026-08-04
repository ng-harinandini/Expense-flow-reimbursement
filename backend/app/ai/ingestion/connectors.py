"""Connector contract for future scheduled/polling knowledge sources.

**Interface only.** A `Connector` differs from a plain :class:`~app.ai.ingestion.sources.Source` in
that it can *enumerate* what's available upstream (so a scheduler can discover new or changed
documents) before fetching any one of them — the shape a SharePoint, Confluence or Google Drive
integration would need. No concrete connector ships in this macro; building one against a real
external API is out of scope until a specific integration is funded.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from app.ai.core.types import RawDocument


@dataclass(frozen=True, slots=True)
class ConnectorDocumentRef:
    """Enough identity for a connector to later fetch one specific upstream document."""

    external_id: str
    display_name: str
    updated_at: datetime | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class Connector(Protocol):
    """Enumerates and fetches documents from an external, polled system."""

    name: str

    def is_available(self) -> bool:
        """False when credentials or configuration for this connector are missing."""
        ...

    def list_documents(self) -> Sequence[ConnectorDocumentRef]:
        """Every document currently visible upstream, for a scheduler to diff against what has
        already been ingested (by ``external_id`` and ``updated_at``)."""
        ...

    def fetch(self, ref: ConnectorDocumentRef) -> RawDocument:
        """Fetch one document's bytes, given a ref returned by :meth:`list_documents`."""
        ...


__all__ = ["Connector", "ConnectorDocumentRef"]
