"""Duplicate detection & vendor intelligence tests (T004-M10).

Pure-Python signal math (hashing, image hashing, text/invoice similarity, thresholds) is tested
without a database at all. Vendor resolution and the end-to-end engine are database-backed, exactly
like ``test_ai_knowledge.py``: real Postgres, real ``pg_trgm``/``word_similarity``, real
``ai_claim_fingerprints``/``ai_vendor_profiles``/``ai_vendor_aliases`` rows.
"""

from __future__ import annotations

import io
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.ai.core.enums import DuplicateSignalKind, DuplicateVerdict
from app.ai.duplicate_detection import image_hash
from app.ai.duplicate_detection.engine import DuplicateCandidateInput, DuplicateDetectionEngine
from app.ai.duplicate_detection.hashing import hamming_distance, hamming_similarity, sha256_hex
from app.ai.duplicate_detection.invoice import (
    InvoiceFields,
    find_multi_receipt_group,
    invoice_similarity,
)
from app.ai.duplicate_detection.service import DuplicateDetectionService
from app.ai.duplicate_detection.signals import (
    embedding_similarity_signal,
    image_hash_signal,
    invoice_similarity_signal,
    ocr_similarity_signal,
    sha256_signal,
    vendor_alias_signal,
)
from app.ai.duplicate_detection.text_similarity import text_similarity_ratio
from app.ai.duplicate_detection.thresholds import build_duplicate_thresholds
from app.ai.repositories.duplicate_detection_repository import (
    ClaimFingerprintRepository,
    VendorRepository,
    normalize_vendor_name,
)

TENANT_ID = "dup-detection-test-tenant"


def _make_receipt_image(*, size=(200, 300), seed_text: str = "Uber Receipt Total 42.50") -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    for i in range(10):
        draw.text((5, 5 + i * 20), f"{seed_text} line {i}", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _reencode(image_bytes: bytes, *, scale: float = 0.8, quality: int = 60) -> bytes:
    from PIL import Image

    with Image.open(io.BytesIO(image_bytes)) as img:
        resized = img.resize((int(img.width * scale), int(img.height * scale)))
        buf = io.BytesIO()
        resized.save(buf, format="JPEG", quality=quality)
        return buf.getvalue()


# ---------------------------------------------------------------------------
# 1. hashing.py — pure Python, no database
# ---------------------------------------------------------------------------


def test_sha256_hex_is_deterministic_and_content_sensitive() -> None:
    assert sha256_hex(b"hello") == sha256_hex(b"hello")
    assert sha256_hex(b"hello") != sha256_hex(b"hellp")


def test_hamming_distance_of_identical_hashes_is_zero() -> None:
    assert hamming_distance("ff00ff00", "ff00ff00") == 0


def test_hamming_distance_counts_differing_bits() -> None:
    # 0x0 vs 0xF differs in all 4 bits of the one hex digit.
    assert hamming_distance("0", "f") == 4


def test_hamming_distance_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError):
        hamming_distance("ff", "ffff")


def test_hamming_similarity_is_1_for_identical_hashes() -> None:
    assert hamming_similarity("abcdef1234567890", "abcdef1234567890", bits=64) == 1.0


def test_hamming_similarity_is_0_for_maximally_different_hashes() -> None:
    assert hamming_similarity("0" * 16, "f" * 16, bits=64) == 0.0


# ---------------------------------------------------------------------------
# 2. image_hash.py — perceptual hashing over real (synthetic) JPEG bytes
# ---------------------------------------------------------------------------


def test_image_hash_is_available_reports_pillow_installed() -> None:
    assert image_hash.is_available() is True


def test_average_and_difference_hash_are_64_bit_hex() -> None:
    image_bytes = _make_receipt_image()
    avg = image_hash.average_hash(image_bytes)
    diff = image_hash.difference_hash(image_bytes)
    assert avg is not None and len(avg) == 16
    assert diff is not None and len(diff) == 16
    int(avg, 16)  # does not raise
    int(diff, 16)


def test_reencoded_and_resized_image_scores_highly_similar() -> None:
    """Task 10 Done Check: 're-encoded/resized image -> perceptual signal above threshold'."""
    original = _make_receipt_image()
    reencoded = _reencode(original, scale=0.7, quality=55)

    original_hash = image_hash.average_hash(original)
    reencoded_hash = image_hash.average_hash(reencoded)
    similarity = hamming_similarity(original_hash, reencoded_hash, bits=64)

    assert similarity >= build_duplicate_thresholds().perceptual_hash_threshold
    assert sha256_hex(original) != sha256_hex(reencoded)  # confirms this is NOT a byte-exact match


