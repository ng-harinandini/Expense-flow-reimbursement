"""Repositories for duplicate detection & vendor intelligence.

Follows the same contract as :mod:`app.ai.repositories.knowledge_repository`: all SQL lives here,
repositories accept a ``Session`` and never commit (``flush`` only), and every read is
tenant-scoped.

**Vendor resolution is two lookups, not a scan.** :meth:`VendorRepository.resolve` tries an exact,
indexed alias match first; only a *miss* falls back to a ``pg_trgm`` ``word_similarity`` query
across existing aliases. ``word_similarity`` (not ``similarity``) is deliberate: it is designed for
exactly the "short query against a longer string" shape a vendor alias is —
``word_similarity('uber', 'uber trip llc')`` scores far higher than symmetric trigram
``similarity`` would, because it looks for the best-matching *word* rather than penalising the
length difference.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional, Sequence

from sqlalchemy import func, select

from app.ai.models.duplicate_detection import ClaimFingerprint, VendorAlias, VendorProfile
from app.core.logging import get_logger
from app.repositories.base import BaseRepository

logger = get_logger(__name__)


def normalize_vendor_name(name: str) -> str:
    """Lower/trim/collapse whitespace — the alias lookup key."""
    return " ".join((name or "").strip().lower().split())


@dataclass(frozen=True, slots=True)
class VendorResolution:
    """The outcome of resolving a raw vendor string to a canonical profile."""

    profile: VendorProfile
    matched_alias: bool          # True: an existing alias (exact or fuzzy) resolved this name.
    similarity: Optional[float]  # word_similarity score, when the fuzzy path was taken.


class VendorRepository(BaseRepository[VendorProfile]):
    """Canonical vendor identity: profiles plus the aliases that resolve to them."""

    model = VendorProfile

    def resolve(
        self, vendor_name: str, *, tenant_id: str, similarity_threshold: float
    ) -> VendorResolution:
        """Resolve ``vendor_name`` to a :class:`VendorProfile`, creating one if this is the first
        time this vendor (under any spelling) has been seen.

        1. Exact match against ``ai_vendor_aliases.alias_normalized`` (indexed, O(1)).
        2. A ``word_similarity`` scan across existing aliases for this tenant; the best match above
           ``similarity_threshold`` wins, and the raw string is recorded as a new alias so the next
           occurrence of this exact spelling takes the fast path.
        3. No match: a new profile is created, with this spelling as its first alias.
        """
        normalized = normalize_vendor_name(vendor_name)

        exact = self._one_or_none_alias(
            select(VendorAlias).where(
                VendorAlias.tenant_id == tenant_id, VendorAlias.alias_normalized == normalized
            )
        )
        if exact is not None:
            return VendorResolution(profile=exact.vendor_profile, matched_alias=True,
                                    similarity=1.0)

        fuzzy_row = self.session.execute(
            select(VendorAlias, func.word_similarity(VendorAlias.alias_normalized, normalized))
            .where(VendorAlias.tenant_id == tenant_id)
            .order_by(func.word_similarity(VendorAlias.alias_normalized, normalized).desc())
            .limit(1)
        ).first()

        if fuzzy_row is not None:
            alias, score = fuzzy_row
            if score is not None and float(score) >= similarity_threshold:
                self._record_alias(
                    vendor_profile_id=alias.vendor_profile_id, tenant_id=tenant_id,
                    alias_raw=vendor_name, alias_normalized=normalized, confidence=float(score),
                )
                return VendorResolution(
                    profile=alias.vendor_profile, matched_alias=True, similarity=float(score)
                )

        profile = VendorProfile(
            tenant_id=tenant_id,
            canonical_name=vendor_name.strip() or normalized or "Unknown Vendor",
        )
        self.session.add(profile)
        self.session.flush()
        self._record_alias(
            vendor_profile_id=profile.id, tenant_id=tenant_id,
            alias_raw=vendor_name, alias_normalized=normalized, confidence=1.0,
        )
        return VendorResolution(profile=profile, matched_alias=False, similarity=None)

    def record_claim(
        self, profile: VendorProfile, *, amount_usd: Decimal, occurred_at: datetime
    ) -> VendorProfile:
        """Update the running spend/claim statistics as one more claim resolves to this vendor."""
        profile.claim_count += 1
        profile.total_spend_usd = (profile.total_spend_usd or Decimal("0")) + amount_usd
        if profile.first_seen_at is None:
            profile.first_seen_at = occurred_at
        if profile.last_seen_at is None or occurred_at > profile.last_seen_at:
            profile.last_seen_at = occurred_at
        self.session.flush()
        return profile

    def _record_alias(
        self, *, vendor_profile_id: uuid.UUID, tenant_id: str, alias_raw: str,
        alias_normalized: str, confidence: float,
    ) -> None:
        self.session.add(
            VendorAlias(
                tenant_id=tenant_id,
                vendor_profile_id=vendor_profile_id,
                alias_raw=alias_raw.strip(),
                alias_normalized=alias_normalized,
                source="AUTO",
                confidence=Decimal(str(round(confidence, 2))),
            )
        )
        self.session.flush()

    def _one_or_none_alias(self, stmt) -> Optional[VendorAlias]:
        return self.session.execute(stmt).unique().scalars().first()


class ClaimFingerprintRepository(BaseRepository[ClaimFingerprint]):
    """Persistence and candidate-window queries for claim fingerprints."""

    model = ClaimFingerprint

    def get_by_claim_id(
        self, claim_id: uuid.UUID, *, tenant_id: str
    ) -> Optional[ClaimFingerprint]:
        return self._one_or_none(
            select(ClaimFingerprint).where(
                ClaimFingerprint.tenant_id == tenant_id, ClaimFingerprint.claim_id == claim_id
            )
        )

    def find_by_checksum(
        self, checksum: str, *, tenant_id: str, exclude_claim_id: Optional[uuid.UUID] = None
    ) -> Sequence[ClaimFingerprint]:
        """Exact byte-for-byte matches — the SHA-256 signal."""
        stmt = select(ClaimFingerprint).where(
            ClaimFingerprint.tenant_id == tenant_id,
            ClaimFingerprint.checksum_sha256 == checksum,
        )
        if exclude_claim_id is not None:
            stmt = stmt.where(ClaimFingerprint.claim_id != exclude_claim_id)
        return self._all(stmt.order_by(ClaimFingerprint.created_at.desc()))

    def find_candidates(
        self,
        *,
        tenant_id: str,
        vendor_profile_id: Optional[uuid.UUID],
        expense_date: date,
        date_window_days: int,
        exclude_claim_id: Optional[uuid.UUID] = None,
        limit: int = 200,
    ) -> Sequence[ClaimFingerprint]:
        """The bounded candidate set every content-similarity signal scans: same vendor, within a
        date window, most recent first. Never every fingerprint in the tenant — that would make a
        duplicate scan's cost grow with the corpus instead of with the window."""
        window = timedelta(days=max(date_window_days, 0))
        stmt = select(ClaimFingerprint).where(
            ClaimFingerprint.tenant_id == tenant_id,
            ClaimFingerprint.expense_date >= expense_date - window,
            ClaimFingerprint.expense_date <= expense_date + window,
        )
        if vendor_profile_id is not None:
            stmt = stmt.where(ClaimFingerprint.vendor_profile_id == vendor_profile_id)
        if exclude_claim_id is not None:
            stmt = stmt.where(ClaimFingerprint.claim_id != exclude_claim_id)
        return self._all(
            self._paginate(stmt.order_by(ClaimFingerprint.created_at.desc()), limit=limit)
        )

    def find_cross_employee_matches(
        self,
        *,
        tenant_id: str,
        vendor_profile_id: Optional[uuid.UUID],
        expense_date: date,
        amount_usd: Decimal,
        amount_tolerance: Decimal,
        exclude_employee_id: uuid.UUID,
        exclude_claim_id: Optional[uuid.UUID] = None,
    ) -> Sequence[ClaimFingerprint]:
        """Same vendor, same day, same amount (within tolerance), a **different** employee — the
        pattern the deterministic per-employee duplicate check structurally cannot see."""
        if vendor_profile_id is None:
            return ()
        stmt = select(ClaimFingerprint).where(
            ClaimFingerprint.tenant_id == tenant_id,
            ClaimFingerprint.vendor_profile_id == vendor_profile_id,
            ClaimFingerprint.expense_date == expense_date,
            func.abs(ClaimFingerprint.amount_usd - amount_usd) <= amount_tolerance,
            ClaimFingerprint.employee_id != exclude_employee_id,
        )
        if exclude_claim_id is not None:
            stmt = stmt.where(ClaimFingerprint.claim_id != exclude_claim_id)
        return self._all(stmt.order_by(ClaimFingerprint.created_at.desc()))

    def find_same_employee_vendor_window(
        self,
        *,
        tenant_id: str,
        employee_id: uuid.UUID,
        vendor_profile_id: Optional[uuid.UUID],
        expense_date: date,
        date_window_days: int,
        exclude_claim_id: Optional[uuid.UUID] = None,
        limit: int = 20,
    ) -> Sequence[ClaimFingerprint]:
        """Same employee and vendor within a date window — the multi-receipt-split candidate
        pool."""
        if vendor_profile_id is None:
            return ()
        window = timedelta(days=max(date_window_days, 0))
        stmt = select(ClaimFingerprint).where(
            ClaimFingerprint.tenant_id == tenant_id,
            ClaimFingerprint.employee_id == employee_id,
            ClaimFingerprint.vendor_profile_id == vendor_profile_id,
            ClaimFingerprint.expense_date >= expense_date - window,
            ClaimFingerprint.expense_date <= expense_date + window,
        )
        if exclude_claim_id is not None:
            stmt = stmt.where(ClaimFingerprint.claim_id != exclude_claim_id)
        return self._all(
            self._paginate(stmt.order_by(ClaimFingerprint.expense_date.asc()), limit=limit)
        )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "ClaimFingerprintRepository",
    "VendorRepository",
    "VendorResolution",
    "normalize_vendor_name",
    "utcnow",
]
