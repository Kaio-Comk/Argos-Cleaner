"""Google Photos Takeout: put the real date, GPS and description back into the photos.

A Takeout export keeps every photo next to a JSON "sidecar" (`IMG_1.jpg.supplemental-metadata.json`)
holding the ORIGINAL data (`photoTakenTime`, `geoData`, `description`), while the photo itself often
loses it (its file date becomes the day you copied it). This module pairs each media file with
its sidecar and can then:

  * set the file's modified/created date to the real one (lossless, no extra tool needed);
  * write the date / GPS / description INTO the file with ExifTool (optional, if installed);
  * move the sidecars out of the way once they are merged.

Pairing handles the quirks of Takeout: the new `.supplemental-metadata` suffix, the old plain `.json`,
names truncated by Google, the number moved by the "(1)" suffix (`a(1).jpg` -> `a.jpg(1).json`),
edited copies (`a-edited.jpg` shares `a.jpg`'s sidecar) and, as a last resort, the `title` field
inside the JSON. The approach is inspired by GooglePhotosTakeoutHelper (Apache-2.0); no code is copied.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

from .files import IMAGE_EXT, VIDEO_EXT

MEDIA_EXT = IMAGE_EXT | VIDEO_EXT
SIDE = ".supplemental-metadata"
# Names Google appends to edited copies (lowercase). Add more languages if needed.
EDITED_MARKS = ("-edited", "-effects", "-smile", "-mix", "-edytowane", "-bearbeitet", "-bewerkt",
                "-編集済み", "-modificato", "-modifié", "-ha editado", "-editat")
_TRUNC_MIN = 40               # only accept a truncated sidecar stem this long (avoids false matches)


@dataclass
class Sidecar:
    path: Path
    title: str = ""
    taken: Optional[datetime] = None          # UTC
    lat: Optional[float] = None
    lon: Optional[float] = None
    alt: Optional[float] = None
    description: str = ""


def parse_sidecar(path: Path) -> Optional[Sidecar]:
    """Read a sidecar. Returns None for unreadable / unrelated JSON."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or "title" not in data:
        return None
    sc = Sidecar(Path(path), title=str(data.get("title") or ""),
                 description=str(data.get("description") or "").strip())
    try:
        ts = int(str((data.get("photoTakenTime") or {}).get("timestamp")))
        sc.taken = datetime.fromtimestamp(ts, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    for key in ("geoData", "geoDataExif"):          # geoData is the edited location; EXIF as fallback
        g = data.get(key) or {}
        try:
            lat, lon = float(g.get("latitude", 0)), float(g.get("longitude", 0))
        except (TypeError, ValueError):
            continue
        if (lat, lon) != (0.0, 0.0) and -90 <= lat <= 90 and -180 <= lon <= 180:
            sc.lat, sc.lon = lat, lon
            try:
                sc.alt = float(g.get("altitude"))
            except (TypeError, ValueError):
                sc.alt = None
            break
    return sc


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _strip_edited(name: str) -> str:
    stem, dot, ext = name.rpartition(".")
    base = _nfc(stem if dot else name).lower()
    for mark in EDITED_MARKS:
        m = re.search(re.escape(mark) + r"(\(\d+\))?$", base)
        if m:
            cut = len(stem if dot else name) - len(m.group(0))
            return (stem if dot else name)[:cut] + (m.group(1) or "") + (dot + ext if dot else "")
    return name


def candidate_names(media_name: str) -> list[str]:
    """Sidecar file names (exact, lowercase) that may belong to `media_name`, best first."""
    names = {media_name, _strip_edited(media_name)}
    out: list[str] = []

    def add(base: str) -> None:
        stem, dot, ext = base.rpartition(".")
        variants = [f"{base}{SIDE}.json", f"{base}.json"]
        m = re.search(r"\((\d+)\)$", stem) if dot else None       # a(1).jpg -> a.jpg(1).json
        if m:
            swapped = stem[: m.start()] + dot + ext
            variants += [f"{swapped}{SIDE}({m.group(1)}).json", f"{swapped}({m.group(1)}).json"]
        if dot:                                                     # uploaded without extension
            variants.append(f"{stem}.json")
        for v in variants:
            if v.lower() not in out:
                out.append(v.lower())

    for n in names:
        add(n)
    return out


def match_dir(folder_files: list[Path]) -> tuple[list[tuple[Path, Path]], list[Path], list[Path]]:
    """Pair media with sidecars among the files of ONE directory.
    Returns (pairs[(media, sidecar)], media_without_sidecar, orphan_sidecars)."""
    jsons = {p.name.lower(): p for p in folder_files if p.suffix.lower() == ".json"}
    media = [p for p in folder_files if p.suffix.lower() in MEDIA_EXT]
    used: set[Path] = set()
    pairs: list[tuple[Path, Path]] = []
    rest: list[Path] = []

    for m in sorted(media):
        found = next((jsons[c] for c in candidate_names(m.name) if c in jsons), None)
        if found is None:                                  # truncated sidecar names
            full = {f"{m.name}{SIDE}".lower(), f"{_strip_edited(m.name)}{SIDE}".lower()}
            for jn, jp in jsons.items():
                stem = jn[:-5]
                if len(stem) >= _TRUNC_MIN and any(f.startswith(stem) and f != stem for f in full):
                    found = jp
                    break
        if found is None:
            rest.append(m)
        else:
            pairs.append((m, found))
            used.add(found)

    if rest:                                               # last resort: the JSON's own `title`
        by_title: dict[str, list[Path]] = {}
        for jp in jsons.values():
            sc = parse_sidecar(jp)
            if sc and sc.title:
                by_title.setdefault(_nfc(sc.title).lower(), []).append(jp)
        still = []
        for m in rest:
            hits = by_title.get(_nfc(m.name).lower()) or by_title.get(_nfc(_strip_edited(m.name)).lower())
            if hits and len(hits) == 1:                    # ambiguous titles are skipped, not guessed
                pairs.append((m, hits[0]))
                used.add(hits[0])
            else:
                still.append(m)
        rest = still
    orphans = sorted(p for p in jsons.values() if p not in used)
    return pairs, rest, orphans


@dataclass
class TakeoutScan:
    pairs: list[tuple[Path, Path]] = field(default_factory=list)
    media_without_sidecar: list[Path] = field(default_factory=list)
    orphan_sidecars: list[Path] = field(default_factory=list)


def scan_takeout(roots: Iterable[Path], *, recursive: bool = True) -> TakeoutScan:
    import os
    res = TakeoutScan()
    for root in roots:
        for dirpath, _dirs, files in os.walk(root) if recursive else [(str(root), [], os.listdir(root))]:
            paths = [Path(dirpath) / f for f in files if (Path(dirpath) / f).is_file()]
            pairs, rest, orphans = match_dir(paths)
            res.pairs += pairs
            res.media_without_sidecar += rest
            res.orphan_sidecars += orphans
    return res


# ── applying ───────────────────────────────────────────────────────────────────
def find_exiftool(explicit: Optional[str] = None) -> Optional[str]:
    if explicit:
        return explicit if Path(explicit).exists() else None
    exe_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
    for cand in (exe_dir / "exiftool.exe", exe_dir / "exiftool(-k).exe"):
        if cand.exists():
            return str(cand)
    return shutil.which("exiftool")


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y:%m:%d %H:%M:%S")


def exiftool_args(sc: Sidecar, kind: str, *, only_missing: bool = True) -> list[str]:
    """ExifTool arguments to embed the sidecar data. `only_missing` never overwrites a tag the
    file already has (`-wm cg`: create-only)."""
    args: list[str] = ["-wm", "cg"] if only_missing else []
    if sc.taken:
        d = _fmt(sc.taken)
        if kind == "video":
            args += ["-api", "QuickTimeUTC=1", f"-QuickTime:CreateDate={d}", f"-QuickTime:ModifyDate={d}",
                     f"-QuickTime:MediaCreateDate={d}", f"-QuickTime:TrackCreateDate={d}"]
        else:
            args += [f"-DateTimeOriginal={d}", f"-CreateDate={d}", "-OffsetTimeOriginal=+00:00"]
    if kind != "video":
        if sc.lat is not None and sc.lon is not None:
            args += [f"-GPSLatitude={abs(sc.lat)}", f"-GPSLatitudeRef={'N' if sc.lat >= 0 else 'S'}",
                     f"-GPSLongitude={abs(sc.lon)}", f"-GPSLongitudeRef={'E' if sc.lon >= 0 else 'W'}"]
            if sc.alt is not None:
                args += [f"-GPSAltitude={abs(sc.alt)}", f"-GPSAltitudeRef={0 if sc.alt >= 0 else 1}"]
        if sc.description:
            args += [f"-ImageDescription={sc.description}", f"-XMP-dc:Description={sc.description}"]
    return args


def set_file_times(path: Path, when: datetime) -> None:
    """Set modified + accessed (and, on Windows, created) time to `when`."""
    import os
    ts = when.timestamp()
    os.utime(path, (ts, ts))
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.windll.kernel32
            k32.CreateFileW.restype = wintypes.HANDLE
            h = k32.CreateFileW(str(path), 0x100, 0, None, 3, 0x80, None)   # FILE_WRITE_ATTRIBUTES
            if h in (None, wintypes.HANDLE(-1).value):
                return
            ft = wintypes.FILETIME(*divmod(int(ts * 10_000_000) + 116444736000000000, 1 << 32)[::-1])
            k32.SetFileTime(h, ctypes.byref(ft), None, None)
            k32.CloseHandle(h)
        except (OSError, AttributeError, ValueError):
            pass


@dataclass
class ApplyOptions:
    set_dates: bool = True
    exif: bool = False
    exiftool: Optional[str] = None
    out_dir: Optional[Path] = None           # write COPIES here, originals untouched (exif mode)
    keep_exiftool_backup: bool = True        # in-place: leave ExifTool's *_original backups
    only_missing: bool = True
    move_sidecars_to: Optional[Path] = None


@dataclass
class ApplyReport:
    dates_set: int = 0
    exif_written: int = 0
    sidecars_moved: int = 0
    errors: list[str] = field(default_factory=list)


def apply_takeout(scan: TakeoutScan, opts: ApplyOptions, *, apply: bool = False,
                  progress: Optional[Callable[[int, int], None]] = None) -> ApplyReport:
    rep = ApplyReport()
    exe = find_exiftool(opts.exiftool) if opts.exif else None
    if opts.exif and not exe:
        raise RuntimeError("ExifTool not found: put exiftool.exe next to the program or pass its path")
    cache: dict[Path, Optional[Sidecar]] = {}
    ok_sidecars: dict[Path, bool] = {}
    for i, (media, side) in enumerate(scan.pairs, 1):
        if progress:
            progress(i, len(scan.pairs))
        sc = cache.setdefault(side, parse_sidecar(side))
        if sc is None:
            rep.errors.append(f"{media.name}: unreadable sidecar")
            ok_sidecars[side] = False
            continue
        kind = "video" if media.suffix.lower() in VIDEO_EXT else "image"
        target = media
        good = True
        try:
            if opts.exif and sc:
                if apply:
                    if opts.out_dir:
                        target = Path(opts.out_dir) / media.name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(media, target)
                    cmd = [exe, "-m", "-q", "-q", *exiftool_args(sc, kind, only_missing=opts.only_missing)]
                    if opts.out_dir or not opts.keep_exiftool_backup:
                        cmd.append("-overwrite_original")
                    kw = {}
                    if sys.platform == "win32":
                        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
                    r = subprocess.run([*cmd, str(target)], capture_output=True, text=True, timeout=300, **kw)
                    if r.returncode != 0:
                        raise RuntimeError(r.stderr.strip() or f"exiftool exit {r.returncode}")
                rep.exif_written += 1
            if opts.set_dates and sc.taken:
                if apply:
                    set_file_times(target, sc.taken)
                rep.dates_set += 1
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            good = False
            rep.errors.append(f"{media.name}: {exc}")
        ok_sidecars[side] = ok_sidecars.get(side, True) and good

    if opts.move_sidecars_to:
        dest = Path(opts.move_sidecars_to)
        for side, ok in ok_sidecars.items():
            if not ok:
                continue
            if apply:
                dest.mkdir(parents=True, exist_ok=True)
                target = dest / side.name
                n = 1
                while target.exists():
                    target = dest / f"{side.stem}.{n}{side.suffix}"
                    n += 1
                try:
                    shutil.move(str(side), str(target))
                except OSError as exc:
                    rep.errors.append(f"{side.name}: {exc}")
                    continue
            rep.sidecars_moved += 1
    return rep
