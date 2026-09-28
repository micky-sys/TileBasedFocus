"""Self-check for the M4 Fine view's zoom geometry (no hardware, no QApplication).

The Fine view exists so an operator can judge focus by eye, so the one thing it
must never do is scale the tile below 1:1 -- averaging sensor pixels together
blurs away the high spatial frequencies that focus is judged on and makes a
sharp lens look soft. That is exactly what happened once the leading-edge
tracker started pinning the ROI: the tile grew from 853x480 (sized from the
display constants) to 2680x1904, and the fill-the-pane scale silently went from
a 1.5x upscale to a ~0.5x downscale.

:func:`capture.focus_panel._fine_view_crop` is the geometry that replaced it.
These checks pin the invariant.

Run: ``py -3 tools/test_fine_view_zoom.py``
"""

from __future__ import annotations

import sys
from math import ceil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOST = REPO / "host"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HOST))

from capture.focus_panel import _fine_view_crop  # noqa: E402

# The tile the leading-edge tracker actually pins, measured on the rig:
# coarse frame 3350x2382 / tiles_per_frame 5 = 670x476, x SUBSAMPLE_FACTOR 4.
TRACKED_W, TRACKED_H = 2680, 1904
PANE_W, PANE_H = 1280, 720


def test_tracked_tile_is_cropped_not_shrunk() -> None:
    """The regression: the 2680x1904 tracked tile must be windowed, not squeezed."""
    x0, y0, cw, ch = _fine_view_crop(TRACKED_W, TRACKED_H, PANE_W, PANE_H, 1)
    assert (cw, ch) == (PANE_W, PANE_H), f"expected a pane-sized 1:1 window, got {cw}x{ch}"
    # Centred on the tile, which is where the tracker is holding the object.
    assert (x0, y0) == ((TRACKED_W - PANE_W) // 2, (TRACKED_H - PANE_H) // 2)
    assert x0 + cw <= TRACKED_W and y0 + ch <= TRACKED_H, "crop leaves the tile"


def test_zoom_divides_the_window() -> None:
    """Higher zoom shows fewer sensor pixels, magnified by exactly that integer."""
    for zoom, (want_w, want_h) in ((2, (640, 360)), (4, (320, 180))):
        _, _, cw, ch = _fine_view_crop(TRACKED_W, TRACKED_H, PANE_W, PANE_H, zoom)
        assert (cw, ch) == (want_w, want_h), f"zoom {zoom}x: got {cw}x{ch}"
        assert cw * zoom == PANE_W and ch * zoom == PANE_H, "does not fill the pane"


def test_fractional_zoom_covers_the_pane() -> None:
    """1.5x / 1.8x don't divide the pane evenly, so the crop must round UP.

    Rounding down would leave a bar of dead pane; rounding up overshoots by
    under one magnified pixel, which the label clips.
    """
    for zoom in (1.5, 1.8):
        _, _, cw, ch = _fine_view_crop(TRACKED_W, TRACKED_H, PANE_W, PANE_H, zoom)
        assert cw * zoom >= PANE_W, f"zoom {zoom}x leaves a bar: {cw}*{zoom}"
        assert ch * zoom >= PANE_H, f"zoom {zoom}x leaves a bar: {ch}*{zoom}"
        assert cw * zoom < PANE_W + zoom, f"zoom {zoom}x overshoots by a whole px"
    # 1.5x is the magnification the old display constants happened to produce,
    # so it should ask for about the tile the centre-circle path used to emit.
    _, _, cw, ch = _fine_view_crop(TRACKED_W, TRACKED_H, PANE_W, PANE_H, 1.5)
    assert (cw, ch) == (854, 480), f"expected ~the old 853x480 view, got {cw}x{ch}"


def test_small_tile_is_not_stretched_to_fill() -> None:
    """A tile smaller than the pane at 1x stays whole and stays 1:1.

    The old centre-circle fallback emits 853x480. Filling the pane with it meant
    a 1.5x upscale; at 1x it should now be shown sensor-native with the label
    centring it, not stretched.
    """
    x0, y0, cw, ch = _fine_view_crop(853, 480, PANE_W, PANE_H, 1)
    assert (x0, y0, cw, ch) == (0, 0, 853, 480), f"tile was not left whole: {cw}x{ch}"


def test_never_asks_for_a_downscale() -> None:
    """Sweep pane sizes, tile sizes and zooms: the crop always fits magnified.

    ``cw * zoom`` may overshoot the pane by under one magnified pixel (the crop
    rounds up so there are no bars); the label clips that. What must never
    happen is a crop so large the caller would have to scale it down.
    """
    for tw, th in ((1280, 720), (900, 500), (1921, 1081), (3840, 2160), (7, 3)):
        for w, h in ((TRACKED_W, TRACKED_H), (853, 480), (64, 64)):
            for zoom in (1, 1.5, 1.8, 2, 4):
                x0, y0, cw, ch = _fine_view_crop(w, h, tw, th, zoom)
                assert 1 <= cw <= w and 1 <= ch <= h, f"crop {cw}x{ch} outside {w}x{h}"
                assert x0 >= 0 and y0 >= 0 and x0 + cw <= w and y0 + ch <= h
                assert cw * zoom < tw + zoom, f"{cw}*{zoom} overshoots pane {tw}"
                # Either it fills the pane, or the whole tile is already showing.
                assert cw * zoom >= tw or cw == w, f"{cw}*{zoom} leaves bars in {tw}"
                assert ch * zoom >= th or ch == h, f"{ch}*{zoom} leaves bars in {th}"


def test_the_overview_inset_boxes_the_tracked_roi_where_it_actually_is() -> None:
    """The bottom-left inset has to put the box where the object is, not near it.

    This is the operator's only view of the whole frame -- at 1x the tile is a
    1280 px window onto 13400 px -- so it is what tells them an object is nearing
    the outflow edge and about to hand over mid-adjustment. A box in roughly the
    right place would be worse than none: it would read as traverse remaining.
    """
    import numpy as np                                      # noqa: E402
    from capture.pipeline import _m4_roi_overview_rgb       # noqa: E402

    # A coarse frame, and a ROI in coarse pixels (what the caller passes).
    FW, FH, DECIM = 3350, 2382, 8
    frame = np.full((FH, FW), 4000, dtype=np.uint16)
    roi = (2512, 1428, 3182, 1904)                           # right side, lower half
    ov = _m4_roi_overview_rgb(frame, roi, decim=DECIM)

    assert ov is not None and ov.ndim == 3 and ov.shape[2] == 3, ov
    assert ov.dtype == np.uint8, ov.dtype     # the panel drops anything else silently
    # Strided slicing keeps the partial last step, so round UP.
    assert ov.shape[:2] == (-(-FH // DECIM), -(-FW // DECIM)), ov.shape

    # The box is the only non-grey thing in a flat frame, so find it by colour and
    # check it lands on the ROI -- rather than trusting the arithmetic that drew it.
    r, g, b = ov[:, :, 0].astype(int), ov[:, :, 1].astype(int), ov[:, :, 2].astype(int)
    box = (g > r + 30) & (b > r + 30)
    assert box.any(), "no box was drawn"
    ys, xs = np.nonzero(box)
    tol = 3                                   # line width + antialiasing
    for got, want, name in (
        (xs.min(), roi[0] / DECIM, "left"), (xs.max(), roi[2] / DECIM, "right"),
        (ys.min(), roi[1] / DECIM, "top"),  (ys.max(), roi[3] / DECIM, "bottom"),
    ):
        assert abs(got - want) <= tol, f"{name} edge at {got}, expected ~{want:.0f}"

    # It must also track: a ROI further along the flow puts the box further along.
    moved = _m4_roi_overview_rgb(frame, (1000, 1428, 1670, 1904), decim=DECIM)
    mb = (moved[:, :, 1].astype(int) > moved[:, :, 0].astype(int) + 30)
    assert np.nonzero(mb)[1].min() < xs.min(), "the box did not follow the ROI"

    # Decoration must never take down the operator's only focus feedback.
    assert _m4_roi_overview_rgb(None, roi) is None
    assert _m4_roi_overview_rgb(np.zeros(4, dtype=np.uint16), roi) is None
    # A ROI hanging off the frame still has to produce a drawable box.
    assert _m4_roi_overview_rgb(frame, (-500, -500, 200, 200)) is not None
    assert _m4_roi_overview_rgb(frame, (FW - 10, FH - 10, FW + 900, FH + 900)) is not None


def test_a_pane_sized_tile_is_displayed_whole_at_every_zoom() -> None:
    """The rule the Fine pick is sized by: tile = ceil(pane / zoom) sensor px.

    ``FocusPanel.fine_view_sensor_wh`` returns exactly this, and app.py resizes the
    tracker's candidate to it on every coarse frame. This is the invariant that
    makes that worth the plumbing: a tile cut by the rule is displayed in FULL, so
    no scored pixel is cropped away and no pane pixel is left grey -- at any window
    size and any offered zoom. A fixed tile can only satisfy one row of this table,
    which is why a maximised window showed a part-filled pane at 1x.
    """
    panes = ((1280, 720), (1600, 900), (1920, 1080), (2560, 1440), (3840, 2160))
    for pane_w, pane_h in panes:
        for zoom in (1.0, 1.5, 1.8, 2.0, 4.0):
            tw, th = ceil(pane_w / zoom), ceil(pane_h / zoom)
            x0, y0, cw, ch = _fine_view_crop(tw, th, pane_w, pane_h, zoom)
            assert (cw, ch) == (tw, th), (
                f"{pane_w}x{pane_h} @ {zoom}x: scored {tw}x{th} but only "
                f"{cw}x{ch} is displayed -- the rest never reaches the screen"
            )
            assert (x0, y0) == (0, 0), f"tile should be shown whole, offset {x0},{y0}"
            assert round(cw * zoom) >= pane_w and round(ch * zoom) >= pane_h, (
                f"{pane_w}x{pane_h} @ {zoom}x: {cw}x{ch} magnified is "
                f"{round(cw * zoom)}x{round(ch * zoom)} -- leaves the pane grey"
            )

    # The regression itself: the nominal constant under-fills a larger pane at 1x.
    _x, _y, cw, ch = _fine_view_crop(1280, 720, 2560, 1440, 1.0)
    assert (cw, ch) == (1280, 720) and cw < 2560, "fixture no longer shows the bug"


def main() -> None:
    test_tracked_tile_is_cropped_not_shrunk()
    test_zoom_divides_the_window()
    test_fractional_zoom_covers_the_pane()
    test_small_tile_is_not_stretched_to_fill()
    test_never_asks_for_a_downscale()
    test_the_overview_inset_boxes_the_tracked_roi_where_it_actually_is()
    test_a_pane_sized_tile_is_displayed_whole_at_every_zoom()
    print("Fine view zoom geometry OK")


if __name__ == "__main__":
    main()
