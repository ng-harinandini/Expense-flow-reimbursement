"""Redis cache backend (Task 15: "Redis ready").

Two design points that matter more than the code:

**The import is guarded.** ``redis`` is not in ``requirements.txt`` — the default deployment does
not need it. Importing it at module level would make this file unimportable on every machine that
has not installed it, and the registry must be able to *register* this adapter regardless (see
``ComponentRegistry``: registration stores a factory, so nothing is constructed until selected).

**Failures degrade to no-caching, they never propagate.** Every operation is wrapped. If Redis
disappears mid-request the platform gets slower, not broken. This is the single most important
property of a cache in a system where every cached value is re-derivable from durable data.

Values are JSON-encoded so the memory and Redis backends are behaviourally identical — a caller
cannot accidentally come to depend on Python object identity surviving a round trip, which would
work in tests with the memory cache and fail in production with Redis.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Optional

from app.ai.core.errors import ProviderNotConfiguredError
from app.core.logging import get_logger

logger = get_logger(__name__)


class RedisCache:
    """Shared cache across workers. Implements :class:`app.ai.interfaces.cache.CacheBackend`."""

    name = "redis"

    def __init__(self, url: str, *, default_ttl_seconds: Optional[int] = None,
                 socket_timeout: float = 2.0) -> None:
        if not url:
            raise ProviderNotConfiguredError(
                provider="redis", kind="CACHE",
                remedy="Set AI_CACHE_REDIS_URL (e.g. redis://localhost:6379/0).",
            )
        try:
            import redis  # noqa: PLC0415  (guarded: optional dependency)
        except ImportError as exc:
            raise ProviderNotConfiguredError(
                provider="redis", kind="CACHE",
                remedy="Install the optional dependency: pip install redis>=5.0.",
            ) from exc

        self._url = url
        self._default_ttl = default_ttl_seconds
        # A short timeout is essential: a cache lookup must never dominate the latency of the
        # operation it is accelerating.
        self._client = redis.Redis.from_url(
            url, socket_timeout=socket_timeout, socket_connect_timeout=socket_timeout,
            decode_responses=True,
        )
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._writes = 0
        self._errors = 0

    def is_available(self) -> bool:
        """Ping Redis. Never raises — the registry relies on this to decide on fallback."""
        try:
            return bool(self._client.ping())
        except Exception:
            return False

    def get(self, key: str) -> Optional[Any]:
        try:
            raw = self._client.get(key)
        except Exception as exc:
            self._note_error("get", exc)
            return None          # a broken cache is a miss, never an error
        with self._lock:
            if raw is None:
                self._misses += 1
                return None
            self._hits += 1
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            # Corrupt or foreign entry: drop it rather than serving nonsense.
            self.delete(key)
            return None

    def set(self, key: str, value: Any, *, ttl_seconds: Optional[int] = None) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        try:
            payload = json.dumps(value, default=str)
        except (TypeError, ValueError) as exc:
            logger.warning("ai.cache.unserializable", extra={"cacheKey": key,
                                                             "error": str(exc)[:200]})
            return
        try:
            if ttl and ttl > 0:
                self._client.setex(key, ttl, payload)
            else:
                self._client.set(key, payload)
            with self._lock:
                self._writes += 1
        except Exception as exc:
            self._note_error("set", exc)

    def delete(self, key: str) -> None:
        try:
            self._client.delete(key)
        except Exception as exc:
            self._note_error("delete", exc)

    def clear(self) -> None:
        """Delete only this platform's keys.

        Scans for the ``emb:``/``ret:`` prefixes rather than issuing ``FLUSHDB``: the Redis instance
        may be shared with other applications, and wiping their data would be an outage caused by a
        cache-clear.
        """
        try:
            for prefix in ("emb:*", "ret:*", "ctx:*", "prompt:*"):
                for key in self._client.scan_iter(match=prefix, count=500):
                    self._client.delete(key)
        except Exception as exc:
            self._note_error("clear", exc)

    def _note_error(self, operation: str, exc: Exception) -> None:
        with self._lock:
            self._errors += 1
        logger.warning(
            "ai.cache.error",
            extra={"operation": operation, "backend": "redis", "error": str(exc)[:200]},
        )

    @property
    def stats(self) -> dict[str, int]:
        with self._lock:
            lookups = self._hits + self._misses
            return {
                "hits": self._hits,
                "misses": self._misses,
                "writes": self._writes,
                "evictions": 0,          # Redis handles eviction by its own maxmemory policy
                "expirations": 0,
                "errors": self._errors,
                "entries": -1,           # -1 = not cheaply knowable; DBSIZE counts other apps too
                "hitRatePercent": int(round(100 * self._hits / lookups)) if lookups else 0,
            }


__all__ = ["RedisCache"]
