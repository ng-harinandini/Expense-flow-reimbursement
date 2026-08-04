"""ORM models for duplicate detection & vendor intelligence: fingerprints and vendor identity.

Alembic remains the sole schema owner (ADR-001); these declarations are what migration ``0005`` is
authored from.

**Vendor identity is resolved, not assumed.** ``merchant_vendor`` on a claim is free text — "Uber",
"UBER *TRIP" and "Uber BV" are the same vendor to a human but three different strings to a database.
``VendorProfile`` is the canonical identity; ``VendorAlias`` is every raw string that has ever
resolved to it, so the *second* time a new spelling is seen it resolves in one indexed lookup rather
than a fresh fuzzy-match scan.

**A fingerprint is per-claim, not per-receipt.** A claim without an uploaded receipt (a manually
entered expense) still gets a fingerprint row — the relational fields (vendor, date, amount,
employee) are always present; the content-derived fields (checksum, perceptual hashes, OCR
similarity text) are nullable and simply absent, so a signal that needs them is skipped rather than
the whole row being unrecordable.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.ai.core.config import BGE_M3_DIMENSIONS
from app.ai.models.knowledge import DEFAULT_TENANT_ID, TenantMixin
from app.ai.vector_store.pg_types import Vector
from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

# Same width as the knowledge-core embeddings (Task 4's shared model). A different width would
# require a new migration anyway, since existing vectors would not be comparable.
EMBEDDING_DIMENSIONS = BGE_M3_DIMENSIONS


class VendorProfile(TenantMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A canonical vendor identity, resolved from one or more raw spellings.

    ``risk_score`` and the spend/claim counters are maintained by the duplicate-detection engine as
    claims resolve to this profile — advisory statistics for the fraud engine and a future vendor
    dashboard, never a value the deterministic engines are required to consult.
    """

    __tablename__ = "ai_vendor_profiles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "canonical_name", name="uq_vendor_profiles_canonical_name"),
        CheckConstraint("claim_count >= 0", name="ck_vendor_profiles_claim_count_non_negative"),
        CheckConstraint(
            "total_spend_usd >= 0", name="ck_vendor_profiles_total_spend_non_negative"
        ),
        CheckConstraint(
            "risk_score IS NULL OR (risk_score >= 0 AND risk_score <= 100)",
            name="ck_vendor_profiles_risk_score_range",
        ),
        Index("ix_vendor_profiles_tenant_risk", "tenant_id", "risk_score"),
    )

    canonical_name: Mapped[str] = mapped_column(String(200), nullable=False)
    risk_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    claim_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_spend_usd: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default="0"
    )
    first_seen_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    aliases: Mapped[List["VendorAlias"]] = relationship(
        back_populates="vendor_profile", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<VendorProfile {self.canonical_name!r} claims={self.claim_count}>"


class VendorAlias(TenantMixin, UUIDPrimaryKeyMixin, Base):
    """One raw spelling that has resolved to a :class:`VendorProfile`.

    ``alias_normalized`` (lower/trim/collapsed-whitespace) is the lookup key: an exact match here is
    an O(1) indexed read, tried before ever falling back to a trigram scan across every profile.
    """

    __tablename__ = "ai_vendor_aliases"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "alias_normalized", name="uq_vendor_aliases_tenant_normalized"
        ),
        Index("ix_vendor_aliases_profile", "vendor_profile_id"),
        # Word-similarity / trigram alias resolution fallback (needs pg_trgm, installed by 0004).
        Index("ix_vendor_aliases_normalized_trgm", "alias_normalized",
              postgresql_using="gin", postgresql_ops={"alias_normalized": "gin_trgm_ops"}),
    )

    vendor_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_vendor_profiles.id", ondelete="CASCADE",
                   name="fk_vendor_aliases_vendor_profile_id"),
        nullable=False,
    )
    alias_raw: Mapped[str] = mapped_column(String(200), nullable=False)
    alias_normalized: Mapped[str] = mapped_column(String(200), nullable=False)
    # AUTO: created by a trigram/word-similarity match at resolution time. MANUAL: reserved for a
    # future admin-curated alias; nothing in this macro writes it, but the column exists now so a
    # later API does not need a migration.
    source: Mapped[str] = mapped_column(String(16), nullable=False, server_default="AUTO")
    confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    vendor_profile: Mapped["VendorProfile"] = relationship(back_populates="aliases")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<VendorAlias {self.alias_raw!r} -> {self.vendor_profile_id}>"


class ClaimFingerprint(TenantMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The duplicate-detection signature of one claim.

    Written once per claim (advisory, best-effort — see ``ClaimService._scan_duplicates``) and
    never mutated afterwards; a claim is not re-fingerprinted when it moves through review, because
    the expense facts a duplicate check cares about (vendor, date, amount, receipt content) do not
    change after submission in this system.
    """

    __tablename__ = "ai_claim_fingerprints"
    __table_args__ = (
        UniqueConstraint("claim_id", name="uq_claim_fingerprints_claim_id"),
        CheckConstraint("amount_usd >= 0", name="ck_claim_fingerprints_amount_non_negative"),
        Index("ix_claim_fingerprints_tenant_employee", "tenant_id", "employee_id"),
        Index("ix_claim_fingerprints_tenant_vendor_date", "tenant_id", "vendor_profile_id",
              "expense_date"),
        Index("ix_claim_fingerprints_checksum", "tenant_id", "checksum_sha256"),
    )

    claim_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    merchant_vendor: Mapped[str] = mapped_column(String(200), nullable=False)
    vendor_profile_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_vendor_profiles.id", ondelete="SET NULL",
                   name="fk_claim_fingerprints_vendor_profile_id"),
        nullable=True,
    )
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    invoice_number: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    # --- content-derived signals (all optional: absent when no receipt bytes were available) ---
    checksum_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    average_hash: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    difference_hash: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    ocr_text_excerpt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ocr_embedding: Mapped[Optional[tuple]] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS), nullable=True
    )

    vendor_profile: Mapped[Optional["VendorProfile"]] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ClaimFingerprint claim={self.claim_id} vendor={self.merchant_vendor!r}>"


__all__ = [
    "DEFAULT_TENANT_ID",
    "EMBEDDING_DIMENSIONS",
    "ClaimFingerprint",
    "VendorAlias",
    "VendorProfile",
]
