"""Textract service — extracts receipt data via Amazon Textract ``AnalyzeExpense``.

Graceful degradation (mirrors ``gemini_service.py``): when ``TEXTRACT_ENABLED`` is false, or
boto3/credentials are missing, or the API call fails, a deterministic fallback extraction is
returned tagged ``source="fallback"``. This keeps local development working with no AWS account.

Return shape (always):
    {
      "source": "textract" | "fallback",
      "rawTextract": { ... verbatim AnalyzeExpense response (or a stub) ... },
      "summary": {"vendorName", "transactionDate", "totalAmount", "currency", "fields": [...]},
      "lineItems": [{"lineNumber", "description", "quantity", "unitPrice", "amount", "raw"}, ...],
      "error": Optional[str],
    }
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


def _get_textract_client():
    try:
        import boto3  # lazy import so the app boots without boto3 installed
    except Exception as e:  # pragma: no cover - import guard
        logger.warning("boto3 not available; Textract disabled: %s", e)
        return None
    try:
        return boto3.client("textract", region_name=settings.AWS_REGION)
    except Exception as e:  # pragma: no cover
        logger.error("Failed to initialize Textract client: %s", e)
        return None


def analyze_receipt_with_textract(
    data: Optional[bytes] = None,
    s3_bucket: Optional[str] = None,
    s3_key: Optional[str] = None,
    file_name: str = "receipt",
) -> Dict[str, Any]:
    """Run AnalyzeExpense on a document supplied by S3 reference or raw bytes.

    Prefers the S3 reference (``s3_bucket``/``s3_key``) when both are given; otherwise sends
    raw ``data`` bytes. Falls back deterministically when AWS is disabled/unavailable.
    """
    if not settings.TEXTRACT_ENABLED:
        return _fallback(file_name, "TEXTRACT_ENABLED is false")

    client = _get_textract_client()
    if client is None:
        return _fallback(file_name, "boto3/Textract client unavailable")

    try:
        if s3_bucket and s3_key:
            document = {"S3Object": {"Bucket": s3_bucket, "Name": s3_key}}
        elif data is not None:
            document = {"Bytes": data}
        else:
            return _fallback(file_name, "no document bytes or S3 reference provided")

        response = client.analyze_expense(Document=document)
        return _parse_response(response, file_name)
    except Exception as e:
        logger.error("Textract AnalyzeExpense failed for %s: %s", file_name, e)
        return _fallback(file_name, f"analyze_expense error: {e}")


def _parse_response(response: Dict[str, Any], file_name: str) -> Dict[str, Any]:
    """Normalize an AnalyzeExpense response into summary + line items."""
    fields: List[Dict[str, Any]] = []
    line_items: List[Dict[str, Any]] = []
    summary_lookup: Dict[str, str] = {}

    for doc in response.get("ExpenseDocuments", []) or []:
        # Summary fields
        for sf in doc.get("SummaryFields", []) or []:
            ftype = (sf.get("Type") or {}).get("Text")
            label = (sf.get("LabelDetection") or {}).get("Text")
            value = (sf.get("ValueDetection") or {}).get("Text")
            confidence = (sf.get("ValueDetection") or {}).get("Confidence")
            fields.append(
                {
                    "fieldType": ftype,
                    "fieldLabel": label,
                    "fieldValue": value,
                    "confidence": round(confidence, 2) if confidence is not None else None,
                }
            )
            if ftype and value and ftype not in summary_lookup:
                summary_lookup[ftype] = value

        # Line item groups
        line_no = 0
        for group in doc.get("LineItemGroups", []) or []:
            for li in group.get("LineItems", []) or []:
                line_no += 1
                row: Dict[str, Optional[str]] = {}
                for lf in li.get("LineItemExpenseFields", []) or []:
                    t = (lf.get("Type") or {}).get("Text")
                    v = (lf.get("ValueDetection") or {}).get("Text")
                    if t:
                        row[t] = v
                line_items.append(
                    {
                        "lineNumber": line_no,
                        "description": row.get("ITEM") or row.get("EXPENSE_ROW"),
                        "quantity": _to_num(row.get("QUANTITY")),
                        "unitPrice": _to_num(row.get("UNIT_PRICE") or row.get("PRICE")),
                        "amount": _to_num(row.get("PRICE") or row.get("AMOUNT")),
                        "raw": row,
                    }
                )

    summary = {
        "vendorName": summary_lookup.get("VENDOR_NAME") or summary_lookup.get("NAME"),
        "transactionDate": _extract_date(summary_lookup),
        "totalAmount": _to_num(summary_lookup.get("TOTAL")),
        "currency": summary_lookup.get("CURRENCY") or _infer_currency(summary_lookup),
        "fields": fields,
    }

    return {
        "source": "textract",
        "rawTextract": response,
        "summary": summary,
        "lineItems": line_items,
        "error": None,
    }


def _extract_date(summary_lookup: Dict[str, str]) -> Optional[str]:
    """Dynamically pick the first summary field whose type denotes a date.

    Textract labels dates with types like INVOICE_RECEIPT_DATE, ORDER_DATE, DUE_DATE —
    all contain "DATE". Rather than enumerate them, match any DATE-typed field. The raw
    string is returned verbatim; format-agnostic parsing happens at the persistence layer.
    """
    for ftype, value in summary_lookup.items():
        if ftype and "DATE" in ftype and value:
            return value
    return None


# Symbol → ISO code. Irreducible lookup: a bare symbol like "$" is ambiguous, so the
# common default is chosen. ISO codes found verbatim in the text are preferred over this.
_CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR"}


def _infer_currency(summary_lookup: Dict[str, str]) -> Optional[str]:
    """Detect currency from the monetary text when Textract's CURRENCY field is empty.

    Prefers an explicit ISO code appearing in any value; falls back to a currency symbol.
    """
    import re

    for value in summary_lookup.values():
        if not value:
            continue
        iso = re.search(r"\b([A-Z]{3})\b", value)
        if iso and iso.group(1) in {code for code in _CURRENCY_SYMBOLS.values()} | {
            "AUD", "CAD", "CHF", "CNY", "SGD", "NZD", "HKD", "AED"
        }:
            return iso.group(1)
        for sym, code in _CURRENCY_SYMBOLS.items():
            if sym in value:
                return code
    return None


def _to_num(value: Optional[str]):
    if value is None:
        return None
    cleaned = "".join(ch for ch in str(value) if ch.isdigit() or ch in ".-")
    try:
        return float(cleaned) if cleaned not in ("", "-", ".", "-.") else None
    except ValueError:
        return None


def _fallback(file_name: str, reason: str) -> Dict[str, Any]:
    """Deterministic stub extraction so local dev works with no AWS."""
    logger.info("Textract fallback for %s: %s", file_name, reason)
    raw_stub = {
        "DocumentMetadata": {"Pages": 1},
        "ExpenseDocuments": [
            {
                "ExpenseIndex": 1,
                "SummaryFields": [
                    {"Type": {"Text": "VENDOR_NAME"}, "ValueDetection": {"Text": "Acme Merchant Cafe", "Confidence": 99.0}},
                    {"Type": {"Text": "INVOICE_RECEIPT_DATE"}, "ValueDetection": {"Text": "2026-07-23", "Confidence": 99.0}},
                    {"Type": {"Text": "TOTAL"}, "ValueDetection": {"Text": "28.50", "Confidence": 99.0}},
                    {"Type": {"Text": "CURRENCY"}, "ValueDetection": {"Text": "USD", "Confidence": 99.0}},
                ],
                "LineItemGroups": [
                    {
                        "LineItems": [
                            {"LineItemExpenseFields": [
                                {"Type": {"Text": "ITEM"}, "ValueDetection": {"Text": "Business Lunch Special"}},
                                {"Type": {"Text": "PRICE"}, "ValueDetection": {"Text": "24.00"}},
                            ]},
                            {"LineItemExpenseFields": [
                                {"Type": {"Text": "ITEM"}, "ValueDetection": {"Text": "Sparkling Water"}},
                                {"Type": {"Text": "PRICE"}, "ValueDetection": {"Text": "4.50"}},
                            ]},
                        ]
                    }
                ],
            }
        ],
        "_fallback": True,
        "_reason": reason,
        "_fileName": file_name,
    }
    parsed = _parse_response(raw_stub, file_name)
    parsed["source"] = "fallback"
    parsed["error"] = None
    return parsed
