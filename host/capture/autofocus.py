"""Contrast-detection autofocus for M4 (one camera at a time).

The aircraft is in flight, so the scene changes rapidly but consecutive frames
overlap heavily. We exploit that by picking a high-texture target from the
*leading edge* of the frame (the side facing flight direction, for maximum dwell
time), tracking that physical patch across frames, and hill-climbing the
open-loop (no-encoder) focus motor to maximise sharpness on the tracked patch.

Two layers:

* :class:`AutoFocusEngine` -- pure-Python, Qt-free state machine. It is driven
  one focus frame at a time via :meth:`feed_frame`, and emits actuator commands
  through plain callables (so it is fully unit-testable against a software focus
  simulator -- see ``tools/test_autofocus.py``).
* :class:`AutoFocusController` -- a thin ``QObject`` wrapper used by the app. It
  binds the engine's callables to a :class:`SerialClient`, gates AF to M4, and
  sequences the relay-selected cameras for "Auto-focus all".

Hardware constraints baked into the design:

* No encoder -> "position" is the cumulative signed pulse count (1 pulse = 10 ms
  of H-bridge drive). Absolute positioning is impossible; we remember the best
  observed position and return to it (approximate, backlash-compensated).
* Single H-bridge, relay-muxed (``$Cn*``) -> AF runs one camera at a time.
* Brushed DC + gear backlash -> extra ``backlash_pulses`` are sent on every
  direction reversal before the counted step.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

import cv2
import numpy as np

# Repo-root modules (see sys.path bootstrap in app.py / tests).
from sharpness import score_tile_subsampled
from tile_geometry import Tile, TileGrid

log = logging.getLogger(__name__)

Roi = tuple[int, int, int, int]  # (x0, y0, x1, y1)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class AutoFocusConfig:
    """Tunables for one autofocus run."""

    # ── Continuous dither ("wobble") servo ──────────────────────────────
    # AF is a never-ending servo, not a one-shot hunt: the real scene is always
    # moving (flying over ground), so focus can't be compared across positions
    # measured seconds apart -- content, not focus, would dominate (the 3->1021
    # score jump seen live). Instead we dither the lens by +-`dither_pulses`
    # around a running centre and compare the SAME motion-tracked patch at +d vs
    # -d, a few frames apart. That near-simultaneous differential cancels scene
    # motion and isolates focus; we nudge the centre up the gradient and hold it
    # in a deadband. See AutoFocusEngine.
    dither_pulses: int = 2
    """Base half-amplitude of the focus wobble (pulses) used near focus. Each cycle
    samples centre+d and centre-d. Small = a tight resolution floor for the hold;
    the probe GROWS automatically (up to ``dither_max_pulses``) when the gradient is
    flat, so a small base doesn't strand the servo out on a defocus plateau."""
    dither_max_pulses: int = 32
    """Widest dither the probe grows to when the local gradient is flat. Far from
    focus the sharpness curve is nearly level, so a small dither reads zero slope
    and the servo can't tell which way to go (the "started blurry, stayed blurry"
    symptom). Widening the probe reaches into the sloped part of the curve to find
    the direction, then it shrinks back to ``dither_pulses`` as focus is neared.
    Wider than the old 16 because str0 pulses are ~half the travel of str1.
    ponytail: ceiling = defocus so deep BOTH arms sit on the floor even at this
    width still reads flat; raise this (or restart AF closer) if that happens."""
    grad_avg_cycles: int = 2
    """Dither cycles measured at the SAME centre and AVERAGED before the centre is
    nudged, for extra noise reduction ON TOP of each cycle's own drift cancellation
    (the time-symmetric double sweep). The true focus gradient is consistent (flight
    focus distance is ~fixed while scenery churns), so averaging beats the shallow,
    noisy far-field tails. Higher = more robust but slower (each cycle is 6 arms;
    the operator accepts minutes)."""
    peak_frac: float = 0.04
    """A cycle also measures the CENTRE (between -d and +d), so each dither reads a
    3-point mini-curve. ``peakedness = (centre - mean(arms)) / centre``. Lock only
    when the gradient is flat AND peakedness >= this (centre is a real local max) --
    a *concave* top, not the flat defocused floor. This is the floor-lock guard: on
    the floor all three points are equal (peakedness ~0) so it keeps hunting instead
    of declaring 'in focus' while blurred (the failure seen on the rig)."""
    lock_anchor_frac: float = 0.90
    """Anchored flat-top lock (option A). A sharp focus curve is nearly FLAT over a +-2
    pulse dither, so its peakedness never reaches ``peak_frac`` at the base dither and the
    servo keeps nudging on a drift-dominated gradient instead of settling (the live run:
    centre climbed 19->83 to the peak at -2 but peak_avg stayed ~0.03, so it never locked
    and hunted until snap-back). Fix: once the gradient is inside the deadband AND the
    absolute centre level is >= this fraction of the best centre level seen this run,
    treat it as 'at the peak' and count toward lock. The best-level anchor is what
    separates a flat PEAK (high plateau) from the flat defocus FLOOR (low plateau) -- the
    floor sits far below best, so it can never satisfy this and still can't false-lock."""
    strong_peak_frac: float = 0.30
    """Peakedness at/above which the centre is an UNAMBIGUOUS local max -> lock and stop
    THIS cycle, without waiting for servo_lock_cycles consecutive clean cycles. On an
    encoderless motor every dither round-trip loses a little open-loop position, so a
    static scene that keeps dithering slowly walks a found focus into blur (the live
    'returned to best but worse, then failed' run: centre-0 score collapsed 86->12 while
    the lens sat at logical 0, then a coarse sweep found the true peak had drifted to
    -244). Per-cycle content/vibration noise keeps breaking the 3-in-a-row lock streak,
    so a strong peak (centre >~1.4x the arms' mean, e.g. the live 86.7 vs 13) must settle
    immediately. ponytail: ceiling = this is the open-loop stopgap until the AMT203S shaft
    encoder (HANDOFF Section 23) gives closed-loop repositioning; with the encoder the
    servo can dither indefinitely without drift and this early-out can relax."""
    servo_gain: float = 2.5
    """Centre-nudge = round(gain * g * dither). The gradient (S+-S-)/(S+ + S-) is in
    [-1, 1]; multiplying by the current dither makes steps coarse when probing wide
    (far out) and fine when the probe is small (near focus). At the base dither of 2
    this reproduces the old ~5*g behaviour (no overshoot at the peak)."""
    servo_max_nudge: int = 8      # cap on centre move per cycle (pulses)
    servo_deadband: float = 0.04
    """|normalised gradient| below this = 'in focus' for the cycle: hold the
    centre, don't chase measurement noise. A few % above the frame noise floor."""
    servo_lock_cycles: int = 3
    """Consecutive in-deadband cycles before reporting 'in focus'. A one-shot run
    (Auto-focus all) finishes here; a continuous run keeps holding + correcting."""
    snap_back_frac: float = 0.0
    """Best-absolute anchor. If >0 and the averaged CENTRE-arm level drops below this
    fraction of the best centre level seen this run, the servo has walked DOWN off the
    peak -- a phantom differential gradient on the encoderless motor (all dither arms
    approach upward, so +d reads high even AT the peak and biases '+' uphill while
    moving + actually blurs; the live static 153->18 walk). It returns to the
    best-scoring position and, for a one-shot run, finishes there. 0 disables it: a
    MOVING scene's absolute level drifts with the content, so a stale best is
    meaningless there -- it relies on the coarse leash + deadband instead."""
    max_center_pulses: int = 600
    """Absolute travel guard on the servo centre (pulses each way). If focus
    would sit beyond this the lens is effectively out of range -> fail. Generous
    because str0's fine pulses need many steps to cross the full focus range."""
    motion_on_px: float = 4.0
    """EMA per-frame patch motion (preview px) at/above which the scene is treated as
    MOVING -> fast per-patch tracking profile. The profile follows DETECTED motion, not
    the mode: M4 is the flight mode (moving in the air, static on the bench) and M5 is a
    bench flight-sim (moving mirrors), so 'M4 vs M5' cannot pick the cycle length -- the
    scene does. Hysteresis with motion_off_px. ponytail: ceiling = these thresholds are
    first guesses; the af-servo log prints motion=<px> so tune them from a real moving
    run (M5/flight) vs a static bench run rather than guessing twice."""
    motion_off_px: float = 1.5
    """Below this EMA motion the scene is treated as STATIC -> long averaged + anchored
    profile (kills the phantom-gradient walk; snap-back on)."""
    motion_ema: float = 0.25
    """EMA weight for the per-frame motion magnitude that selects the profile."""

    # ── Coarse sweep-to-max acquisition ─────────────────────────────────
    # The dither servo is a FINE / tracking servo: it only senses the sharpness
    # slope WITHIN its probe half-width. From gross defocus (lens hundreds of
    # pulses off) the curve is dead flat across even the max dither, so it reads
    # zero slope and can't find the peak -- the "doesn't work on a blurry start"
    # failure seen on the rig (score ~6 flat across dither 2..32, FAILED). Before
    # handing to the servo we therefore sweep the lens across a wide range in
    # coarse steps, measure absolute sharpness at each, and drive to the global
    # max. A go-to-max is robust where a differential is not: it needs no reliable
    # gradient, a monotonic measurement lag only shifts the found peak by a
    # constant (still the peak), and the start position is one of the samples so
    # the result is never worse than where it began.
    coarse_enable: bool = True
    coarse_range_pulses: int = 400
    """Half-range swept each way from the start position (pulses). Must cover the
    distance to focus; too small and a deep-defocus start never reaches the peak.
    Kept <= max_center_pulses. ponytail: ceiling = if the lens can sit >400 str0
    pulses from focus, raise this (slower sweep) or pre-focus roughly first."""
    coarse_step_pulses: int = 25
    """Grid spacing of the coarse pass (pulses). Range/step = sample count; 400/25
    = ~33 positions. Finer = better peak localisation but a longer sweep."""
    coarse_refine_step: int = 6
    """Second pass steps this finely across +-coarse_step around the coarse peak,
    so the handoff to the base dither (2) lands already near the true max."""
    coarse_settle_frames: int = 4
    """Frames skipped after each coarse move before measuring -- covers the lens
    creep + crop-pipeline lag. Smaller than the servo settle (8): a coarse go-to-max
    tolerates a little residual lag (it shifts the peak, doesn't invert it)."""
    coarse_measure_frames: int = 2
    coarse_min_contrast: float = 0.15
    """Sweep peak must rise at least this fraction above the sweep floor
    ((max-min)/max) to be a real focus peak. Below it the whole range is flat
    (no focusable detail in the tile) -> fail instead of driving to a noise max."""
    coarse_servo_leash_pulses: int = 40
    """After acquisition the dither servo centre is clamped to coarse-best +- this.
    The differential servo can chase a phantom gradient (stale/noisy score) and
    walk a good focus into garbage (the live 2800->1300 runaway); the leash caps
    how far it can stray from the swept optimum so a bad gradient can't ruin it."""

    # Measurement cadence (per dither arm). Two lags stack after a move: the
    # brushed-DC lens keeps creeping, AND the full-res crop being scored is produced
    # a few frames behind the live lens (pipeline depth). Measure before both flush
    # and every arm reads like the PREVIOUS (lower) position -- and since the sweep
    # is always upward, that makes +d look sharper than -d at every position, a
    # consistent phantom +gradient that walked the lens off a sharp start into blur
    # (seen on the rig, crop score 190 -> 8). So settle the SAME, generous number of
    # frames on EVERY arm (symmetry matters: an asymmetric settle biases the sign).
    settle_frames: int = 8   # frames to skip after ANY move, to flush lens+pipeline lag
    measure_frames: int = 3  # frames whose scores are combined (median) per arm

    # Backlash comp (brushed DC + gears). Applied on every dither reversal so the
    # +d and -d optical stops repeat; must be >= the mechanism's lash band.
    backlash_pulses: int = 3

    # Drive strength ($Sn) during the run. str0 (extra-fine, ~11% PWM) is the
    # finest travel per pulse -- purpose-built for AF. str1 (2x the move) steps
    # over a narrow focus peak, which straddled the peak and made the +d/-d
    # gradient flip-flop on real hardware; str0 gives the resolution to sit on it.
    # (Confirmed on the rig: str0 moves without stalling and is visibly finer.)
    strength: int = 0

    # Texture / failure detection
    min_texture_score: float = 1.5
    max_low_texture_frames: int = 40

    # Leading-edge target selection
    edge_band: float = 0.25
    acquire_lane_frac: float = 0.0
    """Restrict acquisition to the middle fraction of the frame ACROSS the travel
    axis (0 = off, the flight default). A patch that crosses the frame down the left
    edge and one that crosses down the middle traverse different slices of a curved
    field, so their profiles are not the same measurement. AF3 medians several
    traverses per station, and mixing lanes puts field curvature into that spread --
    which is repeatability lost for nothing, since the lane is free to choose."""
    tiles_per_frame: float = 5.0  # TileGrid zoom -> tile ~ frame/this
    select_subsample: int = 2
    """Subsample for the tile-selection texture score. The default whole-pipeline
    value (4) plus a coarse high-pass erases the fine, DENSE detail of distant
    structure (building windows/edges) while keeping one fat coarse edge, so the
    picker chose a lone bright strip over the buildings ('shitty tile'). Scoring at
    2 preserves finer texture so genuinely detailed tiles win. ponytail: 1 = finest
    but ~4x the work per acquire; 2 is the cost/quality knee on the rig frames."""

    # Saturation-aware tile ranking. A near-clipped edge (e.g. an overexposed
    # mast/window frame) has huge band-pass contrast, so a plain texture metric can
    # rank it top -- but it reads as a "boring bright strip" AND is a poor focus
    # target (clipping flattens the sharpness gradient near best focus). We score
    # each tile on the texture of its UNCLIPPED pixels only (:func:`_unclipped_texture`)
    # so genuinely busy detail wins even when partly overexposed, and a blown strip
    # scores ~0 on its own -- no fraction threshold to re-tune per exposure. (An
    # earlier ``sat_frac_reject`` fade curve did that and inverted good picks: a
    # building tile 30% blown but rich in the other 70% lost to featureless sky.)
    sat_level: float = 0.98        # fraction of full-scale counted as clipped

    # Bench rig (orthophoto screen): restrict tiles to an inset interior,
    # excluding the screen bezel and the optical ground-truth strip band.
    restrict_interior: bool = False
    interior_inset: float = 0.12
    exclude_edge: Optional[str] = None  # top/bottom/left/right = strip location

    # Bench only: no motion, so there is no leading edge -- search the whole
    # frame for the sharpest tile instead of the centre circle. The real rig is
    # always moving (a helicopter), so this stays off in flight.
    full_frame_target: bool = False

    # Tracking
    min_track_response: float = 0.05  # phaseCorrelate response floor

    # Cadence
    drive_cadence_to_min: bool = True
    cadence_steps: int = 26  # number of "<" to send to reach the FW floor

    def apply_scene_profile(self, *, moving: bool) -> None:
        """Retune the dither-cycle length to the scene's dynamics.

        A 3-arm cycle only yields a centre nudge once ALL three arms are measured
        on the SAME tracked patch (the normalised gradient cancels scene content
        only when -d/0/+d are the same content). On a MOVING scene (M5 ground test,
        flight) the patch pans out of the FOV in a handful of frames, forcing a
        reacquire that restarts the cycle -- so the default long, heavily-settled +
        averaged cycle (settle 8 x measure 3 x avg 2 = 66 frames per nudge) never
        completes before the patch is gone, and the lens just dithers in place
        without ever converging (the live 'tracks well but never focuses' bug:
        66-frame cycle vs ~11-frame dwell, zero completed cycles). Use a SHORT
        single-cycle probe there so one nudge lands per patch and the centre
        converges across the stream of patches that cross the FOV.

        A STATIC scene (M4, mirrors parked) has unlimited dwell, so keep the long,
        heavily-settled cycle that averages out noise and kills the pipeline-lag
        phantom gradient (the settle_frames note). These values mirror the dataclass
        defaults.

        ponytail: ceiling = the short profile trusts settle_frames=2 to cover the
        crop-vs-lens pipeline lag; if a moving run walks the wrong way into blur the
        lag is deeper than 2 frames -- raise settle_frames here (and/or slow the
        sweep for more dwell margin) rather than reverting to the slow cycle.
        """
        if moving:
            self.settle_frames = 2
            self.measure_frames = 1
            self.grad_avg_cycles = 1
            # With no cross-cycle averaging, per-cycle content noise leaks into the
            # gradient (~+-0.1 seen live). A tight deadband then nudges on that noise
            # and random-walks the centre off a good focus back to the floor (the live
            # 'found focus then wandered to centre=-13' drift). Widen the deadband so
            # only a real slope (well above content noise) moves the centre; near
            # focus it holds inside the dither band.
            self.servo_deadband = 0.12
            # CRITICAL for M5: the firmware services focus pulses between mirror steps
            # with a blocking 10 ms delay() PER pulse. A wide dither arm move (up to
            # 2*dither pulses) then stalls the sweep and starves frame delivery -- at
            # dither=32 that is ~640 ms/arm, which dropped the preview to ~1 frame / 5 s
            # (the live 'it stays slow' report; 3956 pulses in one run). Cap the probe
            # so no burst monopolises the loop; if there is genuinely no peak within
            # this reach the servo fails fast and STOPS pulsing (sweep recovers) rather
            # than flailing at full width. ponytail: ceiling = a peak >8 pulses from the
            # start on a flat floor won't be found -- pre-focus roughly first, or add a
            # non-blocking coarse sweep to the firmware.
            self.dither_max_pulses = 8
            # A moving scene's absolute sharpness drifts with the content, so a
            # best-so-far anchor is meaningless -- disable snap-back and rely on the
            # coarse leash + wide deadband.
            self.snap_back_frac = 0.0
        else:
            self.settle_frames = 8
            self.measure_frames = 3
            self.grad_avg_cycles = 2
            self.servo_deadband = 0.04
            self.dither_max_pulses = 32
            # Static bench: the absolute level IS trustworthy, so anchor to the best
            # centre level seen and bail back to it if the phantom +d bias walks the
            # lens down off focus (the live 'good focus -> deteriorated' 153->18 walk).
            self.snap_back_frac = 0.85


