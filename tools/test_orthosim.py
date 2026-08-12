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


def test_codec_v2_layout() -> None:
    # v2 added the 16-bit gsd field in place of the 8-bit reserved pad.
    assert codec.FORMAT_VERSION == 2
    assert codec.PAYLOAD_BITS == 96
    assert codec.TOTAL_CELLS == 120


def test_codec_roundtrip_basic() -> None:
    gt = codec.GroundTruth(
        distance_mm=123456, tick=7777, velocity_mmps=40000, gsd_cm_e2=250,
        flags=codec.FLAG_SEAM,
    )
    cells = codec.encode(gt)
    assert len(cells) == codec.TOTAL_CELLS
    out = codec.decode(cells)
    assert out is not None
    assert out.distance_mm == 123456
    assert out.tick == 7777
    assert out.velocity_mmps == 40000
    assert out.gsd_cm_e2 == 250
    assert out.seam is True
    assert out.paused is False
    assert abs(out.distance_m - 123.456) < 1e-9
    assert abs(out.velocity_mps - 40.0) < 1e-9
    assert abs(out.gsd_cm - 2.5) < 1e-9


def test_codec_roundtrip_extremes() -> None:
    for dist, tick, vel, gsd, flags in [
        (0, 0, 0, 0, 0),
        ((1 << 32) - 1, (1 << 20) - 1, (1 << 20) - 1, (1 << 16) - 1, 0x7),
        (999_999_999, 5, 250_000, 12345, codec.FLAG_PAUSED | codec.FLAG_CALIBRATION),
    ]:
        gt = codec.GroundTruth(
            distance_mm=dist, tick=tick, velocity_mmps=vel, gsd_cm_e2=gsd, flags=flags
        )
        out = codec.decode(codec.encode(gt))
        assert out is not None
        assert out.distance_mm == dist
        assert out.tick == tick
        assert out.velocity_mmps == vel
        assert out.gsd_cm_e2 == gsd
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


def _interior_lead_cfg(exclude_edge=None):
    """AF config as the OrthoSim toggle arms it: interior masking on, optionally
    with the optical-strip band excluded."""
    from capture.autofocus import AutoFocusConfig

    cfg = AutoFocusConfig()
    cfg.restrict_interior = True
    cfg.exclude_edge = exclude_edge
    return cfg


def _graded_frame(fw: int, fh: int) -> np.ndarray:
    """Textured frame whose detail fades toward the bottom, so a selector that
    ignores the leading edge would pick near the TOP. Lets us prove the leading-edge
    band actually constrains the choice rather than coinciding with it."""
    rng = np.random.default_rng(3)
    noise = rng.integers(0, 4096, size=(fh, fw)).astype(np.float32)
    fade = np.linspace(1.0, 0.02, fh, dtype=np.float32)[:, None]
    return (noise * fade + 200).astype(np.uint16)


def test_interior_masking_composes_with_leading_edge() -> None:
    """Interior masking and leading-edge preference must BOTH apply on a moving bench
    scene: off the bezel AND at the incoming edge. Masking used to win outright, which
    handed back tiles already part-way across the usable area (short dwell)."""
    pytest.importorskip("cv2")
    from capture.autofocus import select_leading_edge_roi

    fw, fh = 3200, 2400
    cfg = _interior_lead_cfg()
    frame = _graded_frame(fw, fh)
    inset_x = round(fw * cfg.interior_inset)

    # Control: texture fades downward, so with no lead edge the pick goes up top.
    (_, ny0, _, ny1), _ = select_leading_edge_roi(frame, None, cfg)
    top_centre = (ny0 + ny1) / 2

    # lead_edge="bottom" must drag the pick to the lowest usable row despite that row
    # having the LEAST texture -- proof the preference overrides the texture argmax.
    (x0, y0, x1, y1), score = select_leading_edge_roi(frame, "bottom", cfg)
    bottom_centre = (y0 + y1) / 2
    assert bottom_centre > top_centre, (
        f"leading-edge pick {bottom_centre:.0f} no lower than untargeted {top_centre:.0f}"
    )
    assert score > 0.0
    assert x0 >= inset_x and x1 <= fw - inset_x, "tile drifted onto the side bezel"
    assert y1 <= fh - round(fh * cfg.interior_inset), "tile drifted onto the bottom bezel"

    # And the opposite edge resolves the other way, so this tracks lead_edge rather
    # than just always returning an extreme.
    (_, ty0, _, ty1), _ = select_leading_edge_roi(frame, "top", cfg)
    assert (ty0 + ty1) / 2 < bottom_centre


