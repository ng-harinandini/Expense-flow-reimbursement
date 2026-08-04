"""Cost estimation over a governed :class:`~app.ai.models.governance.RegistryEntry`.

``cost_per_unit_usd`` is priced per one ``cost_unit`` (see that field's docstring) — deliberately
not a "per-1M-tokens" style rate, so estimating a cost is a plain multiplication with no unit-prefix
parsing to get wrong. The caller is responsible for expressing ``quantity`` in the same unit the
entry declares (e.g. a raw token count against an entry priced per token).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from app.ai.models.governance import RegistryEntry


def estimate_cost(entry: RegistryEntry, quantity: Decimal | int | float) -> Optional[Decimal]:
    """``quantity`` units of ``entry.cost_unit``, priced at ``entry.cost_per_unit_usd``.

    Returns ``None`` — not zero — when the entry has no cost rate configured at all, so "free" and
    "unpriced" are never confused with each other.
    """
    if entry.cost_per_unit_usd is None:
        return None
    return entry.cost_per_unit_usd * Decimal(str(quantity))


__all__ = ["estimate_cost"]
