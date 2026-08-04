"""Chunking strategy registry: selects one of the seven strategies by name.

Mirrors the provider-registry pattern already used by embeddings/vector stores/rerankers, but
simpler: every chunker maps 1:1 to a :class:`ChunkStrategy` member, so registration keys are just
the lowercase strategy value. There is no "preference list" the way embedding providers have — a
caller asking for HEADING chunking wants heading chunking, not silent degradation to another
strategy. The one strategy with a real availability concern is SEMANTIC, which needs a working
``EmbeddingProvider``; its factory resolves one lazily through
:func:`app.ai.providers.embeddings.resolve_embedding_provider`, so that provider's own fallback
(bge-m3 -> deterministic) still applies and registering SEMANTIC never requires model weights.
"""

from __future__ import annotations

from app.ai.chunking.heading import HeadingChunker
from app.ai.chunking.hybrid import HybridChunker
from app.ai.chunking.parent_child import ParentChildChunker
from app.ai.chunking.recursive import RecursiveChunker
from app.ai.chunking.semantic import SemanticChunker
from app.ai.chunking.sliding_window import SlidingWindowChunker
from app.ai.chunking.token import TokenChunker
from app.ai.core.enums import ChunkStrategy
from app.ai.interfaces.chunker import Chunker


def _build_semantic_chunker() -> SemanticChunker:
    from app.ai.providers.embeddings import resolve_embedding_provider

    return SemanticChunker(embedding_provider=resolve_embedding_provider())


def register_chunkers() -> None:
    """Register every chunking strategy. Called once by the composition root.

    Every factory is lazy and dependency-free except SEMANTIC's, which only reaches for an
    embedding provider when actually resolved.
    """
    from app.ai.registry.registry import chunking_registry as reg

    reg.register(
        ChunkStrategy.RECURSIVE.value.lower(), RecursiveChunker,
        protocol=Chunker, description="Paragraph -> sentence -> word cascade. The default.",
        replace=True,
    )
    reg.register(
        ChunkStrategy.TOKEN.value.lower(), TokenChunker,
        protocol=Chunker, description="Pure word-level windows, no prose structure awareness.",
        replace=True,
    )
    reg.register(
        ChunkStrategy.SLIDING_WINDOW.value.lower(), SlidingWindowChunker,
        protocol=Chunker, description="Fixed-stride overlapping windows, maximum recall.",
        replace=True,
    )
    reg.register(
        ChunkStrategy.HEADING.value.lower(), HeadingChunker,
        protocol=Chunker, description="One chunk per section; structure is authoritative.",
        replace=True,
    )
    reg.register(
        ChunkStrategy.HYBRID.value.lower(), HybridChunker,
        protocol=Chunker, description="Heading-aware boundaries, content-aware packing within.",
        replace=True,
    )
    reg.register(
        ChunkStrategy.PARENT_CHILD.value.lower(), ParentChildChunker,
        protocol=Chunker, description="Wide-context parents, precisely embeddable children.",
        replace=True,
    )
    reg.register(
        ChunkStrategy.SEMANTIC.value.lower(), _build_semantic_chunker,
        protocol=Chunker, description="Groups adjacent sentences by embedding similarity.",
        metadata={"requiresEmbeddingProvider": True},
        replace=True,
    )


def resolve_chunker(strategy: ChunkStrategy) -> Chunker:
    """The chunker implementing ``strategy``, registering the set on first use."""
    from app.ai.registry.registry import chunking_registry as reg

    if not reg.names():
        register_chunkers()
    return reg.resolve(strategy.value.lower())


__all__ = ["register_chunkers", "resolve_chunker"]
