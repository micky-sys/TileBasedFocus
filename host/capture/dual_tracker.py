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
    ) -> None:
        self.cfg = cfg or AutoFocusConfig()
        self._pad = float(pad)
        self._tracker = PatchTracker()
        self._objects: List[_Obj] = []
        self._pane_oid: List[Optional[int]] = [None, None]
        self._next_oid = 0
        self._frames = 0
        self.motion_dx = 0.0
        self.motion_dy = 0.0
        self._reset_diag()

    def _reset_diag(self) -> None:
        # Live "why is the second pane empty?" state, read by :meth:`debug_line`.
        self.last_response = 0.0
        self.last_lead: Optional[str] = None
        self.last_acquire_result = "none"
        self.last_score = 0.0
        self.retired = 0

    def reset(self) -> None:
        self._tracker.reset()
        self._objects = []
        self._pane_oid = [None, None]
        self._frames = 0
        self.motion_dx = 0.0
        self.motion_dy = 0.0
        self._reset_diag()

    def debug_line(self) -> str:
        """One line of live state naming the exact gate that blocked an acquire.

        ``last_acquire_result`` is one of: ``have2`` (both panes busy), ``low_response``
        (phase correlation below ``min_track_response``), ``await_midpoint`` (the live
        patch hasn't crossed the FOV midpoint yet), ``texture`` / ``overlap`` (rejected
        in ``_try_acquire``), or ``acquired``.
        """
        return (
            f"dx={self.motion_dx:+.1f} dy={self.motion_dy:+.1f} "
            f"resp={self.last_response:.3f} lead={self.last_lead} "
            f"objs={len(self._objects)}{[o.oid for o in self._objects]} "
            f"retired={self.retired} gate={self.last_acquire_result} "
            f"score={self.last_score:.2f}/{self.cfg.min_texture_score}"
        )

    def update(self, frame: np.ndarray) -> List[Pane]:
        if frame is None or getattr(frame, "ndim", 0) < 2:
            return self._panes(frame)
        self._frames += 1
        fh, fw = frame.shape[:2]
        dx, dy, response = self._tracker.update(frame)
        self.motion_dx, self.motion_dy = dx, dy
        self.last_response = response

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
                    self.retired += 1
                    continue
                obj.roi = clamped
                survivors.append(obj)
            self._objects = survivors

        # Relay: keep up to two patches alive, staggered so they don't march in lockstep.
        if len(self._objects) >= 2:
            self.last_acquire_result = "have2"
        elif not (response >= self.cfg.min_track_response or not self._objects):
            self.last_acquire_result = "low_response"
        elif not self._past_midpoint(dx, dy, fw, fh):
            self.last_acquire_result = "await_midpoint"
        else:
            self._try_acquire(frame, dx, dy)

        self._bind_panes()
        return self._panes(frame)

    def _past_midpoint(self, dx: float, dy: float, fw: int, fh: int) -> bool:
        """True once every live patch has been carried past the FOV midpoint along the
        flow axis -- the trigger for acquiring the *second* patch.

        A fixed frame gate cannot do this job. The number of frames a patch survives is
        ``FOV_extent / per-frame flow``, which shifts with scroll speed and again with
        preview conflation (a dropped frame doubles the apparent step). Measured on the
        OrthoSim bench that count fell to ~8 frames while the gate was also 8, so each
        successor was acquired just as its predecessor exited and the two were never on
        screen together. Keying on position instead makes the overlap a fixed *fraction*
        of each patch's life (~half) at any speed or frame rate.
        """
        if not self._objects:
            return True
        if abs(dx) < 0.5 and abs(dy) < 0.5:
            return True  # no meaningful flow -- nothing to stagger against
        horizontal = abs(dx) >= abs(dy)
        for o in self._objects:
            if horizontal:
                centre, midpoint, flow = (o.roi[0] + o.roi[2]) / 2.0, fw / 2.0, dx
            else:
                centre, midpoint, flow = (o.roi[1] + o.roi[3]) / 2.0, fh / 2.0, dy
            # Flow < 0 carries the patch toward 0, so "past" means below the midpoint.
            if (centre > midpoint) if flow < 0 else (centre < midpoint):
                return False
        return True

    def _try_acquire(self, frame: np.ndarray, dx: float, dy: float) -> None:
        lead = leading_edge_from_motion(dx, dy)
        self.last_lead = lead
        roi, score = select_leading_edge_roi(frame, lead, self.cfg)
        self.last_score = score
        if score < self.cfg.min_texture_score:
            self.last_acquire_result = "texture"
            return
        if any(_overlaps(roi, o.roi) for o in self._objects):
            self.last_acquire_result = "overlap"
            return  # same patch the other pane already holds -- wait for fresh content
        self.last_acquire_result = "acquired"
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
