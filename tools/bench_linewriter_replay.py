"""Disk-only .bay14line replay benchmark using real payload-size profile.

Purpose:
  Measure pure line-writer append throughput on target drives without JPEG
  decode, Bayer synthesis, or compression time mixed in.

Method:
  1) Read payload lengths from an existing .bay14line file index, **or**
     pass ``--synthetic-payload-bytes`` to replay a fixed size every frame
     (for packed14 vs typical blosc2 without a profile capture).
  2) Replay those payload sizes into a new .bay14line file via LineWriter.
  3) Report append ms/frame, MiB/s (1024²), and margin vs required MiB/s at
     target FPS (printed as \"MB/s\" for historical reasons — values are MiB/s).
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
HOST = REPO / "host"
sys.path.insert(0, str(HOST))

from capture.drive_picker import list_drives  # noqa: E402
from capture.raw_bay_writer import (  # noqa: E402
    COMPRESSION_BLOSC2_ZSTD1_SHUFFLE,
    LINE_HEADER_FIXED_PREFIX,
    LINE_INDEX_ENTRY_SIZE,
    LineWriter,
    parse_line_header_prefix,
    parse_line_index,
)

CAM_W = 13400
CAM_H = 9528
RAW_MB = (CAM_W * CAM_H * 2) / (1024**2)
# GenICam packed 14-bit; live packed14 line payloads use this exact byte count.
PACKED14_PAYLOAD_BYTES = CAM_W * CAM_H * 7 // 4
# Order-of-magnitude blosc2 size from ``raw_bay_writer`` (~1.43x vs uint16 plane).
BLOSC2_TYPICAL_PAYLOAD_BYTES = int(CAM_W * CAM_H * 2 / 1.43)


def load_payload_sizes(path: Path) -> list[int]:
    """Read payload sizes from a .bay14line index."""
    with path.open("rb") as f:
        prefix = f.read(LINE_HEADER_FIXED_PREFIX)
        header_len, n_frames, index_offset, _entry = parse_line_header_prefix(prefix)
        if n_frames <= 0:
            raise ValueError(f"no indexed frames in {path}")
        if index_offset <= 0:
            raise ValueError(f"{path} has no finalized index (index_offset=0)")
        f.seek(index_offset)
        blob = f.read(n_frames * LINE_INDEX_ENTRY_SIZE)
    entries = parse_line_index(blob, n_frames)
    return [int(e[3]) for e in entries]  # payload_len


def detect_target_roots() -> list[str]:
    """Return non-system recordable drive roots (same policy as app picker)."""
    drives = [d for d in list_drives() if not d.is_system]
    return [d.root for d in drives]


def replay_one_drive(
    *,
    drive_root: str,
    payload_sizes: list[int],
    out_dir_name: str,
    camera_serial: str,
    line_number: int,
    seed: int,
) -> dict[str, float]:
    """Replay payload sizes into one .bay14line file; return stats."""
    out_dir = Path(drive_root) / out_dir_name
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / (
        f"Cam{camera_serial}_{time.strftime('%Y%m%d')}_"
        f"replay_L{line_number}_{time.strftime('%H%M%S')}.bay14line"
    )

    max_payload = max(payload_sizes)
    rng = np.random.default_rng(seed)
    reusable = rng.integers(0, 256, size=max_payload, dtype=np.uint8).tobytes()

    writer = LineWriter(
        str(out_path),
        height=CAM_H,
        width=CAM_W,
        line_meta={
            "camera_serial": camera_serial,
            "project": "replay_bench",
            "line": line_number,
            "capture_kind": "sweep",
            "line_started_at": time.time(),
        },
        codec=COMPRESSION_BLOSC2_ZSTD1_SHUFFLE,
    )
    append_ms: list[float] = []
    t0 = time.perf_counter()
    writer.open()
    for i, plen in enumerate(payload_sizes):
        payload = reusable[:plen]
        t1 = time.perf_counter()
        writer.append_chunk(
            payload,
            sweep=(i // 13) + 1,
            frame_in_sweep=(i % 13) + 1,
            frame_meta={
                "acquire_timestamp": time.time(),
                "capture_kind": "sweep",
                "source": "disk_replay",
            },
            gps=None,
        )
        append_ms.append((time.perf_counter() - t1) * 1000.0)
    close_stats = writer.close_with_index()
    wall_s = time.perf_counter() - t0

    payload_total_mb = close_stats["payload_total"] / (1024**2)
    return {
        "frames": float(len(payload_sizes)),
        "append_ms_mean": float(statistics.mean(append_ms)),
        "append_ms_max": float(max(append_ms)),
        "append_ms_min": float(min(append_ms)),
        "payload_mb_mean": float(payload_total_mb / len(payload_sizes)),
        "payload_mb_total": float(payload_total_mb),
        "wall_s": float(wall_s),
        "throughput_mb_s": float(payload_total_mb / wall_s),
        "path": str(out_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile-line",
        default=str(REPO / "bench_outputs" / "training_live_like.bay14line"),
        help="Existing .bay14line used only to extract payload-size profile "
        "(ignored when --synthetic-payload-bytes is set).",
    )
    parser.add_argument(
        "--synthetic-payload-bytes",
        type=int,
        default=None,
        help="Fixed payload size per frame (bytes). When set, profile-line is "
        "not read; repeat for --frames. Constants PACKED14_PAYLOAD_BYTES and "
        "BLOSC2_TYPICAL_PAYLOAD_BYTES in this module match live geometry.",
    )
    parser.add_argument(
        "--drives",
        nargs="*",
        default=None,
        help="Drive roots to test, e.g. D:\\ E:\\ F:\\ (default: auto non-system drives).",
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=64,
        help="Number of payloads to replay per drive (0=all in profile).",
    )
    parser.add_argument(
        "--target-fps",
        type=float,
        default=4.76,
        help="Target producer FPS for margin calculation.",
    )
    parser.add_argument(
        "--out-dir-name",
        default="bench_writer_line_replay",
        help="Subdirectory created under each tested drive.",
    )
    parser.add_argument("--seed", type=int, default=1234)
    args = parser.parse_args()

    if args.synthetic_payload_bytes is not None:
        if args.frames <= 0:
            print("--frames must be positive when using --synthetic-payload-bytes")
            return 1
        nbytes = int(args.synthetic_payload_bytes)
        if nbytes <= 0 or nbytes > 512 * 1024 * 1024:
            print(f"bogus --synthetic-payload-bytes={nbytes}")
            return 1
        payload_sizes = [nbytes] * args.frames
        profile_note = (
            f"synthetic fixed payload {nbytes} bytes x {len(payload_sizes)} frames "
            f"(PACKED14 reference={PACKED14_PAYLOAD_BYTES} B, "
            f"blosc2~1.43x reference={BLOSC2_TYPICAL_PAYLOAD_BYTES} B)"
        )
        print(profile_note)
        mean_payload_mb = nbytes / (1024**2)
    else:
        profile_path = Path(args.profile_line).resolve()
        if not profile_path.is_file():
            print(f"Profile line file not found: {profile_path}")
            print(
                "Hint: use --synthetic-payload-bytes with --frames N, or pass "
                "--profile-line to a finalized .bay14line."
            )
            return 1

        payload_sizes = load_payload_sizes(profile_path)
        if args.frames > 0:
            payload_sizes = payload_sizes[: args.frames]
        if not payload_sizes:
            print("No payload sizes to replay.")
            return 1
        profile_note = str(profile_path)
        print(f"Profile file: {profile_path}")
        print(f"Profile frames available: {len(load_payload_sizes(profile_path))}")
        mean_payload_mb = statistics.mean(payload_sizes) / (1024**2)

    drives = args.drives or detect_target_roots()
    if not drives:
        print("No non-system recordable drives found.")
        return 1

    required_mb_s = mean_payload_mb * args.target_fps

    print(f"Replay frames/drive: {len(payload_sizes)}")
    print(
        f"Payload mean: {mean_payload_mb:.2f} MiB/frame "
        f"(ratio {RAW_MB / mean_payload_mb:.3f}x vs raw uint16)"
    )
    print(
        f"Required at {args.target_fps:.2f} fps: {required_mb_s:.1f} MB/s "
        f"({required_mb_s/1024:.3f} GB/s) per camera"
    )
    print(f"Drives: {', '.join(drives)}")
    print()

    results: list[dict[str, float]] = []
    for idx, drv in enumerate(drives):
        print(f"[{idx+1}/{len(drives)}] replay on {drv} ...")
        try:
            stats = replay_one_drive(
                drive_root=drv,
                payload_sizes=payload_sizes,
                out_dir_name=args.out_dir_name,
                camera_serial="replay",
                line_number=1,
                seed=args.seed + idx,
            )
            results.append(stats)
            margin_pct = (
                (stats["throughput_mb_s"] - required_mb_s) / required_mb_s * 100.0
            )
            print(
                f"  throughput={stats['throughput_mb_s']:.1f} MB/s  "
                f"append_mean={stats['append_ms_mean']:.0f} ms  "
                f"payload_mean={stats['payload_mb_mean']:.2f} MB  "
                f"margin={margin_pct:+.1f}%"
            )
            print(f"  output={stats['path']}")
        except Exception as exc:
            print(f"  FAILED on {drv}: {exc}")
        print()

    if not results:
        print("No successful drive replay runs.")
        return 1

    print("--- summary ---")
    for s in results:
        margin_pct = (s["throughput_mb_s"] - required_mb_s) / required_mb_s * 100.0
        print(
            f"  {Path(str(s['path'])).drive:>3}: "
            f"{s['throughput_mb_s']:.1f} MB/s  "
            f"(margin {margin_pct:+.1f}%)  "
            f"append_mean={s['append_ms_mean']:.0f} ms"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