def test_unrelated_images_score_less_similar_than_a_reencode() -> None:
    original = _make_receipt_image(seed_text="Uber Receipt Total 42.50")
    unrelated = _make_receipt_image(seed_text="Totally Different Vendor Invoice 999.00")
    reencoded = _reencode(original)

    same_similarity = hamming_similarity(
        image_hash.average_hash(original), image_hash.average_hash(reencoded), bits=64
    )
    different_similarity = hamming_similarity(
        image_hash.average_hash(original), image_hash.average_hash(unrelated), bits=64
    )
    assert same_similarity > different_similarity


# ---------------------------------------------------------------------------
# 3. text_similarity.py — pure Python
# ---------------------------------------------------------------------------


def test_text_similarity_ratio_is_1_for_identical_text() -> None:
    text = "Uber trip from airport to downtown office, total 42.50 USD"
    assert text_similarity_ratio(text, text) == 1.0


def test_text_similarity_ratio_is_high_for_near_duplicate_text() -> None:
    a = "Uber trip from airport to downtown office, total 42.50 USD"
    b = "Uber trip from airport to downtown office, total 42.51 USD"
    assert text_similarity_ratio(a, b) > 0.9


def test_text_similarity_ratio_is_low_for_unrelated_text() -> None:
    a = "Uber trip from airport to downtown office, total 42.50 USD"
    b = "Marriott hotel stay for three nights, conference attendance"
    assert text_similarity_ratio(a, b) < 0.5


def test_text_similarity_ratio_is_none_for_too_short_text() -> None:
    assert text_similarity_ratio("hi", "ok this is long enough") is None
    assert text_similarity_ratio("", "") is None


# ---------------------------------------------------------------------------
# 4. invoice.py — pure Python
# ---------------------------------------------------------------------------


def _invoice(vendor="Uber", amount="100.00", day=1, invoice_number=None, claim_id=None):
    return InvoiceFields(
        vendor=vendor, expense_date=date(2026, 1, day), amount_usd=Decimal(amount),
        invoice_number=invoice_number, claim_id=claim_id or uuid.uuid4(),
    )


def test_invoice_similarity_is_1_for_fully_matching_invoices() -> None:
    a = _invoice(invoice_number="INV-1")
    b = _invoice(invoice_number="INV-1")
    assert invoice_similarity(a, b, amount_tolerance=Decimal("0.01")) == 1.0


def test_invoice_similarity_ignores_invoice_number_when_either_side_lacks_one() -> None:
    a = _invoice(invoice_number=None)
    b = _invoice(invoice_number="INV-1")
    # vendor, date, amount all match; invoice_number is excluded from the weighted average rather
    # than counted as a mismatch, so this should still score 1.0.
    assert invoice_similarity(a, b, amount_tolerance=Decimal("0.01")) == 1.0


def test_invoice_similarity_is_low_for_different_vendor_and_amount() -> None:
    a = _invoice(vendor="Uber", amount="100.00")
    b = _invoice(vendor="Marriott", amount="500.00", day=15)
    assert invoice_similarity(a, b, amount_tolerance=Decimal("0.01")) == 0.0


def test_find_multi_receipt_group_detects_a_split_invoice() -> None:
    """Task 10 Done Check: 'one invoice split across multiple receipts'."""
    siblings = [
        _invoice(amount="40.00"), _invoice(amount="35.00"), _invoice(amount="25.00"),
    ]
    group = find_multi_receipt_group(Decimal("100.00"), siblings, amount_tolerance=Decimal("0.01"))
    assert group is not None
    assert len(group) >= 2
    assert sum((item.amount_usd for item in group), Decimal("0")) == Decimal("100.00")


def test_find_multi_receipt_group_returns_none_when_nothing_sums_to_target() -> None:
    siblings = [_invoice(amount="10.00"), _invoice(amount="20.00")]
    assert find_multi_receipt_group(
        Decimal("999.00"), siblings, amount_tolerance=Decimal("0.01")
    ) is None


def test_find_multi_receipt_group_never_returns_a_single_item_subset() -> None:
    """A subset of size 1 is a plain amount match, not a 'split across multiple receipts'."""
    siblings = [_invoice(amount="100.00")]
    assert find_multi_receipt_group(
        Decimal("100.00"), siblings, amount_tolerance=Decimal("0.01")
    ) is None


# ---------------------------------------------------------------------------
# 5. thresholds.py
# ---------------------------------------------------------------------------


def test_build_duplicate_thresholds_uses_ai_settings_defaults() -> None:
    thresholds = build_duplicate_thresholds()
    assert thresholds.sha256_threshold == 1.0
    assert 0.0 < thresholds.perceptual_hash_threshold <= 1.0
    assert thresholds.confirmed_score >= thresholds.likely_score


def test_build_duplicate_thresholds_accepts_overrides() -> None:
    """Task 10 Done Check: 'every threshold overridable by config'."""
    thresholds = build_duplicate_thresholds(near_duplicate_threshold=0.5, likely_score=0.6)
    assert thresholds.near_duplicate_threshold == 0.5
    assert thresholds.likely_score == 0.6


