"""Review a folder of AF focus-view crops.

Reads every ``*.tif`` focus crop in a folder (raw Bayer uint16 as written by the
capture app's "Save focus view"), scores each with the SAME metric autofocus
uses (:func:`focus_measure_full_res` on the green sub-grid), and writes a
mosaic-free green PNG per frame so the hunt can be eyeballed. Prints a score
timeline (ordered by filename = capture time) and flags the peak vs the last
frame -- the "reached a good picture then moved away" check.

Usage:
    python tools/af_crop_review.py "E:/Project/focus" [--out DIR]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))          # for `sharpness` (repo root)
sys.path.insert(0, str(ROOT / "host"))  # for `capture.autofocus`
from capture.autofocus import focus_measure_full_res  # noqa: E402


def _green_png(bayer: np.ndarray) -> np.ndarray:
    """Green sub-grid, stretched to 8-bit for viewing (percentile clip)."""
    green = bayer[0::2, 1::2].astype(np.float32)
    lo, hi = np.percentile(green, (1, 99))
    if hi <= lo:
        hi = lo + 1.0
    out = np.clip((green - lo) / (hi - lo) * 255.0, 0, 255)
    return out.astype(np.uint8)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("folder", help="folder of *.tif focus crops")
    ap.add_argument("--out", default=None, help="output dir (default: <folder>/review)")
    args = ap.parse_args()

    folder = Path(args.folder)
    files = sorted(folder.glob("*.tif"))
    if not files:
        print(f"no *.tif in {folder}")
        return 1
    out = Path(args.out) if args.out else folder / "review"
    out.mkdir(parents=True, exist_ok=True)

    scores: list[float] = []
    print(f"\n{'#':>3}  {'time':<12}  {'score':>8}")
    print("-" * 30)
    for i, f in enumerate(files):
        bayer = cv2.imread(str(f), cv2.IMREAD_UNCHANGED)
        if bayer is None:
            print(f"{i:>3}  {f.name:<12}  <unreadable>")
            scores.append(float("nan"))
            continue
        score = focus_measure_full_res(bayer)
        scores.append(score)
        stamp = f.stem.split("_focus_")[-1]
        cv2.imwrite(str(out / f"{i:03d}_{stamp}_s{score:.1f}.png"), _green_png(bayer))
        print(f"{i:>3}  {stamp:<12}  {score:>8.2f}")

    arr = np.array(scores, dtype=np.float32)
    valid = ~np.isnan(arr)
    if valid.any():
        peak_i = int(np.nanargmax(arr))
        print("-" * 30)
        print(f"peak : #{peak_i} score={arr[peak_i]:.2f}")
        print(f"last : #{len(arr) - 1} score={arr[-1]:.2f}")
        drop = arr[peak_i] - arr[-1]
        pct = 100.0 * drop / arr[peak_i] if arr[peak_i] else 0.0
        print(f"final is {pct:.0f}% below peak "
              f"({'WALKED AWAY from sharper' if pct > 10 else '≈ at peak'})")
    print(f"\ngreen PNGs -> {out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
