"""A document read from a local path — offline ingestion, scripts, and test fixtures on disk."""

from __future__ import annotations

import mimetypes
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.ai.core.enums import KnowledgeSourceType
from app.ai.core.types import RawDocument


class FilesystemSource:
    """Reads the file at ``path`` into memory on :meth:`fetch`. Raises ``OSError`` if unreadable."""

    def __init__(
        self,
        *,
        path: str | Path,
        mime_type: str | None = None,
        source_type: KnowledgeSourceType = KnowledgeSourceType.OTHER,
        tenant_id: str = "default",
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self._path = Path(path)
        self._mime_type = mime_type
        self._source_type = source_type
        self._tenant_id = tenant_id
        self._metadata = dict(metadata or {})

    def fetch(self) -> RawDocument:
        content = self._path.read_bytes()
        mime_type = self._mime_type or mimetypes.guess_type(self._path.name)[0]
        return RawDocument(
            content=content,
            file_name=self._path.name,
            mime_type=mime_type,
            source_type=self._source_type,
            source_uri=str(self._path),
            tenant_id=self._tenant_id,
            metadata=self._metadata,
        )


__all__ = ["FilesystemSource"]
