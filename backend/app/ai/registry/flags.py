"""Feature flags for AI capabilities (Task 13).

Two layers, checked in order:

1. **Runtime overrides** — set in-process (by an admin endpoint or a test). Highest precedence.
2. **Configuration** — ``AISettings`` fields, i.e. environment variables.

Flags are *hierarchical*: ``ENABLED`` is a master switch, and a child capability is on only if both
it and its ancestors are. That is what makes ``AI_ENABLED=false`` a genuine kill switch — without
the hierarchy an operator would have to find and unset a dozen variables during an incident, and
would miss one.

The generative capabilities default **off**. Retrieval and ingestion are read-only and cannot alter
a claim, so they are safe on by default; anything that invokes an LLM must be switched on
deliberately, because merely deploying this code must never start sending data to a model provider.
"""

from __future__ import annotations

import threading
from typing import Mapping, Optional

from app.ai.core.config import AISettings, ai_settings
from app.ai.core.errors import FeatureDisabledError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Flag name -> (settings attribute, parent flag). ``None`` parent means top level.
_FLAG_TREE: dict[str, tuple[str, Optional[str]]] = {
    "ai": ("ENABLED", None),
    "ai.ingestion": ("INGESTION_ENABLED", "ai"),
    "ai.retrieval": ("RETRIEVAL_ENABLED", "ai"),
    "ai.rerank": ("RERANK_ENABLED", "ai.retrieval"),
    "ai.duplicate_detection": ("DUPLICATE_DETECTION_ENABLED", "ai"),
    "ai.decision_memory": ("DECISION_MEMORY_ENABLED", "ai"),
    "ai.category_classification": ("CLASSIFICATION_ENABLED", "ai"),
    "ai.llm": ("LLM_ENABLED", "ai"),
    "ai.llm.explanations": ("LLM_EXPLANATIONS_ENABLED", "ai.llm"),
    "ai.telemetry": ("TELEMETRY_ENABLED", "ai"),
    "ai.pii_detection": ("PII_DETECTION_ENABLED", "ai"),
    "ai.embedding_cache": ("EMBEDDING_CACHE_ENABLED", "ai"),
    "ai.retrieval_cache": ("RETRIEVAL_CACHE_ENABLED", "ai.retrieval"),
}


class FeatureFlags:
    """Resolves AI capability flags, honouring the hierarchy and runtime overrides."""

    def __init__(self, settings: Optional[AISettings] = None) -> None:
        self._settings = settings or ai_settings
        self._overrides: dict[str, bool] = {}
        self._lock = threading.RLock()

    # --- queries ------------------------------------------------------------

    def known_flags(self) -> tuple[str, ...]:
        return tuple(sorted(_FLAG_TREE))

    def is_enabled(self, flag: str) -> bool:
        """Whether ``flag`` and every ancestor are enabled.

        An unknown flag is **disabled**, not enabled. Fail-closed: a typo in a flag name must not
        silently switch a capability on.
        """
        name = flag.strip().lower()
        if name not in _FLAG_TREE:
            logger.warning("ai.flag.unknown", extra={"flag": name})
            return False

        seen: set[str] = set()
        current: Optional[str] = name
        while current is not None:
            if current in seen:      # defensive: a cycle would otherwise hang the request
                raise ValueError(f"Cycle in feature-flag hierarchy at '{current}'.")
            seen.add(current)
            if not self._own_value(current):
                return False
            current = _FLAG_TREE[current][1]
        return True

    def _own_value(self, flag: str) -> bool:
        with self._lock:
            if flag in self._overrides:
                return self._overrides[flag]
        attribute = _FLAG_TREE[flag][0]
        return bool(getattr(self._settings, attribute))

    def require(self, flag: str) -> None:
        """Raise :class:`FeatureDisabledError` (503) unless ``flag`` is enabled."""
        if not self.is_enabled(flag):
            raise FeatureDisabledError(flag)

    def disabled_reason(self, flag: str) -> Optional[str]:
        """Which ancestor actually switched ``flag`` off — the answer an operator needs."""
        name = flag.strip().lower()
        if name not in _FLAG_TREE:
            return f"'{name}' is not a known AI feature flag."
        current: Optional[str] = name
        while current is not None:
            if not self._own_value(current):
                attribute = _FLAG_TREE[current][0]
                if current == name:
                    return f"Disabled by AI_{attribute}."
                return f"Disabled by ancestor flag '{current}' (AI_{attribute})."
            current = _FLAG_TREE[current][1]
        return None

    # --- overrides ----------------------------------------------------------

    def set_override(self, flag: str, value: Optional[bool]) -> None:
        """Force a flag on/off in this process; ``None`` clears the override.

        Deliberately in-process rather than persisted: an emergency AI kill switch must take effect
        without a database write succeeding first.
        """
        name = flag.strip().lower()
        if name not in _FLAG_TREE:
            raise ValueError(
                f"Unknown feature flag '{name}'. Known: {', '.join(self.known_flags())}."
            )
        with self._lock:
            if value is None:
                self._overrides.pop(name, None)
            else:
                self._overrides[name] = bool(value)
        logger.info("ai.flag.override", extra={"flag": name, "value": value})

    def clear_overrides(self) -> None:
        with self._lock:
            self._overrides.clear()

    def snapshot(self) -> Mapping[str, object]:
        """All flags with their effective values — for ``/metrics`` and for support diagnosis."""
        with self._lock:
            overrides = dict(self._overrides)
        return {
            "flags": {
                flag: {
                    "enabled": self.is_enabled(flag),
                    "own": self._own_value(flag),
                    "overridden": flag in overrides,
                    "parent": _FLAG_TREE[flag][1],
                }
                for flag in self.known_flags()
            },
            "overrideCount": len(overrides),
        }


feature_flags = FeatureFlags()

__all__ = ["FeatureFlags", "feature_flags"]
