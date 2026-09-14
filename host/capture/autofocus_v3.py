"""AF3: focus by the SHAPE of the across-field score profile, not by one patch's score.

Why another engine
------------------
AF2 climbs by watching a single tracked patch's score rise and fall. That score moves
for two reasons at once -- focus changed, or the patch moved to a part of the field
that focuses differently -- and 2026-09-08 showed the second effect is worth 2.9x,
which is far larger than the peak it is trying to resolve.

The measurement that separates them came out of the defocus test that day. Take one
object and follow it right across the frame at a FIXED lens position, then compare its
score at frame centre against its own score at the two edges:

    D = ln(score at centre) - mean(ln(score at each edge))

Because both terms come from the SAME patch of ground, content cancels exactly -- no
cross-object calibration, no scale stitch, nothing to bias. And D turns out to be
single-peaked in lens position (field map, 131 positions x 5 tiles):

    raw -1087  D = -1.14      centre far worse than the edges  (bowl)
    raw  +867  D =  0         crossing
    raw +1290  D = +0.29      DOME -- deepest here
    raw +1713  D =  0         crossing
    raw +6254  D = -1.01      bowl again

The mechanism is that off-axis aberration broadens the focus hill and lifts its
pedestal: across that sweep the centre tile swings 45.6x from its own peak while the
left edge swings only 11.6x. So the centre can only out-score the edges while it sits
near its own vertex, which is to say near focus. Aberrations only "matter" near focus.

What that buys is a focus observable that is a RATIO WITHIN ONE FRAME. Exposure, gain,
scene brightness and ground content all cancel, which removes the single thing that
makes patch-score hill climbing fragile.

D's peak is BIASED HIGH -- read this before trusting a park
-----------------------------------------------------------
D is maximised where the centre scores well AND the edges score badly. Past the
centre's own best focus the edges are still getting worse faster than the centre is,
so D keeps climbing for a while after the centre has peaked. The bias is real and
visible in the same field map: the centre tile's vertex is at raw +1082 but D peaks at
+1290, so D lands ~208 counts high. The synthetic check below shows the same sign.

So D's vertex is a REPEATABLE but OFFSET estimate of best focus. That is fine -- a
fixed offset is trivially removable -- but it has to be measured, which is what
``park_bias_counts`` is for. It defaults to 0, i.e. no correction, so a first run
reports where D actually peaks rather than a guess.

Each traverse therefore also yields ``u*``, the field position of the profile's own
maximum, logged alongside D. It costs one extra parabola fit and it is the unbiased
estimator in waiting: on a curved field the sharpest field position walks inward as
focus is approached and PINS at frame centre exactly when the lens reaches the
centre's vertex. Recording it now means the bias-free rule can be evaluated offline
against the same runs, with no extra rig time.

Shape of a run
--------------
Same skeleton as AF2 -- dive below focus fast, walk up, park -- so DIVE, PARK, the
backlash take-up, the tracker, the scoring metric and the parabola vertex fit are all
inherited unchanged. Only what the walk MEASURES is different:

    dive (4 x str3, ~3400c below)      inherited from AF2
    then, per STATION:
        hold the lens dead still
        let 4-5 objects traverse the whole frame, scoring every one every frame
        each object that completes a traverse yields one D (and one u*)
        station D = median of those
        step up station_step_pulses and repeat
    fit a parabola through (position, D) over all stations, park at its vertex

Two design notes worth keeping
------------------------------
*Stations use N x str0, not str1.* The operator's sketch said "pulse str1 once", but
str1 has never been calibrated -- only str0 (~24 counts/pulse) and str3 (~850) are
measured. Six str0 pulses is ~144 counts and needs no new calibration, and the axis
moves closed-loop anyway, so any spacing is available for free. ponytail: if str1 is
ever calibrated, ``station_step_pulses`` + a ``station_strength`` swap in directly.

*The sweep stops early only once it is ARMED.* A bare "D fell" is worthless as a stop:
D is even -- negative on both flanks -- so a dip cannot distinguish "past the peak"
from "not there yet". Run B (2026-09-09) shows exactly that failure mode in the data:
on the pedestal D wandered -0.318, -0.326, -0.420, -0.374, three straight stations
below a running max, 1300 counts short of focus.

But that run also showed the opposite failure, which is worse. Its sweep ended on the
BUDGET, not on a peak: the raw centre score was still at its maximum on the last
station, so the parabola was fitted to data with no far flank and the vertex was an
extrapolation. A one-sided bracket cannot be told apart from a correct one, which
makes every result in every condition unfalsifiable.

So the stop needs both flanks, and it gets them by arming on the RISE before believing
any fall (``_past_the_peak``): D must first have climbed ``station_arm_rise`` out of
its own pedestal -- which per the field map only happens near focus -- and only then
do stations sitting ``station_fall_margin`` below the running max start counting. On
run B's numbers that arms at raw 3716 and never false-triggers through the whole climb,
including the -0.087/-0.089/-0.164 wobble that a naive counter would have fired on.
``sweep_counts`` stays as the backstop for the case where D never rises enough to arm.

Cost: a station needs an object to actually cross the frame, so it is ~15-20 s (run A
managed 14 complete traverses in 52 s; run B needed 22.3 s for 4). The budget is now
~38 stations, but the early exit is expected to end it 3 stations past the peak.
"""