# ---------------------------------------------------------------------------
# Patch tracker (phase correlation)
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
# Target selection + sharpness helpers
# ---------------------------------------------------------------------------


def _tiles_in_lead_band(tiles, lead_edge: str, edge_band: float) -> list[Tile]:
    """The tiles nearest ``lead_edge``, taking the leading ``edge_band`` fraction of
    the span the candidates actually cover along the flow axis.

    This is the leading-edge preference expressed as a *filter* rather than a grid
    region, which is what lets it combine with an interior-masked grid --
    :class:`TileGrid` regions are mutually exclusive by construction.

    The band is measured against the CANDIDATE TILES, not the frame. After interior
    masking the usable area is a sub-rectangle, and grid quantisation shrinks it
    again: with the default 0.12 inset and 5 tiles/frame the lowest interior tile
    centre lands at 0.62 of frame height, so a frame-relative "bottom 25 %" band
    matches nothing and silently disables the preference. Content enters the *usable*
    area at its edge, so that is the edge worth preferring. Never returns empty --
    the extreme tile always qualifies.
    """
    tiles = list(tiles)
    if not tiles:
        return []
    vertical = lead_edge in ("top", "bottom")
    centres = [
        (t.y0 + t.y1) / 2.0 if vertical else (t.x0 + t.x1) / 2.0 for t in tiles
    ]
    lo, hi = min(centres), max(centres)
    span = hi - lo
    if span <= 0:
        return tiles
    band = max(0.0, min(1.0, float(edge_band)))
    if lead_edge in ("top", "left"):
        limit = lo + span * band
        return [t for t, c in zip(tiles, centres) if c <= limit]
    limit = hi - span * band
    return [t for t, c in zip(tiles, centres) if c >= limit]


def _tiles_in_centre_lane(tiles, lead_edge: str, lane_frac: float) -> list[Tile]:
    """The tiles in the middle ``lane_frac`` of the candidates' spread ACROSS the
    flow axis, i.e. the lane an object will travel down.

    Cross axis is the one the object does NOT move along: for ``top``/``bottom`` flow
    it travels in y, so the lane is selected on x, and vice versa. Measured against
    the candidate tiles rather than the frame for the same reason as
    :func:`_tiles_in_lead_band` -- interior masking plus grid quantisation means the
    usable area is a sub-rectangle, and a frame-relative band would silently match
    nothing. Never returns empty: falls back to the single most central tile, because
    a lane preference that can starve acquisition is worse than a wide lane.
    """
    tiles = list(tiles)
    if not tiles:
        return []
    vertical = lead_edge in ("top", "bottom")   # flows along y -> lane picked on x
    centres = [
        (t.x0 + t.x1) / 2.0 if vertical else (t.y0 + t.y1) / 2.0 for t in tiles
    ]
    lo, hi = min(centres), max(centres)
    if hi - lo <= 0:
        return tiles
    mid = 0.5 * (lo + hi)
    half = 0.5 * (hi - lo) * max(0.0, min(1.0, float(lane_frac)))
    kept = [t for t, c in zip(tiles, centres) if abs(c - mid) <= half]
    if kept:
        return kept
    nearest = min(range(len(tiles)), key=lambda i: abs(centres[i] - mid))
    return [tiles[nearest]]


