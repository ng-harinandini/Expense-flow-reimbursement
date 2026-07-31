"""Receipt upload and extraction for expense items.

    POST /expense-items/upload   multipart file -> 200 extraction (no row is created)

**Nothing is persisted.** The endpoint stores the document in S3, runs extraction, and returns the
result for the client to hold. The row is written later, by ``POST /claims``, when the client
echoes the ``file*``/``ocr*`` fields back inside an item. The alternative — a staged item with a
null ``claim_id`` — would mean giving up the ``ON DELETE CASCADE`` guarantee and running a reaper
for abandoned uploads, to save one round trip.

The ``200`` (not ``201``) is the contract: no resource was created.
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.ai.duplicate_detection.hashing import sha256_hex
from app.core.config import settings
from app.core.deps import (
    CurrentUser,
    get_audit_service,
    get_claim_repository,
    get_employee_service,
    get_unit_of_work,
    require_roles,
)
from app.core.logging import get_logger
from app.core.unit_of_work import UnitOfWork
from app.domain.actor import Actor
from app.models.enums import AuditAction, AuditEntity
from app.repositories.claim_repository import ClaimRepository
from app.schemas.schemas import ReceiptExtractionSchema
from app.services import receipt_extraction
from app.services.audit_service import AuditService
from app.services.employee_service import EmployeeService
from app.services.s3_service import upload_receipt_to_s3
from app.services.textract_service import analyze_receipt_with_textract

logger = get_logger(__name__)

router = APIRouter(prefix="/expense-items", tags=["Expense Items"])


@router.post("/upload", status_code=status.HTTP_200_OK, response_model=ReceiptExtractionSchema)
async def upload_receipt(
    file: UploadFile = File(...),
    categoryHint: Optional[str] = Form(None),
    current: CurrentUser = Depends(require_roles("employee")),
    employees: EmployeeService = Depends(get_employee_service),
    claims: ClaimRepository = Depends(get_claim_repository),
    audit: AuditService = Depends(get_audit_service),
    uow: UnitOfWork = Depends(get_unit_of_work),
):
    """Store a receipt, extract it, and return the data — without creating a claim or item."""
    actor = Actor.from_current_user(current)
    employee = employees.resolve_actor_employee(actor)

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(data) > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"File exceeds the {settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MiB limit."
            ),
        )

    file_hash = sha256_hex(data)

    # Advisory only — never 409. Re-uploading after a rejection is legitimate, and weighing a
    # repeat is the fraud engine's job at submission time, not this endpoint's.
    duplicate_of = None
    existing = claims.find_items_by_file_hash(file_hash, employee_id=employee.id)
    if existing:
        duplicate_of = existing[0].claim.claim_number
        logger.info(
            "receipt.duplicate_hash_on_upload",
            extra={"employeeId": employee.employee_code, "claimNumber": duplicate_of},
        )

    # The employee segment of the key is what proves ownership when the client echoes the URL
    # back at submit time — there is no s3_key column to compare against.
    key = receipt_extraction.build_object_key(
        str(employee.id), str(uuid.uuid4()), file.filename or "receipt"
    )
    stored = upload_receipt_to_s3(data, key, file.content_type)

    extraction = analyze_receipt_with_textract(
        data=data,
        s3_bucket=stored.get("bucket") if stored.get("stored") else None,
        s3_key=stored.get("key") if stored.get("stored") else None,
        file_name=file.filename or "receipt",
    )
    suggestions = receipt_extraction.summarize_extraction(extraction)

    # An S3 object was created; an unaudited write to object storage is a compliance gap even
    # though no database row exists yet.
    audit.record(
        actor=actor,
        action=AuditAction.RECEIPT_UPLOAD,
        entity_type=AuditEntity.EXPENSE_ITEM,
        entity_id=file_hash,
        details=f"Uploaded receipt '{file.filename}' ({len(data)} bytes) for extraction.",
        after={"fileHash": file_hash, "stored": bool(stored.get("stored"))},
    )
    uow.commit()

    return ReceiptExtractionSchema(
        fileUrl=receipt_extraction.build_file_url(key) if stored.get("stored") else None,
        fileName=file.filename or "receipt",
        mimeType=file.content_type,
        fileSizeBytes=len(data),
        fileHash=file_hash,
        ocrSource=extraction.get("source") or "fallback",
        ocrConfidence=receipt_extraction.normalize_confidence(extraction),
        extraction=extraction.get("summary") or None,
        suggestedVendor=suggestions["vendor"],
        suggestedDate=suggestions["transactionDate"],
        suggestedAmount=suggestions["totalAmount"],
        suggestedCurrency=suggestions["currency"],
        suggestedCategory=(categoryHint or "").strip() or None,
        duplicateOfClaimNumber=duplicate_of,
        errorMessage=extraction.get("error"),
    )