from __future__ import annotations

import logging
import math
import statistics
import time
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from .autofocus_v2 import (
    AFStateV2,
    AutoFocusConfigV2,
    AutoFocusEngineV2,
    _fit_peak,
)

log = logging.getLogger(__name__)


@dataclass
class AutoFocusConfigV3(AutoFocusConfigV2):
    """AF2's config plus the station walk. Inherits every dive/park/tracker knob."""

    station_step_pulses: int = 6
    """str0 pulses between stations. On the rig one str0 pulse is ~24 counts, so 6 is
    ~144 counts. The positive-D window measured 2026-09-08 was only ~846 counts wide
    (crossings at raw +867 and +1713), so this puts ~6 stations inside it -- enough for
    the parabola fit to have something to bite on."""

    station_objects: int = 5
    """Patches alive at once. More objects means more completed traverses per unit
    time, but each costs ~43 ms of full-res scoring, so 5 is ~215 ms/frame."""

    handover_stagger_frames: int = 1
    """AF2 spreads births 5 frames apart so a handover pair does not enter and leave
    the FOV together, leaving no overlap to cross-calibrate on. AF3 never hands over --
    every D lives inside one object by construction -- so the stagger buys nothing and
    costs the station four gaps of 5 frames before its last traverse can even start. 1
    is the floor, not a preference: the acquire path adds at most one patch per frame.
    On the 143034 run this was ~20 of the 29 frames a station took."""

    station_min_traverses: int = 4
    """Completed full-frame traverses to bank before the station's D is called good.
    The median of 4 is what makes a single mangled patch harmless."""

    station_max_seconds: float = 45.0
    """Give up waiting for ``station_min_traverses`` and use whatever banked. Guards
    against a station stalling forever on featureless ground."""

    sweep_counts: float = 5600.0
    """BACKSTOP for the sweep, not the plan -- ``_past_the_peak`` normally ends it
    first. Counted in NOMINAL counts (``station_step_pulses`` x
    ``encoder_counts_per_pulse``, i.e. 144), while the axis really moves ~180, so the
    travel this buys is ~1.25x the number: 5600 nominal is ~7000 real counts over ~38
    stations. Sized so the backstop cannot cut a bracket short -- run B's 4200 (5395
    real) ended one station BEFORE D turned over, which is the whole reason the early
    exit exists. ponytail: the 24-vs-30 counts/pulse gap is left alone here because
    ``encoder_counts_per_pulse`` also sets AF2's ``backlash_pulses``; fix it there and
    this number comes down to ~7000."""

    station_arm_rise: float = 0.35
    """How far D must climb out of its own pedestal before any fall is believed.
    Chosen just above the worst per-station spread seen on run B (D_iqr 0.28): below
    that, a dip is noise on the flat part of the curve, not the far flank."""

    station_fall_margin: float = 0.08
    """A station only counts as "below the max" by at least this much. Run B's
    station-to-station rise near the peak was ~0.12, so this passes real descent while
    rejecting the +/-0.03 jitter that made 0.476 -> 0.448 look like a fall."""

    station_descend_stop: int = 3
    """Stations past the argmax that must sit clearly below it before the sweep is
    declared bracketed. Three is what makes the far flank a flank rather than one
    unlucky median."""

    park_bias_counts: float = 0.0
    """Subtracted from D's fitted vertex before parking, because D peaks ABOVE the
    centre's true best focus (see the module docstring -- the field map put the gap at
    ~208 counts). Left at 0 deliberately: the first runs should report where D really
    peaks, and this gets set once from the measured gap rather than assumed."""

    profile_centre_frac: float = 0.12
    """Half-width of the "centre" band, as a fraction of the frame along the travel
    axis. 0.12 of 3200 px is +/-384 px, which at 136 px/frame is ~5 samples."""

    profile_edge_frac: float = 0.25
    """The outer quarter at each end is the "edge". Matches the field map's left/right
    tiles, whose centres sat at 0.17 and 0.87 of frame width."""

    profile_min_span_frac: float = 0.55
    """Reject a trail that did not cross at least this much of the frame. A partial
    arc has no edge-to-centre contrast to measure, and every station's first few
    objects are mid-traverse when the lens steps."""

    station_fit_window: int = 2
    """Stations either side of the argmax included in the parabola fit, so 5 points."""

    acquire_lane_frac: float = 0.34
    """ON for AF3, unlike the flight default of 0 (off). D cancels CONTENT because
    both its terms come from one patch, but it does not cancel LANE: a patch crossing
    down the left of the frame and one crossing down the middle sample different
    slices of a curved field, so their Ds are two different measurements being
    medianed together. Run B's own objects were born anywhere from y_centre 1190 to
    1478. A third of the candidate spread leaves roughly one tile column, which is the
    narrowest lane that still finds texture to acquire on."""

    # NOTE on handover: AF3 has no use for it -- every D is computed within ONE object
    # by construction, which is the entire point -- and nothing on its path reads
    # ``enable_handover`` (``_want_objects`` is overridden and ``_handle_handover`` is
    # never called). So it is left inherited rather than pinned here.


