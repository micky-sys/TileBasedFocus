"""Create a live-like .bay14line file from training images and benchmark it.

This script aims to mirror the live capture write path as closely as possible
offline:

1) Read each training image.
2) Resize to camera geometry (13400x9528) if needed.
3) Synthesize an RG Bayer uint16 frame with a lightweight sensor-like model:
   - sRGB-to-linear lookup table (8-bit -> 14-bit domain)
   - CFA sampling at RGGB sites
   - low-amplitude row/column offsets to mimic fixed-pattern noise
4) Compress with the same codec used by live capture:
   capture.raw_bay_writer.compress_frame_blosc2()
5) Append to a real .bay14line container via LineWriter.

Outputs:
  - A .bay14line file on disk
  - Timing and size summary from the real write run
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[1]
HOST = REPO / "host"
sys.path.insert(0, str(HOST))

from capture.raw_bay_writer import (  # noqa: E402
    COMPRESSION_BLOSC2_ZSTD1_SHUFFLE,
    LineWriter,
    compress_frame_blosc2,
)

CAM_W = 13400
CAM_H = 9528
BIT_MAX = 16383
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


def _build_srgb_to_14bit_lut() -> np.ndarray:
    """Build a 256-entry LUT mapping 8-bit sRGB code to 14-bit linear DN."""
    x = np.linspace(0.0, 1.0, 256, dtype=np.float64)
    lin = np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
    lut = np.round(lin * BIT_MAX).astype(np.uint16)
    return lut


def to_camera_shape(bgr: np.ndarray) -> np.ndarray:
    """Resize to camera frame size if input geometry differs."""
    h, w = bgr.shape[:2]
    if (w, h) == (CAM_W, CAM_H):
        return bgr
    return cv2.resize(bgr, (CAM_W, CAM_H), interpolation=cv2.INTER_AREA)


def bgr_to_live_like_bayer_u16(
    bgr: np.ndarray,
    *,
    lut14: np.ndarray,
    rng: np.random.Generator,
    row_noise_lsb: int,
    col_noise_lsb: int,
) -> np.ndarray:
    """Approximate live BayerRG14 from a debayered BGR image.

    Notes:
    - We sample only the CFA site channel at each location (RGGB), rather than
      storing all three channels.
    - Row/column additive offsets mimic low-amplitude fixed-pattern structure.
    """
    h, w = bgr.shape[:2]
    if h % 2 or w % 2:
        raise ValueError(f"need even H,W for Bayer, got {h}x{w}")

    out = np.empty((h, w), dtype=np.uint16)

    # RGGB sampling with sRGB->linear 14-bit LUT.
    out[0::2, 0::2] = lut14[bgr[0::2, 0::2, 2]]  # R sites
    out[0::2, 1::2] = lut14[bgr[0::2, 1::2, 1]]  # G sites
    out[1::2, 0::2] = lut14[bgr[1::2, 0::2, 1]]  # G sites
    out[1::2, 1::2] = lut14[bgr[1::2, 1::2, 0]]  # B sites

    if row_noise_lsb <= 0 and col_noise_lsb <= 0:
        return out

    # Apply small row/column offsets in int32 space, then clamp.
    acc = out.astype(np.int32, copy=False)
    if row_noise_lsb > 0:
        row = rng.integers(
            -row_noise_lsb, row_noise_lsb + 1, size=(h, 1), dtype=np.int16
        )
        acc += row
    if col_noise_lsb > 0:
        col = rng.integers(
            -col_noise_lsb, col_noise_lsb + 1, size=(1, w), dtype=np.int16
        )
        acc += col
    np.clip(acc, 0, BIT_MAX, out=acc)
    return acc.astype(np.uint16, copy=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "input_dir",
        nargs="?",
        default=str(REPO / "Training pictures"),
        help="Folder of training images (jpg/png/tif).",
    )
    parser.add_argument(
        "--output",
        default=str(REPO / "bench_outputs" / "training_live_like.bay14line"),
        help="Output .bay14line file path.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process first N frames only (0 = all).",
    )
    parser.add_argument(
        "--frames-per-sweep",
        type=int,
        default=13,
        help="Frame count per synthetic sweep for metadata.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=12345,
        help="RNG seed for reproducible row/column noise.",
    )
    parser.add_argument(
        "--row-noise-lsb",
        type=int,
        default=8,
        help="Uniform row offset amplitude in LSB (0 disables).",
    )
    parser.add_argument(
        "--col-noise-lsb",
        type=int,
        default=8,
        help="Uniform column offset amplitude in LSB (0 disables).",
    )
    args = parser.parse_args()

    src = Path(args.input_dir).resolve()
    out_path = Path(args.output).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not src.is_dir():
        print(f"Not a directory: {src}")
        return 1
    if args.frames_per_sweep <= 0:
        print("--frames-per-sweep must be >= 1")
        return 1

    paths = sorted(
        p for p in src.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )
    if not paths:
        print(f"No images in {src}")
        return 1
    if args.limit > 0:
        paths = paths[: args.limit]

    lut14 = _build_srgb_to_14bit_lut()
    rng = np.random.default_rng(args.seed)

    encode_ms: list[float] = []
    synth_ms: list[float] = []
    write_ms: list[float] = []
    payload_mb: list[float] = []
    ratios: list[float] = []

    raw_bytes = CAM_H * CAM_W * 2

    line_meta = {
        "camera_serial": "offline-training-synth",
        "project": "training_benchmark",
        "line": 1,
        "capture_kind": "sweep",
        "line_started_at": time.time(),
    }
    writer = LineWriter(
        str(out_path),
        height=CAM_H,
        width=CAM_W,
        line_meta=line_meta,
        codec=COMPRESSION_BLOSC2_ZSTD1_SHUFFLE,
    )

    print(f"Input:   {src}")
    print(f"Output:  {out_path}")
    print(f"Frames:  {len(paths)}")
    print(f"Shape:   {CAM_W}x{CAM_H} uint16 BayerRG")
    print(f"Raw MB/frame: {raw_bytes / (1024**2):.2f}")
    print()

    t_wall0 = time.perf_counter()
    writer.open()
    failed = 0
    try:
        for i, p in enumerate(paths):
            bgr = cv2.imread(str(p), cv2.IMREAD_COLOR)
            if bgr is None:
                failed += 1
                print(f"  skip unreadable: {p.name}")
                continue
            bgr = to_camera_shape(bgr)

            t0 = time.perf_counter()
            frame_u16 = bgr_to_live_like_bayer_u16(
                bgr,
                lut14=lut14,
                rng=rng,
                row_noise_lsb=args.row_noise_lsb,
                col_noise_lsb=args.col_noise_lsb,
            )
            synth_s = time.perf_counter() - t0

            t1 = time.perf_counter()
            payload = compress_frame_blosc2(frame_u16)
            enc_s = time.perf_counter() - t1

            sweep = (i // args.frames_per_sweep) + 1
            frame_in_sweep = (i % args.frames_per_sweep) + 1

            t2 = time.perf_counter()
            writer.append_chunk(
                payload,
                sweep=sweep,
                frame_in_sweep=frame_in_sweep,
                frame_meta={
                    "source_name": p.name,
                    "acquire_timestamp": time.time(),
                    "capture_kind": "sweep",
                    "encode_s": enc_s,
                    "synth_s": synth_s,
                },
                gps=None,
            )
            wr_s = time.perf_counter() - t2

            synth_ms.append(synth_s * 1000.0)
            encode_ms.append(enc_s * 1000.0)
            write_ms.append(wr_s * 1000.0)
            mb = len(payload) / (1024**2)
            payload_mb.append(mb)
            ratios.append(raw_bytes / len(payload))

            if i < 3 or i == len(paths) - 1:
                print(
                    f"  [{i+1}/{len(paths)}] {p.name[:38]:<38} "
                    f"synth={synth_s*1000:.0f} ms  "
                    f"enc={enc_s*1000:.0f} ms  "
                    f"write={wr_s*1000:.0f} ms  "
                    f"out={mb:.2f} MB  ratio={raw_bytes/len(payload):.3f}x"
                )
    finally:
        close_stats = writer.close_with_index()

    wall_s = time.perf_counter() - t_wall0
    file_bytes = out_path.stat().st_size if out_path.exists() else 0

    if not payload_mb:
        print("No frames written.")
        return 1

    print()
    print("--- summary ---")
    print(f"  written frames: {len(payload_mb)}")
    print(f"  failed reads:   {failed}")
    print(
        f"  synth ms:       min={min(synth_ms):.0f}  "
        f"max={max(synth_ms):.0f}  mean={statistics.mean(synth_ms):.0f}"
    )
    print(
        f"  encode ms:      min={min(encode_ms):.0f}  "
        f"max={max(encode_ms):.0f}  mean={statistics.mean(encode_ms):.0f}"
    )
    print(
        f"  append ms:      min={min(write_ms):.0f}  "
        f"max={max(write_ms):.0f}  mean={statistics.mean(write_ms):.0f}"
    )
    print(
        f"  ratio x:        min={min(ratios):.3f}  "
        f"max={max(ratios):.3f}  mean={statistics.mean(ratios):.3f}"
    )
    print(
        f"  payload MB:     mean={statistics.mean(payload_mb):.2f}  "
        f"max={max(payload_mb):.2f}"
    )
    print(
        f"  file size GB:   {file_bytes / (1024**3):.3f} "
        f"({file_bytes / (1024**2):.1f} MB)"
    )
    print(
        f"  wall time:      {wall_s:.1f} s  "
        f"({len(payload_mb) / wall_s:.2f} fps effective end-to-end)"
    )
    print(
        f"  close payload MB: {close_stats['payload_total'] / (1024**2):.1f}  "
        f"index+header MB: {(close_stats['index_size'] + close_stats['header_size']) / (1024**2):.3f}"
    )

    # Live-rate planning line.
    fps = 4.76
    mean_mb = statistics.mean(payload_mb)
    mb_s = mean_mb * fps
    print()
    print(
        f"  at {fps:.2f} fps: {mean_mb:.2f} MB/frame -> {mb_s:.1f} MB/s "
        f"({mb_s/1024:.3f} GB/s) per camera"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
