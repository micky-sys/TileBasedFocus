"""Bench numba JIT pack against imagecodecs for full-frame Bayer."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
from numba import njit, prange, get_num_threads

# Ensure we can import the existing raw_bay_writer for verification.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "host"))
from capture.raw_bay_writer import unpack_u14  # noqa: E402


@njit(cache=True, fastmath=True, boundscheck=False)
def pack_u14_numba(src, dst):
    n = src.size
    ngroups = n // 4
    for i in range(ngroups):
        p0 = np.uint64(src[4 * i + 0])
        p1 = np.uint64(src[4 * i + 1])
        p2 = np.uint64(src[4 * i + 2])
        p3 = np.uint64(src[4 * i + 3])
        q = (
            p0
            | (p1 << np.uint64(14))
            | (p2 << np.uint64(28))
            | (p3 << np.uint64(42))
        )
        b = 7 * i
        dst[b] = np.uint8(q & np.uint64(0xFF))
        dst[b + 1] = np.uint8((q >> np.uint64(8)) & np.uint64(0xFF))
        dst[b + 2] = np.uint8((q >> np.uint64(16)) & np.uint64(0xFF))
        dst[b + 3] = np.uint8((q >> np.uint64(24)) & np.uint64(0xFF))
        dst[b + 4] = np.uint8((q >> np.uint64(32)) & np.uint64(0xFF))
        dst[b + 5] = np.uint8((q >> np.uint64(40)) & np.uint64(0xFF))
        dst[b + 6] = np.uint8((q >> np.uint64(48)) & np.uint64(0xFF))


@njit(cache=True, fastmath=True, boundscheck=False, parallel=True)
def pack_u14_numba_par(src, dst):
    n = src.size
    ngroups = n // 4
    for i in prange(ngroups):
        p0 = np.uint64(src[4 * i + 0])
        p1 = np.uint64(src[4 * i + 1])
        p2 = np.uint64(src[4 * i + 2])
        p3 = np.uint64(src[4 * i + 3])
        q = (
            p0
            | (p1 << np.uint64(14))
            | (p2 << np.uint64(28))
            | (p3 << np.uint64(42))
        )
        b = 7 * i
        dst[b] = np.uint8(q & np.uint64(0xFF))
        dst[b + 1] = np.uint8((q >> np.uint64(8)) & np.uint64(0xFF))
        dst[b + 2] = np.uint8((q >> np.uint64(16)) & np.uint64(0xFF))
        dst[b + 3] = np.uint8((q >> np.uint64(24)) & np.uint64(0xFF))
        dst[b + 4] = np.uint8((q >> np.uint64(32)) & np.uint64(0xFF))
        dst[b + 5] = np.uint8((q >> np.uint64(40)) & np.uint64(0xFF))
        dst[b + 6] = np.uint8((q >> np.uint64(48)) & np.uint64(0xFF))


def main() -> int:
    H, W = 9528, 13400
    rng = np.random.default_rng(0xC0FFEE)
    arr = rng.integers(0, 1 << 14, size=(H, W), dtype=np.uint16)
    flat = arr.ravel()
    dst = np.empty(flat.size * 7 // 4, dtype=np.uint8)

    print(f"NUMBA threads: {get_num_threads()}")

    # JIT warmup (first call compiles)
    print("warmup (1-thread)...", end=" ", flush=True)
    t = time.perf_counter()
    pack_u14_numba(flat, dst)
    print(f"{(time.perf_counter() - t) * 1000:.0f} ms")

    print("warmup (parallel)...", end=" ", flush=True)
    t = time.perf_counter()
    pack_u14_numba_par(flat, dst)
    print(f"{(time.perf_counter() - t) * 1000:.0f} ms")

    # Now timing
    t = time.perf_counter()
    for _ in range(20):
        pack_u14_numba(flat, dst)
    print(f"numba 1-thread avg of 20:  {(time.perf_counter() - t) / 20 * 1000:.0f} ms")

    t = time.perf_counter()
    for _ in range(20):
        pack_u14_numba_par(flat, dst)
    print(f"numba parallel avg of 20:  {(time.perf_counter() - t) / 20 * 1000:.0f} ms")

    # Verify against canonical unpack
    arr_back = unpack_u14(dst, flat.size).reshape(H, W)
    print(f"round-trip: {np.array_equal(arr, arr_back)}")

    # Compare imagecodecs
    try:
        from imagecodecs import packints_encode

        ic_out = np.empty_like(dst)
        packints_encode(flat, 14, bitorder="<", out=ic_out)
        t = time.perf_counter()
        for _ in range(20):
            packints_encode(flat, 14, bitorder="<", out=ic_out)
        print(
            f"imagecodecs avg of 20:     "
            f"{(time.perf_counter() - t) / 20 * 1000:.0f} ms"
        )
        print(f"imagecodecs == numba:      {np.array_equal(ic_out, dst)}")
    except ImportError:
        print("imagecodecs unavailable")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
