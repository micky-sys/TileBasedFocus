"""Two-object leading-edge tracker for the M4/M5 split-screen preview.

Keeps up to two textured patches alive at once and hands each pane a single
object from the moment it enters the leading edge until it pans off the trailing
edge, so the operator can watch each object for as long as possible. This is pure
reuse of the AF engine's tracking primitives (:class:`PatchTracker` +
:func:`select_leading_edge_roi` + the clamp / pinned-against-flow tests) -- there
is no new tracking maths here.

It runs independently of the autofocus engine (fed the coarse decimated frames),
so the preview works with or without an AF run in progress. The AF engine and its
single-object measurement are untouched.

ponytail: single global :class:`PatchTracker` gives one scene-wide motion vector
that moves BOTH ROIs -- correct for the translation-dominant aerial/mirror-rig
motion we can actually test. Upgrade path if independent per-object motion ever
matters (parallax, multiple movers): one tracker per object.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from .autofocus import (
    AutoFocusConfig,
    PatchTracker,
    Roi,
    _clamp_roi,
    _roi_pinned_against_flow,
    leading_edge_from_motion,
    select_leading_edge_roi,
)

# One entry per pane: (object id, padded display ROI) or None when the pane is idle.
Pane = Optional[Tuple[int, Roi]]


@dataclass
class _Obj:
    oid: int
    roi: Roi                 # tight tracked tile (frame pixels)
    lead: Optional[str]      # leading edge it was acquired from
    born: int                # frame index at acquisition (for stagger)


def _pad_roi(roi: Roi, pad: float, fw: int, fh: int) -> Roi:
    """Grow an ROI about its centre by ``pad`` for a roomier preview crop, clamped
    to the frame. Display-only -- tracking keeps using the tight tile."""
    x0, y0, x1, y1 = roi
    px = int((x1 - x0) * (pad - 1.0) / 2.0)
    py = int((y1 - y0) * (pad - 1.0) / 2.0)
    return (max(0, x0 - px), max(0, y0 - py), min(fw, x1 + px), min(fh, y1 + py))


def _overlaps(a: Roi, b: Roi, frac: float = 0.5) -> bool:
    """True when two ROIs share at least ``frac`` of the smaller one's area --
    used to stop the second pane from re-picking the tile the first pane already
    holds (they'd otherwise show the same patch)."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix = max(0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0, min(ay1, by1) - max(ay0, by0))
    inter = ix * iy
    if inter <= 0:
        return False
    amin = min((ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0))
    return inter >= frac * max(1, amin)


class DualObjectTracker:
    """Relay two leading-edge patches across the FOV onto two stable panes.

    Each frame call :meth:`update`; it returns a length-2 list, one entry per pane.
    A pane holds the same object (same ``oid``) for that object's whole visible life;
    when the object pans off the trailing edge the pane frees and re-binds to the
    next freshly acquired patch.
    """

    def __init__(
        self,
        cfg: Optional[AutoFocusConfig] = None,
        pad: float = 1.5,
        stagger_frames: int = 8,
    ) -> None:
        self.cfg = cfg or AutoFocusConfig()
        self._pad = float(pad)
        # Don't acquire a 2nd patch within this many frames of the previous acquire,
        # so the pair doesn't enter (and later exit) the FOV in lockstep -- that would
        # collapse the two panes into one moment and defeat the "view each as long as
        # possible" goal. ponytail: fixed frame gate; upgrade path = acquire the 2nd
        # once the 1st crosses the FOV midpoint.
        self._stagger = int(stagger_frames)
        self._tracker = PatchTracker()
        self._objects: List[_Obj] = []
        self._pane_oid: List[Optional[int]] = [None, None]
        self._next_oid = 0
        self._frames = 0
        self._last_acquire = -(10 ** 9)
        self.motion_dx = 0.0
        self.motion_dy = 0.0

    def reset(self) -> None:
        self._tracker.reset()
        self._objects = []
        self._pane_oid = [None, None]
        self._frames = 0
        self._last_acquire = -(10 ** 9)
        self.motion_dx = 0.0
        self.motion_dy = 0.0

    def update(self, frame: np.ndarray) -> List[Pane]:
        if frame is None or getattr(frame, "ndim", 0) < 2:
            return self._panes(frame)
        self._frames += 1
        fh, fw = frame.shape[:2]
        dx, dy, response = self._tracker.update(frame)
        self.motion_dx, self.motion_dy = dx, dy

        # Advance / retire only when the scene genuinely moved. A low correlation
        # usually means the image blurred (hand-defocus, AF excursion), not that the
        # patch panned -- hold the ROIs then, matching the AF engine's _advance_objects.
        if response >= self.cfg.min_track_response:
            survivors: List[_Obj] = []
            for obj in self._objects:
                moved = (obj.roi[0] + dx, obj.roi[1] + dy,
                         obj.roi[2] + dx, obj.roi[3] + dy)
                clamped = _clamp_roi(
                    (int(round(moved[0])), int(round(moved[1])),
                     int(round(moved[2])), int(round(moved[3]))),
                    fw, fh,
                )
                if clamped is None or _roi_pinned_against_flow(clamped, dx, dy, fw, fh):
                    self._free_pane(obj.oid)  # panned off the FOV -> drop + free its pane
                    continue
                obj.roi = clamped
                survivors.append(obj)
            self._objects = survivors

        # Relay: keep up to two patches alive, staggered so they don't march in lockstep.
        if len(self._objects) < 2 and (response >= self.cfg.min_track_response or not self._objects):
            if (self._frames - self._last_acquire) >= self._stagger or not self._objects:
                self._try_acquire(frame, dx, dy)

        self._bind_panes()
        return self._panes(frame)

    def _try_acquire(self, frame: np.ndarray, dx: float, dy: float) -> None:
        lead = leading_edge_from_motion(dx, dy)
        roi, score = select_leading_edge_roi(frame, lead, self.cfg)
        if score < self.cfg.min_texture_score:
            return
        if any(_overlaps(roi, o.roi) for o in self._objects):
            return  # same patch the other pane already holds -- wait for fresh content
        self._objects.append(_Obj(oid=self._next_oid, roi=roi, lead=lead, born=self._frames))
        self._next_oid += 1
        self._last_acquire = self._frames

    def _free_pane(self, oid: int) -> None:
        for i, bound in enumerate(self._pane_oid):
            if bound == oid:
                self._pane_oid[i] = None

    def _bind_panes(self) -> None:
        bound = set(o for o in self._pane_oid if o is not None)
        live = {o.oid for o in self._objects}
        # Release panes whose object is gone (defensive; drops already free them).
        for i, o in enumerate(self._pane_oid):
            if o is not None and o not in live:
                self._pane_oid[i] = None
        # Bind any unbound live object to the lowest free pane, keeping survivors put.
        for obj in self._objects:
            if obj.oid in bound:
                continue
            for i in range(2):
                if self._pane_oid[i] is None:
                    self._pane_oid[i] = obj.oid
                    bound.add(obj.oid)
                    break

    def _panes(self, frame: Optional[np.ndarray]) -> List[Pane]:
        if frame is None or getattr(frame, "ndim", 0) < 2:
            fw = fh = 0
        else:
            fh, fw = frame.shape[:2]
        by_oid = {o.oid: o for o in self._objects}
        out: List[Pane] = []
        for i in range(2):
            oid = self._pane_oid[i]
            obj = by_oid.get(oid) if oid is not None else None
            if obj is None:
                out.append(None)
            else:
                out.append((obj.oid, _pad_roi(obj.roi, self._pad, fw, fh)))
        return out
