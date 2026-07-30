"""ORM models for the AI platform.

Importing this module registers every AI table on ``Base.metadata``. ``alembic/env.py`` must import
it, or autogenerate will propose dropping these tables — the same rule ``app/models/__init__.py``
already documents for the domain models.
"""

from app.ai.models.duplicate_detection import (  # noqa: F401
    ClaimFingerprint,
    VendorAlias,
    VendorProfile,
)
from app.ai.models.knowledge import (  # noqa: F401
    DEFAULT_TENANT_ID,
    EMBEDDING_DIMENSIONS,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeEmbedding,
    KnowledgeIngestionRun,
    TenantMixin,
)

__all__ = [
    "DEFAULT_TENANT_ID",
    "EMBEDDING_DIMENSIONS",
    "ClaimFingerprint",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "KnowledgeEmbedding",
    "KnowledgeIngestionRun",
    "TenantMixin",
    "VendorAlias",
    "VendorProfile",
]
