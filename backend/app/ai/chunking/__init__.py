"""Chunking engine (Task 3): seven strategies behind one contract.

:func:`app.ai.chunking.factory.resolve_chunker` is the intended entry point — it looks up the
strategy named by a :class:`~app.ai.interfaces.chunker.ChunkingConfig` and returns a ready
:class:`~app.ai.interfaces.chunker.Chunker`. Strategy classes are exported here too, for callers
(tests, the ingestion pipeline) that construct one directly, e.g. to inject a real
``EmbeddingProvider`` into :class:`SemanticChunker` rather than going through the registry.
"""

from __future__ import annotations

from app.ai.chunking.config import build_chunking_config
from app.ai.chunking.factory import register_chunkers, resolve_chunker
from app.ai.chunking.heading import HeadingChunker
from app.ai.chunking.hybrid import HybridChunker
from app.ai.chunking.parent_child import ParentChildChunker
from app.ai.chunking.recursive import RecursiveChunker
from app.ai.chunking.semantic import SemanticChunker
from app.ai.chunking.sliding_window import SlidingWindowChunker
from app.ai.chunking.token import TokenChunker

__all__ = [
    "HeadingChunker",
    "HybridChunker",
    "ParentChildChunker",
    "RecursiveChunker",
    "SemanticChunker",
    "SlidingWindowChunker",
    "TokenChunker",
    "build_chunking_config",
    "register_chunkers",
    "resolve_chunker",
]
