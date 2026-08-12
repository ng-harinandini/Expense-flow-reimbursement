"""Provider-configurable, page-by-page extraction of candidate policy rules.

The output of this module is consumed by a human: a Finance reviewer approves, edits or
dismisses each candidate. That single fact drives every design decision here —

* **Nothing is silently dropped.** Long pages are split into overlapping windows rather than
  truncated, unparseable model output is logged rather than swallowed, and any clause that
  resists structuring is preserved verbatim in ``special_rules`` / ``source_text``.
* **Every candidate is self-contained.** It carries the exact source sentence, page and section,
  so the reviewer never has to reopen the original PDF.
* **Uncertainty is reported, not guessed.** The model is instructed to leave a field null and
  write a review note instead of inventing a number; the extractor adds its own completeness
  notes on top.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Optional, Sequence

from app.ai.core.config import ai_settings
from app.ai.core.errors import ProviderNotConfiguredError
from app.ai.extraction.prompts import get_prompt_template
from app.ai.models.knowledge import KnowledgeChunk
from app.ai.providers.llm.bedrock import BedrockGemmaProvider
from app.models.candidate_rule import (
    EXTRACTED_BY_BEDROCK,
    EXTRACTED_BY_GEMINI,
    CandidatePolicyRule,
)

logger = logging.getLogger(__name__)

# Kept deliberately closed: ``category`` drives the published rule's code and the expense
# category the policy engine matches on, so an invented category yields a rule that never fires.
# Anything outside this set becomes "other" and the natural wording goes into review_notes.
RULE_CATEGORIES = (
    "meals",
    "travel",
    "lodging",
    "entertainment",
    "flights",
    "ground_transport",
    "client_entertainment",
    "other",
)

# --- coercion helpers --------------------------------------------------------


_NUMBER_TOKEN_RE = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")


def _decimal_or_none(value: Any) -> Optional[Decimal]:
    """Parse a single stated amount; never glue together numbers scattered across prose.

    Models leak currency symbols and thousands separators into numeric fields ("$1,200.00",
    "USD 25 per day"), so a string value needs cleanup, not a bare cast. But stripping every
    non-digit character and concatenating what's left turns "up to 2 meals at $50 per meal" into
    250, or a reference clause that mentions an unrelated year or an old rate into a nonsense
    figure. Finding distinct number tokens and requiring exactly one avoids that while still
    accepting any legitimate multi-word amount ("USD 2,000 per employee per year"), which
    contains only one number.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        matches = _NUMBER_TOKEN_RE.findall(text)
        if len(matches) != 1:
            return None
        value = matches[0].replace(",", "")
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d >= 0 else None


def _coerce_str(value: Any, max_len: int) -> Optional[str]:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "n/a", "not stated", "not specified"}:
        return None
    return text[:max_len].strip() or None


def _coerce_str_list(value: Any, *, max_items: int = 60, max_len: int = 1000) -> list[str]:
    """Normalise a model-supplied array of prose into a clean, de-duplicated list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, dict):
            # Occasionally emitted as {"text": ...} / {"item": ...}; keep the payload.
            item = next((item[k] for k in ("text", "item", "value", "name") if k in item), None)
        text = _coerce_str(item, max_len)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= max_items:
            break
    return out


def _coerce_confidence(value: Any) -> Optional[Decimal]:
    """Clamp a confidence to [0, 1], accepting the percentages models sometimes emit."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip().rstrip("%")
    try:
        score = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if score > 1:
        score = score / Decimal(100)
    score = max(Decimal(0), min(Decimal(1), score))
    return score.quantize(Decimal("0.001"))


def _coerce_field_confidence(value: Any) -> Optional[dict]:
    if not isinstance(value, dict):
        return None
    out: dict[str, float] = {}
    for key, raw in value.items():
        score = _coerce_confidence(raw)
        if score is not None:
            out[str(key)[:64]] = float(score)
    return out or None


def _prune(mapping: Any) -> Optional[dict]:
    """Drop empty leaves so ``conditions``/``actions`` are absent rather than full of nulls."""
    if not isinstance(mapping, dict):
        return None
    out: dict[str, Any] = {}
    for key, value in mapping.items():
        if isinstance(value, dict):
            value = _prune(value)
        elif isinstance(value, (list, tuple)):
            value = [v for v in value if v not in (None, "", [], {})]
        if value in (None, "", [], {}):
            continue
        out[str(key)[:64]] = value
    return out or None


