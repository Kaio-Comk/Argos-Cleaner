"""Scan orchestration shared by the command line and the GUI."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .exact import find_exact
from .files import FileInfo, walk
from .perceptual import find_similar_images

Progress = Callable[[str, int], None]


class Cancelled(Exception):
    """Raised by a progress callback to abort a scan cleanly."""


@dataclass
class ScanOptions:
    similar_images: bool = False
    similar_videos: bool = False
    image_threshold: int = 5
    video_threshold: float = 8.0
    min_size: int = 1
    exclude: list[Path] = field(default_factory=list)


@dataclass
class ScanResult:
    files_scanned: int
    exact: list[list[FileInfo]]
    similar_images: list[list[FileInfo]]
    similar_videos: list[list[FileInfo]]


def scan_paths(paths: list[Path], opts: ScanOptions, progress: Optional[Progress] = None) -> ScanResult:
    """Walk, then exact -> similar images -> similar videos. `progress(stage, n)` is called per
    file with stage in {"walk", "hash", "image", "video"}; raise `Cancelled` from it to stop."""
    tick = progress or (lambda stage, n: None)
    files: list[FileInfo] = []
    for fi in walk(paths, min_size=opts.min_size, exclude=opts.exclude):
        files.append(fi)
        if len(files) % 200 == 0:
            tick("walk", 200)
    exact = find_exact(files, progress=tick)
    copies = {f.path for g in exact for f in g[1:]}
    rest = [f for f in files if f.path not in copies]       # exact copies need no second look
    images = find_similar_images(rest, opts.image_threshold, progress=tick) if opts.similar_images else []
    videos = []
    if opts.similar_videos:
        from .video import find_similar_videos
        videos = find_similar_videos(rest, opts.video_threshold, progress=tick)
    return ScanResult(len(files), exact, images, videos)
