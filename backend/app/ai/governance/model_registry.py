"""Thin, kind-scoped convenience wrappers over
:class:`~app.ai.governance.version_registry.RegistryService` for generative/LLM models — the
governed record of which model is currently approved for use, separate from
:mod:`app.ai.governance.embedding_registry` (embeddings are never generative).
"""

from __future__ import annotations

from typing import Optional, Sequence

from app.ai.core.enums import ProviderKind
from app.ai.governance.version_registry import RegistryService
from app.ai.models.governance import RegistryEntry
from app.domain.actor import Actor

KIND = ProviderKind.LLM.value


def register_model(
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


def active_model(service: RegistryService, name: str) -> RegistryEntry:
    return service.get_active(KIND, name)


def list_active_models(service: RegistryService) -> Sequence[RegistryEntry]:
    return service.list_active(KIND)


__all__ = ["KIND", "active_model", "list_active_models", "register_model"]
