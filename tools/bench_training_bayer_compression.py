"""Synthetic Bayer + blosc2 benchmark on debayered training JPGs.

Reads RGB/BGR images (post-debayer), rebuilds an RG Bayer mosaic as uint16
scaled to 14-bit range, resizes to camera geometry (13400×9528), then
compresses with the same path as live capture
(``capture.raw_bay_writer.compress_frame_blosc2``).

Usage:
    py -3 tools/bench_training_bayer_compression.py [path_to_folder]

Default folder: ``<repo>/Training pictures``
"""
from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[1]
HOST = REPO / "host"
sys.path.insert(0, str(HOST))

from capture.raw_bay_writer import compress_frame_blosc2  # noqa: E402

CAM_W, CAM_H = 13400, 9528
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
MAX_14 = 16383


def debayered_bgr_to_bayer_u16(bgr: np.ndarray) -> np.ndarray:
    """Approximate RG Bayer raw from an 8-bit BGR demosaic.

    RG layout (OpenCV / GenICam BayerRG): R at (0,0), G at (0,1)/(1,0), B at (1,1).
    Values scaled linearly into 14-bit range in uint16.
    """
    h, w = bgr.shape[:2]
    if h % 2 or w % 2:
        raise ValueError(f"need even H,W for Bayer, got {h}x{w}")
    b = bgr[:, :, 0].astype(np.uint32)
    g = bgr[:, :, 1].astype(np.uint32)
    r = bgr[:, :, 2].astype(np.uint32)
    r14 = (r * MAX_14 // 255).astype(np.uint16)
    g14 = (g * MAX_14 // 255).astype(np.uint16)
    b14 = (b * MAX_14 // 255).astype(np.uint16)
    out = np.empty((h, w), dtype=np.uint16)
    out[0::2, 0::2] = r14[0::2, 0::2]
    out[0::2, 1::2] = g14[0::2, 1::2]
    out[1::2, 0::2] = g14[1::2, 0::2]
    out[1::2, 1::2] = b14[1::2, 1::2]
    return out


def to_camera_shape(bgr: np.ndarray) -> np.ndarray:
    """Resize/crop to 13400×9528 (camera frame size)."""
    h, w = bgr.shape[:2]
    if (w, h) == (CAM_W, CAM_H):
        return bgr
    return cv2.resize(bgr, (CAM_W, CAM_H), interpolation=cv2.INTER_AREA)


def main() -> int:
    folder = (
        Path(sys.argv[1]).resolve()
        if len(sys.argv) > 1
        else REPO / "Training pictures"
    )
    if not folder.is_dir():
        print(f"Not a directory: {folder}")
        return 1

    paths = sorted(
        p for p in folder.iterdir()
        if p.suffix.lower() in IMAGE_EXTS and p.is_file()
    )
    if not paths:
        print(f"No images in {folder}")
        return 1

    raw_bytes = CAM_H * CAM_W * 2
    ratios: list[float] = []
    enc_ms: list[float] = []
    out_mb: list[float] = []

    print(f"Folder: {folder}")
    print(f"Frames: {len(paths)}  |  camera shape: {CAM_W}x{CAM_H} uint16 RG Bayer")
    print(f"Raw size: {raw_bytes / (1024**2):.2f} MB/frame")
    print()

    for i, p in enumerate(paths):
        bgr = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if bgr is None:
            print(f"  skip (unreadable): {p.name}")
            continue
        bgr = to_camera_shape(bgr)
        bay = debayered_bgr_to_bayer_u16(bgr)

        t0 = time.perf_counter()
        blob = compress_frame_blosc2(bay)
        dt = time.perf_counter() - t0

        ob = len(blob)
        ratio = raw_bytes / ob
        ratios.append(ratio)
        enc_ms.append(dt * 1000.0)
        out_mb.append(ob / (1024**2))

        if i < 3 or i == len(paths) - 1:
            print(
                f"  [{i+1}/{len(paths)}] {p.name[:40]:<40} "
                f"ratio={ratio:.3f}x  encode={dt*1000:.0f} ms  "
                f"out={ob/(1024**2):.2f} MB"
            )

    if not ratios:
        return 1

    print()
    print("--- summary (all successfully read frames) ---")
    print(f"  count:     {len(ratios)}")
    print(f"  ratio:     min={min(ratios):.3f}x  max={max(ratios):.3f}x  "
          f"mean={statistics.mean(ratios):.3f}x  "
          f"stdev={statistics.pstdev(ratios):.4f}")
    print(f"  encode ms: min={min(enc_ms):.0f}  max={max(enc_ms):.0f}  "
          f"mean={statistics.mean(enc_ms):.0f}  "
          f"stdev={statistics.pstdev(enc_ms):.1f}")
    print(f"  compressed MB: mean={statistics.mean(out_mb):.2f}  "
          f"max={max(out_mb):.2f}")

    # Effective disk demand at 4.76 fps (producer)
    fps = 4.76
    mean_mb = statistics.mean(out_mb)
    mb_s = mean_mb * fps
    print()
    print(
        f"  @ {fps:.2f} fps x {mean_mb:.2f} MB/frame ~ {mb_s:.1f} MB/s "
        f"({mb_s/1024:.2f} GB/s) per camera write payload"
    )

    mean_r = statistics.mean(ratios)
    if mean_r >= 2.5 and statistics.mean(enc_ms) <= 380:
        verdict = (
            "GOOD - ratio and encode time look in family with the live "
            "black-frame run; headroom likely OK on this SSD class."
        )
    elif mean_r >= 2.0:
        verdict = (
            "MARGINAL - ratio still workable but closer to the cliff; "
            "real sensor noise may differ slightly from this synthetic Bayer."
        )
    else:
        verdict = (
            "TIGHT - mean ratio < 2x implies materially higher disk demand than "
            "your last green run; consider stronger codec settings or more pack threads."
        )
    print()
    print(f"Verdict: {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
