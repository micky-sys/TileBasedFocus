"""Quick sequential write benchmark for removable / project drives.

Writes incompressible data (like packed raw frames) in large chunks,
reports MiB/s and decimal MB/s for comparison to vendor specs.

Usage:
  py -3 tools/bench_drive_write.py
  py -3 tools/bench_drive_write.py --size-gb 4 D: E: F:
"""

from __future__ import annotations

import argparse
import gc
import os
import sys
import time


def seq_write_mib_s(path: str, total_bytes: int, chunk_bytes: int) -> dict[str, float]:
    buf = os.urandom(min(chunk_bytes, total_bytes))
    t0 = time.perf_counter()
    written = 0
    with open(path, "wb", buffering=chunk_bytes) as f:
        while written < total_bytes:
            n = min(chunk_bytes, total_bytes - written)
            if n <= len(buf):
                f.write(buf[:n])
            else:
                f.write(buf)
            written += n
    try:
        f.flush()
    except Exception:
        pass
    wall = time.perf_counter() - t0
    mib_s = (written / (1024 * 1024)) / wall if wall > 0 else 0.0
    mb_s_decimal = (written / (1000 * 1000)) / wall if wall > 0 else 0.0
    return {
        "bytes": float(written),
        "wall_s": wall,
        "mib_s": mib_s,
        "mb_s_decimal": mb_s_decimal,
    }


def bench_root(root: str, size_gb: float, chunk_mb: int) -> None:
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
    path = os.path.join(testdir, "seq_write_bench.bin")
    total = int(size_gb * 1024 * 1024 * 1024)
    chunk = chunk_mb * 1024 * 1024
    try:
        if os.path.exists(path):
            os.remove(path)
        print(f"{root}: {size_gb} GiB, {chunk_mb} MiB chunks (urandom)...")
        r = seq_write_mib_s(path, total, chunk)
        gib = r["bytes"] / (1024**3)
        print(
            f"  {r['mib_s']:.1f} MiB/s  ({gib:.3f} GiB in {r['wall_s']:.2f} s)  "
            f"~ {r['mb_s_decimal']:.1f} MB/s decimal"
        )
    finally:
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
        gc.collect()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "roots",
        nargs="*",
        default=["D:\\", "E:\\", "F:\\", "C:\\"],
        help="Drive roots to test (default D E F C)",
    )
    p.add_argument("--size-gb", type=float, default=2.0, help="Total write per drive")
    p.add_argument("--chunk-mb", type=int, default=256, help="Write chunk size")
    args = p.parse_args(argv)

    print("Sequential write (buffered Python I/O, incompressible payload)")
    print("1 GiB = 1024 MiB. SSD boxes often quote MB/s using 1000-based MB.\n")
    for root in args.roots:
        bench_root(root, args.size_gb, args.chunk_mb)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