# ---------------------------------------------------------------------------
# 6. signals.py — pure Python, each signal independently scored/explainable
# ---------------------------------------------------------------------------


def test_sha256_signal_fires_on_exact_match() -> None:
    result = sha256_signal("abc123", ["xyz789", "abc123"], threshold=1.0)
    assert result.score == 1.0
    assert result.fired is True


def test_sha256_signal_does_not_fire_without_a_match() -> None:
    result = sha256_signal("abc123", ["xyz789"], threshold=1.0)
    assert result.score == 0.0
    assert result.fired is False


def test_sha256_signal_is_not_applicable_without_a_checksum() -> None:
    result = sha256_signal(None, ["xyz789"], threshold=1.0)
    assert result.score is None
    assert result.fired is False


def test_image_hash_signal_is_not_applicable_without_candidates() -> None:
    result = image_hash_signal("a" * 16, [], threshold=0.9)
    assert result.score is None


def test_ocr_similarity_signal_reports_best_match_among_candidates() -> None:
    target = "Uber trip from airport to downtown office, total 42.50 USD today"
    result = ocr_similarity_signal(
        target,
        ["completely unrelated text about something else entirely",
         "Uber trip from airport to downtown office, total 42.51 USD today"],
        threshold=0.85,
    )
    assert result.fired is True
    assert result.score > 0.9


def test_vendor_alias_signal_fires_on_a_differently_spelled_match() -> None:
    profile_id = uuid.uuid4()
    result = vendor_alias_signal(
        profile_id, "UBER *TRIP", [(profile_id, "Uber")], threshold=0.85
    )
    assert result.fired is True


def test_vendor_alias_signal_does_not_fire_on_an_identically_spelled_match() -> None:
    """Identical spelling needs no alias resolution to know it matches — that overlap belongs to
    INVOICE_SIMILARITY/CROSS_EMPLOYEE/MULTI_RECEIPT, not this signal. Also guards against the
    tautology this signal previously had: every candidate in the engine's own window already
    shares the candidate's resolved vendor by construction, so counting identical spelling as a
    match would make this signal fire on any two same-vendor claims regardless of amount or date."""
    profile_id = uuid.uuid4()
    result = vendor_alias_signal(
        profile_id, "Uber", [(profile_id, "Uber")], threshold=0.85
    )
    assert result.fired is False
    assert result.score == 0.0


def test_vendor_alias_signal_is_not_applicable_when_vendor_unresolved() -> None:
    result = vendor_alias_signal(None, "Uber", [(uuid.uuid4(), "Uber")], threshold=0.85)
    assert result.score is None


def test_invoice_similarity_signal_reports_best_match() -> None:
    candidate = _invoice(amount="100.00")
    others = [_invoice(vendor="Different", amount="1.00", day=20), _invoice(amount="100.00")]
    result = invoice_similarity_signal(
        candidate, others, threshold=0.9, amount_tolerance=Decimal("0.01")
    )
    assert result.fired is True


def test_embedding_similarity_signal_is_not_applicable_without_a_vector() -> None:
    result = embedding_similarity_signal(None, [(1.0, 0.0)], threshold=0.9)
    assert result.score is None


def test_embedding_similarity_signal_scores_cosine_similarity() -> None:
    result = embedding_similarity_signal((1.0, 0.0), [(1.0, 0.0), (0.0, 1.0)], threshold=0.9)
    assert result.score == pytest.approx(1.0)
    assert result.fired is True


# ---------------------------------------------------------------------------
# 7. VendorRepository — database-backed vendor identity resolution
# ---------------------------------------------------------------------------


def test_normalize_vendor_name_collapses_case_and_whitespace() -> None:
    assert normalize_vendor_name("  Uber   BV  ") == "uber bv"


def test_resolve_creates_a_new_profile_for_a_first_seen_vendor(db_session: Session) -> None:
    repo = VendorRepository(db_session)
    resolution = repo.resolve(
        f"Brand New Vendor {uuid.uuid4().hex[:8]}", tenant_id=TENANT_ID, similarity_threshold=0.85
    )
    assert resolution.matched_alias is False
    assert resolution.profile.id is not None
    assert resolution.profile.claim_count == 0


def test_resolve_matches_an_exact_existing_alias(db_session: Session) -> None:
    repo = VendorRepository(db_session)
    name = f"Exact Match Vendor {uuid.uuid4().hex[:8]}"
    first = repo.resolve(name, tenant_id=TENANT_ID, similarity_threshold=0.85)
    second = repo.resolve(name, tenant_id=TENANT_ID, similarity_threshold=0.85)
    assert second.profile.id == first.profile.id
    assert second.matched_alias is True
    assert second.similarity == 1.0


