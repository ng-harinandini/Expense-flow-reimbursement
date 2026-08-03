"""Review and approval of extracted policy-rule proposals.

Deliberately **not** under ``app/ai/api``. Extraction (``app/ai/api/policy_extraction.py``) only
ever writes a ``DRAFT`` proposal; this router is where a human decision crosses into
``policy_rules``, the table the live policy engine reads. That split mirrors ADR-003 §5's choice
of "deterministic decides, LLM explains" (option C2) and is what
``tests/test_ai_architecture.py::test_ai_package_never_imports_claim_write_paths`` protects on the
claim side — this router is the analogous boundary for policy configuration: it is the only code
that turns a model's reading of a document into something the engine will actually be checked
against.

**Approval always merges over the current active ruleset**, never just the proposal's categories.
``PolicyRuleService.replace_ruleset`` retires every active rule whose code is absent from what is
submitted (see ``_retire_absent``); submitting only the categories one document happened to mention
would silently retire the rest. See ``app.ai.policy_extraction.normalization.build_ruleset_payload``
for where that merge happens.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.ai.core.enums import ProposalStatus
from app.ai.models.policy_proposal import PolicyRuleProposal, PolicyRuleProposalItem
from app.ai.policy_extraction.normalization import (
    build_ruleset_payload,
    normalized_rule_from_item,
)
from app.ai.repositories.policy_proposal_repository import (
    PolicyDocumentPageRepository,
    PolicyDocumentSectionRepository,
    PolicyRuleProposalRepository,
)
from app.core.deps import (
    CurrentUser,
    get_audit_service,
    get_db,
    get_policy_rule_service,
    get_unit_of_work,
    require_roles,
)
from app.core.unit_of_work import UnitOfWork
from app.domain.actor import Actor
from app.domain.errors import ConflictError, NotFoundError, ValidationError
from app.models.enums import AuditAction, AuditEntity
from app.services.audit_service import AuditService
from app.services.policy_rule_service import PolicyRuleService

router = APIRouter(prefix="/policy-rule-proposals", tags=["Policy Rule Proposals"])

_TENANT_ID = "default"
_VIEWER_ROLES = ("finance", "admin", "auditor")
_APPROVER_ROLES = ("finance", "admin")


class ApproveProposalRequestSchema(BaseModel):
    """Body for ``POST /policy-rule-proposals/{id}/approve``.

    ``effectiveDate`` is required and never defaulted from the document: policy templates
    routinely ship the literal placeholder ``"[insert date]"``, and guessing a date for live
    configuration is worse than forcing the approver to supply one.
    """

    model_config = ConfigDict(
        extra="ignore", str_strip_whitespace=True, populate_by_name=True
    )

    effective_date: date = Field(alias="effectiveDate")
    notes: Optional[str] = None


class RejectProposalRequestSchema(BaseModel):
    """Body for ``POST /policy-rule-proposals/{id}/reject``. ``reason`` is required by a database
    check constraint — a rejection with no reason is not actionable by whoever submitted it."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    reason: str = Field(min_length=1, max_length=2000)


def _proposal_repo(db=Depends(get_db)) -> PolicyRuleProposalRepository:
    return PolicyRuleProposalRepository(db)


def _page_repo(db=Depends(get_db)) -> PolicyDocumentPageRepository:
    return PolicyDocumentPageRepository(db)


def _section_repo(db=Depends(get_db)) -> PolicyDocumentSectionRepository:
    return PolicyDocumentSectionRepository(db)


def _get_or_404(repo: PolicyRuleProposalRepository, proposal_id: str) -> PolicyRuleProposal:
    try:
        key = uuid.UUID(proposal_id)
    except ValueError as exc:
        raise NotFoundError("PolicyRuleProposal", proposal_id) from exc
    proposal = repo.get(key, tenant_id=_TENANT_ID)
    if proposal is None:
        raise NotFoundError("PolicyRuleProposal", proposal_id)
    return proposal


