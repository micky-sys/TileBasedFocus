"""Benchmark per-drive sustained write throughput for full-frame TIFFs.

Writes the same 13400x9528 uint16 ndarray (~244 MB) to each target drive
several times using the same tifffile + numpy code path the live writer
uses, so we know whether the drive or our writer is the bottleneck.

Modes:
  * ``serial`` (default): bench each drive one after the other in the
    main thread.
  * ``parallel``: spawn one Python thread per drive and have them all
    write concurrently -- mimics the live app load where 3 writer
    threads fight for the GIL while their disk I/O can in principle
    run in parallel.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time

import numpy as np
import tifffile


WIDTH = 13400
HEIGHT = 9528
N_FRAMES = 8  # each ~243 MB; total ~2 GB per drive


def _prep_drive(drive_letter: str) -> str:
    target_dir = f"{drive_letter}:\\bench_writer"
    os.makedirs(target_dir, exist_ok=True)
    for name in os.listdir(target_dir):
        try:
            os.remove(os.path.join(target_dir, name))
        except OSError:
            pass
    return target_dir


def _write_frames(
    target_dir: str, frame: np.ndarray, n: int, label: str, results: dict
) -> None:
    paths = [os.path.join(target_dir, f"bench_{i}.tif") for i in range(n)]
    description = json.dumps(
        {"camera_serial": "0000000", "project": "bench"}, separators=(",", ":")
    )
    t0 = time.perf_counter()
    for p in paths:
        tifffile.imwrite(
            p,
            frame,
            photometric="minisblack",
            compression=None,
            bigtiff=False,
            description=description,
        )
    elapsed = time.perf_counter() - t0
    results[label] = elapsed


def bench_serial(drives: list[str], frame: np.ndarray) -> None:
    print("\n=== SERIAL (one drive at a time, single thread) ===")
    for drv in drives:
        try:
            target_dir = _prep_drive(drv)
        except OSError as exc:
            print(f"  {drv}: prep ERROR {exc}")
            continue
        results: dict = {}
        _write_frames(target_dir, frame, N_FRAMES, drv, results)
        elapsed = results[drv]
        total_mb = (frame.nbytes * N_FRAMES) / (1024 * 1024)
        print(
            f"  {drv}: {total_mb:.0f} MB in {elapsed:.2f} s "
            f"-> {total_mb / elapsed:.0f} MB/s, "
            f"{elapsed / N_FRAMES * 1000:.0f} ms/frame"
        )


def bench_parallel(drives: list[str], frame: np.ndarray) -> None:
    print("\n=== PARALLEL (3 threads writing 3 drives at once) ===")
    target_dirs = {drv: _prep_drive(drv) for drv in drives}
    results: dict = {}
    threads = [
        threading.Thread(
            target=_write_frames,
            args=(target_dirs[drv], frame, N_FRAMES, drv, results),
            name=f"writer-{drv}",
        )
        for drv in drives
    ]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - t0

    total_mb = (frame.nbytes * N_FRAMES * len(drives)) / (1024 * 1024)
    print(
        f"  wall: {wall:.2f} s, aggregate {total_mb:.0f} MB "
        f"-> {total_mb / wall:.0f} MB/s aggregate"
    )
    for drv in drives:
        elapsed = results[drv]
        per_drive_mb = (frame.nbytes * N_FRAMES) / (1024 * 1024)
        print(
            f"  {drv}: {elapsed:.2f} s -> "
            f"{per_drive_mb / elapsed:.0f} MB/s, "
            f"{elapsed / N_FRAMES * 1000:.0f} ms/frame"
        )


def main() -> int:
    print(f"Building {WIDTH}x{HEIGHT} uint16 frame ({WIDTH * HEIGHT * 2 / (1024*1024):.0f} MB)")
    rng = np.random.default_rng(0xC0FFEE)
    # Realistic-ish content so the drive can't trivially compress it.
    frame = rng.integers(0, 16384, size=(HEIGHT, WIDTH), dtype=np.uint16)

    args = sys.argv[1:]
    mode = "both"
    if args and args[0] in ("serial", "parallel", "both"):
        mode = args[0]
        args = args[1:]
    drives = args or ["D", "E", "F"]

    if mode in ("serial", "both"):
        bench_serial(drives, frame)
    if mode in ("parallel", "both"):
        bench_parallel(drives, frame)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
