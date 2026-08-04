"""Knowledge document lifecycle — upload, list, get, reindex, archive.

Thin routes, exactly like every T003 route: authorize -> validate -> call the ingestion pipeline or
a repository -> ``uow.commit()`` -> serialize. This router (not ``KnowledgeService``) is the caller
of ``ingest_document()`` and the knowledge-core repositories directly — legitimate here because
``app/ai/api/`` is the AI platform's own transport layer (see the package docstring), not an
external caller reaching past the service facade.

Upload and reindex call :func:`ingest_document` with ``manage_transaction=False`` so the ingestion
write and this route's audit record land in one transaction, committed once via ``uow.commit()`` —
matching the T003 "one explicit commit point" discipline despite the pipeline's own default of
managing its transaction itself (see that function's docstring for why the flag exists).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.ai.core.config import ai_settings
from app.ai.core.enums import DocumentStatus, KnowledgeSourceType
from app.ai.core.errors import AIValidationError, DocumentTooLargeError, KnowledgeNotFoundError
from app.ai.ingestion.pipeline import DocumentMetadataInput, ingest_document
from app.ai.ingestion.sources.inline import InlineSource
from app.ai.ingestion.sources.upload import UploadSource
from app.ai.repositories.knowledge_repository import (
    KnowledgeChunkRepository,
    KnowledgeDocumentRepository,
)
from app.ai.services.composition import build_ingestion_providers
from app.core.deps import (
    CurrentUser,
    get_audit_service,
    get_current_user,
    get_db,
    get_unit_of_work,
    require_roles,
)
from app.core.unit_of_work import UnitOfWork
from app.domain.actor import Actor
from app.services.audit_service import AuditService

router = APIRouter(prefix="/ai/knowledge", tags=["AI Knowledge Platform"])

_TENANT_ID = "default"


def _serialize_document(document) -> dict:
    return {
        "id": str(document.id),
        "title": document.title,
        "version": document.version,
        "status": document.status.value
        if isinstance(document.status, DocumentStatus) else str(document.status),
        "sourceType": document.source_type,
        "category": document.category,
        "department": document.department,
        "country": document.country,
        "currency": document.currency,
        "policyVersion": document.policy_version,
        "owner": document.owner,
        "effectiveDate": document.effective_date.isoformat() if document.effective_date else None,
        "expiryDate": document.expiry_date.isoformat() if document.expiry_date else None,
        "sizeBytes": document.size_bytes,
        "language": document.language,
        "pageCount": document.page_count,
        "qualityScore": (
            float(document.quality_score) if document.quality_score is not None else None
        ),
        "piiKinds": document.pii_kinds or [],
        "checksumSha256": document.checksum_sha256,
        "supersedesId": str(document.supersedes_id) if document.supersedes_id else None,
        "createdAt": document.created_at.isoformat() if document.created_at else None,
        "indexedAt": document.indexed_at.isoformat() if document.indexed_at else None,
    }


@router.post("/documents", status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    sourceType: str = Form(KnowledgeSourceType.OTHER.value),
    department: Optional[str] = Form(None),
    country: Optional[str] = Form(None),
    currency: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    policyVersion: Optional[str] = Form(None),
    owner: Optional[str] = Form(None),
    effectiveDate: Optional[date] = Form(None),
    expiryDate: Optional[date] = Form(None),
    current: CurrentUser = Depends(require_roles("finance", "admin")),
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> dict:
    """Upload a document into the knowledge corpus. 413 over the configured byte cap, before any
    parsing is attempted — the pipeline's own internal size check (422) never fires for this
    path."""
    actor = Actor.from_current_user(current)
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(data) > ai_settings.MAX_DOCUMENT_BYTES:
        raise DocumentTooLargeError(size_bytes=len(data), max_bytes=ai_settings.MAX_DOCUMENT_BYTES)

    try:
        resolved_source_type = KnowledgeSourceType.coerce(sourceType)
    except ValueError as exc:
        raise AIValidationError(str(exc)) from exc

    source = UploadSource(
        content=data, file_name=file.filename or "document", mime_type=file.content_type,
        source_type=resolved_source_type, tenant_id=_TENANT_ID,
    )
    embedding_service, vector_store = build_ingestion_providers(db)
    result = ingest_document(
        source, session=db,
        metadata=DocumentMetadataInput(
            title=title, department=department, country=country, currency=currency,
            category=category, policy_version=policyVersion, owner=owner,
            effective_date=effectiveDate, expiry_date=expiryDate,
        ),
        embedding_service=embedding_service, vector_store=vector_store,
        actor_sub=actor.sub, manage_transaction=False,
    )
    audit.record(
        actor=actor, action="AI_DOCUMENT_UPLOADED", entity_type="KnowledgeDocument",
        entity_id=result.document_id or result.run_id,
        details=f"Uploaded '{file.filename}' ({result.status.value}).",
        after={
            "documentId": str(result.document_id) if result.document_id else None,
            "version": result.document_version, "status": result.status.value,
            "chunksCreated": result.chunks_created, "embeddingsCreated": result.embeddings_created,
        },
    )
    uow.commit()
    return {
        "runId": str(result.run_id),
        "documentId": str(result.document_id) if result.document_id else None,
        "documentVersion": result.document_version,
        "status": result.status.value,
        "chunksCreated": result.chunks_created,
        "chunksSkipped": result.chunks_skipped,
        "embeddingsCreated": result.embeddings_created,
        "durationMs": result.duration_ms,
    }


@router.get("/documents")
def list_documents(
    sourceType: Optional[str] = None,
    category: Optional[str] = None,
    country: Optional[str] = None,
    includeSuperseded: bool = False,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    repo = KnowledgeDocumentRepository(db)
    documents = repo.search(
        tenant_id=_TENANT_ID, source_type=sourceType, category=category, country=country,
        include_superseded=includeSuperseded, limit=limit, offset=offset,
    )
    return [_serialize_document(document) for document in documents]


@router.get("/documents/{document_id}")
def get_document(
    document_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    repo = KnowledgeDocumentRepository(db)
    document = repo.get_scoped(document_id, tenant_id=_TENANT_ID)
    if document is None:
        raise KnowledgeNotFoundError("KnowledgeDocument", document_id)
    return _serialize_document(document)


@router.post("/documents/{document_id}/reindex", status_code=status.HTTP_201_CREATED)
def reindex_document(
    document_id: uuid.UUID,
    current: CurrentUser = Depends(require_roles("finance", "admin")),
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> dict:
    """Re-embed a document under the currently configured model by reassembling its existing chunk
    text into a fresh version — raw source bytes are not retained for every source type, so this
    replays the indexed text rather than the original upload."""
    actor = Actor.from_current_user(current)
    document_repo = KnowledgeDocumentRepository(db)
    chunk_repo = KnowledgeChunkRepository(db)

    document = document_repo.get_scoped(document_id, tenant_id=_TENANT_ID)
    if document is None:
        raise KnowledgeNotFoundError("KnowledgeDocument", document_id)

    chunks = chunk_repo.list_for_document(document.id, tenant_id=_TENANT_ID)
    if not chunks:
        raise AIValidationError(f"Document '{document.title}' has no chunks to reindex.")

    text = "\n\n".join(chunk.content for chunk in chunks)
    source = InlineSource(
        text=text, file_name=document.file_name or f"{document.title}.txt",
        source_type=KnowledgeSourceType.coerce(document.source_type), tenant_id=_TENANT_ID,
    )
    embedding_service, vector_store = build_ingestion_providers(db)
    result = ingest_document(
        source, session=db,
        metadata=DocumentMetadataInput(
            title=document.title, department=document.department, country=document.country,
            currency=document.currency, category=document.category,
            policy_version=document.policy_version, owner=document.owner,
            effective_date=document.effective_date, expiry_date=document.expiry_date,
        ),
        embedding_service=embedding_service, vector_store=vector_store,
        actor_sub=actor.sub, manage_transaction=False,
    )
    audit.record(
        actor=actor, action="AI_DOCUMENT_REINDEXED", entity_type="KnowledgeDocument",
        entity_id=document.id,
        details=(
            f"Reindexed '{document.title}' -> v{result.document_version} "
            f"({result.status.value})."
        ),
        before={"version": document.version},
        after={"version": result.document_version, "status": result.status.value},
    )
    uow.commit()
    return {
        "runId": str(result.run_id),
        "documentId": str(result.document_id) if result.document_id else None,
        "documentVersion": result.document_version,
        "status": result.status.value,
        "chunksCreated": result.chunks_created,
        "embeddingsCreated": result.embeddings_created,
    }


@router.delete("/documents/{document_id}")
def archive_document(
    document_id: uuid.UUID,
    current: CurrentUser = Depends(require_roles("finance", "admin")),
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> dict:
    """Archive a document. Never a hard delete: a claim decided under this document's content must
    stay explainable, so the row (and its chunks/embeddings) are kept, only marked unretrievable."""
    actor = Actor.from_current_user(current)
    repo = KnowledgeDocumentRepository(db)
    document = repo.get_scoped(document_id, tenant_id=_TENANT_ID)
    if document is None:
        raise KnowledgeNotFoundError("KnowledgeDocument", document_id)

    previous_status = (
        document.status.value if isinstance(document.status, DocumentStatus)
        else str(document.status)
    )
    repo.archive(document)
    audit.record(
        actor=actor, action="AI_DOCUMENT_ARCHIVED", entity_type="KnowledgeDocument",
        entity_id=document.id, details=f"Archived '{document.title}' v{document.version}.",
        before={"status": previous_status}, after={"status": DocumentStatus.ARCHIVED.value},
    )
    uow.commit()
    return {"id": str(document.id), "status": DocumentStatus.ARCHIVED.value}
