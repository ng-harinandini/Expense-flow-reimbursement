"""Policy rule extraction API — Path A of the dual-path ingestion lifecycle.

Four endpoints:

* ``POST   /ai/knowledge/documents/{document_id}/extract-rules``
  Sends the document's chunks to Gemini section-by-section and stores every extracted
  rule as a PENDING ``CandidatePolicyRule``. Finance/Admin only.

* ``GET    /ai/knowledge/documents/{document_id}/candidate-rules``
  Lists candidates for a document, optionally filtered by status.

* ``POST   /ai/knowledge/candidate-rules/{candidate_id}/approve``
  Approves one candidate: publishes it to ``policy_rules`` with full provenance and
  marks it APPROVED. Finance/Admin only.

* ``DELETE /ai/knowledge/candidate-rules/{candidate_id}``
  Dismisses a PENDING candidate without publishing it. Finance/Admin only.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.ai.extraction.policy_rule_extractor import PolicyRuleExtractor
from app.ai.repositories.knowledge_repository import (
    KnowledgeChunkRepository,
    KnowledgeDocumentRepository,
)
from app.core.deps import (
    CurrentUser,
    get_audit_service,
    get_current_user,
    get_db,
    get_policy_rule_service,
    get_unit_of_work,
    require_roles,
)
from app.core.unit_of_work import UnitOfWork
from app.domain.actor import Actor
from app.models.candidate_rule import CandidatePolicyRule
from app.repositories.candidate_rule_repository import CandidateRuleRepository
from app.services.audit_service import AuditService
from app.services.mappers import policy_rule_to_dict
from app.services.policy_rule_service import PolicyRuleService

router = APIRouter(prefix="/ai/knowledge", tags=["AI Rule Extraction"])

_TENANT_ID = "default"


# ── serializers ──────────────────────────────────────────────────────────────


def _serialize_candidate(c: CandidatePolicyRule) -> dict:
    return {
        "id": str(c.id),
        "documentId": str(c.document_id) if c.document_id else None,
        "sourcePageNumber": c.source_page_number,
        "sourceChunkId": str(c.source_chunk_id) if c.source_chunk_id else None,
        "extractedBy": c.extracted_by,
        "extractionRunId": str(c.extraction_run_id),
        "status": c.status,
        "name": c.name,
        "category": c.category,
        "code": c.code,
        "description": c.description,
        "country": c.country,
        "currency": c.currency,
        "gradeTier": c.grade_tier,
        "expenseLimit": float(c.expense_limit) if c.expense_limit is not None else None,
        "limitExpression": c.limit_expression,
        "autoApproveLimitUSD": float(c.auto_approve_limit) if c.auto_approve_limit is not None else None,
        "receiptRequiredAboveUSD": float(c.receipt_required_above) if c.receipt_required_above is not None else None,
        "requiresPreApproval": c.requires_pre_approval,
        "priority": c.priority,
        "specialRules": c.special_rules or [],
        "conditions": c.conditions,
        "actions": c.actions,
        "reviewedBySub": c.reviewed_by_sub,
        "reviewedAt": c.reviewed_at.isoformat() if c.reviewed_at else None,
        "publishedRuleId": str(c.published_rule_id) if c.published_rule_id else None,
        "createdAt": c.created_at.isoformat() if c.created_at else None,
    }


# ── endpoints ────────────────────────────────────────────────────────────────


@router.post(
    "/documents/{document_id}/extract-rules",
    status_code=status.HTTP_201_CREATED,
)
def extract_rules(
    document_id: uuid.UUID,
    current: CurrentUser = Depends(require_roles("finance", "admin")),
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> dict:
    """Send this document's chunks to Gemini and store the extracted rule candidates.

    The document must already be ``INDEXED`` (i.e. Path B must have completed). If Gemini
    is not configured (no ``GEMINI_API_KEY``), returns HTTP 503.
    """
    actor = Actor.from_current_user(current)

    doc_repo = KnowledgeDocumentRepository(db)
    doc = doc_repo.get_scoped(document_id, tenant_id=_TENANT_ID)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    if doc.status.value != "INDEXED":
        raise HTTPException(
            status_code=409,
            detail=f"Document is '{doc.status.value}', must be 'INDEXED' before extracting rules.",
        )

    chunk_repo = KnowledgeChunkRepository(db)
    chunks = chunk_repo.list_for_document(doc.id, tenant_id=_TENANT_ID)
    if not chunks:
        raise HTTPException(
            status_code=409,
            detail="Document has no indexed chunks to extract rules from.",
        )

    run_id = uuid.uuid4()
    extractor = PolicyRuleExtractor()
    try:
        candidates = extractor.extract(chunks, document_id=doc.id, run_id=run_id)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    candidate_repo = CandidateRuleRepository(db)
    saved = candidate_repo.add_all(candidates)

    audit.record(
        actor=actor,
        action="AI_RULES_EXTRACTED",
        entity_type="KnowledgeDocument",
        entity_id=doc.id,
        details=(
            f"Extracted {len(saved)} candidate rule(s) from '{doc.title}' "
            f"(run {run_id})."
        ),
        after={
            "runId": str(run_id),
            "candidatesFound": len(saved),
            "documentId": str(doc.id),
        },
    )
    uow.commit()

    return {
        "runId": str(run_id),
        "documentId": str(doc.id),
        "candidatesFound": len(saved),
        "candidates": [_serialize_candidate(c) for c in saved],
    }


@router.get("/documents/{document_id}/candidate-rules")
def list_candidates(
    document_id: uuid.UUID,
    status_filter: Optional[str] = Query(None, alias="status"),
    current: CurrentUser = Depends(require_roles("finance", "admin")),
    db: Session = Depends(get_db),
) -> list[dict]:
    """List candidate rules for a document, optionally filtered by status."""
    doc_repo = KnowledgeDocumentRepository(db)
    doc = doc_repo.get_scoped(document_id, tenant_id=_TENANT_ID)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    valid_statuses = {"PENDING", "APPROVED", "DISMISSED"}
    if status_filter and status_filter.upper() not in valid_statuses:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of: {', '.join(sorted(valid_statuses))}",
        )

    candidate_repo = CandidateRuleRepository(db)
    candidates = candidate_repo.list_for_document(
        doc.id,
        status=status_filter.upper() if status_filter else None,
    )
    return [_serialize_candidate(c) for c in candidates]


@router.post(
    "/candidate-rules/{candidate_id}/approve",
    status_code=status.HTTP_201_CREATED,
)
def approve_candidate(
    candidate_id: uuid.UUID,
    current: CurrentUser = Depends(require_roles("finance", "admin")),
    db: Session = Depends(get_db),
    policy_service: PolicyRuleService = Depends(get_policy_rule_service),
    audit: AuditService = Depends(get_audit_service),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> dict:
    """Approve one PENDING candidate and publish it as a versioned policy rule.

    The published rule carries full provenance (source_document_id, source_page_number,
    source_chunk_id, extracted_by) so any rule cap can be traced to its origin.
    """
    actor = Actor.from_current_user(current)

    candidate_repo = CandidateRuleRepository(db)
    candidate = candidate_repo.get(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found.")
    if not candidate.is_pending:
        raise HTTPException(
            status_code=409,
            detail=f"Candidate is already '{candidate.status}'.",
        )

    rule = policy_service.publish_single_rule(candidate, actor=actor)
    candidate_repo.approve(candidate, published_rule=rule, actor_sub=actor.sub)

    audit.record(
        actor=actor,
        action="AI_CANDIDATE_APPROVED",
        entity_type="CandidatePolicyRule",
        entity_id=candidate.id,
        details=(
            f"Approved candidate '{candidate.name}' → PolicyRule {rule.code} v{rule.version}."
        ),
        after={
            "publishedRuleId": str(rule.id),
            "code": rule.code,
            "version": rule.version,
        },
    )
    uow.commit()

    return {
        "candidateId": str(candidate.id),
        "status": "APPROVED",
        "publishedRule": policy_rule_to_dict(rule),
    }


@router.delete("/candidate-rules/{candidate_id}", status_code=status.HTTP_200_OK)
def dismiss_candidate(
    candidate_id: uuid.UUID,
    current: CurrentUser = Depends(require_roles("finance", "admin")),
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> dict:
    """Dismiss a PENDING candidate without publishing it."""
    actor = Actor.from_current_user(current)

    candidate_repo = CandidateRuleRepository(db)
    candidate = candidate_repo.get(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found.")
    if not candidate.is_pending:
        raise HTTPException(
            status_code=409,
            detail=f"Candidate is already '{candidate.status}'.",
        )

    candidate_repo.dismiss(candidate, actor_sub=actor.sub)
    audit.record(
        actor=actor,
        action="AI_CANDIDATE_DISMISSED",
        entity_type="CandidatePolicyRule",
        entity_id=candidate.id,
        details=f"Dismissed candidate '{candidate.name}' ({candidate.category}).",
    )
    uow.commit()
    return {"candidateId": str(candidate.id), "status": "DISMISSED"}