def test_resolve_matches_vendor_aliases_via_word_similarity(db_session: Session) -> None:
    """Task 10 Done Check: 'vendor aliases (Uber / UBER *TRIP / Uber BV) resolve to one profile
    via trigram + alias table'."""
    unique = uuid.uuid4().hex[:8]
    repo = VendorRepository(db_session)

    first = repo.resolve(f"Uber{unique}", tenant_id=TENANT_ID, similarity_threshold=0.3)
    second = repo.resolve(f"UBER{unique} *TRIP", tenant_id=TENANT_ID, similarity_threshold=0.3)
    third = repo.resolve(f"Uber{unique} BV", tenant_id=TENANT_ID, similarity_threshold=0.3)

    assert second.profile.id == first.profile.id
    assert third.profile.id == first.profile.id
    assert second.matched_alias is True
    assert third.matched_alias is True


def test_resolve_does_not_merge_genuinely_different_vendors(db_session: Session) -> None:
    unique = uuid.uuid4().hex[:8]
    repo = VendorRepository(db_session)
    uber = repo.resolve(f"Uber {unique}", tenant_id=TENANT_ID, similarity_threshold=0.85)
    marriott = repo.resolve(f"Marriott Hotels {unique}", tenant_id=TENANT_ID,
                            similarity_threshold=0.85)
    assert uber.profile.id != marriott.profile.id


def test_record_claim_updates_vendor_spend_statistics(db_session: Session) -> None:
    from datetime import datetime, timezone

    repo = VendorRepository(db_session)
    resolution = repo.resolve(
        f"Spend Tracking Vendor {uuid.uuid4().hex[:8]}", tenant_id=TENANT_ID,
        similarity_threshold=0.85,
    )
    repo.record_claim(
        resolution.profile, amount_usd=Decimal("50.00"), occurred_at=datetime.now(timezone.utc)
    )
    repo.record_claim(
        resolution.profile, amount_usd=Decimal("25.00"), occurred_at=datetime.now(timezone.utc)
    )
    assert resolution.profile.claim_count == 2
    assert resolution.profile.total_spend_usd == Decimal("75.00")
    assert resolution.profile.first_seen_at is not None


# ---------------------------------------------------------------------------
# 8. ClaimFingerprintRepository — database-backed candidate queries
# ---------------------------------------------------------------------------


def _make_fingerprint(
    db_session: Session, *, vendor_profile_id, employee_id=None, expense_date=None,
    amount_usd="100.00", checksum=None, average_hash=None, difference_hash=None,
    ocr_text_excerpt=None, invoice_number=None,
):
    from app.ai.models.duplicate_detection import ClaimFingerprint

    fingerprint = ClaimFingerprint(
        tenant_id=TENANT_ID,
        claim_id=uuid.uuid4(),
        employee_id=employee_id or uuid.uuid4(),
        merchant_vendor="Test Vendor",
        vendor_profile_id=vendor_profile_id,
        expense_date=expense_date or date(2026, 1, 10),
        amount_usd=Decimal(amount_usd),
        currency="USD",
        invoice_number=invoice_number,
        checksum_sha256=checksum,
        average_hash=average_hash,
        difference_hash=difference_hash,
        ocr_text_excerpt=ocr_text_excerpt,
    )
    db_session.add(fingerprint)
    db_session.flush()
    return fingerprint


def test_find_by_checksum_finds_an_exact_match(db_session: Session) -> None:
    vendor = VendorRepository(db_session).resolve(
        f"Checksum Vendor {uuid.uuid4().hex[:8]}", tenant_id=TENANT_ID, similarity_threshold=0.85
    ).profile
    checksum = sha256_hex(b"some receipt bytes")
    stored = _make_fingerprint(db_session, vendor_profile_id=vendor.id, checksum=checksum)

    repo = ClaimFingerprintRepository(db_session)
    matches = repo.find_by_checksum(checksum, tenant_id=TENANT_ID)
    assert stored.claim_id in [m.claim_id for m in matches]


def test_find_candidates_is_scoped_to_vendor_and_date_window(db_session: Session) -> None:
    vendor_a = VendorRepository(db_session).resolve(
        f"Window Vendor A {uuid.uuid4().hex[:8]}", tenant_id=TENANT_ID, similarity_threshold=0.85
    ).profile
    vendor_b = VendorRepository(db_session).resolve(
        f"Window Vendor B {uuid.uuid4().hex[:8]}", tenant_id=TENANT_ID, similarity_threshold=0.85
    ).profile

    in_window = _make_fingerprint(
        db_session, vendor_profile_id=vendor_a.id, expense_date=date(2026, 2, 10)
    )
    _make_fingerprint(  # different vendor: must not appear
        db_session, vendor_profile_id=vendor_b.id, expense_date=date(2026, 2, 10)
    )
    _make_fingerprint(  # same vendor, far outside the window: must not appear
        db_session, vendor_profile_id=vendor_a.id, expense_date=date(2026, 6, 1)
    )

    repo = ClaimFingerprintRepository(db_session)
    candidates = repo.find_candidates(
        tenant_id=TENANT_ID, vendor_profile_id=vendor_a.id, expense_date=date(2026, 2, 10),
        date_window_days=7,
    )
    claim_ids = {c.claim_id for c in candidates}
    assert in_window.claim_id in claim_ids
    assert vendor_b.id not in {c.vendor_profile_id for c in candidates}
    assert all(c.expense_date >= date(2026, 2, 3) for c in candidates)


