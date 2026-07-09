"""Offline tests for the OrthoSim bench rig codec + calibration math (no Qt)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_TOOLS = Path(__file__).resolve().parent
_ROOT = _TOOLS.parent
_HOST = _ROOT / "host"
for _p in (str(_TOOLS), str(_ROOT), str(_HOST)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orthosim_codec as codec  # noqa: E402
from orthosim_core import (  # noqa: E402
    OrthoSimConfig,
    screen_gsd_cm,
    scroll_speed_screen_px_s,
    simulated_gsd_cm,
)
from tile_geometry import TileGrid  # noqa: E402


def test_codec_roundtrip_basic() -> None:
    gt = codec.GroundTruth(
        distance_mm=123456, tick=7777, velocity_mmps=40000, flags=codec.FLAG_SEAM
    )
    cells = codec.encode(gt)
    assert len(cells) == codec.TOTAL_CELLS
    out = codec.decode(cells)
    assert out is not None
    assert out.distance_mm == 123456
    assert out.tick == 7777
    assert out.velocity_mmps == 40000
    assert out.seam is True
    assert out.paused is False
    assert abs(out.distance_m - 123.456) < 1e-9
    assert abs(out.velocity_mps - 40.0) < 1e-9


def test_codec_roundtrip_extremes() -> None:
    for dist, tick, vel, flags in [
        (0, 0, 0, 0),
        ((1 << 32) - 1, (1 << 20) - 1, (1 << 20) - 1, 0x7),
        (999_999_999, 5, 250_000, codec.FLAG_PAUSED | codec.FLAG_CALIBRATION),
    ]:
        gt = codec.GroundTruth(distance_mm=dist, tick=tick, velocity_mmps=vel, flags=flags)
        out = codec.decode(codec.encode(gt))
        assert out is not None
        assert out.distance_mm == dist
        assert out.tick == tick
        assert out.velocity_mmps == vel
        assert out.flags == flags


def test_codec_rejects_bad_sync() -> None:
    cells = codec.encode(codec.GroundTruth(distance_mm=1))
    cells[0] ^= 1  # corrupt sync
    assert codec.decode(cells) is None


def test_codec_rejects_bad_crc() -> None:
    cells = codec.encode(codec.GroundTruth(distance_mm=1000))
    cells[codec.SYNC_BITS + 5] ^= 1  # flip a payload bit -> CRC mismatch
    assert codec.decode(cells) is None


def test_codec_wrong_length() -> None:
    assert codec.decode([0, 1, 0]) is None


def test_screen_gsd_scales_inverse_with_zoom() -> None:
    assert abs(screen_gsd_cm(5.0, 1.0) - 5.0) < 1e-9
    assert abs(screen_gsd_cm(5.0, 2.0) - 2.5) < 1e-9
    assert screen_gsd_cm(5.0, 0.0) == 0.0


def test_simulated_gsd_needs_magnification() -> None:
    assert abs(simulated_gsd_cm(5.0, 2.0, 1.0) - 2.5) < 1e-9
    assert abs(simulated_gsd_cm(5.0, 2.0, 2.0) - 1.25) < 1e-9
    assert simulated_gsd_cm(5.0, 2.0, 0.0) == 0.0


def test_scroll_speed_mapping() -> None:
    # 40 m/s at 5 cm/ortho-px = 800 ortho-px/s; at zoom 1 -> 800 screen-px/s
    v = scroll_speed_screen_px_s(40.0, 5.0, 1.0)
    assert abs(v - 800.0) < 1e-6
    # zoom 2 doubles the on-screen speed
    assert abs(scroll_speed_screen_px_s(40.0, 5.0, 2.0) - 1600.0) < 1e-6
    assert scroll_speed_screen_px_s(40.0, 0.0, 1.0) == 0.0


def test_config_json_roundtrip(tmp_path) -> None:
    cfg = OrthoSimConfig(ortho_gsd_cm=4.2, magnification=1.3, ground_speed_mps=55.0)
    p = tmp_path / "cfg.json"
    cfg.to_json(p)
    back = OrthoSimConfig.from_json(p)
    assert abs(back.ortho_gsd_cm - 4.2) < 1e-9
    assert abs(back.magnification - 1.3) < 1e-9
    assert abs(back.ground_speed_mps - 55.0) < 1e-9


# ── TileGrid interior region (bezel + strip exclusion) ───────────────────────
def test_tilegrid_interior_excludes_bezel_and_strip() -> None:
    fw, fh = 4000, 3000
    inset = 0.12
    band = 0.2
    grid = TileGrid(
        frame_width=fw,
        frame_height=fh,
        display_width=fw,
        display_height=fh,
        zoom=8.0,
        region="interior",
        interior_inset=inset,
        exclude_edge="top",
        edge_band=band,
    )
    assert len(grid) > 0
    ix0 = round(fw * inset)
    iy_bezel = round(fh * inset)
    iy_strip = round(fh * band)  # top band excluded -> stricter than bezel
    top_limit = max(iy_bezel, iy_strip)
    for t in grid:
        assert t.x0 >= ix0
        assert t.x1 <= fw - ix0
        assert t.y0 >= top_limit          # nothing in the excluded top strip
        assert t.y1 <= fh - iy_bezel


# ── Optical decode pipeline (synthetic rendered screen) ──────────────────────
def _render_screen(gt: codec.GroundTruth, layout) -> np.ndarray:
    """Render a synthetic OrthoSim display frame (grayscale) for decode tests."""
    cv2 = pytest.importorskip("cv2")
    if not hasattr(cv2, "aruco"):
        pytest.skip("cv2.aruco not available")
    w, h, s, m = layout.screen_w, layout.screen_h, layout.fiducial_px, layout.fiducial_margin_px
    canvas = np.full((h, w), 127, np.uint8)
    dic = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, layout.aruco_dict))
    positions = [(m, m), (w - m - s, m), (w - m - s, h - m - s), (m, h - m - s)]
    for mid, (x, y) in enumerate(positions):
        canvas[y - 6:y + s + 6, x - 6:x + s + 6] = 255
        canvas[y:y + s, x:x + s] = cv2.aruco.generateImageMarker(dic, mid, s)
    # optical bar
    cells = codec.encode(gt)
    n = len(cells)
    top = s + m + 12 if layout.bar_edge == "top" else h - layout.bar_height_px
    x0 = layout.bar_margin_px
    band_w = (w - layout.bar_margin_px) - x0
    cell_w = band_w / n
    for i, bit in enumerate(cells):
        cx0 = int(x0 + i * cell_w)
        cx1 = int(x0 + (i + 1) * cell_w) + 1
        canvas[top:top + layout.bar_height_px, cx0:cx1] = 255 if bit else 0
    return canvas


def test_decode_frontoparallel() -> None:
    pytest.importorskip("cv2")
    from capture.orthosim_decode import ScreenLayout, decode_frame

    layout = ScreenLayout(screen_w=1600, screen_h=1000)
    gt = codec.GroundTruth(distance_mm=987654, tick=4242, velocity_mmps=55000, flags=0)
    frame = _render_screen(gt, layout)
    out = decode_frame(frame, layout)
    assert out is not None
    assert out.distance_mm == 987654
    assert out.velocity_mmps == 55000
    assert out.tick == 4242


def test_decode_with_perspective() -> None:
    cv2 = pytest.importorskip("cv2")
    from capture.orthosim_decode import ScreenLayout, decode_frame

    layout = ScreenLayout(screen_w=1600, screen_h=1000)
    gt = codec.GroundTruth(distance_mm=123, tick=9, velocity_mmps=1000, flags=codec.FLAG_SEAM)
    frame = _render_screen(gt, layout)
    h, w = frame.shape
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[40, 25], [w - 20, 10], [w - 55, h - 30], [15, h - 12]])
    warped = cv2.warpPerspective(
        frame, cv2.getPerspectiveTransform(src, dst), (w, h), borderValue=127
    )
    out = decode_frame(warped, layout)
    assert out is not None
    assert out.distance_mm == 123
    assert out.seam is True


def test_speed_distance_tracker() -> None:
    pytest.importorskip("cv2")
    from capture.orthosim_decode import SpeedDistanceTracker

    tr = SpeedDistanceTracker(ema=1.0)  # no smoothing for a clean check
    # 40 m/s: distance advances 40 m each 1.0 s tick
    tr.update(codec.GroundTruth(distance_mm=0, velocity_mmps=40000), now=100.0)
    m = tr.update(codec.GroundTruth(distance_mm=40000, velocity_mmps=40000), now=101.0)
    assert m is not None
    assert abs(m.velocity_mps - 40.0) < 1e-6
    assert abs(m.distance_m - 40.0) < 1e-6
    assert abs(m.encoded_velocity_mps - 40.0) < 1e-6