def _item_to_dict(item: PolicyRuleProposalItem) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "lineNumber": item.line_number,
        "category": item.category,
        "subCategory": item.sub_category,
        "gradeTier": item.grade_tier,
        "currency": item.currency,
        "maxAmountUSD": float(item.max_amount) if item.max_amount is not None else None,
        "autoApproveLimitUSD": (
            float(item.auto_approve_limit) if item.auto_approve_limit is not None else None
        ),
        "receiptRequiredAboveUSD": (
            float(item.receipt_required_above)
            if item.receipt_required_above is not None
            else None
        ),
        "requiresPreApproval": item.requires_pre_approval,
        "specialRules": item.special_rules or [],
        "basis": item.basis,
        "limitExpression": item.limit_expression,
        "alwaysManual": item.always_manual,
        "pageNumbers": item.page_numbers or [],
        "sourceQuote": item.source_quote,
        "confidence": float(item.confidence) if item.confidence is not None else None,
        "representable": item.representable,
        "unrepresentableReason": item.unrepresentable_reason,
        "reviewerEdited": item.reviewer_edited,
        "reviewerNotes": item.reviewer_notes,
        # Traceability and findings. `ruleId` is the stable slot identifier two competing readings
        # of one rule share, so a reviewer resolving a conflict can see which rows it names.
        "ruleId": item.rule_id,
        "sectionId": str(item.section_id) if item.section_id else None,
        "sourceChunkIds": item.source_chunk_ids or [],
        "validationWarnings": item.validation_warnings or [],
    }


def _proposal_to_dict(proposal: PolicyRuleProposal, *, include_items: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(proposal.id),
        "status": proposal.status.value,
        "knowledgeDocumentId": str(proposal.knowledge_document_id),
        "documentVersion": proposal.document_version,
        "documentTitle": proposal.document_title,
        "llmProvider": proposal.llm_provider,
        "llmModel": proposal.llm_model,
        "promptCode": proposal.prompt_code,
        "promptVersion": proposal.prompt_version,
        "rulesExtracted": proposal.rules_extracted,
        "rulesRepresentable": proposal.rules_representable,
        "documentEffectiveDate": (
            proposal.document_effective_date.isoformat()
            if proposal.document_effective_date
            else None
        ),
        "reviewNotes": proposal.review_notes,
        # `sectionsFailed > 0` means the proposal is incomplete: some part of the document was never
        # read, and the rules in it are absent. Surfaced on the list view too, so an incomplete
        # proposal is visible before a reviewer opens it.
        "sectionsTotal": proposal.sections_total,
        "sectionsFailed": proposal.sections_failed,
        "conflicts": proposal.conflicts or [],
        "promptHash": proposal.prompt_hash,
        "schemaVersion": proposal.schema_version,
        "createdBySub": proposal.created_by_sub,
        "approvedBySub": proposal.approved_by_sub,
        "approvedAt": proposal.approved_at.isoformat() if proposal.approved_at else None,
        "publishedEffectiveDate": (
            proposal.published_effective_date.isoformat()
            if proposal.published_effective_date
            else None
        ),
        "createdAt": proposal.created_at.isoformat() if proposal.created_at else None,
    }
    if include_items:
        payload["items"] = [_item_to_dict(item) for item in proposal.items]
    return payload


@router.get("", response_model=List[dict])
def list_proposals(
    status_filter: Optional[str] = None,
    document_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current: CurrentUser = Depends(require_roles(*_VIEWER_ROLES)),
    repo: PolicyRuleProposalRepository = Depends(_proposal_repo),
) -> list[dict[str, Any]]:
    """Recent proposals, newest first."""
    status_value: Optional[ProposalStatus] = None
    if status_filter:
        try:
            status_value = ProposalStatus.coerce(status_filter)
        except ValueError as exc:
            raise ValidationError(f"Unknown status '{status_filter}'.") from exc

    document_uuid: Optional[uuid.UUID] = None
    if document_id:
        try:
            document_uuid = uuid.UUID(document_id)
        except ValueError as exc:
            raise ValidationError("'documentId' must be a UUID.") from exc

    proposals = repo.list_recent(
        tenant_id=_TENANT_ID, status=status_value, document_id=document_uuid,
        limit=min(max(limit, 1), 200), offset=max(offset, 0),
    )
    return [_proposal_to_dict(p, include_items=False) for p in proposals]


@router.get("/{proposal_id}", response_model=dict)
def get_proposal(
    proposal_id: str,
    current: CurrentUser = Depends(require_roles(*_VIEWER_ROLES)),
    repo: PolicyRuleProposalRepository = Depends(_proposal_repo),
) -> dict[str, Any]:
    """One proposal with every extracted item and its evidence."""
    proposal = _get_or_404(repo, proposal_id)
    return _proposal_to_dict(proposal, include_items=True)


