"""Component registry — how a provider is chosen by name instead of by import.

Every swappable component (embedding provider, vector store, chunker, parser, reranker, LLM, cache)
is registered under a name and resolved through here. That indirection is what turns "provider
independent" from a claim into a mechanism: business code asks for *the* embedding provider, and
configuration decides which one arrives.

Three properties earn their complexity:

**Lazy construction.** Registration stores a *factory*, not an instance. An adapter for Pinecone
must be registerable on a machine with no Pinecone credentials and no ``pinecone`` package
installed — otherwise the platform could not boot with the full adapter set present, which is
exactly the situation T004 ships in.

**Protocol verification at resolution.** The registry checks the constructed object against the
runtime-checkable protocol it claims to satisfy and raises if it does not. Catching a
half-implemented adapter here produces a clear error instead of an ``AttributeError`` from deep
inside a retrieval loop.

**Availability-aware fallback.** ``resolve_with_fallback`` walks an ordered preference list and
returns the first component whose ``is_available()`` is true. This is the mechanism behind the
bge-m3 → deterministic degradation path: the ONNX weights being absent is a normal state, not an
outage.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, Iterable, Mapping, Optional, TypeVar

from app.ai.core.enums import ProviderKind
from app.ai.core.errors import ProviderNotConfiguredError
from app.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Registration(Generic[T]):
    """One registered component: how to build it, and what it claims about itself."""

    name: str
    kind: ProviderKind
    factory: Callable[[], T]
    protocol: Optional[type] = None
    description: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


class ComponentRegistry(Generic[T]):
    """Named factories for one kind of component.

    Thread-safe because instances are cached and FastAPI serves requests from a thread pool; two
    concurrent first-requests must not each build their own ONNX session (which would double a
    ~2 GB memory footprint).
    """

    def __init__(self, kind: ProviderKind) -> None:
        self._kind = kind
        self._registrations: dict[str, Registration[T]] = {}
        self._instances: dict[str, T] = {}
        self._lock = threading.RLock()

    @property
    def kind(self) -> ProviderKind:
        return self._kind

    def register(
        self,
        name: str,
        factory: Callable[[], T],
        *,
        protocol: Optional[type] = None,
        description: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
        replace: bool = False,
    ) -> None:
        """Register ``factory`` under ``name``.

        Re-registering without ``replace=True`` is an error: a silent overwrite would make the
        active provider depend on module import order, which is close to impossible to debug.
        """
        key = name.strip().lower()
        if not key:
            raise ValueError("Component name must be non-empty.")
        with self._lock:
            if key in self._registrations and not replace:
                raise ValueError(
                    f"{self._kind.value} component '{key}' is already registered. "
                    "Pass replace=True to override deliberately."
                )
            self._registrations[key] = Registration(
                name=key, kind=self._kind, factory=factory, protocol=protocol,
                description=description, metadata=dict(metadata or {}),
            )
            self._instances.pop(key, None)

    def is_registered(self, name: str) -> bool:
        return name.strip().lower() in self._registrations

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._registrations))

    def describe(self, *, probe: bool = False) -> list[dict[str, Any]]:
        """Registry contents for the ``/metrics`` and governance endpoints.

        **Non-constructing by default.** Introspection must not have side effects: building every
        registered component to report on it would load the bge-m3 ONNX session (~2.3 GB) and dial
        every configured external store just because somebody opened a dashboard. ``available`` is
        therefore ``None`` — meaning "not yet built, unknown" — for anything not already
        instantiated.

        Pass ``probe=True`` only from a deliberate health check that accepts the cost of
        constructing each component.
        """
        with self._lock:
            registrations = sorted(self._registrations.values(), key=lambda r: r.name)
            instantiated = set(self._instances)

        out: list[dict[str, Any]] = []
        for reg in registrations:
            is_built = reg.name in instantiated
            out.append({
                "name": reg.name,
                "kind": reg.kind.value,
                "description": reg.description,
                "instantiated": is_built,
                # Already-built components are free to query; unbuilt ones stay unbuilt.
                "available": self._safe_available(reg.name, construct=is_built or probe),
                **dict(reg.metadata),
            })
        return out

    def resolve(self, name: str) -> T:
        """Build (or return the cached) component called ``name``.

        Construction failures are wrapped in :class:`ProviderNotConfiguredError` so a missing driver
        or credential surfaces as a 503 naming the remedy, rather than an ImportError 500.
        """
        key = name.strip().lower()
        with self._lock:
            cached = self._instances.get(key)
            if cached is not None:
                return cached

            registration = self._registrations.get(key)
            if registration is None:
                raise ProviderNotConfiguredError(
                    provider=key,
                    kind=self._kind.value,
                    remedy=(
                        f"Unknown {self._kind.value.lower()} component. "
                        f"Registered: {', '.join(self.names()) or 'none'}."
                    ),
                )

            try:
                instance = registration.factory()
            except ProviderNotConfiguredError:
                raise
            except Exception as exc:
                raise ProviderNotConfiguredError(
                    provider=key,
                    kind=self._kind.value,
                    remedy=f"Constructing it failed: {type(exc).__name__}: {exc}",
                ) from exc

            if registration.protocol is not None and not isinstance(
                instance, registration.protocol
            ):
                raise TypeError(
                    f"{self._kind.value} component '{key}' ({type(instance).__name__}) does not "
                    f"satisfy {registration.protocol.__name__}. Missing methods or attributes."
                )

            self._instances[key] = instance
            return instance

    def _safe_available(self, name: str, *, construct: bool = False) -> Optional[bool]:
        """``is_available()`` without letting a broken adapter break introspection.

        Returns ``None`` for "unknown": either the name is unregistered, or the component is not
        built and ``construct`` is false. ``False`` means it was actually asked and said no.
        """
        key = name.strip().lower()
        try:
            with self._lock:
                if key not in self._registrations:
                    return None
                instance = self._instances.get(key)
            if instance is None:
                if not construct:
                    return None
                instance = self.resolve(key)
            checker = getattr(instance, "is_available", None)
            return bool(checker()) if callable(checker) else True
        except Exception:
            return False

    def resolve_with_fallback(self, preferences: Iterable[str]) -> T:
        """First available component from ``preferences``, in order.

        The reason the platform can ship with bge-m3 as the default while still running in CI where
        the 2.3 GB weights are absent: the preference list degrades to the deterministic provider
        and logs which one won, loudly enough that nobody mistakes a fallback for the real thing.
        """
        ordered = [p.strip().lower() for p in preferences if p and p.strip()]
        if not ordered:
            raise ValueError("At least one preference is required.")

        failures: list[str] = []
        for name in ordered:
            if not self.is_registered(name):
                failures.append(f"{name}: not registered")
                continue
            try:
                instance = self.resolve(name)
            except ProviderNotConfiguredError as exc:
                failures.append(f"{name}: {exc.message}")
                continue

            checker = getattr(instance, "is_available", None)
            if callable(checker):
                try:
                    available = bool(checker())
                except Exception as exc:
                    failures.append(f"{name}: is_available raised {type(exc).__name__}")
                    continue
                if not available:
                    failures.append(f"{name}: reported unavailable")
                    continue

            if name != ordered[0]:
                logger.warning(
                    "ai.provider.fallback",
                    extra={
                        "kind": self._kind.value,
                        "requested": ordered[0],
                        "using": name,
                        "reasons": failures,
                    },
                )
            return instance

        raise ProviderNotConfiguredError(
            provider=ordered[0],
            kind=self._kind.value,
            remedy=(
                "No candidate was usable. Tried "
                + "; ".join(failures)
                + "."
            ),
        )

    def clear_instances(self) -> None:
        """Drop cached instances, keeping registrations. Used by tests and after a config change."""
        with self._lock:
            self._instances.clear()

    def reset(self) -> None:
        """Drop everything. Test-only."""
        with self._lock:
            self._registrations.clear()
            self._instances.clear()


# --- the platform's registries -----------------------------------------------
# Module-level singletons, populated by each provider package's ``register_*`` function, which the
# composition root in app/ai/services calls once at startup.

embedding_registry: ComponentRegistry[Any] = ComponentRegistry(ProviderKind.EMBEDDING)
vector_store_registry: ComponentRegistry[Any] = ComponentRegistry(ProviderKind.VECTOR_STORE)
rerank_registry: ComponentRegistry[Any] = ComponentRegistry(ProviderKind.RERANK)
llm_registry: ComponentRegistry[Any] = ComponentRegistry(ProviderKind.LLM)
cache_registry: ComponentRegistry[Any] = ComponentRegistry(ProviderKind.CACHE)
ocr_registry: ComponentRegistry[Any] = ComponentRegistry(ProviderKind.OCR)
chunking_registry: ComponentRegistry[Any] = ComponentRegistry(ProviderKind.CHUNKING)

ALL_REGISTRIES: tuple[ComponentRegistry[Any], ...] = (
    embedding_registry,
    vector_store_registry,
    rerank_registry,
    llm_registry,
    cache_registry,
    ocr_registry,
    chunking_registry,
)


def registry_snapshot(*, probe: bool = False) -> dict[str, list[dict[str, Any]]]:
    """Every registry's contents — the payload behind ``GET /api/ai/knowledge/metrics``.

    Non-constructing by default; see :meth:`ComponentRegistry.describe`.
    """
    return {
        registry.kind.value: registry.describe(probe=probe) for registry in ALL_REGISTRIES
    }


def reset_all_registries() -> None:
    """Test helper: return the process to a clean registry state."""
    for registry in ALL_REGISTRIES:
        registry.reset()


__all__ = [
    "ALL_REGISTRIES",
    "ComponentRegistry",
    "Registration",
    "cache_registry",
    "chunking_registry",
    "embedding_registry",
    "llm_registry",
    "ocr_registry",
    "registry_snapshot",
    "rerank_registry",
    "reset_all_registries",
    "vector_store_registry",
]
