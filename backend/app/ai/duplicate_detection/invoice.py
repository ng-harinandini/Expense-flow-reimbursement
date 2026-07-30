"""Invoice-field similarity and the multi-receipt-split detector.

``InvoiceFields`` is a plain value object — this module has no database dependency, so it can be
unit-tested with hand-built fixtures and reused by both the engine (real claims) and its own tests
(synthetic ones).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional, Sequence

# Above this many candidates, evaluating every subset becomes combinatorially wasteful for a
# same-day, same-vendor, same-employee window that should realistically hold a handful of receipts.
# Capped defensively, not because the real data is expected to hit it.
_MAX_SUBSET_CANDIDATES = 12

_FIELD_WEIGHTS = {"vendor": 0.35, "invoice_number": 0.35, "date": 0.15, "amount": 0.15}


@dataclass(frozen=True, slots=True)
class InvoiceFields:
    """The fields of one claim/receipt relevant to invoice-level comparison."""

    vendor: str
    expense_date: date
    amount_usd: Decimal
    invoice_number: Optional[str] = None
    claim_id: Optional[object] = None


def _normalize_vendor(vendor: str) -> str:
    return " ".join((vendor or "").strip().lower().split())


def invoice_similarity(
    a: InvoiceFields, b: InvoiceFields, *, amount_tolerance: Decimal
) -> float:
    """Weighted agreement across vendor, date, amount and (when both sides have one) invoice
    number. Fields that cannot be compared (a missing invoice number on either side) are excluded
    from the weighted average rather than counted as a mismatch."""
    contributions: list[tuple[float, float]] = [
        (_FIELD_WEIGHTS["vendor"],
         1.0 if _normalize_vendor(a.vendor) == _normalize_vendor(b.vendor) else 0.0),
        (_FIELD_WEIGHTS["date"], 1.0 if a.expense_date == b.expense_date else 0.0),
        (_FIELD_WEIGHTS["amount"],
         1.0 if abs(a.amount_usd - b.amount_usd) <= amount_tolerance else 0.0),
    ]
    if a.invoice_number and b.invoice_number:
        contributions.append((
            _FIELD_WEIGHTS["invoice_number"],
            1.0 if a.invoice_number.strip().lower() == b.invoice_number.strip().lower() else 0.0,
        ))
    total_weight = sum(weight for weight, _ in contributions)
    if total_weight <= 0:
        return 0.0
    return sum(weight * score for weight, score in contributions) / total_weight


def find_multi_receipt_group(
    target_amount: Decimal,
    candidates: Sequence[InvoiceFields],
    *,
    amount_tolerance: Decimal,
) -> Optional[tuple[InvoiceFields, ...]]:
    """Whether some subset of ``candidates`` (size >= 2) sums to ``target_amount`` within
    tolerance — one invoice split across multiple receipt claims.

    A subset of size 1 is not "split across multiple receipts"; that pattern is what the
    cross-employee/SHA-256/vendor-alias signals already exist to catch. Returns the first matching
    subset found, smallest first, or ``None`` if no combination fits.
    """
    pool = list(candidates[:_MAX_SUBSET_CANDIDATES])
    for size in range(2, len(pool) + 1):
        for combo in itertools.combinations(pool, size):
            if abs(sum((item.amount_usd for item in combo), Decimal("0")) - target_amount) \
                    <= amount_tolerance:
                return combo
    return None


__all__ = ["InvoiceFields", "find_multi_receipt_group", "invoice_similarity"]
