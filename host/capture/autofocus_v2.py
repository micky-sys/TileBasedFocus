"""In-flight dive-then-climb autofocus (AF v2), an alternative to the legacy servo.

Why a different engine (see HANDOFF Section 24 for the legacy dither servo it
sits beside):

The thing we actually correct is an **atmospheric lens** -- temperature/pressure
gradients at altitude that defocus the image. That effect is roughly **uniform
across the field of view** and changes on an **hours** timescale, so best focus is
*effectively static for most of a mission*. But it only *exists at altitude*: you
cannot pre-set it on the ground, it has to be found **in flight, against a moving
scene**, then parked and held.

That reframes AF from a fast servo into a **robust one-time global search**, done
as a single upward pass:

1. **Dive** a fixed amount BELOW the (hand-focused, near-focus) start, fast, using
   the coarse strength (str3). Split into a few separate bursts so the encoder is
   re-read between them -- a stalled motor / end stop is caught early. The dive
   seats the gear lash ONCE, from below.
2. **Climb** back UP in small fine (str0) steps, taking **one (encoder, score)
   sample per frame**. Because the climb only ever goes up, the lash stays seated
   and every sample shares the same mechanical state, so the score-vs-encoder curve
   is clean.
3. **Peak-detect**: stop the moment the smoothed score has clearly risen and then
   fallen (we've climbed through focus).
4. **Park upward** onto the peak position (approach from below by the known lash)
   so the parked lens sits exactly where the measurements said focus was, then HOLD.

The critical design choice is that steps are **fixed open-loop pulse bursts** and we
merely READ where the encoder landed -- there is NO absolute-position servo that
re-commands a `move_to` every frame. On this rig the encoder streams at only ~10 Hz
and each discrete focus pulse takes ~15-22 ms, so a re-issuing servo re-commanded on
stale feedback and oscillated violently (overshooting a -120 target to -1387 and
firing 1000-char correction bursts). A one-way incremental climb is immune to that:
a consistent feedback lag only shifts the whole curve by a constant, which the
upward park then cancels.

Scene motion is handled by tracking the target patch (``PatchTracker``) so the
scored ROI follows a slow pan; if the patch leaves the frame a fresh leading-edge
patch is re-acquired. (ponytail: this is single-object -- good for the static bench
and slow pans. The old multi-object stitch for fast flight churn is a future layer;
it is not needed to find focus on a near-static scene.)

Lens motion is abstracted behind :class:`focus_axis.FocusAxis` (open-loop pulse
count, or the closed-loop AMT203S ``EncoderAxis``); the search never changes.

The engine exposes the SAME duck-typed surface as
:class:`capture.autofocus.AutoFocusEngine`, so :class:`AutoFocusController` can
drive either one interchangeably (side-by-side A/B testing).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

import numpy as np

# Reuse the legacy building blocks verbatim -- same tracker, tile picker, metrics.
from .autofocus import (
    PatchTracker,
    _clamp_roi,
    _roi_pinned_against_flow,
    _smooth3,
    focus_measure,
    focus_measure_normalized,
    leading_edge_from_motion,
    select_leading_edge_roi,
)
# Reuse the dual-preview tracker's ROI-overlap test so the on-deck handover object
# can't re-pick the patch the primary already holds (same content -> useless cross-cal).
from .dual_tracker import _overlaps
from .focus_axis import FocusAxis, OpenLoopAxis

log = logging.getLogger(__name__)

Roi = tuple[int, int, int, int]  # (x0, y0, x1, y1)


def _segment(samples: list) -> list:
    """Split ``[(pos, score, oid)]`` into runs of consecutive samples sharing an oid."""
    segs: list = []
    for pos, score, oid in samples:
        if not segs or segs[-1][0] != oid:
            segs.append((oid, []))
        segs[-1][1].append((pos, score))
    return [pts for _oid, pts in segs]


def _fit_peak_scale_free(
    samples: list, min_seg_points: int = 2, min_segments: int = 3,
) -> Optional[float]:
    """Peak position from per-object segments, with NO cross-object scale factor at all.

    ``samples`` is ``[(pos, score, oid)]`` in climb order. Every tracked patch carries its
    own unknown brightness/texture scale ``k``, so absolute scores are not comparable
    between objects. The previous approach measured an A/B ratio per handover and
    multiplied it into a running ``cumulative_scale``, but a climb spans ~16 objects (~237
    frames of stepping against a ~15-frame object life at 400 mm), so per-link error
    compounded rather than averaged: a geometric-mean ratio of 0.922 -- only 8% off unity,
    a genuinely good content ratio -- became a 3.7x scale drift and a 27-pulse park error.
    Across six live runs the parked position tracked ``cum_scale`` in perfect monotonic
    order (rank correlation 1.00) and showed no discernible dependence on focus at all.

    ``k`` cancels exactly in the LOG-SLOPE, since d/dp log(k*S(p)) = d/dp log S(p). And a
    near-focus sharpness peak is ~Gaussian, so log S = -(p-p0)^2/(2*sigma^2) + c and that
    slope is -(p-p0)/sigma^2 -- LINEAR in position, crossing zero exactly at the peak. So
    take one log-slope per segment (each scale-free), fit a straight line through them,
    and solve for the zero crossing. No ratio, no chain, nothing to compound: a segment
    with a wildly wrong ``k`` contributes the same slope as one with k=1.

    Measured against the structure of live run 7 (57 samples over 20 objects, of which 8
    changes were re-anchors carrying NO scale correction at all, patch ln-scale spread
    0.157) this lands 4.0 pulses from truth at the median vs 11.3 for the single-curve
    parabola, and it stays at 4.0 as scene heterogeneity rises while the parabola degrades
    past 35. ``min_seg_points=2`` because live objects only survive ~2.8 samples; a 2-point
    log-slope is noisy but unbiased, and ~20 of them feed one line fit.

    ponytail: slopes are fitted unweighted, so a 2-point segment counts as much as a
    6-point one -- that is what leaves the 90th-percentile error at ~19 pulses while the
    median is 4. Upgrade path if the tail matters: weight each slope by its segment's
    point count and position span (its inverse slope variance).

    Returns None when there are too few usable segments, or when the slopes *increase*
    with position (that implies a minimum, not a peak) -- caller falls back.
    """
    centres: list[float] = []
    slopes: list[float] = []
    for pts in _segment(samples):
        if len(pts) < min_seg_points:
            continue
        xs = np.asarray([p for p, _ in pts], dtype=np.float64)
        ys = np.asarray([s for _, s in pts], dtype=np.float64)
        if np.ptp(xs) <= 0 or not np.all(np.isfinite(ys)) or ys.min() <= 0:
            continue
        slopes.append(float(np.polyfit(xs, np.log(ys), 1)[0]))
        centres.append(float(xs.mean()))
    if len(slopes) < min_segments:
        return None
    cx_all = np.asarray(centres)
    cy_all = np.asarray(slopes)
    if np.ptp(cx_all) <= 0:
        return None
    # Refit locally, twice. Far from focus the sharpness metric does not keep falling --
    # it bottoms out on a sensor/scene noise floor -- so those segments report ~flat
    # slopes where a Gaussian predicts steep ones. They sit at the longest lever arm from
    # the crossing, so a handful of them dominates the fitted gradient: live run 8, and a
    # 2%-floor simulation of it, both put the crossing +9 pulses out, pinned to the clamp.
    # The window comes from the fit itself (its gradient is -1/sigma^2), so no absolute
    # scale or hand-tuned width enters and the estimate stays scale-free.
    cx, cy = cx_all, cy_all
    xv: Optional[float] = None
    for _ in range(3):
        if np.ptp(cx) <= 0:
            break
        m, b = np.polyfit(cx, cy, 1)
        if m >= 0:  # log-slope rising with position => a trough, not a peak
            if xv is None:
                return None
            break
        xv = float(-b / m)
        sigma = 1.0 / np.sqrt(-m)
        keep = np.abs(cx_all - xv) <= 2.0 * sigma
        if int(keep.sum()) < min_segments or int(keep.sum()) == cx.size:
            break  # too few to refit, or the set stopped shrinking
        cx, cy = cx_all[keep], cy_all[keep]
    if xv is None:
        return None
    # Clamp into the swept range: beyond it the linear-slope model is extrapolating.
    lo = min(p for p, _, _ in samples)
    hi = max(p for p, _, _ in samples)
    return float(min(max(xv, lo), hi))


def _fit_peak(samples: list, window: int, log_domain: bool = True) -> Optional[float]:
    """Sub-sample peak position of a noisy score curve via a local parabola fit.

    ``samples`` is the climb's ``[(pos, score), ...]`` in climb order. We fit a
    quadratic to the ``window`` points on each side of the argmax and return the
    vertex ``-b/2a`` -- using ALL those points averages out the per-sample noise
    that makes a raw argmax jump around. Returns ``None`` (caller falls back to
    argmax) when there aren't enough points, the fit isn't concave (``a >= 0`` --
    no real hump), or the vertex lands outside the fitted span (extrapolation is
    not a measurement). The vertex is clamped to the fitted x-range as a guard.

    ``log_domain`` fits ``log(score)`` first. The metric is a variance estimator,
    so its noise is multiplicative (SD grows with the score); ``log`` is the
    variance-stabilising transform that turns that into ~constant additive noise,
    which is what plain least-squares assumes. It's also a better-shaped model: a
    near-focus sharpness peak is ~Gaussian and ``log(Gaussian)`` is exactly a
    parabola. If the log fit is degenerate we fall through to the linear fit (also
    the ``log_domain=False`` path).
    """
    if not samples or window < 1:
        return None
    xs_all = [p for p, _ in samples]
    ys_all = [s for _, s in samples]
    i = int(np.argmax(ys_all))
    lo, hi = max(0, i - window), min(len(samples), i + window + 1)
    xs = np.asarray(xs_all[lo:hi], dtype=np.float64)
    ys = np.asarray(ys_all[lo:hi], dtype=np.float64)
    if xs.size < 3 or np.ptp(xs) <= 0:
        return None
    xmin, xmax = float(xs.min()), float(xs.max())

    def _vertex(vals: np.ndarray) -> Optional[float]:
        a, b, _c = np.polyfit(xs, vals, 2)
        if a >= 0:  # not concave -> no interior peak to refine
            return None
        xv = -b / (2.0 * a)
        if xv < xmin or xv > xmax:  # extrapolation is not a measurement
            return None
        return float(xv)

    if log_domain:
        r = _vertex(np.log(np.clip(ys, 1e-9, None)))
        if r is not None:
            return r
    return _vertex(ys)  # linear fallback / log_domain=False


class _CrossCal:
    """Overlap cross-calibration between two tracked objects' focus scores.

    While object A (the scored primary) and object B (the on-deck handover target)
    are BOTH in the frame, we score each on the SAME frame and keep their ratio. The
    robust ``ratio()`` = ``median(A / B)`` is the scale that maps B's scores onto A's
    scale, so when A pans off and B is promoted the stitched score curve stays
    continuous (``B_norm = B * ratio``). Two design choices matter:

    * We keep RATIOS, not a slope fit: the focus metric is a variance estimator with
      multiplicative noise, so a per-frame A/B ratio is the noise-stable quantity.
    * We take the MEDIAN, so a single blurred/occluded overlap frame can't skew the
      calibration (the mean would).

    ``valid()`` gates on having at least ``min_overlap`` usable pairs -- a handover with
    too little shared visibility is untrustworthy, and the engine falls back rather
    than stitching blind.
    """

    def __init__(self, min_overlap: int) -> None:
        self._min_overlap = int(min_overlap)
        self._ratios: list[float] = []

    def add(self, score_a: Optional[float], score_b: Optional[float]) -> None:
        # Guard the denominator: a near-flat/near-black B gives a huge, meaningless
        # ratio that median can't fully tame if enough of them pile up.
        if score_a is None or score_b is None:
            return
        if score_a > 0.0 and score_b > 1e-6:
            self._ratios.append(float(score_a) / float(score_b))

    def valid(self) -> bool:
        return len(self._ratios) >= self._min_overlap

    def ratio(self) -> float:
        return float(np.median(self._ratios)) if self._ratios else 1.0

    @property
    def count(self) -> int:
        return len(self._ratios)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class AutoFocusConfigV2:
    """Tunables for one dive-then-climb run.

    The selection-related fields (edge_band .. full_frame_target) carry the same
    names/meanings as :class:`capture.autofocus.AutoFocusConfig` so the reused
    ``select_leading_edge_roi`` picker consumes this config unchanged.
    """

    # %% Search: dive (fast, below focus) %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    dive_strength: int = 3
    """Focus strength for the fast dive below focus (str3 = big ~10 ms pulses)."""
    dive_bursts: int = 4
    """Number of SEPARATE '-' pulse bursts in the dive. Separate (not one long
    burst) so the encoder is re-read between them -- a stalled motor or an end stop
    is caught early instead of after one long blind drive."""
    dive_burst_pulses: int = 15
    """Pulses per dive burst (at ``dive_strength``). ``dive_bursts`` x this sets the
    total dive depth, which must overshoot below focus so the climb passes through
    it. ponytail: str3 counts/pulse is not calibrated yet -- the per-burst encoder
    delta is logged (af2-dive), so tune this from the first on-rig run."""
    dive_burst_interval_s: float = 0.7
    """Wall-clock wait between dive bursts so a burst fully executes and the ~10 Hz
    encoder registers it before the next burst / the downward-motion check."""
    dive_min_drop_counts: float = 20.0
    """The dive must move the position DOWN by at least this many encoder COUNTS in
    total, else we fail ('motor stuck') rather than climbing from nowhere.

    NOTE on every ``*_counts`` default in this config: they are sized for the
    encoderless ``OpenLoopAxis``, where ``encoder_counts_per_pulse`` is 1.0 and a
    count IS a pulse. A rig with a real encoder must scale them by its measured
    counts/pulse -- see the ``AutoFocusConfigV2(...)`` block in app.py, which does
    exactly that off ``af_counts_per_pulse``."""

    # %% Search: climb (fine, one sample per frame) %%%%%%%%%%%%%%%%%%%%%%
    strength: int = 0
    """Focus strength for the fine climb (str0 = finest 3 ms pulse). Named
    ``strength`` for parity with the legacy config / app wiring."""
    climb_step_pulses: int = 2
    """Upward step (str0 PULSES -- a drive command, so it cannot be in counts) taken
    once per frame during the climb. Small = finer peak localisation but a longer
    climb. On the rig one str0 pulse is ~24 counts, so this samples every ~48."""
    climb_settle_frames: int = 1
    """Frames to take after each climb step, scoring ONLY the last one. 1 = score
    every frame (fastest). >1 lets the shaft + the ~10 Hz encoder settle before the
    (pos, score) sample so the climb reads an un-lagged position -- slower, but removes
    the motion-lag offset that leaves the park a few counts below the true peak."""
    climb_max_pulses: int = 400
    """Safety cap on total upward climb, in PULSES COMMANDED (not counts measured --
    the whole point is to stop climbing even if a stalled motor reports no counts).
    Must exceed the dive depth plus the worst start-to-focus gap."""
    peak_fall_frac: float = 0.15
    """The climb stops once the score has fallen this fraction below its best AND
    stayed there for ``peak_confirm_samples`` frames -- i.e. we've climbed through
    focus."""
    peak_confirm_samples: int = 3
    """Consecutive post-peak frames below best*(1-peak_fall_frac) needed to confirm
    the peak (rejects a single-frame noise dip)."""
    climb_min_samples: int = 5
    """Minimum samples before a peak may be declared (need a rising shoulder first)."""
    climb_lucky_frames: int = 1
    """Lucky-imaging: frames scored at EACH encoder position before stepping. The
    per-position score is the MAX over them -- turbulence/vibration only ever blur
    (lower) sharpness, so the sharpest frame in a short burst is the least-corrupted
    read of true focus, while the mean is biased low. 1 = score every frame (fastest,
    today's behaviour); >1 trades ~Nx climb time for a cleaner curve. Supersedes the
    dead ``climb_settle_frames`` knob (which was set but never read)."""
    peak_curve_fit: bool = True
    """Refine ``best_pos`` by fitting a parabola to the score curve near the argmax
    (see :func:`_fit_peak`) instead of trusting a single noisy sample. Off -> raw
    argmax (revert switch)."""
    peak_fit_window: int = 3
    """Samples each side of the argmax used by the curve fit."""
    peak_fit_log: bool = True
    """Fit the peak parabola on log(score) (variance-stabilising for the metric's
    multiplicative noise; log(Gaussian)==parabola). Off -> plain linear fit."""

    # %% Park + hold %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    park_stable_counts: float = 2.0
    """The park is only DONE once the encoder has stopped moving (frame-to-frame
    change <= this many COUNTS). Reaching the target alone latched too early: the
    park passes DOWN through best_pos on its way to (best_pos - lash), so we'd
    measure mid-move. Requiring the shaft to be at rest waits out the full
    down-then-up pulse train."""
    park_stable_frames: int = 2
    """Consecutive at-target-and-stable frames required before the park is DONE."""
    park_max_wait_s: float = 45.0
    """Bound on the CREEP-up leg (clock is reset when descend hands off). A creep that
    never reaches best_pos still finishes rather than hanging forever. AF runs at only
    ~1-2 fps, so a deep (~1 lash + margin) creep at park_coarse_step_pulses/frame needs
    tens of seconds -- too tight a bound made the creep TIME OUT short of the peak."""
    park_descend_max_s: float = 35.0
    """Separate bound on the descend leg. Previously descend + creep shared one clock,
    so a stalled descend ate the whole budget and the creep never ran (parked far below
    focus). At ~1-2 fps the deep descend needs this much wall-clock even at the coarse
    park step -- a 20 s bound timed out at only ~26 pulses below the peak (< 1 lash), so
    the lash never reversed and the lens parked soft ('it went back a lot less')."""
    step_gate: bool = False
    """Debug: pause at every phase boundary (acquire->dive->climb->descend->creep->
    settle) and wait for ``step()`` (operator SPACE) before continuing. Lets the
    operator read the encoder / inspect the image between moves to confirm where the
    motor actually goes -- e.g. whether the post-climb descend flings it to end-of-
    travel. No motor moves while paused."""
    park_slow_band_counts: float = 12.0
    """Within this many COUNTS of best_pos the creep drops to single-pulse steps so
    the lens is seated onto the peak gently (operator saw the final move overshoot when
    it arrived fast)."""
    park_climb_band_counts: float = 36.0
    """Between park_slow_band_counts and this, the creep slows from the coarse bulk step
    to the CLIMB's own pace (climb_step_pulses/frame) -- so the final approach onto the
    peak mirrors the climb that found it, decelerating well before the seat instead of
    blasting in at the coarse step and stopping abruptly. Must exceed
    park_slow_band_counts."""
    park_coarse_step_pulses: int = 6
    """str0 PULSES/frame for the DESCEND and the far-from-peak part of the creep. AF is
    frame-gated at ~1-2 fps, so the fine climb step (2/frame) can't traverse the deep
    lash-clearing excursion within the time budget -- it timed out short and left the
    lash un-reversed. Precision doesn't matter out here (we're below focus, not
    measuring); the gentle single-pulse seat inside park_slow_band_counts still lands
    the peak without aliasing, so this only speeds the bulk travel."""
    park_descend_margin_counts: float = 20.0
    """The park first descends to best_pos - lash - THIS many COUNTS, and waits until
    the encoder actually REACHES that depth, so the gear lash is fully reversed (the
    lens really moves below focus) before the creep back up. Descending only ~to the
    lash -- or stopping short on a stability guess -- left the slack un-reversed, so the
    lens stayed at the climb's overshoot (past focus) and parked soft."""
    park_settle_s: float = 1.2
    """Wall-clock dwell AFTER the encoder has arrived at best_pos (lets the shaft ring
    down) before the parked-score sanity check + hold."""
    park_settle_max_s: float = 8.0
    """HARD cap on the whole settle sub-phase. The dwell above wants the encoder to sit
    still within park_stable_counts, but full-res crops stream every frame during park
    and jitter the encoder SPI read, so on a live rig that dwell can never latch and the
    park hangs at 'parking at focus' forever. We already KNOW best_pos from the climb, so
    when this cap trips we measure once (best effort) and hold there anyway."""
    settle_frames: int = 3
    """Frames averaged for the parked-focus sanity measurement."""
    backlash_pulses: int = 3
    """Gear/spur lash take-up, in PULSES (a drive amount), for the upward park. Must
    be >= the mechanism's lash band so the park reliably seats focus from below."""
    encoder_counts_per_pulse: float = 1.0
    """AMT203S motor counts per str0 focus pulse. Its SIGN encodes polarity -- make it
    NEGATIVE when a '+' focus pulse drives the raw encoder count DOWN, so "drive '+'"
    maps to "position increases" (a wrong sign makes the axis walk off instead of
    tracking focus). Its MAGNITUDE converts a wanted move in counts into whole pulses,
    and sets the park's landing tolerance (half a pulse -- the finest step there is).
    ponytail: CALIBRATE ON-RIG (drive a known pulse burst, read the signed encoder
    delta); 1.0 is only a self-check placeholder."""
    min_contrast: float = 0.15
    """The climbed curve peak must rise this fraction above its floor
    ((max-min)/max) to count as a real focus peak; below it the range is flat (no
    focusable detail) -> fail instead of parking on a noise max."""

    # %% Object tracking %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    min_track_response: float = 0.05
    """phaseCorrelate response floor below which we don't trust the motion vector
    (so we hold the ROI rather than chase a garbage shift)."""

    # %% Cross-object handover (moving-scene climb) %%%%%%%%%%%%%%%%%%%%%%%
    enable_handover: bool = True
    """Keep a second leading-edge object on deck during the CLIMB and cross-calibrate
    the two so the score-vs-encoder curve can be stitched across a handover when the
    primary pans off mid-climb. Off = single-object climb (finishes/fails as before
    when the target leaves), which is all a near-static bench needs."""
    xcal_min_overlap_frames: int = 6
    """Minimum frames with BOTH objects textured-and-visible before their cross-cal
    ratio is trusted for a handover. Fewer -> too little shared visibility to pin the
    A/B scale; the engine then finishes the climb with the peak so far instead of
    stitching a new object on an unknown scale."""
    handover_stagger_frames: int = 8
    """Don't acquire the on-deck object until this many frames after the primary was
    acquired, so the pair doesn't enter (and later leave) the FOV in lockstep -- that
    would give near-zero overlap to calibrate on. Mirrors DualObjectTracker's stagger."""

    # %% Hold / re-verify %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    reverify_interval_s: float = 0.0
    """After locking, a continuous (flight) run idles. If > 0 it re-runs the whole
    search this often (focus drifts over hours). 0 = hold indefinitely (default)."""

    # %% Drive cadence (start-of-run, like the legacy engine) %%%%%%%%%%%%
    drive_cadence_to_min: bool = True
    cadence_steps: int = 26

    # %% Texture / failure detection %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    min_texture_score: float = 1.5
    max_low_texture_frames: int = 40

    # %% Leading-edge target selection (consumed by select_leading_edge_roi) %
    edge_band: float = 0.25
    tiles_per_frame: float = 5.0
    select_subsample: int = 2
    sat_level: float = 0.98
    restrict_interior: bool = False
    interior_inset: float = 0.12
    exclude_edge: Optional[str] = None
    full_frame_target: bool = False


