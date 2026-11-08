"""Self-checks for the Fine view's leading-edge tracker (no hardware, no Qt).

A synthetic scene is scrolled past a fixed window one frame at a time: a bland
gradient with one small high-contrast blob of texture. That is the case the
tracker exists for -- pick the thing worth focusing on, then stay on it while
the rig pans -- and it is the case the old build kept getting wrong in ways the
operator saw as "the tile keeps hopping" or "the view lags the object".

Run: ``py -3 tools/test_fine_tracker.py``
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "host"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from capture.fine_tracker import (  # noqa: E402
    FineFocusTracker, clamp_roi, leading_edge_from_motion, roi_pinned_against_flow,
)

H, W = 240, 400          # frame size in "coarse" px
TILE = (80, 60)          # what the pane can show, in the same px
PAD = 1000               # world margin either side of the frame window
RNG = np.random.default_rng(7)

# ONE world image, sampled through a sliding window. It has to be built once and
# scrolled, not regenerated per frame: phase correlation measures how far the SAME
# content moved, so a scene whose texture is re-randomised each frame translates by
# nothing at all, however far the nominal object "moved".
_WORLD_W, _WORLD_H = W + 2 * PAD, H + 2 * PAD
_world = np.linspace(2000, 6000, _WORLD_W, dtype=np.float32)[None, :].repeat(_WORLD_H, 0)
_world += RNG.normal(0.0, 15.0, size=(_WORLD_H, _WORLD_W))


def put_blob(cx: int, cy: int, size: int = 44) -> None:
    """Paint one high-contrast textured patch into the world at world coordinates."""
    y0, x0 = cy - size // 2, cx - size // 2
    _world[y0:y0 + size, x0:x0 + size] = RNG.integers(
        500, 15000, size=(size, size)).astype(np.float32)


# The only thing in this world worth focusing on, near the middle of the window's travel.
put_blob(PAD + 200, PAD + 120)


def scene(ox: int, oy: int = 0) -> np.ndarray:
    """The frame seen when the window sits at world offset ``(ox, oy)``, uint16 14-bit.

    A POSITIVE ``ox`` slides the window right, so content moves LEFT in the frame
    (dx < 0) and new content enters from the right -- the leading edge.
    """
    x0, y0 = PAD + int(ox), PAD + int(oy)
    # Running off the world silently returns a NARROWER frame, which changes the tile
    # grid under the test and looks like a tracker bug. Fail loudly instead.
    assert 0 <= x0 and x0 + W <= _WORLD_W and 0 <= y0 and y0 + H <= _WORLD_H, (ox, oy)
    return np.clip(_world[y0:y0 + H, x0:x0 + W], 0, 16383).astype(np.uint16)


def pan(tracker: FineFocusTracker, step: int, n: int, *, start: int = 0):
    """Slide the window by ``step`` px per frame for ``n`` frames; return the ROIs seen."""
    return [tracker.update(scene(start + step * i)) for i in range(n)]


def test_leading_edge_is_opposite_the_motion():
    # Content sliding right (+dx) means new content arrives from the left.
    assert leading_edge_from_motion(+9.0, 0.0) == "left"
    assert leading_edge_from_motion(-9.0, 0.0) == "right"
    assert leading_edge_from_motion(0.0, +9.0) == "top"
    assert leading_edge_from_motion(0.0, -9.0) == "bottom"
    assert leading_edge_from_motion(0.2, -0.3) is None   # too static to mean anything


def test_clamp_slides_rigidly_and_rejects_oversize():
    # Rigid: the ROI keeps its size and is pushed back in, never trimmed. A trimmed ROI
    # would carry the object half the lost width across the screen.
    assert clamp_roi((-30, 10, 70, 70), 400, 240) == (0, 10, 100, 70)
    assert clamp_roi((360, 10, 460, 70), 400, 240) == (300, 10, 400, 70)
    assert clamp_roi((0, 0, 500, 70), 400, 240) is None      # wider than the frame
    assert clamp_roi((0, 0, 1, 70), 400, 240) is None        # degenerate


def test_pinned_only_counts_when_the_flow_still_pushes_outward():
    at_left = (0, 50, 80, 110)
    assert roi_pinned_against_flow(at_left, -5.0, 0.0, 400, 240)   # still sliding off
    assert not roi_pinned_against_flow(at_left, +5.0, 0.0, 400, 240)  # coming back in
    assert not roi_pinned_against_flow((100, 50, 180, 110), -5.0, 0.0, 400, 240)


def test_the_first_pick_lands_on_the_textured_blob_not_the_bland_gradient():
    """Static start: no flow, so the whole frame is searched. A centre-only search
    would lock onto the smooth gradient, which is the 'why did it pick THIS boring
    tile?' symptom."""
    tr = FineFocusTracker(tile_wh=TILE)
    roi = tr.update(scene(0))                 # blob sits at frame (200, 120)
    assert roi is not None
    cx, cy = (roi[0] + roi[2]) / 2, (roi[1] + roi[3]) / 2
    assert abs(cx - 200) < 55 and abs(cy - 120) < 45, (roi, cx, cy)


