"""Assembles a budget-bounded, citable :class:`ContextBundle` from one :class:`RetrievalResult`.

Only formats what the engine already produced — chunk ordering, the token budget, and the
``truncated`` flag all come from :class:`~app.ai.retrieval.engine.HybridRetrievalEngine` itself
(see :mod:`app.ai.retrieval.budget`); this module's only job is turning that into the flat, citable
text a reviewer or a model reads.
"""

from __future__ import annotations

from app.ai.core.types import ContextBundle, RetrievalResult


def build_context(result: RetrievalResult) -> ContextBundle:
    """Concatenate ``result``'s chunks, each tagged with a citation marker, in rank order."""
    if not result.chunks:
        return ContextBundle(
            text="",
            citations=(),
            token_count=0,
            chunk_count=0,
            truncated=result.truncated,
            embedding_version=result.embedding_version,
            config_fingerprint=result.config_fingerprint,
            retrieval=result,
        )

    text = "\n\n".join(f"[{index + 1}] {chunk.text}" for index, chunk in enumerate(result.chunks))
    token_count = sum(chunk.chunk.token_count for chunk in result.chunks)
    return ContextBundle(
        text=text,
        citations=result.citations,
        token_count=token_count,
        chunk_count=len(result.chunks),
        truncated=result.truncated,
        embedding_version=result.embedding_version,
        config_fingerprint=result.config_fingerprint,
        retrieval=result,
    )


__all__ = ["build_context"]