def test_interior_lead_band_still_respects_excluded_strip() -> None:
    """Leading-edge preference must not drag a tile into the excluded optical-strip
    band -- that band is exactly the 'false readings from the surrounding area' case.
    Worst case: the strip and the leading edge are the SAME edge."""
    pytest.importorskip("cv2")
    from capture.autofocus import select_leading_edge_roi

    fw, fh = 3200, 2400
    cfg = _interior_lead_cfg(exclude_edge="bottom")
    frame = _graded_frame(fw, fh)

    (x0, y0, x1, y1), score = select_leading_edge_roi(frame, "bottom", cfg)
    assert x1 > x0 and y1 > y0, "degenerate ROI"
    assert score > 0.0
    assert y1 <= fh - round(fh * cfg.edge_band), "tile sits in the excluded strip"


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
    # optical bar -- MUST mirror tools/orthosim_gui.py::_draw_bar so this test
    # exercises the real display geometry (the bar shares the fiducial row and is
    # inset to clear the corner markers), not a stale layout that only agrees with
    # itself. If this drifts from _draw_bar, decode fails on the real rig.
    cells = codec.encode(gt)
    n = len(cells)
    bh = layout.bar_height_px
    band_top = m if layout.bar_edge == "top" else h - m - s
    top = band_top + max(0, (s - bh) // 2)
    clear = m + s + 6 + layout.bar_margin_px
    x0 = clear
    band_w = (w - clear) - x0
    cell_w = band_w / n
    for i, bit in enumerate(cells):
        cx0 = int(x0 + i * cell_w)
        cx1 = int(x0 + (i + 1) * cell_w) + 1
        canvas[top:top + layout.bar_height_px, cx0:cx1] = 255 if bit else 0
    return canvas


def test_detect_fiducials_reports_which_markers_it_saw() -> None:
    """The field map has to tell a blank display from one obscured corner, and a
    bare None cannot. Occlusion still fails the detect -- it just says why."""
    pytest.importorskip("cv2")
    from capture.orthosim_decode import ScreenLayout, detect_fiducials

    layout = ScreenLayout(screen_w=1600, screen_h=1000)
    gt = codec.GroundTruth(distance_mm=1, tick=1, velocity_mmps=1, flags=0)
    frame = _render_screen(gt, layout)

    seen: list = []
    assert detect_fiducials(frame, layout.aruco_dict, seen) is not None
    assert seen == [0, 1, 2, 3], f"expected all four markers, got {seen}"

    s, m = layout.fiducial_px, layout.fiducial_margin_px
    covered = frame.copy()
    covered[m - 8:m + s + 8, m - 8:m + s + 8] = 127   # someone leans over marker 0
    seen = []
    assert detect_fiducials(covered, layout.aruco_dict, seen) is None
    assert 0 not in seen and len(seen) == 3, f"expected ids 1,2,3, got {seen}"

    seen = []
    blank = np.full_like(frame, 127)
    assert detect_fiducials(blank, layout.aruco_dict, seen) is None
    assert seen == [], f"blank display should see nothing, got {seen}"


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


def test_decode_survives_uneven_bar_illumination() -> None:
    """The rig images the bar with a strong left-to-right falloff (white ~205 at one
    end vs ~43 at the other, black ~15 throughout), which puts the dim end's white
    cells under a single global midpoint. Reproduce that ramp and require a decode --
    and check the old global threshold really does fail on it, so this test would
    notice if the local-envelope thresholding were reverted."""
    pytest.importorskip("cv2")
    from capture.orthosim_decode import ScreenLayout, _sample_bits, decode_rectified

    layout = ScreenLayout(screen_w=1600, screen_h=1000)
    gt = codec.GroundTruth(distance_mm=543620833, tick=725578, velocity_mmps=27440,
                           gsd_cm_e2=351, flags=0)
    screen = _render_screen(gt, layout).astype(np.float32)
    ramp = np.linspace(1.0, 0.21, screen.shape[1], dtype=np.float32)  # measured falloff
    dim = np.clip(screen * ramp[None, :], 0, 255).astype(np.uint8)

    out = decode_rectified(dim, layout)
    assert out is not None, "uneven illumination defeated the decode"
    assert out.distance_mm == 543620833
    assert out.velocity_mmps == 27440
    assert out.gsd_cm_e2 == 351

    # Same cell levels, old global (max+min)/2 rule -> should not survive CRC.
    centers, cell_w = layout.bar_cell_centers()
    win = max(1, int(cell_w * 0.3))
    lv = np.array([dim[int(cy) - win:int(cy) + win + 1,
                       int(cx) - win:int(cx) + win + 1].mean() for cx, cy in centers])
    thr = (lv.max() + lv.min()) / 2.0
    assert codec.decode([1 if v >= thr else 0 for v in lv]) is None, \
        "global threshold coped, so this frame does not exercise the fix"
    assert _sample_bits(dim, layout) == list(codec.encode(gt))


def test_measure_scale_recovers_known_magnification() -> None:
    """``measure_scale`` must recover the camera-px/display-px M that the altitude
    readout otherwise assumes from a hand-set ``--M``. Image the same screen at
    known, deliberately anisotropic scales and check both axes come back."""
    cv2 = pytest.importorskip("cv2")
    from capture.orthosim_decode import (
        ScreenLayout, _to_gray_u8, detect_fiducials, measure_scale,
    )

    layout = ScreenLayout(screen_w=1600, screen_h=1000)
    screen = _render_screen(codec.GroundTruth(distance_mm=1), layout)
    for fx, fy in ((1.0, 1.0), (0.75, 0.75), (1.5, 1.25)):
        h, w = screen.shape
        imaged = cv2.resize(screen, (int(w * fx), int(h * fy)),
                            interpolation=cv2.INTER_AREA)
        src = detect_fiducials(_to_gray_u8(imaged), layout.aruco_dict)
        assert src is not None, f"markers not detected at {fx}x{fy}"
        mx, my = measure_scale(src, layout)
        assert mx == pytest.approx(fx, rel=0.02), f"x scale {mx} != {fx}"
        assert my == pytest.approx(fy, rel=0.02), f"y scale {my} != {fy}"


def test_measure_scale_is_not_fooled_by_tilt() -> None:
    """A modest perspective tilt must not bias M: averaging opposite edges is the
    whole reason the two-edge mean is there rather than a single edge."""
    cv2 = pytest.importorskip("cv2")
    from capture.orthosim_decode import (
        ScreenLayout, _to_gray_u8, detect_fiducials, measure_scale,
    )

    layout = ScreenLayout(screen_w=1600, screen_h=1000)
    screen = _render_screen(codec.GroundTruth(distance_mm=1), layout)
    h, w = screen.shape
    # Keystone: top edge squeezed, bottom stretched by the same amount.
    src_q = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst_q = np.float32([[30, 0], [w - 30, 0], [w, h], [0, h]])
    warped = cv2.warpPerspective(
        screen, cv2.getPerspectiveTransform(src_q, dst_q), (w, h), borderValue=127
    )
    src = detect_fiducials(_to_gray_u8(warped), layout.aruco_dict)
    assert src is not None
    mx, _ = measure_scale(src, layout)
    assert mx == pytest.approx(1.0, rel=0.05), f"tilt biased M to {mx}"


def test_speed_distance_tracker() -> None:
    pytest.importorskip("cv2")
    from capture.orthosim_decode import SpeedDistanceTracker

    tr = SpeedDistanceTracker(ema=1.0)  # no smoothing for a clean check
    # 40 m/s: distance advances 40 m each 1.0 s tick; gsd 2.5 cm/px flows through
    tr.update(codec.GroundTruth(distance_mm=0, velocity_mmps=40000, gsd_cm_e2=250), now=100.0)
    m = tr.update(
        codec.GroundTruth(distance_mm=40000, velocity_mmps=40000, gsd_cm_e2=250), now=101.0
    )
    assert m is not None
    assert abs(m.velocity_mps - 40.0) < 1e-6
    assert abs(m.distance_m - 40.0) < 1e-6
    assert abs(m.encoded_velocity_mps - 40.0) < 1e-6
    assert abs(m.gsd_cm - 2.5) < 1e-6


def test_gt_logger_survives_a_failing_sink(tmp_path) -> None:
    """A CSV write failure must not escape maybe_log, and must disable the log.

    orthosim_gui._on_tick() calls self.update() AFTER the logger, so an exception here
    skips the repaint: the display stops animating while the internal scroll keeps
    advancing, which a bench run reads as a *frozen scene* with no visible signal that
    anything broke. Seen live 2026-08-10 -- the default CSV path sits next to the .tif on
    the Google Drive stream, which threw Errno 22 on every flush for 12.5 h straight.
    """
    from orthosim_core import GroundTruthLogger

    log = GroundTruthLogger(tmp_path / "gt.csv", sample_hz=1000.0)
    log.open()

    class _ExplodingSink:
        def flush(self) -> None:
            raise OSError(22, "Invalid argument")

    log._fh = _ExplodingSink()  # csv.writer still holds the real file; the flush blows up

    kw = dict(tick=1, distance_m=1.0, velocity_mps=2.0, screen_gsd_cm_value=3.0,
              simulated_gsd_cm_value=4.0, zoom=1.0, flags=0)

    log.maybe_log(**kw)                     # must swallow the OSError
    assert log._writer is None, "logger should disable itself after a write failure"

    log._last_perf = 0.0                    # defeat the rate limit
    log.maybe_log(**kw)                     # and stay quiet forever after
