"""Extracted policy rules awaiting human approval, plus the metadata behind them.

Four tables, and a deliberate decision about which of them ``policy_rules`` is *not*:

``policy_rule_proposals``       one extraction run over one document version
``policy_rule_proposal_items``  one row per rule the model read out of that document
``policy_document_pages``       one row per page of the document, with its extraction metadata
``policy_document_sections``    one row per logical section, and the LLM call that processed it

**Nothing here is live configuration.** ``policy_rules`` is untouched by this migration and remains
the only table the policy engine reads. A proposal is a *reviewable claim about what a document
says*, which is a different thing with a different lifecycle: it can be wrong, it carries a
confidence, it cites its evidence, and it is discarded or approved rather than versioned forward.
Mixing the two would mean the engine could read an unreviewed model output.

``policy_rule_proposal_items`` therefore holds two columns ``policy_rules`` has no equivalent for,
and that is the point of it existing:

* ``basis`` — what an amount is measured *per*. ``policy_rules`` stores a scalar ``expense_limit``,
  so "$40 per day" and "$40 per claim" become the same row. A reviewer must see the difference
  before publishing, because the engine cannot.
* ``representable`` / ``unrepresentable_reason`` — whether the rule can map onto the closed
  ``expense_categories`` set at all. A policy document routinely states rules for categories the
  system has never had (communications, training, relocation). Dropping them would hide real policy;
  inventing categories for them would break the ``expense_categories`` ↔ ``policy_rules`` lockstep
  invariant. So they are stored, flagged, and excluded from publishing.

``policy_document_sections`` is the one table here that is *not* a record of anything a human
reviewed. It is a recomputable index over chunks — regenerated wholesale on every extraction run —
which is why its foreign key cascades where the proposal's deliberately restricts. What makes it
worth persisting rather than recomputing on demand is the second half of its columns: which model,
prompt and schema version processed each section, what that cost, and whether it succeeded. That is
the per-section observability and reproducibility trail, and it cannot be derived from anything else
after the fact.

Schema is owned by Alembic (ADR-001) — this declaration is the source migrations ``0009`` and
``0010`` were authored from.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.ai.core.enums import ProposalStatus, proposal_status_enum
from app.ai.models.knowledge import TenantMixin
from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

#: Same precision as ``policy_rules``' money columns, so a value cannot round on publish.
MONEY = Numeric(14, 2)


class PolicyRuleProposal(TenantMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One extraction run over one document version, awaiting review.

    The provenance columns are not decoration. An extracted limit is only auditable if you can say
    which model read which version of which document under which prompt — that is what lets a
    reviewer re-run an extraction and compare, and what makes a wrong published limit traceable to
    its source afterwards.
    """

    __tablename__ = "policy_rule_proposals"
    __table_args__ = (
        CheckConstraint(
            "(status <> 'APPROVED') OR (approved_by_sub IS NOT NULL AND approved_at IS NOT NULL)",
            name="ck_policy_rule_proposals_approved_has_approver",
        ),
        CheckConstraint(
            "(status <> 'REJECTED') OR (review_notes IS NOT NULL)",
            name="ck_policy_rule_proposals_rejected_has_reason",
        ),
        CheckConstraint(
            "rules_extracted >= 0 AND rules_representable >= 0 "
            "AND rules_representable <= rules_extracted",
            name="ck_policy_rule_proposals_counts_consistent",
        ),
        CheckConstraint(
            "sections_total >= 0 AND sections_failed >= 0 AND sections_failed <= sections_total",
            name="ck_policy_rule_proposals_section_counts",
        ),
        Index("ix_policy_rule_proposals_document", "knowledge_document_id"),
        Index("ix_policy_rule_proposals_status", "status"),
        Index("ix_policy_rule_proposals_tenant_status", "tenant_id", "status"),
    )

    # RESTRICT, not CASCADE: a proposal is the audit record of what was proposed, and archiving the
    # source document must not erase the reason a limit was published.
    knowledge_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "knowledge_documents.id",
            ondelete="RESTRICT",
            name="fk_policy_rule_proposals_document_id",
        ),
        nullable=False,
    )
    #: Snapshot of the document version extracted, so a later re-ingest is visibly a different run.
    document_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    document_title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    status: Mapped[ProposalStatus] = mapped_column(
        proposal_status_enum, nullable=False, server_default=ProposalStatus.DRAFT.value
    )

    # --- provenance ---
    llm_provider: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    llm_model: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    prompt_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 6), nullable=True)

    # --- summary counts, so a list view needs no aggregate query ---
    rules_extracted: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    rules_representable: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    #: What the model said the document's effective date was. Frequently ``NULL``: policy templates
    #: ship with a placeholder date, and the approver must supply the real one at publish time
    #: rather than have one guessed.
    document_effective_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    #: Anything the reviewer must decide, and the rejection reason when rejected.
    review_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    #: Irreconcilable readings of the same rule, found while merging the sections' outputs. Lives on
    #: the proposal rather than an item: a conflict is *between* items and belongs to neither.
    #: Shape: ``[{ruleId, category, gradeTier, severity, competing: [...]}]`` — see
    #: :mod:`app.ai.policy_extraction.merge`. Empty is the normal, healthy case.
    conflicts: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    #: Hash of the rendered prompt, and the version of the output schema it was validated against.
    #: With ``llm_model`` these are what make a run reproducible: the same document, model, prompt
    #: text and schema can be replayed to check a disputed limit.
    prompt_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    schema_version: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    #: How the document was divided, and how much of it was read. ``sections_failed > 0`` means the
    #: proposal is incomplete — some part of the document was never extracted — which a reviewer
    #: must see before approving.
    sections_total: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    sections_failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # --- review trail ---
    created_by_sub: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    approved_by_sub: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Effective date actually published, which need not equal ``document_effective_date``.
    published_effective_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    items: Mapped[List["PolicyRuleProposalItem"]] = relationship(
        "PolicyRuleProposalItem",
        back_populates="proposal",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="PolicyRuleProposalItem.line_number",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<PolicyRuleProposal {self.id} {self.status} items={self.rules_extracted}>"


class PolicyRuleProposalItem(TenantMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One rule read out of the document.

    Faithful to the source: a category stated once per grade band produces one row per band, even
    though ``policy_rules`` can only hold one row per category. The merge down to publishable shape
    happens at approval time in
    :func:`app.ai.policy_extraction.normalization.build_ruleset_payload`, not here — collapsing on
    write would throw away the document's own structure and make the proposal unreviewable against
    the PDF a reviewer is reading beside it.
    """

    __tablename__ = "policy_rule_proposal_items"
    __table_args__ = (
        UniqueConstraint(
            "proposal_id", "line_number", name="uq_policy_rule_proposal_items_line"
        ),
        CheckConstraint("line_number > 0", name="ck_policy_rule_proposal_items_line_positive"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_policy_rule_proposal_items_confidence_range",
        ),
        CheckConstraint(
            "max_amount IS NULL OR max_amount >= 0",
            name="ck_policy_rule_proposal_items_max_amount_non_negative",
        ),
        CheckConstraint(
            "auto_approve_limit IS NULL OR auto_approve_limit >= 0",
            name="ck_policy_rule_proposal_items_auto_approve_non_negative",
        ),
        CheckConstraint(
            "receipt_required_above IS NULL OR receipt_required_above >= 0",
            name="ck_policy_rule_proposal_items_receipt_non_negative",
        ),
        # An unpublishable rule must say why. Without this the reviewer sees an excluded rule with
        # no explanation and cannot tell a real gap from an extraction bug.
        CheckConstraint(
            "representable OR unrepresentable_reason IS NOT NULL",
            name="ck_policy_rule_proposal_items_unrepresentable_has_reason",
        ),
        # "always manual" and a numeric threshold are contradictory: one says never auto-approve,
        # the other names the amount below which it happens automatically.
        CheckConstraint(
            "NOT (always_manual AND auto_approve_limit IS NOT NULL)",
            name="ck_policy_rule_proposal_items_manual_excludes_threshold",
        ),
        CheckConstraint(
            "char_length(currency) = 3", name="ck_policy_rule_proposal_items_currency_iso4217"
        ),
        Index("ix_policy_rule_proposal_items_proposal", "proposal_id"),
        Index("ix_policy_rule_proposal_items_category", "category"),
        Index("ix_policy_rule_proposal_items_representable", "representable"),
        Index("ix_policy_rule_proposal_items_rule_id", "rule_id"),
        Index("ix_policy_rule_proposal_items_section", "section_id"),
    )

    proposal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "policy_rule_proposals.id",
            ondelete="CASCADE",
            name="fk_policy_rule_proposal_items_proposal_id",
        ),
        nullable=False,
    )
    #: Document order, so the review UI reads in the same sequence as the PDF.
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)

    #: Stable slot identifier derived from category + grade tier + basis (see
    #: :mod:`app.ai.policy_extraction.rule_ids`). Deliberately *not* unique per proposal: when two
    #: sections disagree about one rule, both readings are kept under the same id and the
    #: disagreement is recorded on the proposal. A unique constraint would force the merge step to
    #: silently drop one side, which is exactly the behaviour conflict detection exists to prevent.
    rule_id: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    #: Which detected section produced this rule. ``SET NULL`` on delete: sections are recomputed
    #: on every run, and losing one must never cascade into deleting a reviewed rule.
    section_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "policy_document_sections.id",
            ondelete="SET NULL",
            name="fk_policy_rule_proposal_items_section_id",
        ),
        nullable=True,
    )
    #: Deterministic findings from :mod:`app.ai.policy_extraction.validation` — never a reason to
    #: reject the extraction, always a reason for the reviewer to look closer at this row.
    validation_warnings: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    # --- fields mirroring PolicyRuleDefinitionSchema ---
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    sub_category: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    grade_tier: Mapped[str] = mapped_column(String(64), nullable=False, server_default="All Staff")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="USD")
    max_amount: Mapped[Optional[Decimal]] = mapped_column(MONEY, nullable=True)
    auto_approve_limit: Mapped[Optional[Decimal]] = mapped_column(MONEY, nullable=True)
    receipt_required_above: Mapped[Optional[Decimal]] = mapped_column(MONEY, nullable=True)
    requires_pre_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    special_rules: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    # --- the dimensions policy_rules cannot express ---
    #: What the amount is measured per. ``VARCHAR``: new bases must not need a migration.
    basis: Mapped[str] = mapped_column(String(32), nullable=False, server_default="OTHER")
    #: The document's wording when no single number expresses the limit ("rate x miles").
    limit_expression: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    #: The "— (always manual)" sentinel, kept distinct from a threshold of zero.
    always_manual: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    # --- evidence ---
    #: 1-based pages the rule's evidence appears on. A rule can cite several: a limits table whose
    #: header and data rows straddle a page break is still one rule.
    page_numbers: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    source_chunk_ids: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    #: Verbatim quote. A reviewer checks the numbers against this rather than re-reading the PDF.
    source_quote: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 2), nullable=True)

    # --- publishability ---
    representable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    unrepresentable_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- reviewer edits ---
    #: Set when a reviewer corrected the extraction. The original stays in the columns above, so a
    #: systematically mis-read table is still visible after correction.
    reviewer_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    reviewer_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    proposal: Mapped["PolicyRuleProposal"] = relationship(
        "PolicyRuleProposal", back_populates="items"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<PolicyRuleProposalItem {self.category} {self.grade_tier} {self.max_amount}>"


class PolicyDocumentPage(TenantMixin, UUIDPrimaryKeyMixin, Base):
    """Per-page extraction metadata for an ingested document.

    ``knowledge_chunks.page_number`` already records which page a chunk came from, but nothing
    described a *page* — so there was no way to answer "did page 3 extract cleanly?", "which pages
    held tables?", or "which pages needed OCR?". Those are exactly the questions asked when an
    extracted limit looks wrong, and answering them from chunk rows means re-deriving per-page facts
    on every query.

    ``ocr_used`` is worth its column: the PDF parser falls back to Textract **per page**, so one bad
    page in an otherwise clean document is both possible and invisible without this.
    """

    __tablename__ = "policy_document_pages"
    __table_args__ = (
        UniqueConstraint(
            "knowledge_document_id", "page_number", name="uq_policy_document_pages_page"
        ),
        CheckConstraint("page_number > 0", name="ck_policy_document_pages_page_positive"),
        CheckConstraint(
            "char_count >= 0 AND token_count >= 0 AND chunk_count >= 0 "
            "AND rules_extracted_count >= 0",
            name="ck_policy_document_pages_counts_non_negative",
        ),
        Index("ix_policy_document_pages_document", "knowledge_document_id"),
        Index("ix_policy_document_pages_tables", "table_detected"),
    )

    knowledge_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "knowledge_documents.id",
            ondelete="CASCADE",
            name="fk_policy_document_pages_document_id",
        ),
        nullable=False,
    )
    #: 1-based, matching ``knowledge_chunks.page_number``.
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)

    #: Heading detected on the page, used as the citation anchor in a review UI.
    section_heading: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: Whether the page appears to hold a limits table — including the malformed inline-pipe form a
    #: PDF extractor produces, which is where extraction most often goes wrong.
    table_detected: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    #: True when the text layer was empty and the page went through OCR.
    ocr_used: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    #: How many rules were read from this page, so a table-bearing page that yielded none is
    #: visible.
    rules_extracted_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    chunk_ids: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    page_metadata: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<PolicyDocumentPage p{self.page_number} chunks={self.chunk_count}>"


class PolicyDocumentSection(TenantMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One logical section of a document, and the record of the LLM call that extracted it.

    **Why sections are stored at all**, given they are derived from chunks and recomputed every run:
    the derivation is not the valuable part — the *outcome* is. Without this table nothing answers
    the questions that matter when an extracted limit looks wrong. Which part of the document
    produced this rule? Did every part actually get read, or did one call fail? What did this run
    cost, and under which model and prompt? All of that is per-section and unrecoverable afterwards.

    **Recomputed, not versioned.** Every extraction run replaces this document's rows wholesale
    (the same delete-and-recreate pattern ``policy_document_pages`` uses). A section indexes chunks
    rather than recording a review, so there is nothing here worth carrying forward — which is also
    why the foreign key cascades while ``policy_rule_proposals``' deliberately restricts.

    ``status``/``error_message``/``retry_count`` are what make a partially-failed extraction
    honest: eleven ``EXTRACTED`` rows and one ``FAILED`` says precisely which pages were never read,
    instead of a proposal that looks complete and quietly is not. They also carry the shape a
    retry-one-section operation will need, without that operation existing yet.
    """

    __tablename__ = "policy_document_sections"
    __table_args__ = (
        UniqueConstraint(
            "knowledge_document_id", "section_index", name="uq_policy_document_sections_index"
        ),
        CheckConstraint("section_index >= 0", name="ck_policy_document_sections_index_positive"),
        CheckConstraint(
            "start_page > 0 AND end_page >= start_page",
            name="ck_policy_document_sections_page_range",
        ),
        CheckConstraint(
            "char_count >= 0 AND token_count >= 0 AND chunk_count >= 0 AND retry_count >= 0",
            name="ck_policy_document_sections_counts_non_negative",
        ),
        CheckConstraint(
            "detection_confidence IS NULL "
            "OR (detection_confidence >= 0 AND detection_confidence <= 1)",
            name="ck_policy_document_sections_confidence_range",
        ),
        # A failed section must say why. Otherwise a reviewer sees a gap in the document with no
        # way to tell a provider outage from a section that legitimately held no rules.
        CheckConstraint(
            "status <> 'FAILED' OR error_message IS NOT NULL",
            name="ck_policy_document_sections_failed_has_error",
        ),
        Index("ix_policy_document_sections_document", "knowledge_document_id"),
        Index("ix_policy_document_sections_status", "status"),
    )

    knowledge_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "knowledge_documents.id",
            ondelete="CASCADE",
            name="fk_policy_document_sections_document_id",
        ),
        nullable=False,
    )
    #: 0-based position in document order.
    section_index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    start_page: Mapped[int] = mapped_column(Integer, nullable=False)
    end_page: Mapped[int] = mapped_column(Integer, nullable=False)

    # --- content ---
    #: The exact text sent to the model, page markers included. Stored rather than re-derived so a
    #: disputed rule can be checked against what was actually read, not against a re-chunked
    #: approximation of it.
    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    chunk_ids: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    table_detected: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    # --- how the boundary was found ---
    #: ``NUMBERING`` | ``HEADING`` | ``SEMANTIC`` | ``LLM``. ``VARCHAR``: a new detector (a
    #: layout-aware parser, say) must not need a migration.
    detection_method: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    detection_confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 2), nullable=True)

    # --- provenance of the extraction call ---
    llm_provider: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    llm_model: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    prompt_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    #: SHA-256 of the *rendered* prompt. The template alone does not identify a call — the same
    #: template over a re-ingested document is a different prompt.
    prompt_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    schema_version: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    #: The Bedrock ``effort`` setting. There is no ``temperature`` column because current Claude
    #: models reject a non-default sampling parameter outright — recording one would be recording a
    #: value that was never sent.
    reasoning_effort: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    input_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 6), nullable=True)

    # --- lifecycle ---
    #: ``PENDING`` | ``EXTRACTED`` | ``FAILED``. ``VARCHAR``, not a native enum: this is operational
    #: state, and nothing about approval depends on it, unlike ``ProposalStatus``.
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="PENDING")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    rules_extracted_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<PolicyDocumentSection {self.section_index} {self.title!r} "
            f"p{self.start_page}-{self.end_page} {self.status}>"
        )


__all__ = [
    "MONEY",
    "PolicyDocumentPage",
    "PolicyDocumentSection",
    "PolicyRuleProposal",
    "PolicyRuleProposalItem",
]