# ---------------------------------------------------------------------------
# Tracked object + engine state
# ---------------------------------------------------------------------------


@dataclass
class _TrackedObject:
    """The tracked target patch: an ROI that ``PatchTracker`` slides to follow the
    scene, scored once per climb frame to build the score-vs-encoder curve."""

    oid: int
    roi: Roi


class AFStateV2(Enum):
    IDLE = "idle"
    ACQUIRE = "acquire"
    DIVE = "dive"
    CLIMB = "climb"
    PARK = "park"
    HOLD = "hold"
    DONE = "done"
    FAILED = "failed"


@dataclass
class _RunState:
    continuous: bool = True
    seed_roi: Optional[Roi] = None
    lead_hint: Optional[str] = None
    frames: int = 0
    low_texture_frames: int = 0
    motion_dx: float = 0.0
    motion_dy: float = 0.0
    objects: list = field(default_factory=list)  # objects[0] = the scored target
    next_oid: int = 0
    # cross-object handover (climb): objects[0] is the scored primary, objects[1] the
    # on-deck target. cross_cal builds the A/B scale during their overlap; on handover
    # cumulative_scale folds it in so the whole climb curve lives on the first object's
    # scale (the peak detector then works across the seam).
    primary_oid: Optional[int] = None
    ondeck_oid: Optional[int] = None
    cross_cal: Optional["_CrossCal"] = None
    cumulative_scale: float = 1.0
    handovers: int = 0
    seam_positions: list = field(default_factory=list)
    last_acquire_frame: int = -(10 ** 9)
    # dive
    dive_start_pos: float = 0.0
    dive_bursts_left: int = 0
    dive_last_t: float = 0.0
    # climb
    # [(pos:int, score:float, oid:int)] -- the oid marks segment boundaries so the peak
    # can be located per-object (scale-free) instead of on one stitched absolute curve.
    climb_samples: list = field(default_factory=list)
    climb_pulses_done: int = 0
    best_score: float = -1e30
    min_score: float = 1e30
    best_pos: Optional[int] = None
    best_oid: Optional[int] = None  # object the peak was measured on; park compares to it
    fell_count: int = 0
    # climb
    climb_start_pos: float = 0.0
    climb_wait: int = 0  # frames left to settle after a step before scoring
    lucky_scores: list = field(default_factory=list)  # scores at the current position
    lucky_left: int = 0  # frames still to collect at the current position before stepping
    last_score: Optional[float] = None  # score _emit_sample computed this frame (reused by park settle)
    # park
    park_phase: str = "descend"   # descend -> creep -> settle
    park_descend_target: float = 0.0
    park_since: float = 0.0
    park_move_since: float = 0.0
    park_settle_since: float = 0.0  # settle-phase entry (never reset by jitter -> hard cap)
    park_creep_last_t: float = 0.0
    park_last_pos: float = 1e9
    park_scores: list = field(default_factory=list)
    hold_since: float = 0.0


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class AutoFocusEngineV2:
    """Frame-driven dive-then-climb autofocus (Qt-free, fully testable)."""

    def __init__(
        self,
        *,
        pulse: Callable[[str, int], None],
        set_strength: Optional[Callable[[int], None]] = None,
        adjust_cadence: Optional[Callable[[str], None]] = None,
        status: Optional[Callable[[str], None]] = None,
        finished: Optional[Callable[[bool, dict], None]] = None,
        config: Optional[AutoFocusConfigV2] = None,
        full_res_score: Optional[Callable[[Roi], Optional[float]]] = None,
        sample: Optional[Callable[[int, float, bool], None]] = None,
        axis: Optional[FocusAxis] = None,
    ) -> None:
        self.cfg = config or AutoFocusConfigV2()
        self._set_strength = set_strength
        self._adjust_cadence = adjust_cadence
        self._status = status or (lambda _m: None)
        self._finished = finished or (lambda _ok, _r: None)
        self._full_res_score = full_res_score
        self._sample = sample or (lambda _p, _s, _f=False: None)
        self._pulse = pulse  # kept so set_axis() can rebuild the open-loop fallback
        # Inject a closed-loop EncoderAxis to run on real motor position; default to
        # the open-loop pulse-count axis (unchanged behaviour + all self-checks).
        self._axis = axis if axis is not None else OpenLoopAxis(
            pulse, backlash_pulses=self.cfg.backlash_pulses
        )
        self._tracker = PatchTracker()
        self.state = AFStateV2.IDLE
        self._rs = _RunState()
        # step-gate (debug): paused at a phase boundary awaiting operator SPACE.
        self._paused = False
        self._gate_resume: Optional[Callable[[], None]] = None
        self._gate_label = ""
        # Accepted for interface parity with the legacy engine; v2 has a single
        # dive-then-climb profile, so there is nothing to adapt.
        self.adaptive_profile = False

    def set_axis(self, axis: Optional[FocusAxis]) -> None:
        """Swap the focus axis (call while idle). ``None`` rebuilds the open-loop
        pulse-count axis; a concrete axis (e.g. EncoderAxis) drives closed-loop."""
        self._axis = axis if axis is not None else OpenLoopAxis(
            self._pulse, backlash_pulses=self.cfg.backlash_pulses
        )

    # %% lifecycle %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

    @property
    def running(self) -> bool:
        return self.state not in (AFStateV2.IDLE, AFStateV2.DONE, AFStateV2.FAILED)

    @property
    def current_roi(self) -> Optional[Roi]:
        rs = self._rs
        return rs.objects[0].roi if rs.objects else None

    @property
    def _counts_per_pulse(self) -> float:
        """Encoder counts in one str0 pulse, as a positive magnitude. Taken from the
        axis when it knows (the open-loop fallback defines a count AS a pulse)."""
        return getattr(self._axis, "counts_per_pulse_mag", None) or abs(
            float(self.cfg.encoder_counts_per_pulse)) or 1.0

    @property
    def _land_tolerance(self) -> float:
        """How close to a target position counts as arrived: half a pulse, because that
        is the finest move the hardware can make. Anything larger, on a one-directional
        approach, is a systematic offset rather than a tolerance -- see the creep."""
        return self._counts_per_pulse / 2.0

    def start(
        self,
        *,
        lead_edge_hint: Optional[str] = None,
        continuous: bool = True,
        seed_roi: Optional[Roi] = None,
    ) -> None:
        self._tracker.reset()
        self._axis.reset()
        self._paused = False
        self._gate_resume = None
        self._rs = _RunState(continuous=bool(continuous), seed_roi=seed_roi,
                             lead_hint=lead_edge_hint)
        self.state = AFStateV2.ACQUIRE
        if self.cfg.drive_cadence_to_min and self._adjust_cadence is not None:
            for _ in range(self.cfg.cadence_steps):
                try:
                    self._adjust_cadence("<")
                except Exception:  # pragma: no cover
                    log.exception("adjust_cadence failed")
                    break
        self._status("autofocus v2: acquiring target")

    def stop(self, reason: str = "stopped by operator") -> None:
        self._paused = False
        self._gate_resume = None
        if not self.running:
            return
        rs = self._rs
        # HALT IN PLACE -- do NOT move the motor on stop. The dive-then-climb PARK
        # already seated the lens on focus, so pressing STOP should just end the hold.
        # The old code re-parked here with the LEGACY park_upward() (a big absolute
        # move_to burst), which yanked the lens off focus with rapid moves on STOP and
        # left pulses draining that corrupted the next run's dive. Just stop.
        self._set_strength_safe(self.cfg.strength)
        if rs.best_pos is not None:
            self.state = AFStateV2.DONE
            self._status(f"autofocus v2: stopped (holding centre={rs.best_pos:+d}c)")
            self._finished(True, self._report(ok=True, stopped=True))
            return
        self._fail(reason)

    # %% per-frame step %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

    # %% step-gate (debug) %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    def _gate(self, done_label: str, next_label: str,
              resume: Callable[[], None]) -> None:
        """Pause at a phase boundary until ``step()``. When ``step_gate`` is off this
        is a straight passthrough (calls ``resume`` immediately)."""
        if not self.cfg.step_gate:
            resume()
            return
        self._gate_resume = resume
        self._gate_label = next_label
        self._paused = True
        pos = int(round(self._axis.position))
        raw = getattr(self._axis, "raw_count", None)
        rawtxt = "" if raw is None else f" raw={int(round(raw))}"
        self._status(f"PAUSED after {done_label} @ pos={pos:+d}c{rawtxt}"
                     f" -- press SPACE for {next_label}")
        log.info("af2-gate paused after %s pos=%+dc%s -- awaiting SPACE",
                 done_label, pos, rawtxt)

    def step(self) -> None:
        """Operator pressed SPACE: release the current phase gate and continue."""
        if not self._paused:
            return
        self._paused = False
        fn, self._gate_resume = self._gate_resume, None
        log.info("af2-gate released -> %s", self._gate_label)
        if fn is not None:
            fn()

    def feed_frame(self, frame: np.ndarray) -> None:
        if not self.running:
            return
        if self._paused:
            return  # gated at a phase boundary; no motor moves until step()
        if frame is None or frame.ndim < 2:
            return
        rs = self._rs
        rs.frames += 1

        dx, dy, response = self._tracker.update(frame)
        rs.motion_dx, rs.motion_dy = dx, dy

        if self.state == AFStateV2.ACQUIRE:
            self._acquire(frame)
            return

        if self.state == AFStateV2.HOLD:
            self._hold_tick(frame)
            return

        # Keep the scored ROI on the target as the scene pans.
        self._advance_objects(frame, dx, dy, response)

        # Sky / featureless phase: pause -- never count these frames toward the
        # measurement (they would pull a false low into the curve).
        if self._frame_is_featureless(frame):
            rs.low_texture_frames += 1
            if rs.low_texture_frames > self.cfg.max_low_texture_frames:
                self._fail("low texture (featureless frame)")
            return
        rs.low_texture_frames = 0

        if self.state == AFStateV2.DIVE:
            self._emit_sample(frame)  # trace the dive on the graph too
            self._dive_tick(frame)
        elif self.state == AFStateV2.CLIMB:
            self._climb_tick(frame)   # emits its own settled samples
        elif self.state == AFStateV2.PARK:
            self._emit_sample(frame)  # trace descend + creep back up onto the peak
            self._park_tick(frame)

    def _emit_sample(self, frame: np.ndarray) -> None:
        """Push one (encoder, score) point to the graphs for the current frame, so the
        scatter graph traces the WHOLE run (dive down, climb up, park down-and-back-up)
        -- not just the climb. Cheap: reuses the same score the ticks would compute."""
        rs = self._rs
        if not rs.objects:
            rs.last_score = None
            return
        pos = int(round(self._axis.position))
        raw = self._score_primary(frame)
        # Same normalisation as the climb: after a handover the parked object is the
        # promoted one, so scale its score onto the anchor object's curve so the park
        # settle's "parked but soft" check compares against the peak on one scale.
        score = raw * rs.cumulative_scale if raw is not None else None
        rs.last_score = score  # stash: park settle reuses this (the full-res
        # latch is consume-once, so re-scoring the same frame there returns None)
        if score is not None:
            self._sample(pos, score, True)

    # %% acquisition %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

    def _acquire(self, frame: np.ndarray) -> None:
        rs = self._rs
        if rs.seed_roi is not None:
            clamped = _clamp_roi(tuple(int(v) for v in rs.seed_roi),
                                 frame.shape[1], frame.shape[0])
            rs.seed_roi = None
            if clamped is not None:
                self._add_object(clamped)
                log.info("af2-acquire seeded-from-fine roi=%s", clamped)
                self._gate("acquire", "DIVE", self._begin_dive)
                return
        lead = self._resolve_lead()
        roi, score = select_leading_edge_roi(frame, lead, self.cfg)
        if score < self.cfg.min_texture_score:
            rs.low_texture_frames += 1
            if rs.low_texture_frames > self.cfg.max_low_texture_frames:
                self._fail("low texture at acquisition")
            self.state = AFStateV2.ACQUIRE
            return
        rs.low_texture_frames = 0
        self._add_object(roi)
        edge = lead if lead is not None else "full-frame"
        log.info("af2-acquire target=%s roi=%s score=%.1f", edge, roi, score)
        self._gate("acquire", "DIVE", self._begin_dive)

    def _resolve_lead(self) -> Optional[str]:
        if self.cfg.full_frame_target:
            return None
        rs = self._rs
        return rs.lead_hint or leading_edge_from_motion(rs.motion_dx, rs.motion_dy)

    def _add_object(self, roi: Roi) -> _TrackedObject:
        rs = self._rs
        obj = _TrackedObject(oid=rs.next_oid, roi=roi)
        rs.next_oid += 1
        rs.objects.append(obj)
        return obj

    # %% object tracking %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

    def _advance_objects(self, frame, dx, dy, response) -> None:
        rs = self._rs
        fh, fw = frame.shape[:2]
        if response >= self.cfg.min_track_response:
            survivors: list[_TrackedObject] = []
            for obj in rs.objects:
                moved = (obj.roi[0] + dx, obj.roi[1] + dy,
                         obj.roi[2] + dx, obj.roi[3] + dy)
                clamped = _clamp_roi(
                    (int(round(moved[0])), int(round(moved[1])),
                     int(round(moved[2])), int(round(moved[3]))),
                    fw, fh,
                )
                if clamped is None or _roi_pinned_against_flow(clamped, dx, dy, fw, fh):
                    continue  # panned off the FOV -> drop it
                obj.roi = clamped
                survivors.append(obj)
            rs.objects = survivors
        # Target gone -> reacquire a fresh leading-edge patch so measuring can go on.
        if not rs.objects:
            lead = self._resolve_lead()
            roi, score = select_leading_edge_roi(frame, lead, self.cfg)
            if score >= self.cfg.min_texture_score:
                self._add_object(roi)
                rs.last_acquire_frame = rs.frames
            return
        # Handover prep (CLIMB only): keep a SECOND, non-overlapping leading-edge patch
        # on deck so that if the primary pans off mid-climb we can promote an object we
        # already cross-calibrated against -- rather than a cold fresh patch on an
        # unknown score scale. Staggered so the pair doesn't march through the FOV in
        # lockstep (which would give ~no overlap to calibrate on).
        # ...but NOT on a frame where the cull above just promoted the on-deck object
        # into objects[0]. Refilling here would overwrite `ondeck_oid` and reset the
        # `cross_cal` that `_handle_handover` -- which runs later this same frame -- needs
        # to recognise the promotion and stitch it. Since the refill triggers at
        # `handover_stagger_frames` (8) but the stitch needs only `xcal_min_overlap_frames`
        # (6), clobbering left a 2-frame-wide window as the ONLY stitchable overlap: live
        # runs logged 38 "no stitchable cross-cal" re-anchors and handovers=0, so the climb
        # curve mixed patches of differing intrinsic texture. Two runs on a static bench
        # then disagreed by 38 pulses and parked at 0.36/0.52 of their own declared peak.
        # Deferring the refill by one frame costs nothing: the promoted object was picked
        # at the leading edge and has most of the FOV still to cross.
        promoted = rs.primary_oid is not None and rs.objects[0].oid != rs.primary_oid
        if (
            self.cfg.enable_handover
            and self.state == AFStateV2.CLIMB
            and len(rs.objects) == 1
            and not promoted
            and response >= self.cfg.min_track_response
            and (rs.frames - rs.last_acquire_frame) >= self.cfg.handover_stagger_frames
        ):
            lead = self._resolve_lead()
            roi, score = select_leading_edge_roi(frame, lead, self.cfg)
            if score >= self.cfg.min_texture_score and not _overlaps(roi, rs.objects[0].roi):
                obj = self._add_object(roi)
                rs.ondeck_oid = obj.oid
                # Fresh calibration: the ratio is A/B for THIS B, so any stale ratios
                # from a previous on-deck object (that panned off before promotion) must
                # not carry over.
                rs.cross_cal = _CrossCal(self.cfg.xcal_min_overlap_frames)
                rs.last_acquire_frame = rs.frames
                log.info("af2-climb on-deck acquired oid=%d roi=%s", obj.oid, roi)

    # %% dive (fast, below focus) %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

    def _begin_dive(self) -> None:
        rs = self._rs
        self._set_strength_safe(self.cfg.dive_strength)
        rs.dive_start_pos = float(self._axis.position)
        rs.dive_bursts_left = max(1, self.cfg.dive_bursts)
        rs.dive_last_t = 0.0  # 0 => first burst fires on the next tick
        self.state = AFStateV2.DIVE
        self._status("autofocus v2: diving below focus")
        log.info("af2-dive begin start=%+dc bursts=%d x%d pulses",
                 int(round(rs.dive_start_pos)), self.cfg.dive_bursts,
                 self.cfg.dive_burst_pulses)

    def _dive_tick(self, frame: np.ndarray) -> None:
        rs = self._rs
        now = time.monotonic()
        # Let the previous burst execute + the ~10 Hz encoder register it.
        if rs.dive_last_t and (now - rs.dive_last_t) < self.cfg.dive_burst_interval_s:
            return
        pos = float(self._axis.position)
        if rs.dive_bursts_left <= 0:
            dropped = rs.dive_start_pos - pos  # +ve if we moved DOWN
            raw = getattr(self._axis, "raw_count", None)
            log.info("af2-dive done pos=%+dc dropped=%+dc raw=%s", int(round(pos)),
                     int(round(dropped)),
                     "n/a" if raw is None else int(round(raw)))
            if dropped < self.cfg.dive_min_drop_counts:
                self._fail(
                    f"dive moved down only {int(round(dropped))} counts "
                    f"(motor stuck / wrong direction?)"
                )
                return
            self._gate("dive", "CLIMB", self._begin_climb)
            return
        # Fire one downward burst (open-loop; we read the encoder next tick).
        self._axis.step_pulses(-1, self.cfg.dive_burst_pulses)
        rs.dive_bursts_left -= 1
        rs.dive_last_t = now
        raw = getattr(self._axis, "raw_count", None)
        log.info("af2-dive burst left=%d pos=%+dc raw=%s", rs.dive_bursts_left,
                 int(round(pos)), "n/a" if raw is None else int(round(raw)))

    # %% climb (fine, lucky-imaging max over climb_lucky_frames per position) %%%

    def _begin_climb(self) -> None:
        rs = self._rs
        self._set_strength_safe(self.cfg.strength)
        rs.climb_samples = []
        rs.climb_pulses_done = 0
        rs.best_score = -1e30
        rs.min_score = 1e30
        rs.best_pos = None
        rs.fell_count = 0
        rs.lucky_scores = []
        rs.lucky_left = max(1, self.cfg.climb_lucky_frames)
        rs.climb_start_pos = float(self._axis.position)
        # Handover bookkeeping: the object we start scoring is the calibration anchor
        # (scale 1.0); a fresh cross-cal collects the A/B ratio for the on-deck object.
        rs.primary_oid = rs.objects[0].oid if rs.objects else None
        rs.ondeck_oid = None
        rs.cross_cal = _CrossCal(self.cfg.xcal_min_overlap_frames)
        rs.cumulative_scale = 1.0
        rs.handovers = 0
        rs.seam_positions = []
        rs.last_acquire_frame = rs.frames
        self.state = AFStateV2.CLIMB
        self._status("autofocus v2: climbing to focus")
        log.info("af2-climb begin from pos=%+dc step=%dp lucky=%d handover=%s",
                 int(round(rs.climb_start_pos)), self.cfg.climb_step_pulses,
                 self.cfg.climb_lucky_frames, self.cfg.enable_handover)

    def _handle_handover(self) -> bool:
        """Reconcile a mid-climb primary change that ``_advance_objects`` (run earlier
        this frame) may have made by dropping the panned-off primary and shifting the
        on-deck object into ``objects[0]``. Returns True iff the climb was finished
        here (the caller must then return).

        Only a valid, calibrated promotion of the KNOWN on-deck object may be stitched:
        we fold its cross-cal scale into ``cumulative_scale`` so the curve stays on the
        anchor object's scale. Any other primary change -- a cold fresh patch acquired
        after BOTH objects were lost, or a promotion with too little overlap to trust --
        is unstitchable, so we finish the climb with the peak found so far instead of
        extending the curve on an unknown scale."""
        rs = self._rs
        cur = rs.objects[0].oid if rs.objects else None
        if cur is None or rs.primary_oid is None or cur == rs.primary_oid:
            if cur is not None:
                rs.primary_oid = cur
            return False
        if (
            self.cfg.enable_handover
            and cur == rs.ondeck_oid
            and rs.cross_cal is not None
            and rs.cross_cal.valid()
        ):
            r = rs.cross_cal.ratio()
            rs.cumulative_scale *= r
            rs.handovers += 1
            pos = int(round(self._axis.position))
            rs.seam_positions.append(pos)
            log.info("af2-climb handover #%d @pos=%+dc ratio=%.3f cum_scale=%.4g "
                     "(overlap n=%d)", rs.handovers, pos, r, rs.cumulative_scale,
                     rs.cross_cal.count)
            rs.cross_cal = _CrossCal(self.cfg.xcal_min_overlap_frames)
            rs.primary_oid = cur
            rs.ondeck_oid = None
            return False
        # Not a stitchable handover: either tracker relabel churn on a near-static
        # scene (no on-deck acquired yet) or a cold reacquire after both were lost.
        # The pre-handover climb never tracked identity -- it just kept scoring
        # objects[0] -- so do the same: re-anchor onto the new primary and keep
        # climbing rather than aborting a barely-started climb. Peak POSITION is
        # scale-invariant within a segment, so accumulating across an equal-scale
        # relabel is safe. ponytail: an *uncalibrated* real handover (primary pans
        # off before overlap calibrates) continues on an unknown A/B scale, which
        # can bias only the cross-seam score COMPARE, not the argmax; the upgrade
        # path is the calibrated stitch above, which handover_stagger is tuned for.
        log.info("af2-climb primary changed %s->%s, no stitchable cross-cal -> "
                 "re-anchor and continue", rs.primary_oid, cur)
        rs.primary_oid = cur
        rs.ondeck_oid = None
        if rs.cross_cal is not None:
            rs.cross_cal = _CrossCal(self.cfg.xcal_min_overlap_frames)
        return False

    def _climb_tick(self, frame: np.ndarray) -> None:
        rs = self._rs
        cfg = self.cfg
        pos = int(round(self._axis.position))

        # Fold in any handover the tracker made this frame (may finish the climb).
        if self._handle_handover():
            return

        # While BOTH objects are visible, sample their SAME-frame coarse scores to build
        # the A/B calibration the next handover uses. Coarse (not full-res) because the
        # pipeline taps only one full-res crop/frame (the primary's); the ratio is a
        # content-scale constant that survives the resolution drop (module notes) -- but
        # only if measured in the curve's own units, hence _coarse_pair_score rather than
        # a bare focus_measure. It does NOT survive a change of metric definition: the
        # raw variance scales with brightness^2 where the full-res metric divides it out.
        if len(rs.objects) >= 2 and rs.cross_cal is not None:
            rs.cross_cal.add(
                self._coarse_pair_score(frame, rs.objects[0].roi),
                self._coarse_pair_score(frame, rs.objects[1].roi),
            )

        # Normalise the primary's full-res score onto the anchor object's scale so the
        # whole climb curve (and the peak compare below) is continuous across handovers.
        raw = self._score_primary(frame)
        score = raw * rs.cumulative_scale if raw is not None else None
        if score is not None:
            self._sample(pos, score, True)  # every raw frame -> scatter graph (normalized)
            rs.lucky_scores.append(score)
        # Lucky imaging: dwell climb_lucky_frames frames at THIS position (the lens is
        # held -- no step during collection), then take the MAX as the sample. Blur
        # from turbulence/vibration only ever lowers sharpness, so the sharpest frame
        # in the burst is the least-corrupted read of focus here. lucky_left counts
        # frames (not just valid scores) so a stray None score can't stall the climb.
        rs.lucky_left -= 1
        if rs.lucky_left > 0:
            return
        agg = max(rs.lucky_scores) if rs.lucky_scores else None
        rs.lucky_scores = []
        rs.lucky_left = max(1, cfg.climb_lucky_frames)
        if agg is not None:
            # Tag the segment. cumulative_scale only ever changes at a handover, which is
            # exactly when primary_oid changes, so it is constant within a segment -- and a
            # log-slope kills any constant factor. That makes these stored values usable
            # by _fit_peak_scale_free untouched, whether or not the stitch drifted.
            rs.climb_samples.append((pos, agg, rs.primary_oid))
            rs.min_score = min(rs.min_score, agg)
            # Skip the backlash dead-zone at the start of the climb: until the encoder
            # has advanced ~one lash the optics haven't begun moving, so the score is a
            # flat blur floor + noise. Latching a "peak" there parks the lens a full lash
            # below true focus (seen live: started at good focus, climbed 20 pulses,
            # declared a noise peak 118 pulses low, then parked away from focus). Only
            # hunt the peak once the optics are actually sweeping through focus.
            if rs.climb_pulses_done >= cfg.backlash_pulses:
                if agg > rs.best_score:
                    rs.best_score = agg
                    rs.best_pos = pos
                    rs.best_oid = rs.primary_oid
                    rs.fell_count = 0
                elif rs.best_score > 1e-6 and agg < rs.best_score * (1 - cfg.peak_fall_frac):
                    rs.fell_count += 1
                else:
                    rs.fell_count = 0
                if (
                    len(rs.climb_samples) >= cfg.climb_min_samples
                    and rs.fell_count >= cfg.peak_confirm_samples
                    and rs.best_pos is not None
                ):
                    self._finish_climb(bracketed=True)
                    return
        # Safety cap: climbed the whole range without a clean peak.
        if rs.climb_pulses_done >= cfg.climb_max_pulses:
            self._finish_climb(bracketed=False)
            return
        # One small step up (open-loop). Measured BEFORE stepping so this sample's
        # score pairs with the position it was actually taken at.
        self._axis.step_pulses(+1, cfg.climb_step_pulses)
        rs.climb_pulses_done += cfg.climb_step_pulses

    def _finish_climb(self, *, bracketed: bool) -> None:
        rs = self._rs
        n = len(rs.climb_samples)
        smax = rs.best_score if rs.best_score > -1e29 else 0.0
        contrast = ((smax - rs.min_score) / smax) if smax > 1e-6 else 0.0
        # Focus-improvement metric: how much the (normalized) sharpness rose from the
        # climb's first sample to its peak. With handover the whole curve is on one
        # scale, so this spans objects -- an actual cross-object improvement figure.
        start_score = rs.climb_samples[0][1] if rs.climb_samples else 0.0
        improvement = (smax / start_score) if start_score > 1e-6 else 0.0
        log.info("af2-climb %s n=%d best_pos=%+dc best=%.2f contrast=%.2f "
                 "handovers=%d improvement=%.2fx",
                 "peak" if bracketed else "cap", n,
                 rs.best_pos if rs.best_pos is not None else 0, smax, contrast,
                 rs.handovers, improvement)
        if rs.handovers:
            self._status(
                f"autofocus v2: climbed through {rs.handovers + 1} objects "
                f"(improvement {improvement:.1f}x)"
            )
        if rs.best_pos is None or contrast < self.cfg.min_contrast:
            self._fail(
                f"no focus peak found (contrast={contrast:.2f} < {self.cfg.min_contrast})"
            )
            return
        best = int(rs.best_pos)
        # Refine the peak with a local parabola fit: the argmax is one noisy sample,
        # the vertex uses the whole shoulder so it doesn't chase a single spike.
        #
        # _fit_peak_scale_free() sits unused (with its self-checks in
        # tools/test_af_segment_peak.py) -- NOT because it was shown to be worse. It was
        # dropped from this path on a freeze-test reading that turned out to be invalid:
        # the two runs were compared as if they shared a zero, when start() re-zeros the
        # axis on every AF press, so each run's positions are measured from the previous
        # run's parked position. Rebased onto raw_count (the only cross-run frame) the
        # same session says the opposite -- scale-free landed ~6 counts-worth off the
        # frozen-scene reference and the argmax it replaced ~35. That is n=1, and it was
        # taken while the park still had the systematic ~96-count undershoot fixed above,
        # so it settles nothing either way. Deciding this needs repeated moving/static
        # pairs read the way the operator framed it: a static run started from where a
        # moving run parked should report best_pos ~= 0, and whatever it reports instead
        # IS that moving run's signed error, no rebasing required.
        if self.cfg.peak_curve_fit:
            xv = _fit_peak([(p, s) for p, s, _ in rs.climb_samples],
                           self.cfg.peak_fit_window,
                           log_domain=self.cfg.peak_fit_log)
            if xv is not None:
                fit = int(round(xv))
                log.info("af2-climb peak refine argmax=%+dc fit=%+dc", best, fit)
                best = fit
        self._gate("climb", "PARK-descend", lambda: self._begin_park(best))

    # %% park + hold %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

    def _begin_park(self, best_pos: int) -> None:
        rs = self._rs
        rs.best_pos = int(best_pos)
        # Reproduce the trajectory the CLIMB already proved lands on focus: the climb
        # started at the dive bottom and rode the lens UP to best_pos. So descend back
        # below the peak (past the lash), then creep up onto it. Both legs stay at str0
        # (never the coarse str3, which jumped ~34 counts/pulse and aliased the ~10 Hz
        # encoder), but move in COARSE park_coarse_step_pulses bursts until near the
        # peak -- AF is only ~1-2 fps, so a fine 2/frame step can't cover the deep
        # lash-clearing excursion in time. The final seat inside park_slow_band_counts
        # drops to single str0 pulses, so the lens still lands the peak alias-free.
        self._set_strength_safe(self.cfg.strength)
        # Descend far enough below the peak to fully REVERSE the gear lash before
        # creeping back up, so the final approach re-seats the lash from below (same
        # direction as the climb that found the peak). The climb started at the dive
        # bottom, but a SHORT dive/climb (e.g. the 4-pulse str3 dive) can leave
        # climb_start less than one lash below best_pos -- descending only to it then
        # doesn't take up the slack and the lens parks soft ("doesn't go far enough
        # back to clear the backlash"). So descend to whichever is DEEPER: the dive
        # bottom, or a full lash + margin below the peak.
        margin = self.cfg.park_descend_margin_counts
        lash_counts = self.cfg.backlash_pulses * self._counts_per_pulse
        by_climb = rs.climb_start_pos - margin
        by_lash = rs.best_pos - lash_counts - margin
        rs.park_descend_target = min(by_climb, by_lash)
        rs.park_phase = "descend"
        rs.park_move_since = time.monotonic()
        rs.park_since = 0.0
        rs.park_creep_last_t = 0.0
        rs.park_scores = []
        self.state = AFStateV2.PARK
        self._status(f"autofocus v2: parking at focus (centre={rs.best_pos:+d}c)")

    def _park_tick(self, frame: np.ndarray) -> None:
        rs = self._rs
        cfg = self.cfg
        now = time.monotonic()
        # Phase 1: step DOWN to the target depth in coarse str0 bursts
        # (park_coarse_step_pulses/frame) until the encoder reaches it. Own time budget
        # so a slow descend can't starve the creep.
        if rs.park_phase == "descend":
            pos = float(self._axis.position)
            reached = pos <= rs.park_descend_target + self._land_tolerance
            descend_timed_out = (now - rs.park_move_since) >= cfg.park_descend_max_s
            if reached or descend_timed_out:
                log.info("af2-park descended pos=%+dc (target %+dc), creeping up to %+dc%s",
                         int(round(pos)), int(round(rs.park_descend_target)),
                         int(round(rs.best_pos)),
                         "" if reached else " (DESCEND-TIMEOUT)")

                def _enter_creep() -> None:
                    rs.park_phase = "creep"
                    rs.park_move_since = time.monotonic()  # creep gets its own budget

                self._gate(f"park-descend (pos={int(round(pos)):+d}c)", "PARK-creep",
                           _enter_creep)
                return
            self._axis.step_pulses(-1, cfg.park_coarse_step_pulses)  # coarse: just get below
            return
        timed_out = (now - rs.park_move_since) >= cfg.park_max_wait_s
        # Phase 2: creep UP onto best_pos at str0 -- coarse park_coarse_step_pulses
        # bursts until near the peak, then single pulses inside the slow band so the lens
        # seats gently instead of overshooting. Stop when the encoder reaches the peak.
        if rs.park_phase == "creep":
            pos = float(self._axis.position)
            # Stop at the CLOSEST reachable point to best_pos, not the first point
            # that is merely "close enough". The creep only ever moves UP, so any
            # tolerance subtracted here is a pure one-directional bias: the old
            # `pos >= best - park_tolerance_pulses` (4 pulses = 96 counts) exited the
            # instant it crossed the BOTTOM edge of the band and never closed the gap,
            # so all 14 logged runs parked 2-4 pulses BELOW target, every time, never
            # above. Half a pulse is the honest tolerance -- it's the finest step the
            # hardware has, so stopping within it means the next step would overshoot
            # by more than staying put undershoots. Residual is now centred on 0.
            if pos >= rs.best_pos - self._land_tolerance or timed_out:
                log.info("af2-park arrived pos=%+dc target=%+dc miss=%+dc after=%.1fs%s",
                         int(round(pos)), int(round(rs.best_pos)),
                         int(round(pos - rs.best_pos)), now - rs.park_move_since,
                         "" if not timed_out else " (TIMEOUT)")

                def _enter_settle() -> None:
                    rs.park_phase = "settle"
                    now2 = time.monotonic()
                    rs.park_since = now2
                    rs.park_settle_since = now2  # hard-cap clock (jitter never resets it)
                    rs.park_last_pos = float(pos)  # seed so the first tick's diff is ~0

                self._gate(
                    f"park-creep arrived (pos={int(round(pos)):+d}c "
                    f"target={int(round(rs.best_pos)):+d}c)",
                    "PARK-settle+measure", _enter_settle)
                return
            remaining = rs.best_pos - pos
            # Three-speed approach: coarse bulk travel far out, decelerate to the
            # CLIMB's own pace as we near the peak (so the approach mirrors the climb
            # that located focus), then single pulses for the gentle, alias-free seat.
            if remaining <= cfg.park_slow_band_counts:
                step = 1
            elif remaining <= cfg.park_climb_band_counts:
                step = cfg.climb_step_pulses
            else:
                step = cfg.park_coarse_step_pulses
            self._axis.step_pulses(+1, step)
            rs.park_creep_last_t = now
            return
        # Phase 3: measure only once the shaft has actually STOPPED (the last creep
        # step's pulses are still draining right after arrival -> the '-19 then -13'
        # drift) and then sat still for the settle dwell.
        pos = float(self._axis.position)
        moved = abs(pos - rs.park_last_pos)
        rs.park_last_pos = pos
        # Reuse the score _emit_sample already computed this frame -- re-scoring here
        # would hit the consume-once full-res latch and always return None (the old
        # 'parked 0% of peak' phantom). Accumulate every settle frame so the ratio is
        # real even when the dwell never latches and we exit via the cap.
        if rs.last_score is not None:
            rs.park_scores.append(rs.last_score)
        # Hard cap: the dwell gate below can never latch on a live rig (encoder SPI
        # jitter under the per-frame full-res crop load keeps `moved` above the
        # stability band), so past park_settle_max_s we stop waiting and hold at the
        # climb's best_pos regardless -- that's the position we're already parked on.
        settle_capped = (now - rs.park_settle_since) >= cfg.park_settle_max_s
        if not settle_capped:
            if moved > cfg.park_stable_counts:
                rs.park_since = now  # still moving; restart the dwell
                return
            if (now - rs.park_since) < cfg.park_settle_s:
                return
            if len(rs.park_scores) < max(1, self.cfg.settle_frames):
                return
        parked = float(np.median(rs.park_scores)) if rs.park_scores else 0.0
        if settle_capped:
            log.info("af2-park settle capped after %.1fs (n=%d scores)",
                     now - rs.park_settle_since, len(rs.park_scores))
        pos = int(round(self._axis.position))
        raw = getattr(self._axis, "raw_count", None)
        ratio = (parked / rs.best_score) if rs.best_score > 1e-6 else 0.0
        # parked-vs-peak only means something if BOTH were measured on the same patch.
        # _advance_objects keeps running through PARK, and the park (descend + creep +
        # settle: 4-11 s live) routinely outlasts one object (~3.2 s at 400 mm), so on a
        # moving scene the parked score is usually a DIFFERENT patch -- and scaled by a
        # cumulative_scale calibrated for yet another one, since handovers only fold in
        # during CLIMB. Reporting that as "parked but soft" invents a focus problem out of
        # a content difference; the ratios 0.36 / 0.52 / 0.04 seen live were mostly this.
        comparable = (
            rs.best_oid is not None
            and bool(rs.objects)
            and rs.objects[0].oid == rs.best_oid
        )
        # `raw` is the only number here comparable ACROSS runs: pos is relative to this
        # run's start (the axis re-zeros on every AF press), so two runs' pos values
        # share no frame. Logging both is what lets a moving run be scored against a
        # frozen-scene reference without reconstructing the chain from the dive lines.
        log.info("af2-park pos=%+dc raw=%s parked=%.2f peak=%.2f ratio=%.2f%s",
                 pos, "n/a" if raw is None else int(round(raw)),
                 parked, rs.best_score, ratio,
                 "" if comparable else
                 f" (peak was oid={rs.best_oid}, parked on "
                 f"oid={rs.objects[0].oid if rs.objects else None} -> NOT comparable)")
        self._sample(pos, parked, True)
        if comparable and ratio < (1.0 - 2.0 * self.cfg.peak_fall_frac):
            # Parked notably softer than the measured peak -- surface it, but still
            # hold here (best available); a repeat AF can re-seek.
            self._status(
                f"autofocus v2: parked but soft (parked {ratio:.0%} of peak) "
                f"@ {pos:+d}"
            )
        self._settle_and_hold(rs.best_pos)

    def _settle_and_hold(self, best_pos: int) -> None:
        rs = self._rs
        rs.best_pos = int(best_pos)
        if not rs.continuous:
            self.state = AFStateV2.DONE
            self._status(f"autofocus v2: done (in focus, centre={best_pos:+d}c)")
            self._finished(True, self._report(ok=True))
            return
        # Flight/continuous: hold at focus. The atmospheric lens is static for
        # ~hours, so we sit still (no dithering) and optionally re-verify slowly.
        self.state = AFStateV2.HOLD
        rs.hold_since = time.monotonic()
        self._status(f"autofocus v2: in focus, holding (centre={best_pos:+d}c)")

    def _hold_tick(self, frame: np.ndarray) -> None:
        rs = self._rs
        interval = self.cfg.reverify_interval_s
        if interval > 0.0 and (time.monotonic() - rs.hold_since) >= interval:
            self._status("autofocus v2: re-verifying focus")
            self._begin_dive()

    # %% scoring %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

    def _coarse_pair_score(self, frame, roi: Roi) -> float:
        """Coarse score for one half of a cross-cal pair, in the SAME units the climb
        curve is measured in -- a ratio is only transferable between identical metrics.
        Mirrors the branch in :meth:`_score_primary`: mean^2-normalised when the curve
        comes from ``focus_measure_full_res``, raw variance when we fall back to scoring
        the coarse frame (sim / encoderless bench), where brightness is part of the
        metric and dividing it out would introduce the mismatch in the other direction."""
        if self._full_res_score is not None:
            return float(focus_measure_normalized(frame, roi))
        return float(focus_measure(frame, roi))

    def _score_primary(self, frame) -> Optional[float]:
        """Sharpness of the tracked target patch, ALWAYS on the sensor-native full-res
        crop when that path is wired (every real M4/M5 run). The 4x-decimated coarse
        frame aliases away the high frequencies focus lives in, so scoring it peaks in
        the wrong place and walks a good focus into blur. When the full-res crop is
        momentarily stale the callback returns None and we return None too: the caller
        skips that sample and waits for a fresh crop rather than substituting a blurry
        coarse score. Only when NO full-res path is wired at all (sim / encoderless
        bench) do we score the frame we were handed."""
        rs = self._rs
        if not rs.objects:
            return None
        roi = rs.objects[0].roi
        if self._full_res_score is not None:
            try:
                return self._full_res_score(roi)  # sensor-native; None -> skip, never guess
            except Exception:  # pragma: no cover - UI guard
                log.exception("full_res_score failed")
                return None
        return float(focus_measure(frame, roi))

    def _frame_is_featureless(self, frame: np.ndarray) -> bool:
        fh, fw = frame.shape[:2]
        g = frame[:: max(1, fh // 64), :: max(1, fw // 64)]
        if g.dtype == np.uint16:
            g = (g >> 6).astype(np.float32)
        else:
            g = g.astype(np.float32)
        return float(np.std(g)) < self.cfg.min_texture_score

    # %% completion %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

    def _set_strength_safe(self, level: int) -> None:
        if self._set_strength is None:
            return
        try:
            self._set_strength(int(level))
        except Exception:  # pragma: no cover - actuator guard
            log.exception("set_strength failed")

    def _report(self, *, ok: bool, stopped: bool = False, reason: str = "") -> dict:
        rs = self._rs
        r = {
            "ok": ok,
            "center": rs.best_pos if rs.best_pos is not None else 0,
            "total_pulses": self._axis.total_pulses,
            "frames": rs.frames,
            "algorithm": "sweep_and_hold",
        }
        if stopped:
            r["stopped"] = True
        if reason:
            r["reason"] = reason
        return r

    def _fail(self, reason: str) -> None:
        self.state = AFStateV2.FAILED
        self._status(f"autofocus v2: FAILED ({reason})")
        self._finished(False, self._report(ok=False, reason=reason))
