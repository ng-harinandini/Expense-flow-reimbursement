"""Repositories for the AI platform.

All SQL for the AI tables lives here, per the T003 contract: repositories accept a ``Session``,
never commit, and every read is tenant-scoped.
"""

from app.ai.repositories.knowledge_repository import (
    EmbeddingWrite,
    KnowledgeChunkRepository,
    KnowledgeDocumentRepository,
    KnowledgeEmbeddingRepository,
    KnowledgeIngestionRunRepository,
)

__all__ = [
    "EmbeddingWrite",
    "KnowledgeChunkRepository",
    "KnowledgeDocumentRepository",
    "KnowledgeEmbeddingRepository",
    "KnowledgeIngestionRunRepository",
]
