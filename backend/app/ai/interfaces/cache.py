"""Cache contract (Task 15).

One protocol serves the embedding, retrieval, prompt, context and model caches. They differ only in
key prefix and TTL, so a second abstraction would add nothing.

Contract:

1. **A cache miss is never an error.** ``get`` returns ``None``. A cache backend that is down must
   degrade to no-caching, never fail the request it was meant to accelerate — this is the whole
   reason caching is behind an interface rather than inlined.
2. ``set`` is best-effort: a failed write is logged and swallowed.
3. Keys are opaque strings built by :mod:`app.ai.core.ids`, which already scope them by tenant and
   by model version. A cache must never widen that scope.
4. Values must be JSON-serializable, so the memory and Redis backends behave identically and one
   cannot accidentally rely on Python object identity.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class CacheBackend(Protocol):
    """Key/value cache with TTL."""

    name: str

    def is_available(self) -> bool:
        ...

    def get(self, key: str) -> Optional[Any]:
        """Return the cached value, or ``None`` on miss **or on backend failure**."""
        ...

    def set(self, key: str, value: Any, *, ttl_seconds: Optional[int] = None) -> None:
        """Best-effort write. Never raises."""
        ...

    def delete(self, key: str) -> None:
        ...

    def clear(self) -> None:
        """Drop everything. Used by tests and by an operator after a model change."""
        ...

    @property
    def stats(self) -> dict[str, int]:
        """``{"hits", "misses", "writes", "evictions"}`` — surfaced on the metrics endpoint.

        Hit rate is the only way to tell a cache that is helping from one that is merely consuming
        memory, so it is part of the contract rather than an implementation detail.
        """
        ...


__all__ = ["CacheBackend"]
