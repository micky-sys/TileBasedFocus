"""Self-check for FineFocusTracker (leading-edge tile lock + follow)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "host"))

from capture.autofocus import (  # noqa: E402
    FineFocusTracker,
    PatchTracker,
    select_leading_edge_roi,
)
from capture.autofocus_v3 import AutoFocusConfigV3  # noqa: E402
from capture.focus_panel import _fine_view_crop  # noqa: E402
from capture.pipeline import (  # noqa: E402
    M4_FINE_PICK_TILE_H,
    M4_FINE_PICK_TILE_W,
)


def _textured(h: int, w: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 4096, (h, w), dtype=np.uint16)
    stripes = ((np.arange(w) // 8) % 2).astype(np.uint16) * 800
    return np.clip(base + stripes[None, :], 0, 16383).astype(np.uint16)


def test_locks_and_follows() -> None:
    h, w = 240, 320
    tr = FineFocusTracker()
    f0 = _textured(h, w, 1)
    roi0 = tr.update(f0)
    assert roi0 is not None, "first lock failed"

    f = f0
    rois = [roi0]
    for _ in range(8):
        f = np.roll(f, shift=2, axis=1)
        roi = tr.update(f)
        assert roi is not None, "track lost"
        rois.append(roi)

    # Tile should move with content (same size, not stuck at origin forever).
    assert any(r != rois[0] for r in rois[1:]), "ROI never moved across frames"
    assert tr.frames_on_object > 0, "dwell counter never advanced"


def test_reacquires_at_the_trailing_edge() -> None:
    """The object must be handed over, not left pinned on the border.

    _clamp_roi only returns None for an invalid SIZE, so the old None-only test
    never fired and the tile sat stuck against the outflow edge with ground
    sliding under it. Pan far enough to push the patch off and the tracker has
    to pick a FRESH leading-edge object -- a new object_id with its dwell reset.
    """
    h, w = 240, 320
    tr = FineFocusTracker()
    f = _textured(h, w, 2)
    assert tr.update(f) is not None, "first lock failed"
    first_id = tr.object_id

    # Pan more than a full frame width, so whatever was picked must exit.
    for _ in range(40):
        f = np.roll(f, shift=10, axis=1)
        assert tr.update(f) is not None, "track lost mid-pan"

    assert tr.object_id > first_id, (
        f"never re-acquired: still object #{tr.object_id} after panning "
        f"{40 * 10} px across a {w} px frame -- the tile is stuck on the border"
    )
    assert tr.frames_on_object < 40, "dwell counter not reset on handover"
    x0, _y0, x1, _y1 = tr.roi
    assert 0 < x0 and x1 < w, (
        f"re-acquired patch {tr.roi} is itself against a border, not in-frame"
    )


def test_frames_left_tracks_the_exit() -> None:
    """The dwell estimate has to fall as the object approaches the trailing edge."""
    h, w = 240, 320
    tr = FineFocusTracker()
    f = _textured(h, w, 3)
    tr.update(f)
    f = np.roll(f, shift=4, axis=1)
    tr.update(f)
    start_id, first = tr.object_id, tr.frames_left()
    assert first is not None and first > 0, f"no exit estimate ({first})"

    seen = [first]
    for _ in range(6):
        f = np.roll(f, shift=4, axis=1)
        tr.update(f)
        if tr.object_id != start_id:
            break              # handed over; the estimate resets by design
        left = tr.frames_left()
        assert left is not None
        seen.append(left)
    assert seen[-1] < seen[0], f"estimate never fell while panning: {seen}"


def test_static_scene_reports_no_flow() -> None:
    h, w = 240, 320
    tr = FineFocusTracker()
    f = _textured(h, w, 4)
    for _ in range(4):
        tr.update(f)          # same frame: no motion at all
    assert tr.frames_left() is None, "invented an exit estimate with no flow"


def test_acquires_on_a_bland_frame() -> None:
    """A low-texture scene must still yield a target to hand-focus on.

    This is the bug the rig exposed: the pick was gated on min_texture_score, so an
    out-of-focus lens -- which by definition has almost no texture -- produced
    "no object yet (too bland)" on every frame and never handed over a tile. The
    operator needs texture to appear, but needed a tile to create the texture.
    select_leading_edge_roi always returns the best available tile, so there is
    something to return; _reacquire never gated on score either.
    """
    h, w = 240, 320
    rng = np.random.default_rng(7)
    # Near-flat: a faint gradient plus a couple of counts of noise.
    bland = (
        1000
        + (np.arange(w, dtype=np.uint16) // 64)[None, :]
        + rng.integers(0, 3, (h, w), dtype=np.uint16)
    ).astype(np.uint16)

    tr = FineFocusTracker()
    roi = tr.update(bland)
    assert roi is not None, (
        "refused to pick a target on a bland frame -- an out-of-focus lens can "
        "never bootstrap, which is exactly the rig failure"
    )
    assert tr.object_id == 1, f"no object adopted (object_id={tr.object_id})"
    x0, y0, x1, y1 = roi
    assert x1 > x0 and y1 > y0, f"degenerate ROI {roi}"
    assert tr.pick_score < tr.cfg.min_texture_score, (
        f"test scene is not actually bland (score {tr.pick_score:.2f} >= "
        f"{tr.cfg.min_texture_score}); it would have passed the old gate anyway"
    )


def test_tile_advances_with_the_scroll_even_on_static_content() -> None:
    """The tile must move with the global flow, period -- never weld to the sensor.

    This is the regression that cost a bench cycle. An appearance re-match was added
    on top of dead reckoning, and the sharpest-tile pick favours things that are
    static in SENSOR coordinates (fiducials, screen surround, sensor dust). For such
    a patch the true template match sits at ``predicted - (dx,dy)``, inside any sane
    search window, so the re-match cancelled the scroll every frame and the tile sat
    still while the scene moved under it -- reported from the rig as "it now just
    dwells on the same place in the sensor".

    Here the tile starts ON a static marker while the background scrolls. Correct
    behaviour is to advance with the flow and leave the marker behind.
    """
    h, w = 240, 320
    bg = _textured(h, w, 31)
    step = 6
    marker_x, marker_y = 120, 100

    def scene(i: int) -> np.ndarray:
        f = np.roll(bg, shift=step * i, axis=1)
        f[marker_y:marker_y + 40, marker_x:marker_x + 40] = 16000  # fixed on sensor
        return f

    tr = FineFocusTracker()
    tr.update(scene(0))                  # prime the motion reference
    tr.roi = (marker_x, marker_y, marker_x + 40, marker_y + 40)
    tr.lead_edge = "left"                # skip the static->lead upgrade re-pick
    start_x = tr.roi[0]
    first_id = tr.object_id

    for i in range(1, 9):
        got = tr.update(scene(i))
        assert got is not None, f"lost the tile at frame {i}"
        if tr.object_id != first_id:
            break                        # re-picked (legitimate); stop checking
        assert got[0] > start_x, (
            f"frame {i}: tile still at x={got[0]} (started {start_x}) while the "
            f"scene scrolled {step * i} px -- it is welded to the sensor, not "
            f"following the ground"
        )


def test_coarse_tap_is_not_gated_on_fine_focus() -> None:
    """The Fine tracker must actually RECEIVE frames.

    Structural, because the bug was structural: ``raw_focus_frame.emit`` was nested
    inside ``elif not self._fine_focus:``, so in Fine mode the GUI never got a coarse
    frame, ``_update_fine_leading_edge`` never ran, ``set_focus_roi`` was never
    called, and the displayed tile silently fell back to the pipeline's centre-circle
    auto-pick. Every unit test of the tracker still passed -- it was simply never
    called. Asserting on indentation would be brittle, so assert on the AST: no
    ancestor branch of that emit may test ``_fine_focus``.
    """
    import ast

    src = (ROOT / "host" / "capture" / "pipeline.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node

    emits = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and n.attr == "emit"
        and isinstance(n.value, ast.Attribute)
        and n.value.attr == "raw_focus_frame"
    ]
    assert emits, "raw_focus_frame.emit not found -- did the focus tap get renamed?"

    for emit in emits:
        node: ast.AST = emit
        while id(node) in parents:
            parent = parents[id(node)]
            if isinstance(parent, ast.If):
                names = {
                    a.attr for a in ast.walk(parent.test)
                    if isinstance(a, ast.Attribute)
                }
                assert "_fine_focus" not in names, (
                    "raw_focus_frame.emit is gated on _fine_focus, so Fine mode "
                    "gets no coarse frames and the leading-edge tracker can never "
                    "run (tile falls back to the centre circle)"
                )
            node = parent


def test_the_pick_searches_the_whole_leading_edge() -> None:
    """The pick must consider the full width of the leading edge, not one tile.

    The tracker shares the app's AF3 config (so OrthoSim interior masking reaches
    it), and that config carries ``acquire_lane_frac`` = 0.34 for AF3's
    long-traverse patches. On a 3350x2382 coarse frame the lane cut the five
    leading-edge candidates down to exactly one, mid-height, so the winning-tile
    scoring had nothing to choose between and every pick landed in the same spot
    whatever was there. FineFocusTracker overrides the lane; prove it, and prove
    the lane really would have blocked the detail.
    """
    h, w = 2382, 3350
    cfg = AutoFocusConfigV3()
    assert cfg.acquire_lane_frac > 0.0, "config no longer carries a lane to override"

    # Flat frame, with the only focusable detail on the leading (right) edge but
    # near the TOP -- i.e. outside the centre lane.
    frame = np.full((h, w), 2000, dtype=np.uint16)
    frame[120:360, 2600:3100] = _textured(240, 500, 7)
    marker_y = 240

    lane_roi, _ = select_leading_edge_roi(frame, "right", cfg)
    # edge_band=0.0 matches what the tracker asks for, so the only difference left
    # between this reference and the tracker's pick below is the lane -- which is
    # what this test is about. Without it the band depth differs too and the
    # comparison stops being a statement about the lane at all.
    full_roi, _ = select_leading_edge_roi(
        frame, "right", cfg, lane_frac=0.0, edge_band=0.0)

    lane_cy = (lane_roi[1] + lane_roi[3]) // 2
    full_cy = (full_roi[1] + full_roi[3]) // 2
    assert abs(lane_cy - h // 2) < 100, (
        f"the shipped lane should pin the pick mid-height, got y={lane_cy}"
    )
    assert full_roi[1] <= marker_y <= full_roi[3], (
        f"lane_frac=0 should find the detail at y={marker_y}, picked {full_roi}"
    )

    # And the tracker itself must take the unrestricted path.
    tr = FineFocusTracker()
    tr.cfg = cfg
    tracker_roi, _ = tr._pick(frame, "right")
    assert tracker_roi == full_roi, (
        f"FineFocusTracker still honours the lane: {tracker_roi} != {full_roi}"
    )


def test_the_pick_is_decided_by_the_pixels_the_operator_can_see() -> None:
    """A candidate must be the size of the pane, and start flush at the leading edge.

    The pane centre-crops the tile to the sensor pixels that fit at 1:1
    (854x480 at 1280x720 / 1.5x). Candidates were whole coarse grid tiles --
    2680x1904 sensor px -- so 12x more area than is displayed chose the object,
    and detail sitting in a margin could win and then not be on screen. Prove the
    winner is now fully visible, that there are more positions to choose from, and
    that the band starts where content arrives: a band deeper than a whole number
    of tiles anchors them at its INNER edge, which left the outer 168 px of 3350
    unsearched, one whole frame of travel.
    """
    h, w = 2382, 3350
    cfg = AutoFocusConfigV3()
    sub, pane_w, pane_h, zoom = 4, 1280, 720, 1.5
    tile_wh = (M4_FINE_PICK_TILE_W, M4_FINE_PICK_TILE_H)

    # Detail on the leading edge, deliberately in the upper margin of the OLD
    # 670x476 candidate -- inside it, but outside the centre crop it displayed.
    frame = np.full((h, w), 2000, dtype=np.uint16)
    frame[40:130, 3200:3340] = _textured(90, 140, 11)
    det_cy, det_cx = 85, 3270

    tr = FineFocusTracker(tile_wh=tile_wh)
    tr.cfg = cfg
    roi, _ = tr._pick(frame, "right")
    tw, th = roi[2] - roi[0], roi[3] - roi[1]

    assert (tw, th) == tile_wh, f"candidate is not pane-sized: {tw}x{th} != {tile_wh}"
    # One pane at 1:1, to within the coarse grid -- the most the view can ever show,
    # so it fits every offered zoom and wastes no score on pixels it cannot.
    for got, need in ((tw * sub, pane_w), (th * sub, pane_h)):
        assert need <= got < need + sub, f"{got} sensor px does not match {need}"

    # The band must reach the frame's leading edge, or a patch is only picked up
    # after it has already travelled a frame's worth inward.
    assert roi[2] == w, f"band stops {w - roi[2]} px short of the leading edge"

    # More positions across the flow than the 5 the coarse grid offered -- that is
    # the granularity, and each one is a distinct pane-sized view.
    old_rows = h // (int(h / cfg.tiles_per_frame) & ~1)   # what the coarse grid gave
    assert h // th >= 2 * old_rows, (
        f"only {h // th} positions across the flow vs the grid's {old_rows}"
    )
    direct, _ = select_leading_edge_roi(
        frame, "right", cfg, lane_frac=0.0, edge_band=0.0, tile_wh=tile_wh)
    assert direct == roi, f"tracker and direct call disagree: {roi} != {direct}"

    # The payoff: the detail that won is inside the crop the pane displays. The
    # old candidate contained this detail and then cropped it away.
    cx0, cy0, cw, ch = _fine_view_crop(tw * sub, th * sub, pane_w, pane_h, zoom)
    vx0, vy0 = roi[0] * sub + cx0, roi[1] * sub + cy0
    assert vx0 <= det_cx * sub < vx0 + cw and vy0 <= det_cy * sub < vy0 + ch, (
        f"the winning detail is off screen: view {(vx0, vy0, cw, ch)}"
    )

    old_roi, _ = select_leading_edge_roi(frame, "right", cfg, lane_frac=0.0)
    ow, oh = (old_roi[2] - old_roi[0]) * sub, (old_roi[3] - old_roi[1]) * sub
    ox0, oy0, ocw, och = _fine_view_crop(ow, oh, pane_w, pane_h, zoom)
    ovy0 = old_roi[1] * sub + oy0
    assert not (ovy0 <= det_cy * sub < ovy0 + och), (
        "the old oversized candidate would have shown this detail too, so this "
        "test no longer demonstrates anything"
    )


def test_a_hand_over_does_not_blind_the_motion_estimate() -> None:
    """Re-acquiring must not throw away the tracker's previous frame.

    ``_reacquire`` used to call ``self._tracker.reset()``. The frame stream is
    continuous across a hand-over and PatchTracker measures the SCENE rather than
    the ROI, so the previous frame was still the correct reference -- discarding
    it made the very next ``update`` return (0, 0). The freshly picked tile then
    failed to advance by one frame of motion: 672 sensor px at the measured -168
    px/frame, once per object, which is half the visible window at 1x zoom.

    DualObjectTracker never reset on acquisition, which is why its boxes looked
    clean while this tile did not.

    Checks EVERY hand-over in the run, not just the first change of
    ``object_id``. The first change is the static-start upgrade to a leading
    edge, which does not go through ``_reacquire`` at all -- stopping there let
    an earlier version of this test pass with the bug fully in place.
    """
    h, w, scroll = 240, 320, 6
    tr = FineFocusTracker()
    f = _textured(h, w, 11)
    assert tr.update(f) is not None, "first lock failed"

    rows = []  # (object_id, motion_dx, roi_x0) per frame
    # A tile crosses this frame in ~w/scroll frames, so this covers several
    # hand-overs. The static-start upgrade lands on rows[0], outside the
    # comparison window below, so every change found here is a real re-acquire.
    for _ in range(160):
        f = np.roll(f, shift=-scroll, axis=1)
        tr.update(f)
        assert tr.roi is not None
        rows.append((tr.object_id, tr.motion_dx, tr.roi[0]))

    handovers = [k for k in range(1, len(rows) - 1) if rows[k][0] != rows[k - 1][0]]
    assert len(handovers) >= 2, (
        f"expected the object to be handed over repeatedly, saw {len(handovers)}"
    )
    for k in handovers:
        _oid, dx, x0 = rows[k + 1]
        assert abs(dx) > 0.5 * scroll, (
            f"frame after hand-over at {k} measured no scene motion "
            f"(dx={dx:+.2f}, true {-scroll}) -- the new tile is stranded"
        )
        assert x0 < rows[k][2], (
            f"tile did not follow the scroll after hand-over at {k}: "
            f"x0 {rows[k][2]} -> {x0}"
        )


def test_the_flow_probe_survives_a_real_frame() -> None:
    """Run ``MainWindow._probe_fine_flow`` for real, on actual arrays.

    It first shipped with a NameError (numpy is imported locally in app.py, not
    as a module-level ``np``) that could only fire at runtime. Because the call
    sits in ``_update_fine_leading_edge`` ahead of ``set_focus_roi``, the throw
    took the pin with it: Fine mode fell back to the centre-circle auto-pick and
    presented as "the tracker is broken again". The call is now wrapped so a
    diagnostic cannot break tracking, but it should also just work.

    The method only needs a few attributes, so a stub stands in for the window
    rather than building a QApplication.
    """
    from capture.app import MainWindow  # local: pulls in Qt

    class _Stub:
        pass

    logged: list[str] = []
    stub = _Stub()
    stub._fine_flow_probe = PatchTracker()
    stub._fine_flow_samples = []
    stub._fine_tracker = FineFocusTracker()
    stub._log = logged.append

    h, w, scroll = 600, 800, 8
    rng = np.random.default_rng(5)
    scene = rng.integers(0, 4096, (h, w * 2), dtype=np.uint16)
    surround = rng.integers(0, 4096, (h, w), dtype=np.uint16)
    for i in range(18):
        f = surround.copy()
        f[h // 4 : h - h // 4, w // 4 : w - w // 4] = scene[
            h // 4 : h - h // 4, i * scroll + w // 4 : i * scroll + w - w // 4
        ]
        stub._fine_tracker.update(f)
        MainWindow._probe_fine_flow(stub, f)

    assert logged, "probe ran 18 frames without ever reporting a summary"
    assert "tracker/whole=" in logged[-1], f"unexpected summary: {logged[-1]}"
    # Both estimators now see the same continuous frame stream, so they should
    # agree. A ratio away from 1.0 means one of them is being fed differently --
    # which is the bug this probe accidentally found the first time around.
    ratio = float(logged[-1].split("tracker/whole=")[1])
    assert 0.98 < ratio < 1.02, f"estimators disagree: tracker/whole={ratio}"

    # And it must no-op cleanly when disabled, which is the default.
    stub._fine_flow_probe = None
    MainWindow._probe_fine_flow(stub, surround)


def main() -> None:
    test_locks_and_follows()
    test_reacquires_at_the_trailing_edge()
    test_frames_left_tracks_the_exit()
    test_static_scene_reports_no_flow()
    test_acquires_on_a_bland_frame()
    test_tile_advances_with_the_scroll_even_on_static_content()
    test_coarse_tap_is_not_gated_on_fine_focus()
    test_the_pick_searches_the_whole_leading_edge()
    test_the_pick_is_decided_by_the_pixels_the_operator_can_see()
    test_a_hand_over_does_not_blind_the_motion_estimate()
    test_the_flow_probe_survives_a_real_frame()
    print("FineFocusTracker self-check OK")


if __name__ == "__main__":
    main()
