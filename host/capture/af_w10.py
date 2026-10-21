"""w10 autofocus: the decision module (HANDOFF sec.54). Pure Python -- no Qt, no hardware, no cv2 beyond the gate.

Why this engine is different
----------------------------
Every earlier engine scored a PATCH, and a patch score moves when the ground under it changes as much as when
focus does -- hence trackers, handovers and cross-object stitching, all of which failed in flight (sec.30-47).
``w10`` (host/capture/psf_w10.py) is the 10-90% rise width of the sharpest natural step edges in the frame: a
PSF width, content-independent to second order (sec.52). So EVERY gated frame is a (lens position, focus)
sample regardless of what is under it, and the whole problem collapses to fitting one curve:

    w10^2 = w0^2 + c * (e - e0)^2          (sigma grows linearly with defocus; w10 ~= hypot(floor, 2.4 sigma))

and parking at ``e0``.

Shape of a run
--------------
    START    first frame fixes the origin; command a move AGAINST the sweep direction by ``start_offset``
    SWEEP    creep in the sweep direction ``step_counts`` per gated frame, refitting the V on every frame
    REFINE   both flanks have risen ``rise_px`` above the floor: smaller steps until the vertex CI <= tol
    PARK     return past the vertex on the start side by ``lash_counts``, then re-approach e0 in the sweep
             direction -- the encoder is on the motor side of ~1000 counts of lash (sec.49/53.5), so the lens
             is only where the curve says it is when approached the way the curve was measured
    DONE / FAIL

Everything the module decides comes out as an :class:`Action`: ``hold`` (no move), ``move(target)`` (absolute
encoder count), ``done(target)`` or ``fail(reason)``. Driving the axis, the frame tap and the panel belong to the
integration layer. ``tools/af_w10_replay.py`` drives this class with recorded flights; ``tools/w10_af_study.py``
uses the same fit primitives to size the constants.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np

from .psf_w10 import frame_ok

FRAME_S = 0.209          # PCB trigger period at the delivered 4.8 fps (sec.42)


# ---------------------------------------------------------------------------------------------- the V fit

@dataclass
class Vfit:
    e0: float            # vertex, encoder counts
    w0: float            # w10 at the vertex, px
    c: float             # curvature of w10^2 per count^2
    resid_sd: float      # robust sd of the w10 residuals, px
    n: int

    def w_at(self, e) -> np.ndarray:
        return np.sqrt(np.maximum(self.w0 ** 2 + self.c * (np.asarray(e, float) - self.e0) ** 2, 0.0))


def fit_v(e, w, trim: float = 3.0) -> Optional[Vfit]:
    """Least squares of w10^2 on (1, e, e^2) with two rounds of trimming residuals beyond ``trim`` robust sd.
    None when fewer than 5 frames remain or the parabola is not convex (no minimum inside the data's reach)."""
    e = np.asarray(e, float); w = np.asarray(w, float)
    m = np.isfinite(e) & np.isfinite(w)
    e, w = e[m], w[m]
    if len(e) < 5:
        return None
    mu, sc = e.mean(), max(e.std(), 1.0)           # centre + scale for conditioning
    x = (e - mu) / sc
    keep = np.ones(len(e), bool)
    for _ in range(3):
        if keep.sum() < 5:
            return None
        A = np.c_[np.ones(keep.sum()), x[keep], x[keep] ** 2]
        coef, *_ = np.linalg.lstsq(A, w[keep] ** 2, rcond=None)
        pred2 = coef[0] + coef[1] * x + coef[2] * x ** 2
        resid = w - np.sqrt(np.maximum(pred2, 0.0))
        mad = 1.4826 * np.median(np.abs(resid[keep] - np.median(resid[keep])))
        new = np.abs(resid) <= max(trim * mad, 0.05)
        if (new == keep).all():
            break
        keep = new
    a, b, cc = coef
    if cc <= 0:
        return None
    x0 = -b / (2 * cc)
    w0sq = a - b ** 2 / (4 * cc)
    return Vfit(e0=float(mu + x0 * sc), w0=float(math.sqrt(max(w0sq, 0.0))), c=float(cc / sc ** 2),
                resid_sd=float(mad), n=int(keep.sum()))


def depth_counts(fit: Vfit, rise_px: float) -> float:
    """Counts either side of the vertex that raise w10 by ``rise_px`` -- the usable depth of focus."""
    return math.sqrt(max(((fit.w0 + rise_px) ** 2 - fit.w0 ** 2) / fit.c, 0.0)) if fit.c > 0 else math.inf


def bowl_mask(e, w, rise_px: float = 2.0) -> np.ndarray:
    """Samples within ``rise_px`` of the bowl's bottom, the bottom being the minimum of the 3-sample running
    median of w10 in encoder order (one noisy frame cannot set it). Model-free; feeds ``fit_v`` when a shelf
    has defeated the global parabola."""
    e = np.asarray(e, float); w = np.asarray(w, float)
    order = np.argsort(e)
    ws = w[order]
    med = np.array([np.median(ws[max(i - 1, 0):i + 2]) for i in range(len(ws))])
    return w <= med.min() + rise_px


def fit_v_local(e, w, rise_px: float, window_depths: float, min_frames: int = 8, trim: float = 3.0):
    """Global V fit, then a refit on the samples within ``window_depths`` depths of its vertex. The recorded Vs
    are not parabolic far out (one flank is a shelf, sec.54), and a long tail leans the parabola's vertex ~100 c
    toward itself. Returns (fit, keep_mask); the mask is all-True when no refit happened."""
    e = np.asarray(e, float); w = np.asarray(w, float)
    fit = fit_v(e, w, trim)
    keep = np.ones(len(e), bool)
    if fit is None and len(e) >= min_frames:
        # No convex parabola through ALL the samples: a long shelf on one flank (the metric saturates at
        # ~11 px, so a start far from focus records a flat ceiling before the descent -- 2026-10-21, f/4,
        # back-off 5700 c above the vertex, 23 ceiling frames then a clean bowl) bends the global fit concave,
        # and the bowl in the data went unused. Seed the bowl model-free instead: the samples within 2 px of
        # the running-median minimum; the depth window below then refines it as usual.
        seed = bowl_mask(e, w)
        if seed.sum() >= min_frames:
            fit, keep = fit_v(e[seed], w[seed], trim), seed
    if fit is None or window_depths <= 0:
        return fit, keep
    win = np.abs(e - fit.e0) <= window_depths * depth_counts(fit, rise_px)
    if min_frames <= win.sum() < len(e):
        local = fit_v(e[win], w[win], trim)
        if local is not None:
            return local, win
    return fit, keep


def bracketed(e, w, fit: Optional[Vfit], rise_px: float, min_far: int = 3) -> bool:
    """Has the sweep SEEN both flanks? Model-light on purpose: on each side of e0 the frames beyond one depth of
    focus must be at least ``min_far`` and their median w10 must exceed the near frames' median by rise/2. A fit
    extrapolated from one flank puts e0 outside the data and fails this on the empty side."""
    return flank_seen(e, w, fit, rise_px, -1, min_far) and flank_seen(e, w, fit, rise_px, 1, min_far)


def flank_seen(e, w, fit: Optional[Vfit], rise_px: float, side: int, min_far: int = 3) -> bool:
    """One side of ``bracketed``: at least ``min_far`` frames beyond one depth on that side of e0, rising by
    rise/2 over the near frames' median (``side`` is +1 for encoder values above e0, -1 below)."""
    if fit is None:
        return False
    e = np.asarray(e, float); w = np.asarray(w, float)
    dep = depth_counts(fit, rise_px)
    if not math.isfinite(dep) or dep <= 0:
        return False
    # near = within one depth (the flank's reference level; the far samples start where it ends). Half a
    # depth held only 2 of the 100 c sweep steps on a +-263 c lens (2026-10-21), so a clean bowl was
    # never called bracketed and the sweep ran out its budget.
    near = np.abs(e - fit.e0) < dep
    if near.sum() < min_far:
        return False
    far = side * (e - fit.e0) >= dep
    return far.sum() >= min_far and float(np.median(w[far]) - np.median(w[near])) >= rise_px / 2


def bootstrap_vertex(e, w, n_boot: int = 100, seed: int = 0, q=(2.5, 97.5)) -> tuple[float, float]:
    """Percentile interval of e0 over frame resamples; (-inf, inf) when the fit fails on >30% of resamples."""
    e = np.asarray(e, float); w = np.asarray(w, float)
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(e), len(e))
        f = fit_v(e[idx], w[idx])
        if f is not None:
            out.append(f.e0)
    if len(out) < 0.7 * n_boot:
        return -math.inf, math.inf
    lo, hi = np.percentile(out, q)
    return float(lo), float(hi)


