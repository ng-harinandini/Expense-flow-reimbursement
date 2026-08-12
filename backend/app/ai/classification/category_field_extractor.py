"""Category-specific field extraction — the second, gated Bedrock call.

Only invoked once classification has confirmed the document matches the employee's selected
category with sufficient confidence (a mismatched or low-confidence document is never asked to
yield "confident-looking" field values for a category it may not even be). Pure: no database
access, no ``ExpenseItem`` import.

The field vocabulary this coerces against (``text``/``number``/``date``/``boolean``/``enum``)
matches ``app.services.category_service._validate_custom_fields`` exactly, since that function is
what validates the very schema this module is asked to fill in.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Sequence

from app.ai.classification.document_classifier import _loads_lenient
from app.services.receipt_extraction import parse_date

logger = logging.getLogger(__name__)

_NUMBER_TOKEN_RE = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")


@dataclass
class FieldExtractionOutcome:
    fields: dict[str, Any] = field(default_factory=dict)
    failed: bool = False
    notes: Optional[str] = None


def _decimal_or_none(value: Any) -> Optional[Decimal]:
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
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _coerce_by_type(value: Any, data_type: str, options: Optional[list]) -> Any:
    if value is None:
        return None
    if data_type == "text":
        text = str(value).strip()
        return text[:2000] if text else None
    if data_type == "number":
        decimal_value = _decimal_or_none(value)
        return float(decimal_value) if decimal_value is not None else None
    if data_type == "date":
        if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
            return value.strip()
        parsed = parse_date(str(value))
        return parsed.isoformat() if parsed else None
    if data_type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            return value.strip().lower() == "true"
        return None
    if data_type == "enum":
        text = str(value).strip()
        allowed = {str(o).strip().casefold(): str(o) for o in (options or [])}
        return allowed.get(text.casefold())
    return None


class CategoryFieldExtractor:
    """Extracts one category's ``custom_fields`` from a confirmed document."""

    def __init__(self, *, client: Any) -> None:
        self._client = client

    def extract(
        self,
        *,
        field_schema: list[dict],
        ocr_text: Optional[str],
        images: Optional[Sequence[dict[str, Any]]] = None,
    ) -> FieldExtractionOutcome:
        """``images`` is the same model-ready list ``DocumentCategoryClassifier.classify`` takes."""
        if not field_schema:
            return FieldExtractionOutcome()

        prompt = self._build_prompt(ocr_text, field_schema)
        images = list(images) if images else None

        try:
            raw = self._client.generate_text(prompt, images=images)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ai.classification.field_extraction_failed", extra={"error": str(exc)[:300]})
            return FieldExtractionOutcome(
                failed=True, notes=f"Category field extraction unavailable: {exc}"[:1000]
            )

        payload = _loads_lenient(raw or "")
        raw_fields = payload.get("fields") if isinstance(payload, dict) else None
        if not isinstance(raw_fields, dict):
            logger.warning("ai.classification.field_extraction_unparseable", extra={"length": len(raw or "")})
            return FieldExtractionOutcome(
                failed=True, notes="Category field extraction returned an unparseable response."
            )

        schema_by_name = {str(f.get("name")): f for f in field_schema if f.get("name")}
        notes: list[str] = []
        result: dict[str, Any] = {}
        for name, spec in schema_by_name.items():
            if name not in raw_fields:
                continue
            data_type = str(spec.get("data_type") or "text")
            coerced = _coerce_by_type(raw_fields[name], data_type, spec.get("options"))
            if coerced is None and raw_fields[name] not in (None, ""):
                notes.append(f"Value for '{name}' could not be interpreted as {data_type}; dropped.")
                continue
            if coerced is not None:
                result[name] = coerced

        return FieldExtractionOutcome(fields=result, failed=False, notes=" ".join(notes)[:2000] or None)

    @staticmethod
    def _build_prompt(ocr_text: Optional[str], field_schema: list[dict]) -> str:
        from app.ai.classification.prompts import FIELD_EXTRACTION_PROMPT_TEMPLATE

        lines = []
        for spec in field_schema:
            name = spec.get("name")
            if not name:
                continue
            parts = [f"- {name}", f"label={spec.get('label')}", f"data_type={spec.get('data_type') or 'text'}"]
            if spec.get("options"):
                parts.append(f"options={spec.get('options')}")
            if spec.get("required"):
                parts.append("required=true")
            lines.append(" ".join(parts))

        return (
            FIELD_EXTRACTION_PROMPT_TEMPLATE.replace("<<FIELD_SCHEMA>>", "\n".join(lines)).replace(
                "<<OCR_TEXT>>", ocr_text or "(no OCR text available)"
            )
        )


__all__ = ["CategoryFieldExtractor", "FieldExtractionOutcome"]
