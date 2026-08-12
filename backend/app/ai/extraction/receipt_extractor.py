"""Receipt extraction via one multimodal Bedrock call — the alternative to AWS Textract.

Selected by ``AI_RECEIPT_EXTRACTION_PROVIDER=bedrock``. Where Textract's ``AnalyzeExpense`` can only
transcribe a document, this reads it *and* decides which expense category it belongs to, in a single
call, so ``POST /expense-items/upload`` can hand the submission form a category to pre-select. That
is the whole reason this path exists: Textract has no notion of an expense category, so before this
the form's category field could never be filled in ahead of submission.

**It emits the Textract dict shape verbatim** (see ``_adapt``). That is deliberate and load-bearing:
``summarize_extraction``, ``normalize_confidence`` and every persistence path downstream read the
Textract shape, and none of them had to change to accommodate this engine. The category verdict
rides *alongside* that shape at the top level, outside ``summary`` — so it reaches the upload
response but is never persisted as ``ocr_extracted_json``.

The category list and every category's field schema are read from the database on each call, never
baked into the prompt file, so an admin adding or renaming a category reaches the model with no
deploy.

This is not the authoritative classification. ``ClaimService._classify_item_category`` re-runs
classification at submission against the category the employee actually chose, and that verdict is
the one the policy and fraud engines route on. This one only pre-fills a form.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from app.ai.classification import document_render
from app.ai.classification.category_field_extractor import _coerce_by_type
from app.ai.classification.document_classifier import _loads_lenient
from app.ai.core.config import ai_settings
from app.ai.extraction.receipt_prompts import RECEIPT_EXTRACTION_PROMPT_TEMPLATE
from app.ai.providers.llm.bedrock import BedrockGemmaProvider
from app.models.enums import AIInferenceStatus
from app.repositories.ai_inference_repository import AIInferenceRepository
from app.repositories.category_repository import CategoryRepository
from app.services.receipt_extraction import empty_extraction

logger = logging.getLogger(__name__)

#: ``expense_items.ocr_source`` value on success. Only the literal ``"fallback"`` carries behaviour
#: downstream (it suppresses OCR text at classification time), so a distinct name here is free and
#: makes the ledger and any support question answerable from the column alone.
SOURCE_BEDROCK = "bedrock"
SOURCE_FALLBACK = "fallback"

#: Emitted into ``summary.fields`` so the shape matches Textract's. The frontend reads ``TAX`` off
#: this list by name to pre-fill the tax input, and ``normalize_confidence`` maps these exact
#: ``fieldType`` values onto its own aliases.
_SUMMARY_FIELD_TYPES = (
    ("VENDOR_NAME", "Vendor", "vendorName"),
    ("INVOICE_RECEIPT_DATE", "Date", "transactionDate"),
    ("SUBTOTAL", "Subtotal", "subtotalAmount"),
    ("TAX", "Tax", "taxAmount"),
    ("TOTAL", "Total", "totalAmount"),
)


def _fallback(file_name: str, reason: str) -> dict[str, Any]:
    """The same empty-but-well-formed result Textract returns when it is disabled.

    Strict by design: a Bedrock failure never silently reaches for Textract. A deployment that
    switched providers should be able to see that it switched, and a hidden cross-provider fallback
    would make a misconfigured model look like it was working.
    """
    logger.warning("receipt.bedrock_extraction_degraded", extra={"reason": reason[:300]})
    return empty_extraction(file_name, reason)


def _num(value: Any) -> Optional[float]:
    """Reuses the submission-time coercion so ``"$1,234.56"`` and ``1234.56`` land identically, and
    so a value carrying two numbers is dropped rather than half-read."""
    return _coerce_by_type(value, "number", None)


def _iso_date(value: Any) -> Optional[str]:
    """``YYYY-MM-DD`` or ``None`` — same coercion the category fields get."""
    return _coerce_by_type(value, "date", None)


def _text(value: Any, limit: int) -> Optional[str]:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text[:limit] if text else None


class BedrockReceiptExtractor:
    """Reads a receipt and classifies it in one call. Never raises; degrades to ``_fallback``."""

    def __init__(
        self,
        category_repository: CategoryRepository,
        ai_inference_repository: AIInferenceRepository,
        *,
        client: Any = None,
    ) -> None:
        self._categories = category_repository
        self._ai_inference = ai_inference_repository
        self._injected_client = client

    def _client(self) -> Any:
        if self._injected_client is not None:
            return self._injected_client
        # ``kind``/``model_setting_name`` are what make a provider outage name the setting an
        # operator actually has to fix, rather than rule extraction's.
        return BedrockGemmaProvider(
            model=ai_settings.RECEIPT_EXTRACTION_MODEL,
            region=ai_settings.BEDROCK_REGION,
            timeout_seconds=ai_settings.RECEIPT_EXTRACTION_TIMEOUT_SECONDS,
            max_output_tokens=ai_settings.RECEIPT_EXTRACTION_MAX_OUTPUT_TOKENS,
            temperature=ai_settings.LLM_TEMPERATURE,
            kind="RECEIPT_EXTRACTION",
            model_setting_name="AI_RECEIPT_EXTRACTION_MODEL",
        )

    # -- the entry point -----------------------------------------------------

    def extract(
        self,
        *,
        data: Optional[bytes],
        mime_type: Optional[str] = None,
        file_name: str = "receipt",
        s3_bucket: Optional[str] = None,
        s3_key: Optional[str] = None,
    ) -> dict[str, Any]:
        """``s3_bucket``/``s3_key`` are accepted to match the Textract adapter's signature and are
        deliberately unused: the caller still holds the bytes, so this path never re-fetches what it
        was just handed.
        """
        if not data:
            return _fallback(file_name, "No document bytes were provided.")

        # A vision model cannot read a PDF; document_render rasterizes it, sniffs the real type from
        # magic bytes rather than trusting the multipart header, and never raises.
        prepared = document_render.prepare_images(
            data,
            mime_type,
            max_pages=ai_settings.CLASSIFICATION_MAX_PDF_PAGES,
            scale=ai_settings.CLASSIFICATION_PDF_RENDER_SCALE,
        )
        if not prepared.images:
            reason = "Receipt could not be turned into an image for extraction."
            return _fallback(file_name, f"{reason} {prepared.note}" if prepared.note else reason)

        categories = list(self._categories.list_all(include_inactive=False))
        common = next((c for c in categories if c.is_common), None)
        # The COMMON row holds the fields every invoice carries; it is a field bucket, never a
        # category an employee can pick, so it must not appear in the candidate list.
        candidates = [c for c in categories if not c.is_common]
        if not candidates:
            return _fallback(file_name, "No active expense categories are configured.")

        prompt = self._build_prompt(common, candidates)

        try:
            raw = self._client().generate_text(prompt, images=prepared.images)
        except Exception as exc:  # noqa: BLE001 - any provider failure degrades, never propagates
            self._record(status=AIInferenceStatus.FAILED, error=str(exc)[:1000], prepared=prepared)
            return _fallback(file_name, f"Receipt extraction unavailable: {exc}")

        payload = _loads_lenient(raw or "")
        if not isinstance(payload, dict):
            self._record(
                status=AIInferenceStatus.FAILED,
                error="Receipt extraction returned an unparseable response.",
                prepared=prepared,
            )
            return _fallback(file_name, "Receipt extraction returned an unparseable response.")

        result = self._adapt(payload, candidates=candidates, common=common, prepared=prepared)
        self._record(
            status=AIInferenceStatus.SUCCESS,
            prepared=prepared,
            output={
                "suggestedCategory": result.get("suggestedCategory"),
                "confidence": result.get("categoryConfidence"),
                "hasTotal": result["summary"]["totalAmount"] is not None,
                "categoryFieldCount": len(result.get("categoryFields") or {}),
            },
        )
        return result

    # -- prompt --------------------------------------------------------------

    @staticmethod
    def _field_lines(custom_fields: Optional[Sequence[dict]], indent: str = "") -> list[str]:
        """Renders a category's ``custom_fields`` the way ``CategoryFieldExtractor`` already does.

        The JSONB objects are snake_case (``data_type``) even though the categories API serves them
        under a camelCase envelope — read them as stored.
        """
        lines: list[str] = []
        for spec in custom_fields or []:
            name = spec.get("name")
            if not name:
                continue
            parts = [
                f"{indent}- {name}",
                f"label={spec.get('label') or name}",
                f"data_type={spec.get('data_type') or 'text'}",
            ]
            if spec.get("options"):
                parts.append(f"options={spec.get('options')}")
            if spec.get("required"):
                parts.append("required=true")
            lines.append(" ".join(parts))
        return lines

    def _build_prompt(self, common: Any, candidates: Sequence[Any]) -> str:
        common_lines = self._field_lines(common.custom_fields if common else None)

        category_lines: list[str] = []
        for category in candidates:
            category_lines.append(f"* {category.name}")
            own = self._field_lines(category.custom_fields, indent="  ")
            category_lines.extend(own or ["    (no category-specific fields)"])

        return RECEIPT_EXTRACTION_PROMPT_TEMPLATE.replace(
            "<<COMMON_FIELDS>>", "\n".join(common_lines) or "(none configured)"
        ).replace("<<CATEGORY_SCHEMA>>", "\n".join(category_lines))

    # -- response -> the Textract shape --------------------------------------

    def _adapt(
        self,
        payload: dict[str, Any],
        *,
        candidates: Sequence[Any],
        common: Any,
        prepared: document_render.PreparedImages,
    ) -> dict[str, Any]:
        vendor = _text(payload.get("vendorName"), 200)
        currency = _text(payload.get("currency"), 3)
        total = _num(payload.get("totalAmount"))
        subtotal = _num(payload.get("subtotalAmount"))
        tax = _num(payload.get("taxAmount"))

        transaction_date = _iso_date(payload.get("transactionDate"))

        values = {
            "vendorName": vendor,
            "transactionDate": transaction_date,
            "subtotalAmount": subtotal,
            "taxAmount": tax,
            "totalAmount": total,
        }
        # Textract reports a per-field confidence; a single-call model reports one confidence for
        # the whole document. Leaving these null rather than inventing per-field numbers keeps
        # normalize_confidence honest — it falls through to the top-level confidenceScore below.
        fields = [
            {
                "fieldType": field_type,
                "fieldLabel": label,
                "fieldValue": str(values[key]),
                "confidence": None,
            }
            for field_type, label, key in _SUMMARY_FIELD_TYPES
            if values[key] is not None
        ]

        confidence = _num(payload.get("confidence"))
        if confidence is not None:
            confidence = max(0.0, min(1.0, confidence))

        category = self._resolve_category(payload.get("suggestedCategory"), candidates)
        category_fields = self._coerce_category_fields(
            payload.get("categoryFields"), category=category, candidates=candidates, common=common
        )

        return {
            "source": SOURCE_BEDROCK,
            # Provenance under Textract's key so any reader of ocr_extracted_json's sibling data
            # finds the same thing in the same place. The verbatim model payload is kept because it
            # is the only record of what was actually said if a field looks wrong later.
            "rawTextract": {
                "_provider": SOURCE_BEDROCK,
                "_model": ai_settings.RECEIPT_EXTRACTION_MODEL,
                "_pagesRendered": prepared.pages_rendered,
                "_response": payload,
            },
            "summary": {
                "vendorName": vendor,
                "transactionDate": transaction_date,
                "totalAmount": total,
                "currency": currency.upper() if currency else None,
                "fields": fields,
            },
            "lineItems": self._line_items(payload.get("lineItems")),
            "error": None,
            #: 0-1. normalize_confidence rescales this to {"overall": 0-100} — the documented
            #: escape hatch for an extractor that has no per-field confidences.
            "confidenceScore": confidence,
            # --- read by the upload route, then discarded; deliberately outside "summary" so none
            # --- of it is persisted as ocr_extracted_json. Submission re-derives its own verdict.
            "documentType": _text(payload.get("documentType"), 64),
            "suggestedCategory": category,
            "categoryConfidence": confidence,
            "categoryFields": category_fields or None,
        }

    @staticmethod
    def _resolve_category(raw: Any, candidates: Sequence[Any]) -> Optional[str]:
        """Maps the model's answer onto a real category name, or nulls it.

        A category the model invented is never returned — the form would offer the employee an
        option that does not exist, and the submitted claim would carry a category no policy
        ruleset has anything to say about.
        """
        name = _text(raw, 200)
        if not name:
            return None
        lookup = {c.name.strip().casefold(): c.name for c in candidates}
        canonical = lookup.get(name.strip().casefold())
        if canonical is None:
            logger.info("receipt.bedrock_category_rejected", extra={"proposed": name[:120]})
        return canonical

    def _coerce_category_fields(
        self,
        raw: Any,
        *,
        category: Optional[str],
        candidates: Sequence[Any],
        common: Any,
    ) -> dict[str, Any]:
        """Keeps only fields declared by the COMMON bucket or the chosen category, type-coerced.

        Anything the model invented, or emitted for a category it did not choose, is dropped rather
        than guessed at — the same rule ``CategoryFieldExtractor`` applies at submission time.
        """
        if not isinstance(raw, dict):
            return {}

        schema: list[dict] = []
        if common and common.custom_fields:
            schema.extend(common.custom_fields)
        if category:
            matched = next(
                (c for c in candidates if c.name.strip().casefold() == category.strip().casefold()),
                None,
            )
            if matched and matched.custom_fields:
                schema.extend(matched.custom_fields)

        result: dict[str, Any] = {}
        for spec in schema:
            name = spec.get("name")
            if not name or name not in raw:
                continue
            coerced = _coerce_by_type(
                raw[name], str(spec.get("data_type") or "text"), spec.get("options")
            )
            if coerced is not None:
                result[str(name)] = coerced
        return result

    @staticmethod
    def _line_items(raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        items: list[dict[str, Any]] = []
        for index, row in enumerate(raw, start=1):
            if not isinstance(row, dict):
                continue
            description = _text(row.get("description"), 500)
            amount = _num(row.get("amount"))
            if description is None and amount is None:
                continue
            items.append(
                {
                    "lineNumber": index,
                    "description": description,
                    "quantity": _num(row.get("quantity")),
                    "unitPrice": _num(row.get("unitPrice")),
                    "amount": amount,
                    "raw": row,
                }
            )
        return items

    # -- ledger --------------------------------------------------------------

    def _record(
        self,
        *,
        status: AIInferenceStatus,
        prepared: document_render.PreparedImages,
        output: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        """One ledger row per call. ``claim_id``/``expense_item_id`` are null because no claim and
        no item exist yet at upload — both columns are nullable for exactly this case.
        """
        try:
            self._ai_inference.record(
                provider="bedrock",
                model=ai_settings.RECEIPT_EXTRACTION_MODEL or "unknown",
                operation="receipt_extraction",
                status=status,
                claim_id=None,
                expense_item_id=None,
                input_summary={
                    "imageCount": len(prepared.images),
                    "sourceMimeType": prepared.source_mime_type,
                    "pdfConverted": bool(prepared.was_pdf and prepared.images),
                },
                output_summary=output,
                error_message=error,
            )
        except Exception:  # noqa: BLE001 - the ledger is observability, never the deliverable
            logger.exception("receipt.bedrock_ledger_write_failed")


__all__ = ["BedrockReceiptExtractor", "SOURCE_BEDROCK", "SOURCE_FALLBACK"]