def test_find_cross_employee_matches_excludes_the_same_employee(db_session: Session) -> None:
    vendor = VendorRepository(db_session).resolve(
        f"Cross Employee Vendor {uuid.uuid4().hex[:8]}", tenant_id=TENANT_ID,
        similarity_threshold=0.85,
    ).profile
    employee_a, employee_b = uuid.uuid4(), uuid.uuid4()
    other = _make_fingerprint(
        db_session, vendor_profile_id=vendor.id, employee_id=employee_b,
        expense_date=date(2026, 3, 1), amount_usd="75.00",
    )

    repo = ClaimFingerprintRepository(db_session)
    matches = repo.find_cross_employee_matches(
        tenant_id=TENANT_ID, vendor_profile_id=vendor.id, expense_date=date(2026, 3, 1),
        amount_usd=Decimal("75.00"), amount_tolerance=Decimal("0.01"),
        exclude_employee_id=employee_a,
    )
    assert other.claim_id in [m.claim_id for m in matches]

    same_employee_matches = repo.find_cross_employee_matches(
        tenant_id=TENANT_ID, vendor_profile_id=vendor.id, expense_date=date(2026, 3, 1),
        amount_usd=Decimal("75.00"), amount_tolerance=Decimal("0.01"),
        exclude_employee_id=employee_b,
    )
    assert other.claim_id not in [m.claim_id for m in same_employee_matches]


# ---------------------------------------------------------------------------
# 9. DuplicateDetectionEngine / DuplicateDetectionService — end to end
# ---------------------------------------------------------------------------


def _candidate(**overrides) -> DuplicateCandidateInput:
    fields = {
        "claim_id": uuid.uuid4(), "employee_id": uuid.uuid4(),
        "merchant_vendor": f"Engine Test Vendor {uuid.uuid4().hex[:8]}",
        "expense_date": date(2026, 4, 1), "amount_usd": Decimal("100.00"), "currency": "USD",
    }
    fields.update(overrides)
    return DuplicateCandidateInput(**fields)


def test_exact_reupload_scores_sha256_at_1(db_session: Session) -> None:
    """Task 10 Done Check: 'exact re-upload -> SHA-256 signal at 1.0'."""
    vendor = f"Exact Reupload Vendor {uuid.uuid4().hex[:8]}"
    image_bytes = _make_receipt_image()

    engine = DuplicateDetectionEngine(db_session)
    first = engine.scan_and_record(
        _candidate(merchant_vendor=vendor, receipt_bytes=image_bytes), tenant_id=TENANT_ID
    )
    second = engine.scan_and_record(
        _candidate(merchant_vendor=vendor, receipt_bytes=image_bytes), tenant_id=TENANT_ID
    )

    sha_signal = next(s for s in second.signals if s.kind == DuplicateSignalKind.SHA256)
    assert sha_signal.score == 1.0
    assert sha_signal.fired is True
    assert first.claim_id in second.related_claim_ids
    assert second.verdict in (DuplicateVerdict.LIKELY, DuplicateVerdict.CONFIRMED)


def test_reencoded_image_fires_perceptual_but_not_sha256(db_session: Session) -> None:
    """Task 10 Done Check: 're-encoded/resized image -> perceptual signal above threshold,
    SHA-256 below'."""
    vendor = f"Reencoded Vendor {uuid.uuid4().hex[:8]}"
    original = _make_receipt_image()
    reencoded = _reencode(original, scale=0.75, quality=55)

    engine = DuplicateDetectionEngine(db_session)
    engine.scan_and_record(
        _candidate(merchant_vendor=vendor, receipt_bytes=original), tenant_id=TENANT_ID
    )
    report = engine.scan_and_record(
        _candidate(merchant_vendor=vendor, receipt_bytes=reencoded), tenant_id=TENANT_ID
    )

    sha_signal = next(s for s in report.signals if s.kind == DuplicateSignalKind.SHA256)
    image_signal = next(s for s in report.signals if s.kind == DuplicateSignalKind.IMAGE_HASH)
    assert sha_signal.score == 0.0
    assert image_signal.fired is True
    near_duplicate = next(s for s in report.signals if s.kind == DuplicateSignalKind.NEAR_DUPLICATE)
    assert near_duplicate.fired is True