@router.get("/{proposal_id}/pages", response_model=List[dict])
def get_proposal_pages(
    proposal_id: str,
    current: CurrentUser = Depends(require_roles(*_VIEWER_ROLES)),
    repo: PolicyRuleProposalRepository = Depends(_proposal_repo),
    pages: PolicyDocumentPageRepository = Depends(_page_repo),
) -> list[dict[str, Any]]:
    """Per-page extraction metadata for this proposal's source document."""
    proposal = _get_or_404(repo, proposal_id)
    return [
        {
            "pageNumber": page.page_number,
            "sectionHeading": page.section_heading,
            "charCount": page.char_count,
            "tokenCount": page.token_count,
            "chunkCount": page.chunk_count,
            "tableDetected": page.table_detected,
            "ocrUsed": page.ocr_used,
            "rulesExtractedCount": page.rules_extracted_count,
        }
        for page in pages.list_for_document(
            proposal.knowledge_document_id, tenant_id=_TENANT_ID
        )
    ]


@router.get("/{proposal_id}/sections", response_model=List[dict])
def get_proposal_sections(
    proposal_id: str,
    current: CurrentUser = Depends(require_roles(*_VIEWER_ROLES)),
    repo: PolicyRuleProposalRepository = Depends(_proposal_repo),
    sections: PolicyDocumentSectionRepository = Depends(_section_repo),
) -> list[dict[str, Any]]:
    """How the document was divided, and what each section's extraction call did.

    The view that answers "was this document fully read?" — a ``FAILED`` row names a part of the
    document whose rules are missing from the proposal entirely, which no amount of reading the
    extracted rules would reveal. Also carries the per-section cost and model provenance.

    ``text`` is deliberately omitted: a section's full text can run to thousands of characters and
    a document has many of them. The section's page range and chunk ids locate it precisely enough
    for a reviewer to open the source.
    """
    proposal = _get_or_404(repo, proposal_id)
    return [
        {
            "id": str(section.id),
            "sectionIndex": section.section_index,
            "title": section.title,
            "startPage": section.start_page,
            "endPage": section.end_page,
            "charCount": section.char_count,
            "tokenCount": section.token_count,
            "chunkCount": section.chunk_count,
            "chunkIds": section.chunk_ids or [],
            "tableDetected": section.table_detected,
            "detectionMethod": section.detection_method,
            "detectionConfidence": (
                float(section.detection_confidence)
                if section.detection_confidence is not None
                else None
            ),
            "status": section.status,
            "errorMessage": section.error_message,
            "retryCount": section.retry_count,
            "rulesExtractedCount": section.rules_extracted_count,
            "llmProvider": section.llm_provider,
            "llmModel": section.llm_model,
            "promptVersion": section.prompt_version,
            "promptHash": section.prompt_hash,
            "schemaVersion": section.schema_version,
            "reasoningEffort": section.reasoning_effort,
            "inputTokens": section.input_tokens,
            "outputTokens": section.output_tokens,
            "latencyMs": section.latency_ms,
            "costUsd": float(section.cost_usd) if section.cost_usd is not None else None,
        }
        for section in sections.list_for_document(
            proposal.knowledge_document_id, tenant_id=_TENANT_ID
        )
    ]


@router.get("/{proposal_id}/diff", response_model=dict)
def diff_proposal(
    proposal_id: str,
    current: CurrentUser = Depends(require_roles(*_VIEWER_ROLES)),
    repo: PolicyRuleProposalRepository = Depends(_proposal_repo),
    policy_rules: PolicyRuleService = Depends(get_policy_rule_service),
) -> dict[str, Any]:
    """What approving this proposal would change, category by category.

    Computed from the same merge (:func:`build_ruleset_payload`) approval itself would submit, so
    the diff a reviewer reads is exactly the change that would happen — not an approximation of it.
    """
    proposal = _get_or_404(repo, proposal_id)
    current_active = policy_rules.list_active_as_dicts()
    representable = [
        normalized_rule_from_item(item) for item in proposal.items if item.representable
    ]
    proposed = build_ruleset_payload(representable, current_active=current_active)

    current_by_category = {r["category"]: r for r in current_active}
    proposed_by_category = {r["category"]: r for r in proposed}

    changes: list[dict[str, Any]] = []
    for category, new_rule in proposed_by_category.items():
        old_rule = current_by_category.get(category)
        if old_rule is None:
            changes.append({"category": category, "change": "new", "proposed": new_rule})
            continue
        fields = ("maxAmountUSD", "autoApproveLimitUSD", "receiptRequiredAboveUSD", "gradeTier")
        if any(old_rule.get(f) != new_rule.get(f) for f in fields):
            changes.append(
                {
                    "category": category, "change": "changed",
                    "current": {f: old_rule.get(f) for f in fields},
                    "proposed": {f: new_rule.get(f) for f in fields},
                }
            )
        else:
            changes.append({"category": category, "change": "unchanged"})

    unrepresentable = [
        {"category": item.category, "reason": item.unrepresentable_reason}
        for item in proposal.items
        if not item.representable
    ]
    # Always empty by construction (build_ruleset_payload carries every untouched category
    # through), but stated explicitly so a reviewer never has to take that on faith.
    retired = [c for c in current_by_category if c not in proposed_by_category]

    return {
        "proposalId": str(proposal.id),
        "changes": changes,
        "unrepresentable": unrepresentable,
        "wouldRetire": retired,
    }


