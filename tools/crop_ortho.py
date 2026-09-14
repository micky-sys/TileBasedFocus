"""Crop a tall orthophoto to a fixed row count, streaming strip by strip.

OrthoSim tiles the ortho modulo its length and paints a magenta seam at the join,
so two orthos of different length seam at different rates: at 68.59 m/s Bnei Brak
(3.89 km) wraps every 57 s but Pardes Hana (6.37 km) every 93 s, against an AF run
that takes ~52 s. Comparing scenes of different length would therefore compare
seam rates as much as terrain. Matching the row count makes the seam a controlled
constant.

    py -3 tools/crop_ortho.py "Running TIF/Pardes_hana.tif" ^
        C:/OrthoBench/Pardes_hana_matched.tif --rows 77786

Output is uncompressed 3-channel RGB: OrthoSim loads via cv2.IMREAD_COLOR, which
discards alpha and decompresses anyway. Writing off the Google Drive volume keeps
a multi-GB sync from competing with the bench run.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np
import tifffile
import zarr

BLOCK_ROWS = 2048  # ~0.13 GB per block at 15 k columns RGBA


def crop(src: str, dst: str, start: int, rows: int, block: int = BLOCK_ROWS) -> tuple[int, int]:
    """Copy ``rows`` rows starting at ``start`` from ``src`` into ``dst``. Returns (rows, width)."""
    store = tifffile.imread(src, aszarr=True)
    try:
        z = zarr.open(store, mode="r")
        h, w = int(z.shape[0]), int(z.shape[1])
        if start < 0 or rows <= 0 or start + rows > h:
            raise SystemExit(f"source has {h} rows; requested {start}..{start + rows} overruns it")

        os.makedirs(os.path.dirname(os.path.abspath(dst)) or ".", exist_ok=True)
        out = tifffile.memmap(dst, shape=(rows, w, 3), dtype=np.uint8, photometric="rgb")
        try:
            t0 = time.time()
            for y in range(0, rows, block):
                n = min(block, rows - y)
                out[y : y + n] = np.asarray(z[start + y : start + y + n, :, :3])
                done = (y + n) / rows
                sys.stdout.write(
                    f"\r  {done * 100:5.1f}%  {y + n:>7}/{rows} rows  {time.time() - t0:5.0f}s"
                )
                sys.stdout.flush()
            out.flush()
            print()
        finally:
            del out  # release the memmap before anything reopens the file
        return rows, w
    finally:
        store.close()


def verify(src: str, dst: str, start: int, rows: int, samples: int = 8) -> None:
    """Re-read scattered rows from both files and require an exact match.

    A silently wrong crop (off-by-one window, channel swap, predictor mishandled)
    would invalidate every measurement taken against it, so this is not optional.
    """
    store = tifffile.imread(src, aszarr=True)
    try:
        z = zarr.open(store, mode="r")
        out = tifffile.memmap(dst, mode="r")
        try:
            if out.shape[0] != rows:
                raise SystemExit(f"output has {out.shape[0]} rows, expected {rows}")
            for y in np.linspace(0, rows - 1, samples, dtype=int):
                want = np.asarray(z[start + int(y) : start + int(y) + 1, :, :3])[0]
                got = np.asarray(out[int(y)])
                if not np.array_equal(want, got):
                    bad = int((want != got).sum())
                    raise SystemExit(f"row {y} differs from source in {bad} bytes")
            print(f"  verified {samples} sampled rows match the source exactly")
        finally:
            del out
    finally:
        store.close()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--rows", type=int, required=True, help="row count to keep")
    ap.add_argument("--start", type=int, default=0, help="first source row (default 0)")
    ap.add_argument("--block", type=int, default=BLOCK_ROWS)
    args = ap.parse_args(argv)

    print(f"cropping {args.src}\n      -> {args.dst}")
    rows, w = crop(args.src, args.dst, args.start, args.rows, args.block)
    verify(args.src, args.dst, args.start, rows)
    size = os.path.getsize(args.dst)
    print(f"  wrote {rows} x {w} x 3 = {size / 1e9:.2f} GB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
