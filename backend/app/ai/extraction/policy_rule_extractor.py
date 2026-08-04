"""Gemini-powered extraction of candidate policy rules from indexed document chunks.

Sends each section of the document to Gemini 2.5 Flash with a structured prompt that
requests ``PolicyRuleDefinition``-shaped JSON. Returns unsaved ``CandidatePolicyRule``
instances (the caller adds them to the session and commits).

**Section grouping**: chunks are grouped by their ``section`` field. Within a group the
text is concatenated in ``chunk_index`` order. Groups with fewer than 80 characters are
skipped — they are usually table-of-contents entries or whitespace artifacts, not rule
text. ``source_chunk_id`` is set to the first chunk in each group; ``source_page_number``
to its ``page_number``.

**Gemini unavailable**: raises ``ValueError`` rather than silently returning nothing.
A caller that asked for rule extraction and got no response because the API key is absent
should know immediately — there is no useful fallback for this operation.
"""

from __future__ import annotations

import json
import logging
import uuid
from decimal import Decimal, InvalidOperation
from itertools import groupby
from typing import Any, Optional, Sequence

from app.ai.models.knowledge import KnowledgeChunk
from app.models.candidate_rule import EXTRACTED_BY_GEMINI, CandidatePolicyRule

logger = logging.getLogger(__name__)

_MIN_SECTION_CHARS = 80

_PROMPT_TEMPLATE = """\
You are an expert policy analyst for corporate expense reimbursement.

Analyze the following section of a corporate expense policy document and extract \
every expense reimbursement rule or spending limit it defines.

For each rule found, produce a JSON object matching this exact schema:
{{
  "name": "string — 3 to 8 word descriptive name",
  "category": "string — one of: meals, travel, lodging, entertainment, flights, ground_transport, client_entertainment, other",
  "description": "string — what the rule says, quoting the source text verbatim where possible",
  "expense_limit": null or number — maximum reimbursable amount in USD, null if not stated,
  "limit_expression": null or "string" — if the limit is prose not a number (e.g. 'Actual cost with manager approval'), else null,
  "auto_approve_limit": null or number — amount below which auto-approval applies,
  "receipt_required_above": null or number — threshold above which a receipt is required,
  "requires_pre_approval": false or true,
  "grade_tier": null or "string" — employee tier this rule applies to, e.g. 'All Staff', 'Manager+', 'VP+',
  "country": null or "string" — ISO 3166-1 alpha-2 country code if country-specific, else null,
  "currency": "USD" — ISO 4217 code, default USD unless the document states otherwise,
  "special_rules": ["string"] — verbatim human-readable clauses, exceptions, or caveats
}}

Return a JSON object with a single key "rules" containing an array of rule objects:
{{"rules": [...]}}

If this section contains no expense rules, return: {{"rules": []}}

---
POLICY SECTION ({section_name}):
{section_text}
"""


