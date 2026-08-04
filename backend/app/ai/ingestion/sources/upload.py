"""A document already fully in memory — the shape an API multipart upload arrives in."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.ai.core.enums import KnowledgeSourceType
from app.ai.core.types import RawDocument


class UploadSource:
    """Wraps bytes a caller already has (e.g. from ``UploadFile.read()``) as a :class:`Source`."""

    def __init__(
        self,
        *,
        content: bytes,
        file_name: str,
        mime_type: str | None = None,
        source_type: KnowledgeSourceType = KnowledgeSourceType.OTHER,
        tenant_id: str = "default",
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self._content = content
        self._file_name = file_name
        self._mime_type = mime_type
        self._source_type = source_type
        self._tenant_id = tenant_id
        self._metadata = dict(metadata or {})

    def fetch(self) -> RawDocument:
        return RawDocument(
            content=self._content,
            file_name=self._file_name,
            mime_type=self._mime_type,
            source_type=self._source_type,
            source_uri=None,
            tenant_id=self._tenant_id,
            metadata=self._metadata,
        )


__all__ = ["UploadSource"]
