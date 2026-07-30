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
from app.ai.models.governance import FlagOverride, RegistryEntry  # noqa: F401
from app.ai.models.knowledge import (  # noqa: F401
    DEFAULT_TENANT_ID,
    EMBEDDING_DIMENSIONS,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeEmbedding,
    KnowledgeIngestionRun,
    TenantMixin,
)
from app.ai.models.prompt import PromptTemplate  # noqa: F401

__all__ = [
    "DEFAULT_TENANT_ID",
    "EMBEDDING_DIMENSIONS",
    "ClaimFingerprint",
    "FlagOverride",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "KnowledgeEmbedding",
    "KnowledgeIngestionRun",
    "PromptTemplate",
    "RegistryEntry",
    "TenantMixin",
    "VendorAlias",
    "VendorProfile",
]
