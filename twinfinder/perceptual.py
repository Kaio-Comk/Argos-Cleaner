"""Similar images: the same photo resized, recompressed or converted.

dHash (difference hash): shrink to 9x8 grayscale and record whether each pixel is
brighter than its right neighbour: 64 bits that survive scaling and JPEG noise.
Similarity = Hamming distance between hashes.

Comparing every pair is O(n^2). Instead hashes are indexed by chunks (multi-index
hashing): two hashes within distance <= t must share at least one chunk when
the hash is split in t+1 chunks (pigeonhole), so only hashes sharing a chunk are compared.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Callable, Iterable, Optional

from .files import FileInfo

HASH_BITS = 64


def dhash_pixels(gray9x8: bytes) -> int:
    """64-bit dHash from 72 grayscale bytes (9 columns x 8 rows, row-major)."""
    if len(gray9x8) != 72:
        raise ValueError("expected 72 bytes (9x8)")
    bits = 0
    for row in range(8):
        base = row * 9
        for col in range(8):
            bits = (bits << 1) | (1 if gray9x8[base + col] > gray9x8[base + col + 1] else 0)
    return bits


def dhash_image(path: Path) -> Optional[int]:
    """dHash of an image file, or None when it cannot be decoded. Honors EXIF rotation."""
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:                      # pragma: no cover
        raise RuntimeError("Pillow is required for image similarity: pip install Pillow") from exc
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            gray = im.convert("L").resize((9, 8), Image.LANCZOS)
            return dhash_pixels(bytes(gray.tobytes()))
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def image_size(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:  # noqa: BLE001 - metadata only; unreadable means "unknown"
        return (0, 0)


def group_similar(hashes: dict, threshold: int) -> list[list]:
    """Group keys whose hashes are within `threshold` bits. `hashes`: {key: int}.

    Transitive (A~B and B~C put A, B, C together). Returns groups of >= 2 keys."""
    if not 0 <= threshold <= 16:
        raise ValueError("threshold must be between 0 and 16")
    n_chunks = threshold + 1
    bounds = [round(i * HASH_BITS / n_chunks) for i in range(n_chunks + 1)]
    index: dict[tuple, list] = defaultdict(list)
    for key, h in hashes.items():
        for c in range(n_chunks):
            lo, hi = bounds[c], bounds[c + 1]
            index[(c, (h >> lo) & ((1 << (hi - lo)) - 1))].append(key)

    parent = {k: k for k in hashes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for bucket in index.values():
        if len(bucket) < 2:
            continue
        for i, a in enumerate(bucket):
            for b in bucket[i + 1:]:
                if find(a) != find(b) and hamming(hashes[a], hashes[b]) <= threshold:
                    parent[find(a)] = find(b)

    groups: dict = defaultdict(list)
    for k in hashes:
        groups[find(k)].append(k)
    return [g for g in groups.values() if len(g) > 1]


def find_similar_images(files: Iterable[FileInfo], threshold: int = 5, *,
                        progress: Optional[Callable[[str, int], None]] = None
                        ) -> list[list[FileInfo]]:
    by_path = {}
    hashes = {}
    for fi in files:
        if fi.kind != "image":
            continue
        h = dhash_image(fi.path)
        if progress:
            progress("image", 1)
        if h is not None:
            by_path[fi.path] = fi
            hashes[fi.path] = h
    groups = group_similar(hashes, threshold)
    return sorted((sorted((by_path[p] for p in g), key=lambda x: str(x.path)) for g in groups),
                  key=lambda g: str(g[0].path))
