"""Self-check for DualObjectTracker relay + pane binding (no hardware, no CV luck).

The tracker reuses the AF engine's tracking primitives; the genuinely NEW logic
here is the two-pane RELAY: bind each acquired object to a stable pane for its
whole visible life, free the pane when the object pans off, then rebind that pane
to the next object WITHOUT disturbing the other pane. These asserts drive that
binding bookkeeping directly (deterministic -- no reliance on synthetic texture or
motion happening to trigger an acquisition), plus one smoke pass through the public
``update()`` so the real entry point is exercised end to end.

Run: py -3 tools/test_dual_tracker.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOST = REPO / "host"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HOST))

import numpy as np  # noqa: E402

from capture.dual_tracker import (  # noqa: E402
    DualObjectTracker,
    _Obj,
    _overlaps,
)


def _obj(oid: int, x0: int, y0: int, x1: int, y1: int) -> _Obj:
    return _Obj(oid=oid, roi=(x0, y0, x1, y1), lead="left", born=0)


def test_overlaps_helper() -> None:
    a = (0, 0, 100, 100)
    assert _overlaps(a, a)                       # identical -> full overlap
    assert _overlaps(a, (10, 10, 110, 110))      # ~81% of the smaller box
    assert not _overlaps(a, (200, 200, 300, 300))  # disjoint
    assert not _overlaps(a, (60, 60, 160, 160))  # 40x40=1600 < 0.5*10000


def test_relay_binds_frees_and_rebinds_stably() -> None:
    tr = DualObjectTracker()
    frame = np.zeros((400, 400), np.uint16)  # _panes() only needs the shape

    # Two acquired objects bind to the two distinct panes.
    tr._objects = [_obj(0, 10, 10, 60, 60), _obj(1, 300, 300, 350, 350)]
    tr._bind_panes()
    p = tr._panes(frame)
    assert p[0] is not None and p[1] is not None, p
    assert {p[0][0], p[1][0]} == {0, 1}, p
    pane_of_1 = 0 if p[0][0] == 1 else 1
    other = 1 - pane_of_1

    # Object 0 pans off: drop it and free its pane. Object 1 must stay put.
    tr._objects = [o for o in tr._objects if o.oid != 0]
    tr._free_pane(0)
    tr._bind_panes()
    p2 = tr._panes(frame)
    assert p2[pane_of_1] is not None and p2[pane_of_1][0] == 1, p2
    assert p2[other] is None, p2  # the freed pane is now idle

    # A fresh object rebinds to the freed pane, never bumping the survivor.
    tr._objects.append(_obj(2, 20, 20, 70, 70))
    tr._bind_panes()
    p3 = tr._panes(frame)
    assert p3[pane_of_1][0] == 1, p3          # survivor untouched
    assert p3[other] is not None and p3[other][0] == 2, p3  # rebound


def test_reset_clears_state() -> None:
    tr = DualObjectTracker()
    tr._objects = [_obj(0, 10, 10, 60, 60)]
    tr._bind_panes()
    tr._frames = 5
    tr.reset()
    assert tr._objects == []
    assert tr._pane_oid == [None, None]
    assert tr._frames == 0


def test_update_smoke_returns_two_panes() -> None:
    tr = DualObjectTracker()
    rng = np.random.default_rng(0)
    base = rng.integers(0, 4096, size=(300, 500), dtype=np.uint16)
    out = None
    for k in range(20):
        frame = np.ascontiguousarray(np.roll(base, k * 3, axis=1))  # pan right
        out = tr.update(frame)
    assert isinstance(out, list) and len(out) == 2, out


def test_two_panes_overlap_at_any_scroll_speed() -> None:
    """Both panes must stay occupied for a real share of the run at slow AND fast
    scroll. The old fixed frame gate passed at slow speed and collapsed at fast speed
    (successor acquired exactly as its predecessor exited), which is the bench symptom:
    a second box flashing for a single frame. Speeds are per-frame flow in pixels, and
    240 px/frame on a 600 px-tall frame is deliberately brutal -- ~2.5 frames of life.
    """
    rng = np.random.default_rng(7)
    fh, fw = 600, 800
    big = rng.integers(0, 4096, size=(fh * 6, fw), dtype=np.uint16)
    for step in (20, 80, 240):
        tr = DualObjectTracker()
        both = 0
        frames = 40
        for k in range(frames):
            y = (k * step) % (fh * 5)
            panes = tr.update(np.ascontiguousarray(big[y:y + fh, :]))
            if panes[0] is not None and panes[1] is not None:
                both += 1
        assert both >= frames // 4, (
            f"step={step}: both panes occupied only {both}/{frames} frames"
        )


def main() -> int:
    tests = [
        test_overlaps_helper,
        test_relay_binds_frees_and_rebinds_stably,
        test_reset_clears_state,
        test_update_smoke_returns_two_panes,
        test_two_panes_overlap_at_any_scroll_speed,
    ]
    for t in tests:
        t()
        print(f"  ok: {t.__name__}")
    print("dual tracker tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
