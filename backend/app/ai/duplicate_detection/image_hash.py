"""Perceptual image hashing: average-hash and difference-hash, pure Python over decoded pixels.

Deliberately not a heavyweight CV dependency (no OpenCV, no numpy) — Pillow decodes and resizes the
image; the hash itself is a plain Python loop over at most 72 pixels, which is negligible even at
receipt-upload volume. Guarded exactly like every optional format parser in
:mod:`app.ai.parsing`: :func:`is_available` reports whether Pillow is importable, and both hash
functions return ``None`` rather than raising when it is not, so a host without Pillow loses only
these two signals, never the rest of duplicate detection.

Both hashes are 64-bit and returned as 16-character hex strings, comparable via
:func:`app.ai.duplicate_detection.hashing.hamming_similarity`.
"""

from __future__ import annotations

import io
from typing import Optional

_HASH_GRID = 8  # 8x8 = 64 bits for both hashes.


def _pillow_image_module():
    try:
        from PIL import Image
    except ImportError:
        return None
    return Image


def is_available() -> bool:
    """Whether Pillow is importable — both hash functions no-op (return ``None``) without it."""
    return _pillow_image_module() is not None


def _bits_to_hex(bits: str) -> str:
    return f"{int(bits, 2):0{len(bits) // 4}x}"


def average_hash(image_bytes: bytes) -> Optional[str]:
    """aHash: each pixel is 1 if brighter than the thumbnail's mean, else 0.

    Robust to re-encoding, mild recompression and resizing — the exact use case Task 10 calls out
    ("re-encoded/resized image"), because shrinking to an 8x8 grayscale grid erases the
    high-frequency detail JPEG recompression perturbs.
    """
    Image = _pillow_image_module()
    if Image is None:
        return None
    with Image.open(io.BytesIO(image_bytes)) as source:
        thumbnail = source.convert("L").resize(
            (_HASH_GRID, _HASH_GRID), Image.Resampling.LANCZOS
        )
        pixels = list(thumbnail.getdata())
    average = sum(pixels) / len(pixels)
    bits = "".join("1" if p >= average else "0" for p in pixels)
    return _bits_to_hex(bits)


def difference_hash(image_bytes: bytes) -> Optional[str]:
    """dHash: each bit is whether a pixel is brighter than its right-hand neighbour.

    Captures gradient direction rather than absolute brightness, which average-hash misses — the
    two hashes are independent signals (``IMAGE_HASH`` and ``PERCEPTUAL_HASH`` in
    :class:`~app.ai.core.enums.DuplicateSignalKind``) precisely because they can disagree.
    """
    Image = _pillow_image_module()
    if Image is None:
        return None
    width = _HASH_GRID + 1
    with Image.open(io.BytesIO(image_bytes)) as source:
        thumbnail = source.convert("L").resize((width, _HASH_GRID), Image.Resampling.LANCZOS)
        pixels = list(thumbnail.getdata())
    bits: list[str] = []
    for row in range(_HASH_GRID):
        row_pixels = pixels[row * width:(row + 1) * width]
        for col in range(_HASH_GRID):
            bits.append("1" if row_pixels[col] > row_pixels[col + 1] else "0")
    return _bits_to_hex("".join(bits))


__all__ = ["average_hash", "difference_hash", "is_available"]
