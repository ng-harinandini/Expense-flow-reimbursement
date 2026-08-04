"""Vendor intelligence retrieval: what the platform already knows about one vendor.

A thin retrieval-query builder, not the duplicate-detection/vendor-matching engine itself
(``app.ai.duplicate_detection``, built on top of the same vendor identity M9/M10 resolve). Scoped
to the two source types a vendor's own indexed knowledge would actually carry — a signed contract
or a service manual — rather than every document type.
"""

from __future__ import annotations

from app.ai.core.enums import KnowledgeSourceType
from app.ai.core.types import MetadataFilter, RetrievalQuery

VENDOR_SOURCE_TYPES = (KnowledgeSourceType.VENDOR_CONTRACT, KnowledgeSourceType.VENDOR_MANUAL)


def vendor_context_query(
    vendor_name: str, *, tenant_id: str = "default", top_k: int = 5
) -> RetrievalQuery:
    """A retrieval query scoped to indexed vendor contracts and manuals matching ``vendor_name``."""
    return RetrievalQuery(
        text=vendor_name,
        tenant_id=tenant_id,
        top_k=top_k,
        filters=(
            MetadataFilter(
                field_name="source_type", op="in",
                value=[source_type.value for source_type in VENDOR_SOURCE_TYPES],
            ),
        ),
    )


__all__ = ["VENDOR_SOURCE_TYPES", "vendor_context_query"]
