"""Sequences the extraction pipeline. Owns the order of operations and nothing else.

Every stage below is implemented elsewhere, by a module that owns that concern alone:

    detect        sectioning.SectionDetectionPipeline      chunks -> logical sections
    split         sectioning.split_oversized_sections      sections -> sections within budget
    execute       executor.SequentialSectionExecutor       sections -> per-section outcomes
    normalize     normalization.normalize_extraction       raw payload -> NormalizedRule
    merge         merge.merge_section_rules                per-section rules -> one set + conflicts
    validate      validation.validate_rules                rules -> deterministic findings
    persist       repositories                             proposal, items, sections, pages

This module is the only thing that knows the order, and it deliberately holds no rule logic of its
own — no parsing, no thresholds, no decisions about what a rule means. When something about
extraction behaviour needs to change, the change belongs in a stage; a change here means the
*pipeline shape* changed.

**Nothing in this package decides anything.** ``app/ai/**`` cannot import ``ClaimService``,
``ClaimRepository`` or ``policy_engine`` (``tests/test_ai_architecture.py`` enforces it), so the
furthest this pipeline can go is writing a ``DRAFT`` proposal. Turning one into live configuration
happens in a different module outside this package, behind a human approval.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

from app.ai.core.config import ai_settings
from app.ai.core.enums import IngestionStage
from app.ai.core.errors import KnowledgeNotFoundError
from app.ai.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.ai.models.policy_proposal import PolicyRuleProposal, PolicyRuleProposalItem
from app.ai.policy_extraction.executor import (
    SectionExtractionExecutor,
    SectionExtractionOutcome,
    SequentialSectionExecutor,
)
from app.ai.policy_extraction.extractor import (
    EXTRACTION_SCHEMA_VERSION,
    ExtractionContext,
    PolicyExtractor,
)
from app.ai.policy_extraction.merge import RuleConflict, merge_section_rules
from app.ai.policy_extraction.normalization import NormalizedRule, normalize_extraction
from app.ai.policy_extraction.sectioning import (
    DetectedSection,
    SectionDetectionPipeline,
    SectionStatus,
    SubsectionAdvisor,
    split_oversized_sections,
)
from app.ai.policy_extraction.validation import ValidationWarning, validate_rules
from app.ai.repositories.knowledge_repository import KnowledgeChunkRepository
from app.ai.repositories.policy_proposal_repository import (
    PolicyDocumentPageRepository,
    PolicyDocumentSectionRepository,
    PolicyRuleProposalRepository,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Rough words-per-token, used only to size a page's stored token count when the chunk row does not
#: carry one. Never billed usage — that comes from the provider's own reported ``TokenUsage``.
_CHARS_PER_TOKEN_ESTIMATE = 4

_TABLE_PIPE_THRESHOLD = 3


class PolicyExtractionOrchestrator:
    """Runs one extraction over one document version and writes a ``DRAFT`` proposal."""

    def __init__(
        self,
        *,
        chunk_repository: KnowledgeChunkRepository,
        proposal_repository: PolicyRuleProposalRepository,
        page_repository: PolicyDocumentPageRepository,
        section_repository: PolicyDocumentSectionRepository,
        extractor: PolicyExtractor,
        executor: Optional[SectionExtractionExecutor] = None,
        detection_pipeline: Optional[SectionDetectionPipeline] = None,
        subsection_advisor: Optional[SubsectionAdvisor] = None,
        max_section_tokens: Optional[int] = None,
    ) -> None:
        self._chunks = chunk_repository
        self._proposals = proposal_repository
        self._pages = page_repository
        self._sections = section_repository
        self._extractor = extractor
        self._executor = executor or SequentialSectionExecutor()
        self._detection = detection_pipeline or SectionDetectionPipeline()
        self._advisor = subsection_advisor
        self._max_section_tokens = (
            max_section_tokens or ai_settings.EXTRACTION_MAX_SECTION_TOKENS
        )

    def run(
        self,
        document: KnowledgeDocument,
        *,
        tenant_id: str,
        actor_sub: Optional[str],
        known_categories: Sequence[str],
    ) -> PolicyRuleProposal:
        chunks = self._chunks.list_for_document(document.id, tenant_id=tenant_id)
        if not chunks:
            raise KnowledgeNotFoundError(
                "KnowledgeChunk", f"document {document.id} has no chunks; ingest it first"
            )

        # Before the (slow, multi-call) extraction, not after: a caller who re-extracts twice in
        # quick succession must not end up with two live drafts a reviewer could approve in either
        # order.
        self._proposals.supersede_open_proposals(document.id, tenant_id=tenant_id)

        sections = self._plan_sections(chunks, known_categories=known_categories)
        outcomes = self._executor.run(
            sections,
            extractor=self._extractor,
            context=ExtractionContext(
                document_title=document.title or "(untitled)",
                document_currency=document.currency or "USD",
                known_categories=tuple(known_categories),
                section_count=len(sections),
            ),
        )

        per_section = [
            self._rules_of(outcome, known_categories=known_categories) for outcome in outcomes
        ]
        merge = merge_section_rules(per_section)
        warnings = validate_rules(merge.rules)

        proposal, items = self._write_proposal(
            document,
            tenant_id=tenant_id,
            actor_sub=actor_sub,
            outcomes=outcomes,
            rules=merge.rules,
            conflicts=merge.conflicts,
            warnings=warnings,
        )
        section_rows = self._write_sections(
            document, tenant_id=tenant_id, outcomes=outcomes, rules=merge.rules
        )
        _attach_sections(items, merge.rules, section_rows)
        self._write_page_metadata(
            document, chunks=chunks, rules=merge.rules, tenant_id=tenant_id
        )

        logger.info(
            "ai.policy_extraction.completed",
            extra={
                "documentId": str(document.id),
                "proposalId": str(proposal.id),
                "sections": len(outcomes),
                "sectionsFailed": sum(1 for o in outcomes if not o.ok),
                "rulesExtracted": proposal.rules_extracted,
                "rulesRepresentable": proposal.rules_representable,
                "conflicts": len(merge.conflicts),
                "stage": IngestionStage.EXTRACT_RULES.value,
            },
        )
        return proposal

    # --- stages ---------------------------------------------------------------

    def _plan_sections(
        self, chunks: Sequence[KnowledgeChunk], *, known_categories: Sequence[str]
    ) -> list[DetectedSection]:
        detected = self._detection.detect(chunks, known_categories=known_categories)
        return split_oversized_sections(
            detected,
            chunks_by_id={chunk.id: chunk for chunk in chunks},
            max_tokens=self._max_section_tokens,
            advisor=self._advisor,
        )

    def _rules_of(
        self, outcome: SectionExtractionOutcome, *, known_categories: Sequence[str]
    ) -> list[NormalizedRule]:
        """Normalize one section's payload and stamp its provenance onto every rule.

        ``section_index`` and ``source_chunk_ids`` are attached here rather than inside
        ``normalize_rule`` because normalization sees one rule at a time and has no view of the
        document — but they are what completes the traceability chain from a published limit back to
        the chunk text it was read from.
        """
        if outcome.result is None:
            return []
        rules = normalize_extraction(outcome.result.payload, known_categories=known_categories)
        chunk_ids = tuple(str(cid) for cid in outcome.section.chunk_ids)
        return [
            _with_source(rule, section_index=outcome.section.index, chunk_ids=chunk_ids)
            for rule in rules
        ]

    # --- persistence ----------------------------------------------------------

    def _write_proposal(
        self,
        document: KnowledgeDocument,
        *,
        tenant_id: str,
        actor_sub: Optional[str],
        outcomes: Sequence[SectionExtractionOutcome],
        rules: Sequence[NormalizedRule],
        conflicts: Sequence[RuleConflict],
        warnings: Mapping[str, list[ValidationWarning]],
    ) -> tuple[PolicyRuleProposal, list[PolicyRuleProposalItem]]:
        successful = [o for o in outcomes if o.ok and o.result is not None]
        first = successful[0].result if successful else None

        proposal = self._proposals.create(
            PolicyRuleProposal(
                tenant_id=tenant_id,
                knowledge_document_id=document.id,
                document_version=document.version,
                document_title=document.title,
                # Every section shares one model, prompt and schema, so the run-level provenance is
                # any successful section's. Per-section detail lives on the section rows.
                llm_provider=first.metrics.provider if first else None,
                llm_model=first.metrics.model if first else None,
                prompt_code="POLICY_RULE_EXTRACTION",
                prompt_version=first.metrics.prompt_version if first else None,
                prompt_hash=first.metrics.prompt_hash if first else None,
                schema_version=EXTRACTION_SCHEMA_VERSION,
                input_tokens=sum(o.result.metrics.input_tokens for o in successful),
                output_tokens=sum(o.result.metrics.output_tokens for o in successful),
                latency_ms=sum(o.result.metrics.latency_ms for o in successful),
                cost_usd=sum(
                    (o.result.metrics.cost_usd for o in successful), Decimal("0")
                ),
                sections_total=len(outcomes),
                sections_failed=sum(1 for o in outcomes if not o.ok),
                conflicts=[conflict.to_dict() for conflict in conflicts] or None,
                document_effective_date=_effective_date(outcomes),
                review_notes=_review_notes(outcomes, conflicts),
                created_by_sub=actor_sub,
            )
        )
        items = self._proposals.add_items(
            proposal, [_to_item_row(rule, warnings.get(rule.rule_id, [])) for rule in rules]
        )
        return proposal, items

    def _write_sections(
        self,
        document: KnowledgeDocument,
        *,
        tenant_id: str,
        outcomes: Sequence[SectionExtractionOutcome],
        rules: Sequence[NormalizedRule],
    ) -> list[Any]:
        rules_per_section: dict[int, int] = defaultdict(int)
        for rule in rules:
            if rule.section_index is not None:
                rules_per_section[rule.section_index] += 1

        rows = []
        for outcome in outcomes:
            section = outcome.section
            metrics = outcome.result.metrics if outcome.result else None
            rows.append(
                {
                    "section_index": section.index,
                    "title": section.title[:500],
                    "start_page": section.start_page,
                    "end_page": section.end_page,
                    "text": section.text,
                    "char_count": section.char_count,
                    "token_count": section.token_count,
                    "chunk_count": section.chunk_count,
                    "chunk_ids": [str(cid) for cid in section.chunk_ids],
                    "table_detected": section.table_detected,
                    "detection_method": section.detection_method.value,
                    "detection_confidence": section.detection_confidence,
                    "llm_provider": metrics.provider if metrics else None,
                    "llm_model": metrics.model if metrics else None,
                    "prompt_version": metrics.prompt_version if metrics else None,
                    "prompt_hash": metrics.prompt_hash if metrics else None,
                    "schema_version": metrics.schema_version if metrics else None,
                    "reasoning_effort": metrics.reasoning_effort if metrics else None,
                    "input_tokens": metrics.input_tokens if metrics else None,
                    "output_tokens": metrics.output_tokens if metrics else None,
                    "latency_ms": metrics.latency_ms if metrics else None,
                    "cost_usd": metrics.cost_usd if metrics else None,
                    "status": (
                        SectionStatus.EXTRACTED.value if outcome.ok
                        else SectionStatus.FAILED.value
                    ),
                    "error_message": outcome.error,
                    "retry_count": max(outcome.attempts - 1, 0),
                    "rules_extracted_count": rules_per_section.get(section.index, 0),
                }
            )
        return list(
            self._sections.replace_for_document(
                document.id, tenant_id=tenant_id, sections=rows
            )
        )

    def _write_page_metadata(
        self,
        document: KnowledgeDocument,
        *,
        chunks: Sequence[KnowledgeChunk],
        rules: Sequence[NormalizedRule],
        tenant_id: str,
    ) -> None:
        """Per-page metadata, derived from chunks exactly as before sectioning existed.

        Unchanged by the redesign on purpose: a page is still a page whatever sections were detected
        over it, and a reviewer citing "p3" needs the same answer either way.
        """
        by_page: dict[int, list[KnowledgeChunk]] = defaultdict(list)
        for chunk in chunks:
            if chunk.page_number:
                by_page[chunk.page_number].append(chunk)

        rules_per_page: dict[int, int] = defaultdict(int)
        for rule in rules:
            for page in rule.page_numbers:
                rules_per_page[page] += 1

        rows = [
            {
                "page_number": page,
                "section_heading": page_chunks[0].section if page_chunks else None,
                "char_count": sum(len(c.content) for c in page_chunks),
                "token_count": sum(
                    c.content_tokens or _estimate_tokens(c.content) for c in page_chunks
                ),
                "chunk_count": len(page_chunks),
                "table_detected": any(_looks_like_a_table(c.content) for c in page_chunks),
                "ocr_used": any(
                    bool((c.chunk_metadata or {}).get("ocrUsed")) for c in page_chunks
                ),
                "rules_extracted_count": rules_per_page.get(page, 0),
                "chunk_ids": [str(c.id) for c in page_chunks],
            }
            for page, page_chunks in sorted(by_page.items())
        ]
        self._pages.replace_for_document(document.id, tenant_id=tenant_id, pages=rows)


# --- helpers ------------------------------------------------------------------


def _with_source(
    rule: NormalizedRule, *, section_index: int, chunk_ids: tuple[str, ...]
) -> NormalizedRule:
    from dataclasses import replace

    return replace(rule, section_index=section_index, source_chunk_ids=chunk_ids)


def _attach_sections(
    items: Sequence[PolicyRuleProposalItem],
    rules: Sequence[NormalizedRule],
    section_rows: Sequence[Any],
) -> None:
    """Point each item at the section row it came from.

    Done after both are persisted rather than at item-construction time, because a section row has
    no database id until it is written. The resulting FK is what a reviewer follows from a
    suspicious rule to the exact text that produced it — and, with the item's ``source_chunk_ids``,
    completes the trail down to the original chunk and page.

    ``items`` and ``rules`` are positionally aligned: ``add_items`` preserves the order given,
    and it was given one item per rule in order.
    """
    by_index = {row.section_index: row.id for row in section_rows}
    for item, rule in zip(items, rules, strict=True):
        if rule.section_index is not None:
            item.section_id = by_index.get(rule.section_index)


def _to_item_row(
    rule: NormalizedRule, warnings: Sequence[ValidationWarning]
) -> PolicyRuleProposalItem:
    return PolicyRuleProposalItem(
        id=uuid.uuid4(),
        rule_id=rule.rule_id or None,
        category=rule.category,
        sub_category=rule.sub_category,
        grade_tier=rule.grade_tier,
        currency=rule.currency,
        max_amount=rule.max_amount,
        auto_approve_limit=rule.auto_approve_limit,
        receipt_required_above=rule.receipt_required_above,
        requires_pre_approval=rule.requires_pre_approval,
        special_rules=rule.special_rules or None,
        basis=rule.basis.value,
        limit_expression=rule.limit_expression,
        always_manual=rule.always_manual,
        page_numbers=list(rule.page_numbers) or None,
        source_chunk_ids=list(rule.source_chunk_ids) or None,
        source_quote=rule.source_quote or None,
        confidence=rule.confidence,
        representable=rule.representable,
        unrepresentable_reason=rule.unrepresentable_reason,
        validation_warnings=[w.to_dict() for w in warnings] or None,
    )


def _effective_date(outcomes: Sequence[SectionExtractionOutcome]) -> Optional[date]:
    """The document's effective date, only when the sections that stated one agree.

    Disagreement returns ``None`` and is reported in the review notes instead. Picking the first
    section's answer would be arbitrary — the front matter and an appendix are equally plausible
    places for the real date — and a wrong effective date silently mis-scopes every published rule.
    """
    candidates: list[date] = []
    for outcome in outcomes:
        if outcome.result is None:
            continue
        parsed = _parse_date(outcome.result.payload.get("documentEffectiveDate"))
        if parsed is not None and parsed not in candidates:
            candidates.append(parsed)
    return candidates[0] if len(candidates) == 1 else None


def _review_notes(
    outcomes: Sequence[SectionExtractionOutcome], conflicts: Sequence[RuleConflict]
) -> Optional[str]:
    """Everything the reviewer must know before approving, as one readable block.

    Failed sections come first and are stated plainly: they mean the proposal is incomplete —
    the single most important thing to know about it, and the one thing no amount of reading the
    extracted rules would reveal.
    """
    lines: list[str] = []

    failed = [o for o in outcomes if not o.ok]
    if failed:
        lines.append(
            f"INCOMPLETE: {len(failed)} of {len(outcomes)} sections could not be extracted. "
            "The rules they contain are missing from this proposal."
        )
        lines.extend(
            f"  - Section {o.section.index + 1} '{o.section.title}' ({o.section.page_range}): "
            f"{o.error}"
            for o in failed
        )

    if conflicts:
        lines.append(
            f"{len(conflicts)} rule(s) were stated inconsistently in different parts of the "
            "document. Every reading is included below; resolve each before approving."
        )
        lines.extend(
            f"  - {conflict.category} ({conflict.grade_tier}): "
            + " vs ".join(_describe(value) for value in conflict.competing)
            for conflict in conflicts
        )

    dates = {
        _parse_date((o.result.payload or {}).get("documentEffectiveDate"))
        for o in outcomes
        if o.result is not None
    }
    dates.discard(None)
    if len(dates) > 1:
        lines.append(
            "Sections disagree on the document's effective date ("
            + ", ".join(sorted(d.isoformat() for d in dates if d))
            + "). None has been recorded; supply the correct one at approval."
        )

    for outcome in outcomes:
        note = (outcome.result.payload or {}).get("reviewNotes") if outcome.result else None
        if note:
            lines.append(f"[{outcome.section.title}] {note}")

    return "\n".join(lines) if lines else None


def _describe(value: Any) -> str:
    amount = value.max_amount
    shown = f"{amount}" if amount is not None else (value.limit_expression or "unstated")
    pages = ", ".join(f"p{page}" for page in value.pages) or "no page"
    return f"{shown} ({pages})"


def _parse_date(value: Any) -> Optional[date]:
    """Parse an ISO date, or ``None`` for anything else.

    Anything unparseable — including the literal ``"[insert date]"`` a policy template ships with —
    becomes ``None``. The pipeline never guesses a date; the approver supplies one.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN_ESTIMATE)


def _looks_like_a_table(text: str) -> bool:
    return any(line.count("|") >= _TABLE_PIPE_THRESHOLD for line in text.splitlines())


__all__ = ["PolicyExtractionOrchestrator"]
