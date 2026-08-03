"""Persistence for extracted rule proposals and per-page document metadata.

Every method is tenant-scoped, matching the convention the other AI repositories follow: the
``tenant_id`` filter lives here rather than being left to call sites, so a forgotten filter cannot
leak one tenant's proposals into another's review queue.

State transitions are the interesting part. :meth:`PolicyRuleProposalRepository.approve` and
:meth:`reject` both refuse to act on a proposal that is not ``DRAFT``, which is what makes
double-approval impossible — the database check constraint guarantees an approved row *has* an
approver, and this guarantees it only gets one.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional, Sequence

from sqlalchemy import func, select

from app.ai.core.enums import ProposalStatus
from app.ai.models.policy_proposal import (
    PolicyDocumentPage,
    PolicyDocumentSection,
    PolicyRuleProposal,
    PolicyRuleProposalItem,
)
from app.domain.errors import ConflictError
from app.repositories.base import BaseRepository


class PolicyRuleProposalRepository(BaseRepository[PolicyRuleProposal]):
    """Proposals and their items."""

    model = PolicyRuleProposal

    def get(self, proposal_id: uuid.UUID, *, tenant_id: str) -> Optional[PolicyRuleProposal]:
        return self._one_or_none(
            select(PolicyRuleProposal).where(
                PolicyRuleProposal.id == proposal_id,
                PolicyRuleProposal.tenant_id == tenant_id,
            )
        )

    def list_recent(
        self,
        *,
        tenant_id: str,
        status: Optional[ProposalStatus] = None,
        document_id: Optional[uuid.UUID] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[PolicyRuleProposal]:
        query = select(PolicyRuleProposal).where(PolicyRuleProposal.tenant_id == tenant_id)
        if status is not None:
            query = query.where(PolicyRuleProposal.status == status)
        if document_id is not None:
            query = query.where(PolicyRuleProposal.knowledge_document_id == document_id)
        return self._all(
            query.order_by(PolicyRuleProposal.created_at.desc()).limit(limit).offset(offset)
        )

    def create(self, proposal: PolicyRuleProposal) -> PolicyRuleProposal:
        self.session.add(proposal)
        self.session.flush()
        return proposal

    def add_items(
        self, proposal: PolicyRuleProposal, items: Sequence[PolicyRuleProposalItem]
    ) -> list[PolicyRuleProposalItem]:
        """Attach items in document order, numbering them 1..n.

        ``line_number`` is assigned here rather than by the caller so the unique
        ``(proposal_id, line_number)`` constraint cannot be violated by a caller that miscounts.
        """
        attached: list[PolicyRuleProposalItem] = []
        for index, item in enumerate(items, start=1):
            item.proposal_id = proposal.id
            item.tenant_id = proposal.tenant_id
            item.line_number = index
            self.session.add(item)
            attached.append(item)
        self.session.flush()

        proposal.rules_extracted = len(attached)
        proposal.rules_representable = sum(1 for item in attached if item.representable)
        self.session.flush()
        return attached

    def supersede_open_proposals(
        self, document_id: uuid.UUID, *, tenant_id: str, except_id: Optional[uuid.UUID] = None
    ) -> int:
        """Mark this document's other ``DRAFT`` proposals ``SUPERSEDED``.

        Re-extracting a document must not leave two live drafts a reviewer could approve in either
        order. The older one stays readable as the record of what was proposed at the time, rather
        than being deleted.
        """
        superseded = 0
        for proposal in self._all(
            select(PolicyRuleProposal).where(
                PolicyRuleProposal.tenant_id == tenant_id,
                PolicyRuleProposal.knowledge_document_id == document_id,
                PolicyRuleProposal.status == ProposalStatus.DRAFT,
            )
        ):
            if except_id is not None and proposal.id == except_id:
                continue
            proposal.status = ProposalStatus.SUPERSEDED
            superseded += 1
        if superseded:
            self.session.flush()
        return superseded

    def approve(
        self,
        proposal: PolicyRuleProposal,
        *,
        actor_sub: Optional[str],
        effective_date: date,
        notes: Optional[str] = None,
    ) -> PolicyRuleProposal:
        """Mark ``proposal`` approved. Refuses anything not currently ``DRAFT``."""
        self._require_draft(proposal, action="approved")
        proposal.status = ProposalStatus.APPROVED
        proposal.approved_by_sub = actor_sub
        proposal.approved_at = datetime.now(timezone.utc)
        proposal.published_effective_date = effective_date
        if notes:
            proposal.review_notes = notes
        self.session.flush()
        return proposal

    def reject(
        self, proposal: PolicyRuleProposal, *, actor_sub: Optional[str], reason: str
    ) -> PolicyRuleProposal:
        """Mark ``proposal`` rejected. ``reason`` is required by a database check constraint."""
        self._require_draft(proposal, action="rejected")
        proposal.status = ProposalStatus.REJECTED
        proposal.approved_by_sub = actor_sub
        proposal.review_notes = reason
        self.session.flush()
        return proposal

    @staticmethod
    def _require_draft(proposal: PolicyRuleProposal, *, action: str) -> None:
        if proposal.status is not ProposalStatus.DRAFT:
            raise ConflictError(
                f"Proposal is {proposal.status.value} and cannot be {action}.",
                details={
                    "proposalId": str(proposal.id),
                    "status": proposal.status.value,
                    "expected": ProposalStatus.DRAFT.value,
                },
            )


class PolicyDocumentPageRepository(BaseRepository[PolicyDocumentPage]):
    """Per-page extraction metadata."""

    model = PolicyDocumentPage

    def replace_for_document(
        self, document_id: uuid.UUID, *, tenant_id: str, pages: Sequence[dict[str, Any]]
    ) -> list[PolicyDocumentPage]:
        """Replace this document's page rows.

        A full replace, not an upsert: the unique ``(document_id, page_number)`` constraint means a
        re-extraction would otherwise collide, and a stale row for a page that no longer exists
        would be worse than none.
        """
        self.delete_for_document(document_id, tenant_id=tenant_id)
        rows = [
            PolicyDocumentPage(
                tenant_id=tenant_id, knowledge_document_id=document_id, **page
            )
            for page in pages
        ]
        self.session.add_all(rows)
        self.session.flush()
        return rows

    def list_for_document(
        self, document_id: uuid.UUID, *, tenant_id: str
    ) -> Sequence[PolicyDocumentPage]:
        return self._all(
            select(PolicyDocumentPage)
            .where(
                PolicyDocumentPage.tenant_id == tenant_id,
                PolicyDocumentPage.knowledge_document_id == document_id,
            )
            .order_by(PolicyDocumentPage.page_number)
        )

    def delete_for_document(self, document_id: uuid.UUID, *, tenant_id: str) -> int:
        rows = list(self.list_for_document(document_id, tenant_id=tenant_id))
        for row in rows:
            self.session.delete(row)
        if rows:
            self.session.flush()
        return len(rows)

    def count_for_document(self, document_id: uuid.UUID, *, tenant_id: str) -> int:
        return int(
            self.session.execute(
                select(func.count())
                .select_from(PolicyDocumentPage)
                .where(
                    PolicyDocumentPage.tenant_id == tenant_id,
                    PolicyDocumentPage.knowledge_document_id == document_id,
                )
            ).scalar()
            or 0
        )


class PolicyDocumentSectionRepository(BaseRepository[PolicyDocumentSection]):
    """Logical sections and their per-section extraction outcomes."""

    model = PolicyDocumentSection

    def replace_for_document(
        self, document_id: uuid.UUID, *, tenant_id: str, sections: Sequence[dict[str, Any]]
    ) -> list[PolicyDocumentSection]:
        """Replace this document's section rows.

        A full replace rather than an upsert, mirroring
        :meth:`PolicyDocumentPageRepository.replace_for_document`. Detection is re-run from scratch
        on every extraction, so the previous run's boundaries carry no information — and since
        ``(document_id, section_index)`` is unique, a re-detection that produced a different number
        of sections would otherwise either collide or leave orphaned rows describing sections that
        no longer exist.
        """
        self.delete_for_document(document_id, tenant_id=tenant_id)
        rows = [
            PolicyDocumentSection(
                tenant_id=tenant_id, knowledge_document_id=document_id, **section
            )
            for section in sections
        ]
        self.session.add_all(rows)
        self.session.flush()
        return rows

    def list_for_document(
        self, document_id: uuid.UUID, *, tenant_id: str
    ) -> Sequence[PolicyDocumentSection]:
        return self._all(
            select(PolicyDocumentSection)
            .where(
                PolicyDocumentSection.tenant_id == tenant_id,
                PolicyDocumentSection.knowledge_document_id == document_id,
            )
            .order_by(PolicyDocumentSection.section_index)
        )

    def delete_for_document(self, document_id: uuid.UUID, *, tenant_id: str) -> int:
        rows = list(self.list_for_document(document_id, tenant_id=tenant_id))
        for row in rows:
            self.session.delete(row)
        if rows:
            # Flushed before the caller inserts replacements, or the unique
            # (document_id, section_index) constraint fires against rows already marked deleted.
            self.session.flush()
        return len(rows)

    def list_failed(
        self, document_id: uuid.UUID, *, tenant_id: str
    ) -> Sequence[PolicyDocumentSection]:
        """Sections whose extraction call did not succeed.

        The query a retry-failed-sections operation will run, and what a reviewer needs to see which
        pages of a document were never read.
        """
        return self._all(
            select(PolicyDocumentSection)
            .where(
                PolicyDocumentSection.tenant_id == tenant_id,
                PolicyDocumentSection.knowledge_document_id == document_id,
                PolicyDocumentSection.status == "FAILED",
            )
            .order_by(PolicyDocumentSection.section_index)
        )


__all__ = [
    "PolicyDocumentPageRepository",
    "PolicyDocumentSectionRepository",
    "PolicyRuleProposalRepository",
]
