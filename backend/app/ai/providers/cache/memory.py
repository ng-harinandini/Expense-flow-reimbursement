"""In-process LRU cache with TTL — the default backend (Task 15).

Chosen as the default because it needs no infrastructure and makes the embedding cache genuinely
useful in a single-process deployment: re-embedding the same policy text is the most common
avoidable cost in the platform.

Its limitation is stated plainly rather than discovered later: the cache is **per process**, so
under ``uvicorn --workers 4`` each worker keeps its own copy and the hit rate falls accordingly.
That is the same per-worker divergence ADR-003 identified as fatal for *state*; it is merely
wasteful for a *cache*, because every entry is derivable from durable data. Configure
``AI_CACHE_BACKEND=redis`` for a shared cache.

Eviction is LRU by access order with lazy TTL expiry. Nothing here raises: a cache that fails a
request it exists to accelerate is worse than no cache (see :class:`CacheBackend`).
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


class MemoryCache:
    """Thread-safe LRU + TTL cache. Implements :class:`app.ai.interfaces.cache.CacheBackend`."""

    name = "memory"

    def __init__(
        self, *, max_entries: int = 10_000, default_ttl_seconds: Optional[int] = None
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1.")
        self._max_entries = max_entries
        self._default_ttl = default_ttl_seconds
        # value + absolute expiry. OrderedDict gives O(1) LRU via move_to_end/popitem.
        self._entries: OrderedDict[str, tuple[Any, Optional[float]]] = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._writes = 0
        self._evictions = 0
        self._expirations = 0

    def is_available(self) -> bool:
        return True

    def get(self, key: str) -> Optional[Any]:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            value, expires_at = entry
            if expires_at is not None and now >= expires_at:
                # Lazy expiry: cheaper than a reaper thread, and a stale entry is never returned.
                del self._entries[key]
                self._expirations += 1
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return value

    def set(self, key: str, value: Any, *, ttl_seconds: Optional[int] = None) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        expires_at = (time.monotonic() + ttl) if ttl and ttl > 0 else None
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
            self._entries[key] = (value, expires_at)
            self._writes += 1
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)      # evict least recently used
                self._evictions += 1

    def delete(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    @property
    def stats(self) -> dict[str, int]:
        with self._lock:
            lookups = self._hits + self._misses
            return {
                "hits": self._hits,
                "misses": self._misses,
                "writes": self._writes,
                "evictions": self._evictions,
                "expirations": self._expirations,
                "entries": len(self._entries),
                # Integer percent: the metrics payload stays JSON-clean and diffable.
                "hitRatePercent": int(round(100 * self._hits / lookups)) if lookups else 0,
            }

    def reset_stats(self) -> None:
        with self._lock:
            self._hits = self._misses = self._writes = self._evictions = self._expirations = 0


class NullCache:
    """Caches nothing. Selected by ``AI_CACHE_BACKEND=none``.

    Useful for benchmarking cold-path cost and for tests that must observe every provider call.
    """

    name = "none"

    def is_available(self) -> bool:
        return True

    def get(self, key: str) -> Optional[Any]:
        return None

    def set(self, key: str, value: Any, *, ttl_seconds: Optional[int] = None) -> None:
        return None

    def delete(self, key: str) -> None:
        return None

    def clear(self) -> None:
        return None

    @property
    def stats(self) -> dict[str, int]:
        return {"hits": 0, "misses": 0, "writes": 0, "evictions": 0,
                "expirations": 0, "entries": 0, "hitRatePercent": 0}


__all__ = ["MemoryCache", "NullCache"]
