"""Content-identity hashing: exact-byte SHA-256 and the Hamming distance perceptual hashes share.

Kept separate from :mod:`app.ai.duplicate_detection.image_hash` because SHA-256 needs no image
decoding at all — it is the cheapest, most certain signal (an exact re-upload) and must work even
when Pillow is unavailable and every perceptual signal is skipped.
"""

from __future__ import annotations

import hashlib


def sha256_hex(data: bytes) -> str:
    """Hex digest of raw bytes — identical bytes always produce an identical, exact match."""
    return hashlib.sha256(data).hexdigest()


def hamming_distance(hash_a: str, hash_b: str) -> int:
    """Bit-distance between two equal-length hex hash strings.

    Raises rather than silently truncating on a length mismatch — comparing hashes of different
    bit-widths (e.g. a bug that produced a 32-bit hash against a stored 64-bit one) would otherwise
    return a meaningless number instead of surfacing the bug.
    """
    if len(hash_a) != len(hash_b):
        raise ValueError(
            f"Cannot compare hashes of different lengths: {len(hash_a)} vs {len(hash_b)}."
        )
    int_a = int(hash_a, 16)
    int_b = int(hash_b, 16)
    return bin(int_a ^ int_b).count("1")


def hamming_similarity(hash_a: str, hash_b: str, *, bits: int) -> float:
    """``1 - distance/bits``, clamped to ``[0, 1]`` — a perceptual-hash score comparable to the
    other similarity signals, which are all "higher is more similar"."""
    distance = hamming_distance(hash_a, hash_b)
    return max(0.0, min(1.0, 1.0 - (distance / bits)))


__all__ = ["hamming_distance", "hamming_similarity", "sha256_hex"]
