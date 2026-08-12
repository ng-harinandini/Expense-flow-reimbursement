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
from functools import partial
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from app.ai.duplicate_detection.hashing import sha256_hex
from app.core.config import settings
from app.core.deps import (
    CurrentUser,
    get_actor,
    get_audit_service,
    get_claim_repository,
    get_claim_service,
    get_employee_service,
    get_receipt_extractor,
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
from app.services.claim_service import ClaimService
from app.services.employee_service import EmployeeService
from app.services.receipt_extraction import ReceiptExtractor
from app.services.s3_service import (
    S3DownloadError,
    download_receipt_from_s3,
    upload_receipt_to_s3,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/expense-items", tags=["Expense Items"])


#: Content types the viewer is allowed to render inline. Anything else is forced to download:
#: serving an attacker-supplied ``text/html`` from this origin would be stored XSS with the
#: caller's session attached.
_INLINE_CONTENT_TYPES = frozenset(
    {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp"}
)


@router.get("/receipt", status_code=status.HTTP_200_OK)
def download_receipt(
    fileUrl: str = Query(..., description="The item's stored fileUrl, echoed back verbatim."),
    actor: Actor = Depends(get_actor),
    claims: ClaimRepository = Depends(get_claim_repository),
    service: ClaimService = Depends(get_claim_service),
):
    """Stream a receipt document through the API.

    The S3 bucket is private, so the browser cannot fetch ``fileUrl`` directly. This endpoint is
    the authorized path to those bytes: it resolves the URL to the expense item that owns it and
    reuses the claim's own access rule, so a caller only ever reads receipts on claims they can
    already see.

    ``fileUrl`` is **never** trusted as an S3 key. It is matched against a persisted
    ``expense_items.file_url``; a URL with no such row is a 404, which is what stops a caller from
    walking the bucket by editing the query string.
    """
    key = receipt_extraction.parse_object_key(fileUrl)
    if not key:
        raise HTTPException(status_code=400, detail="A valid fileUrl is required.")

    item = claims.find_item_by_file_url(fileUrl)
    # 404 rather than 403 for the same reason as get_claim_for_actor: the response must not
    # confirm that an object exists to someone who cannot read it.
    if item is None or item.claim is None:
        raise HTTPException(status_code=404, detail="Receipt not found.")

    # Raises NotFoundError (-> 404) when this actor cannot see the claim.
    service.get_claim_for_actor(str(item.claim_id), actor=actor)

    try:
        data, content_type = download_receipt_from_s3(key)
    except S3DownloadError as e:
        logger.error("receipt.download_failed", extra={"key": key, "error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The receipt could not be retrieved from storage.",
        )

    resolved_type = content_type or item.mime_type or "application/octet-stream"
    disposition = "inline" if resolved_type in _INLINE_CONTENT_TYPES else "attachment"
    file_name = (item.file_name or "receipt").replace('"', "")

    return Response(
        content=data,
        media_type=resolved_type,
        headers={
            "Content-Disposition": f'{disposition}; filename="{file_name}"',
            # The bytes are per-caller and authorization is re-checked on every request; a shared
            # cache holding them would serve one employee's receipt to the next.
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/upload", status_code=status.HTTP_200_OK, response_model=ReceiptExtractionSchema)
async def upload_receipt(
    file: UploadFile = File(...),
    categoryHint: Optional[str] = Form(None),
    current: CurrentUser = Depends(require_roles("employee")),
    employees: EmployeeService = Depends(get_employee_service),
    claims: ClaimRepository = Depends(get_claim_repository),
    audit: AuditService = Depends(get_audit_service),
    uow: UnitOfWork = Depends(get_unit_of_work),
    extractor: ReceiptExtractor = Depends(get_receipt_extractor),
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

    # Which engine runs is a deployment setting (AI_RECEIPT_EXTRACTION_PROVIDER); both return the
    # same shape, and both degrade to an empty result rather than raising, so a scan failure costs
    # the employee a pre-filled form and never the upload.
    #
    # Off the event loop: both engines block (boto3), and the Bedrock one also rasterizes PDFs, for
    # up to AI_RECEIPT_EXTRACTION_TIMEOUT_SECONDS. Held on the loop, that would stall every other
    # request this worker is serving, healthchecks included.
    try:
        extraction = await run_in_threadpool(
            partial(
                extractor.extract,
                data=data,
                mime_type=file.content_type,
                file_name=file.filename or "receipt",
                s3_bucket=stored.get("bucket") if stored.get("stored") else None,
                s3_key=stored.get("key") if stored.get("stored") else None,
            )
        )
    except Exception as exc:  # noqa: BLE001 - the scan is advisory; the stored upload is not
        # Both engines already degrade internally rather than raise, so reaching here means one
        # broke its own contract. The object is in S3 and still needs auditing below, and the
        # employee can fill the fields in by hand — none of which is worth losing to a 500.
        logger.exception(
            "receipt.extraction_raised",
            extra={"fileHash": file_hash, "mimeType": file.content_type, "error": str(exc)[:300]},
        )
        extraction = receipt_extraction.empty_extraction(
            file.filename or "receipt", f"Extraction engine failed: {exc}"
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
        # Only the Bedrock engine can suggest a category — Textract has no notion of one, and
        # leaves these null, so that path still just echoes the caller's hint as it always did.
        suggestedCategory=extraction.get("suggestedCategory")
        or (categoryHint or "").strip()
        or None,
        suggestedCategoryConfidence=extraction.get("categoryConfidence"),
        documentType=extraction.get("documentType"),
        categoryFields=extraction.get("categoryFields"),
        duplicateOfClaimNumber=duplicate_of,
        errorMessage=extraction.get("error"),
    )
