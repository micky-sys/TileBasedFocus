"""Inspect ONE focus crop: characterise the 'checkerboard' scale.

Loads a raw-Bayer uint16 focus crop, reports shape/stats, saves a zoomed 160x160
center patch of both the raw crop and the green sub-grid (stretched to 8-bit), and
prints the mean absolute difference between adjacent columns/rows at a few pixel
strides -- a periodic block pattern shows up as a spike at its period.

Usage:  python tools/af_crop_inspect.py "E:/Project/focus/<one>.tif"
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np


def main() -> int:
    path = Path(sys.argv[1])
    bayer = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if bayer is None:
        print(f"unreadable: {path}")
        return 1
    print(f"file   : {path.name}")
    print(f"shape  : {bayer.shape}  dtype={bayer.dtype}")
    print(f"range  : min={bayer.min()} max={bayer.max()} mean={bayer.mean():.1f}")

    h, w = bayer.shape[:2]
    cy, cx = h // 2, w // 2
    raw_patch = bayer[cy:cy + 160, cx:cx + 160].astype(np.float32)
    green = bayer[0::2, 1::2].astype(np.float32)
    gh, gw = green.shape
    green_patch = green[gh // 2:gh // 2 + 160, gw // 2:gw // 2 + 160]

    def stretch(a: np.ndarray) -> np.ndarray:
        lo, hi = np.percentile(a, (1, 99))
        hi = hi if hi > lo else lo + 1
        return np.clip((a - lo) / (hi - lo) * 255, 0, 255).astype(np.uint8)

    out = path.parent / "inspect"
    out.mkdir(exist_ok=True)
    cv2.imwrite(str(out / "raw_zoom.png"), cv2.resize(
        stretch(raw_patch), (480, 480), interpolation=cv2.INTER_NEAREST))
    cv2.imwrite(str(out / "green_zoom.png"), cv2.resize(
        stretch(green_patch), (480, 480), interpolation=cv2.INTER_NEAREST))

    # Periodicity probe on a mid row of the green plane: autocorrelation of the
    # 1st difference; a block period P shows a peak at lag P.
    row = green[gh // 2].astype(np.float32)
    print("\ngreen row mean abs-diff by column stride (period hint):")
    for p in (1, 2, 3, 4, 6, 8, 16):
        print(f"  stride {p:>2}: mean |x[i]-x[i+{p}]| = "
              f"{np.mean(np.abs(row[:-p] - row[p:])):.1f}")
    print(f"\nzoom PNGs -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
