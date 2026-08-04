"""Document-level scoping — the half of filtering the vector store DSL deliberately refuses.

``app/ai/vector_store/filters.py`` filters *chunks*: it has no notion of a document's lifecycle
status or a query's as-of date, because those aren't chunk columns and a vector store adapter has no
join to ``knowledge_documents`` to express them with. That module's own docstring says so explicitly
and points here.

Two document-level concerns, two different mechanisms, because they compose differently with each
leg of hybrid retrieval:

**Supersession** (excluding a superseded document by default) is expressed by widening the chunk
filter list with a plain ``document_id nin [...]`` clause — a field and operator every store, local
or external, already understands. Both legs receive it identically, computed once here from
:meth:`KnowledgeDocumentRepository.superseded_ids`.

**Effective-dating** cannot be expressed the same way. The generic DSL's ``gte``/``lte`` follow
SQL's null-is-absent convention (a chunk with no ``effective_date`` fails ``effective_date <=
when``), but
:meth:`~app.ai.core.types.ChunkMetadata.is_effective_on` treats a missing bound as *open*, which is
the semantics the platform actually wants — a chunk with no declared window applies at every date.
The lexical leg gets this natively and correctly via ``lexical_search``'s own ``effective_on``
parameter, which is built on the same ``_effective_on`` SQL helper the document query itself uses.
The dense leg has no equivalent path into SQL, so it is filtered in Python, after the vector search
returns, using the exact same value-type method — which is what guarantees the two legs agree rather
than each re-implementing the null-handling rule and drifting.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Sequence

from app.ai.core.types import Chunk, MetadataFilter
from app.ai.repositories.knowledge_repository import KnowledgeDocumentRepository
from app.ai.vector_store.filters import FilterOp


@dataclass(frozen=True, slots=True)
class DocumentScope:
    """The document-level policy for one retrieval call, computed once and handed to both legs.

    A value object rather than passing ``tenant_id``/``include_superseded``/``effective_on`` down
    separately to each leg: the two legs must apply *exactly* the same policy, and a value object
    that is built once and threaded through is how that stays true even as the engine grows more
    call sites.
    """

    tenant_id: str
    effective_on: Optional[date] = None
    include_superseded: bool = False
    excluded_document_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)

    def as_chunk_filter(self) -> tuple[MetadataFilter, ...]:
        """The exclusion as a chunk-level filter every store — local or external — can apply.

        Empty when there is nothing to exclude, which matters: an empty ``nin`` list is refused by
        the filter DSL as a caller bug (see ``app/ai/vector_store/filters.py``), because on a
        genuinely fresh tenant with no superseded documents yet, there is nothing wrong with the
        query — there is just nothing to filter.
        """
        if not self.excluded_document_ids:
            return ()
        return (
            MetadataFilter(
                "document_id", FilterOp.NIN.value, tuple(sorted(self.excluded_document_ids))
            ),
        )

    def admits(self, chunk: Chunk) -> bool:
        """Whether a chunk already returned by a leg still belongs in the result.

        The dense leg's own DSL filter already excludes superseded documents (via
        :meth:`as_chunk_filter`, passed to ``VectorStore.search``), so this only re-checks
        effective-dating — the one policy no store-level filter can express. Cheap to call on every
        result: no query, just the value type's own method.
        """
        if self.effective_on is None:
            return True
        return chunk.metadata.is_effective_on(self.effective_on)


def build_document_scope(
    documents: KnowledgeDocumentRepository,
    *,
    tenant_id: str,
    effective_on: Optional[date] = None,
    include_superseded: bool = False,
) -> DocumentScope:
    """Compute the scope once per retrieval call.

    One query (``superseded_ids``) regardless of how many legs run or how many candidates come back
    — the alternative, checking each result's document status individually, would be one query per
    distinct document in the candidate set.
    """
    excluded = (
        frozenset()
        if include_superseded
        else documents.superseded_ids(tenant_id=tenant_id)
    )
    return DocumentScope(
        tenant_id=tenant_id,
        effective_on=effective_on,
        include_superseded=include_superseded,
        excluded_document_ids=excluded,
    )


def combined_chunk_filters(
    explicit: Sequence[MetadataFilter], scope: DocumentScope
) -> tuple[MetadataFilter, ...]:
    """The caller's own filters plus the document-scope exclusion, as one list for either leg."""
    return tuple(explicit) + scope.as_chunk_filter()


__all__ = [
    "DocumentScope",
    "build_document_scope",
    "combined_chunk_filters",
]
