"""Leading-edge patch tracker for the M4/M5 Fine view (manual focus aid).

The Fine view shows one full-resolution patch at 1:1 or better so the operator
can judge focus by eye. On a moving rig that patch has to be CHOSEN (the most
focusable thing currently entering the frame) and then FOLLOWED, or it pans off
screen within a few frames and every manual adjustment lands on a different
subject.

This is the tracker that was removed with the legacy AF modules in 7d68360,
lifted back out of ``autofocus.py`` and trimmed to the Fine view's needs: phase
correlation for the scene motion vector, a leading-edge tile pick scored on
unclipped texture, dead-reckoning advance, and a hand-over when the patch pins
against the trailing edge. The AF-engine-only parts (motor servo, OrthoSim bench
interior masking, the cross-flow acquisition lane) are deliberately NOT restored
-- the bench rig they served is gone, and the lane was actively wrong here: at
the AF value of 0.34 it cut the leading-edge candidates down to one, so the
winning-tile scoring was decorative.

No motor, no autofocus: this only ever reports an ROI.

Self-check: ``py -3 tools/test_fine_tracker.py``
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np

# Shared with the rest of the app (sharpness / tile_geometry live at the repo root).
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from sharpness import score_tile_subsampled  # noqa: E402
from tile_geometry import Tile, TileGrid  # noqa: E402

log = logging.getLogger(__name__)

Roi = Tuple[int, int, int, int]


@dataclass
class FineTrackerConfig:
    """The knobs the Fine tracker actually reads (the AF config's relevant subset)."""

    # Phase-correlation response below which the frame is treated as "the picture
    # changed for some other reason" (a defocus excursion) rather than "the scene
    # panned". Holding the ROI then is what stops the tile hopping while the
    # operator racks focus -- which is the one thing it must not do.
    min_track_response: float = 0.05
    # Fraction of full scale at or above which a pixel counts as clipped.
    sat_level: float = 0.98
    # Decimation for the tile texture scoring pass (speed, not accuracy).
    select_subsample: int = 4
    # Advisory only: below this the pick is flagged WEAK to the operator. It does
    # NOT gate acquisition -- see FineFocusTracker.update.
    min_texture_score: float = 2.0
    # Consecutive frames the ROI must sit jammed against a border with the scene still
    # pushing it that way before the object counts as gone. One frame is not enough:
    # the edge grid lays its last row flush with the far border, so a quarter of all
    # picks START pinned, and a couple of pixels of correlation noise -- which a heavily
    # defocused frame produces in abundance, exactly when the operator is using this
    # view -- then retires a patch that has its whole traverse ahead of it.
    pinned_frames_to_retire: int = 2


# ---------------------------------------------------------------------------
# Scene motion
# ---------------------------------------------------------------------------


class PatchTracker:
    """Estimate global inter-frame translation via phase correlation.

    Works on a small windowed copy of each frame, so it is cheap and robust for
    the translation-dominant motion of aerial imagery (and of the ground-test
    mirror rig). The measured motion vector is also what lets us derive the
    leading edge automatically.
    """

    def __init__(self, work_size: int = 256) -> None:
        self._work = int(work_size)
        self._prev: Optional[np.ndarray] = None
        self._win: Optional[np.ndarray] = None
        self._sx = 1.0
        self._sy = 1.0
        # Granularity worth knowing before trusting a small reading: the frame is
        # squashed to work_size^2, so on a 3350x2382 coarse frame one working-space
        # pixel is 13.1 frame px across and 9.3 down. Correlation is also
        # unambiguous only to +-work_size/2.

    def reset(self) -> None:
        self._prev = None

    def _prepare(self, frame: np.ndarray) -> np.ndarray:
        fh, fw = frame.shape[:2]
        small = cv2.resize(frame, (self._work, self._work), interpolation=cv2.INTER_AREA)
        if small.dtype == np.uint16:
            small = (small >> 6).astype(np.float32)
        else:
            small = small.astype(np.float32)
        if self._win is None:
            self._win = cv2.createHanningWindow((self._work, self._work), cv2.CV_32F)
        self._sx = fw / float(self._work)
        self._sy = fh / float(self._work)
        return small

    def update(self, frame: np.ndarray) -> tuple[float, float, float]:
        """Return ``(dx, dy, response)`` -- how far content moved since the last
        frame, in *frame* pixels, plus the phase-correlation response (0..1)."""
        cur = self._prepare(frame)
        if self._prev is None:
            self._prev = cur
            return 0.0, 0.0, 1.0
        (sx, sy), response = cv2.phaseCorrelate(self._prev, cur, self._win)
        self._prev = cur
        return sx * self._sx, sy * self._sy, float(response)


def leading_edge_from_motion(dx: float, dy: float) -> Optional[str]:
    """Leading edge = the edge *opposite* the content-motion vector.

    New content enters from there, so a target picked at that edge dwells the
    longest. Returns ``None`` when motion is too small to be meaningful.
    """
    if abs(dx) < 0.5 and abs(dy) < 0.5:
        return None
    if abs(dx) >= abs(dy):
        return "left" if dx > 0 else "right"
    return "top" if dy > 0 else "bottom"


# ---------------------------------------------------------------------------
# Tile selection
# ---------------------------------------------------------------------------


def _unclipped_texture(
    frame: np.ndarray, tile: Tile, cfg: FineTrackerConfig, subsample: int,
) -> tuple[float, float]:
    """High-pass texture measured over a tile's UNCLIPPED pixels only.

    Judges a tile on its GENUINE focusable detail rather than penalising it by
    clipped fraction: a building block that is 30% blown-white but has strong edges
    in the other 70% is a fine focus target and must beat a featureless-but-unclipped
    sky tile -- the fraction-based fade got that backwards and inverted good picks
    across exposures. A truly blown region has no unclipped detail left to measure so
    it scores ~0 on its own, no threshold needed. Returns ``(texture, clipped_frac)``.
    """
    region = frame[tile.y0:tile.y1:subsample, tile.x0:tile.x1:subsample]
    if region.size == 0:
        return 0.0, 0.0
    full = 16383.0 if region.dtype == np.uint16 else 255.0
    if region.dtype == np.uint16:
        r = (region >> 6).astype(np.float32)  # 14-bit -> 8-bit range, like the base metric
    else:
        r = region.astype(np.float32)
    clipped = region >= (cfg.sat_level * full)
    sat = float(np.count_nonzero(clipped)) / float(region.size)
    valid = ~clipped
    if int(valid.sum()) < max(16, int(0.1 * valid.size)):
        return 0.0, sat  # almost nothing unclipped: not a focus target
    # Neutralise blown pixels by filling them with the unclipped median BEFORE the
    # high-pass, so a bright strip's dark->pinned-white clip boundary stops reading as
    # "texture". Use the SAME coarse Gaussian high-pass as the base texture metric (NOT
    # a local Laplacian): it is a band-pass that ignores pixel noise, so flat hazy SKY
    # with sensor noise stays low instead of scoring as fake detail.
    if sat > 0.0:
        r = r.copy()
        r[clipped] = float(np.median(r[valid]))
    hp = r - cv2.GaussianBlur(r, (0, 0), 7)
    return float(np.std(hp[valid])), sat


def _best_tile(frame: np.ndarray, grid, cfg: FineTrackerConfig) -> tuple[Tile, float]:
    """Pick the tile with the most focusable detail, scoring each on the texture of its
    UNCLIPPED pixels only (see :func:`_unclipped_texture`) so genuinely busy detail wins
    even when it is partly overexposed. Falls back to the plain texture winner if every
    tile is clipped."""
    ss = max(1, cfg.select_subsample)
    best_i, best_eff = 0, -1.0
    best_raw_i, best_raw = 0, -1.0
    for i, tile in enumerate(grid):
        texture = score_tile_subsampled(frame, tile, subsample=ss)
        if texture > best_raw:
            best_raw, best_raw_i = texture, i
        eff, _sat = _unclipped_texture(frame, tile, cfg, ss)
        if eff > best_eff:
            best_eff, best_i = eff, i
    if best_eff <= 0.0:  # every candidate was clipped: keep the raw texture winner
        return grid[best_raw_i], float(best_raw)
    return grid[best_i], float(score_tile_subsampled(frame, grid[best_i], subsample=ss))


def _tiles_in_lead_band(tiles, lead_edge: str) -> list:
    """The tiles flush against ``lead_edge``: the single row/column of candidates whose
    centre is extreme along the flow axis.

    One tile deep is both the longest dwell and the only depth that starts at the
    leading edge -- :meth:`TileGrid._build_grid_edge` walks tile origins outward from
    the band's INNER edge with a full-tile stride, so a deeper band than fits a whole
    number of tiles leaves the outer remainder unsearched (168 px of 3350 at the AF's
    0.25, one whole frame of travel, so a patch was picked up a frame after it arrived).
    """
    tiles = list(tiles)
    if not tiles:
        return []
    vertical = lead_edge in ("top", "bottom")
    centres = [(t.y0 + t.y1) / 2.0 if vertical else (t.x0 + t.x1) / 2.0 for t in tiles]
    edge = min(centres) if lead_edge in ("top", "left") else max(centres)
    # Half a tile of slack, so the whole flush row qualifies despite even-origin snapping.
    slack = 0.5 * ((tiles[0].y1 - tiles[0].y0) if vertical else (tiles[0].x1 - tiles[0].x0))
    return [t for t, c in zip(tiles, centres) if abs(c - edge) <= slack]


def select_roi(
    frame: np.ndarray,
    lead_edge: Optional[str],
    cfg: FineTrackerConfig,
    tile_wh: Optional[tuple[int, int]],
) -> tuple[Roi, float]:
    """Pick the most focusable tile on the leading edge (whole frame if static).

    Candidates are sized to ``tile_wh`` -- the PANE, in frame pixels -- rather than by
    dividing the frame. A fixed window the operator stares at needs that twice over: one
    scalar divisor cannot express a 16:9 pane on a 1.41 frame, and a tile larger than the
    view lets detail the operator never sees pick their object. Smaller tiles also mean
    more of them: 5 candidates across the flow became 13 at the Fine pane's size.

    Returns ``((x0, y0, x1, y1), texture_score)``. Never returns empty.
    """
    fh, fw = frame.shape[:2]
    if tile_wh is None:
        tile_dw, tile_dh = fw // 4, fh // 4
    else:
        tile_dw, tile_dh = int(tile_wh[0]), int(tile_wh[1])
    if lead_edge is None:
        # Static (bench, or flight before the pan starts): search the WHOLE frame for the
        # sharpest tile. A centre-only search locks onto smooth sky or water -- the "why
        # did it pick THIS boring tile?" symptom. Motion picks the edge once it appears.
        grid = TileGrid(frame_width=fw, frame_height=fh, display_width=tile_dw,
                        display_height=tile_dh, zoom=1.0, region="interior",
                        interior_inset=0.0)
    else:
        grid = TileGrid(frame_width=fw, frame_height=fh, display_width=tile_dw,
                        display_height=tile_dh, zoom=1.0, region="edge",
                        lead_edge=lead_edge, edge_band=0.0)
        grid = _tiles_in_lead_band(grid, lead_edge) or list(grid)
    if len(grid) == 0:
        # Degenerate frame/tile sizing -- fall back to a centred ROI.
        tw = max(2, fw // 4) & ~1
        th = max(2, fh // 4) & ~1
        x0 = (fw // 2 - tw // 2) & ~1
        y0 = (fh // 2 - th // 2) & ~1
        roi = (x0, y0, x0 + tw, y0 + th)
        return roi, score_tile_subsampled(frame, Tile(*roi), subsample=cfg.select_subsample)
    t, score = _best_tile(frame, grid, cfg)
    return (t.x0, t.y0, t.x1, t.y1), score


# ---------------------------------------------------------------------------
# ROI bookkeeping
# ---------------------------------------------------------------------------


def clamp_roi(roi: Roi, fw: int, fh: int) -> Optional[Roi]:
    """Slide ``roi`` rigidly back inside a ``fw`` x ``fh`` frame; ``None`` if it cannot fit.

    Rigid on purpose. Cropping clamps edges independently, which gives up width rather
    than position -- and because the view shows the tile CENTRE, a tile narrowed by
    218 px carries the object 109 px across the screen.
    """
    x0, y0, x1, y1 = roi
    w = x1 - x0
    h = y1 - y0
    if w < 2 or h < 2 or w > fw or h > fh:
        return None
    x0 = int(round(min(max(0, x0), fw - w)))
    y0 = int(round(min(max(0, y0), fh - h)))
    return (x0, y0, x0 + w, y0 + h)


def roi_pinned_against_flow(
    clamped: Roi, dx: float, dy: float, fw: int, fh: int, tol: float = 1.0,
) -> bool:
    """True when the ROI is clamped against a frame border AND the scene is still
    flowing further that way -- i.e. the tracked patch has reached the trailing edge
    and content is now sliding under a stuck tile. (The ROI is re-clamped fully inside
    every frame, so it never leaves the frame *entirely*; this pinned-in-motion test is
    what detects 'the object left the FOV', not a None check on the clamp.)"""
    x0, y0, x1, y1 = clamped
    return (
        (dx < -tol and x0 <= 0) or (dx > tol and x1 >= fw)
        or (dy < -tol and y0 <= 0) or (dy > tol and y1 >= fh)
    )


class FineFocusTracker:
    """Leading-edge winning-tile tracker for the M4/M5 Fine view (no motor).

    Coarse decimated frames in; ROI out in the same pixel space as the frame.
    """

    def __init__(
        self,
        cfg: Optional[FineTrackerConfig] = None,
        *,
        tile_wh: Optional[tuple[int, int]] = None,
    ) -> None:
        self.cfg = cfg or FineTrackerConfig()
        # Candidate size in COARSE px, set by the caller from the pane it will fill.
        self.tile_wh = tile_wh
        self._tracker = PatchTracker()
        self.roi: Optional[Roi] = None
        self.lead_edge: Optional[str] = None
        self.motion_dx = 0.0
        self.motion_dy = 0.0
        # Dwell bookkeeping. The operator hand-focusing on one object needs to know
        # which object they are on and how much of its traverse is left -- otherwise a
        # switch mid-adjustment looks like the focus moved.
        self.object_id = 0
        self.frames_on_object = 0
        self.pick_score = 0.0
        self._frame_wh: Optional[tuple[int, int]] = None
        self._pinned_frames = 0

    def reset(self) -> None:
        self._tracker.reset()
        self.roi = None
        self.lead_edge = None
        self.motion_dx = 0.0
        self.motion_dy = 0.0
        self.object_id = 0
        self.frames_on_object = 0
        self.pick_score = 0.0
        self._frame_wh = None
        self._pinned_frames = 0

    def update(self, frame: np.ndarray) -> Optional[Roi]:
        """Advance tracking; return the coarse-frame ROI (None only before any frame)."""
        if frame is None or getattr(frame, "ndim", 0) < 2:
            return self.roi
        fh, fw = frame.shape[:2]
        self._frame_wh = (fw, fh)
        dx, dy, response = self._tracker.update(frame)
        self.motion_dx, self.motion_dy = dx, dy

        # NO texture gate on the pick. This is a MANUAL-focus aid, and the gate was
        # backwards for it: it demanded texture before handing over a tile, but an
        # out-of-focus lens produces no texture -- so precisely when the operator needs
        # something to focus on, it refused and reported "too bland" forever. select_roi
        # already returns the BEST tile and never returns empty, so the gate only ever
        # added refusal.
        if self.roi is None:
            lead = leading_edge_from_motion(dx, dy)
            self._take(*select_roi(frame, lead, self.cfg, self.tile_wh), lead=lead)
            return self.roi

        # Static start (whole-frame fallback): upgrade to the leading edge once motion
        # appears, so the patch gets the full traverse instead of starting mid-crossing.
        if self.lead_edge is None:
            new_lead = leading_edge_from_motion(dx, dy)
            if new_lead is not None:
                self._take(*select_roi(frame, new_lead, self.cfg, self.tile_wh),
                           lead=new_lead)
                return self.roi

        # Only re-pick when the patch genuinely pans out of the frame. A low tracking
        # response usually means the IMAGE got blurry (the operator racking focus, an AF
        # excursion), NOT that the patch moved -- re-picking then makes the tile hop to
        # whatever looks busiest at that blur level.
        if response >= self.cfg.min_track_response:
            clamped = clamp_roi(
                (int(round(self.roi[0] + dx)), int(round(self.roi[1] + dy)),
                 int(round(self.roi[2] + dx)), int(round(self.roi[3] + dy))),
                fw, fh,
            )
            if clamped is None:
                self._reacquire(frame)
                return self.roi
            if roi_pinned_against_flow(clamped, dx, dy, fw, fh):
                self._pinned_frames += 1
                if self._pinned_frames >= self.cfg.pinned_frames_to_retire:
                    self._reacquire(frame)
                    return self.roi
            else:
                self._pinned_frames = 0
            # Dead reckoning ONLY. An appearance re-match was tried here and had to go:
            # the sharpest-tile pick favours things that are static in SENSOR coordinates
            # (fiducials, sensor dust), and for those the true template match sits at
            # `predicted - (dx, dy)`. The re-match then cancelled the scroll every frame
            # and welded the tile to one spot on the sensor.
            self.roi = clamped
        self.frames_on_object += 1
        return self.roi

    def predicted_roi(self, lag_frames: float = 1.0) -> Optional[Roi]:
        """Where the tracked patch will be ``lag_frames`` frames from now.

        The consumer of this ROI is the acquisition thread, which can only apply it to a
        LATER frame than the coarse one it was derived from (the GUI sees that frame
        after the acq thread has passed it). Pinning the un-extrapolated ROI leaves the
        crop trailing the object by a frame of flow, which at a flown ~650 sensor px per
        frame is half the Fine pane.

        ponytail: one frame of lag, assumed constant. It is the true figure whenever the
        GUI keeps up, and the GUI conflates under load, so a backlog still shows as some
        trailing. Upgrade path is the acq-side travel odometer the old build carried
        (stamp each frame's cumulative flow, charge the pin against its own source
        frame) -- that costs a second phase correlation on the acq thread.
        """
        if self.roi is None or self._frame_wh is None:
            return None
        dx = self.motion_dx * float(lag_frames)
        dy = self.motion_dy * float(lag_frames)
        moved = (int(round(self.roi[0] + dx)), int(round(self.roi[1] + dy)),
                 int(round(self.roi[2] + dx)), int(round(self.roi[3] + dy)))
        # `or self.roi`: an unshifted crop beats no crop -- the Fine view is the
        # operator's only feedback.
        return clamp_roi(moved, *self._frame_wh) or self.roi

    def frames_left(self) -> Optional[int]:
        """Advisory frames until the patch pins against the trailing edge.

        Distance from the ROI to the outflow border over the per-frame flow. Only as
        good as the motion estimate, hence advisory -- it exists to tell the operator
        whether there is time to creep focus on this object or whether to wait for the
        next one. ``None`` when the scene is too static for the direction to mean
        anything.
        """
        if self.roi is None or self._frame_wh is None:
            return None
        fw, fh = self._frame_wh
        x0, y0, x1, y1 = self.roi
        spans = []
        if abs(self.motion_dx) > 0.5:
            spans.append((x0 if self.motion_dx < 0 else fw - x1) / abs(self.motion_dx))
        if abs(self.motion_dy) > 0.5:
            spans.append((y0 if self.motion_dy < 0 else fh - y1) / abs(self.motion_dy))
        if not spans:
            return None
        return max(int(min(spans)), 0)

    def _take(self, roi: Roi, score: float, *, lead: Optional[str]) -> None:
        """Adopt a freshly picked patch as the current object."""
        self.roi = roi
        self.pick_score = score
        self.lead_edge = lead
        self.object_id += 1
        self.frames_on_object = 0
        self._pinned_frames = 0

    def _reacquire(self, frame: np.ndarray) -> None:
        lead = self.lead_edge or leading_edge_from_motion(self.motion_dx, self.motion_dy)
        self._take(*select_roi(frame, lead, self.cfg, self.tile_wh), lead=lead)
        # Deliberately NOT self._tracker.reset(). The frame stream is continuous across a
        # hand-over and PatchTracker measures the SCENE, not the ROI, so its previous
        # frame is still the correct reference. Resetting threw it away, so the next
        # update returned (0, 0) and the freshly picked tile failed to advance by one
        # frame's worth of motion -- at the measured -168 coarse px/frame that is 672
        # sensor px of instant offset, once per object. reset() stays on the public entry
        # point, where the stream really can be discontinuous (mode / camera change).
