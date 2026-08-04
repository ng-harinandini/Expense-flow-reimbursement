"""Thin, kind-scoped convenience wrappers over
:class:`~app.ai.governance.version_registry.RegistryService` for embedding providers/models — the
governed record of which embedding spec is currently policy, distinct from
:data:`app.ai.registry.registry.embedding_registry` (the in-process factory that actually builds
one).
"""

from __future__ import annotations

from typing import Optional, Sequence

from app.ai.core.enums import ProviderKind
from app.ai.governance.version_registry import RegistryService
from app.ai.models.governance import RegistryEntry
from app.domain.actor import Actor

KIND = ProviderKind.EMBEDDING.value


def register_embedding_spec(
    service: RegistryService,
    name: str,
    *,
    actor: Actor,
    config: Optional[dict] = None,
    cost_per_unit_usd: Optional[object] = None,
    cost_unit: Optional[str] = None,
) -> RegistryEntry:
    return service.publish(
        KIND, name, actor=actor, config=config, cost_per_unit_usd=cost_per_unit_usd,
        cost_unit=cost_unit,
    )


def active_embedding_spec(service: RegistryService, name: str) -> RegistryEntry:
    return service.get_active(KIND, name)


def list_active_embedding_specs(service: RegistryService) -> Sequence[RegistryEntry]:
    return service.list_active(KIND)


__all__ = [
    "KIND", "active_embedding_spec", "list_active_embedding_specs", "register_embedding_spec",
]
