"""A document fetched from Amazon S3.

Guarded exactly like every other optional AWS dependency in this platform
(:mod:`app.ai.parsing.image_ocr`, the embedding/rerank Bedrock adapters): ``boto3`` is imported
lazily, ``is_available()`` checks for a resolvable credential without a network call, and every
failure to fetch raises :class:`ProviderError` rather than propagating a raw boto3 exception.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.ai.core.enums import KnowledgeSourceType
from app.ai.core.errors import ProviderError, ProviderNotConfiguredError
from app.ai.core.types import RawDocument

NAME = "s3"


class S3Source:
    """Fetches one object from S3 as a :class:`RawDocument`."""

    def __init__(
        self,
        *,
        bucket: str,
        key: str,
        region: str | None = None,
        mime_type: str | None = None,
        source_type: KnowledgeSourceType = KnowledgeSourceType.OTHER,
        tenant_id: str = "default",
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self._bucket = bucket
        self._key = key
        self._region = region
        self._mime_type = mime_type
        self._source_type = source_type
        self._tenant_id = tenant_id
        self._metadata = dict(metadata or {})

    def is_available(self) -> bool:
        """Whether S3 can plausibly be reached: boto3 installed and a credential resolvable."""
        try:
            import boto3
        except ImportError:
            return False
        try:
            return boto3.Session().get_credentials() is not None
        except Exception:
            return False

    def fetch(self) -> RawDocument:
        if not self.is_available():
            raise ProviderNotConfiguredError(
                provider=NAME, kind="INGESTION_SOURCE",
                remedy="Install boto3 and configure AWS credentials.",
            )
        try:
            import boto3

            client = boto3.client("s3", region_name=self._region)
            response = client.get_object(Bucket=self._bucket, Key=self._key)
            content = response["Body"].read()
        except Exception as exc:
            raise ProviderError(
                NAME, f"GetObject failed for s3://{self._bucket}/{self._key}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        file_name = self._key.rsplit("/", 1)[-1]
        mime_type = self._mime_type or response.get("ContentType")
        return RawDocument(
            content=content,
            file_name=file_name,
            mime_type=mime_type,
            source_type=self._source_type,
            source_uri=f"s3://{self._bucket}/{self._key}",
            tenant_id=self._tenant_id,
            metadata=self._metadata,
        )


__all__ = ["S3Source"]
