"""Run the 2-writer-per-drive sustained bench on ALL drives concurrently.

This mirrors live capture's actual load shape: each of 3 cameras feeds
one drive via 2 io_writer subprocesses, and ALL 3 drives are hammered
at the same time. If the host has 3 U.2 drives on the same PCIe x16 -> 3xX4
backplane, single-drive bench numbers will overstate sustained throughput
because they don't include cross-drive bus / DMA contention.

Usage:
    py -3 tools/bench_drive_write_all_parallel.py
    py -3 tools/bench_drive_write_all_parallel.py --writers 2 --size-gb 32 H:\\ I:\\ J:\\

Per-drive aggregate throughput is reported, plus the cross-drive
aggregate (sum across drives) so we can tell PCIe/DMA saturation apart
from per-drive limits.
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import sys
import time


def _writer_proc(path: str, total_bytes: int, chunk_bytes: int,
                 t_start_unix: float, result_q) -> None:
    buf = os.urandom(chunk_bytes)
    # Synchronise start across ALL writers across all drives
    while time.time() < t_start_unix:
        time.sleep(0.001)
    written = 0
    t0 = time.perf_counter()
    with open(path, "wb", buffering=chunk_bytes) as f:
        while written < total_bytes:
            n = min(chunk_bytes, total_bytes - written)
            if n == len(buf):
                f.write(buf)
            else:
                f.write(buf[:n])
            written += n
        f.flush()
    wall = time.perf_counter() - t0
    result_q.put({
        "path": path,
        "bytes": written,
        "wall_s": wall,
        "mib_s": (written / (1024 * 1024)) / wall if wall > 0 else 0.0,
    })


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "roots", nargs="*", default=["H:\\", "I:\\", "J:\\"],
        help="Drive roots to test in parallel (default H I J)",
    )
    p.add_argument("--writers", type=int, default=2,
                   help="Concurrent writer processes per drive (default 2)")
    p.add_argument("--size-gb", type=float, default=32.0,
                   help="Total write per writer (default 32 GiB)")
    p.add_argument("--chunk-mb", type=int, default=256,
                   help="Write chunk size (default 256 MiB)")
    args = p.parse_args(argv)

    print("CROSS-DRIVE concurrent sustained write bench")
    print(f"writers/drive = {args.writers}, size/writer = {args.size_gb} GiB, "
          f"chunk = {args.chunk_mb} MiB, drives = {args.roots}\n")

    chunk = args.chunk_mb * 1024 * 1024
    total_per_writer = int(args.size_gb * 1024 * 1024 * 1024)

    # Prep + collect paths per drive
    drive_paths: dict[str, list[str]] = {}
    for root in args.roots:
        root_abs = os.path.abspath(root)
        if not os.path.isdir(root_abs):
            print(f"skip (not a directory): {root_abs}")
            continue
        td = os.path.join(root_abs, "_write_bench_tmp_parallel")
        os.makedirs(td, exist_ok=True)
        paths = [os.path.join(td, f"seq_w_{i}.bin") for i in range(args.writers)]
        for pth in paths:
            if os.path.exists(pth):
                try:
                    os.remove(pth)
                except OSError:
                    pass
        drive_paths[root_abs] = paths

    if not drive_paths:
        print("no usable drives")
        return 1

    ctx = mp.get_context("spawn")
    result_q = ctx.Queue()

    # Start all writers across all drives synchronised on a future epoch.
    t_start = time.time() + 2.0
    procs: list[tuple[str, mp.Process]] = []
    t_wall_start = time.perf_counter()
    for root, paths in drive_paths.items():
        for path in paths:
            proc = ctx.Process(
                target=_writer_proc,
                args=(path, total_per_writer, chunk, t_start, result_q),
                daemon=False,
            )
            proc.start()
            procs.append((root, proc))

    print(f"launched {len(procs)} writer processes across "
          f"{len(drive_paths)} drives; sync start at +2.0 s ...")
    total_expected = len(procs)
    results: list[dict] = []
    for _ in range(total_expected):
        results.append(result_q.get())
    for _root, proc in procs:
        proc.join()
    wall_aggregate = time.perf_counter() - t_wall_start

    # Group results by drive
    by_drive: dict[str, list[dict]] = {}
    for r in results:
        d = os.path.splitdrive(r["path"])[0].upper() or os.path.dirname(r["path"])
        by_drive.setdefault(d, []).append(r)

    grand_bytes = 0
    print()
    for d in sorted(by_drive):
        rs = by_drive[d]
        b = sum(r["bytes"] for r in rs)
        # per-drive wall = max writer end time (the longest writer on this drive)
        max_wall = max(r["wall_s"] for r in rs)
        drive_mib_s = (b / (1024 * 1024)) / max_wall if max_wall > 0 else 0
        drive_mb_s = (b / (1000 * 1000)) / max_wall if max_wall > 0 else 0
        print(f"{d:6} ({len(rs)} writers): wrote {b/(1024**3):.2f} GiB, "
              f"per-drive aggregate {drive_mib_s:.1f} MiB/s "
              f"(~{drive_mb_s:.1f} MB/s decimal)")
        for r in rs:
            print(f"        {os.path.basename(r['path']):>20}: "
                  f"{r['mib_s']:.1f} MiB/s solo "
                  f"({r['bytes']/(1024**3):.2f} GiB in {r['wall_s']:.2f} s)")
        grand_bytes += b

    grand_mib_s = (grand_bytes / (1024 * 1024)) / wall_aggregate if wall_aggregate > 0 else 0
    grand_mb_s = (grand_bytes / (1000 * 1000)) / wall_aggregate if wall_aggregate > 0 else 0
    print()
    print(f"CROSS-DRIVE TOTAL: {grand_bytes/(1024**3):.2f} GiB in "
          f"{wall_aggregate:.2f} s -> {grand_mib_s:.1f} MiB/s aggregate "
          f"(~{grand_mb_s:.1f} MB/s)")

    print()
    print(f"For reference: live-capture demand is ~1.10 GB/s * 3 cameras = "
          f"~3.30 GB/s aggregate (uint16 codec=none, 4.33 fps trigger).")

    # Cleanup
    for paths in drive_paths.values():
        for pth in paths:
            try:
                if os.path.exists(pth):
                    os.remove(pth)
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