# ---------------------------------------------------------------------------------------------- the engine

class AFW10State(Enum):
    START = "start"
    SWEEP = "sweep"
    REFINE = "refine"
    PARK = "park"
    DONE = "done"
    FAIL = "fail"


@dataclass
class Action:
    kind: str                    # 'hold' | 'move' | 'done' | 'fail'
    target: Optional[float] = None
    reason: str = ""


@dataclass
class W10Config:
    direction: int = -1          # sweep direction in encoder counts; -1 = "sweep down, return fast" (sec.53.5)
    start_offset: float = 3500.0 # counts to back off AGAINST the sweep direction before sweeping
                                 # (>= takeup_counts + 2x depth, so the far flank is sampled after the take-up)
    takeup_counts: float = 1200.0  # after a reversal the motor turns this far before the lens moves (sec.49,
                                 # 53.5: ~1000-1290); frames inside it sit off the curve and are not sampled
    step_counts: float = 100.0   # creep per gated frame during SWEEP (one frame per station: sec.54 study)
    refine_step: float = 50.0    # creep per gated frame during REFINE
    span_counts: float = 7000.0  # sweep budget in counts from the start point; FAIL if never bracketed
    max_backoffs: int = 3        # back-offs allowed (the first one included) before giving up on the near flank
    fit_window_depths: float = 2.5   # refit on samples within this many depths of the vertex (0 = use all)
    rise_px: float = 0.3         # flank rise that defines "bracketed" and the depth of focus
    tol_frac: float = 0.5        # park tolerance (95% CI half-width) as a fraction of the depth of focus: half
                                 # a depth costs +0.075 px of w10, invisible; 1/3 was unreachable in a sane
                                 # budget on the 2026-09-30 flight (CI +-250..400 at 30 frames, sec.54)
    tol_min: float = 40.0        # ... clamped to this range in counts
    tol_max: float = 300.0
    min_frames: int = 8          # frames before the first fit is believed
    max_refine_frames: int = 40  # REFINE gives up waiting for the CI and parks on what it has
    lash_counts: float = 1500.0  # return this far past the vertex (start side) before re-approaching
                                 # (> takeup_counts, so the final approach moves the lens, not just the motor)
    settle_counts: float = 60.0  # encoder within this of a commanded target counts as "arrived"
    stall_frames: int = 25       # a commanded move that leaves the encoder flat for this many frames has hit a
                                 # limit (or the drive is dead): FAIL in SWEEP, proceed in PARK, re-base at START
    max_bad_frames: int = 60     # consecutive gated-out frames before FAIL (cloud, sea, stale encoder)
    min_edges: int = 300         # frame gate (sec.52.4): edges over the 5 ROIs; frames that barely pass read
                                 # high (sec.54.3 c), so raise this when time is cheap and skip the frame instead
    min_roi: int = 1             # frame gate: ROIs that contributed
    n_boot: int = 60


