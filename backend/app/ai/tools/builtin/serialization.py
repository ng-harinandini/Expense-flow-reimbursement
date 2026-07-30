"""Shared JSON-serialization of a :class:`~app.ai.core.types.ContextBundle` for a tool's output.

Every built-in tool returns this same shape, so one schema (see ``schemas.py``) validates all of
them, and a caller does not need to know which specific retrieval method produced the context.
"""

from __future__ import annotations

from typing import Any

from app.ai.core.types import ContextBundle


def context_bundle_to_dict(bundle: ContextBundle) -> dict[str, Any]:
    return {
        "text": bundle.text,
        "tokenCount": bundle.token_count,
        "chunkCount": bundle.chunk_count,
        "truncated": bundle.truncated,
        "citations": [
            {
                "documentId": str(citation.document_id),
                "documentTitle": citation.document_title,
                "documentVersion": citation.document_version,
                "sourceType": (
                    citation.source_type.value if citation.source_type is not None else None
                ),
                "section": citation.section,
                "effectiveDate": (
                    citation.effective_date.isoformat()
                    if citation.effective_date is not None else None
                ),
            }
            for citation in bundle.citations
        ],
    }


__all__ = ["context_bundle_to_dict"]
