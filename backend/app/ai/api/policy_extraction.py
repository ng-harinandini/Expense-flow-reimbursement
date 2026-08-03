"""Runs rule extraction over an ingested document.

Legitimately part of ``app/ai/api`` — this is the platform's own transport layer, calling
:class:`PolicyRuleExtractionService` and the knowledge/proposal repositories directly, the same way
``app/ai/api/knowledge.py`` calls the ingestion pipeline. What it writes is a ``DRAFT`` proposal:
never ``policy_rules``. Reviewing and approving that proposal is a separate, non-AI route
(``app/api/policy_rule_proposals.py``) that calls ``PolicyRuleService`` itself — the module boundary
``tests/test_ai_architecture.py`` enforces by banning ``app/ai/**`` from importing
``ClaimService``/``ClaimRepository``/``policy_engine``/``fraud_engine``.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.core.enums import DocumentStatus
from app.ai.core.errors import KnowledgeNotFoundError
from app.ai.repositories.knowledge_repository import KnowledgeDocumentRepository
from app.ai.services.composition import build_policy_rule_extraction_service
from app.core.deps import CurrentUser, get_audit_service, get_db, get_unit_of_work, require_roles
from app.core.unit_of_work import UnitOfWork
from app.domain.actor import Actor
from app.models.category import ExpenseCategory
from app.models.enums import AuditAction, AuditEntity
from app.services.audit_service import AuditService

router = APIRouter(prefix="/ai/policy-extraction", tags=["AI Knowledge Platform"])

_TENANT_ID = "default"


def _active_category_names(db: Session) -> list[str]:
    """The closed set the extractor must map onto — the same set ``policy_rules.category`` and
    ``expense_categories.name`` are asserted to stay in lockstep, in display order."""
    rows = db.execute(
        select(ExpenseCategory.name)
        .where(ExpenseCategory.is_active.is_(True))
        .order_by(ExpenseCategory.display_order, ExpenseCategory.name)
    ).scalars()
    return list(rows)


@router.post("/documents/{document_id}/extract", response_model=dict)
def extract_policy_rules(
    document_id: str,
    current: CurrentUser = Depends(require_roles("finance", "admin")),
    db: Session = Depends(get_db),
    audit: AuditService = Depends(get_audit_service),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> dict:
    """Extract structured rules from an already-ingested document, as a ``DRAFT`` proposal.

    A prior open (``DRAFT``) proposal for this document is marked ``SUPERSEDED`` first, so
    re-running extraction never leaves two drafts a reviewer could approve in either order.
    """
    actor = Actor.from_current_user(current)
    documents = KnowledgeDocumentRepository(db)

    try:
        document_uuid = uuid.UUID(document_id)
    except ValueError as exc:
        raise KnowledgeNotFoundError("KnowledgeDocument", document_id) from exc

    document = documents.get_scoped(document_uuid, tenant_id=_TENANT_ID)
    if document is None or document.status != DocumentStatus.INDEXED:
        raise KnowledgeNotFoundError("KnowledgeDocument", document_id)

    service = build_policy_rule_extraction_service(db)
    proposal = service.extract(
        document,
        tenant_id=_TENANT_ID,
        actor_sub=actor.sub,
        known_categories=_active_category_names(db),
    )

    audit.record(
        actor=actor,
        action=AuditAction.POLICY_RULE_EXTRACTED,
        entity_type=AuditEntity.POLICY_RULE_PROPOSAL,
        entity_id=proposal.id,
        details=(
            f"Extracted {proposal.rules_extracted} rule(s) "
            f"({proposal.rules_representable} publishable) from document {document.title!r}."
        ),
        after={
            "proposalId": str(proposal.id),
            "documentId": str(document.id),
            "rulesExtracted": proposal.rules_extracted,
            "rulesRepresentable": proposal.rules_representable,
        },
    )
    uow.commit()

    return {
        "id": str(proposal.id),
        "status": proposal.status.value,
        "documentId": str(document.id),
        "rulesExtracted": proposal.rules_extracted,
        "rulesRepresentable": proposal.rules_representable,
        "llmProvider": proposal.llm_provider,
        "llmModel": proposal.llm_model,
    }


__all__ = ["router"]