@dataclass
class W10Focus:
    cfg: W10Config = field(default_factory=W10Config)
    state: AFW10State = AFW10State.START
    enc: list = field(default_factory=list)     # gated samples
    w10: list = field(default_factory=list)
    fit: Optional[Vfit] = None
    ci: tuple = (-math.inf, math.inf)
    target: Optional[float] = None
    frames: int = 0
    bad_run: int = 0
    refine_frames: int = 0
    start_enc: Optional[float] = None
    taking_up: bool = True       # between a back-off command and the end of the lash take-up: no sampling
    landed: bool = True          # False from a back-off command until the encoder reaches (or passes) its start
    backoffs: int = 0
    n_at_backoff: int = 0        # samples held when the latest back-off was commanded
    far_target: Optional[float] = None
    enc_hist: list = field(default_factory=list)   # every encoder reading seen (the stall detector's input)
    log: list = field(default_factory=list)

    # -- helpers --------------------------------------------------------------------------------------------
    def _say(self, msg: str) -> None:
        self.log.append(f"[{self.state.value}] f{self.frames} {msg}")

    def _stalled(self, encoder: float) -> bool:
        """A move was commanded, the target is still far, and the encoder has not moved for stall_frames."""
        if self.target is None or abs(encoder - self.target) <= self.cfg.settle_counts:
            return False
        h = self.enc_hist[-self.cfg.stall_frames:]
        return len(h) >= self.cfg.stall_frames and (max(h) - min(h)) <= self.cfg.settle_counts

    def _move(self, target: float) -> Action:
        self.target = float(target)
        return Action("move", self.target)

    def _step(self, encoder: float, step: float) -> Action:
        """Next creep target: one step beyond the further of the reading and the last target, so a reading that
        lags the command (motor still turning, or a frame served from just behind) cannot stall the sweep.
        Once a convex fit exists the step shrinks to a third of the depth of focus, so a sharp lens (cam 2:
        +-300 counts) is sampled finely enough for the near-flank test, while a soft one is not crawled."""
        d = self.cfg.direction
        if self.fit is not None and math.isfinite(self.depth):
            step = max(self.cfg.refine_step, min(step, self.depth / 3))
        base = encoder if self.target is None else d * max(d * encoder, d * self.target)
        return self._move(base + d * step)

    def _fail(self, reason: str) -> Action:
        self.state = AFW10State.FAIL
        self._say("FAIL " + reason)
        return Action("fail", reason=reason)

    def _refit(self) -> None:
        e = np.asarray(self.enc); w = np.asarray(self.w10)
        self.fit, keep = fit_v_local(e, w, self.cfg.rise_px, self.cfg.fit_window_depths, self.cfg.min_frames)
        if self.fit is not None and keep.sum() >= self.cfg.min_frames:
            self.ci = bootstrap_vertex(e[keep], w[keep], self.cfg.n_boot, seed=len(e))
        else:
            self.ci = (-math.inf, math.inf)

    @property
    def depth(self) -> float:
        return depth_counts(self.fit, self.cfg.rise_px) if self.fit else math.inf

    @property
    def tol(self) -> float:
        d = self.depth
        return min(max(self.cfg.tol_frac * d, self.cfg.tol_min), self.cfg.tol_max) if math.isfinite(d) else self.cfg.tol_max

    @property
    def ci_half(self) -> float:
        return (self.ci[1] - self.ci[0]) / 2

    def _bracketed(self) -> bool:
        return bracketed(self.enc, self.w10, self.fit, self.cfg.rise_px)

    def _back_off(self, from_enc: float) -> Action:
        """Command the start point ``start_offset`` against the sweep direction from ``from_enc``; samples taken
        so far stay (they were approached in the sweep direction too), the take-up after the reversal is skipped."""
        self.backoffs += 1
        self.start_enc = float(from_enc) - self.cfg.direction * self.cfg.start_offset
        self.taking_up = True
        self.landed = False
        self.n_at_backoff = len(self.enc)
        self._say(f"backing off to {self.start_enc:.0f}")
        return self._move(self.start_enc)

    def _started_past_vertex(self) -> bool:
        """Is the start-side flank unobservable from the current start point? Either the fit puts the vertex
        within one depth of the start (or behind it), or there is no convex fit and w10 only rises with travel."""
        d = self.cfg.direction
        e = np.asarray(self.enc); w = np.asarray(self.w10)
        if len(e) - self.n_at_backoff < self.cfg.min_frames:
            return False   # the verdict on a start point needs samples taken from it
        if (self.fit is not None and e.min() < self.fit.e0 < e.max()
                and flank_seen(e, w, self.fit, self.cfg.rise_px, d)
                and not flank_seen(e, w, self.fit, self.cfg.rise_px, -d)):
            # the vertex was passed and the end flank is in (same test as ``bracketed``), yet the start-side
            # flank is not: it never will be from this start point. (An early fit whose vertex falls outside
            # the travelled range is extrapolation and is not trusted here.)
            return True
        if np.ptp(e) < 1 or np.ptp(w) < 1e-6:
            return False
        # model-free (no fit, or a spurious bowl fitted on a shelf): the START third of the travel is the
        # sharpest and w10 rises with travel overall. Segment medians, not the argmin -- one frame is 0.5 px of
        # noise. (Rising alone is also what the far flank does, hence both conditions.)
        travel = d * (e - self.start_enc)
        q = travel.max() / 3
        first, last = w[travel < q], w[travel > 2 * q]
        if len(first) < 2 or len(last) < 2:
            return False
        rises = float(np.median(last) - np.median(first)) >= self.cfg.rise_px / 2
        return rises and float(np.corrcoef(travel, w)[0, 1]) > 0.6

    # -- the one entry point -----------------------------------------------------------------------------
    def feed(self, encoder: Optional[float], stats: Optional[dict]) -> Action:
        """One frame: its encoder reading (counts, from the frame's focus block) and its ``array_edges`` stats.
        ``stats`` may be None when the frame was not scored (e.g. engine budget); it then only advances the
        motion bookkeeping."""
        self.frames += 1
        cfg, d = self.cfg, self.cfg.direction
        if self.state in (AFW10State.DONE, AFW10State.FAIL):
            return Action(self.state.value, self.target)
        if encoder is None or not math.isfinite(encoder):
            self.bad_run += 1
            return self._fail("no encoder reading") if self.bad_run > cfg.max_bad_frames else Action("hold", reason="no encoder")
        self.enc_hist.append(float(encoder))
        stalled = self._stalled(encoder)

        if self.state == AFW10State.START:
            self.state = AFW10State.SWEEP
            self._say(f"origin {encoder:.0f}; sweeping {'down' if d < 0 else 'up'}")
            return self._back_off(encoder)

        if self.state == AFW10State.PARK:
            return self._park_tick(encoder, stalled)

        travelled = d * (encoder - self.start_enc)
        if self.state == AFW10State.SWEEP and self.taking_up:
            # wait for the back-off to land, then creep through the lash take-up without sampling: the motor
            # turns but the lens does not, so those frames would put a flat shelf on the far flank
            if not self.landed:
                self.landed = (abs(encoder - self.start_enc) <= cfg.settle_counts
                               or travelled < -cfg.settle_counts)
            if not self.landed or travelled < -cfg.settle_counts:
                # Still on the way to the start point (or overshot it). A lens that stops SHORT of
                # the start reads as already past it (travelled > 0), which is why ``landed`` is
                # tracked rather than inferred: without it the take-up began from a position that
                # was never reached, and a limit there failed the run instead of re-basing.
                if stalled:
                    self.start_enc = float(encoder)
                    self.landed = True
                    self._say(f"back-off stopped short at {encoder:.0f} (limit?): sweeping from here")
                    return self._step(encoder, cfg.step_counts)
                return Action("hold", reason="moving to the start point")
            if travelled < cfg.takeup_counts:
                return self._step(encoder, cfg.step_counts)
            self.taking_up = False
        if stalled:
            if self.state == AFW10State.REFINE and self._bracketed():
                self._say(f"lens stopped at {encoder:.0f} while refining: parking on what was measured")
                return self._begin_park()
            return self._fail(f"lens stopped moving at {encoder:.0f} (limit?) before the vertex was bracketed")

        ok = stats is not None and frame_ok(stats, min_edges=cfg.min_edges, min_roi=cfg.min_roi)
        if not ok:
            self.bad_run += 1
            if self.bad_run > cfg.max_bad_frames:
                return self._fail(f"{self.bad_run} consecutive frames failed the w10 gate")
            return Action("hold", reason="frame gated out")
        self.bad_run = 0
        self.enc.append(float(encoder)); self.w10.append(float(stats["w10"]))
        self._refit()

        if self.state == AFW10State.SWEEP:
            if len(self.enc) >= cfg.min_frames and self._bracketed():
                self.state = AFW10State.REFINE
                self._say(f"bracketed: vertex {self.fit.e0:.0f} CI +-{self.ci_half:.0f}, depth +-{self.depth:.0f}, tol +-{self.tol:.0f}")
            elif len(self.enc) >= cfg.min_frames and self._started_past_vertex():
                # the start point was already on the far side of the vertex (or inside one depth of it), so
                # the start-side flank can never be seen from here: back off again and come through once more
                if self.backoffs >= cfg.max_backoffs:
                    return self._fail(f"start-side flank still missing after {self.backoffs} back-offs")
                self._say(f"start-side flank missing (vertex {self.fit.e0 if self.fit else float('nan'):.0f} vs "
                          f"start {self.start_enc:.0f}): backing off again")
                return self._back_off(self.start_enc)
            elif travelled > cfg.span_counts:
                return self._fail(f"swept {travelled:.0f} counts without bracketing the vertex")
            else:
                return self._step(encoder, cfg.step_counts)

        if self.state == AFW10State.REFINE:
            self.refine_frames += 1
            if not self._bracketed():
                if self.refine_frames > 5:
                    # the refit un-bracketed us: keep sweeping rather than park on a guess
                    self.state = AFW10State.SWEEP; self.refine_frames = 0
                    self._say("bracket lost on refit; back to SWEEP")
                return self._step(encoder, cfg.step_counts)
            if self.ci_half <= self.tol or self.refine_frames > cfg.max_refine_frames or travelled > cfg.span_counts:
                return self._begin_park()
            # keep collecting: fine steps through the bowl, then normal steps out along the far flank (more
            # lever arm on the curvature); standing still would only re-sample one patch of ground
            past = d * (encoder - self.fit.e0) > self.depth
            return self._step(encoder, cfg.step_counts if past else cfg.refine_step)
        return Action("hold")

    def _begin_park(self) -> Action:
        self.state = AFW10State.PARK
        e0 = self.fit.e0
        self.far_target = e0 - self.cfg.direction * self.cfg.lash_counts
        self._say(f"park: vertex {e0:.0f} +-{self.ci_half:.0f} (tol +-{self.tol:.0f}); returning to {self.far_target:.0f} first")
        return self._move(self.far_target)

    def _park_tick(self, encoder: float, stalled: bool) -> Action:
        cfg, e0 = self.cfg, self.fit.e0
        if self.target == self.far_target:
            if abs(encoder - self.far_target) <= cfg.settle_counts or cfg.direction * (encoder - self.far_target) < 0 \
                    or stalled:
                self._say(f"at the far side ({encoder:.0f}{', stopped short' if stalled else ''}); "
                          f"approaching {e0:.0f} in the sweep direction")
                return self._move(e0)
            return Action("hold", reason="returning")
        if abs(encoder - e0) <= cfg.settle_counts or cfg.direction * (encoder - e0) > 0 or stalled:
            self.state = AFW10State.DONE
            self._say(f"DONE at {encoder:.0f}{' (stopped short)' if stalled else ''} "
                      f"(vertex {e0:.0f}, w0 {self.fit.w0:.2f} px, {len(self.enc)} samples)")
            return Action("done", e0)
        return Action("hold", reason="approaching the vertex")

    def report(self) -> dict:
        return dict(state=self.state.value, frames=self.frames, samples=len(self.enc),
                    vertex=self.fit.e0 if self.fit else None, w0=self.fit.w0 if self.fit else None,
                    ci_half=self.ci_half if math.isfinite(self.ci_half) else None,
                    depth=self.depth if math.isfinite(self.depth) else None, seconds=self.frames * FRAME_S)