def test_ocr_text_near_duplicate_is_detected(db_session: Session) -> None:
    """Task 10 Done Check: 'OCR-text near-duplicate detected'."""
    vendor = f"OCR Vendor {uuid.uuid4().hex[:8]}"
    text_a = "Uber trip from airport to downtown office on business, total fare 42.50 USD"
    text_b = "Uber trip from airport to downtown office on business, total fare 42.51 USD"

    engine = DuplicateDetectionEngine(db_session)
    engine.scan_and_record(
        _candidate(merchant_vendor=vendor, ocr_text=text_a), tenant_id=TENANT_ID
    )
    report = engine.scan_and_record(
        _candidate(merchant_vendor=vendor, ocr_text=text_b), tenant_id=TENANT_ID
    )

    ocr_signal = next(s for s in report.signals if s.kind == DuplicateSignalKind.OCR_SIMILARITY)
    assert ocr_signal.fired is True


def test_cross_employee_duplicate_is_detected(db_session: Session) -> None:
    """Task 10 Done Check: 'same receipt submitted by two employees -> cross-employee case'."""
    vendor = f"Cross Employee Engine Vendor {uuid.uuid4().hex[:8]}"
    engine = DuplicateDetectionEngine(db_session)

    engine.scan_and_record(
        _candidate(merchant_vendor=vendor, expense_date=date(2026, 5, 5),
                  amount_usd=Decimal("88.00")),
        tenant_id=TENANT_ID,
    )
    report = engine.scan_and_record(
        _candidate(merchant_vendor=vendor, expense_date=date(2026, 5, 5),
                  amount_usd=Decimal("88.00")),
        tenant_id=TENANT_ID,
    )

    assert report.cross_employee is True
    cross_employee_signal = next(
        s for s in report.signals if s.kind == DuplicateSignalKind.CROSS_EMPLOYEE
    )
    assert cross_employee_signal.fired is True


def test_multi_receipt_split_is_detected(db_session: Session) -> None:
    """Task 10 Done Check: 'one invoice split across multiple receipts'."""
    vendor = f"Multi Receipt Engine Vendor {uuid.uuid4().hex[:8]}"
    employee_id = uuid.uuid4()
    engine = DuplicateDetectionEngine(db_session)

    engine.scan_and_record(
        _candidate(merchant_vendor=vendor, employee_id=employee_id, expense_date=date(2026, 6, 1),
                  amount_usd=Decimal("40.00")),
        tenant_id=TENANT_ID,
    )
    engine.scan_and_record(
        _candidate(merchant_vendor=vendor, employee_id=employee_id, expense_date=date(2026, 6, 1),
                  amount_usd=Decimal("35.00")),
        tenant_id=TENANT_ID,
    )
    report = engine.scan_and_record(
        _candidate(merchant_vendor=vendor, employee_id=employee_id, expense_date=date(2026, 6, 1),
                  amount_usd=Decimal("75.00")),
        tenant_id=TENANT_ID,
    )

    assert report.multi_receipt_claim_ids
    multi_receipt_signal = next(
        s for s in report.signals if s.kind == DuplicateSignalKind.MULTI_RECEIPT
    )
    assert multi_receipt_signal.fired is True


def test_vendor_alias_signal_fires_at_the_engine_level(db_session: Session) -> None:
    """Task 10 Done Check: vendor aliases resolve to one profile, reflected in the verdict."""
    unique = uuid.uuid4().hex[:8]
    engine = DuplicateDetectionEngine(
        db_session, thresholds=build_duplicate_thresholds(vendor_alias_threshold=0.3)
    )

    engine.scan_and_record(
        _candidate(merchant_vendor=f"Uber{unique}", expense_date=date(2026, 7, 1),
                  amount_usd=Decimal("12.00")),
        tenant_id=TENANT_ID,
    )
    report = engine.scan_and_record(
        _candidate(merchant_vendor=f"UBER{unique} *TRIP", expense_date=date(2026, 7, 3),
                  amount_usd=Decimal("999.00")),
        tenant_id=TENANT_ID,
    )

    vendor_signal = next(s for s in report.signals if s.kind == DuplicateSignalKind.VENDOR_ALIAS)
    assert vendor_signal.fired is True


def test_unrelated_claims_score_no_match(db_session: Session) -> None:
    engine = DuplicateDetectionEngine(db_session)
    report = engine.scan_and_record(
        _candidate(merchant_vendor=f"Lonely Vendor {uuid.uuid4().hex[:8]}"), tenant_id=TENANT_ID
    )
    assert report.verdict == DuplicateVerdict.NO_MATCH
    assert report.score == 0.0


