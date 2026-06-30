"""Concurrent sequential write benchmark.

Spawns N writer processes per drive, each writing its own file with
incompressible 256 MiB chunks, and reports BOTH:

* per-writer effective write speed (during its own writes), and
* aggregate per-drive throughput (sum of writer fps × payload).

Why this matters: a single sequential stream can hit the NVMe's
nominal ~1.1 GB/s, but two concurrent streams to the same NTFS
volume contend on the file system + drive queue. The production
io_writer pipeline runs N=2 streams per camera, so the relevant
ceiling is the 2-stream aggregate, NOT the 1-stream peak.

Usage:
  py -3 tools/bench_drive_write_concurrent.py
  py -3 tools/bench_drive_write_concurrent.py --writers 2 --size-gb 4 D: E: F:
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import sys
import time


def _writer_proc(
    path: str, total_bytes: int, chunk_bytes: int, result_q
) -> None:
    buf = os.urandom(chunk_bytes)
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


def bench_root(root: str, n_writers: int, size_gb: float, chunk_mb: int) -> None:
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        print(f"skip (not a directory): {root}")
        return
    testdir = os.path.join(root, "_write_bench_tmp")
    try:
        os.makedirs(testdir, exist_ok=True)
    except OSError as exc:
        print(f"cannot create {testdir}: {exc}")
        return
    paths = [
        os.path.join(testdir, f"seq_write_concurrent_{i}.bin")
        for i in range(n_writers)
    ]
    for p in paths:
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    total_per_writer = int(size_gb * 1024 * 1024 * 1024)
    chunk = chunk_mb * 1024 * 1024

    ctx = mp.get_context("spawn")
    result_q = ctx.Queue()
    procs: list[mp.Process] = []
    print(
        f"{root}: {n_writers} concurrent writer(s), "
        f"{size_gb} GiB each, {chunk_mb} MiB chunks (urandom)..."
    )
    t0 = time.perf_counter()
    for path in paths:
        p = ctx.Process(
            target=_writer_proc,
            args=(path, total_per_writer, chunk, result_q),
            daemon=False,
        )
        p.start()
        procs.append(p)
    results = []
    for _ in procs:
        results.append(result_q.get())
    for p in procs:
        p.join()
    wall_aggregate = time.perf_counter() - t0

    total_bytes = sum(r["bytes"] for r in results)
    aggregate_mib_s = (
        (total_bytes / (1024 * 1024)) / wall_aggregate
        if wall_aggregate > 0 else 0.0
    )
    aggregate_mb_s = (
        (total_bytes / (1000 * 1000)) / wall_aggregate
        if wall_aggregate > 0 else 0.0
    )
    for r in results:
        print(
            f"  writer {os.path.basename(r['path'])}: "
            f"{r['mib_s']:.1f} MiB/s solo-window "
            f"({r['bytes'] / (1024**3):.2f} GiB in {r['wall_s']:.2f} s)"
        )
    print(
        f"  AGGREGATE drive throughput: "
        f"{aggregate_mib_s:.1f} MiB/s "
        f"({total_bytes / (1024**3):.2f} GiB in {wall_aggregate:.2f} s)  "
        f"~ {aggregate_mb_s:.1f} MB/s decimal"
    )

    for p in paths:
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "roots",
        nargs="*",
        default=["D:\\", "E:\\", "F:\\"],
        help="Drive roots to test (default D E F)",
    )
    p.add_argument("--writers", type=int, default=2,
                   help="Concurrent writer processes per drive (default 2)")
    p.add_argument("--size-gb", type=float, default=4.0,
                   help="Total write per writer (default 4 GiB)")
    p.add_argument("--chunk-mb", type=int, default=256,
                   help="Write chunk size (default 256 MiB)")
    args = p.parse_args(argv)

    print("Concurrent sequential write benchmark")
    print(
        f"writers per drive = {args.writers}, "
        f"each writes {args.size_gb} GiB in {args.chunk_mb} MiB chunks\n"
    )
    for root in args.roots:
        bench_root(root, args.writers, args.size_gb, args.chunk_mb)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
