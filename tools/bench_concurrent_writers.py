"""Benchmark concurrent writes per drive (1 vs 2 vs 3 writer subprocesses).

The live capture pipeline has 2 writer subprocesses per camera, all
hitting one drive each. This benchmark mirrors that: spawn N
subprocesses against each drive, each writing K back-to-back files,
and report aggregate MB/s per drive.

Hypothesis: NTFS / the U.2 controller serialises large concurrent
writes, so going from 1 to 2 writers does not double throughput.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

import numpy as np


PAYLOAD_BYTES = 223_244_000  # 213 MiB
N_FILES_PER_WRITER = 6


def _writer(drive: str, worker_idx: int, payload: np.ndarray, t_start: float, results: mp.Queue) -> None:
    """Subprocess entry: write N files into <drive>:\\bench_conc\\<worker_idx>_*.bay14."""
    target = Path(f"{drive}:\\bench_conc")
    target.mkdir(exist_ok=True)
    # Wait until shared start moment so all workers begin together
    while time.perf_counter() < t_start:
        time.sleep(0.001)
    t0 = time.perf_counter()
    for i in range(N_FILES_PER_WRITER):
        p = target / f"w{worker_idx}_f{i}.bay14"
        with open(p, "wb") as f:
            payload.tofile(f)
    elapsed = time.perf_counter() - t0
    results.put((worker_idx, elapsed, N_FILES_PER_WRITER))


def prep(drive: str) -> None:
    target = Path(f"{drive}:\\bench_conc")
    target.mkdir(exist_ok=True)
    for f in target.iterdir():
        try:
            f.unlink()
        except OSError:
            pass


def run_one(drive: str, n_writers: int, payload: np.ndarray) -> None:
    prep(drive)
    ctx = mp.get_context("spawn")
    results: mp.Queue = ctx.Queue()
    t_start = time.perf_counter() + 0.5  # let processes start first
    procs: list[mp.Process] = []
    for w in range(n_writers):
        p = ctx.Process(target=_writer, args=(drive, w, payload, t_start, results), daemon=False)
        p.start()
        procs.append(p)
    for p in procs:
        p.join()
    per_writer = []
    while not results.empty():
        per_writer.append(results.get())
    total_bytes = n_writers * N_FILES_PER_WRITER * PAYLOAD_BYTES
    max_elapsed = max(e for _, e, _ in per_writer) if per_writer else 0.0
    aggregate_mbs = total_bytes / 1e6 / max_elapsed if max_elapsed else 0.0
    per_writer_mbs = [PAYLOAD_BYTES * k / 1e6 / e for _, e, k in per_writer]
    pw = ", ".join(f"{m:.0f}" for m in per_writer_mbs)
    print(
        f"  {n_writers} writer(s): aggregate {aggregate_mbs:.0f} MB/s "
        f"({total_bytes / 1e6:.0f} MB in {max_elapsed:.2f}s); "
        f"per-writer [{pw}] MB/s"
    )


def main() -> int:
    drives = sys.argv[1:] or ["D", "E", "F"]
    payload = np.random.default_rng(0xCAFE).integers(0, 256, size=PAYLOAD_BYTES, dtype=np.uint8)
    print(f"payload {payload.nbytes / 1e6:.0f} MB per file, {N_FILES_PER_WRITER} files per writer")

    for drv in drives:
        print(f"--- drive {drv} ---")
        for n in (1, 2, 3):
            run_one(drv, n, payload)
    return 0


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