def _normalize_country(value: Any) -> Optional[str]:
    """Only accept a real alpha-2 code — truncating 'India' to 'IN' is luck, not parsing."""
    text = _coerce_str(value, 64)
    if not text:
        return None
    code = text.strip().upper()
    return code if re.fullmatch(r"[A-Z]{2}", code) else None


def _normalize_currency(value: Any) -> Optional[str]:
    """Only accept a real-looking ISO 4217 code — truncating '$'/'Dollars' to 3 chars is luck."""
    text = _coerce_str(value, 16)
    if not text:
        return None
    code = re.sub(r"[^A-Za-z]", "", text).upper()
    return code if re.fullmatch(r"[A-Z]{3}", code) else None


# --- extractor ---------------------------------------------------------------


class PolicyRuleExtractor:
    """Extracts candidate policy rules one page at a time."""

    def __init__(
        self,
        model: Optional[str] = None,
        *,
        provider: Optional[str] = None,
        client: Any = None,
    ) -> None:
        self._provider = (provider or ai_settings.RULE_EXTRACTION_PROVIDER).lower()
        self._model = model or ai_settings.RULE_EXTRACTION_MODEL
        self._client = client
        self._provenance = self._build_provenance()

    def _build_provenance(self) -> str:
        if self._provider == "gemini":
            return EXTRACTED_BY_GEMINI
        if self._model:
            normalized = re.sub(r"[^A-Z0-9]+", "-", self._model.upper()).strip("-")
            return f"{EXTRACTED_BY_BEDROCK}-{normalized}"[:64]
        return EXTRACTED_BY_BEDROCK

    def extract(
        self,
        chunks: Sequence[KnowledgeChunk],
        *,
        document_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> list[CandidatePolicyRule]:
        """Return unsaved candidates; provider failures propagate with HTTP-aware AI errors."""
        client = self._client
        if not self._model:
            raise ProviderNotConfiguredError(
                provider=self._provider,
                kind="RULE_EXTRACTION",
                remedy="Set AI_RULE_EXTRACTION_MODEL to the model ID to use.",
            )
        if self._provider == "gemini":
            if client is None:
                from app.services.gemini_service import get_gemini_client
                client = get_gemini_client()
            if client is None:
                raise ValueError(
                    "Gemini is not configured. Set GEMINI_API_KEY or choose Bedrock with "
                    "AI_RULE_EXTRACTION_PROVIDER=bedrock."
                )
        elif self._provider == "bedrock":
            if client is None:
                client = BedrockGemmaProvider(
                    model=self._model,
                    region=ai_settings.BEDROCK_REGION,
                    timeout_seconds=ai_settings.LLM_TIMEOUT_SECONDS,
                    max_output_tokens=ai_settings.RULE_EXTRACTION_MAX_OUTPUT_TOKENS,
                    temperature=ai_settings.LLM_TEMPERATURE,
                )
        else:
            raise ValueError(
                f"Unsupported rule extraction provider '{self._provider}'. Use 'bedrock' or 'gemini'."
            )

        candidates: list[CandidatePolicyRule] = []
        pages_failed = 0
        for page_name, page_chunks in self._group_by_page(chunks):
            page_text = "\n\n".join(c.content for c in page_chunks)
            if not page_text.strip():
                continue
            first_chunk = page_chunks[0]
            section_hint = self._section_hint(page_chunks)

            raw_rules: list[dict] = []
            windows = self._split_into_windows(page_text)
            for index, window in enumerate(windows):
                label = page_name if len(windows) == 1 else f"{page_name} (part {index + 1}/{len(windows)})"
                produced = self._call_provider(client, label, window, section_hint)
                if produced is None:
                    pages_failed += 1
                    continue
                raw_rules.extend(produced)

            for raw in self._dedupe(raw_rules):
                candidate = self._build_candidate(
                    raw,
                    document_id=document_id,
                    run_id=run_id,
                    source_chunk_id=first_chunk.id,
                    source_page_number=first_chunk.page_number,
                    section_hint=section_hint,
                    extracted_by=self._provenance,
                )
                if candidate is not None:
                    candidates.append(candidate)

        logger.info(
            "ai.extraction.completed",
            extra={
                "documentId": str(document_id),
                "runId": str(run_id),
                "candidatesFound": len(candidates),
                "windowsFailed": pages_failed,
                "candidatesNeedingReview": sum(1 for c in candidates if c.review_notes),
            },
        )
        return candidates

    # --- page segmentation ---------------------------------------------------

    @staticmethod
    def _group_by_page(
        chunks: Sequence[KnowledgeChunk],
    ) -> list[tuple[str, list[KnowledgeChunk]]]:
        """Group by page; unpaged documents are one virtual ``Document`` page."""
        groups: dict[Optional[int], list[KnowledgeChunk]] = {}
        ordered = sorted(
            chunks,
            key=lambda c: (c.page_number is None, c.page_number or 0, c.chunk_index),
        )
        for chunk in ordered:
            groups.setdefault(chunk.page_number, []).append(chunk)
        return [
            (f"Page {page}" if page is not None else "Document", page_chunks)
            for page, page_chunks in groups.items()
        ]

    _group_by_section = _group_by_page

    @staticmethod
    def _section_hint(page_chunks: Sequence[KnowledgeChunk]) -> Optional[str]:
        """Best available section label for this page, from the chunker's citation anchors.

        The model is asked for ``source_section`` too; this is the fallback when it returns
        nothing, and it also tells the model which section it is reading.
        """
        for chunk in page_chunks:
            section = _coerce_str(getattr(chunk, "section", None), 500)
            if section:
                return section
        for chunk in page_chunks:
            path = getattr(chunk, "heading_path", None)
            if isinstance(path, (list, tuple)) and path:
                joined = " > ".join(str(p).strip() for p in path if str(p).strip())
                if joined:
                    return joined[:500]
        return None

    @staticmethod
    def _split_into_windows(page_text: str) -> list[str]:
        """Split a page into overlapping windows on paragraph boundaries.

        The previous implementation truncated at 12k characters, silently discarding every rule
        past that point. Windows keep the whole page in play; the overlap means a rule that
        straddles a boundary is seen intact by at least one window, and ``_dedupe`` removes the
        duplicate it produces.
        """
        max_chars = max(1000, ai_settings.RULE_EXTRACTION_MAX_CHARS_PER_WINDOW)
        overlap = max(0, min(ai_settings.RULE_EXTRACTION_WINDOW_OVERLAP_CHARS, max_chars // 2))

        text = page_text.strip()
        if len(text) <= max_chars:
            return [text]

        # Hard-split any single paragraph that is itself larger than a window.
        paragraphs: list[str] = []
        for para in re.split(r"\n\s*\n", text):
            para = para.strip()
            if not para:
                continue
            while len(para) > max_chars:
                cut = para.rfind(" ", 0, max_chars)
                cut = cut if cut > max_chars // 2 else max_chars
                paragraphs.append(para[:cut].strip())
                para = para[cut:].strip()
            if para:
                paragraphs.append(para)

        windows: list[str] = []
        current: list[str] = []
        size = 0
        for para in paragraphs:
            if current and size + len(para) + 2 > max_chars:
                window = "\n\n".join(current)
                windows.append(window)
                tail = window[-overlap:] if overlap else ""
                current = [tail] if tail else []
                size = len(tail)
            current.append(para)
            size += len(para) + 2
        if current:
            windows.append("\n\n".join(current))

        logger.info(
            "ai.extraction.page_windowed",
            extra={"chars": len(text), "windows": len(windows), "maxChars": max_chars},
        )
        return windows

    # --- provider calls ------------------------------------------------------

    def _call_provider(
        self,
        client: Any,
        page_name: str,
        page_text: str,
        section_hint: Optional[str],
    ) -> Optional[list[dict]]:
        """Return parsed rules, or ``None`` when this window failed (so it can be counted)."""
        prompt = self._build_prompt(page_name, page_text, section_hint)
        if self._provider == "bedrock":
            generate = getattr(client, "generate_text", None) or getattr(client, "generate", None)
            if generate is None:
                raise TypeError("Bedrock provider must expose generate_text(prompt).")
            try:
                return self._parse_rules(generate(prompt), page_name=page_name)
            except Exception as exc:
                # Isolate the failure to this window: one bad page must not cost the document.
                logger.warning(
                    "ai.extraction.page_failed",
                    extra={"page": page_name, "provider": "bedrock", "error": str(exc)[:200]},
                )
                return None
        return self._call_gemini(client, page_name, page_text, section_hint)

    @staticmethod
    def _build_prompt(
        page_name: str,
        page_text: str,
        section_hint: Optional[str] = None,
    ) -> str:
        hint = f"(Section: {section_hint})\n" if section_hint else ""
        template = get_prompt_template(ai_settings.RULE_EXTRACTION_PROMPT_VERSION)
        return (
            template
            .replace("<<CATEGORIES>>", ", ".join(RULE_CATEGORIES))
            .replace("<<SECTION_NAME>>", page_name or "General")
            .replace("<<SECTION_HINT>>", hint)
            .replace("<<PAGE_TEXT>>", page_text)
        )

    def _call_gemini(
        self,
        client: Any,
        page_name: str,
        page_text: str,
        section_hint: Optional[str],
    ) -> Optional[list[dict]]:
        from google import genai

        try:
            response = client.models.generate_content(
                model=self._model,
                contents=self._build_prompt(page_name, page_text, section_hint),
                config=genai.types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=ai_settings.LLM_TEMPERATURE,
                    max_output_tokens=ai_settings.RULE_EXTRACTION_MAX_OUTPUT_TOKENS,
                ),
            )
            return self._parse_rules(response.text or "", page_name=page_name)
        except Exception as exc:
            logger.warning(
                "ai.extraction.page_failed",
                extra={"page": page_name, "provider": "gemini", "error": str(exc)[:200]},
            )
            return None

    # --- response parsing ----------------------------------------------------

    @staticmethod
    def _parse_rules(text: str, *, page_name: str = "") -> list[dict]:
        """Parse the model's JSON, tolerating fences and commentary, and log what it cannot."""
        raw = (text or "").strip()
        if not raw:
            logger.warning("ai.extraction.empty_response", extra={"page": page_name})
            return []

        payload = _loads_lenient(raw)
        if payload is None:
            logger.warning(
                "ai.extraction.unparseable_response",
                extra={"page": page_name, "length": len(raw), "head": raw[:300]},
            )
            return []

        rules = payload.get("rules") if isinstance(payload, dict) else payload
        if isinstance(payload, dict) and not isinstance(rules, list):
            # Some models return a bare rule object instead of the wrapper.
            rules = [payload] if payload.get("name") else None
        if not isinstance(rules, list):
            logger.warning(
                "ai.extraction.missing_rules_key",
                extra={"page": page_name, "keys": list(payload)[:10] if isinstance(payload, dict) else None},
            )
            return []
        return [rule for rule in rules if isinstance(rule, dict)]

    @staticmethod
    def _dedupe(raw_rules: Iterable[dict]) -> list[dict]:
        """Collapse the duplicates that window overlap intentionally produces.

        Keeps the richer copy: overlap can show the model a fragment in one window and the whole
        clause in the next, and the whole clause is the one worth reviewing.
        """
        best: dict[tuple[str, str], dict] = {}
        order: list[tuple[str, str]] = []
        for raw in raw_rules:
            name = str(raw.get("name") or "").strip().casefold()
            anchor = str(raw.get("source_text") or raw.get("description") or "").strip().casefold()
            anchor = re.sub(r"\s+", " ", anchor)[:160]
            if not name and not anchor:
                continue
            key = (name, anchor)
            if key not in best:
                best[key] = raw
                order.append(key)
            elif len(json.dumps(raw, default=str)) > len(json.dumps(best[key], default=str)):
                best[key] = raw
        return [best[key] for key in order]

    # --- candidate construction ----------------------------------------------

    @staticmethod
    def _build_candidate(
        raw: dict,
        *,
        document_id: uuid.UUID,
        run_id: uuid.UUID,
        source_chunk_id: Optional[uuid.UUID],
        source_page_number: Optional[int],
        section_hint: Optional[str] = None,
        extracted_by: str = EXTRACTED_BY_BEDROCK,
    ) -> Optional[CandidatePolicyRule]:
        category = _coerce_str(raw.get("category"), 64)
        name = _coerce_str(raw.get("name"), 200)
        if not category or not name:
            logger.warning(
                "ai.extraction.rule_discarded",
                extra={"reason": "missing name or category", "raw": json.dumps(raw, default=str)[:300]},
            )
            return None

        notes: list[str] = []

        proposed_category = category
        category = category.lower().replace(" ", "_").replace("-", "_")
        if category not in RULE_CATEGORIES:
            notes.append(
                f"Model proposed category '{proposed_category}', stored as 'other' — set the correct category."
            )
            category = "other"

        expense_limit = _decimal_or_none(raw.get("expense_limit"))
        limit_expression = _coerce_str(raw.get("limit_expression"), 500)

        source_text = _coerce_str(raw.get("source_text"), 8000)
        description = _coerce_str(raw.get("description"), 2000)
        if not source_text:
            # Requirement: every candidate carries its citation. Falling back to the description
            # keeps the clause rather than losing it, but the reviewer must be told it is not
            # verbatim.
            source_text = description
            notes.append("No verbatim source sentence was returned — the text shown is the model's paraphrase; verify against the document.")

        special_rules = _coerce_str_list(raw.get("special_rules"))
        exclusions = _coerce_str_list(raw.get("exclusions"))
        required_documents = _coerce_str_list(raw.get("required_documents"))

        country = _normalize_country(raw.get("country"))
        if raw.get("country") and country is None:
            notes.append(f"Country '{raw.get('country')}' was not a valid ISO 3166-1 alpha-2 code and was dropped.")

        currency = _normalize_currency(raw.get("currency"))
        if raw.get("currency") and currency is None:
            notes.append(f"Currency '{raw.get('currency')}' was not a valid ISO 4217 code and was dropped.")
        elif currency is None:
            notes.append(
                "No currency was explicitly stated for this rule — left unset; confirm the "
                "settlement currency before approving (amounts may use a generic symbol like "
                "'$' without naming one)."
            )

        try:
            priority = int(raw.get("priority") or 100)
        except (TypeError, ValueError):
            priority = 100

        conditions = _prune(raw.get("conditions"))
        actions = _prune(raw.get("actions"))
        receipt_required_above = _decimal_or_none(raw.get("receipt_required_above"))
        if receipt_required_above is None and isinstance(actions, dict):
            # Mirror the action into the typed column the policy engine reads.
            receipt_required_above = _decimal_or_none(actions.get("receipt_required_above"))

        model_note = _coerce_str(raw.get("review_notes"), 4000)
        if model_note:
            notes.insert(0, model_note)

        # --- completeness validation (nothing leaves without usable metadata) ---
        if expense_limit is None and not limit_expression:
            hint = source_text or description or ""
            if re.search(r"(?:up to|maximum|max\.?|not exceed|limit(?:ed)? to|capped)", hint, re.I):
                notes.append("The source text reads like it states a limit, but none was extracted — check for a missing amount.")
        if not special_rules and not exclusions:
            hint = source_text or description or ""
            if re.search(r"\b(?:except|exception|however|provided that|unless|subject to|notwithstanding)\b", hint, re.I):
                notes.append("The source text contains a qualifier (except/unless/provided that) that was not captured as a special rule — review the original clause.")
        if not conditions and (raw.get("grade_tier") or country):
            notes.append("Applicability was stated on the rule itself but no structured conditions were returned — confirm who this rule applies to.")

        overall_confidence = _coerce_confidence(raw.get("overall_confidence"))
        if overall_confidence is None:
            notes.append("The model returned no confidence score for this rule.")

        return CandidatePolicyRule(
            document_id=document_id,
            source_page_number=source_page_number,
            source_chunk_id=source_chunk_id,
            extracted_by=extracted_by,
            extraction_run_id=run_id,
            name=name,
            category=category,
            description=description,
            country=country,
            currency=currency,
            grade_tier=_coerce_str(raw.get("grade_tier"), 64),
            expense_limit=expense_limit,
            limit_expression=limit_expression,
            auto_approve_limit=_decimal_or_none(raw.get("auto_approve_limit")),
            receipt_required_above=receipt_required_above,
            requires_pre_approval=bool(raw.get("requires_pre_approval", False)),
            priority=priority,
            special_rules=special_rules or None,
            conditions=conditions,
            actions=actions,
            source_text=source_text,
            source_section=_coerce_str(raw.get("source_section"), 500) or section_hint,
            exclusions=exclusions or None,
            required_documents=required_documents or None,
            field_confidence=_coerce_field_confidence(raw.get("field_confidence")),
            overall_confidence=overall_confidence,
            review_notes=" ".join(notes)[:8000] or None,
            status="PENDING",
        )


def _loads_lenient(raw: str) -> Optional[Any]:
    """``json.loads`` that survives markdown fences, prose preambles and trailing commentary."""
    candidates = [raw]

    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", raw, re.S | re.I)
    if fenced:
        candidates.append(fenced.group(1))

    # Widest brace span — handles "Here are the rules: {...} Hope that helps."
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        candidates.append(raw[start : end + 1])

    for text in candidates:
        text = text.strip()
        if not text:
            continue
        try:
            return json.loads(text)
        except (TypeError, ValueError):
            pass
        # Trailing commas are the single most common malformation from small models.
        repaired = re.sub(r",\s*([}\]])", r"\1", text)
        try:
            return json.loads(repaired)
        except (TypeError, ValueError):
            continue
    return None


__all__ = ["PolicyRuleExtractor", "RULE_CATEGORIES"]
