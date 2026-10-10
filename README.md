# dedupekit

Find **duplicate photos and videos by content, not by name**, and clean them up
safely. Pure Python, runs locally, nothing is uploaded anywhere.

| It finds | How |
|---|---|
| **Exact duplicates** (any file type) | size, then partial hash, then full hash (BLAKE3 or SHA-256). Files are only read in full when they could really match. |
| **Similar photos** | perceptual hash (dHash) that survives resizing, recompression and format changes (`photo.png` vs a 70% JPEG thumbnail). |
| **Similar videos** | fingerprint of 5 frames per video (via ffmpeg): the same video re-encoded, resized or re-muxed. |

## Windows app (no Python needed)

Download **`dedupekit.exe`** from the [latest release](https://github.com/Kaio-Comk/dedupekit/releases/latest)
and double-click it. The window walks you through four steps:

1. add the folders to scan;
2. choose options (similar photos / similar videos, sensitivity, which copy to keep);
3. review the groups (double-click a file to flip *Keep* / *Remove*; a preview shows the photo);
4. clean: move to a **quarantine folder** or the **Recycle Bin**, always after a confirmation.

Notes:
- For *similar videos*, put `ffmpeg.exe` and `ffprobe.exe` in the same folder as `dedupekit.exe`.
- Windows SmartScreen may warn about an unsigned app. The exe is built by GitHub Actions from this
  source (see `.github/workflows/release.yml`), and a `.sha256` file is published next to it so you
  can verify the download: `certutil -hashfile dedupekit.exe SHA256`.
- From source: `pip install "dedupekit[all]"` then `dedupekit-gui`.

## Safety first

- **Dry run is the default.** Nothing moves unless you pass `--apply`.
- Files are **never deleted for good**: they go to a **quarantine folder** (mirroring the
  original path, so you can restore them) or to the system **trash** (`--action trash`).
- At least one file of every group always stays.
- A file that changed after the scan is skipped, not touched.
- Hard links to the same file are not duplicates; symlinks are never followed.
- Similar (not byte-identical) groups are only touched with `--include-similar`.

## Install

```bash
pip install git+https://github.com/Kaio-Comk/dedupekit.git
pip install "dedupekit[all]"    # optional: Pillow (similar photos), blake3 (speed), send2trash (trash)
```

Similar videos need `ffmpeg` and `ffprobe` on your `PATH`.

## Use

```bash
# 1. Look first (changes nothing). Add --json to save the full list.
dedupekit scan D:\Photos --similar-images --similar-videos --json report.json

# 2. Dry run of the cleanup: shows what WOULD move.
dedupekit clean D:\Photos --keep highest-res

# 3. Do it (exact duplicates go to .\dedupekit-quarantine, nothing is deleted).
dedupekit clean D:\Photos --keep highest-res --apply

# Also act on similar photos/videos, sending them to the Recycle Bin instead:
dedupekit clean D:\Photos --similar-images --similar-videos --include-similar \
    --keep highest-res --action trash --apply
```

`--keep` chooses which file of each group survives: `highest-res` (default; falls back to
file size), `largest`, `smallest`, `oldest`, `newest` or `shortest-path`.

Tuning: `--image-threshold` (default 5 differing bits of 64; lower is stricter),
`--video-threshold` (default 8 bits per frame), `--min-size`, `--exclude PATH`.

After checking the quarantine folder, delete it yourself when you are sure.

## Honest limits

- "Similar" is a judgment call: very different crops, heavy edits or very short videos can be
  missed, and near-identical shots (burst photos) can be grouped. That is why similar groups need
  `--include-similar`, and why the quarantine exists. Review the `scan` report first.
- Videos are compared by a handful of frames and their duration; two different videos with the
  same duration and a nearly black/white look could be confused: lower `--video-threshold`.
- Exact-duplicate detection is byte-for-byte reliable.

## How it relates to other tools

dedupekit is a small, scriptable, MIT-licensed take on ideas proven by
[fclones](https://github.com/pkolaczk/fclones) (staged hashing),
[Czkawka](https://github.com/qarmin/czkawka) and
[dupeGuru](https://github.com/arsenetar/dupeguru) (similar images/videos),
[imagededup](https://github.com/idealo/imagededup) (perceptual hashing) and
[VideoCull](https://github.com/StippieDot/VideoCull) (video de-duplication). **No code was copied**
from them; they are excellent, and for huge libraries or a GUI you should look at them too.

## Tests

```bash
pip install -e ".[dev]" && pytest      # video tests need ffmpeg and skip without it
```

## License

MIT
