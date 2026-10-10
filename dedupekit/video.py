"""Similar videos: the same video re-encoded, resized or re-muxed.

A fingerprint is the dHash of a few frames taken at fixed fractions of the duration
(10%, 30%, 50%, 70%, 90%) via ffmpeg. Two videos are similar when their durations are
close and the average Hamming distance over the frames is small. Needs ffmpeg/ffprobe.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from .files import FileInfo
from .perceptual import dhash_pixels, hamming

FRACTIONS = (0.1, 0.3, 0.5, 0.7, 0.9)


class VideoToolsMissing(RuntimeError):
    pass


def _tool(name: str, override: Optional[str]) -> str:
    path = override or shutil.which(name)
    if not path:
        raise VideoToolsMissing(f"{name} not found: install ffmpeg or pass its path")
    return path


def probe(path: Path, ffprobe: Optional[str] = None) -> tuple[float, int, int]:
    """(duration_seconds, width, height); zeros when unknown."""
    exe = _tool("ffprobe", ffprobe)
    try:
        out = subprocess.run(
            [exe, "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height:format=duration", "-of", "csv=p=0:s=,", str(path)],
            capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return (0.0, 0, 0)
    width = height = 0
    duration = 0.0
    for line in out.splitlines():
        parts = [p for p in line.strip().split(",") if p != ""]
        try:
            if len(parts) == 2 and "." not in parts[0]:
                width, height = int(parts[0]), int(parts[1])
            elif len(parts) == 1:
                duration = float(parts[0])
        except ValueError:
            continue
    return (duration, width, height)


def frame_hash(path: Path, at: float, ffmpeg: Optional[str] = None) -> Optional[int]:
    exe = _tool("ffmpeg", ffmpeg)
    try:
        raw = subprocess.run(
            [exe, "-v", "error", "-ss", f"{at:.3f}", "-i", str(path), "-frames:v", "1",
             "-vf", "scale=9:8:flags=area,format=gray", "-f", "rawvideo", "-"],
            capture_output=True, timeout=120).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return dhash_pixels(raw) if len(raw) == 72 else None


@dataclass
class Fingerprint:
    duration: float
    width: int
    height: int
    frames: tuple[int, ...]


def fingerprint(path: Path, *, ffmpeg: Optional[str] = None,
                ffprobe: Optional[str] = None) -> Optional[Fingerprint]:
    duration, w, h = probe(path, ffprobe)
    if duration <= 0:
        return None
    frames = []
    for frac in FRACTIONS:
        fh = frame_hash(path, duration * frac, ffmpeg)
        if fh is None:
            return None
        frames.append(fh)
    return Fingerprint(duration, w, h, tuple(frames))


def distance(a: Fingerprint, b: Fingerprint) -> float:
    return sum(hamming(x, y) for x, y in zip(a.frames, b.frames)) / len(a.frames)


def find_similar_videos(files: Iterable[FileInfo], threshold: float = 8.0, *,
                        duration_tolerance: float = 0.03, ffmpeg: Optional[str] = None,
                        ffprobe: Optional[str] = None,
                        progress: Optional[Callable[[str, int], None]] = None
                        ) -> list[list[FileInfo]]:
    """Groups of videos whose frames look alike. Videos are sorted by duration and only
    compared inside a +-`duration_tolerance` window, so cost is far below all-pairs."""
    _tool("ffmpeg", ffmpeg)
    _tool("ffprobe", ffprobe)
    prints: list[tuple[FileInfo, Fingerprint]] = []
    for fi in files:
        if fi.kind != "video":
            continue
        fp = fingerprint(fi.path, ffmpeg=ffmpeg, ffprobe=ffprobe)
        if progress:
            progress("video", 1)
        if fp:
            prints.append((fi, fp))
    prints.sort(key=lambda t: t[1].duration)

    parent = list(range(len(prints)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, (_, a) in enumerate(prints):
        for j in range(i + 1, len(prints)):
            b = prints[j][1]
            if b.duration > a.duration * (1 + duration_tolerance) + 0.5:
                break
            if distance(a, b) <= threshold:
                parent[find(i)] = find(j)

    groups: dict[int, list[FileInfo]] = {}
    for i, (fi, _) in enumerate(prints):
        groups.setdefault(find(i), []).append(fi)
    return sorted((sorted(g, key=lambda x: str(x.path)) for g in groups.values() if len(g) > 1),
                  key=lambda g: str(g[0].path))