def test_the_tile_follows_the_scene_instead_of_staying_put_on_the_sensor():
    tr = FineFocusTracker(tile_wh=TILE)
    rois = pan(tr, step=+6, n=14)             # content moves LEFT at 6 px/frame
    first, last = rois[1], rois[-1]
    travelled = last[0] - first[0]
    assert travelled < -50, (first, last, travelled)   # 12 frames x -6 px
    assert tr.object_id == 2, "handed over mid-traverse"  # 1 static pick + 1 edge pick
    assert tr.frames_on_object >= 10


def test_racking_focus_does_not_make_the_tile_hop_to_another_object():
    """The operator defocuses and refocuses while watching this view. Texture collapses
    as they do. If the tile hopped to whatever looked busiest at that blur level, the
    adjustment would land on a different subject and read as the focus having moved."""
    tr = FineFocusTracker(tile_wh=TILE)
    pan(tr, step=+6, n=6)
    obj = tr.object_id
    for sigma in (1.0, 3.0, 7.0, 3.0, 1.0):          # out of focus and back
        blurred = cv2.GaussianBlur(scene(36).astype(np.float32), (0, 0), sigma)
        tr.update(blurred.astype(np.uint16))
    assert tr.object_id == obj, (obj, tr.object_id)


def test_a_low_correlation_response_holds_the_roi_rather_than_moving_it():
    """The response gate itself: below it the measured vector is meaningless, so the ROI
    must not be shifted by it. (Above, a blurred frame still correlates well; this pins
    the branch that catches the frames where it does not.)"""
    tr = FineFocusTracker(tile_wh=TILE)
    pan(tr, step=+6, n=6)
    held = tr.roi
    tr.cfg.min_track_response = 1.1                  # nothing can clear it
    pan(tr, step=+6, n=4, start=36)
    assert tr.roi == held, (held, tr.roi)


def test_an_object_leaving_the_frame_hands_over_at_the_leading_edge():
    tr = FineFocusTracker(tile_wh=TILE)
    seen, fresh = 0, []
    for i in range(40):                       # content streams left at 20 px/frame
        roi = tr.update(scene(20 * i))
        if tr.object_id != seen:
            seen = tr.object_id
            fresh.append((tr.lead_edge, roi))
    assert seen >= 3, seen                    # static pick, edge pick, then a hand-over
    # Every pick made once the flow is known is taken flush against the leading (right)
    # edge, so the patch gets the whole frame to cross before it exits. Picking it
    # wherever it happened to look busiest would hand the operator a patch already
    # part-way across, and the dwell readout would be a lie.
    for lead, roi in fresh[1:]:
        assert lead == "right" and roi[2] >= W - 2, (lead, roi)


class ScriptedMotion:
    """Stands in for PatchTracker so a test can dictate the measured flow exactly."""

    def __init__(self, steps):
        self.steps = list(steps)

    def update(self, _frame):
        return self.steps.pop(0) if self.steps else (0.0, 0.0, 1.0)

    def reset(self):
        self.steps = []


