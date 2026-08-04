"""Embedding provider contract.

Every provider in Task 4 — Titan, Voyage, OpenAI, Cohere, BGE, E5, Instructor — implements this one
protocol, which is what allows the model to be swapped by configuration. The behavioural clauses
below are not advice: ``tests/test_ai_contracts.py`` asserts each of them against every registered
adapter, so an adapter that violates one fails the build rather than degrading retrieval quietly.

Contract:

1. ``embed_documents(texts)`` returns exactly ``len(texts)`` vectors, in the same order.
2. Every vector has ``spec.dimensions`` components.
3. Determinism: the same text and spec produce byte-identical values within a process.
4. If ``spec.normalized`` is true, every vector is unit length (L2 norm 1.0 ± 1e-4).
5. ``embed_query`` may differ from ``embed_documents`` for one text (asymmetric models prepend an
   instruction prefix) but must return a vector comparable to indexed document vectors.
6. Empty input returns an empty result and performs no I/O.
7. An unavailable provider raises ``ProviderNotConfiguredError``, never a bare exception.
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from app.ai.core.types import EmbeddingResult, EmbeddingSpec, EmbeddingVector


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Turns text into vectors. The only component permitted to know a model's name."""

    name: str

    @property
    def spec(self) -> EmbeddingSpec:
        """Immutable identity of this provider's model — stored beside every vector it produces."""
        ...

    def is_available(self) -> bool:
        """Whether this provider can serve a request right now.

        Must be cheap and must not raise: the registry calls it to decide whether to fall back, so
        an exception here would defeat the graceful-degradation path it exists to enable.
        """
        ...

    def embed_documents(self, texts: Sequence[str]) -> EmbeddingResult:
        """Embed texts for **indexing**. Order-preserving, one vector per input."""
        ...

    def embed_query(self, text: str) -> EmbeddingVector:
        """Embed one text for **searching**.

        Separate from ``embed_documents`` because asymmetric models (E5, Instructor, and bge-m3 when
        used with instructions) require a different prefix for queries than for passages. Providers
        with symmetric embeddings simply delegate.
        """
        ...


@runtime_checkable
class TokenCounter(Protocol):
    """Counts tokens the way a specific model does.

    Injected into chunkers so token budgets are exact rather than estimated. The heuristic in
    :func:`app.ai.core.text.estimate_tokens` implements this protocol as the no-tokenizer default;
    the bge-m3 provider exposes its real SentencePiece tokenizer through it.
    """

    def count(self, text: str) -> int:
        ...


__all__ = ["EmbeddingProvider", "TokenCounter"]
