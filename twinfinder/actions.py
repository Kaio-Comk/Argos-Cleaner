"""Choose which file of a group to keep and (optionally) get rid of the rest SAFELY.

Safety rules, all enforced here:
  * dry run is the default: nothing moves unless `apply=True`;
  * files are never deleted for good: they go to the system trash (needs `send2trash`)
    or to a quarantine folder that mirrors their original path;
  * at least one file of each group always stays;
  * a file changed since the scan (size/mtime) is skipped, not touched.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .files import FileInfo
from .perceptual import image_size

POLICIES = ("largest", "smallest", "oldest", "newest", "highest-res", "shortest-path")


def _resolution(fi: FileInfo) -> int:
    if fi.kind == "image":
        w, h = image_size(fi.path)
        return w * h
    if fi.kind == "video":
        try:
            from .video import probe
            _d, w, h = probe(fi.path)
            return w * h
        except Exception:  # noqa: BLE001 - ffprobe missing: fall back to size
            return 0
    return 0


def pick_keeper(group: list[FileInfo], policy: str) -> FileInfo:
    if policy not in POLICIES:
        raise ValueError(f"unknown policy {policy!r}; choose one of {POLICIES}")
    tie = lambda f: (len(str(f.path)), str(f.path))        # stable, deterministic tie-break
    if policy == "largest":
        return min(group, key=lambda f: (-f.size, *tie(f)))
    if policy == "smallest":
        return min(group, key=lambda f: (f.size, *tie(f)))
    if policy == "oldest":
        return min(group, key=lambda f: (f.mtime, *tie(f)))
    if policy == "newest":
        return min(group, key=lambda f: (-f.mtime, *tie(f)))
    if policy == "shortest-path":
        return min(group, key=tie)
    return min(group, key=lambda f: (-_resolution(f), -f.size, *tie(f)))      # highest-res


@dataclass
class Plan:
    kind: str                       # "exact" | "similar"
    keep: FileInfo
    remove: list[FileInfo]

    @property
    def reclaimable(self) -> int:
        return sum(f.size for f in self.remove)


def make_plans(groups: list[list[FileInfo]], kind: str, policy: str) -> list[Plan]:
    plans = []
    for g in groups:
        keep = pick_keeper(g, policy)
        rest = [f for f in g if f.path != keep.path]
        if rest:
            plans.append(Plan(kind, keep, rest))
    return plans


@dataclass
class Result:
    path: Path
    status: str                     # moved | trashed | skipped | dry-run | error
    detail: str = ""


@dataclass
class Outcome:
    results: list[Result] = field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(1 for r in self.results if r.status == status)


def _unchanged(fi: FileInfo) -> bool:
    try:
        st = fi.path.stat()
    except OSError:
        return False
    return st.st_size == fi.size and abs(st.st_mtime - fi.mtime) < 1e-6


def _quarantine_target(fi: FileInfo, roots: list[Path], qdir: Path) -> Path:
    resolved = fi.path.resolve()
    for i, root in enumerate(roots):
        try:
            rel = resolved.relative_to(root.resolve())
            break
        except ValueError:
            continue
    else:
        i, rel = 0, Path(resolved.name)
    target = qdir / f"root{i}" / rel
    n = 1
    while target.exists():
        target = target.with_name(f"{target.stem}.{n}{target.suffix}")
        n += 1
    return target


def apply_plans(plans: list[Plan], *, action: str = "quarantine", roots: list[Path],
                quarantine_dir: Optional[Path] = None, apply: bool = False,
                trash: Optional[Callable[[str], None]] = None) -> Outcome:
    """Execute (or simulate) the plans. `action`: 'quarantine' or 'trash'."""
    if action not in ("quarantine", "trash"):
        raise ValueError("action must be 'quarantine' or 'trash'")
    if action == "quarantine" and quarantine_dir is None:
        raise ValueError("quarantine_dir is required for the quarantine action")
    if action == "trash" and trash is None:
        try:
            from send2trash import send2trash as trash      # type: ignore
        except ImportError as exc:
            raise RuntimeError("the trash action needs send2trash: pip install send2trash") from exc

    out = Outcome()
    for plan in plans:
        if not plan.keep.path.exists():
            out.results.extend(Result(f.path, "skipped", "keeper missing") for f in plan.remove)
            continue
        for fi in plan.remove:
            if fi.path == plan.keep.path:
                out.results.append(Result(fi.path, "skipped", "is the keeper"))
            elif not _unchanged(fi):
                out.results.append(Result(fi.path, "skipped", "changed since the scan"))
            elif not apply:
                out.results.append(Result(fi.path, "dry-run"))
            else:
                try:
                    if action == "trash":
                        trash(str(fi.path))
                        out.results.append(Result(fi.path, "trashed"))
                    else:
                        target = _quarantine_target(fi, roots, Path(quarantine_dir))
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(fi.path), str(target))
                        out.results.append(Result(fi.path, "moved", str(target)))
                except OSError as exc:
                    out.results.append(Result(fi.path, "error", str(exc)))
    return out
