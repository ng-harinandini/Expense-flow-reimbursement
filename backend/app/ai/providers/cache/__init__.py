"""Cache backends (Task 15) and their registration."""

from app.ai.interfaces.cache import CacheBackend
from app.ai.providers.cache.memory import MemoryCache, NullCache


def register_cache_providers() -> None:
    """Register every cache backend. Called once by the composition root.

    Redis is registered via a factory that imports ``redis`` lazily, so this function succeeds on a
    machine where the optional dependency is absent.
    """
    from app.ai.core.config import ai_settings
    from app.ai.registry.registry import cache_registry

    cache_registry.register(
        "memory",
        lambda: MemoryCache(
            max_entries=ai_settings.CACHE_MAX_ENTRIES,
            default_ttl_seconds=ai_settings.EMBEDDING_CACHE_TTL_SECONDS,
        ),
        protocol=CacheBackend,
        description="In-process LRU + TTL. Per-worker; no infrastructure required.",
        replace=True,
    )
    cache_registry.register(
        "none",
        NullCache,
        protocol=CacheBackend,
        description="Disables caching entirely.",
        replace=True,
    )

    def _redis():
        from app.ai.providers.cache.redis_cache import RedisCache
        return RedisCache(
            ai_settings.CACHE_REDIS_URL or "",
            default_ttl_seconds=ai_settings.EMBEDDING_CACHE_TTL_SECONDS,
        )

    cache_registry.register(
        "redis",
        _redis,
        protocol=CacheBackend,
        description="Shared across workers. Requires AI_CACHE_REDIS_URL and the redis package.",
        replace=True,
    )


__all__ = ["MemoryCache", "NullCache", "register_cache_providers"]
