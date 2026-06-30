"""A/B benchmark: do '.bay14' files write slower than '.tif' files?

Hypothesis: Windows Defender's real-time scan adds extra latency
on the unfamiliar ``.bay14`` extension while it lets ``.tif`` writes
through faster. If true, per-file create time differs noticeably.

This script writes the *same* 213 MiB random-byte payload to each
target drive twice -- once with ``.bay14`` extension, once with
``.tif`` -- and reports the per-file mean wall time for each.
Results are reported per-drive so we can see whether the effect is
uniform across drives.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np


N_FRAMES = 8
PAYLOAD_BYTES = 223_244_000  # ~213 MiB, same as a real .bay14 frame


def prep_target(drive: str) -> Path:
    target = Path(f"{drive}:\\bench_ext")
    target.mkdir(exist_ok=True)
    for f in target.iterdir():
        try:
            f.unlink()
        except OSError:
            pass
    return target


def bench_one(target_dir: Path, ext: str, payload: np.ndarray) -> float:
    """Write N_FRAMES files with extension *ext*, return mean ms/file."""
    paths = [target_dir / f"frame_{i}{ext}" for i in range(N_FRAMES)]
    t0 = time.perf_counter()
    for p in paths:
        with open(p, "wb") as f:
            payload.tofile(f)
    elapsed = time.perf_counter() - t0
    return elapsed / N_FRAMES * 1000.0


def main() -> int:
    drives = sys.argv[1:] or ["D", "E", "F"]
    rng = np.random.default_rng(0xC0FFEE)
    payload = rng.integers(0, 256, size=PAYLOAD_BYTES, dtype=np.uint8)
    print(f"payload {payload.nbytes / 1e6:.0f} MB; {N_FRAMES} files per ext per drive")

    for drv in drives:
        try:
            target = prep_target(drv)
        except OSError as exc:
            print(f"  {drv}: prep failed ({exc})")
            continue
        print(f"--- drive {drv} ---")
        # Interleave so neither extension benefits from "warmer cache".
        # Order: tif, bay14, tif, bay14 across two passes.
        ms_tif = bench_one(target, ".tif", payload)
        ms_bay = bench_one(target, ".bay14", payload)
        # Re-run to average noise out
        ms_tif2 = bench_one(target, ".tif", payload)
        ms_bay2 = bench_one(target, ".bay14", payload)
        avg_tif = (ms_tif + ms_tif2) / 2
        avg_bay = (ms_bay + ms_bay2) / 2
        delta = avg_bay - avg_tif
        delta_pct = (delta / avg_tif) * 100
        print(
            f"  .tif:    {avg_tif:.0f} ms/file  ({PAYLOAD_BYTES / 1e6 / (avg_tif/1000):.0f} MB/s)"
        )
        print(
            f"  .bay14:  {avg_bay:.0f} ms/file  ({PAYLOAD_BYTES / 1e6 / (avg_bay/1000):.0f} MB/s)"
        )
        print(f"  delta:   {delta:+.0f} ms  ({delta_pct:+.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
