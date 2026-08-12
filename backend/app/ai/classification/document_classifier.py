"""Document -> expense-category classification.

Pure: no database access, no ``ExpenseItem`` import. Given OCR text and/or an image plus the closed
list of valid category names, returns a plain, unsaved :class:`ClassificationOutcome`. The caller
(``DocumentClassificationService``) supplies the Bedrock-shaped ``client`` and persists the result.

Mirrors the proven shape of ``app.ai.extraction.policy_rule_extractor.PolicyRuleExtractor``
(injectable client, closed-vocabulary-with-rejection, lenient JSON parsing, never raises on a
malformed or missing model response) without importing anything from that module — the two
features are kept independent per the "separate from policy-document rule extraction" requirement.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)


@dataclass
class ClassificationOutcome:
    """Unsaved classification verdict for one document."""

    document_type: Optional[str] = None
    #: Restricted to the caller-supplied ``valid_categories`` — never an invented value.
    suggested_category: Optional[str] = None
    confidence: Optional[Decimal] = None
    category_mismatch: bool = False
    #: True whenever a human should look at this before trusting it: a mismatch, low confidence,
    #: an invalid category from the model, or an outright provider failure.
    needs_review: bool = False
    notes: Optional[str] = None
    failed: bool = False


def _coerce_str(value: Any, max_len: int) -> Optional[str]:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "n/a"}:
        return None
    return text[:max_len].strip() or None


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


def _loads_lenient(raw: str) -> Optional[Any]:
    """``json.loads`` that survives markdown fences, prose preambles and trailing commentary."""
    candidates = [raw]

    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", raw, re.S | re.I)
    if fenced:
        candidates.append(fenced.group(1))

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
        repaired = re.sub(r",\s*([}\]])", r"\1", text)
        try:
            return json.loads(repaired)
        except (TypeError, ValueError):
            continue
    return None


class DocumentCategoryClassifier:
    """Classifies one document against a closed list of expense categories."""

    def __init__(self, *, client: Any) -> None:
        self._client = client

    def classify(
        self,
        *,
        ocr_text: Optional[str],
        images: Optional[Sequence[dict[str, Any]]] = None,
        valid_categories: Sequence[str],
        employee_category: str,
        min_confidence: float,
    ) -> ClassificationOutcome:
        """``images`` arrives already model-ready from :mod:`app.ai.classification.document_render`.

        Taking the list rather than one ``(bytes, mime_type)`` pair is what lets a PDF contribute
        more than one rendered page, and keeps the decision about *what* is a valid image part in the
        one module that knows how to make one.
        """
        prompt = self._build_prompt(ocr_text, valid_categories)
        images = list(images) if images else None

        try:
            raw = self._client.generate_text(prompt, images=images)
        except Exception as exc:  # noqa: BLE001 - any provider failure degrades to a review flag
            logger.warning("ai.classification.provider_failed", extra={"error": str(exc)[:300]})
            return ClassificationOutcome(
                failed=True,
                needs_review=True,
                notes=f"AI classification unavailable: {exc}"[:1000],
            )

        payload = _loads_lenient(raw or "")
        if not isinstance(payload, dict):
            logger.warning(
                "ai.classification.unparseable_response", extra={"length": len(raw or "")}
            )
            return ClassificationOutcome(
                failed=True,
                needs_review=True,
                notes="AI classification returned an unparseable response.",
            )

        document_type = _coerce_str(payload.get("documentType"), 64)
        raw_category = _coerce_str(payload.get("suggestedCategory"), 200)
        confidence = _coerce_confidence(payload.get("confidence"))
        reasoning = _coerce_str(payload.get("reasoning"), 1000)

        valid_lookup = {c.strip().casefold(): c for c in valid_categories}
        notes: list[str] = []
        suggested_category: Optional[str] = None
        category_mismatch = False

        if raw_category is None:
            notes.append("Model did not return a suggested category.")
            category_mismatch = True
        else:
            canonical = valid_lookup.get(raw_category.strip().casefold())
            if canonical is None:
                notes.append(
                    f"Model proposed category '{raw_category}', which is not a recognized "
                    "expense category."
                )
                category_mismatch = True
            else:
                suggested_category = canonical
                category_mismatch = (
                    canonical.strip().casefold() != employee_category.strip().casefold()
                )

        if confidence is None:
            notes.append("Model returned no confidence score.")

        needs_review = (
            category_mismatch or confidence is None or confidence < Decimal(str(min_confidence))
        )

        if reasoning:
            notes.append(reasoning)

        return ClassificationOutcome(
            document_type=document_type,
            suggested_category=suggested_category,
            confidence=confidence,
            category_mismatch=category_mismatch,
            needs_review=needs_review,
            notes=" ".join(notes)[:2000] or None,
            failed=False,
        )

    @staticmethod
    def _build_prompt(ocr_text: Optional[str], valid_categories: Sequence[str]) -> str:
        from app.ai.classification.prompts import CLASSIFICATION_PROMPT_TEMPLATE

        return (
            CLASSIFICATION_PROMPT_TEMPLATE.replace(
                "<<CATEGORIES>>", ", ".join(valid_categories)
            ).replace("<<OCR_TEXT>>", ocr_text or "(no OCR text available)")
        )


__all__ = ["ClassificationOutcome", "DocumentCategoryClassifier"]
