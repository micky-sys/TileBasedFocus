"""Quick wall-clock + ratio bench for blosc2 BITSHUFFLE+ZSTD on a
synthetic Bayer frame the size of the rig's camera (13400 x 9528 uint16).

We try a handful of (codec, clevel, shuffle, nthreads) combos and print
encode/decode ms + on-disk MB/frame + ratio. Uses pseudo-random data
biased toward 14-bit dynamic range so the compression ratio reflects
something more realistic than pure entropy or pure zeros.
"""

from __future__ import annotations

import time

import numpy as np

import blosc2

H, W = 9528, 13400
N_PIXELS = H * W


def make_frame() -> np.ndarray:
    """Synthetic 14-bit Bayer-ish frame.

    Real Bayer is locally smooth (lens / scene structure) so a pure
    uniform random gives a pessimistic ratio. We emulate that by
    starting from low-frequency noise + a small high-freq overlay,
    clamped to 14 bits.
    """
    rng = np.random.default_rng(0)
    coarse_h = (H + 15) // 16
    coarse_w = (W + 15) // 16
    coarse = rng.integers(0, 1 << 12, size=(coarse_h, coarse_w), dtype=np.uint16)
    base = np.repeat(np.repeat(coarse, 16, axis=0), 16, axis=1)
    base = np.ascontiguousarray(base[:H, :W])
    noise = rng.integers(0, 1 << 9, size=(H, W), dtype=np.uint16)
    frame = (base + noise) & 0x3FFF
    return np.ascontiguousarray(frame)


def bench(label: str, fn, n_iter: int = 3) -> tuple[float, bytes]:
    out = b""
    t0 = time.perf_counter()
    for _ in range(n_iter):
        out = fn()
    dt = (time.perf_counter() - t0) / n_iter
    return dt, out


def main() -> None:
    print(f"sensor: {W}x{H} uint16, {N_PIXELS / 1e6:.1f} Mpx, "
          f"raw uint16 = {N_PIXELS * 2 / (1024 ** 2):.1f} MiB / frame")
    frame = make_frame()
    raw_bytes = frame.tobytes()
    raw_mib = len(raw_bytes) / (1024 ** 2)

    # Per-thread budgets we care about: per-frame producer budget at
    # 4.76 fps is ~210 ms per packer. With 3 packers per camera,
    # budget per packer rises to ~630 ms but we want to leave the
    # disk plenty of room.
    print()
    print(f"{'codec':14s} {'clevel':>6} {'shuffle':>10} {'nthreads':>8} "
          f"{'enc ms':>8} {'dec ms':>8} {'on-disk MB':>11} {'ratio':>6}")

    # Trial matrix. clevel 1 is "fast"; 3 is "balanced"; 5 starts
    # paying real CPU. Shuffle BITSHUFFLE on uint16 typically beats
    # SHUFFLE on uint16 for high-bit-depth raw imagery.
    trials = [
        ("zstd", 1, blosc2.Filter.SHUFFLE, 1),
        ("zstd", 1, blosc2.Filter.SHUFFLE, 2),
        ("zstd", 1, blosc2.Filter.SHUFFLE, 4),
        ("zstd", 1, blosc2.Filter.BITSHUFFLE, 1),
        ("zstd", 1, blosc2.Filter.BITSHUFFLE, 2),
        ("zstd", 1, blosc2.Filter.BITSHUFFLE, 4),
        ("zstd", 3, blosc2.Filter.BITSHUFFLE, 4),
        ("zstd", 5, blosc2.Filter.BITSHUFFLE, 4),
        ("lz4",  1, blosc2.Filter.BITSHUFFLE, 4),
    ]

    for codec_name, clevel, filt, nth in trials:
        cparams = {
            "codec": (
                blosc2.Codec.ZSTD if codec_name == "zstd"
                else blosc2.Codec.LZ4
            ),
            "clevel": clevel,
            "filters": [filt],
            "nthreads": nth,
            "typesize": 2,  # uint16
        }
        dparams = {"nthreads": nth}

        t_enc, packed = bench(
            f"{codec_name}-{clevel}",
            lambda: blosc2.compress2(frame, **cparams),
        )
        t_dec, _ = bench(
            f"{codec_name}-{clevel}-dec",
            lambda: blosc2.decompress2(packed, dparams=dparams),
        )
        on_disk_mib = len(packed) / (1024 ** 2)
        ratio = raw_mib / on_disk_mib if on_disk_mib else 0.0
        filt_name = (
            "SHUFFLE" if filt == blosc2.Filter.SHUFFLE
            else "BITSHUFFLE"
        )
        print(f"{codec_name:14s} {clevel:>6} {filt_name:>10} {nth:>8} "
              f"{t_enc * 1000:>8.0f} {t_dec * 1000:>8.0f} "
              f"{on_disk_mib:>11.1f} {ratio:>6.2f}")

    # Demand math. Producer rate at full M1 across 3 cams is ~4.76 fps
    # per camera. Per-camera drive ceiling on this rig is ~1.07 GB/s.
    print()
    print("at 4.76 fps producer (per camera):")
    for codec_name, clevel, filt, nth in trials:
        cparams = {
            "codec": (
                blosc2.Codec.ZSTD if codec_name == "zstd"
                else blosc2.Codec.LZ4
            ),
            "clevel": clevel,
            "filters": [filt],
            "nthreads": nth,
            "typesize": 2,
        }
        packed = blosc2.compress2(frame, **cparams)
        mb_per_s = (len(packed) / 1e6) * 4.76
        margin_pct = (1.07e3 - mb_per_s) / 1.07e3 * 100.0
        filt_name = (
            "SHUFFLE" if filt == blosc2.Filter.SHUFFLE
            else "BITSHUFFLE"
        )
        print(f"  {codec_name}-{clevel} {filt_name} nth={nth}: "
              f"{mb_per_s:6.0f} MB/s  ({margin_pct:+.0f}% drive headroom)")


if __name__ == "__main__":
    main()
