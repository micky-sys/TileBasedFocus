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
    leading_edge_from_motion,
    select_leading_edge_roi,
)
from .focus_axis import FocusAxis, OpenLoopAxis

log = logging.getLogger(__name__)

Roi = tuple[int, int, int, int]  # (x0, y0, x1, y1)


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
    dive_min_drop_pulses: int = 20
    """The dive must move the position DOWN by at least this many pulse-units total,
    else we fail ('motor stuck') rather than climbing from nowhere."""

    # %% Search: climb (fine, one sample per frame) %%%%%%%%%%%%%%%%%%%%%%
    strength: int = 0
    """Focus strength for the fine climb (str0 = finest 3 ms pulse). Named
    ``strength`` for parity with the legacy config / app wiring."""
    climb_step_pulses: int = 2
    """Upward step (str0 pulses) taken once per frame during the climb. Small = finer
    peak localisation but a longer climb. ~1 str0 pulse == 1 position unit."""
    climb_settle_frames: int = 1
    """Frames to take after each climb step, scoring ONLY the last one. 1 = score
    every frame (fastest). >1 lets the shaft + the ~10 Hz encoder settle before the
    (pos, score) sample so the climb reads an un-lagged position -- slower, but removes
    the motion-lag offset that leaves the park a few counts below the true peak."""
    climb_max_pulses: int = 400
    """Safety cap on total upward climb (pulse-units) before giving up if no peak is
    bracketed -- must exceed the dive depth plus the worst start-to-focus gap."""
    peak_fall_frac: float = 0.15
    """The climb stops once the score has fallen this fraction below its best AND
    stayed there for ``peak_confirm_samples`` frames -- i.e. we've climbed through
    focus."""
    peak_confirm_samples: int = 3
    """Consecutive post-peak frames below best*(1-peak_fall_frac) needed to confirm
    the peak (rejects a single-frame noise dip)."""
    climb_min_samples: int = 5
    """Minimum samples before a peak may be declared (need a rising shoulder first)."""

    # %% Park + hold %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    park_tolerance_pulses: int = 4
    """The park move (down-by-lash then up-by-lash, ~100+ discrete str0 pulses on this
    rig) is only DONE once the encoder is within this many pulse-units of ``best_pos``.
    Measuring on a fixed dwell instead landed the lens mid-move, below focus."""
    park_stable_pulses: int = 2
    """...AND the encoder has stopped moving (frame-to-frame change <= this). Being
    'within tolerance' alone latched too early: park_upward passes DOWN through
    best_pos on its way to (best_pos - lash), so we'd measure mid-move. Requiring the
    shaft to be at rest waits out the full down-then-up pulse train."""
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
    park_creep_step_pulses: int = 3
    """The final approach onto the peak is a SLOW creep from below in steps this size
    (str0 pulses), paced one per ``park_creep_interval_s`` so the shaft coasts to rest
    between them and can't overshoot the peak (operator asked for a gentle landing)."""
    park_creep_interval_s: float = 0.2
    """Wall-clock gap between creep steps -- long enough for the discrete pulse to run
    and the shaft to settle before the next, so no momentum accumulates."""
    park_slow_band_pulses: int = 12
    """Within this many pulse-units of best_pos the creep drops to single-pulse steps so
    the lens is seated onto the peak gently (operator saw the final move overshoot when
    it arrived fast)."""
    park_climb_band_pulses: int = 36
    """Between park_slow_band_pulses and this, the creep slows from the coarse bulk step
    to the CLIMB's own pace (climb_step_pulses/frame) -- so the final approach onto the
    peak mirrors the climb that found it, decelerating well before the seat instead of
    blasting in at the coarse step and stopping abruptly. Must exceed
    park_slow_band_pulses."""
    park_coarse_step_pulses: int = 6
    """str0 pulses/frame for the DESCEND and the far-from-peak part of the creep. AF is
    frame-gated at ~1-2 fps, so the fine climb step (2/frame) can't traverse the deep
    lash-clearing excursion within the time budget -- it timed out short and left the
    lash un-reversed. Precision doesn't matter out here (we're below focus, not
    measuring); the gentle single-pulse seat inside park_slow_band_pulses still lands
    the peak without aliasing, so this only speeds the bulk travel."""
    park_descend_margin_pulses: int = 20
    """The park first descends to best_pos - lash - THIS, and waits until the encoder
    actually REACHES that depth, so the gear lash is fully reversed (the lens really
    moves below focus) before the creep back up. Descending only ~to the lash -- or
    stopping short on a stability guess -- left the slack un-reversed, so the lens
    stayed at the climb's overshoot (past focus) and parked soft."""
    park_settle_s: float = 1.2
    """Wall-clock dwell AFTER the encoder has arrived at best_pos (lets the shaft ring
    down) before the parked-score sanity check + hold."""
    settle_frames: int = 3
    """Frames averaged for the parked-focus sanity measurement."""
    backlash_pulses: int = 3
    """Gear/spur lash take-up (pulse-units) for the upward park. Must be >= the
    mechanism's lash band so ``park_upward`` reliably seats focus from below."""
    encoder_counts_per_pulse: float = 1.0
    """Only used when the closed-loop EncoderAxis is active: AMT203S motor counts per
    focus pulse, used to express the measured position in the pulse-sized units the
    rest of this config assumes. Its SIGN encodes polarity -- make it NEGATIVE when a
    '+' focus pulse drives the encoder DOWN, so "drive '+'" maps to "position
    increases" (a wrong sign makes the axis walk off instead of tracking focus).
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
    # dive
    dive_start_pos: float = 0.0
    dive_bursts_left: int = 0
    dive_last_t: float = 0.0
    # climb
    climb_samples: list = field(default_factory=list)  # [(pos:int, score:float)]
    climb_pulses_done: int = 0
    best_score: float = -1e30
    min_score: float = 1e30
    best_pos: Optional[int] = None
    fell_count: int = 0
    # climb
    climb_start_pos: float = 0.0
    climb_wait: int = 0  # frames left to settle after a step before scoring
    # park
    park_phase: str = "descend"   # descend -> creep -> settle
    park_descend_target: float = 0.0
    park_since: float = 0.0
    park_move_since: float = 0.0
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
            self._status(f"autofocus v2: stopped (holding centre={rs.best_pos:+d})")
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
        self._status(f"PAUSED after {done_label} @ pos={pos:+d}{rawtxt}"
                     f" -- press SPACE for {next_label}")
        log.info("af2-gate paused after %s pos=%+d%s -- awaiting SPACE",
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
        if not self._rs.objects:
            return
        pos = int(round(self._axis.position))
        score = self._score_primary(frame)
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

    # %% dive (fast, below focus) %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

    def _begin_dive(self) -> None:
        rs = self._rs
        self._set_strength_safe(self.cfg.dive_strength)
        rs.dive_start_pos = float(self._axis.position)
        rs.dive_bursts_left = max(1, self.cfg.dive_bursts)
        rs.dive_last_t = 0.0  # 0 => first burst fires on the next tick
        self.state = AFStateV2.DIVE
        self._status("autofocus v2: diving below focus")
        log.info("af2-dive begin start=%+d bursts=%d x%d pulses",
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
            log.info("af2-dive done pos=%+d dropped=%+d raw=%s", int(round(pos)),
                     int(round(dropped)),
                     "n/a" if raw is None else int(round(raw)))
            if dropped < self.cfg.dive_min_drop_pulses:
                self._fail(
                    f"dive moved down only {int(round(dropped))} units "
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
        log.info("af2-dive burst left=%d pos=%+d raw=%s", rs.dive_bursts_left,
                 int(round(pos)), "n/a" if raw is None else int(round(raw)))

    # %% climb (fine, one sample per frame) %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

    def _begin_climb(self) -> None:
        rs = self._rs
        self._set_strength_safe(self.cfg.strength)
        rs.climb_samples = []
        rs.climb_pulses_done = 0
        rs.best_score = -1e30
        rs.min_score = 1e30
        rs.best_pos = None
        rs.fell_count = 0
        rs.climb_start_pos = float(self._axis.position)
        self.state = AFStateV2.CLIMB
        self._status("autofocus v2: climbing to focus")
        log.info("af2-climb begin from pos=%+d step=%d",
                 int(round(rs.climb_start_pos)), self.cfg.climb_step_pulses)

    def _climb_tick(self, frame: np.ndarray) -> None:
        rs = self._rs
        cfg = self.cfg
        pos = int(round(self._axis.position))
        score = self._score_primary(frame)
        if score is not None:
            rs.climb_samples.append((pos, score))
            rs.min_score = min(rs.min_score, score)
            self._sample(pos, score, True)  # full-res / fine metric -> graphs
            if score > rs.best_score:
                rs.best_score = score
                rs.best_pos = pos
                rs.fell_count = 0
            elif rs.best_score > 1e-6 and score < rs.best_score * (1 - cfg.peak_fall_frac):
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
        # One small step up (open-loop). Measure BEFORE stepping so this frame's
        # score pairs with the position it was actually taken at.
        self._axis.step_pulses(+1, cfg.climb_step_pulses)
        rs.climb_pulses_done += cfg.climb_step_pulses
        rs.climb_wait = max(0, cfg.climb_settle_frames - 1)  # settle before next sample

    def _finish_climb(self, *, bracketed: bool) -> None:
        rs = self._rs
        n = len(rs.climb_samples)
        smax = rs.best_score if rs.best_score > -1e29 else 0.0
        contrast = ((smax - rs.min_score) / smax) if smax > 1e-6 else 0.0
        log.info("af2-climb %s n=%d best_pos=%+d best=%.2f contrast=%.2f",
                 "peak" if bracketed else "cap", n,
                 rs.best_pos if rs.best_pos is not None else 0, smax, contrast)
        if rs.best_pos is None or contrast < self.cfg.min_contrast:
            self._fail(
                f"no focus peak found (contrast={contrast:.2f} < {self.cfg.min_contrast})"
            )
            return
        best = int(rs.best_pos)
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
        # lash-clearing excursion in time. The final seat inside park_slow_band_pulses
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
        margin = self.cfg.park_descend_margin_pulses
        by_climb = int(round(rs.climb_start_pos)) - margin
        by_lash = int(rs.best_pos) - self.cfg.backlash_pulses - margin
        rs.park_descend_target = min(by_climb, by_lash)
        rs.park_phase = "descend"
        rs.park_move_since = time.monotonic()
        rs.park_since = 0.0
        rs.park_creep_last_t = 0.0
        rs.park_scores = []
        self.state = AFStateV2.PARK
        self._status(f"autofocus v2: parking at focus (centre={rs.best_pos:+d})")

    def _park_tick(self, frame: np.ndarray) -> None:
        rs = self._rs
        cfg = self.cfg
        now = time.monotonic()
        # Phase 1: step DOWN to the target depth in coarse str0 bursts
        # (park_coarse_step_pulses/frame) until the encoder reaches it. Own time budget
        # so a slow descend can't starve the creep.
        if rs.park_phase == "descend":
            pos = int(round(self._axis.position))
            reached = pos <= int(round(rs.park_descend_target)) + cfg.park_tolerance_pulses
            descend_timed_out = (now - rs.park_move_since) >= cfg.park_descend_max_s
            if reached or descend_timed_out:
                log.info("af2-park descended pos=%+d (target %+d), creeping up to %+d%s",
                         pos, int(round(rs.park_descend_target)), int(rs.best_pos),
                         "" if reached else " (DESCEND-TIMEOUT)")

                def _enter_creep() -> None:
                    rs.park_phase = "creep"
                    rs.park_move_since = time.monotonic()  # creep gets its own budget

                self._gate(f"park-descend (pos={pos:+d})", "PARK-creep", _enter_creep)
                return
            self._axis.step_pulses(-1, cfg.park_coarse_step_pulses)  # coarse: just get below
            return
        timed_out = (now - rs.park_move_since) >= cfg.park_max_wait_s
        # Phase 2: creep UP onto best_pos at str0 -- coarse park_coarse_step_pulses
        # bursts until near the peak, then single pulses inside the slow band so the lens
        # seats gently instead of overshooting. Stop when the encoder reaches the peak.
        if rs.park_phase == "creep":
            pos = int(round(self._axis.position))
            if pos >= int(rs.best_pos) - cfg.park_tolerance_pulses or timed_out:
                log.info("af2-park arrived pos=%+d target=%+d after=%.1fs%s",
                         pos, int(rs.best_pos), now - rs.park_move_since,
                         "" if not timed_out else " (TIMEOUT)")

                def _enter_settle() -> None:
                    rs.park_phase = "settle"
                    rs.park_since = time.monotonic()

                self._gate(f"park-creep arrived (pos={pos:+d} target={rs.best_pos:+d})",
                           "PARK-settle+measure", _enter_settle)
                return
            remaining = int(rs.best_pos) - pos
            # Three-speed approach: coarse bulk travel far out, decelerate to the
            # CLIMB's own pace as we near the peak (so the approach mirrors the climb
            # that located focus), then single pulses for the gentle, alias-free seat.
            if remaining <= cfg.park_slow_band_pulses:
                step = 1
            elif remaining <= cfg.park_climb_band_pulses:
                step = cfg.climb_step_pulses
            else:
                step = cfg.park_coarse_step_pulses
            self._axis.step_pulses(+1, step)
            rs.park_creep_last_t = now
            return
        # Phase 3: measure only once the shaft has actually STOPPED (the last creep
        # step's pulses are still draining right after arrival -> the '-19 then -13'
        # drift) and then sat still for the settle dwell.
        pos = int(round(self._axis.position))
        moved = abs(pos - rs.park_last_pos)
        rs.park_last_pos = pos
        if moved > cfg.park_stable_pulses:
            rs.park_since = now  # still moving; restart the dwell
            return
        if (now - rs.park_since) < cfg.park_settle_s:
            return
        score = self._score_primary(frame)
        if score is not None:
            rs.park_scores.append(score)
        if len(rs.park_scores) < max(1, self.cfg.settle_frames):
            return
        parked = float(np.median(rs.park_scores))
        pos = int(round(self._axis.position))
        ratio = (parked / rs.best_score) if rs.best_score > 1e-6 else 0.0
        log.info("af2-park pos=%+d parked=%.2f peak=%.2f ratio=%.2f",
                 pos, parked, rs.best_score, ratio)
        self._sample(pos, parked, True)
        if ratio < (1.0 - 2.0 * self.cfg.peak_fall_frac):
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
            self._status(f"autofocus v2: done (in focus, centre={best_pos:+d})")
            self._finished(True, self._report(ok=True))
            return
        # Flight/continuous: hold at focus. The atmospheric lens is static for
        # ~hours, so we sit still (no dithering) and optionally re-verify slowly.
        self.state = AFStateV2.HOLD
        rs.hold_since = time.monotonic()
        self._status(f"autofocus v2: in focus, holding (centre={best_pos:+d})")

    def _hold_tick(self, frame: np.ndarray) -> None:
        rs = self._rs
        interval = self.cfg.reverify_interval_s
        if interval > 0.0 and (time.monotonic() - rs.hold_since) >= interval:
            self._status("autofocus v2: re-verifying focus")
            self._begin_dive()

    # %% scoring %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

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
