"""S3 service — uploads the original receipt document to Amazon S3.

Graceful degradation (mirrors ``gemini_service.py``): if S3 is not usable — no bucket
configured, boto3 missing, no credentials, or the API call fails — this NEVER raises. It
returns a fallback marker so local development works with no AWS account, and the caller can
still persist the receipt (with ``source="fallback"`` and null S3 location).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


def _get_s3_client():
    """Return a boto3 S3 client, or None if boto3/credentials are unavailable."""
    try:
        import boto3  # imported lazily so the app boots without boto3 installed
    except Exception as e:  # pragma: no cover - import guard
        logger.warning("boto3 not available; S3 uploads disabled: %s", e)
        return None

    try:
        return boto3.client("s3", region_name=settings.AWS_REGION)
    except Exception as e:  # pragma: no cover - credential/config errors
        logger.error("Failed to initialize S3 client: %s", e)
        return None


def s3_enabled() -> bool:
    return bool(settings.S3_BUCKET_NAME)


def upload_receipt_to_s3(
    data: bytes,
    key: str,
    content_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Upload ``data`` to S3 at ``key``.

    Returns a location dict on success::

        {"stored": True, "source": "s3", "bucket": ..., "key": ..., "region": ...}

    Returns a fallback marker (never raises) when S3 is disabled/unavailable::

        {"stored": False, "source": "fallback", "bucket": None, "key": None,
         "region": None, "reason": "..."}
    """
    if not s3_enabled():
        return _fallback("S3_BUCKET_NAME not configured")

    client = _get_s3_client()
    if client is None:
        return _fallback("boto3/S3 client unavailable")

    try:
        extra = {"ContentType": content_type} if content_type else {}
        client.put_object(Bucket=settings.S3_BUCKET_NAME, Key=key, Body=data, **extra)
        return {
            "stored": True,
            "source": "s3",
            "bucket": settings.S3_BUCKET_NAME,
            "key": key,
            "region": settings.AWS_REGION,
        }
    except Exception as e:
        logger.error("S3 upload failed for key=%s: %s", key, e)
        return _fallback(f"upload error: {e}")


def _fallback(reason: str) -> Dict[str, Any]:
    logger.info("S3 upload skipped (fallback): %s", reason)
    return {
        "stored": False,
        "source": "fallback",
        "bucket": None,
        "key": None,
        "region": None,
        "reason": reason,
    }
