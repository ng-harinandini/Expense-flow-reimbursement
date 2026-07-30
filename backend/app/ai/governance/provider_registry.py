"""Cross-kind views over :class:`~app.ai.governance.version_registry.RegistryService`.

Where ``model_registry.py``/``embedding_registry.py`` scope to one :class:`~app.ai.core.enums.
ProviderKind` each, this module answers the kind-agnostic question: *what is the platform's entire
governed configuration right now, across every kind* — the shape a future ``/metrics``-style
governance endpoint (M13) would call.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Mapping, Sequence

from app.ai.governance.version_registry import RegistryService
from app.ai.models.governance import RegistryEntry


def active_providers_by_kind(service: RegistryService) -> Mapping[str, Sequence[RegistryEntry]]:
    """Every active registry entry, grouped by kind — one call for the platform's full governed
    configuration rather than one query per kind."""
    grouped: dict[str, list[RegistryEntry]] = defaultdict(list)
    for entry in service.list_active():
        grouped[entry.kind].append(entry)
    return dict(grouped)


def describe(service: RegistryService) -> dict[str, object]:
    """A plain, JSON-serializable summary — for a future ``/metrics``/governance endpoint."""
    grouped = active_providers_by_kind(service)
    return {
        "activeCount": sum(len(entries) for entries in grouped.values()),
        "byKind": {
            kind: [
                {"name": entry.name, "version": entry.version, "costPerUnitUsd": (
                    str(entry.cost_per_unit_usd) if entry.cost_per_unit_usd is not None else None
                ), "costUnit": entry.cost_unit}
                for entry in entries
            ]
            for kind, entries in grouped.items()
        },
    }


__all__ = ["active_providers_by_kind", "describe"]