def _decimal_or_none(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        d = Decimal(str(value))
        return d if d >= 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _coerce_str(value: Any, max_len: int) -> Optional[str]:
    if not value:
        return None
    return str(value)[:max_len].strip() or None


class PolicyRuleExtractor:
    """Extracts candidate policy rules from document chunks via Gemini."""

    def __init__(self, model: str = "gemini-2.5-flash") -> None:
        self._model = model

    # --- public interface ------------------------------------------------

    def extract(
        self,
        chunks: Sequence[KnowledgeChunk],
        *,
        document_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> list[CandidatePolicyRule]:
        """Return unsaved ``CandidatePolicyRule`` instances for every rule found.

        Raises ``ValueError`` if Gemini is not configured.
        """
        from app.services.gemini_service import get_gemini_client
        client = get_gemini_client()
        if client is None:
            raise ValueError(
                "Gemini is not configured. Set GEMINI_API_KEY to enable rule extraction."
            )

        candidates: list[CandidatePolicyRule] = []
        for section_name, section_chunks in self._group_by_section(chunks):
            section_text = "\n\n".join(c.content for c in section_chunks)
            if len(section_text.strip()) < _MIN_SECTION_CHARS:
                continue

            first_chunk = section_chunks[0]
            raw_rules = self._call_gemini(client, section_name, section_text)

            for raw in raw_rules:
                candidate = self._build_candidate(
                    raw,
                    document_id=document_id,
                    run_id=run_id,
                    source_chunk_id=first_chunk.id,
                    source_page_number=first_chunk.page_number,
                )
                if candidate is not None:
                    candidates.append(candidate)

        logger.info(
            "ai.extraction.completed",
            extra={
                "documentId": str(document_id),
                "runId": str(run_id),
                "candidatesFound": len(candidates),
            },
        )
        return candidates

    # --- internals -------------------------------------------------------

    @staticmethod
    def _group_by_section(
        chunks: Sequence[KnowledgeChunk],
    ) -> list[tuple[str, list[KnowledgeChunk]]]:
        """Group chunks by section, preserving chunk_index order within each group."""
        sorted_chunks = sorted(chunks, key=lambda c: (c.section or "", c.chunk_index))
        result = []
        for section_key, group in groupby(sorted_chunks, key=lambda c: c.section or ""):
            result.append((section_key or "Document", list(group)))
        return result

    def _call_gemini(
        self,
        client: Any,
        section_name: str,
        section_text: str,
    ) -> list[dict]:
        from google import genai

        prompt = _PROMPT_TEMPLATE.format(
            section_name=section_name or "General",
            section_text=section_text[:12_000],  # guard against huge sections
        )
        try:
            response = client.models.generate_content(
                model=self._model,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    response_mime_type="application/json"
                ),
            )
            payload = json.loads(response.text or "{}")
            rules = payload.get("rules", [])
            if not isinstance(rules, list):
                return []
            return [r for r in rules if isinstance(r, dict)]
        except Exception as exc:
            logger.warning(
                "ai.extraction.section_failed",
                extra={"section": section_name, "error": str(exc)[:200]},
            )
            return []

    @staticmethod
    def _build_candidate(
        raw: dict,
        *,
        document_id: uuid.UUID,
        run_id: uuid.UUID,
        source_chunk_id: Optional[uuid.UUID],
        source_page_number: Optional[int],
    ) -> Optional[CandidatePolicyRule]:
        category = _coerce_str(raw.get("category"), 64)
        name = _coerce_str(raw.get("name"), 200)
        if not category or not name:
            return None

        expense_limit = _decimal_or_none(raw.get("expense_limit"))
        # If Gemini returned a prose limit, honour it; clear the numeric field.
        limit_expression = _coerce_str(raw.get("limit_expression"), 120)
        if expense_limit is None and not limit_expression:
            limit_expression = None

        special_rules = raw.get("special_rules")
        if not isinstance(special_rules, list):
            special_rules = []

        return CandidatePolicyRule(
            document_id=document_id,
            source_page_number=source_page_number,
            source_chunk_id=source_chunk_id,
            extracted_by=EXTRACTED_BY_GEMINI,
            extraction_run_id=run_id,
            name=name,
            category=category.lower(),
            description=_coerce_str(raw.get("description"), 2000),
            country=_coerce_str(raw.get("country"), 2),
            currency=(_coerce_str(raw.get("currency"), 3) or "USD").upper(),
            grade_tier=_coerce_str(raw.get("grade_tier"), 64),
            expense_limit=expense_limit,
            limit_expression=limit_expression,
            auto_approve_limit=_decimal_or_none(raw.get("auto_approve_limit")),
            receipt_required_above=_decimal_or_none(raw.get("receipt_required_above")),
            requires_pre_approval=bool(raw.get("requires_pre_approval", False)),
            priority=int(raw.get("priority") or 100),
            special_rules=special_rules or None,
            conditions=raw.get("conditions") if isinstance(raw.get("conditions"), dict) else None,
            actions=raw.get("actions") if isinstance(raw.get("actions"), dict) else None,
            status="PENDING",
        )
