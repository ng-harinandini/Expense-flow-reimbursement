"""Populates the citation every retrieved chunk carries.

M7 deliberately left ``RetrievedChunk.citation`` ``None`` — resolving it needs one document lookup
per result to get the title/version a chunk's own metadata does not carry, and M7's Done Check never
asked for it. This is that lookup, run once per unique document in a result set rather than once per
chunk (several chunks routinely share a document).
"""

from __future__ import annotations

from dataclasses import replace

from app.ai.core.enums import KnowledgeSourceType
from app.ai.core.types import Citation, RetrievalResult, RetrievedChunk
from app.ai.models.knowledge import KnowledgeDocument
from app.ai.repositories.knowledge_repository import KnowledgeDocumentRepository


def attach_citations(
    result: RetrievalResult, *, document_repo: KnowledgeDocumentRepository, tenant_id: str
) -> RetrievalResult:
    """Return ``result`` with every chunk's ``citation`` resolved from its source document."""
    if not result.chunks:
        return result

    document_ids = {
        c.chunk.metadata.document_id for c in result.chunks if c.chunk.metadata.document_id
    }
    documents = {
        document.id: document
        for document in (
            document_repo.get_scoped(document_id, tenant_id=tenant_id)
            for document_id in document_ids
        )
        if document is not None
    }
    if not documents:
        return result

    cited_chunks = tuple(
        replace(rc, citation=_citation_for(rc, documents[rc.chunk.metadata.document_id]))
        if rc.chunk.metadata.document_id in documents
        else rc
        for rc in result.chunks
    )
    return replace(result, chunks=cited_chunks)


def _citation_for(chunk: RetrievedChunk, document: KnowledgeDocument) -> Citation:
    metadata = chunk.chunk.metadata
    return Citation(
        document_id=document.id,
        document_title=document.title,
        document_version=document.version,
        source_type=KnowledgeSourceType(document.source_type) if document.source_type else None,
        section=metadata.section,
        page_number=metadata.page_number,
        effective_date=document.effective_date,
        source_uri=document.source_uri,
    )


__all__ = ["attach_citations"]
