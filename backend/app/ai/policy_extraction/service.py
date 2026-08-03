"""The entry point callers use to extract rules from an ingested document.

A thin façade over :class:`~app.ai.policy_extraction.orchestrator.PolicyExtractionOrchestrator`, and
thin on purpose. ``extract()`` used to hold the whole pipeline — chunk grouping, prompt assembly,
the model call, normalization, persistence — which made every one of those concerns untestable
without a provider and a database, and meant a change to any of them touched the same function.

The class survives because the API route, the composition root, and the architecture tests all name
it, and because "extract this document" is the right shape for a caller. What it no longer does is
decide *how*.

Nothing here, or anywhere beneath it, can approve anything: ``app/ai/**`` is banned from importing
``ClaimService``/``ClaimRepository``/``policy_engine`` (``tests/test_ai_architecture.py``), so the
furthest this reaches is a ``DRAFT`` proposal. Publishing lives outside this package, behind a
human.
"""

from __future__ import annotations

from typing import Optional, Sequence

from app.ai.models.knowledge import KnowledgeDocument
from app.ai.models.policy_proposal import PolicyRuleProposal
from app.ai.policy_extraction.orchestrator import PolicyExtractionOrchestrator


class PolicyRuleExtractionService:
    """Extracts structured rules from one document version and writes a ``DRAFT`` proposal."""

    def __init__(self, *, orchestrator: PolicyExtractionOrchestrator) -> None:
        self._orchestrator = orchestrator

    def extract(
        self,
        document: KnowledgeDocument,
        *,
        tenant_id: str,
        actor_sub: Optional[str],
        known_categories: Sequence[str],
    ) -> PolicyRuleProposal:
        """Run one extraction over ``document`` and persist the resulting ``DRAFT`` proposal."""
        return self._orchestrator.run(
            document,
            tenant_id=tenant_id,
            actor_sub=actor_sub,
            known_categories=known_categories,
        )


__all__ = ["PolicyRuleExtractionService"]
