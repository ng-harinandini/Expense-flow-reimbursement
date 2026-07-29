"""Vector arithmetic, in pure Python.

No numpy here on purpose. This module is imported by the *framework* (batching, caching, similarity
checks in the duplicate-detection engine), and the framework must work wherever the platform runs —
including a deployment that never installs a model runtime at all. Providers that already depend on
numpy do their own bulk maths; these functions handle the handful of small operations everything
else needs.

The dimensions here are 1024 and the call sites are single-vector comparisons, so the pure-Python
cost is negligible. If a hot loop ever needs thousands of comparisons, that belongs in the vector
store where the database does it in C.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

# Below this, a vector is treated as degenerate: normalizing it would amplify floating-point noise
# into a meaningless direction rather than producing a useful unit vector.
_MIN_NORM = 1e-12


def l2_norm(vector: Sequence[float]) -> float:
    """Euclidean length."""
    return math.sqrt(sum(float(v) * float(v) for v in vector))


def normalize(vector: Sequence[float]) -> tuple[float, ...]:
    """Scale to unit length.

    A zero (or near-zero) vector is returned unchanged rather than raising: an empty or
    whitespace-only chunk can legitimately embed to something degenerate, and failing the whole
    ingestion batch over one such chunk would be worse than storing it as-is. It simply never
    matches anything, which is the correct outcome.
    """
    norm = l2_norm(vector)
    if norm < _MIN_NORM:
        return tuple(float(v) for v in vector)
    return tuple(float(v) / norm for v in vector)


def is_normalized(vector: Sequence[float], *, tolerance: float = 1e-4) -> bool:
    """Whether the vector is already unit length, within ``tolerance``.

    Used by the provider contract suite: a provider that claims ``normalized=True`` must actually
    return unit vectors, or cosine and inner-product ranking silently diverge.
    """
    return abs(l2_norm(vector) - 1.0) <= tolerance


def dot(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError(
            f"Cannot take the dot product of {len(left)}- and {len(right)}-dimensional vectors."
        )
    return sum(float(a) * float(b) for a, b in zip(left, right, strict=True))


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity in ``[-1, 1]``.

    Computed from the raw vectors rather than assuming normalization, because this is also used on
    vectors that arrive from outside the platform (a client-supplied query vector, a fixture).
    Clamped, since accumulated floating-point error can push an identical pair marginally past 1.0
    and a caller comparing against a threshold of exactly 1.0 would then behave inconsistently.
    """
    if len(left) != len(right):
        raise ValueError(
            f"Cannot compare {len(left)}- and {len(right)}-dimensional vectors."
        )
    left_norm = l2_norm(left)
    right_norm = l2_norm(right)
    if left_norm < _MIN_NORM or right_norm < _MIN_NORM:
        return 0.0
    raw = dot(left, right) / (left_norm * right_norm)
    return max(-1.0, min(1.0, raw))


def cosine_distance(left: Sequence[float], right: Sequence[float]) -> float:
    """``1 - cosine_similarity``, matching pgvector's ``<=>`` operator."""
    return 1.0 - cosine_similarity(left, right)


def euclidean_distance(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError(
            f"Cannot compare {len(left)}- and {len(right)}-dimensional vectors."
        )
    return math.sqrt(
        sum((float(a) - float(b)) ** 2 for a, b in zip(left, right, strict=True))
    )


def similarity_from_distance(distance: float) -> float:
    """Map a cosine distance to a ``[0, 1]`` similarity score.

    Every vector-store adapter must return scores on this scale regardless of what its engine
    natively produces (see the ``VectorStore`` contract), so this is the shared conversion.

    ``1 - distance``, clamped — which is just the cosine similarity with negative values floored at
    zero. Two properties this has to have, and one it deliberately gives up:

    * **Monotone non-increasing in distance.** Adapters let the engine order by ascending distance
      and convert afterwards, so any conversion that could rank a *further* vector higher would
      reorder results relative to the ``ORDER BY`` that produced them. (An earlier version halved
      distances above 1.0 and was not monotone: distance 1.0 scored 0.0 while 1.5 scored 0.25.)
    * **Directly interpretable.** A score of 0.85 means a cosine similarity of 0.85, so a
      ``score_threshold`` means the same thing to a caller as it does to anyone reading the model's
      documentation. Rescaling ``[0, 2]`` onto ``[0, 1]`` would have made every threshold a number
      only this codebase can explain, and would put unrelated text at 0.5.
    * **Anti-correlated pairs all collapse to 0.** Cosine distances above 1.0 mean negative
      similarity: definitively not a match. Their relative order carries nothing a retrieval caller
      can use, and preserving it would cost the property above.

    Clamped at both ends because an approximate index can return a distance marginally outside the
    theoretical range.
    """
    return max(0.0, min(1.0, 1.0 - float(distance)))


def mean_vector(vectors: Sequence[Sequence[float]]) -> tuple[float, ...]:
    """Centroid of ``vectors``.

    Used by semantic chunking to represent a group of sentences, and by vendor intelligence to
    summarise a merchant's historical receipts.
    """
    if not vectors:
        raise ValueError("mean_vector requires at least one vector.")
    width = len(vectors[0])
    if any(len(v) != width for v in vectors):
        raise ValueError("All vectors must share the same dimensionality.")
    count = len(vectors)
    return tuple(sum(float(v[i]) for v in vectors) / count for i in range(width))


def top_k_by_similarity(
    query: Sequence[float],
    candidates: Iterable[tuple[str, Sequence[float]]],
    *,
    k: int = 10,
    threshold: float = 0.0,
) -> list[tuple[str, float]]:
    """Brute-force nearest neighbours, highest similarity first.

    Powers the in-memory vector store (the reference implementation the contract suite validates
    every real adapter against) and the deterministic offline path. The stable secondary sort on the
    id makes paging deterministic when scores tie — the ``VectorStore`` contract requires it.
    """
    scored: list[tuple[str, float]] = []
    for identifier, vector in candidates:
        score = cosine_similarity(query, vector)
        if score >= threshold:
            scored.append((identifier, score))
    scored.sort(key=lambda pair: (-pair[1], pair[0]))
    return scored[: max(0, k)]


__all__ = [
    "cosine_distance",
    "cosine_similarity",
    "dot",
    "euclidean_distance",
    "is_normalized",
    "l2_norm",
    "mean_vector",
    "normalize",
    "similarity_from_distance",
    "top_k_by_similarity",
]
