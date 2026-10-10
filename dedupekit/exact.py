"""Exact duplicates in stages, so most files are never read in full:

  1. group by size          (free: already in the directory entry)
  2. group by partial hash  (first + last 64 KB)
  3. group by full hash     (BLAKE3 when installed, SHA-256 otherwise)

Only files that survive a stage go to the next one. Two files are reported as
duplicates only when their FULL hashes match.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Callable, Iterable, Optional

from .files import FileInfo

PARTIAL = 64 * 1024
CHUNK = 1024 * 1024

try:                                    # optional speed-up
    import blake3 as _blake3
except ImportError:                     # pragma: no cover - depends on the environment
    _blake3 = None


def _hasher():
    return _blake3.blake3() if _blake3 else hashlib.sha256()


def partial_hash(path: Path, size: int) -> str:
    h = _hasher()
    with open(path, "rb") as f:
        h.update(f.read(PARTIAL))
        if size > 2 * PARTIAL:
            f.seek(-PARTIAL, 2)
            h.update(f.read(PARTIAL))
    return h.hexdigest()


def full_hash(path: Path) -> str:
    h = _hasher()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def find_exact(files: Iterable[FileInfo], *, progress: Optional[Callable[[str, int], None]] = None
               ) -> list[list[FileInfo]]:
    """Groups (size >= 2 members each) of byte-identical files."""
    by_size: dict[int, list[FileInfo]] = defaultdict(list)
    for fi in files:
        by_size[fi.size].append(fi)
    candidates = [g for g in by_size.values() if len(g) > 1]

    groups: list[list[FileInfo]] = []
    for g in candidates:
        by_partial: dict[str, list[FileInfo]] = defaultdict(list)
        for fi in g:
            try:
                by_partial[partial_hash(fi.path, fi.size)].append(fi)
            except OSError:
                continue
        for pg in by_partial.values():
            if len(pg) < 2:
                continue
            by_full: dict[str, list[FileInfo]] = defaultdict(list)
            for fi in pg:
                try:
                    by_full[full_hash(fi.path)].append(fi)
                except OSError:
                    continue
                if progress:
                    progress("hash", 1)
            groups.extend(sorted(fg, key=lambda x: str(x.path)) for fg in by_full.values() if len(fg) > 1)
    return sorted(groups, key=lambda g: str(g[0].path))
