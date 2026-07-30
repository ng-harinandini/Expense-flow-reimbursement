"""A document constructed directly in-process — tests, scripts, and programmatic ingestion."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.ai.core.enums import KnowledgeSourceType
from app.ai.core.types import RawDocument


class InlineSource:
    """Wraps plain text (or raw bytes) supplied directly, with no upload or file system involved."""

    def __init__(
        self,
        *,
        text: str | None = None,
        content: bytes | None = None,
        file_name: str = "inline.txt",
        mime_type: str = "text/plain",
        source_type: KnowledgeSourceType = KnowledgeSourceType.OTHER,
        tenant_id: str = "default",
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        if text is None and content is None:
            raise ValueError("InlineSource requires either text or content.")
        self._content = content if content is not None else text.encode("utf-8")
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


__all__ = ["InlineSource"]