def profile_metric(trail: list, frame_w: int, frame_h: int,
                   cfg: AutoFocusConfigV3) -> Optional[tuple]:
    """One object's whole traverse -> ``(D, u_star)``.

    ``D`` is how much better it scored at frame centre than at its own two edge
    positions, in log units. ``u_star`` is where along the travel axis its profile
    actually peaked, as a fraction of the frame (0.5 = dead centre), or ``None`` if
    the profile has no interior hump to fit.

    ``trail`` is ``[(cx, cy, score), ...]`` in frame order. Returns ``None`` when the
    traverse is too short or misses a band, because a half-crossing measures a
    different thing and quietly averaging it in would flatten the very signal we want.

    The travel axis is taken from the trail itself rather than assumed, so this works
    for OrthoSim ``--dir left/right`` (x) and ``up/down`` (y) with no switch.
    """
    if len(trail) < 6:
        return None
    xs = [t[0] for t in trail]
    ys = [t[1] for t in trail]
    span_x, span_y = max(xs) - min(xs), max(ys) - min(ys)
    if span_x >= span_y:
        axis, extent, span = xs, float(frame_w), span_x
    else:
        axis, extent, span = ys, float(frame_h), span_y
    if extent <= 0 or span < cfg.profile_min_span_frac * extent:
        return None

    sc = [t[2] for t in trail]
    mid = 0.5 * extent
    centre = [s for a, s in zip(axis, sc) if abs(a - mid) <= cfg.profile_centre_frac * extent]
    near = [s for a, s in zip(axis, sc) if a <= cfg.profile_edge_frac * extent]
    far = [s for a, s in zip(axis, sc) if a >= (1.0 - cfg.profile_edge_frac) * extent]
    if not (centre and near and far):
        return None

    def lmed(vals: list) -> float:
        return math.log(max(statistics.median(vals), 1e-9))

    d = lmed(centre) - 0.5 * (lmed(near) + lmed(far))

    # Where the profile peaked in the FIELD. Same local-parabola fit the climb uses, in
    # the log domain because these are sharpness scores (multiplicative noise).
    uv = _fit_peak(list(zip(axis, sc)), window=max(2, len(trail) // 4), log_domain=True)
    return d, (None if uv is None else uv / extent)


@dataclass
class _Station:
    pos: int
    t0: float
    d: Optional[float] = None
    u: Optional[float] = None


class AutoFocusEngineV3(AutoFocusEngineV2):
    """AF2 with the climb's per-step observable swapped for the field profile.

    Everything not listed as an override below is AF2's, on purpose: a
    reimplementation would be measuring a different instrument than the one the
    2026-09-08 data characterised.
    """

    def __init__(self, *, profile_cb: Optional[Callable[[dict], None]] = None,
                 **kw) -> None:
        # An explicit config=None must still land on a V3 config, not V2's: the
        # station walk reads station_* attributes that a V2 config does not have, and
        # the controller passes config=None whenever the app handed it the wrong type.
        if kw.get("config") is None:
            kw["config"] = AutoFocusConfigV3()
        super().__init__(**kw)
        # Live preview of the station in progress (one polyline per object + D).
        self._profile_cb = profile_cb or (lambda _row: None)
        self._stations: list[_Station] = []
        self._trails: dict[int, list] = {}
        self._done_trails: list[tuple[int, list]] = []
        self._banked: list[float] = []
        self._banked_u: list[float] = []
        self._station: Optional[_Station] = None

    # %% object budget %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

    def _want_objects(self) -> int:
        """A station's D is a median over several completed traverses, so the climb
        wants a crowd, not AF2's pair. Off-station this falls back to AF2."""
        if self.state == AFStateV2.CLIMB:
            return max(2, int(self.cfg.station_objects))
        return super()._want_objects()

    # %% the station walk (replaces AF2's fine climb) %%%%%%%%%%%%%%%%%%%%%

    def _begin_climb(self) -> None:
        super()._begin_climb()
        # AF2 anchors the climb to objects[0] as its cross-cal reference. AF3 must NOT:
        # _advance_objects refuses to refill while `objects[0].oid != primary_oid`
        # (its guard against clobbering a just-promoted handover), and in AF3 objects
        # retire and shift up constantly -- so leaving this set starves the station of
        # the very traverses it is counting.
        self._rs.primary_oid = None
        self._rs.ondeck_oid = None
        self._stations = []
        self._open_station()
        step_counts = self.cfg.station_step_pulses * self._counts_per_pulse
        log.info("af3-climb begin from pos=%+dc  ~%d stations of %dp (~%.0fc) over %.0fc",
                 int(round(self._rs.climb_start_pos)), self._station_budget(),
                 self.cfg.station_step_pulses, step_counts, self.cfg.sweep_counts)

    def _station_budget(self) -> int:
        """Stations the backstop allows, if the early exit never fires."""
        step = self.cfg.station_step_pulses * self._counts_per_pulse
        return int(self.cfg.sweep_counts // max(1.0, step))

    def _open_station(self) -> None:
        """Start a fresh station at the current position.

        Trails are dropped, not carried: an object that was mid-traverse when the lens
        stepped has samples from two different focus positions in it, and that is a
        blend of two curves rather than a noisy sample of one.
        """
        self._trails = {}
        self._banked = []
        self._banked_u = []
        # Retire every live patch along with its trail. What each has LEFT to cross from
        # where it now sits is a partial arc with no edge-to-centre contrast in it, so
        # `profile_metric` rejects it -- on the 143034 run 82 of 167 objects (49%) banked
        # nothing for exactly this reason, while holding roster slots that fresh
        # leading-edge patches needed. Clearing costs one frame, not five: AF2's
        # `_advance_objects` re-acquires immediately on an empty roster, with no stagger
        # gate on that path. The scene-motion tracker is independent of the roster, so
        # dropping the objects does not disturb tracking.
        self._rs.objects = []
        # Completed traverses, kept for the preview only. Dropped with the station for
        # the same reason the in-progress trails are: they belong to THIS lens
        # position and mean nothing at the next one. ~6 traverses x ~19 samples.
        self._done_trails = []
        # pos is a placeholder: the step this station was opened for has been commanded
        # but not travelled, so it is filled in from the encoder by `_climb_tick`. The
        # status line deliberately omits it rather than printing the stale one.
        self._station = _Station(pos=int(round(self._axis.position)), t0=time.monotonic())
        self._status(f"af3: station {len(self._stations) + 1} "
                     f"of ~{self._station_budget()} -- waiting on traverses")

    def _climb_tick(self, frame: np.ndarray) -> None:
        rs = self._rs
        cfg = self.cfg
        st = self._station
        if st is None:  # not reachable via _begin_climb; keeps a stray frame harmless
            return

        # A station's position has to be READ each frame, not remembered from open:
        # `_open_station` runs in the same tick as `step_pulses`, before the motor has
        # moved, so the position it captured is the PREVIOUS station's. Uncorrected that
        # pairs every D with the position one step behind it and slides the fitted vertex
        # a full station up-travel -- the 143034 run parked +155c off its own D peak, and
        # its status log printed two stations at -3412c. What the fit consumes is the
        # value from the last frame of an ~18 s dwell, settled by any measure.
        st.pos = int(round(self._axis.position))

        # Every live object, scored off THIS frame. Same-frame is load-bearing: two
        # scores a frame apart would fold in whatever moved between the frames.
        objs = list(rs.objects)
        scores = self._observe_scores(frame)
        live = set()
        for obj, score in zip(objs, scores):
            live.add(obj.oid)
            if score is None:
                continue
            self._log_object(obj, score)  # same CSV as OBSERVE, so one analysis reads both
            self._trails.setdefault(obj.oid, []).append(
                (0.5 * (obj.roi[0] + obj.roi[2]), 0.5 * (obj.roi[1] + obj.roi[3]),
                 float(score))
            )
        # Keep the always-on score-vs-encoder scatter alive through the walk.
        if scores and scores[0] is not None:
            self._sample(st.pos, float(scores[0]), True)

        # An oid that vanished from rs.objects panned off the FOV -- its traverse is
        # complete, so it can be reduced to one D.
        fh, fw = frame.shape[:2]
        for oid in [o for o in self._trails if o not in live]:
            trail = self._trails.pop(oid)
            got = profile_metric(trail, fw, fh, cfg)
            if got is None:
                continue
            d, u = got
            # Only trails that yielded a D reach the preview, so what the operator
            # sees is exactly the set of traverses the station's median is built from
            # -- a rejected half-crossing on screen would invite reading a shape that
            # contributed nothing.
            self._done_trails.append((oid, trail))
            self._banked.append(d)
            if u is not None:
                self._banked_u.append(u)
            log.info("af3-station pos=%+dc oid=%d D=%+.3f u*=%s (%d/%d banked)",
                     st.pos, oid, d, "n/a" if u is None else "%.3f" % u,
                     len(self._banked), cfg.station_min_traverses)

        self._publish_profile(fw, fh)

        enough = len(self._banked) >= cfg.station_min_traverses
        stalled = (time.monotonic() - st.t0) >= cfg.station_max_seconds
        if not (enough or stalled):
            return

        if self._banked:
            st.d = statistics.median(self._banked)
            st.u = statistics.median(self._banked_u) if self._banked_u else None
            self._stations.append(st)
            log.info("af3-station CLOSED pos=%+dc D=%+.3f u*=%s from %d traverses%s",
                     st.pos, st.d, "n/a" if st.u is None else "%.3f" % st.u,
                     len(self._banked), " (timed out)" if stalled else "")
        else:
            log.warning("af3-station pos=%+dc banked NOTHING in %.0fs -- skipped",
                        st.pos, cfg.station_max_seconds)

        if self._past_the_peak():
            log.info("af3-climb bracketed: %d stations, far flank seen -- stopping "
                     "%.0fc short of the %.0fc backstop",
                     len(self._stations),
                     cfg.sweep_counts - rs.climb_pulses_done * self._counts_per_pulse,
                     cfg.sweep_counts)
            self._finish_climb(bracketed=True)
            return
        swept = rs.climb_pulses_done * self._counts_per_pulse
        if swept >= cfg.sweep_counts:
            # Reached on a run where D never rose enough to arm the stop, so the
            # vertex about to be fitted may be an extrapolation off the end of the
            # data. Say so: run B looked healthy and was not.
            log.warning("af3-climb hit the %.0fc backstop WITHOUT a far flank -- the "
                        "vertex is extrapolated, treat the park as unverified",
                        cfg.sweep_counts)
            self._finish_climb(bracketed=False)
            return
        self._axis.step_pulses(+1, cfg.station_step_pulses)
        rs.climb_pulses_done += cfg.station_step_pulses
        self._open_station()

    def _past_the_peak(self) -> bool:
        """Has the walk seen a real far flank, or is this still the pedestal?

        Derived from ``self._stations`` rather than kept as a counter: the argmax moves
        as stations close, and a counter that has to be reset every time it moves is a
        second source of truth waiting to disagree with the first.
        """
        cfg = self.cfg
        ds = [s.d for s in self._stations if s.d is not None]
        if len(ds) < cfg.station_descend_stop + 2:
            return False
        if max(ds) - min(ds) < cfg.station_arm_rise:
            return False  # still flat: a dip here is noise, and D is even
        dmax = max(ds)
        tail = ds[ds.index(dmax) + 1:]
        return sum(1 for d in tail
                   if d < dmax - cfg.station_fall_margin) >= cfg.station_descend_stop

    def _publish_profile(self, frame_w: int, frame_h: int) -> None:
        """Hand the in-progress station to the preview overlay."""
        st = self._station
        if st is None:
            return
        self._profile_cb({
            "pos": st.pos,
            "station": len(self._stations) + 1,
            "banked": list(self._banked),
            "need": int(self.cfg.station_min_traverses),
            "completed": [(oid, list(tr)) for oid, tr in self._done_trails],
            "frame_w": frame_w,
            "frame_h": frame_h,
            "stations": [(s.pos, s.d) for s in self._stations if s.d is not None],
        })

    # %% pick the vertex and hand back to AF2's park %%%%%%%%%%%%%%%%%%%%%%

    def _finish_climb(self, *, bracketed: bool) -> None:
        pts = [(s.pos, s.d) for s in self._stations if s.d is not None]
        if len(pts) < 3:
            self._fail(f"af3: only {len(pts)} usable stations in the sweep")
            return
        best_pos, best_d = max(pts, key=lambda p: p[1])
        # log_domain=False: D is ALREADY a log ratio (and goes negative), so a parabola
        # in D is the right model and log() of it would be a domain error.
        xv = _fit_peak(pts, self.cfg.station_fit_window, log_domain=False)
        vertex = float(xv) if xv is not None else float(best_pos)
        target = int(round(vertex - self.cfg.park_bias_counts))
        log.info("af3-climb done: %d stations, D peaks %+.3f at %+dc, vertex %s, "
                 "bias %+.0fc -> park %+dc  u*=%s",
                 len(pts), best_d, best_pos,
                 "n/a (argmax)" if xv is None else "%+.1fc" % xv,
                 self.cfg.park_bias_counts, target,
                 ", ".join("%+d:%.2f" % (s.pos, s.u)
                           for s in self._stations if s.u is not None) or "n/a")
        self._status(f"af3: D peaked {best_d:+.2f} over {len(pts)} stations -- parking"
                     + ("" if bracketed else " (NO far flank -- vertex extrapolated)"))
        self._begin_park(target)


# ---------------------------------------------------------------------------
# Self-check: python -m host.capture.autofocus_v3
# ---------------------------------------------------------------------------

def _selfcheck() -> None:
    """Synthetic curved field. Asserts the properties AF3 leans on: D is single-peaked
    in lens position, it is blind to patch content, its peak is biased HIGH (never
    low), and u* walks inward and pins at frame centre at the true vertex."""
    cfg = AutoFocusConfigV3()
    W, H = 3200, 2232
    V0, K, WID = 1000.0, 0.0008, 1200.0   # vertex at centre, field curves away, hill width

    def vertex_at(u: float) -> float:
        """Best-focus counts for field position u -- a curved field, centre farthest."""
        return V0 - K * (u - 0.5 * W) ** 2

    def trail_at(lens: float, content: float, n: int = 20) -> list:
        out = []
        for i in range(n):
            u = W * (i + 0.5) / n
            # Gaussian hill about this position's own vertex, times a pedestal that
            # rises off-axis (broader hill off-centre -- the measured mechanism), all
            # scaled by however textured this particular patch of ground happens to be.
            d = (lens - vertex_at(u)) / WID
            ped = 0.02 + 0.10 * abs(u - 0.5 * W) / (0.5 * W)
            out.append((u, 0.5 * H, content * (math.exp(-d * d) + ped)))
        return out

    lenses = list(range(-3000, 5001, 250))
    got = [profile_metric(trail_at(L, 1.0), W, H, cfg) for L in lenses]
    assert all(g is not None for g in got), "every full traverse must yield a D"
    ds = [g[0] for g in got]
    us = [g[1] for g in got]

    # 1. single-peaked with both flanks reading as bowls.
    # Monotonicity is only claimed over the region a sweep can actually resolve, i.e.
    # where D is within reach of zero. Far below focus every field position is deep in
    # its pedestal, and D turns back UP again -- the real field map does exactly this
    # too (-1.137 at raw -1087 after a local min of -1.162 at -434). A metric that is
    # unimodal only near focus is fine; the sweep is centred there by the dive.
    peak_i = max(range(len(ds)), key=lambda i: ds[i])
    assert 0 < peak_i < len(ds) - 1, "peak must be interior, not at a sweep end"
    assert ds[0] < 0 and ds[-1] < 0, "both far flanks must read as bowls (D < 0)"
    lo_i = next(i for i, L in enumerate(lenses) if L >= -500)
    hi_i = next(i for i, L in reversed(list(enumerate(lenses))) if L <= 3000)
    for i in range(lo_i + 1, peak_i + 1):
        assert ds[i] > ds[i - 1] - 1e-9, f"not monotone rising below the peak at i={i}"
    for i in range(peak_i + 1, hi_i + 1):
        assert ds[i] < ds[i - 1] + 1e-9, f"not monotone falling above the peak at i={i}"

    # 2. the peak is biased HIGH -- above the centre's true best focus, never below.
    # This is the reason park_bias_counts exists; the field map showed the same sign
    # (centre vertex +1082, D peaked +1290).
    assert lenses[peak_i] >= V0, \
        f"D peaked at {lenses[peak_i]}, BELOW the true vertex {V0} -- bias changed sign"
    bias = lenses[peak_i] - V0

    # 3. content cancels -- a patch 50x more textured gives the SAME D and u*
    for L in (-1000, 0, 1000, 2000):
        a, ua = profile_metric(trail_at(L, 1.0), W, H, cfg)
        b, ub = profile_metric(trail_at(L, 50.0), W, H, cfg)
        assert abs(a - b) < 1e-9, f"content leaked into D at lens={L}: {a} vs {b}"
        # u* is None far from focus (no interior hump to fit); content must not change
        # even THAT verdict, let alone the fitted position.
        assert (ua is None) == (ub is None), f"content changed u* fittability at lens={L}"
        assert ua is None or abs(ua - ub) < 1e-9, \
            f"content leaked into u* at lens={L}: {ua} vs {ub}"

    # 4. u* is the bias-free estimator in waiting: it walks in from an edge and pins at
    # frame centre once the lens is at or past the centre's vertex.
    at_v0 = min(range(len(lenses)), key=lambda i: abs(lenses[i] - V0))
    assert us[at_v0] is not None and abs(us[at_v0] - 0.5) < 0.05, \
        f"u* should sit at frame centre when the lens is at the vertex, got {us[at_v0]}"
    below = [u for L, u in zip(lenses, us) if L <= V0 - 1000 and u is not None]
    assert below and max(abs(u - 0.5) for u in below) > 0.10, \
        "u* should be well off centre while the lens is far below the vertex"

    # 5. and the payoff: "u* first arrives at frame centre" is a MUCH less biased
    # estimate of the true vertex than "D peaks". This is the whole reason u* is
    # logged, so lock it in -- if a metric change ever inverts it, this fails.
    u_cross = None
    for i in range(1, len(lenses)):
        a, b = us[i - 1], us[i]
        if a is None or b is None or b < 0.5 or a >= 0.5:
            continue
        u_cross = lenses[i - 1] + (lenses[i] - lenses[i - 1]) * (0.5 - a) / (b - a)
        break
    assert u_cross is not None, "u* never reached frame centre across the sweep"
    assert abs(u_cross - V0) < abs(lenses[peak_i] - V0), (
        f"u* crossing {u_cross:.0f} should beat D's peak {lenses[peak_i]} "
        f"against truth {V0}")

    # 6. the parabola fit stays inside the swept range (extrapolation is not a fit)
    pts = list(zip(lenses, ds))
    xv = _fit_peak(pts, cfg.station_fit_window, log_domain=False)
    assert xv is not None, "parabola fit must find a vertex on a clean hill"
    assert min(lenses) <= xv <= max(lenses), "fitted vertex must stay inside the sweep"

    # 7. a half traverse is refused rather than silently averaged in
    half = [t for t in trail_at(V0, 1.0) if t[0] < 0.45 * W]
    assert profile_metric(half, W, H, cfg) is None, "partial traverse must be rejected"

    print("autofocus_v3 self-check OK")
    print("  D peaks at lens %+d, true centre vertex %+d -> bias %+d counts"
          % (lenses[peak_i], V0, bias))
    print("  parabola vertex %+.0f;  D range %+.2f .. %+.2f" % (xv, min(ds), max(ds)))
    print("  u* reaches frame centre at %+.0f -> bias %+.0f counts (%.1fx better)"
          % (u_cross, u_cross - V0, bias / max(1.0, abs(u_cross - V0))))
    print("  NOTE: set park_bias_counts from the RIG's measured gap, not this model's.")


if __name__ == "__main__":
    _selfcheck()
