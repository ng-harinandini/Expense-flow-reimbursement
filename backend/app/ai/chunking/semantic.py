"""Semantic strategy: group adjacent sentences by embedding similarity.

The only strategy that needs a model, injected by constructor rather than looked up globally so it
stays unit-testable with a deterministic stand-in (contract preamble in
``app/ai/interfaces/chunker.py``). Groups grow while each new sentence stays within
``config.semantic_threshold`` cosine similarity of the running group's centroid *and* the group
stays within ``config.max_tokens``; a similarity or budget miss closes the group and starts a new
one. No inter-group overlap: unlike a token window's arbitrary cut point, a semantic boundary is
already the meaningful one.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.ai.chunking.base import BaseChunker, RawChunk, recursive_pack
from app.ai.core.enums import ChunkStrategy
from app.ai.core.text import split_sentences
from app.ai.core.types import ParsedDocument
from app.ai.embeddings.math import cosine_similarity, mean_vector
from app.ai.interfaces.chunker import ChunkingConfig
from app.ai.interfaces.embeddings import EmbeddingProvider, TokenCounter


class SemanticChunker(BaseChunker):
    strategy = ChunkStrategy.SEMANTIC

    def __init__(
        self, *, embedding_provider: EmbeddingProvider,
        token_counter: TokenCounter | None = None,
    ) -> None:
        super().__init__(token_counter=token_counter)
        self._embedding_provider = embedding_provider

    def _split(self, document: ParsedDocument, config: ChunkingConfig) -> list[RawChunk]:
        sentences = split_sentences(document.text)
        if not sentences:
            return []
        if len(sentences) == 1:
            return [RawChunk(text=sentences[0], index=0)]

        result = self._embedding_provider.embed_documents(sentences)
        vectors = [v.values for v in result.vectors]

        groups: list[list[int]] = [[0]]
        centroids: list[list[Sequence[float]]] = [[vectors[0]]]
        for i in range(1, len(sentences)):
            candidate_text = " ".join(sentences[j] for j in groups[-1]) + " " + sentences[i]
            similarity = cosine_similarity(vectors[i], mean_vector(centroids[-1]))
            fits_budget = self._count(candidate_text) <= config.max_tokens
            if similarity >= config.semantic_threshold and fits_budget:
                groups[-1].append(i)
                centroids[-1].append(vectors[i])
            else:
                groups.append([i])
                centroids.append([vectors[i]])

        raw: list[RawChunk] = []
        index = 0
        for group in groups:
            text = " ".join(sentences[j] for j in group)
            pieces = (
                [text] if self._count(text) <= config.max_tokens
                else recursive_pack(
                    text, max_tokens=config.max_tokens, overlap_tokens=config.overlap_tokens,
                    count_tokens=self._count,
                )
            )
            for piece in pieces:
                if not piece.strip():
                    continue
                raw.append(RawChunk(text=piece, index=index))
                index += 1
        return raw


__all__ = ["SemanticChunker"]
