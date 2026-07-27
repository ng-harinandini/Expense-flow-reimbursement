"""Receipts API — upload → S3 → Textract → PostgreSQL persistence.

Endpoints:
  POST /receipts/upload   multipart file (+ optional employeeId) → 201 detail
  GET  /receipts          list (summary-level)
  GET  /receipts/{id}     full record incl. raw Textract JSON, fields, line items
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.receipt import (
    ExtractionStatus,
    Receipt,
    ReceiptField,
    ReceiptLineItem,
)
from app.schemas.schemas import (
    ReceiptDetailSchema,
    ReceiptSummarySchema,
    ReceiptUploadResponseSchema,
)
from app.services.s3_service import upload_receipt_to_s3
from app.services.textract_service import analyze_receipt_with_textract

router = APIRouter(prefix="/receipts", tags=["Receipts"])


# --- helpers -----------------------------------------------------------------

def _to_decimal(value) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _parse_date(value: Optional[str]) -> Optional[date]:
    """Parse a date from arbitrary text (e.g. "12 October, 2025", "2025-10-12", "10/12/25").

    Uses dateutil's fuzzy parser so no format list needs to be maintained.
    """
    if not value:
        return None
    try:
        from dateutil import parser as _dateparser

        return _dateparser.parse(value.strip(), fuzzy=True).date()
    except (ValueError, OverflowError, TypeError):
        return None


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _serialize_summary(r: Receipt) -> dict:
    return {
        "id": str(r.id),
        "fileName": r.file_name,
        "contentType": r.content_type,
        "fileSizeBytes": r.file_size_bytes,
        "employeeId": r.employee_id,
        "extractionStatus": r.extraction_status.value
        if isinstance(r.extraction_status, ExtractionStatus)
        else str(r.extraction_status),
        "extractionSource": r.extraction_source,
        "vendorName": r.vendor_name,
        "transactionDate": r.transaction_date.isoformat() if r.transaction_date else None,
        "totalAmount": float(r.total_amount) if r.total_amount is not None else None,
        "currency": r.currency,
        "s3": {"bucket": r.s3_bucket, "key": r.s3_key, "region": r.s3_region}
        if r.s3_bucket
        else None,
        "createdAt": _iso(r.created_at),
        "updatedAt": _iso(r.updated_at),
    }


def _serialize_detail(r: Receipt) -> dict:
    base = _serialize_summary(r)
    base.update(
        {
            "rawTextract": r.raw_textract,
            "normalizedExtraction": r.normalized_extraction,
            "errorMessage": r.error_message,
            "fields": [
                {
                    "fieldType": f.field_type,
                    "fieldLabel": f.field_label,
                    "fieldValue": f.field_value,
                    "confidence": float(f.confidence) if f.confidence is not None else None,
                }
                for f in r.fields
            ],
            "lineItems": [
                {
                    "lineNumber": li.line_number,
                    "description": li.description,
                    "quantity": float(li.quantity) if li.quantity is not None else None,
                    "unitPrice": float(li.unit_price) if li.unit_price is not None else None,
                    "amount": float(li.amount) if li.amount is not None else None,
                    "raw": li.raw,
                }
                for li in r.line_items
            ],
        }
    )
    return base


# --- endpoints ---------------------------------------------------------------

@router.post(
    "/upload",
    status_code=status.HTTP_201_CREATED,
    response_model=ReceiptUploadResponseSchema,
)
async def upload_receipt(
    file: UploadFile = File(...),
    employeeId: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    receipt_id = uuid.uuid4()

    # 1) Store original document in S3 (safe-skips to fallback when disabled/unavailable).
    key = f"receipts/{receipt_id}/{file.filename or 'receipt'}"
    s3 = upload_receipt_to_s3(data, key, file.content_type)

    # 2) Extract with Textract (S3 reference when actually stored, else raw bytes).
    extraction = analyze_receipt_with_textract(
        data=data,
        s3_bucket=s3.get("bucket") if s3.get("stored") else None,
        s3_key=s3.get("key") if s3.get("stored") else None,
        file_name=file.filename or "receipt",
    )

    summary = extraction.get("summary") or {}

    receipt = Receipt(
        id=receipt_id,
        file_name=file.filename or "receipt",
        content_type=file.content_type,
        file_size_bytes=len(data),
        s3_bucket=s3.get("bucket"),
        s3_key=s3.get("key"),
        s3_region=s3.get("region"),
        employee_id=employeeId,
        extraction_status=ExtractionStatus.COMPLETED
        if not extraction.get("error")
        else ExtractionStatus.FAILED,
        extraction_source=extraction.get("source"),
        raw_textract=extraction.get("rawTextract"),
        normalized_extraction=summary,
        vendor_name=summary.get("vendorName"),
        transaction_date=_parse_date(summary.get("transactionDate")),
        total_amount=_to_decimal(summary.get("totalAmount")),
        currency=summary.get("currency"),
        error_message=extraction.get("error"),
    )

    for f in summary.get("fields", []) or []:
        receipt.fields.append(
            ReceiptField(
                field_type=f.get("fieldType"),
                field_label=f.get("fieldLabel"),
                field_value=f.get("fieldValue"),
                confidence=_to_decimal(f.get("confidence")),
            )
        )

    for li in extraction.get("lineItems", []) or []:
        receipt.line_items.append(
            ReceiptLineItem(
                line_number=li.get("lineNumber"),
                description=li.get("description"),
                quantity=_to_decimal(li.get("quantity")),
                unit_price=_to_decimal(li.get("unitPrice")),
                amount=_to_decimal(li.get("amount")),
                raw=li.get("raw"),
            )
        )

    db.add(receipt)
    db.commit()
    db.refresh(receipt)

    return _serialize_detail(receipt)


@router.get("", response_model=List[ReceiptSummarySchema])
def list_receipts(
    employeeId: Optional[str] = None,
    db: Session = Depends(get_db),
):
    stmt = select(Receipt).order_by(Receipt.created_at.desc())
    if employeeId:
        stmt = stmt.where(Receipt.employee_id == employeeId)
    rows = db.execute(stmt).scalars().all()
    return [_serialize_summary(r) for r in rows]


@router.get("/{receipt_id}", response_model=ReceiptDetailSchema)
def get_receipt(receipt_id: str, db: Session = Depends(get_db)):
    try:
        rid = uuid.UUID(receipt_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid receipt id.")

    receipt = db.get(Receipt, rid)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found.")
    return _serialize_detail(receipt)
