"""End-to-end: write a tiny .bay14line, then convert it via bay14_to_tif.py.

This exercises the full per-line pipeline outside the live capture
app, so we can shake out the format + extraction logic without
needing the cameras attached.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

import numpy as np
import tifffile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "host"))
sys.path.insert(0, ROOT)

from capture.raw_bay_writer import (  # noqa: E402
    COMPRESSION_BLOSC2_ZSTD1_SHUFFLE,
    LineWriter,
    compress_frame_blosc2,
)


H, W = 480, 640  # tiny so the tool runs in <1 s
N_FRAMES = 5


def make_frames(n: int) -> list[np.ndarray]:
    rng = np.random.default_rng(42)
    return [
        np.ascontiguousarray(
            rng.integers(0, 1 << 14, size=(H, W), dtype=np.uint16)
        )
        for _ in range(n)
    ]


def main() -> int:
    frames = make_frames(N_FRAMES)
    with tempfile.TemporaryDirectory() as tmp:
        line_path = os.path.join(
            tmp, "Cam1234_20260506_DemoProj_L7_120000.bay14line",
        )
        w = LineWriter(
            line_path, height=H, width=W,
            line_meta={
                "camera_serial": "1234",
                "project": "DemoProj",
                "line": 7,
                "capture_kind": "sweep",
                "line_started_at": "2026-05-06T12:00:00.000",
            },
            codec=COMPRESSION_BLOSC2_ZSTD1_SHUFFLE,
        )
        w.open()
        for i, fr in enumerate(frames):
            payload = compress_frame_blosc2(fr)
            w.append_chunk(
                payload,
                sweep=1, frame_in_sweep=i + 1,
                frame_meta={
                    "acquire_timestamp": f"2026-05-06T12:00:0{i}",
                    "capture_kind": "sweep",
                },
            )
        w.close_with_index(fsync=False)

        out_dir = os.path.join(tmp, "tifs")
        cmd = [
            sys.executable,
            os.path.join(HERE, "bay14_to_tif.py"),
            line_path,
            "--out-dir", out_dir,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr, file=sys.stderr)
        if result.returncode != 0:
            print(f"converter returned {result.returncode}", file=sys.stderr)
            return 1

        tif_files = sorted(os.listdir(out_dir))
        assert len(tif_files) == N_FRAMES, (tif_files, N_FRAMES)

        for i, tif_name in enumerate(tif_files):
            tif_path = os.path.join(out_dir, tif_name)
            with tifffile.TiffFile(tif_path) as tf:
                page = tf.pages[0]
                arr = page.asarray()
                desc = page.tags["ImageDescription"].value
            assert arr.shape == (H, W), arr.shape
            assert arr.dtype == np.uint16, arr.dtype
            assert np.array_equal(arr, frames[i]), f"frame {i} mismatch"
            meta = json.loads(desc)
            assert meta["camera_serial"] == "1234"
            assert meta["project"] == "DemoProj"
            assert meta["line"] == 7
            assert meta["sweep"] == 1
            assert meta["frame_in_sweep"] == i + 1
            assert meta["compression"] == COMPRESSION_BLOSC2_ZSTD1_SHUFFLE

        print(f"OK: extracted {N_FRAMES} TIFFs from .bay14line, "
              f"all pixel-perfect, all metadata preserved.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