def test_thresholds_overridden_per_engine_change_the_verdict(db_session: Session) -> None:
    """Task 10 Done Check: 'every threshold overridable by config and reflected in the verdict'."""
    vendor = f"Threshold Vendor {uuid.uuid4().hex[:8]}"
    text = "Uber trip from airport to downtown office, total fare 42.50 USD for business"
    similar_text = "Uber trip from airport to downtown office, total fare 42.60 USD for business"

    # Both calls (within one engine) share the same vendor, expense date, amount and employee —
    # needed so they land in each other's candidate window at all — which means VENDOR_ALIAS,
    # INVOICE_SIMILARITY and CROSS_EMPLOYEE would trivially fire too (a fresh random employee_id
    # per call, as most other tests use, would make CROSS_EMPLOYEE fire regardless of the OCR
    # threshold), and NEAR_DUPLICATE reads every continuous signal's *raw* score regardless of
    # whether that signal itself fired. All four are neutralised in both configs so this test
    # isolates exactly what it claims to: the OCR threshold, and only the OCR threshold, changing
    # the verdict.
    _isolate_to_ocr = {
        "vendor_alias_threshold": 1.01, "invoice_similarity_threshold": 1.01,
        "near_duplicate_threshold": 1.01, "cross_employee_enabled": False,
    }
    employee_id = uuid.uuid4()
    strict_engine = DuplicateDetectionEngine(
        db_session, thresholds=build_duplicate_thresholds(
            ocr_similarity_threshold=0.999, likely_score=0.999, confirmed_score=0.9999,
            **_isolate_to_ocr,
        ),
    )
    strict_engine.scan_and_record(
        _candidate(merchant_vendor=vendor + "-strict", employee_id=employee_id, ocr_text=text),
        tenant_id=TENANT_ID,
    )
    strict_report = strict_engine.scan_and_record(
        _candidate(merchant_vendor=vendor + "-strict", employee_id=employee_id,
                  ocr_text=similar_text),
        tenant_id=TENANT_ID,
    )

    lenient_engine = DuplicateDetectionEngine(
        db_session, thresholds=build_duplicate_thresholds(
            ocr_similarity_threshold=0.5, likely_score=0.5, confirmed_score=0.999,
            **_isolate_to_ocr,
        ),
    )
    lenient_engine.scan_and_record(
        _candidate(merchant_vendor=vendor + "-lenient", employee_id=employee_id, ocr_text=text),
        tenant_id=TENANT_ID,
    )
    lenient_report = lenient_engine.scan_and_record(
        _candidate(merchant_vendor=vendor + "-lenient", employee_id=employee_id,
                  ocr_text=similar_text),
        tenant_id=TENANT_ID,
    )

    ocr_strict = next(
        s for s in strict_report.signals if s.kind == DuplicateSignalKind.OCR_SIMILARITY
    )
    ocr_lenient = next(
        s for s in lenient_report.signals if s.kind == DuplicateSignalKind.OCR_SIMILARITY
    )
    assert ocr_strict.score == ocr_lenient.score  # same underlying similarity...
    assert ocr_strict.fired is False              # ...but a stricter threshold does not fire...
    assert ocr_lenient.fired is True               # ...while a looser one does.
    assert lenient_report.verdict != DuplicateVerdict.NO_MATCH
    assert strict_report.verdict == DuplicateVerdict.NO_MATCH


def test_scanning_the_same_claim_twice_does_not_duplicate_its_fingerprint(
    db_session: Session,
) -> None:
    """Fingerprints are written once per claim; a retried scan must not raise a unique-constraint
    violation or create a second row."""
    candidate = _candidate()
    engine = DuplicateDetectionEngine(db_session)
    engine.scan_and_record(candidate, tenant_id=TENANT_ID)
    engine.scan_and_record(candidate, tenant_id=TENANT_ID)  # must not raise

    repo = ClaimFingerprintRepository(db_session)
    stored = repo.get_by_claim_id(candidate.claim_id, tenant_id=TENANT_ID)
    assert stored is not None


def test_duplicate_detection_service_scan_claim_matches_the_recorder_protocol(
    db_session: Session,
) -> None:
    """The primitive-argument adapter ``ClaimService`` actually calls."""
    service = DuplicateDetectionService(session=db_session, tenant_id=TENANT_ID)
    report = service.scan_claim(
        uuid.uuid4(), uuid.uuid4(), f"Adapter Vendor {uuid.uuid4().hex[:8]}", date(2026, 8, 1),
        Decimal("50.00"), "USD",
    )
    assert report.verdict == DuplicateVerdict.NO_MATCH


def test_duplicate_detection_service_resolve_vendor_reuses_engine_resolution(
    db_session: Session,
) -> None:
    service = DuplicateDetectionService(session=db_session, tenant_id=TENANT_ID)
    name = f"Facade Vendor {uuid.uuid4().hex[:8]}"
    first = service.resolve_vendor(name)
    second = service.resolve_vendor(name)
    assert first.id == second.id


def test_duplicate_detection_service_describe_reports_active_thresholds(
    db_session: Session,
) -> None:
    service = DuplicateDetectionService(session=db_session, tenant_id=TENANT_ID)
    description = service.describe()
    assert description["tenantId"] == TENANT_ID
    assert "thresholds" in description


