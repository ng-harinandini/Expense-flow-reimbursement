"""Orchestrates classification + category-field extraction for one expense item.

The only class in this package imported outside it (from ``app.core.deps`` only). ``ClaimService``
never imports this module directly — it depends on a local ``Protocol`` with an all-primitive
signature, the same boundary already used for ``decision_memory``/``duplicate_detection`` (see
``app.services.claim_service``), so ``app.core``/``app.services`` never needs to know this package
exists beyond the dependency-injection wiring in ``deps.py``.

This class resolves the candidate category list, builds the Bedrock client from settings, and logs
every model call through the pre-built ``AIInferenceRepository`` ledger. It never touches an
``ExpenseItem`` ORM object or the database beyond reading categories — ``ClaimService`` assigns the
returned dict onto the item's columns, mirroring how ``evaluate_expense_policy``/
``screen_for_anomalies`` hand back plain dicts today.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from app.ai.classification import document_render
from app.ai.classification.category_field_extractor import CategoryFieldExtractor
from app.ai.classification.document_classifier import DocumentCategoryClassifier
from app.ai.core.config import ai_settings
from app.ai.providers.llm.bedrock import BedrockGemmaProvider
from app.models.enums import AIInferenceStatus
from app.repositories.ai_inference_repository import AIInferenceRepository
from app.repositories.category_repository import CategoryRepository

logger = logging.getLogger(__name__)

#: Below this many characters of flattened OCR text, treat it the same as "no usable text" —
#: near-empty Textract output is no more trustworthy to classify from than the fallback stub.
_MIN_USABLE_TEXT_LENGTH = 10


def _empty_result(*, needs_review: bool = False, notes: Optional[str] = None) -> dict[str, Any]:
    return {
        "documentType": None,
        "suggestedCategory": None,
        "confidence": None,
        "categoryMismatch": False,
        "categoryReviewRequired": needs_review,
        "extractedFields": None,
        "notes": notes,
    }


class DocumentClassificationService:
    def __init__(
        self,
        category_repository: CategoryRepository,
        ai_inference_repository: AIInferenceRepository,
    ) -> None:
        self._categories = category_repository
        self._ai_inference = ai_inference_repository

    def _client(self) -> BedrockGemmaProvider:
        return BedrockGemmaProvider(
            model=ai_settings.CLASSIFICATION_MODEL,
            region=ai_settings.BEDROCK_REGION,
            timeout_seconds=ai_settings.CLASSIFICATION_TIMEOUT_SECONDS,
            max_output_tokens=ai_settings.CLASSIFICATION_MAX_OUTPUT_TOKENS,
            temperature=ai_settings.LLM_TEMPERATURE,
            kind="CLASSIFICATION",
            model_setting_name="AI_CLASSIFICATION_MODEL",
        )

    def classify_and_extract(
        self,
        *,
        ocr_text: Optional[str],
        ocr_is_fallback: bool,
        has_receipt: bool,
        image_bytes: Optional[bytes],
        image_mime_type: Optional[str],
        employee_category: str,
        claim_id: Optional[uuid.UUID],
        expense_item_id: Optional[uuid.UUID],
        actor_sub: Optional[str],
    ) -> dict[str, Any]:
        # Fabricated fallback text is worse than no text at all — never send it to the model.
        usable_text = None if ocr_is_fallback else (ocr_text or None)
        if usable_text is not None and len(usable_text.strip()) < _MIN_USABLE_TEXT_LENGTH:
            usable_text = None

        # A receipt is not necessarily an image: PDFs have to be rasterized before any vision model
        # can read them, and an unreadable upload must cost us the image only, never the whole
        # verdict. `prepared.note` carries the reason onto the item so a reviewer can see why a
        # classification was made from text alone.
        images: list[dict[str, Any]] = []
        prepared: Optional[document_render.PreparedImages] = None
        if image_bytes and ai_settings.CLASSIFICATION_INCLUDE_IMAGE:
            prepared = document_render.prepare_images(
                image_bytes,
                image_mime_type,
                max_pages=ai_settings.CLASSIFICATION_MAX_PDF_PAGES,
                scale=ai_settings.CLASSIFICATION_PDF_RENDER_SCALE,
            )
            images = prepared.images
            self._log_image_prepared(
                prepared,
                declared_mime_type=image_mime_type,
                original_bytes=len(image_bytes),
                claim_id=claim_id,
                expense_item_id=expense_item_id,
            )
        use_image = bool(images)

        if usable_text is None and not use_image:
            if not has_receipt:
                # Nothing was ever uploaded — a paper receipt is legitimate; don't punish it.
                return _empty_result()
            reason = "Receipt could not be read for classification (no usable OCR text or image)."
            if prepared is not None and prepared.note:
                reason = f"{reason} {prepared.note}"
            return _empty_result(needs_review=True, notes=reason)

        all_categories = self._categories.list_all(include_inactive=False)
        candidate_names = [c.name for c in all_categories if not c.is_common]
        if not candidate_names:
            return _empty_result(
                needs_review=True,
                notes="No active expense categories are configured for classification.",
            )

        classifier = DocumentCategoryClassifier(client=self._client())
        outcome = classifier.classify(
            ocr_text=usable_text,
            images=images,
            valid_categories=candidate_names,
            employee_category=employee_category,
            min_confidence=ai_settings.CLASSIFICATION_MIN_CONFIDENCE,
        )

        self._ai_inference.record(
            provider="bedrock",
            model=ai_settings.CLASSIFICATION_MODEL or "unknown",
            operation="category_classification",
            status=AIInferenceStatus.FAILED if outcome.failed else AIInferenceStatus.SUCCESS,
            claim_id=claim_id,
            expense_item_id=expense_item_id,
            input_summary={
                "employeeCategory": employee_category,
                "hasImage": use_image,
                "imageCount": len(images),
                "sourceMimeType": prepared.source_mime_type if prepared else None,
                "pdfConverted": bool(prepared and prepared.was_pdf and prepared.images),
            },
            output_summary={
                "suggestedCategory": outcome.suggested_category,
                "confidence": float(outcome.confidence) if outcome.confidence is not None else None,
                "categoryMismatch": outcome.category_mismatch,
            },
            error_message=outcome.notes if outcome.failed else None,
            actor_sub=actor_sub,
        )

        extracted_fields: Optional[dict] = None
        if not outcome.failed and outcome.suggested_category and not outcome.needs_review:
            field_schema = self._field_schema_for(all_categories, outcome.suggested_category)
            if field_schema:
                extractor = CategoryFieldExtractor(client=self._client())
                field_outcome = extractor.extract(
                    field_schema=field_schema,
                    ocr_text=usable_text,
                    images=images,
                )
                self._ai_inference.record(
                    provider="bedrock",
                    model=ai_settings.CLASSIFICATION_MODEL or "unknown",
                    operation="category_field_extraction",
                    status=AIInferenceStatus.FAILED if field_outcome.failed else AIInferenceStatus.SUCCESS,
                    claim_id=claim_id,
                    expense_item_id=expense_item_id,
                    input_summary={"category": outcome.suggested_category, "fieldCount": len(field_schema)},
                    output_summary={"fieldsPopulated": len(field_outcome.fields)},
                    error_message=field_outcome.notes if field_outcome.failed else None,
                    actor_sub=actor_sub,
                )
                if not field_outcome.failed:
                    extracted_fields = field_outcome.fields or None

        return {
            "documentType": outcome.document_type,
            "suggestedCategory": outcome.suggested_category,
            "confidence": float(outcome.confidence) if outcome.confidence is not None else None,
            "categoryMismatch": outcome.category_mismatch,
            "categoryReviewRequired": outcome.needs_review,
            "extractedFields": extracted_fields,
            "notes": self._combine_notes(outcome.notes, prepared),
        }

    @staticmethod
    def _combine_notes(
        notes: Optional[str], prepared: Optional[document_render.PreparedImages]
    ) -> Optional[str]:
        """Carry a rendering caveat onto the item alongside the model's own reasoning.

        Only when it actually changed what the model saw — a clean single-page conversion is the
        expected path and needs no annotation.
        """
        if prepared is None or not prepared.note:
            return notes
        return f"{notes} {prepared.note}".strip()[:2000] if notes else prepared.note[:2000]

    def _log_image_prepared(
        self,
        prepared: document_render.PreparedImages,
        *,
        declared_mime_type: Optional[str],
        original_bytes: int,
        claim_id: Optional[uuid.UUID],
        expense_item_id: Optional[uuid.UUID],
    ) -> None:
        """Types, counts and sizes only — never any part of the document itself."""
        context = {
            "claimId": str(claim_id) if claim_id else None,
            "expenseItemId": str(expense_item_id) if expense_item_id else None,
            "declaredMimeType": declared_mime_type,
            "sourceMimeType": prepared.source_mime_type,
            "originalBytes": original_bytes,
            "pdfConverted": prepared.was_pdf and bool(prepared.images),
            "pdfPageCount": prepared.pdf_page_count,
            "pagesRendered": prepared.pages_rendered,
            "imageCount": len(prepared.images),
            "imageMimeType": prepared.image_mime_type,
            "imageBytes": prepared.total_bytes,
            "pdfRenderAvailable": document_render.is_pdf_render_available(),
        }
        if prepared.images:
            logger.info("ai.classification.image_prepared", extra=context)
        else:
            # Not an error: classification continues on OCR text. Logged at WARNING because a
            # persistent stream of these means receipts are silently losing their strongest signal.
            logger.warning(
                "ai.classification.image_unusable",
                extra={**context, "reason": prepared.note},
            )

    @staticmethod
    def _field_schema_for(all_categories, category_name: str) -> list[dict]:
        """The category's own fields plus the shared ``COMMON`` bucket's fields, combined.

        The ``COMMON`` row exists specifically to hold fields every invoice carries regardless of
        category (see ``app.models.category``) — omitting it here would silently never extract
        those shared fields.
        """
        common = next((c for c in all_categories if c.is_common), None)
        matched = next(
            (c for c in all_categories if c.name.strip().casefold() == category_name.strip().casefold()),
            None,
        )
        schema: list[dict] = []
        if common and common.custom_fields:
            schema.extend(common.custom_fields)
        if matched and matched.custom_fields:
            schema.extend(matched.custom_fields)
        return schema


__all__ = ["DocumentClassificationService"]
