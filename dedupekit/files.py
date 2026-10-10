"""Walk the roots and classify files. Symlinks are never followed; hard links to the same
inode are one file, not duplicates of each other."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".webm", ".m4v", ".mpg", ".mpeg", ".3gp", ".ts"}


@dataclass(frozen=True)
class FileInfo:
    path: Path
    size: int
    mtime: float
    inode_key: tuple          # (st_dev, st_ino); (0, 0) when the OS does not provide it

    @property
    def kind(self) -> str:
        ext = self.path.suffix.lower()
        return "image" if ext in IMAGE_EXT else "video" if ext in VIDEO_EXT else "other"


def walk(roots: Iterable[Path], *, min_size: int = 1,
         skip_dirs: Iterable[str] = (".git", "$RECYCLE.BIN", "System Volume Information"),
         exclude: Iterable[Path] = ()) -> Iterator[FileInfo]:
    """Yield regular files once each (hard links collapsed). `exclude` directories are pruned,
    e.g. the quarantine folder, so a second run never re-scans what the first one moved."""
    skip = set(skip_dirs)
    excluded = [Path(e).resolve() for e in exclude]
    seen_inodes: set = set()
    for root in roots:
        root = Path(root)
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            here = Path(dirpath).resolve()
            dirnames[:] = [d for d in dirnames if d not in skip
                           and not any((here / d).resolve() == e for e in excluded)]
            for name in filenames:
                p = Path(dirpath) / name
                try:
                    if p.is_symlink():
                        continue
                    st = p.stat()
                except OSError:
                    continue
                if not p.is_file() or st.st_size < min_size:
                    continue
                key = (st.st_dev, st.st_ino)
                if key != (0, 0):
                    if key in seen_inodes:
                        continue
                    seen_inodes.add(key)
                yield FileInfo(p, st.st_size, st.st_mtime, key)
