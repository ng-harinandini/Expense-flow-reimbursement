"""Receipt document helpers: object URLs, and normalizing extractor output.

Two responsibilities, both consequences of ``expense_items`` storing the receipt inline:

1. **The object URL is the only record of the S3 key.** ``expense_items`` has no
   ``s3_bucket``/``s3_key``/``s3_region`` columns — the bucket and region come from settings and
   the key is recovered from ``file_url``. That makes the URL format load-bearing, so building and
   parsing it lives here rather than being reconstructed ad hoc at each call site.

2. **Two extractors, two shapes.** Textract reports a confidence per summary field on a 0-100
   scale; Gemini reports a single 0-1 ``confidenceScore``. Both are normalized to a 0-100 mapping
   here so ``expense_items.ocr_confidence`` is comparable regardless of which engine ran.

The ``_parse_date`` / ``_to_decimal`` helpers were salvaged from the deleted ``app/api/receipts.py``.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional
from urllib.parse import quote, unquote, urlparse

from app.core.config import settings

logger = logging.getLogger(__name__)

#: Key prefix for receipts uploaded against an expense item. The employee's id is embedded so the
#: submit path can verify that a client echoing back a ``file_url`` owns the object it points at.
ITEM_RECEIPT_PREFIX = "expense-items"


# --- object URLs -------------------------------------------------------------


def build_object_key(employee_id: str, upload_id: str, file_name: str) -> str:
    """``expense-items/{employee}/{upload}/{name}`` — the employee segment is the ownership check."""
    safe_name = (file_name or "receipt").replace("\\", "/").rsplit("/", 1)[-1].strip()
    return f"{ITEM_RECEIPT_PREFIX}/{employee_id}/{upload_id}/{safe_name or 'receipt'}"


def build_file_url(key: str) -> str:
    """Virtual-hosted-style S3 URL for ``key``, or a relative marker when S3 is not configured."""
    bucket = settings.S3_BUCKET_NAME
    if not bucket:
        return f"/{key}"
    region = settings.AWS_REGION
    host = f"{bucket}.s3.{region}.amazonaws.com" if region else f"{bucket}.s3.amazonaws.com"
    return f"https://{host}/{quote(key)}"


def parse_object_key(file_url: Optional[str]) -> Optional[str]:
    """Recover the object key from a URL built by :func:`build_file_url`.

    Returns ``None`` for a URL this module did not produce — callers must treat that as "no
    verifiable key" rather than assuming ownership.
    """
    if not file_url:
        return None
    path = urlparse(file_url).path if "://" in file_url else file_url
    key = unquote(path).lstrip("/")
    return key or None


def owns_object(file_url: Optional[str], employee_id: str) -> bool:
    """Whether ``file_url`` points at an object uploaded by ``employee_id``.

    The client echoes ``file_url`` back at submit time, so without this a caller could attach a
    receipt uploaded by someone else. With no ``s3_key`` column to check against, the employee
    segment of the key is the only server-side evidence of ownership.
    """
    key = parse_object_key(file_url)
    if not key:
        return False
    return key.startswith(f"{ITEM_RECEIPT_PREFIX}/{employee_id}/")


# --- scalar coercion ---------------------------------------------------------


def to_decimal(value: Any) -> Optional[Decimal]:
    """Best-effort ``Decimal``; ``None`` when the value is absent or unparseable."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def parse_date(value: Optional[str]) -> Optional[date]:
    """Parse a date from arbitrary text ("12 October, 2025", "2025-10-12", "10/12/25").

    Uses dateutil's fuzzy parser so no format list needs to be maintained.
    """
    if not value:
        return None
    try:
        from dateutil import parser as _dateparser

        return _dateparser.parse(str(value).strip(), fuzzy=True).date()
    except (ValueError, OverflowError, TypeError):
        return None


# --- extractor output normalization ------------------------------------------

#: Textract summary field types -> the key used in ``ocr_confidence``.
_TEXTRACT_FIELD_ALIASES = {
    "VENDOR_NAME": "vendor",
    "TOTAL": "total",
    "INVOICE_RECEIPT_DATE": "date",
    "SUBTOTAL": "subtotal",
    "TAX": "tax",
}


def normalize_confidence(extraction: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Per-field confidences on a 0-100 scale, or ``None`` when the extractor reported none.

    Textract already reports 0-100 per summary field. Gemini reports one overall ``confidenceScore``
    in 0-1, which is scaled up and recorded under ``overall`` so the two are directly comparable.
    """
    confidences: Dict[str, float] = {}

    summary = extraction.get("summary") or {}
    for field in summary.get("fields") or []:
        raw = field.get("confidence")
        if raw is None:
            continue
        label = _TEXTRACT_FIELD_ALIASES.get(
            str(field.get("fieldType") or "").upper()
        ) or str(field.get("fieldType") or "").lower()
        if label:
            confidences[label] = round(float(raw), 2)

    overall = extraction.get("confidenceScore")
    if overall is not None:
        try:
            value = float(overall)
        except (TypeError, ValueError):
            value = None
        if value is not None:
            # Gemini reports 0-1; anything above 1 is already on the 0-100 scale.
            confidences["overall"] = round(value * 100 if value <= 1 else value, 2)

    return confidences or None


def summarize_extraction(extraction: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten either extractor's output into the ``suggested*`` prefill fields.

    Returns ``{vendor, transactionDate, totalAmount, currency}`` with ``None`` for anything the
    extractor did not produce. The values are *suggestions*: the employee may overwrite any of
    them, and the override is recorded in ``expense_items.employee_corrected_data``.
    """
    summary = extraction.get("summary") or {}

    vendor = summary.get("vendorName") or extraction.get("vendorName")
    raw_date = summary.get("transactionDate") or extraction.get("transactionDate")
    total = summary.get("totalAmount")
    if total is None:
        total = extraction.get("totalAmount")
    currency = summary.get("currency") or extraction.get("currency")

    parsed_date = parse_date(raw_date)
    parsed_total = to_decimal(total)

    return {
        "vendor": str(vendor).strip() if vendor else None,
        "transactionDate": parsed_date.isoformat() if parsed_date else None,
        "totalAmount": float(parsed_total) if parsed_total is not None else None,
        "currency": str(currency).strip().upper()[:3] if currency else None,
    }


def resolve_field(
    field: str,
    *,
    submitted: Any = None,
    corrections: Optional[Dict[str, Any]] = None,
    extracted: Optional[Dict[str, Any]] = None,
) -> Any:
    """Resolve one item field as *correction -> extraction -> submitted value*.

    Mirrors the three-layer model documented on :class:`~app.models.expense_item.ExpenseItem`:
    the extraction is a guess, the correction is the human's override of that guess, and an
    explicitly submitted value wins when neither applies (a manually entered item has no receipt).
    """
    if corrections and field in corrections and corrections[field] is not None:
        return corrections[field]
    if submitted is not None:
        return submitted
    if extracted and field in extracted and extracted[field] is not None:
        return extracted[field]
    return None
