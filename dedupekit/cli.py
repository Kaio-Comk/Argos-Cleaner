"""Command line: `dedupekit scan` (read only) and `dedupekit clean` (dry run unless --apply)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .actions import POLICIES, apply_plans, make_plans
from .core import ScanOptions, scan_paths


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024


def _add_scan_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("paths", nargs="+", type=Path, help="folders to scan")
    p.add_argument("--similar-images", action="store_true",
                   help="also find the same photo resized/recompressed (needs Pillow)")
    p.add_argument("--similar-videos", action="store_true",
                   help="also find the same video re-encoded (needs ffmpeg)")
    p.add_argument("--image-threshold", type=int, default=5,
                   help="max differing bits of 64 for similar images (default 5; lower = stricter)")
    p.add_argument("--video-threshold", type=float, default=8.0,
                   help="max average differing bits per frame for similar videos (default 8)")
    p.add_argument("--min-size", type=int, default=1, help="ignore files smaller than N bytes")
    p.add_argument("--exclude", action="append", default=[], type=Path,
                   help="folder to skip (repeatable)")


def _scan(args, exclude_extra=()) -> dict:
    res = scan_paths(args.paths, ScanOptions(
        similar_images=args.similar_images, similar_videos=args.similar_videos,
        image_threshold=args.image_threshold, video_threshold=args.video_threshold,
        min_size=args.min_size, exclude=[*args.exclude, *exclude_extra]))
    return {"files_scanned": res.files_scanned, "exact": res.exact,
            "similar_images": res.similar_images, "similar_videos": res.similar_videos}


def _group_json(groups):
    return [[{"path": str(f.path), "size": f.size} for f in g] for g in groups]


def _report(res: dict) -> None:
    for title, key in (("Exact duplicates", "exact"), ("Similar images", "similar_images"),
                       ("Similar videos", "similar_videos")):
        groups = res[key]
        if key != "exact" and not groups:
            continue
        waste = sum(f.size for g in groups for f in g[1:])
        print(f"\n== {title}: {len(groups)} group(s), ~{_human(waste)} reclaimable")
        for g in groups[:20]:
            for i, f in enumerate(g):
                print(f"  {'*' if i == 0 else ' '} {f.path}  ({_human(f.size)})")
            print()
        if len(groups) > 20:
            print(f"  ... and {len(groups) - 20} more group(s); use --json for the full list")
    print(f"\nScanned {res['files_scanned']} file(s).")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="dedupekit", description=__doc__)
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("scan", help="find duplicates; changes nothing")
    _add_scan_args(sp)
    sp.add_argument("--json", type=Path, help="write the full result to this JSON file")

    cp = sub.add_parser("clean", help="remove duplicates safely (dry run unless --apply)")
    _add_scan_args(cp)
    cp.add_argument("--keep", choices=POLICIES, default="highest-res",
                    help="which file of each group to keep (default highest-res)")
    cp.add_argument("--action", choices=("quarantine", "trash"), default="quarantine")
    cp.add_argument("--quarantine-dir", type=Path, default=Path("dedupekit-quarantine"))
    cp.add_argument("--include-similar", action="store_true",
                    help="also act on similar (not byte-identical) groups; exact is always included")
    cp.add_argument("--apply", action="store_true", help="really move the files (default: dry run)")

    args = ap.parse_args(argv)

    if args.cmd == "scan":
        res = _scan(args)
        _report(res)
        if args.json:
            args.json.write_text(json.dumps(
                {"files_scanned": res["files_scanned"], "exact": _group_json(res["exact"]),
                 "similar_images": _group_json(res["similar_images"]),
                 "similar_videos": _group_json(res["similar_videos"])}, indent=2), encoding="utf-8")
            print(f"Full result written to {args.json}")
        return 0

    # clean
    qdir = args.quarantine_dir.resolve()
    res = _scan(args, exclude_extra=[qdir])
    plans = make_plans(res["exact"], "exact", args.keep)
    if args.include_similar:
        plans += make_plans(res["similar_images"], "similar", args.keep)
        plans += make_plans(res["similar_videos"], "similar", args.keep)
    total = sum(p.reclaimable for p in plans)
    print(f"{len(plans)} group(s); {sum(len(p.remove) for p in plans)} file(s) to remove, "
          f"~{_human(total)} reclaimable. Keep policy: {args.keep}.")
    try:
        out = apply_plans(plans, action=args.action, roots=list(args.paths),
                          quarantine_dir=qdir, apply=args.apply)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for r in out.results[:40]:
        print(f"  [{r.status}] {r.path}" + (f" -> {r.detail}" if r.detail else ""))
    if len(out.results) > 40:
        print(f"  ... {len(out.results) - 40} more")
    if args.apply:
        print(f"\nDone: moved {out.count('moved')}, trashed {out.count('trashed')}, "
              f"skipped {out.count('skipped')}, errors {out.count('error')}.")
        if args.action == "quarantine":
            print(f"Nothing was deleted: look in {qdir} and delete that folder when you are sure.")
    else:
        print("\nDry run: nothing was changed. Re-run with --apply to do it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
