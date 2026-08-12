"""Pre-extraction claim document classification.

Classifies an uploaded receipt/document against the closed set of expense categories, using OCR
text plus (optionally) the document image, and — only when the AI's suggestion agrees with the
employee's own category selection at sufficient confidence — extracts that category's
``custom_fields``.

Deliberately separate from :mod:`app.ai.extraction` (policy-document rule extraction): different
input (a receipt, not a policy PDF), different output (a classification + optional field set, not a
candidate rule), and a different reviewer-facing contract. The two features share no code beyond the
generic :class:`~app.ai.providers.llm.bedrock.BedrockGemmaProvider` adapter.

The employee-selected ``ExpenseItem.category`` is never written by anything in this package — every
class here returns a plain, unsaved result object; :class:`app.services.claim_service.ClaimService`
is the only thing that assigns AI output onto an ``ExpenseItem`` column.
"""

from __future__ import annotations

from app.ai.classification.category_field_extractor import (
    CategoryFieldExtractor,
    FieldExtractionOutcome,
)
from app.ai.classification.document_classifier import (
    ClassificationOutcome,
    DocumentCategoryClassifier,
)
from app.ai.classification.document_render import (
    PreparedImages,
    is_pdf_render_available,
    prepare_images,
)
from app.ai.classification.service import DocumentClassificationService

__all__ = [
    "CategoryFieldExtractor",
    "ClassificationOutcome",
    "DocumentCategoryClassifier",
    "DocumentClassificationService",
    "FieldExtractionOutcome",
    "PreparedImages",
    "is_pdf_render_available",
    "prepare_images",
]