def select_leading_edge_roi(
    frame: np.ndarray,
    lead_edge: Optional[str],
    cfg: AutoFocusConfig,
) -> tuple[Roi, float]:
    """Pick a high-texture tile on the leading-edge band (or central circle if
    ``lead_edge`` is None). Returns ``((x0,y0,x1,y1), score)``."""
    fh, fw = frame.shape[:2]
    if cfg.restrict_interior:
        # Bench rig: keep tiles inside the screen interior, off the bezel and
        # off the optical strip band, regardless of motion direction.
        grid = TileGrid(
            frame_width=fw,
            frame_height=fh,
            display_width=fw,
            display_height=fh,
            zoom=cfg.tiles_per_frame,
            region="interior",
            interior_inset=cfg.interior_inset,
            exclude_edge=cfg.exclude_edge,
            edge_band=cfg.edge_band,
        )
        # A moving bench scene wants BOTH: mask off the bezel/strip *and* still take
        # the patch that just entered, so it has the whole usable width to cross
        # before it exits. Masking alone used to win outright, which handed back tiles
        # already part-way across -- short dwell, and the two-object relay never got
        # its overlap.
        if lead_edge is not None:
            banded = _tiles_in_lead_band(grid, lead_edge, cfg.edge_band)
            if banded:
                grid = banded
    elif lead_edge is None:
        # No leading edge (bench/static, or flight before the pan starts): search
        # the WHOLE interior for the sharpest tile, not a tiny centre circle. The
        # real detail (buildings/horizon) usually sits off-centre, and a centre-only
        # search locks onto smooth sky/water -- the "why did it pick THIS boring
        # tile?" symptom. Once the scene moves, motion picks the leading edge below.
        grid = TileGrid(
            frame_width=fw,
            frame_height=fh,
            display_width=fw,
            display_height=fh,
            zoom=cfg.tiles_per_frame,
            region="interior",
            interior_inset=cfg.interior_inset,
        )
    else:
        grid = TileGrid(
            frame_width=fw,
            frame_height=fh,
            display_width=fw,
            display_height=fh,
            zoom=cfg.tiles_per_frame,
            region="edge",
            lead_edge=lead_edge,
            edge_band=cfg.edge_band,
        )
    # Applied AFTER the lead band so the two compose: leading edge picks WHEN a patch
    # is taken, the lane picks WHERE across the flow. Needs a known flow axis, so it
    # is skipped when there is no lead edge (static bench).
    lane = float(getattr(cfg, "acquire_lane_frac", 0.0) or 0.0)
    if lane > 0.0 and lead_edge is not None:
        grid = _tiles_in_centre_lane(grid, lead_edge, lane)
    if len(grid) == 0:
        # Degenerate frame/tile sizing -- fall back to a centred ROI.
        tw = max(2, fw // 4) & ~1
        th = max(2, fh // 4) & ~1
        x0 = (fw // 2 - tw // 2) & ~1
        y0 = (fh // 2 - th // 2) & ~1
        roi = (x0, y0, x0 + tw, y0 + th)
        return roi, roi_sharpness(frame, roi)
    t, score = _best_tile_saturation_aware(frame, grid, cfg)
    return (t.x0, t.y0, t.x1, t.y1), score


def _clipped_fraction(frame: np.ndarray, tile: Tile, cfg: AutoFocusConfig,
                      subsample: int = 4) -> float:
    """Fraction of a tile's (subsampled) pixels at/above the clip level."""
    region = frame[tile.y0:tile.y1:subsample, tile.x0:tile.x1:subsample]
    if region.size == 0:
        return 0.0
    full = 16383.0 if region.dtype == np.uint16 else 255.0  # 14-bit raw or uint8
    hi = cfg.sat_level * full
    return float(np.count_nonzero(region >= hi)) / float(region.size)


def _unclipped_texture(
    frame: np.ndarray, tile: Tile, cfg: AutoFocusConfig, subsample: int,
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
    # "texture" (that boundary edge is why an overexposed strip used to win). A
    # genuinely busy tile that is only partly blown keeps its real edges in the
    # unclipped majority. Use the SAME coarse Gaussian high-pass as the base texture
    # metric (NOT a local Laplacian): it is a band-pass that ignores pixel noise, so
    # flat hazy SKY with sensor noise stays low instead of scoring as fake detail
    # (a Laplacian ranked noisy sky above the buildings).
    if sat > 0.0:
        r = r.copy()
        r[clipped] = float(np.median(r[valid]))
    hp = r - cv2.GaussianBlur(r, (0, 0), 7)
    return float(np.std(hp[valid])), sat


def _best_tile_saturation_aware(
    frame: np.ndarray, grid, cfg: AutoFocusConfig,
) -> tuple[Tile, float]:
    """Pick the tile with the most focusable detail, scoring each tile on the texture
    of its UNCLIPPED pixels only (see :func:`_unclipped_texture`) so genuinely busy
    detail wins even when it is partly overexposed, while a blown-white strip scores
    ~0 on its own. Falls back to the plain texture winner if every tile is clipped."""
    ss = max(1, cfg.select_subsample)
    best_i, best_eff = 0, -1.0
    best_raw_i, best_raw = 0, -1.0
    ranked: list[tuple[float, float, float, int, Tile]] = []  # (eff, texture, sat, i, tile)
    for i, tile in enumerate(grid):
        texture = score_tile_subsampled(frame, tile, subsample=ss)
        if texture > best_raw:
            best_raw, best_raw_i = texture, i
        eff, sat = _unclipped_texture(frame, tile, cfg, ss)
        ranked.append((eff, texture, sat, i, tile))
        if eff > best_eff:
            best_eff, best_i = eff, i
    # Log the ranking so a bad pick can be diagnosed: eff = unclipped-pixel texture
    # (the ranking key), tex = raw whole-tile texture, sat = clipped fraction.
    ranked.sort(key=lambda r: r[0], reverse=True)
    top = "; ".join(
        f"[{r[3]}] eff={r[0]:.1f} tex={r[1]:.1f} sat={r[2]*100:.0f}%"
        f" c=({(r[4].x0 + r[4].x1) // 2},{(r[4].y0 + r[4].y1) // 2})"
        for r in ranked[:5]
    )
    log.info("af-select ss=%d top: %s", ss, top)
    if best_eff <= 0.0:  # every candidate was clipped: keep the raw texture winner
        return grid[best_raw_i], float(best_raw)
    return grid[best_i], float(score_tile_subsampled(frame, grid[best_i], subsample=ss))


def roi_sharpness(frame: np.ndarray, roi: Roi, subsample: int = 2) -> float:
    """High-pass std of a fixed ROI: a *texture-presence* measure (is there
    focusable detail here?). Used for tile selection and the low-texture guard,
    NOT for judging focus -- its heavy blur subtraction keeps only coarse
    features that barely change with focus. Use :func:`focus_measure` to score
    focus."""
    x0, y0, x1, y1 = roi
    return score_tile_subsampled(frame, Tile(x0, y0, x1, y1), subsample=subsample)


def focus_measure(frame: np.ndarray, roi: Roi) -> float:
    """Focus score = variance of the Laplacian over the full-resolution ROI.

    Unlike :func:`roi_sharpness`, this keeps the *high* spatial frequencies --
    the ones defocus actually blurs away -- with no extra subsampling and no
    low-frequency band-pass. It is the classic passive-AF metric and peaks
    sharply at best focus, so the hill-climb can localise focus instead of
    stalling on a flat coarse-texture curve. The preview is a single Bayer
    channel (stride-4), so a 3x3 Laplacian sees no mosaic checkerboard.
    """
    x0, y0, x1, y1 = roi
    region = frame[y0:y1, x0:x1]
    if region.size == 0:
        return 0.0
    if region.dtype == np.uint16:
        region = (region >> 6).astype(np.uint8)
    lap = cv2.Laplacian(region.astype(np.float32), cv2.CV_32F, ksize=3)
    return float(lap.var())


def focus_measure_normalized(frame: np.ndarray, roi: Roi) -> float:
    """:func:`focus_measure` carrying the same mean^2 normalisation as
    :func:`focus_measure_full_res`, so a ratio measured with one is valid for the other.

    The cross-object handover measures its A/B ratio on the coarse frame but applies it
    to climb samples produced by ``focus_measure_full_res``, which divides brightness
    out. Raw Laplacian variance instead scales *with* brightness^2, so mixing the two
    transfers a ratio wrong by a factor of (mean_B/mean_A)^2 -- and the sign is fixed,
    not random: the on-deck patch is always freshly argmax-picked in the leading-edge
    band near frame centre, while the outgoing primary has drifted to a dim, vignetted
    frame edge. So mean_A < mean_B on every single handover. Live, that read as 13
    consecutive ratios in 0.40-0.64 (never once above 1), compounding cum_scale down to
    3e-4, crushing every late sample and parking the lens at the dive floor.
    """
    x0, y0, x1, y1 = roi
    region = frame[y0:y1, x0:x1]
    if region.size == 0:
        return 0.0
    mean = float(region.mean())
    if region.dtype == np.uint16:
        mean /= 64.0  # focus_measure shifts uint16 >> 6 before differencing
    return focus_measure(frame, roi) / (mean * mean + 1.0) * 1e4


# ---------------------------------------------------------------------------
# Display-raster notch (bench only)
# ---------------------------------------------------------------------------
#
# On the OrthoSim bench the "scene" is a 4K LCD, and the camera oversamples it
# ~3.36:1, so the panel's own pixel/subpixel grid is imaged as real, periodic
# detail. It focuses in the same plane as the displayed scene and is far more
# uniform across the field than terrain is, so it drives the AF metric *better*
# than the picture does -- the 2026-09-14 grey-panel runs bracketed on a blank
# screen and parked 2 counts from the imagery run. Left in, every bench number
# is partly a measurement of the monitor.
#
# The grid lands in the spectrum as a lattice of narrow spikes (fundamentals,
# harmonics and their sensor-level aliases), so this kills it where it lives:
# zero those bins, leave the broadband content alone. Real terrain has no such
# spikes, and IN FLIGHT THERE IS NO SCREEN -- so this is off by default and the
# flight path is bit-for-bit unchanged.
#
# ponytail: an FFT per patch, not a cheap separable kernel. The lattice is
# rotated off-axis (the panel is not square to the sensor) and harmonic-rich, so
# a separable notch cannot reach it, and this only ever runs on the bench where
# ~15 ms/patch against a 7.4 s station is free. If it is ever wanted in flight,
# that is the point to spend effort on a steerable IIR comb.

_NOTCH_SPIKES: Optional[np.ndarray] = None   # (n, 2) of (fx, fy) in cycles/px
_NOTCH_RADIUS = 0.012
_NOTCH_MASKS: dict[tuple[int, int], np.ndarray] = {}


def set_raster_notch(spikes, radius: float = 0.012) -> None:
    """Turn the notch on with ``spikes`` = [(fx, fy), ...], or off with ``None``.

    Frequencies are cycles per GREEN-SUB-GRID pixel (that plane is the only one
    scored), which makes them independent of crop size -- masks are rebuilt per
    shape on demand. Calibrate with ``tools/raster_spectrum.py --calibrate``.
    """
    global _NOTCH_SPIKES, _NOTCH_RADIUS
    _NOTCH_MASKS.clear()
    arr = None
    if spikes is not None and len(spikes):
        arr = np.asarray(spikes, dtype=np.float32).reshape(-1, 2)
    _NOTCH_SPIKES = arr
    _NOTCH_RADIUS = float(radius)


def raster_notch_enabled() -> bool:
    return _NOTCH_SPIKES is not None


def _notch_mask(shape: tuple[int, int]) -> np.ndarray:
    """Cached keep-mask over the full (fftshift-free) spectrum of ``shape``."""
    mask = _NOTCH_MASKS.get(shape)
    if mask is not None:
        return mask
    h, w = shape
    fy = np.fft.fftfreq(h).astype(np.float32)[:, None]
    fx = np.fft.fftfreq(w).astype(np.float32)[None, :]
    keep = np.ones((h, w), dtype=np.float32)
    r2 = _NOTCH_RADIUS * _NOTCH_RADIUS
    for sx, sy in _NOTCH_SPIKES:
        # Both signs: the spectrum is conjugate-symmetric, and killing only one
        # lobe leaves half the sinusoid behind as its mirror.
        for sign in (1.0, -1.0):
            # Wrapped difference -- frequency is periodic in 1 cycle/px, so a
            # spike near Nyquist is adjacent to the opposite edge of the grid.
            dx = (fx - sign * sx + 0.5) % 1.0 - 0.5
            dy = (fy - sign * sy + 0.5) % 1.0 - 0.5
            keep[(dx * dx + dy * dy) <= r2] = 0.0
    keep[0, 0] = 1.0  # DC stays: the metric divides by mean^2 downstream
    # Paired with DFT_COMPLEX_OUTPUT, so it has to scale re and im alike.
    mask = np.repeat(keep[:, :, None], 2, axis=2)
    _NOTCH_MASKS[shape] = mask
    return mask


def apply_raster_notch(green: np.ndarray) -> np.ndarray:
    """Remove the calibrated raster spikes from a green-plane image.

    Padded to a DFT-friendly size and run through cv2 rather than numpy: a live
    crop is 1340 px wide, 1340 factors as 2^2 x 5 x 67, and that prime 67 puts
    numpy's transform on its slow path -- 124 ms/patch against 13 ms for the
    whole un-notched metric. The pad is BORDER_REFLECT, not zeros, because a
    frequency-domain multiply wraps around: a hard edge at the seam smears
    broadband energy back across the very spectrum being cleaned.
    """
    if _NOTCH_SPIKES is None:
        return green
    h, w = green.shape
    ph, pw = cv2.getOptimalDFTSize(h), cv2.getOptimalDFTSize(w)
    pad = cv2.copyMakeBorder(green, 0, ph - h, 0, pw - w, cv2.BORDER_REFLECT)
    spec = cv2.dft(pad, flags=cv2.DFT_COMPLEX_OUTPUT)
    spec *= _notch_mask((ph, pw))
    out = cv2.idft(spec, flags=cv2.DFT_SCALE | cv2.DFT_REAL_OUTPUT)
    return np.ascontiguousarray(out[:h, :w])


def focus_measure_full_res(crop: np.ndarray) -> float:
    """Laplacian-variance focus score for a FULL-RES *raw-Bayer* ROI crop.

    A 3x3 Laplacian on the raw mosaic would read the RGGB checkerboard as
    high-frequency "detail" -- a large, focus-*insensitive* pedestal that swamps
    the signal. We do NOT demosaic: bilinear demosaic is a low-pass that
    attenuates the very high frequencies defocus removes, and it costs CPU
    without adding real detail a Bayer sensor never sampled. Instead we score a
    single green sub-grid: mosaic-free by construction (one colour site), real
    sensor samples, no interpolation. Green has 2x the sample density of R/B and
    carries most of the luminance detail. It's half the linear pitch, but focus
    is a *relative* comparison across motor positions, so that's all we need --
    and it's still 2x finer than the 4x-decimated preview :func:`focus_measure`
    scores, which is the whole point of "look at the tile in full res".

    The crop origin is the winning-tile ROI x SUBSAMPLE_FACTOR, i.e. a multiple
    of the (even) decimation stride, so the Bayer phase is preserved and
    ``crop[0::2, 1::2]`` lands on the same colour every frame.

    Having the pipeline cut that sub-grid instead, so this gather disappears, was
    tried on 2026-09-10 and reverted: it is a wash. The gather is ~11 ms of a
    ~30 ms patch, but a column stride reads the same cache lines it would have
    read anyway and gives up the vectorised copy, so what the cut saves the score
    pays back. See ``tools/bench_af_cost.py``.
    """
    if crop is None or crop.ndim < 2 or crop.size == 0:
        return 0.0
    return focus_measure_green(crop[0::2, 1::2])


def focus_measure_green(green: np.ndarray) -> float:
    """:func:`focus_measure_full_res` from the green sub-grid onward.

    Split out because the sub-grid is what several things on disk already hold:
    'Save focus view' writes the green plane, not the Bayer crop, so anything
    re-scoring those files must enter here or it decimates a second time and
    measures a different, aliased image than the engine ever saw.
    """
    if green is None or green.ndim < 2 or green.size == 0:
        return 0.0
    # One pass, not two. The old form shifted right by 6 into a fresh uint16 array and
    # then converted THAT to float, gathering the strided sub-grid twice. The shift was
    # also pointless: the result below divides by mean^2, so any constant scaling of the
    # input cancels exactly -- all >>6 contributed was a truncation that threw away the
    # bottom 6 bits of a 14-bit sample. Measured 11.3 ms -> ~7 ms on a live-sized crop.
    green = green.astype(np.float32)
    # Bench only, no-op unless calibrated: strip the display's own pixel grid
    # before it is scored as detail. Ahead of the blur so the notch sees the
    # spikes at full amplitude, and DC survives it so the mean^2 divide below is
    # still measuring the same brightness.
    green = apply_raster_notch(green)
    # Light pre-blur before the Laplacian: sensor read/shot noise is broadband
    # and the Laplacian (a high-pass) amplifies it hard, which is what put a
    # ~10-15% frame-to-frame spread on the score at a *fixed* lens position
    # (seen in the af-trace). Defocus blurs detail over several pixels, so a
    # sigma~1 smooth costs little real focus signal while cutting the noise
    # floor -- it raises the metric's SNR where it's marginal.
    green = cv2.GaussianBlur(green, (0, 0), 1.0)
    lap = cv2.Laplacian(green, cv2.CV_32F, ksize=3)
    # Normalise by mean^2: a global exposure/gain change scales intensity I->kI,
    # so the Laplacian variance scales by k^2 and mean^2 by k^2 -- the ratio
    # cancels it. Without this, auto-exposure hunting makes the score swing
    # wildly on a *static* scene (~900-1600 seen live), so the hill-climb has no
    # trustworthy gradient and bails at pos +0. +1 guards a near-black tile.
    # cv2's reductions, not numpy's: np.var on a 1.3 Mpx float32 plane measured 13.1 ms
    # against ~2 ms for meanStdDev, and this runs on every AF frame for every patch.
    # meanStdDev is the population sigma, matching np.var's default ddof=0.
    _, lap_sd = cv2.meanStdDev(lap)
    mean = float(cv2.mean(green)[0])
    var = float(lap_sd[0][0]) ** 2
    return float(var / (mean * mean + 1.0) * 1e4)


def _smooth3(values: list[float]) -> list[float]:
    """3-point moving average (edge-clamped). Tames single-sample noise in the
    coarse sweep profile before argmax so one spurious spike can't win."""
    n = len(values)
    if n < 3:
        return list(values)
    out = [0.0] * n
    for i in range(n):
        a = values[i - 1] if i > 0 else values[i]
        b = values[i]
        c = values[i + 1] if i < n - 1 else values[i]
        out[i] = (a + b + c) / 3.0
    return out


def _clamp_roi(roi: Roi, fw: int, fh: int) -> Optional[Roi]:
    x0, y0, x1, y1 = roi
    w = x1 - x0
    h = y1 - y0
    if w < 2 or h < 2 or w > fw or h > fh:
        return None
    x0 = int(round(min(max(0, x0), fw - w)))
    y0 = int(round(min(max(0, y0), fh - h)))
    return (x0, y0, x0 + w, y0 + h)


def _roi_pinned_against_flow(
    clamped: Roi, dx: float, dy: float, fw: int, fh: int, tol: float = 1.0,
) -> bool:
    """True when the ROI is clamped against a frame border AND the scene is still
    flowing further that way -- i.e. the tracked patch has reached the trailing edge
    and content is now sliding under a stuck tile. (The ROI is re-clamped fully inside
    every frame, so it never leaves the frame *entirely*; this pinned-in-motion test is
    what detects 'the object left the FOV', not an overlap/None check.)"""
    x0, y0, x1, y1 = clamped
    return (
        (dx < -tol and x0 <= 0) or (dx > tol and x1 >= fw)
        or (dy < -tol and y0 <= 0) or (dy > tol and y1 >= fh)
    )


class FineFocusTracker:
    """Leading-edge winning-tile tracker for M4/M5 Fine preview (no motor).

    Reuses the same patch tracker + leading-edge tile pick as the AF engine.
    Coarse decimated frames in; ROI in the same pixel space as the frame.
    """

    def __init__(self, cfg: Optional[AutoFocusConfig] = None) -> None:
        self.cfg = cfg or AutoFocusConfig()
        self._tracker = PatchTracker()
        self.roi: Optional[Roi] = None
        self.lead_edge: Optional[str] = None
        self.motion_dx = 0.0
        self.motion_dy = 0.0

    def reset(self) -> None:
        self._tracker.reset()
        self.roi = None
        self.lead_edge = None
        self.motion_dx = 0.0
        self.motion_dy = 0.0

    def update(self, frame: np.ndarray) -> Optional[Roi]:
        """Advance tracking; return coarse-frame ROI or None if not locked yet."""
        if frame is None or frame.ndim < 2:
            return self.roi
        fh, fw = frame.shape[:2]
        dx, dy, response = self._tracker.update(frame)
        self.motion_dx, self.motion_dy = dx, dy

        if self.roi is None:
            lead = leading_edge_from_motion(dx, dy)
            roi, score = select_leading_edge_roi(frame, lead, self.cfg)
            if score < self.cfg.min_texture_score:
                return None
            self.roi = roi
            self.lead_edge = lead
            return roi

        # Static start (centre fallback): upgrade to leading edge once motion appears.
        if self.lead_edge is None:
            new_lead = leading_edge_from_motion(dx, dy)
            if new_lead is not None:
                roi, score = select_leading_edge_roi(frame, new_lead, self.cfg)
                if score >= self.cfg.min_texture_score:
                    self.roi = roi
                    self.lead_edge = new_lead
                    return roi

        moved = (
            self.roi[0] + dx,
            self.roi[1] + dy,
            self.roi[2] + dx,
            self.roi[3] + dy,
        )
        clamped = _clamp_roi(
            (
                int(round(moved[0])),
                int(round(moved[1])),
                int(round(moved[2])),
                int(round(moved[3])),
            ),
            fw,
            fh,
        )
        # Only re-pick when the patch genuinely pans out of the frame. A low
        # tracking response usually means the *image* got blurry (e.g. the user
        # hand-defocusing on the bench, or an AF dither excursion), NOT that the
        # patch moved -- re-picking then makes the tile hop to whatever looks
        # "busiest" at that blur level. Hold the current ROI instead. (Mirrors
        # the AF engine's feed_frame fix.)
        if response >= self.cfg.min_track_response:
            if clamped is None:
                self._reacquire(frame)
                return self.roi
            self.roi = clamped
        return self.roi

    def _reacquire(self, frame: np.ndarray) -> None:
        lead = self.lead_edge or leading_edge_from_motion(
            self.motion_dx, self.motion_dy
        )
        roi, _score = select_leading_edge_roi(frame, lead, self.cfg)
        self.roi = roi
        self.lead_edge = lead
        self._tracker.reset()


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class AFState(Enum):
    IDLE = "idle"
    ACQUIRE = "acquire"
    COARSE = "coarse"     # sweeping the lens to find the global sharpness peak
    SETTLE = "settle"     # skip settle frames after a dither move
    MEASURE = "measure"   # accumulate this arm's frames
    DONE = "done"
    FAILED = "failed"


@dataclass
class _RunState:
    roi: Optional[Roi] = None
    lead_edge: Optional[str] = None
    seed_roi: Optional[Roi] = None  # Fine-preview ROI to focus instead of re-picking
    center: int = 0            # running best-focus estimate (pulses)
    position: int = 0          # actual commanded optical position (pulses)
    last_move_dir: int = 0
    total_pulses: int = 0
    low_texture_frames: int = 0
    measure_buf: list[float] = field(default_factory=list)
    measure_target: int = 1    # frames to combine this measurement (arm or coarse pos)
    settle_left: int = 0
    frames: int = 0
    # Timing diagnostics (wall clock). arm_drive_t = when this arm's move was issued;
    # last_frame_t / frame_dt = feed_frame delivery interval. These expose the
    # measurement-lag the user suspects: how long after a move the score is actually
    # taken, and whether frames arrive steadily or in backed-up bursts.
    arm_drive_t: float = 0.0
    last_frame_t: float = 0.0
    frame_dt: float = 0.0
    # Coarse sweep-to-max acquisition (runs before the dither servo).
    coarse_active: bool = False
    coarse_positions: list = field(default_factory=list)  # centre offsets to visit
    coarse_index: int = 0
    coarse_results: list = field(default_factory=list)     # (position, score)
    coarse_phase: int = 0       # 0 = wide grid, 1 = fine refine around the peak
    coarse_done: bool = False   # a coarse sweep has already run this session
    best_c_score: float = 0.0   # highest averaged CENTRE-arm level seen (snap-back)
    best_c_center: int = 0      # centre position where best_c_score was measured
    motion_mag: float = 0.0     # EMA of per-frame patch motion magnitude (preview px)
    moving_profile: Optional[bool] = None  # current scene-motion profile (None=unset)
    center_lo: Optional[int] = None   # dither-centre clamp after acquisition (leash)
    center_hi: Optional[int] = None
    motion_dx: float = 0.0
    motion_dy: float = 0.0
    # Dither cycle: sample -d, centre, +d in ASCENDING order, all approached going
    # up from a prep point below, so gear lash is taken up identically for all three
    # (no reversal between arms = lash-clean) and the scores are directly comparable.
    # Every arm waits settle_frames after its move so the lens AND the crop pipeline
    # have flushed before measuring (a short settle biased the gradient sign -> the
    # runaway seen on the rig). The centre sample makes it a 3-point mini-curve so a
    # real (concave) peak is distinguishable from the flat defocused floor.
    arm: int = 0                          # 0 = -d, 1 = centre, 2 = +d
    arm_scores: list = field(default_factory=lambda: [None, None, None])
    cycles: int = 0
    locked_cycles: int = 0
    saturated_cycles: int = 0
    flat_wide_cycles: int = 0  # consecutive flat-and-not-a-peak cycles at max probe
    best_score: float = 0.0    # best arm score seen this run (for the report)
    last_gradient: float = 0.0
    dither: int = 0            # current probe half-amplitude (grows when flat)
    slope_seen: bool = False   # have we ever measured a real gradient this run?
    slope_sign: int = 0        # sign of the last real gradient (RProp-style step growth)
    peak_confirmed: bool = False  # have we ever measured a genuine concave peak this run?
    # Averaged over grad_avg_cycles at a fixed centre before the centre is nudged,
    # so a moving scene's per-cycle content error cancels and focus survives.
    minus_samples: list = field(default_factory=list)   # per-cycle score at centre-d
    center_samples: list = field(default_factory=list)  # per-cycle score at centre
    plus_samples: list = field(default_factory=list)    # per-cycle score at centre+d


class AutoFocusEngine:
    """Frame-driven autofocus state machine (Qt-free, fully testable).

    Wire up the actuator callables and feed it one focus frame at a time. The
    engine sends focus pulses / strength / cadence through the callables and
    reports progress + completion through the status/finished callbacks.
    """

    def __init__(
        self,
        *,
        pulse: Callable[[str, int], None],
        set_strength: Optional[Callable[[int], None]] = None,
        adjust_cadence: Optional[Callable[[str], None]] = None,
        status: Optional[Callable[[str], None]] = None,
        finished: Optional[Callable[[bool, dict], None]] = None,
        config: Optional[AutoFocusConfig] = None,
        full_res_score: Optional[Callable[[Roi], Optional[float]]] = None,
        sample: Optional[Callable[[int, float, bool], None]] = None,
    ) -> None:
        self._pulse = pulse
        self._set_strength = set_strength
        self._adjust_cadence = adjust_cadence
        self._status = status or (lambda _m: None)
        self._finished = finished or (lambda _ok, _r: None)
        # Optional live telemetry sink: called with (lens_position_pulses, score,
        # fine_metric) once per settled measurement, for the preview's AF graph
        # overlay. fine_metric flags which sharpness metric produced the score so
        # the graph can scale the coarse and full-res streams independently; the
        # legacy servo runs one metric, so it always reports coarse (False).
        self._sample = sample or (lambda _p, _s, _f=False: None)
        # Optional full-resolution focus scorer for the current ROI. When set
        # and it returns a value, AF scores focus on the FULL-res winning tile
        # (real high frequencies) instead of the 4x-decimated preview -- the
        # preview's low Nyquist blurs out the very detail defocus removes.
        self._full_res_score = full_res_score
        self.cfg = config or AutoFocusConfig()
        self.state = AFState.IDLE
        self._tracker = PatchTracker()
        self._rs = _RunState()
        self._lead_hint: Optional[str] = None
        self._continuous = True  # run forever (flight) vs finish on lock (one-shot)
        # When True, start() baselines to the static profile and the engine promotes to
        # the fast profile on detected motion (the app/flight path). Off by default so
        # tests that set an explicit profile/config are not overridden.
        self.adaptive_profile = False

    # ── lifecycle ───────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self.state not in (AFState.IDLE, AFState.DONE, AFState.FAILED)

    @property
    def current_roi(self) -> Optional[Roi]:
        """The winning-tile ROI the engine is currently optimising (coarse-frame
        pixel coords), or None before lock. Used to overlay it on the preview."""
        return self._rs.roi

    def start(
        self,
        *,
        lead_edge_hint: Optional[str] = None,
        continuous: bool = True,
        seed_roi: Optional[Roi] = None,
    ) -> None:
        self._lead_hint = lead_edge_hint
        self._continuous = bool(continuous)
        self._tracker.reset()
        self._rs = _RunState()
        # Seed target from the Fine preview's held ROI (picked while the operator had
        # the scene sharp). Re-picking on a now-defocused frame favours coarse texture
        # (bushes) over the fine detail the operator actually wants (building/gantry) --
        # the 'Fine picks the building, AF picks bushes' report. Honouring the seed
        # focuses the tile they were looking at instead of re-guessing from blur.
        self._rs.seed_roi = seed_roi
        if self.adaptive_profile:
            # Start on the STATIC (averaged + anchored) profile: the scene's motion isn't
            # known until a few frames of tracking are in, and the averaged profile is the
            # safe default for acquisition. The engine promotes to the fast tracking
            # profile once it measures sustained motion (flight / M5 mirrors) -- see
            # _maybe_switch_profile. This is why AF no longer keys the profile off the
            # mode (M4 is flight = moving in the air, static only on the bench). Resetting
            # here also clears any profile a previous run's adaptation left in cfg.
            self.cfg.apply_scene_profile(moving=False)
            self._rs.moving_profile = False
        self._rs.dither = self.cfg.dither_pulses
        self.state = AFState.ACQUIRE
        if self._set_strength is not None:
            try:
                self._set_strength(self.cfg.strength)
            except Exception:  # pragma: no cover - actuator guard
                log.exception("set_strength failed")
        if self.cfg.drive_cadence_to_min and self._adjust_cadence is not None:
            for _ in range(self.cfg.cadence_steps):
                try:
                    self._adjust_cadence("<")
                except Exception:  # pragma: no cover
                    log.exception("adjust_cadence failed")
                    break
        self._status("autofocus: acquiring target")

    def stop(self, reason: str = "stopped by operator") -> None:
        if not self.running:
            return
        rs = self._rs
        # If we ever locked a patch, rest the lens at the current best-focus
        # centre and finish cleanly (not an error) so it stops sharp.
        if self.state != AFState.ACQUIRE and rs.roi is not None:
            self._rest_upward(rs.center)  # park sharp: land upward like the arms measured
            self.state = AFState.DONE
            self._status(f"autofocus: stopped (centre={rs.center:+d})")
            self._finished(True, {
                "ok": True, "stopped": True, "best_score": rs.best_score,
                "center": rs.center, "final_score": rs.best_score,
                "total_pulses": rs.total_pulses, "frames": rs.frames,
                "lead_edge": rs.lead_edge, "cycles": rs.cycles,
            })
            return
        self._fail(reason)

    # ── per-frame step ──────────────────────────────────────────────────

    def feed_frame(self, frame: np.ndarray) -> None:
        if not self.running:
            return
        if frame is None or frame.ndim < 2:
            return
        rs = self._rs
        rs.frames += 1
        now = time.monotonic()
        rs.frame_dt = (now - rs.last_frame_t) if rs.last_frame_t else 0.0
        rs.last_frame_t = now
        fh, fw = frame.shape[:2]

        dx, dy, response = self._tracker.update(frame)
        rs.motion_dx, rs.motion_dy = dx, dy
        # Rolling estimate of scene motion (only when the correlation is trustworthy --
        # a failed match returns a garbage-large shift). Drives the motion-adaptive
        # profile switch at cycle boundaries (_maybe_switch_profile).
        if response >= self.cfg.min_track_response:
            mag = float(np.hypot(dx, dy))
            a = self.cfg.motion_ema
            rs.motion_mag = (1.0 - a) * rs.motion_mag + a * mag

        # Global "featureless frame" guard (e.g. an oblique camera pointing at
        # sky at the top of its orbit). Pause without disturbing search state;
        # abort only if it persists. Suppressed during the coarse sweep, which
        # deliberately drives the lens deep into blur -- a blurred textured scene
        # legitimately reads low there and must be MEASURED (that's the whole
        # point), not mistaken for a featureless sky and aborted.
        if not rs.coarse_active and self._frame_is_featureless(frame):
            rs.low_texture_frames += 1
            if rs.low_texture_frames > self.cfg.max_low_texture_frames:
                self._fail("low texture (featureless frame)")
            return

        if self.state == AFState.ACQUIRE:
            self._acquire(frame)
            return

        # Track the patch: move ROI with the content so the +d and -d arms score
        # the SAME physical patch (motion-compensated), not a fixed screen region.
        if rs.roi is not None:
            if response >= self.cfg.min_track_response:
                moved = (rs.roi[0] + dx, rs.roi[1] + dy,
                         rs.roi[2] + dx, rs.roi[3] + dy)
                clamped = _clamp_roi(
                    (int(round(moved[0])), int(round(moved[1])),
                     int(round(moved[2])), int(round(moved[3]))),
                    fw, fh,
                )
                # Patch reached the trailing edge: the ROI is pinned against a border
                # and the scene is still flowing that way, so the tracked object is
                # leaving the FOV. Pick a fresh leading-edge patch (else the tile
                # 'sticks' where the object exited) and RESTART the dither cycle -- its
                # arms must all be on the same patch. The focus centre is kept.
                if not rs.coarse_active and (
                    clamped is None or _roi_pinned_against_flow(clamped, dx, dy, fw, fh)
                ):
                    self._reacquire(frame)
                    if self.running and self.state != AFState.ACQUIRE:
                        self._begin_cycle()
                    return
                # During the coarse sweep we compare absolute sharpness across lens
                # positions on the SAME tile, so never re-pick it mid-sweep; just keep
                # it in-frame.
                if clamped is not None:
                    rs.roi = clamped
            # else: low tracking confidence -- e.g. the lens dither is blurring the
            # patch frame-to-frame, which drops the phase-correlation peak. Do NOT
            # reacquire on that (it would re-pick a tile on a blurry frame and can
            # jump to sky); just hold the ROI where it is and keep servoing.

        # Two metrics: `texture` (coarse high-pass std) decides whether there is
        # focusable detail here; `score` (Laplacian variance, full-res if wired)
        # is what the servo differentiates.
        texture = roi_sharpness(frame, rs.roi) if rs.roi is not None else 0.0
        score = self._score(frame, rs.roi)

        # Intermittent low texture (e.g. oblique sky phase): pause -- don't count
        # this frame, hold focus, resume when texture returns. NOT during the coarse
        # sweep: defocus makes the tracked tile read low at off-focus positions, and
        # that low score is exactly the sweep datum we need (aborting here is the
        # 'coarse sweep dies in the blur' bug).
        if not rs.coarse_active and texture < self.cfg.min_texture_score:
            rs.low_texture_frames += 1
            if rs.low_texture_frames > self.cfg.max_low_texture_frames:
                self._fail("low texture (no focusable detail)")
            return
        rs.low_texture_frames = 0

        if self.state == AFState.SETTLE:
            rs.settle_left -= 1
            if rs.settle_left > 0:
                return
            self.state = AFState.MEASURE
            rs.measure_buf = []
            # fall through and measure this frame

        if self.state == AFState.MEASURE:
            rs.measure_buf.append(score)
            if len(rs.measure_buf) < max(1, rs.measure_target):
                return
            arm_score = float(np.median(rs.measure_buf))
            rs.measure_buf = []
            self._sample(rs.position, arm_score, False)  # live graph: pulses vs score
            # Measurement-lag probe: wall-clock gap between issuing this arm's move and
            # actually taking its score, plus the current frame-delivery interval. If the
            # gap is short relative to the move's pulse time, or frame_dt is large/bursty,
            # the score reflects a lens position the move hasn't reached yet -> phantom
            # gradient / drift (the 'delays' the user suspects).
            if not rs.coarse_active:
                log.info(
                    "af-time arm=%d pos=%+d lag=%.2fs settle=%d frame_dt=%.2fs score=%.1f",
                    rs.arm, rs.position, now - rs.arm_drive_t, self.cfg.settle_frames,
                    rs.frame_dt, arm_score,
                )
            if rs.coarse_active:
                self._coarse_measure_done(arm_score)
            else:
                self._arm_measure_done(arm_score)

    # ── internals ───────────────────────────────────────────────────────

    def _frame_is_featureless(self, frame: np.ndarray) -> bool:
        """Cheap whole-frame texture check (decimated std)."""
        fh, fw = frame.shape[:2]
        g = frame[:: max(1, fh // 64), :: max(1, fw // 64)]
        if g.dtype == np.uint16:
            g = (g >> 6).astype(np.float32)
        else:
            g = g.astype(np.float32)
        return float(np.std(g)) < self.cfg.min_texture_score

    def _acquire(self, frame: np.ndarray) -> None:
        rs = self._rs
        # Prefer the Fine-preview seed (a target the operator locked while sharp) over
        # re-picking on a possibly-defocused acquisition frame. Clamp it to the frame in
        # case the preview size differs, then score it like any tile.
        if rs.seed_roi is not None:
            clamped = _clamp_roi(tuple(int(v) for v in rs.seed_roi),
                                 frame.shape[1], frame.shape[0])
            rs.seed_roi = None  # one-shot: a later reacquire re-picks normally
            if clamped is not None:
                roi = clamped
                score = roi_sharpness(frame, roi)
                lead = None
                rs.roi = roi
                rs.lead_edge = lead
                self._dump_select_frame(frame, roi)
                log.info("af-acquire seeded-from-fine roi=%s score=%.1f", roi, score)
                self._status(f"autofocus: locked seeded target (score={score:.1f})")
                self._begin_cycle()
                return
        # Static (full_frame_target): always full-frame search, ignoring any bench
        # jitter. Otherwise honour a manual hint, else the motion-derived edge.
        if self.cfg.full_frame_target:
            lead = None
        else:
            lead = self._lead_hint or leading_edge_from_motion(rs.motion_dx, rs.motion_dy)
        roi, score = select_leading_edge_roi(frame, lead, self.cfg)
        rs.roi = roi
        rs.lead_edge = lead
        # DIAGNOSTIC (ponytail: remove once tile selection is sorted): dump the exact
        # frame the picker scored, so the selection metric can be re-tested offline on
        # the real building/gantry scene instead of tuning blind. One PNG per run,
        # overwritten, at the repo root (the app's cwd).
        self._dump_select_frame(frame, roi)
        if score < self.cfg.min_texture_score:
            rs.low_texture_frames += 1
            if rs.low_texture_frames > self.cfg.max_low_texture_frames:
                self._fail("low texture at acquisition")
            # stay in ACQUIRE; try again next frame (e.g. through sky phase)
            self.state = AFState.ACQUIRE
            return
        rs.low_texture_frames = 0
        edge_txt = lead if lead is not None else "full-frame"
        clipped = _clipped_fraction(
            frame, Tile(roi[0], roi[1], roi[2], roi[3]), self.cfg
        )
        log.info(
            "af-acquire target=%s roi=%s score=%.1f clipped=%.0f%%",
            edge_txt, roi, score, clipped * 100.0,
        )
        self._status(f"autofocus: locked target on {edge_txt} (score={score:.1f})")
        # Start with the dither servo, NOT the coarse sweep: a good-focus start must
        # just be held (tiny wobble), never dragged through a full-range sweep into
        # blur (the 'ran AF from good focus and it deteriorated' report). The servo
        # escalates to the coarse sweep-to-max on its own ONLY when it confirms it
        # can't see a focus slope even at its widest probe (genuine gross defocus).
        self._begin_cycle()

    def _dump_select_frame(self, frame: np.ndarray, roi: Roi) -> None:
        """        Save the picker's input frame (uint8) with the chosen ROI outlined, for
        offline metric testing. Best-effort; never disturbs a run."""
        if min(frame.shape[:2]) < 400:
            return  # synthetic test frames are tiny; only dump real preview frames
        try:
            if frame.dtype == np.uint16:
                img = (frame >> 6).astype(np.uint8)
            else:
                img = frame.astype(np.uint8, copy=True)
            vis = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            x0, y0, x1, y1 = roi
            cv2.rectangle(vis, (x0, y0), (x1, y1), (0, 0, 255), 2)
            path = os.path.join(os.getcwd(), "af_select_frame.png")
            cv2.imwrite(path, img)          # clean copy for re-scoring
            cv2.imwrite(path.replace(".png", "_roi.png"), vis)  # with the pick outlined
            log.info("af-select dumped picker frame -> %s (roi=%s)", path, roi)
        except Exception:  # pragma: no cover - diagnostic only
            log.exception("af-select frame dump failed")

    def _reacquire(self, frame: np.ndarray) -> None:
        """Lock a fresh leading-edge patch (the old one panned out of the FOV),
        keeping the focus centre. The caller restarts the dither cycle."""
        rs = self._rs
        if self.cfg.full_frame_target:
            lead = None
        else:
            lead = self._lead_hint or rs.lead_edge or leading_edge_from_motion(
                rs.motion_dx, rs.motion_dy
            )
        roi, _score = select_leading_edge_roi(frame, lead, self.cfg)
        rs.roi = roi
        self._tracker.reset()

    # ── coarse sweep-to-max acquisition ──────────────────────────────────

    def _begin_coarse(self) -> None:
        """Start the wide sweep-to-max acquisition (phase 0).

        The dither servo can't acquire from gross defocus (flat curve within its
        probe). So first sweep an ascending grid of centre positions spanning
        +-coarse_range around the start, measuring absolute sharpness at each, and
        drive to the global max before handing off. Ascending + a prep point below
        the grid takes gear lash up identically for every sample, so the scores are
        directly comparable (see _begin_cycle for the same lash logic)."""
        rs = self._rs
        cfg = self.cfg
        rs.coarse_active = True
        rs.coarse_phase = 0
        rs.coarse_results = []
        rng = max(cfg.coarse_step_pulses,
                  min(cfg.coarse_range_pulses, cfg.max_center_pulses))
        step = max(1, cfg.coarse_step_pulses)
        start = rs.center
        rs.coarse_positions = list(range(start - rng, start + rng + 1, step))
        rs.coarse_index = 0
        self._status("autofocus: coarse sweep (finding focus)")
        self._start_coarse_position(prep=True)

    def _start_coarse_position(self, *, prep: bool) -> None:
        rs = self._rs
        cfg = self.cfg
        if prep:
            first = rs.coarse_positions[0]
            prep_pos = first - max(1, cfg.backlash_pulses)
            delta = int(prep_pos - rs.position)
            if delta != 0:
                self._drive(1 if delta > 0 else -1, abs(delta))
        target = rs.coarse_positions[rs.coarse_index]
        delta = int(target - rs.position)
        if delta != 0:
            self._drive(1 if delta > 0 else -1, abs(delta))
        self.state = AFState.SETTLE
        rs.settle_left = cfg.coarse_settle_frames
        rs.measure_target = cfg.coarse_measure_frames
        rs.measure_buf = []

    def _coarse_measure_done(self, score: float) -> None:
        rs = self._rs
        pos = rs.coarse_positions[rs.coarse_index]
        rs.coarse_results.append((pos, score))
        rs.best_score = max(rs.best_score, score)
        rs.coarse_index += 1
        if rs.coarse_index < len(rs.coarse_positions):
            self._start_coarse_position(prep=False)
            return
        self._coarse_phase_done()

    def _coarse_phase_done(self) -> None:
        rs = self._rs
        cfg = self.cfg
        positions = [p for p, _ in rs.coarse_results]
        scores = [s for _, s in rs.coarse_results]
        # 3-point smooth before argmax so one noisy sample can't win the sweep.
        sm = _smooth3(scores)
        best_i = int(np.argmax(sm))
        best_pos = positions[best_i]
        smax, smin = max(sm), min(sm)
        contrast = (smax - smin) / smax if smax > 1e-6 else 0.0
        log.info(
            "af-coarse phase=%d n=%d best_pos=%+d score=%.1f contrast=%.2f "
            "range=[%+d,%+d]",
            rs.coarse_phase, len(scores), best_pos, scores[best_i], contrast,
            positions[0], positions[-1],
        )
        if rs.coarse_phase == 0:
            if contrast < cfg.coarse_min_contrast:
                # Flat across the WHOLE range: the tile has no focusable detail at any
                # position (not a defocus problem). Fail instead of driving to a noise
                # max. stop()/_fail leave the lens where it is; the start was one sample.
                self._fail("no focus peak found in coarse sweep")
                return
            rs.coarse_phase = 1
            rs.coarse_results = []
            lo = best_pos - cfg.coarse_step_pulses
            hi = best_pos + cfg.coarse_step_pulses
            rs.coarse_positions = list(range(lo, hi + 1, max(1, cfg.coarse_refine_step)))
            rs.coarse_index = 0
            self._status(f"autofocus: coarse peak ~{best_pos:+d}; refining")
            self._start_coarse_position(prep=True)
            return
        # Refine done: adopt the peak, leash the dither servo around it (so a phantom
        # gradient can't walk it back into blur), and hand off to the servo to hold.
        rs.coarse_active = False
        rs.coarse_done = True
        rs.center = best_pos
        rs.center_lo = best_pos - cfg.coarse_servo_leash_pulses
        rs.center_hi = best_pos + cfg.coarse_servo_leash_pulses
        rs.dither = cfg.dither_pulses
        # Drop the pre-coarse snap-back anchor: the old 'best' was measured in the tiny
        # window the servo just failed in, so it must not pull the servo off the freshly
        # acquired global peak. The next centre cycle re-establishes best at best_pos.
        rs.best_c_score = 0.0
        rs.best_c_center = best_pos
        self._status(f"autofocus: acquired focus at centre={best_pos:+d}")
        self._begin_cycle()

    # ── continuous dither servo ─────────────────────────────────────────

    def _maybe_switch_profile(self) -> None:
        """Promote/demote the servo profile from measured scene motion (hysteresis).

        The cycle length must follow the SCENE, not the mode: M4 (flight) is moving in
        the air but static on the bench; M5 (bench flight-sim) is moving. A static scene
        needs the long averaged + anchored cycle (kills the phantom-gradient walk); a
        moving scene needs the short per-patch cycle (else the patch pans out before a
        nudge lands). Called at cycle boundaries so params change only between cycles.
        """
        rs = self._rs
        cfg = self.cfg
        if rs.moving_profile is None:
            return
        want = rs.moving_profile
        if not rs.moving_profile and rs.motion_mag >= cfg.motion_on_px:
            want = True
        elif rs.moving_profile and rs.motion_mag <= cfg.motion_off_px:
            want = False
        if want != rs.moving_profile:
            rs.moving_profile = want
            cfg.apply_scene_profile(moving=want)
            # A profile switch re-arms/disarms snap-back; drop the stale best anchor so a
            # peak measured under the other regime can't fire a bad snap-back.
            rs.best_c_score = 0.0
            self._status(
                f"autofocus: scene {'moving' if want else 'static'} "
                f"(motion={rs.motion_mag:.1f}px) -> "
                f"{'fast-track' if want else 'averaged'} profile"
            )

    def _begin_cycle(self) -> None:
        """Start a new dither cycle. First drive BELOW the low arm (prep), then
        sample centre-d and centre+d both approaching upward -- so brushed-DC gear
        lash is taken up identically for both and the +d/-d scores are directly
        comparable. (Alternating the arm order instead makes each arm land in a
        different lash state, so the gradient flips every cycle and never locks.)
        """
        rs = self._rs
        cfg = self.cfg
        self._maybe_switch_profile()
        if rs.dither <= 0:
            rs.dither = cfg.dither_pulses
        rs.arm = 0
        rs.arm_scores = [None, None, None]
        prep = rs.center - rs.dither - max(1, cfg.backlash_pulses)
        delta = int(prep - rs.position)
        if delta != 0:
            self._drive(1 if delta > 0 else -1, abs(delta))
        self._go_to_arm()

    # -d, centre, +d -- ascending so all three share the same lash take-up.
    _ARM_OFFSETS = (-1, 0, 1)

    def _go_to_arm(self) -> None:
        rs = self._rs
        offset = self._ARM_OFFSETS[rs.arm] * rs.dither
        target = rs.center + offset
        delta = int(target - rs.position)
        if delta != 0:
            self._drive(1 if delta > 0 else -1, abs(delta))
        self.state = AFState.SETTLE
        rs.arm_drive_t = time.monotonic()  # for the measurement-lag diagnostic
        # Same generous settle on every arm -- an asymmetric settle biases the +d/-d
        # gradient sign (see settle_frames note), which is exactly the runaway bug.
        rs.settle_left = self.cfg.settle_frames
        rs.measure_target = self.cfg.measure_frames
        rs.measure_buf = []

    def _arm_measure_done(self, arm_score: float) -> None:
        rs = self._rs
        rs.arm_scores[rs.arm] = arm_score
        rs.best_score = max(rs.best_score, arm_score)
        if rs.arm < 2:
            rs.arm += 1
            self._go_to_arm()
            return
        self._servo_update()

    def _servo_update(self) -> None:
        """One 3-point dither cycle done. Fold its gradient + peakedness into the
        running average; once ``grad_avg_cycles`` are in, nudge the centre and
        decide slope / peak / floor. Averaging cancels a moving scene's per-cycle
        content error; the 3-point peak test stops it locking on the blurred floor."""
        rs = self._rs
        cfg = self.cfg
        s_minus, s_center, s_plus = rs.arm_scores  # -d, centre, +d
        rs.minus_samples.append(s_minus)
        rs.center_samples.append(s_center)
        rs.plus_samples.append(s_plus)
        # Per-raw-cycle trace: repeated reads at the SAME centre. Stable numbers ->
        # a real slope; jumpy numbers -> we're measuring mid-motion / vibration.
        log.info(
            "af-raw centre=%+d dither=%d n=%d/%d s(-,0,+)=%.1f/%.1f/%.1f",
            rs.center, rs.dither, len(rs.center_samples), cfg.grad_avg_cycles,
            s_minus, s_center, s_plus,
        )

        # Keep re-measuring at the SAME centre until we have enough cycles to average.
        if len(rs.center_samples) < max(1, cfg.grad_avg_cycles):
            self._begin_cycle()
            return

        # Averaged arm levels: averaging over cycles cancels a moving scene's per-cycle
        # content/noise error, leaving the consistent focus signal.
        m = float(np.mean(rs.minus_samples))
        c = float(np.mean(rs.center_samples))
        p = float(np.mean(rs.plus_samples))
        rs.minus_samples = []
        rs.center_samples = []
        rs.plus_samples = []
        rs.cycles += 1

        # A STATIC scene (bench M4, or flight hovering) must FIND focus and STOP: the
        # encoderless motor loses open-loop position on every dither round-trip, so
        # continuing to dither a still scene walks the found focus into blur (the live
        # 86->12 collapse + drift to -244). A MOVING scene (flight cruise, M5 mirrors)
        # keeps servoing -- the scene itself churns, so a stale park is useless and the
        # constant re-measurement corrects the drift. moving_profile is None only when
        # motion-adaptation is off (tests): keep the old continuous/one-shot semantics.
        static_scene = rs.moving_profile is False

        # Best-absolute anchor. The differential gradient can be fooled by a phantom
        # +d>-d bias on the encoderless motor (all arms approach upward, so +d reads
        # high even AT the peak), walking the centre DOWN off a good focus while '+'
        # keeps looking uphill (the live static 153->18 walk). The absolute centre
        # level does NOT lie: track the best seen, and if we have fallen well below it
        # the peak is behind us -- return to it instead of chasing the phantom.
        if c > rs.best_c_score:
            rs.best_c_score = c
            rs.best_c_center = rs.center
        if (cfg.snap_back_frac > 0.0 and rs.best_c_score > 1e-6
                and c < cfg.snap_back_frac * rs.best_c_score):
            log.info(
                "af-servo snap-back: c=%.1f < %.2f*best=%.1f -> centre %+d",
                c, cfg.snap_back_frac, rs.best_c_score, rs.best_c_center,
            )
            rs.center = rs.best_c_center
            # Falling below best means the fine servo's differential gradient can't be
            # trusted here: it either chased a phantom lash bias (+d reads high because
            # every arm is approached upward) or the TRUE peak sits outside its tiny
            # dither window. If we NEVER confirmed a real concave peak this run, that is
            # gross defocus -> one wide coarse sweep-to-max recovers the far focus (the
            # live 'declared in focus at ~half score; true focus 3 str3 clicks away'). But
            # if a genuine peak WAS confirmed (rs.peak_confirmed), focus is right here and
            # the phantom merely nudged us off it -- just return to best and stop; a full
            # +-400 sweep there would thrash a good focus through blur (the live 'strong
            # peak at centre 0, then coarse swept the whole range' -- what the hell). Only
            # once per run (coarse_done) either way.
            if (cfg.coarse_enable and not rs.coarse_done
                    and not rs.peak_confirmed):
                self._status("autofocus: below best -- coarse sweep to verify focus")
                self._begin_coarse()
                return
            self._status(f"autofocus: returned to best focus (centre={rs.center:+d})")
            if not self._continuous or static_scene:
                self._complete()  # lands upward at best_c_center (see _rest_upward)
                return
            # Continuous scene: re-baseline so a genuinely changing scene can set a new
            # best rather than being pinned to a stale one, then keep tracking. The
            # next cycle's prep drive repositions the lens; no manual move needed here.
            rs.best_c_score = c
            rs.dither = cfg.dither_pulses
            self._begin_cycle()
            return

        denom = p + m
        # Normalised gradient in [-1, 1]: content-scale-invariant (a busier/dimmer
        # patch doesn't change it -- only the +d vs -d contrast, i.e. focus, does).
        g_avg = (p - m) / denom if denom > 1e-6 else 0.0
        # Peakedness: how much the centre rises above the arms' mean. >0 = concave.
        peak_avg = (c - 0.5 * denom) / c if c > 1e-6 else 0.0
        # The unambiguous "at the peak" test: the centre outscores BOTH neighbours, so
        # moving either way is worse. This settles a sharp peak that a small dither
        # straddles (where the gradient stays large and would otherwise flip-flop +-d
        # forever) -- the sign of g is moot once the centre is the local max.
        center_is_max = c >= p and c >= m
        rs.last_gradient = g_avg
        # A genuine concave peak anywhere this run (even at the wide probe: a big
        # centre-vs-arms contrast is strong evidence focus is right here). Once set, a
        # later snap-back returns to best instead of launching a disruptive coarse sweep.
        if center_is_max and peak_avg >= cfg.peak_frac:
            rs.peak_confirmed = True

        at_max_probe = rs.dither >= cfg.dither_max_pulses
        # Peakedness is only a real FOCUS signal at the fine dither. At the wide probe the
        # centre beats the far-flung +-dither arms even when it is well off the true peak
        # (they sit out on the falloff tails), so "centre > both arms" there just means
        # "narrow the probe", NOT "locked on focus" -- counting it as a lock parks off
        # peak (the test caught this: it locked at the start on the wide probe).
        at_fine = rs.dither <= cfg.dither_pulses
        # An unambiguous concave top at the FINE dither: settle NOW rather than waiting for
        # a lock streak that per-cycle noise keeps resetting.
        strong_peak = at_fine and center_is_max and peak_avg >= cfg.strong_peak_frac
        # Concave top measurable at this dither, OR (option A) a flat top we recognise by
        # its absolute level: gradient dead AND the centre sits on the high plateau near
        # the best seen (having climbed here). The anchor separates peak-flat from
        # floor-flat, so a peak too flat to reach peak_frac at +-2 still settles instead
        # of hunting on drift.
        concave_peak = peak_avg >= cfg.peak_frac and (
            center_is_max or abs(g_avg) < cfg.servo_deadband
        )
        anchored_peak = (
            rs.slope_seen and abs(g_avg) < cfg.servo_deadband
            and rs.best_c_score > 1e-6 and c >= cfg.lock_anchor_frac * rs.best_c_score
        )
        if concave_peak or anchored_peak:
            # Concave top with the centre at (or indistinguishable from) the max:
            # genuine focus. Settle to the base probe. Count toward lock ONLY at the fine
            # dither (see at_fine) -- a wide-probe peak just narrows the search.
            step = 0
            rs.dither = cfg.dither_pulses
            rs.slope_seen = True
            rs.slope_sign = 0  # on the peak: next slope starts a fresh RProp run
            rs.locked_cycles = rs.locked_cycles + 1 if at_fine else 0
            rs.flat_wide_cycles = 0
        elif abs(g_avg) >= cfg.servo_deadband:
            # Real averaged slope: step toward it and reset the lock counter. The step
            # size adapts RProp-style to the CONSISTENCY of the direction (below).
            rs.slope_seen = True
            # Never step further than the probe half-width we just MEASURED over: beyond
            # that we'd move blind past the sampled span, overshooting a steep peak into a
            # flip-flop limit cycle (the +-4 swing seen in sim).
            measured_dither = rs.dither
            max_nudge = min(cfg.servo_max_nudge, measured_dither)
            step = int(round(cfg.servo_gain * g_avg * measured_dither))
            step = max(-max_nudge, min(max_nudge, step))
            if step == 0:
                step = 1 if g_avg > 0 else -1
            # RProp-style adaptive probe. The old code shrank the probe on EVERY slope and
            # capped the step to it, so a clear, consistent slope collapsed to a +-1 crawl
            # and just saw-toothed uphill (the live 'positive kept raising the score, it
            # should have pushed up more'). Instead: while the gradient keeps the SAME
            # sign we under-shot the peak -> GROW the probe so the next step commits harder;
            # only when the sign FLIPS (we straddled the peak) shrink to refine. This
            # climbs a real slope decisively yet still homes in once it's on top.
            sign = 1 if g_avg > 0 else -1
            if sign == rs.slope_sign:
                rs.dither = min(cfg.dither_max_pulses, measured_dither + cfg.dither_pulses)
            else:
                rs.dither = max(cfg.dither_pulses, measured_dither // 2)
            rs.slope_sign = sign
            # A single-pulse trim ON a concave top is the resolution floor -- count it
            # toward lock (a moving scene keeps nudging +-1 forever otherwise). A big
            # step, or any step on a non-concave slope, is still hunting -> reset.
            if abs(step) <= 1 and peak_avg >= cfg.peak_frac:
                rs.locked_cycles += 1
            else:
                rs.locked_cycles = 0
            rs.flat_wide_cycles = 0
        else:
            # Flat but NOT concave: the defocused floor / a plateau. Never lock here
            # (the rig bug).
            step = 0
            rs.locked_cycles = 0
            rs.slope_sign = 0  # no slope: next real gradient starts a fresh RProp run
            if at_max_probe:
                # The fine servo can't see a slope even at its widest probe -> we are
                # grossly defocused (flat far-field), beyond dither reach. Escalate to
                # the coarse sweep-to-max ONCE to acquire the peak, then the servo
                # resumes near it (leashed). This is why a GOOD-focus start is never
                # swept: only a confirmed-flat start triggers coarse. If coarse already
                # ran and it is STILL flat here, there is genuinely no peak -> fail.
                # NEVER coarse-sweep a MOVING scene: a wide absolute-score sweep is
                # meaningless while the scene changes under it, and worse, it fires
                # hundreds of str0 pulses -- each a blocking 10 ms firmware delay() that
                # stalls the M5 mirror sweep and starves frame delivery (the live 'M5
                # slowed down very much -- the delay thing again'). On a moving scene a
                # missing slope just means this patch has no focusable detail; keep
                # tracking the next patch instead of sweeping.
                moving_scene = rs.moving_profile is True
                if cfg.coarse_enable and not rs.coarse_done and not moving_scene:
                    self._status("autofocus: no slope in probe -- coarse sweep")
                    self._begin_coarse()
                    return
                rs.dither = cfg.dither_max_pulses
                rs.flat_wide_cycles += 1
                if rs.flat_wide_cycles >= 2 * cfg.servo_lock_cycles:
                    self._fail("no focus peak found in range")
                    return
            else:
                # Jump straight to the widest probe instead of crawling 2->4->8->16->32
                # over many slow cycles: from a flat small dither we want to learn fast
                # whether a wide probe sees the slope (hill-climb) or we must escalate
                # to the coarse sweep.
                rs.dither = cfg.dither_max_pulses

        new_center = rs.center + step
        if abs(new_center) > cfg.max_center_pulses:
            new_center = max(-cfg.max_center_pulses,
                             min(cfg.max_center_pulses, new_center))
            rs.saturated_cycles += 1
            if rs.saturated_cycles >= cfg.servo_lock_cycles:
                self._fail("focus out of range (servo saturated)")
                return
        else:
            rs.saturated_cycles = 0
        # Leash: after a coarse acquisition the servo may only wander +-leash from the
        # swept optimum, so a phantom gradient (stale/noisy score) can't walk a good
        # focus into garbage (the live 2800->1300 runaway).
        if rs.center_lo is not None:
            new_center = max(rs.center_lo, min(rs.center_hi, new_center))
        rs.center = new_center

        log.info(
            "af-servo cycle=%d g=%+.3f peak=%+.3f cmax=%d avg(-,0,+)=%.1f/%.1f/%.1f "
            "step=%+d centre=%+d dither=%d locked=%d motion=%.1fpx %s",
            rs.cycles, g_avg, peak_avg, int(center_is_max), m, c, p,
            step, rs.center, rs.dither, rs.locked_cycles, rs.motion_mag,
            "moving" if rs.moving_profile else "static",
        )

        locked = rs.locked_cycles >= cfg.servo_lock_cycles or strong_peak
        self._status(
            f"autofocus: {'in focus' if locked else 'focusing'} "
            f"(centre={rs.center:+d}, g={g_avg:+.2f})"
        )
        # Stop when locked on a STATIC scene (park sharp, no more open-loop drift) or on
        # any one-shot run; a moving scene keeps tracking.
        if locked and (static_scene or not self._continuous):
            self._complete()
            return
        self._begin_cycle()

    def _drive(self, direction: int, count: int) -> None:
        """Send ``count`` pulses in ``direction`` (+1/-1), with backlash
        compensation on a direction reversal, and update bookkeeping."""
        rs = self._rs
        cfg = self.cfg
        sym = "+" if direction > 0 else "-"
        if rs.last_move_dir != 0 and direction != rs.last_move_dir and cfg.backlash_pulses > 0:
            self._safe_pulse(sym, cfg.backlash_pulses)
            rs.total_pulses += cfg.backlash_pulses
        self._safe_pulse(sym, count)
        rs.position += direction * count
        rs.total_pulses += count
        rs.last_move_dir = direction

    def _safe_pulse(self, sym: str, count: int) -> None:
        try:
            self._pulse(sym, int(count))
        except Exception:  # pragma: no cover - actuator guard
            log.exception("focus pulse failed")

    def _score(self, frame: Optional[np.ndarray], roi: Optional[Roi]) -> float:
        """Focus score for ``roi``: full-res tile if a scorer is wired and has a
        fresh crop, else the decimated-preview Laplacian variance."""
        if roi is None:
            return 0.0
        if self._full_res_score is not None:
            try:
                s = self._full_res_score(roi)
            except Exception:  # pragma: no cover - UI guard
                log.exception("full_res_score failed")
                s = None
            if s is not None:
                return float(s)
        if frame is None:
            return 0.0
        return focus_measure(frame, roi)

    def _rest_upward(self, target: int) -> None:
        """Bring the lens to rest at ``target`` on a strictly UPWARD final move.

        Every dither arm is measured approaching upward (-d/0/+d are all reached by
        driving up), so 'best = target' was measured with the gear lash seated upward.
        If we then land on ``target`` coming DOWN (e.g. from the wide-probe +d arm),
        the lash reseats the other way and the lens parks a hair off the measured
        focus -- the live 'parked-still image blurrier than the servo measured' bug.
        Overshoot just below and come up so the resting lash state matches the arms.
        """
        rs = self._rs
        lash = max(1, self.cfg.backlash_pulses)
        below = target - lash
        d = int(below - rs.position)
        if d != 0:
            self._drive(1 if d > 0 else -1, abs(d))
        self._drive(1, lash)  # final leg upward -> lash seated like the measurements

    def _complete(self) -> None:
        """One-shot run reached the in-focus deadband: rest the lens at the
        centre (approaching upward, see _rest_upward) and report."""
        rs = self._rs
        self._rest_upward(rs.center)
        self.state = AFState.DONE
        report = {
            "ok": True,
            "best_score": rs.best_score,
            "center": rs.center,
            "final_score": rs.best_score,
            "total_pulses": rs.total_pulses,
            "frames": rs.frames,
            "lead_edge": rs.lead_edge,
            "cycles": rs.cycles,
        }
        self._status(
            f"autofocus: done (in focus, centre={rs.center:+d} pulses, "
            f"cycles={rs.cycles})"
        )
        self._finished(True, report)

    def _fail(self, reason: str) -> None:
        rs = self._rs
        self.state = AFState.FAILED
        report = {
            "ok": False,
            "reason": reason,
            "best_score": rs.best_score,
            "center": rs.center,
            "total_pulses": rs.total_pulses,
            "frames": rs.frames,
        }
        self._status(f"autofocus: FAILED ({reason})")
        self._finished(False, report)


# ---------------------------------------------------------------------------
# Qt controller
# ---------------------------------------------------------------------------

try:  # pragma: no cover - exercised only inside the GUI app
    from PySide6.QtCore import QObject, Signal

    class AutoFocusController(QObject):
        """Qt wrapper: binds :class:`AutoFocusEngine` to the app's serial /
        mode / camera plumbing and sequences cameras for "Auto-focus all".

        The app feeds each M4 focus frame for the active camera into
        :meth:`feed_frame`; the controller forwards it to the engine while a
        run is active.
        """

        status = Signal(str)
        finished = Signal(bool, dict)        # one camera completed
        run_state_changed = Signal(bool)     # True = a run (single or all) is active
        sample = Signal(int, float, bool)    # (lens pulses, score, fine_metric) per measurement

        def __init__(
            self,
            serial,
            modes,
            *,
            select_camera_cb: Optional[Callable[[int], None]] = None,
            set_roi_cb: Optional[Callable[[Optional[Roi]], None]] = None,
            full_res_score_cb: Optional[Callable[[Roi], Optional[float]]] = None,
            full_res_pair_cb: Optional[Callable[[Roi, Roi], tuple]] = None,
            full_res_multi_cb: Optional[Callable[[list], Optional[list]]] = None,
            observe_row_cb: Optional[Callable[[dict], None]] = None,
            profile_cb: Optional[Callable[[dict], None]] = None,
            config: Optional[AutoFocusConfig] = None,
            algorithm: str = "sweep_and_hold",
            encoder_read: Optional[Callable[[], float]] = None,
            encoder_present: Optional[Callable[[], bool]] = None,
            parent: Optional[QObject] = None,
        ) -> None:
            super().__init__(parent)
            self._serial = serial
            self._modes = modes
            self._select_camera_cb = select_camera_cb
            self._set_roi_cb = set_roi_cb
            self._full_res_score_cb = full_res_score_cb
            self._full_res_pair_cb = full_res_pair_cb
            self._full_res_multi_cb = full_res_multi_cb
            self._observe_row_cb = observe_row_cb
            self._profile_cb = profile_cb   # AF3 only: live station-profile preview
            self._config = config
            self._encoder_read = encoder_read
            self._encoder_present = encoder_present
            self._algorithm = self._normalise_algorithm(algorithm)
            self._engine = self._build_engine(self._algorithm)
            self._sequence: list[int] = []
            self._seq_index = 0
            self._continuous = True
            self._seq_lead_hint: Optional[str] = None

        @staticmethod
        def _normalise_algorithm(name: str) -> str:
            key = str(name).lower()
            if key in ("field_profile", "field-profile", "af3", "v3"):
                return "field_profile"
            if key in ("sweep_and_hold", "sweep-and-hold", "v2"):
                return "sweep_and_hold"
            return "legacy"

        def _build_engine(self, algorithm: str):
            """Construct the selected AF engine, bound to the same plumbing.

            Both engines expose the same duck-typed surface, so everything the
            controller does (start/stop/feed_frame, sequencing, telemetry) is
            identical regardless of which is active -- that is what makes the two
            side-by-side A/B testable."""
            common = dict(
                pulse=self._serial.focus_step,
                set_strength=self._serial.set_strength,
                adjust_cadence=self._serial.adjust_trigger_period,
                status=self.status.emit,
                finished=self._on_engine_finished,
                full_res_score=self._full_res_score_cb,
                sample=self.sample.emit,
            )
            if algorithm == "field_profile":
                # AF3: same skeleton as v2 (it subclasses it), but the climb measures
                # the across-field profile instead of one patch's score. Kept a
                # separate algorithm string so reverting to v2 is a one-line change.
                from .autofocus_v3 import AutoFocusConfigV3, AutoFocusEngineV3

                cfg = self._config if isinstance(self._config, AutoFocusConfigV3) else None
                engine = AutoFocusEngineV3(
                    config=cfg, axis=self._maybe_encoder_axis(cfg),
                    full_res_pair=self._full_res_pair_cb,
                    full_res_multi=self._full_res_multi_cb,
                    observe_row=self._observe_row_cb,
                    profile_cb=self._profile_cb, **common
                )
            elif algorithm == "sweep_and_hold":
                # Lazy import breaks the autofocus <-> autofocus_v2 cycle (v2 reuses
                # this module's tracker/metrics at import time).
                from .autofocus_v2 import AutoFocusConfigV2, AutoFocusEngineV2

                cfg = self._config if isinstance(self._config, AutoFocusConfigV2) else None
                # full_res_pair is v2-only: the legacy engine has no cross-object
                # handover, so there is no A/B ratio for it to calibrate.
                engine = AutoFocusEngineV2(
                    config=cfg, axis=self._maybe_encoder_axis(cfg),
                    full_res_pair=self._full_res_pair_cb,
                    full_res_multi=self._full_res_multi_cb,
                    observe_row=self._observe_row_cb, **common
                )
            else:
                cfg = self._config if isinstance(self._config, AutoFocusConfig) else None
                engine = AutoFocusEngine(config=cfg, **common)
            # Live/flight AF: let the (legacy) engine pick its servo profile from
            # detected scene motion. v2 accepts+ignores this (single sweep-and-hold).
            engine.adaptive_profile = True
            return engine

        def _maybe_encoder_axis(self, cfg):
            """Build an :class:`EncoderAxis` when the encoder is present, else ``None``
            (engine falls back to its open-loop pulse-count axis). The rig always has
            the encoder, so this only returns ``None`` when it reports absent ($ENC=-1
            -> ``encoder_present`` False)."""
            if self._encoder_read is None:
                return None
            if self._encoder_present is not None and not self._encoder_present():
                self.status.emit("autofocus: encoder reads absent ($ENC=-1) -- open-loop")
                return None
            from .autofocus_v2 import AutoFocusConfigV2
            from .focus_axis import EncoderAxis

            c = cfg or AutoFocusConfigV2()
            return EncoderAxis(
                self._serial.focus_step,
                self._encoder_read,
                counts_per_pulse=getattr(c, "encoder_counts_per_pulse", 1.0),
                backlash_pulses=getattr(c, "backlash_pulses", 3),
            )

        def _refresh_encoder_axis(self) -> None:
            """Pick the axis for THIS run from the encoder's current presence.

            The engine is built once at startup (before ``$ENC`` has streamed, so the
            encoder reads absent then); re-choosing at each run start means a present
            encoder is used without needing a UI toggle, and a genuinely dead one
            ($ENC=-1) still falls back to open-loop. Only the v2 engine has a swappable
            FocusAxis."""
            if hasattr(self._engine, "set_axis"):
                self._engine.set_axis(self._maybe_encoder_axis(self._engine.cfg))

        @property
        def algorithm(self) -> str:
            return self._algorithm

        def set_algorithm(self, name: str) -> None:
            """Swap the active AF engine (only while idle). Used by the focus-panel
            'AF algorithm' selector to A/B test the legacy dither servo vs the new
            sweep-and-hold engine."""
            new = self._normalise_algorithm(name)
            if new == self._algorithm:
                return
            if self._engine.running:
                self.status.emit(
                    "autofocus: stop the current run before switching algorithm"
                )
                return
            self._algorithm = new
            self._engine = self._build_engine(new)
            self.status.emit(f"autofocus: algorithm = {new}")

        @property
        def config(self):
            return self._engine.cfg

        def is_running(self) -> bool:
            return self._engine.running

        # ── start / stop ────────────────────────────────────────────────

        def start_single(
            self,
            *,
            lead_edge_hint: Optional[str] = None,
            continuous: bool = True,
            seed_roi: Optional[Roi] = None,
        ) -> bool:
            """Run the dither servo on the active camera. ``continuous`` (default)
            keeps servoing focus as the scene flows by; set False to finish once
            it first locks (used by ``start_all``). ``seed_roi`` focuses a
            caller-supplied tile (the Fine preview's held target) instead of
            re-picking on the acquisition frame."""
            if not self._guard():
                return False
            self._sequence = []
            self._continuous = continuous
            self._seq_lead_hint = lead_edge_hint
            self._refresh_encoder_axis()
            self.run_state_changed.emit(True)
            self._engine.start(lead_edge_hint=lead_edge_hint, continuous=continuous,
                               seed_roi=seed_roi)
            return True

        def start_all(
            self,
            cameras: list[int],
            *,
            lead_edge_hint: Optional[str] = None,
        ) -> bool:
            if not self._guard():
                return False
            if not cameras:
                self.status.emit("autofocus: no cameras available")
                return False
            self._sequence = list(cameras)
            self._seq_index = 0
            self._continuous = False  # focus each camera once, then advance
            self._seq_lead_hint = lead_edge_hint
            self._refresh_encoder_axis()
            self.run_state_changed.emit(True)
            self._select_for_sequence()
            self._engine.start(lead_edge_hint=lead_edge_hint, continuous=False)
            return True

        def start_observe(self, *, lead_edge_hint: Optional[str] = None) -> bool:
            """Diagnostic: watch several patches at a fixed lens position (v2 only)."""
            if not hasattr(self._engine, "start_observe"):
                self.status.emit("observe: needs the v2 engine")
                return False
            if not self._guard():
                return False
            self._sequence = []
            self._continuous = False
            self._seq_lead_hint = lead_edge_hint
            self._refresh_encoder_axis()
            self.run_state_changed.emit(True)
            self._engine.start_observe(lead_edge_hint=lead_edge_hint)
            return True

        def stop(self, reason: str = "stopped by operator") -> None:
            self._sequence = []
            self._engine.stop(reason)

        def step(self) -> None:
            """Release the engine's debug phase gate (operator SPACE), if any."""
            fn = getattr(self._engine, "step", None)
            if callable(fn):
                fn()

        def feed_frame(self, frame) -> None:
            if self._engine.running:
                self._engine.feed_frame(frame)
                # Surface the locked winning tile so the operator can see which
                # patch AF is optimising (drawn as an overlay on the preview).
                if self._set_roi_cb is not None:
                    try:
                        self._set_roi_cb(self._engine.current_roi)
                    except Exception:  # pragma: no cover - UI guard
                        log.exception("set_roi_cb failed")

        # ── internals ────────────────────────────────────────────────────

        def _guard(self) -> bool:
            if self._engine.running:
                self.status.emit("autofocus: already running")
                return False
            if self._modes is not None and not self._modes.is_af_mode():
                self.status.emit(
                    "autofocus: only available in M4 (Focus) or M5 (Ground Test) mode"
                )
                return False
            if not getattr(self._serial, "connected", False):
                self.status.emit("autofocus: PCB not connected")
                return False
            return True

        def _select_for_sequence(self) -> None:
            if 0 <= self._seq_index < len(self._sequence) and self._select_camera_cb:
                cam = self._sequence[self._seq_index]
                try:
                    self._select_camera_cb(cam)
                except Exception:
                    log.exception("select_camera_cb failed")

        def _on_engine_finished(self, ok: bool, report: dict) -> None:
            if self._set_roi_cb:
                try:
                    self._set_roi_cb(None)
                except Exception:
                    log.exception("set_roi_cb clear failed")
            self.finished.emit(ok, report)
            # Advance the "Auto-focus all" sequence if one is in progress.
            if self._sequence and self._seq_index + 1 < len(self._sequence):
                self._seq_index += 1
                self._select_for_sequence()
                self._engine.start(
                    lead_edge_hint=self._seq_lead_hint, continuous=False
                )
            else:
                self._sequence = []
                self.run_state_changed.emit(False)

except ImportError:  # pragma: no cover - PySide6 absent (pure-logic tests)
    AutoFocusController = None  # type: ignore[assignment, misc]