@router.post("/{proposal_id}/approve", response_model=dict)
def approve_proposal(
    proposal_id: str,
    payload: ApproveProposalRequestSchema,
    current: CurrentUser = Depends(require_roles(*_APPROVER_ROLES)),
    repo: PolicyRuleProposalRepository = Depends(_proposal_repo),
    policy_rules: PolicyRuleService = Depends(get_policy_rule_service),
    audit: AuditService = Depends(get_audit_service),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> dict[str, Any]:
    """Publish this proposal's representable rules and mark it ``APPROVED``.

    Status is checked *before* anything is published: a proposal already approved or rejected must
    never reach ``replace_ruleset`` a second time, which would publish a redundant version and
    audit an approval that did not really happen.
    """
    actor = Actor.from_current_user(current)
    proposal = _get_or_404(repo, proposal_id)
    if proposal.status is not ProposalStatus.DRAFT:
        raise ConflictError(
            f"Proposal is {proposal.status.value} and cannot be approved.",
            details={"proposalId": proposal_id, "status": proposal.status.value},
        )

    representable = [
        normalized_rule_from_item(item) for item in proposal.items if item.representable
    ]
    if not representable:
        raise ValidationError(
            "This proposal has no publishable rules — every extracted item is unrepresentable."
        )

    current_active = policy_rules.list_active_as_dicts()
    ruleset_payload = build_ruleset_payload(representable, current_active=current_active)
    published = policy_rules.replace_ruleset(
        ruleset_payload, actor=actor, effective_date=payload.effective_date,
    )

    repo.approve(
        proposal, actor_sub=actor.sub, effective_date=payload.effective_date, notes=payload.notes,
    )
    audit.record(
        actor=actor,
        action=AuditAction.POLICY_RULE_PROPOSAL_REVIEWED,
        entity_type=AuditEntity.POLICY_RULE_PROPOSAL,
        entity_id=proposal.id,
        details=(
            f"Approved proposal {proposal.id}: published {len(published)} rule(s) effective "
            f"{payload.effective_date.isoformat()}."
        ),
        after={
            "proposalId": str(proposal.id),
            "effectiveDate": payload.effective_date.isoformat(),
            "publishedCategories": [r.category for r in published],
        },
    )
    uow.commit()

    return {
        "id": str(proposal.id),
        "status": proposal.status.value,
        "publishedRuleCount": len(published),
        "publishedCategories": [r.category for r in published],
    }


@router.post("/{proposal_id}/reject", response_model=dict)
def reject_proposal(
    proposal_id: str,
    payload: RejectProposalRequestSchema,
    current: CurrentUser = Depends(require_roles(*_APPROVER_ROLES)),
    repo: PolicyRuleProposalRepository = Depends(_proposal_repo),
    audit: AuditService = Depends(get_audit_service),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> dict[str, Any]:
    """Reject this proposal. ``policy_rules`` is never touched."""
    actor = Actor.from_current_user(current)
    proposal = _get_or_404(repo, proposal_id)
    if proposal.status is not ProposalStatus.DRAFT:
        raise ConflictError(
            f"Proposal is {proposal.status.value} and cannot be rejected.",
            details={"proposalId": proposal_id, "status": proposal.status.value},
        )

    repo.reject(proposal, actor_sub=actor.sub, reason=payload.reason)
    audit.record(
        actor=actor,
        action=AuditAction.POLICY_RULE_PROPOSAL_REVIEWED,
        entity_type=AuditEntity.POLICY_RULE_PROPOSAL,
        entity_id=proposal.id,
        details=f"Rejected proposal {proposal.id}: {payload.reason}",
        after={"proposalId": str(proposal.id), "reason": payload.reason},
    )
    uow.commit()

    return {"id": str(proposal.id), "status": proposal.status.value}


__all__ = ["router"]
