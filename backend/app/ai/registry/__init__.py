"""Component registry and feature flags — how providers and capabilities are selected by name."""

from app.ai.registry.flags import FeatureFlags, feature_flags
from app.ai.registry.registry import (
    ALL_REGISTRIES,
    ComponentRegistry,
    Registration,
    cache_registry,
    embedding_registry,
    llm_registry,
    ocr_registry,
    registry_snapshot,
    rerank_registry,
    reset_all_registries,
    vector_store_registry,
)

__all__ = [
    "ALL_REGISTRIES",
    "ComponentRegistry",
    "FeatureFlags",
    "Registration",
    "cache_registry",
    "embedding_registry",
    "feature_flags",
    "llm_registry",
    "ocr_registry",
    "registry_snapshot",
    "rerank_registry",
    "reset_all_registries",
    "vector_store_registry",
]