def test_a_tile_flush_with_a_border_survives_one_noisy_frame_but_not_a_real_exit():
    """The edge grid lays its last row flush with the far border, so a quarter of all
    picks start pinned. Retiring on the first frame that nudges them outward throws
    away a patch with its whole traverse ahead -- which is what a defocused frame's
    couple of pixels of correlation noise looks like."""
    def flush_tracker(steps):
        tr = FineFocusTracker(tile_wh=TILE)
        tr.update(scene(0))                            # one real frame to pick + size
        tr.roi = (100, H - TILE[1], 100 + TILE[0], H)  # flush with the bottom border
        tr.lead_edge = "top"                           # already flowing: content moves down
        tr._tracker = ScriptedMotion(steps)
        return tr

    # One outward nudge, then the scene settles: the patch is still there, keep it.
    tr = flush_tracker([(0.0, +2.0, 0.9), (0.0, -0.1, 0.9), (0.0, +0.2, 0.9)])
    obj = tr.object_id
    for _ in range(3):
        tr.update(scene(0))
    assert tr.object_id == obj, "retired on a single noisy frame"

    # Flow that keeps pushing it out IS the object leaving: hand over.
    tr = flush_tracker([(0.0, +2.0, 0.9)] * 3)
    obj = tr.object_id
    for _ in range(3):
        tr.update(scene(0))
    assert tr.object_id > obj, "never retired a patch that really left"


def test_the_pin_is_extrapolated_one_frame_so_the_crop_does_not_trail():
    tr = FineFocusTracker(tile_wh=TILE)
    pan(tr, step=+8, n=6)
    assert tr.motion_dx < -1.0, tr.motion_dx  # the pan was measured at all
    ahead = tr.predicted_roi()
    assert ahead is not None and ahead[0] < tr.roi[0], (tr.roi, ahead, tr.motion_dx)
    # One frame ahead, not an arbitrary leap.
    assert abs((ahead[0] - tr.roi[0]) - tr.motion_dx) <= 1.0

    # Clamped, never off the frame, however long the predicted leap.
    tr.roi = (W - TILE[0], 0, W, TILE[1])
    tr.motion_dx = 5000.0
    pred = tr.predicted_roi()
    assert pred is not None and pred[2] <= W and pred[0] >= 0, pred


def test_frames_left_shrinks_as_the_object_crosses_and_is_none_when_static():
    tr = FineFocusTracker(tile_wh=TILE)
    tr.update(scene(0))
    assert tr.frames_left() is None, "no flow yet, so no honest estimate"
    early_rois = pan(tr, step=+10, n=4, start=10)
    early = tr.frames_left()
    pan(tr, step=+10, n=8, start=10 + 4 * 10)
    late = tr.frames_left()
    assert early is not None and late is not None, (early, late)
    assert late < early, (early, late, early_rois[-1], tr.roi)


def test_reset_forgets_the_object_and_the_motion_reference():
    tr = FineFocusTracker(tile_wh=TILE)
    pan(tr, step=+6, n=4)
    tr.reset()
    assert tr.roi is None and tr.object_id == 0 and tr.motion_dx == 0.0
    assert tr.predicted_roi() is None
    assert tr.update(scene(0)) is not None     # picks again from scratch


def test_a_tile_as_big_as_the_frame_still_yields_a_roi():
    """A maximised pane at 1x asks for more sensor px than the coarse frame has. The
    grid comes back empty then, and the fallback must still hand back something the
    pipeline can crop -- the view is the operator's only feedback."""
    tr = FineFocusTracker(tile_wh=(W * 2, H * 2))
    roi = tr.update(scene(0))
    assert roi is not None
    assert 0 <= roi[0] < roi[2] <= W and 0 <= roi[1] < roi[3] <= H, roi


def main() -> int:
    for t in (
        test_leading_edge_is_opposite_the_motion,
        test_clamp_slides_rigidly_and_rejects_oversize,
        test_pinned_only_counts_when_the_flow_still_pushes_outward,
        test_the_first_pick_lands_on_the_textured_blob_not_the_bland_gradient,
        test_the_tile_follows_the_scene_instead_of_staying_put_on_the_sensor,
        test_racking_focus_does_not_make_the_tile_hop_to_another_object,
        test_a_low_correlation_response_holds_the_roi_rather_than_moving_it,
        test_an_object_leaving_the_frame_hands_over_at_the_leading_edge,
        test_a_tile_flush_with_a_border_survives_one_noisy_frame_but_not_a_real_exit,
        test_the_pin_is_extrapolated_one_frame_so_the_crop_does_not_trail,
        test_frames_left_shrinks_as_the_object_crosses_and_is_none_when_static,
        test_reset_forgets_the_object_and_the_motion_reference,
        test_a_tile_as_big_as_the_frame_still_yields_a_roi,
    ):
        t()
        print(f"  ok: {t.__name__}")
    print("fine tracker tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
