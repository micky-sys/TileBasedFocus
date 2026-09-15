"""Self-check for FineFocusTracker (leading-edge tile lock + follow)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "host"))

from capture.autofocus import FineFocusTracker  # noqa: E402


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


def main() -> None:
    test_locks_and_follows()
    test_reacquires_at_the_trailing_edge()
    test_frames_left_tracks_the_exit()
    test_static_scene_reports_no_flow()
    print("FineFocusTracker self-check OK")


if __name__ == "__main__":
    main()
