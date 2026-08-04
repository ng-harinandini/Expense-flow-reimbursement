"""Deterministic hashing embedder — the offline fallback, never the recommended default.

**This provider has no semantic understanding.** It projects character n-grams into a fixed-width
space by hashing. Two paraphrases of the same policy get unrelated vectors. It exists for exactly
three jobs:

1. **CI and offline development.** The suite must be green on a machine with no credentials and
   without the 2.3 GB bge-m3 weights. Every mechanism *around* the model — batching, caching,
   version stamping, cost accounting, telemetry, storage, tenant isolation, the vector-store
   contract — is fully exercised by it.
2. **A second adapter for the contract suite.** A contract test that only ever runs against one
   implementation proves nothing about interchangeability.
3. **Graceful degradation.** When the configured provider is unusable, the platform degrades to this
   rather than failing, and says so loudly at startup and in API response metadata.

It is *not* a stand-in for real embeddings, and the platform must never let it be mistaken for one:
:attr:`is_semantic` is ``False``, and every path that surfaces retrieval results reports the spec
key that produced them.

Determinism comes from BLAKE2b, not Python's ``hash()``, which is per-process randomized and would
make vectors differ between restarts — corrupting an index across a redeploy.
"""

from __future__ import annotations

import hashlib
import math
from typing import Sequence

from app.ai.core.config import BGE_M3_DIMENSIONS
from app.ai.core.types import (
    EmbeddingResult,
    EmbeddingSpec,
    EmbeddingUsage,
    EmbeddingVector,
)
from app.ai.core.text import estimate_tokens, normalize_for_matching
from app.ai.embeddings.math import normalize

PROVIDER_NAME = "deterministic"

# 3 and 4 together: 3-grams catch short tokens and abbreviations, 4-grams add enough specificity
# that unrelated short texts do not collide into near-identical vectors.
_NGRAM_SIZES = (3, 4)


class DeterministicEmbeddingProvider:
    """Hash-based embeddings. Implements :class:`app.ai.interfaces.embeddings.EmbeddingProvider`."""

    name = PROVIDER_NAME

    # Declared so no caller can mistake this for a real model. The knowledge service copies it into
    # response metadata, and a startup warning is emitted when this provider is the active one.
    is_semantic = False

    def __init__(
        self,
        *,
        dimensions: int = BGE_M3_DIMENSIONS,
        version: str = "v1",
        model_name: str = "hash-ngram",
    ) -> None:
        if dimensions < 8:
            raise ValueError("Deterministic embeddings need at least 8 dimensions.")
        self._spec = EmbeddingSpec(
            provider=PROVIDER_NAME,
            model=model_name,
            dimensions=dimensions,
            version=version,
            normalized=True,
            max_input_tokens=10_000_000,   # no model limit: it is pure arithmetic
            cost_per_million_tokens_usd=0.0,
        )

    @property
    def spec(self) -> EmbeddingSpec:
        return self._spec

    def is_available(self) -> bool:
        """Always. Having no dependencies is the entire point of this provider."""
        return True

    # --- embedding ----------------------------------------------------------

    def _vector_for(self, text: str) -> tuple[float, ...]:
        """Hash character n-grams into buckets, then L2-normalize.

        Signed contributions (the sign taken from a separate bit of the digest) rather than a plain
        count: with counts only, every vector sits in the positive orthant and cosine similarity
        between *any* two texts is high, which would make retrieval look like it works while ranking
        essentially at random.
        """
        dimensions = self._spec.dimensions
        buckets = [0.0] * dimensions
        cleaned = normalize_for_matching(text)
        if not cleaned:
            return tuple(buckets)

        padded = f" {cleaned} "
        for size in _NGRAM_SIZES:
            if len(padded) < size:
                continue
            for start in range(len(padded) - size + 1):
                gram = padded[start : start + size]
                digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
                index = int.from_bytes(digest[:4], "big") % dimensions
                sign = 1.0 if digest[4] & 1 else -1.0
                buckets[index] += sign

        # Sublinear scaling, as in tf-idf: a term repeated 50 times should not dominate a vector 50x
        # more than one appearing once.
        scaled = [math.copysign(math.log1p(abs(v)), v) for v in buckets]
        return normalize(scaled)

    def embed_documents(self, texts: Sequence[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=(), spec=self._spec, usage=EmbeddingUsage())

        vectors = tuple(
            EmbeddingVector(
                values=self._vector_for(text),
                spec_key=self._spec.key,
                dimensions=self._spec.dimensions,
            )
            for text in texts
        )
        usage = EmbeddingUsage(
            texts=len(texts),
            tokens=sum(estimate_tokens(t) for t in texts),
            latency_ms=0,
            cost_usd=0.0,
            provider=PROVIDER_NAME,
            model=self._spec.model,
        )
        return EmbeddingResult(vectors=vectors, spec=self._spec, usage=usage)

    def embed_query(self, text: str) -> EmbeddingVector:
        """Symmetric: hashing has no notion of a query/passage asymmetry."""
        return EmbeddingVector(
            values=self._vector_for(text),
            spec_key=self._spec.key,
            dimensions=self._spec.dimensions,
        )


__all__ = ["PROVIDER_NAME", "DeterministicEmbeddingProvider"]
