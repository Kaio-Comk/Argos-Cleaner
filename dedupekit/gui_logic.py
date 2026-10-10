"""GUI view-model: no Tk in here, so it is unit-testable.

A GroupVM is one group of duplicates as the user sees it, with each file marked KEEP or REMOVE.
The invariant that protects the user: **a group always keeps at least one file**.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .actions import Plan, pick_keeper
from .core import ScanResult
from .files import FileInfo

KIND_LABEL = {"exact": "Idêntico", "similar": "Parecido"}


@dataclass
class GroupVM:
    kind: str                                   # "exact" | "similar"
    files: list[FileInfo]
    remove: set[Path] = field(default_factory=set)

    def is_removed(self, path: Path) -> bool:
        return path in self.remove

    def toggle(self, path: Path) -> bool:
        """Flip KEEP/REMOVE for a file. Refuses (returns False) to remove the last kept file."""
        if path not in {f.path for f in self.files}:
            return False
        if path in self.remove:
            self.remove.discard(path)
            return True
        kept = [f for f in self.files if f.path not in self.remove]
        if len(kept) <= 1:
            return False
        self.remove.add(path)
        return True

    def auto_mark(self, policy: str) -> None:
        keeper = pick_keeper(self.files, policy)
        self.remove = {f.path for f in self.files if f.path != keeper.path}

    def clear_marks(self) -> None:
        self.remove = set()

    @property
    def removed_files(self) -> list[FileInfo]:
        return [f for f in self.files if f.path in self.remove]

    @property
    def reclaimable(self) -> int:
        return sum(f.size for f in self.removed_files)


def build_groups(result: ScanResult, policy: str, *, mark_similar: bool = False) -> list[GroupVM]:
    """Exact groups are auto-marked by `policy`. Similar groups start with everything KEPT
    unless `mark_similar` (they are a judgment call, so the user opts in)."""
    groups = [GroupVM("exact", list(g)) for g in result.exact]
    groups += [GroupVM("similar", list(g)) for g in (*result.similar_images, *result.similar_videos)]
    for g in groups:
        if g.kind == "exact" or mark_similar:
            g.auto_mark(policy)
    return groups


def remark(groups: Iterable[GroupVM], policy: str, *, mark_similar: bool) -> None:
    for g in groups:
        if g.kind == "exact" or mark_similar:
            g.auto_mark(policy)
        else:
            g.clear_marks()


def summary(groups: Iterable[GroupVM]) -> tuple[int, int, int]:
    """(files to remove, bytes reclaimable, how many of those come from 'similar' groups)."""
    n = size = similar = 0
    for g in groups:
        r = g.removed_files
        n += len(r)
        size += sum(f.size for f in r)
        if g.kind == "similar":
            similar += len(r)
    return n, size, similar


def to_plans(groups: Iterable[GroupVM]) -> list[Plan]:
    plans = []
    for g in groups:
        removed = g.removed_files
        if not removed:
            continue
        kept = [f for f in g.files if f.path not in g.remove]
        if not kept:                                   # defensive: never plan an empty keep set
            continue
        plans.append(Plan(g.kind, kept[0], removed))
    return plans


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
