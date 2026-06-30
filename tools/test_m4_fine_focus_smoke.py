"""Smoke test for M4 Fine tile selection + BayerRG demosaic (no hardware).

Uses :func:`capture.pipeline.m4_fine_tile_rgb_from_frame` on a synthetic
uint16 frame with an artificial sharp pattern in the centre.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
HOST = REPO / "host"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HOST))

from capture.pipeline import (  # noqa: E402
    M4_FINE_DISPLAY_H,
    M4_FINE_DISPLAY_W,
    M4_FINE_ZOOM,
    M4_FOCUS_OVERVIEW_DECIM,
    m4_fine_tile_rgb_from_frame,
)


def test_fine_tile_rgb_shape_and_targets_centre() -> None:
    fh, fw = 960, 1344
    raw = np.zeros((fh, fw), dtype=np.uint16)
    raw[:, :] = 1000

    tw = int(M4_FINE_DISPLAY_W / M4_FINE_ZOOM) & ~1
    th = int(M4_FINE_DISPLAY_H / M4_FINE_ZOOM) & ~1
    y0 = (fh // 2 - th // 2) & ~1
    x0 = (fw // 2 - tw // 2) & ~1
    patch = raw[y0 : y0 + th, x0 : x0 + tw]
    patch[::8, ::8] = 16000

    rgb, result, crop_u16, overview_rgb = m4_fine_tile_rgb_from_frame(
        raw,
        display_w=M4_FINE_DISPLAY_W,
        display_h=M4_FINE_DISPLAY_H,
        zoom=M4_FINE_ZOOM,
    )

    assert rgb.dtype == np.uint8 and rgb.ndim == 3 and rgb.shape[2] == 3
    assert overview_rgb.dtype == np.uint8 and overview_rgb.ndim == 3
    assert overview_rgb.shape[2] == 3
    step = M4_FOCUS_OVERVIEW_DECIM
    assert overview_rgb.shape[0] == (fh + step - 1) // step
    assert overview_rgb.shape[1] == (fw + step - 1) // step
    assert crop_u16.dtype == np.uint16 and crop_u16.ndim == 2
    assert rgb.shape[0] == crop_u16.shape[0] == th
    assert rgb.shape[1] == crop_u16.shape[1] == tw

    from tile_geometry import TileGrid  # noqa: E402

    grid = TileGrid(
        frame_width=fw,
        frame_height=fh,
        display_width=M4_FINE_DISPLAY_W,
        display_height=M4_FINE_DISPLAY_H,
        zoom=M4_FINE_ZOOM,
    )
    t = grid[result.best_index]
    tcx = t.x0 + t.width // 2
    tcy = t.y0 + t.height // 2
    assert abs(tcx - fw // 2) <= max(t.width, tw // 2)
    assert abs(tcy - fh // 2) <= max(t.height, th // 2)


def test_fine_tile_no_demosaic_gray_rgb() -> None:
    fh, fw = 960, 1344
    raw = np.zeros((fh, fw), dtype=np.uint16)
    raw[:, :] = 1000
    tw = int(M4_FINE_DISPLAY_W / M4_FINE_ZOOM) & ~1
    th = int(M4_FINE_DISPLAY_H / M4_FINE_ZOOM) & ~1
    y0 = (fh // 2 - th // 2) & ~1
    x0 = (fw // 2 - tw // 2) & ~1
    raw[y0 : y0 + th, x0 : x0 + tw][::8, ::8] = 16000

    rgb, _result, crop_u16, _ov = m4_fine_tile_rgb_from_frame(
        raw,
        display_w=M4_FINE_DISPLAY_W,
        display_h=M4_FINE_DISPLAY_H,
        zoom=M4_FINE_ZOOM,
        demosaic=False,
    )
    assert rgb.shape[2] == 3
    assert np.all(rgb[:, :, 0] == rgb[:, :, 1])
    assert np.all(rgb[:, :, 1] == rgb[:, :, 2])
    assert crop_u16.dtype == np.uint16


def main() -> int:
    test_fine_tile_rgb_shape_and_targets_centre()
    test_fine_tile_no_demosaic_gray_rgb()
    print("m4 fine focus smoke OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