# ---------------------------------------------------------------------------
# 10. ClaimService integration: advisory only, byte-identical when disabled
# ---------------------------------------------------------------------------


def _build_claim_service(repositories: dict, *, duplicate_detection=None):
    from app.services.audit_service import AuditService
    from app.services.claim_service import ClaimService
    from app.services.employee_service import EmployeeService
    from app.services.policy_rule_service import PolicyRuleService

    audit = AuditService(repositories["audit"])
    employees = EmployeeService(repositories["employees"], repositories["roles"])
    policies = PolicyRuleService(repositories["policy_rules"], audit)
    return ClaimService(
        claim_repository=repositories["claims"],
        fraud_repository=repositories["fraud"],
        workflow_repository=repositories["workflows"],
        receipt_repository=repositories["receipts"],
        employee_service=employees,
        policy_rule_service=policies,
        audit_service=audit,
        duplicate_detection=duplicate_detection,
    )


def test_submitting_a_claim_records_its_fingerprint(
    db_session: Session, repositories: dict, claim_payload, employee_actor, employee,
) -> None:
    duplicate_detection = DuplicateDetectionService(session=db_session, tenant_id=TENANT_ID)
    service = _build_claim_service(repositories, duplicate_detection=duplicate_detection)

    claim = service.submit_claim(
        claim_payload(merchantVendor="Fingerprinted Claim Vendor"), actor=employee_actor,
    )

    repo = ClaimFingerprintRepository(db_session)
    stored = repo.get_by_claim_id(claim.id, tenant_id=TENANT_ID)
    assert stored is not None
    assert stored.employee_id == claim.employee_id


def test_disabling_duplicate_detection_leaves_claim_submission_byte_identical(
    db_session: Session, repositories: dict, claim_payload, employee_actor, employee,
) -> None:
    with_scan = _build_claim_service(
        repositories,
        duplicate_detection=DuplicateDetectionService(session=db_session, tenant_id=TENANT_ID),
    )
    without_scan = _build_claim_service(repositories, duplicate_detection=None)

    claim_a = with_scan.submit_claim(
        claim_payload(merchantVendor="Byte Identical Vendor A"), actor=employee_actor
    )
    claim_b = without_scan.submit_claim(
        claim_payload(merchantVendor="Byte Identical Vendor B"), actor=employee_actor
    )

    assert claim_a.status == claim_b.status
    assert claim_a.amount_usd == claim_b.amount_usd


def test_duplicate_scan_never_blocks_claim_submission_even_on_a_confirmed_verdict(
    db_session: Session, repositories: dict, claim_payload, employee_actor, employee,
) -> None:
    """The engine is advisory only: even a CONFIRMED verdict must not prevent the second claim
    from being created (the existing deterministic block, not this engine, owns rejection — and it
    only blocks the exact same employee/vendor/date/amount combination, which this test avoids so
    both submissions succeed and the advisory scan can be observed on the second one)."""
    duplicate_detection = DuplicateDetectionService(session=db_session, tenant_id=TENANT_ID)
    service = _build_claim_service(repositories, duplicate_detection=duplicate_detection)
    vendor = f"Advisory Only Vendor {uuid.uuid4().hex[:8]}"

    # Same vendor and amount (so the advisory VENDOR_ALIAS signal fires a CONFIRMED verdict on the
    # second submission) but a different expense date, so the *deterministic* exact-match block
    # (same employee + vendor + date + amount) does not itself refuse the second claim — isolating
    # what this test actually checks: that the advisory engine's own verdict has no say either way.
    first = service.submit_claim(
        claim_payload(
            merchantVendor=vendor, amountUSD=Decimal("60.00"),
            expenseDate=(date.today() - timedelta(days=5)).isoformat(),
        ),
        actor=employee_actor,
    )
    second = service.submit_claim(
        claim_payload(
            merchantVendor=vendor, amountUSD=Decimal("60.00"),
            expenseDate=(date.today() - timedelta(days=1)).isoformat(),
        ),
        actor=employee_actor,
    )

    assert first.status is not None
    assert second.status is not None

    report = duplicate_detection.scan_claim(
        uuid.uuid4(), uuid.uuid4(), vendor, date.today() - timedelta(days=1),
        Decimal("60.00"), "USD",
    )
    assert report.verdict in (DuplicateVerdict.LIKELY, DuplicateVerdict.CONFIRMED)


def test_duplicate_scan_failure_does_not_fail_claim_submission(
    db_session: Session, repositories: dict, claim_payload, employee_actor, employee,
) -> None:
    class _BrokenRecorder:
        def scan_claim(self, *args, **kwargs):
            raise RuntimeError("boom")

    service = _build_claim_service(repositories, duplicate_detection=_BrokenRecorder())
    claim = service.submit_claim(
        claim_payload(merchantVendor="Resilient To Failure Vendor"), actor=employee_actor,
    )
    assert claim.status is not None
