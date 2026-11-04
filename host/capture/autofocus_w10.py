"""Live w10 autofocus: the glue between ``af_w10.W10Focus`` and the app (HANDOFF sec.54.8).

:class:`AutoFocusEngineW10` is the engine (``start`` / ``stop`` / ``feed_crops`` / ``running`` /
``cfg``); :class:`AutoFocusController` is the thin Qt wrapper the app talks to (signals, the M4/M5
+ PCB guards, and the "Auto-focus all" camera sequence). The earlier dither / sweep-and-hold /
field-profile engines were removed on 2026-11-02 after the flight showed only w10 works.

* It does not score the coarse tap. At ``start`` it asks the app (``pin_rois_cb``) to pin the five
  sec.52.2 ROIs on the active pipeline; the acquisition thread cuts them from the held full-res slot
  and the app hands each batch to :meth:`feed_crops`. One batch is scored at a time (~150 ms on one
  worker thread, sec.54.2); a batch arriving while one is in flight is skipped, not queued.
* Positions are RAW encoder counts (the frame metadata's ``encoder_count``), the frame the recorded
  V-curves and ``W10Focus`` live in -- not the per-run axis position of the other engines.
* Polarity is MEASURED, never assumed -- by the preflight motor test when the operator ran one
  this session (``start(polarity=...)``), else by a probe at the start of the run. The focus motors are brushed DC motors and each lens may have
  its leads the other way round, so which of '+'/'-' raises the count is a property of the camera,
  not of the rig (2026-10-18: cam 2439007 ran its back-off the wrong way on the sec.23.6 sign). A
  ``probe_pulses`` burst of '+' goes out first; once the encoder has settled, the sign of its move
  fixes the counts/pulse sign for the run. The encoder sits on the motor side of the lash, so the
  probe reads cleanly whatever the gear state; it costs a few frames and ~200 counts of travel
  that the 3500-count back-off swallows anyway. The preflight hands over sign AND magnitude at
  this strength (``start(counts_per_pulse=...)``); ``_learn_cpp`` refines it from the drive bursts.
* Drive. ``W10Focus`` assumes a commanded move has LANDED by the next frame it is fed (that is how
  the replay harness serves it), so the glue owns the transit: a ``move`` becomes a burst of str0
  pulses toward the target, and frames that arrive while the encoder is still far from it are
  neither scored nor fed. The next burst goes out only once the encoder has read flat for
  ``settle_ticks`` frames (motor stopped, 10 Hz encoder caught up), so nothing is re-commanded on
  stale feedback (sec.23.7). Bursts are capped (``max_pulses_per_burst``, and the binding
  ``max_counts_per_burst``: under half a motor turn, or the 12-bit encoder unwrap loses a
  revolution -- the 2026-11-02 flight) and, beyond a few pulses, aim ``approach_frac`` of the way,
  so an uncalibrated long-burst counts/pulse cannot overshoot the final approach onto the vertex --
  the one place an overshoot costs focus. A burst that moves the encoder not at all is handed to
  the engine unscored, so its own stall detector sees the limit.
"""
from __future__ import annotations

import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal

from .af_w10 import W10Config, W10Focus
from .focus_csv_log import FocusCsvLogger
from .psf_w10 import frame_ok, roi_boxes, score_rois_raw14

log = logging.getLogger(__name__)

ROI_NAMES = ("TL", "TR", "C", "BL", "BR")   # roi_boxes order: BANDS rows top to bottom, columns left to right
# One row per frame the engine saw (scored, gated out, or unscored transit/stall frames with w10 empty).
CSV_HEADER = (["timestamp", "frame", "state", "encoder", "gated", "w10", "n_edges", "n_roi", "med_mean"]
              + [f"w10_{n}" for n in ROI_NAMES] + ["fit_e0", "ci_half", "depth", "action", "target", "reason"])


def _roi_line(stats) -> str:
    """``rois TL 3.6 TR 3.7 C 5.1 BL 4.4 BR -- px`` from a stats dict (-- for a ROI that produced no w10)."""
    if not stats:
        return ""
    parts = []
    for k, n in enumerate(ROI_NAMES):
        v = stats.get(f"r{k}_w10")
        parts.append(f"{n} {v:.2f}" if isinstance(v, float) and v == v else f"{n} --")
    return "; rois " + " ".join(parts) + " px"


def burst_pulses(target: float, encoder: float, counts_per_pulse: float, max_pulses: int,
                 approach_frac: float = 1.0, exact_within_pulses: int = 4) -> int:
    """Signed pulses that move the RAW encoder from ``encoder`` toward ``target``, capped per burst.

    ``counts_per_pulse`` is SIGNED (counts the raw reading moves per '+' pulse; -24 on the rig, where
    a '+' pulse lowers the count), so the sign of the result is the pulse direction: + means '+'.
    Beyond ``exact_within_pulses`` the burst aims only ``approach_frac`` of the distance, so repeated
    settle-and-burst converges from the near side even if a long burst delivers more per pulse than
    the single-pulse calibration says.
    """
    cpp = float(counts_per_pulse) or 1.0
    n = (float(target) - float(encoder)) / cpp
    if abs(n) > exact_within_pulses:
        n *= approach_frac
    n = int(round(n))
    return max(-int(max_pulses), min(int(max_pulses), n))


class AutoFocusEngineW10(QObject):
    _scored = Signal(float, object)   # (encoder at capture, array_edges stats or None): worker -> GUI thread
    live_scored = Signal(float, object)   # same, for a frame scored OUTSIDE a run (operator focusing by hand)

    def __init__(
        self,
        *,
        pulse: Callable[[str, int], None],
        encoder_read: Callable[[], float],
        counts_per_pulse: float,
        encoder_present: Optional[Callable[[], bool]] = None,
        pin_rois_cb: Optional[Callable[[Optional[list]], None]] = None,
        set_strength: Optional[Callable[[int], None]] = None,
        status: Optional[Callable[[str], None]] = None,
        finished: Optional[Callable[[bool, dict], None]] = None,
        sample: Optional[Callable[[int, float, bool], None]] = None,
        roi_sample: Optional[Callable[[int, list], None]] = None,
        config: Optional[W10Config] = None,
        strength: int = 0,
        max_pulses_per_burst: int = 50,
        max_counts_per_burst: float = 700.0,
        approach_frac: float = 0.75,
        settle_ticks: int = 2,
        no_motion_frames: int = 6,
        probe_pulses: int = 8,
        csv_dir: Optional[str] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.cfg = config or W10Config()
        self._pulse = pulse
        self._encoder_read = encoder_read
        self._encoder_present = encoder_present
        # Magnitude from the config; the SIGN is re-measured by the probe at every start.
        self._cpp_mag = abs(float(counts_per_pulse)) or 1.0
        self._cpp = float(counts_per_pulse) or 1.0
        self._probe_pulses = int(probe_pulses)
        self._probe: Optional[dict] = None   # {"enc0", "sym", "tries"} while the polarity probe is out
        self._pin = pin_rois_cb or (lambda _r: None)
        self._set_strength = set_strength
        self._status = status or (lambda _m: None)
        self._finished = finished or (lambda _ok, _r: None)
        self._sample = sample or (lambda _p, _s, _f=False: None)
        self._roi_sample = roi_sample or (lambda _p, _r: None)
        self._strength = int(strength)
        # ponytail: 50 chars per line is the envelope the manual Focus +/- spinbox has always used;
        # longer lines are untested against the PCB's RX buffer, and str0 counts/pulse is only
        # calibrated for single pulses (sec.23.6). Upgrade path: calibrate a long str0 burst (or str3)
        # against the encoder and raise the cap / drop approach_frac to 1.
        self._max_pulses = int(max_pulses_per_burst)
        # The binding cap is in COUNTS. The AMT203S is a 12-bit single-turn angle and the host
        # unwraps it by shortest path, so a burst that carries the shaft more than half a turn
        # (2048 c) between two $ENC reports the host actually receives is folded the wrong way
        # and the count silently loses a whole revolution. Reports fail during fast motion (the
        # firmware's double-read agreement), so "between reports" can be the whole burst: the
        # 2026-11-02 flight lost 4096 c on cam3 in five of eight back-offs with 50-pulse lines
        # (~1150-1500 c each), drove the lens into its end stop, and the stall points all agree
        # modulo 4096. 700 c leaves ~3x margin even if the train coasts past the probe's c/pulse.
        self._max_counts = abs(float(max_counts_per_burst))
        self._approach_frac = float(approach_frac)
        self._settle_ticks = int(settle_ticks)
        # Command -> `$S0*` ack -> pulse line -> 10 Hz `$ENC` report is ~0.5 s on the bench (10:27
        # run: 445 ms after the probe the encoder still read the old value), i.e. more than two
        # frames. So "flat" only counts once the move has been SEEN; a move that never shows is
        # declared only after this many frames.
        self._no_motion_frames = int(no_motion_frames)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="w10-af")
        self._focus: Optional[W10Focus] = None
        self._inflight = False
        self._live = False                        # the in-flight score is a live (no-run) one
        self._last_enc: Optional[float] = None    # encoder at the previous frame (flat detection)
        self._burst_enc: Optional[float] = None   # encoder when the last burst went out
        self._burst_n = 0                         # signed pulses of the last burst ('+' positive)
        self._since_burst = 0                     # frames since the last burst
        self._moved = False                       # encoder has changed since the last burst
        self._flat_ticks = 0
        self._log_seen = 0
        self._csv_dir = csv_dir                      # None = the Session logs folder
        self._csv: Optional[FocusCsvLogger] = None   # one file per run, opened once the probe has passed
        self._last_stats: Optional[dict] = None      # stats of the last scored frame (per-ROI on the DONE line)
        self._scored.connect(self._on_scored)

    # -- controller surface ------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._focus is not None or self._probe is not None

    @property
    def strength(self) -> int:
        return self._strength

    @property
    def config_cpp_mag(self) -> float:
        return self._cpp_mag

    def start(self, counts_per_pulse: Optional[float] = None) -> None:
        """Begin a run on the selected camera: polarity probe first, then the sweep. One-shot: w10
        needs no target and always parks.

        ``counts_per_pulse`` (SIGNED: counts the raw encoder moves per '+' pulse at this run's
        strength) skips the probe -- it is what the probe would have measured, taken from the
        preflight's motor test and calibration bursts on this very motor. ``_learn_cpp`` still
        refines the magnitude from the drive bursts."""
        if self._encoder_present is not None and not self._encoder_present():
            self._status("w10 AF: encoder reads absent ($ENC=-1) -- cannot run")
            self._finished(False, {"reason": "no encoder"})
            return
        self._focus = None
        self._inflight = False
        self._last_enc = None
        self._burst_enc = None
        self._flat_ticks = 0
        self._log_seen = 0
        self._last_stats = None
        if self._set_strength is not None:
            try:
                self._set_strength(self._strength)
            except Exception:  # pragma: no cover - actuator guard
                log.exception("set_strength failed")
        self._pin(roi_boxes())
        if counts_per_pulse:
            self._cpp = float(counts_per_pulse)
            self._begin_sweep(float(self._encoder_read()),
                              f"from preflight: '+' = {self._cpp:+.0f} c/pulse at S{self._strength} (no probe)")
        else:
            self._send_probe("+", tries=0)

    def _send_probe(self, sym: str, tries: int) -> None:
        enc = float(self._encoder_read())
        self._probe = {"enc0": enc, "sym": sym, "tries": tries}
        self._status(f"w10 AF: probing motor polarity at enc {enc:.0f} ('{sym}' x{self._probe_pulses})")
        self._send(sym, self._probe_pulses, enc)

    def _send(self, sym: str, n: int, enc: float) -> None:
        """One pulse line, and the bookkeeping ``_settled`` reads."""
        self._burst_enc = enc
        self._burst_n = int(n) if sym == "+" else -int(n)   # signed pulses, '+' positive
        self._since_burst = 0
        self._moved = False
        self._flat_ticks = 0
        try:
            self._pulse(sym, int(n))
        except Exception:  # pragma: no cover - actuator guard
            log.exception("w10 AF: pulse failed")

    def _learn_cpp(self, enc: float) -> None:
        """Update counts/pulse from a settled drive burst: what the encoder moved over what was sent.
        Only bursts of a few pulses or more (the 10 Hz encoder reads +-2 c), only when the sign agrees
        with the probe's (a burst into an end stop is not a calibration), half-weight each time."""
        n = self._burst_n
        if abs(n) < 4 or self._burst_enc is None:
            return
        obs = (enc - self._burst_enc) / n
        if math.copysign(1, obs) != math.copysign(1, self._cpp) or not 0.3 < abs(obs) / abs(self._cpp) < 3.0:
            return
        self._cpp = 0.5 * self._cpp + 0.5 * obs

    def _settled(self, enc: float) -> Optional[bool]:
        """Per frame after a burst. None: still waiting. True: the encoder moved and has since read
        flat for ``settle_ticks`` frames (motor stopped, report caught up). False: no motion at all
        within ``no_motion_frames`` (limit, dead drive, dropped line)."""
        self._since_burst += 1
        if self._burst_enc is not None and abs(enc - self._burst_enc) >= 1.0:
            self._moved = True
        flat = self._last_enc is not None and abs(enc - self._last_enc) < 1.0
        self._flat_ticks = self._flat_ticks + 1 if flat else 0
        if self._moved:
            return True if self._flat_ticks >= self._settle_ticks else None
        return False if self._since_burst >= self._no_motion_frames else None

    def _probe_tick(self, enc: float) -> None:
        """Wait for the probe burst to settle, then read the sign of the encoder's move."""
        settled = self._settled(enc)
        if settled is None:
            return
        p, self._probe = self._probe, None
        delta = enc - p["enc0"]
        if abs(delta) < 2 * self._cpp_mag:
            # Two pulses' worth is the floor for a readable sign. One retry the other way covers a
            # burst lost at an end stop; after that the drive (or the encoder) is not answering.
            if p["tries"] < 1:
                self._status(f"w10 AF: probe moved the encoder only {delta:+.0f} c; retrying the other way")
                self._send_probe("-" if p["sym"] == "+" else "+", tries=p["tries"] + 1)
                return
            self._focus = None
            self._status(f"w10 AF: FAILED (motor did not move on the polarity probe: {delta:+.0f} c)")
            self._finished(False, {"reason": "no motion on polarity probe", "probe_delta": delta})
            return
        # counts the raw reading moves per '+' pulse: sign AND magnitude as this motor actually has them.
        # The config magnitude (24, sec.23.6) was the 400 mm bench lens; the 300 mm delivers 33-41 c
        # per pulse (2026-10-20) and with 24 assumed, the final approach overshot its 60 c settle
        # window on every burst and hunted around the park target indefinitely.
        plus_delta = delta if p["sym"] == "+" else -delta
        self._cpp = math.copysign(max(abs(delta) / self._probe_pulses, 1.0), plus_delta)
        self._begin_sweep(enc, f"polarity: '{p['sym']}' x{self._probe_pulses} moved the encoder {delta:+.0f} c "
                               f"({delta / self._probe_pulses:+.1f} c/pulse) -> '+' = {self._cpp:+.0f} c/pulse")

    def _begin_sweep(self, enc: float, how: str) -> None:
        """Polarity settled (probed or given): open the run's CSV and hand over to :class:`W10Focus`."""
        self._focus = W10Focus(cfg=self.cfg)
        self._log_seen = 0
        self._burst_enc = None
        self._last_stats = None
        self._csv = FocusCsvLogger(self._csv_dir, header=CSV_HEADER, prefix="Iron661_w10af")
        d = "down" if self.cfg.direction < 0 else "up"
        self._status(f"w10 AF: {how}; start at enc {enc:.0f}, sweeping {d}"
                     + (f"; CSV {self._csv.path}" if self._csv.path else ""))

    def stop(self, reason: str = "stopped by operator") -> None:
        """Halt in place -- no motor move on stop (the same rule as v2)."""
        if self.running:
            self._probe = None
            self._finish(False, reason)

    def feed_crops(self, crops) -> bool:
        """One batch of the five pinned ROIs (sensor-native uint16, ``roi_boxes`` order) from the held
        full-res slot. Returns True when the batch belongs to this engine (running), whether or not it
        was scored; the encoder is read now, as close to the exposure as the GUI thread gets."""
        if not self.running:
            return False
        enc = float(self._encoder_read())
        f = self._focus
        if self._probe is not None:
            self._probe_tick(enc)               # polarity first: no score, no feed
        elif f.target is not None and abs(enc - f.target) > self.cfg.settle_counts:
            self._transit_tick(f, enc)          # still on the way: no score, no feed
        elif not self._inflight and crops and len(crops) == 5 and all(c is not None for c in crops):
            self._inflight = True
            self._pool.submit(self._score, list(crops), enc)
        self._last_enc = enc
        return True

    def score_live(self, crops) -> None:
        """Score one pinned-ROI batch outside a run, so the operator sees the same w10 the AF
        would while focusing by hand. Same scorer, same single-worker pool; the result comes
        back on ``live_scored``. Dropped when a run owns the engine or a score is in flight."""
        if self.running or self._inflight or not crops or len(crops) != 5 or any(c is None for c in crops):
            return
        self._inflight = True
        self._live = True
        self._pool.submit(self._score, list(crops), float(self._encoder_read()))

    def _transit_tick(self, f: W10Focus, enc: float) -> None:
        settled = self._settled(enc)
        if settled is None:
            return
        if settled is False:
            # The burst moved nothing (limit, dead drive, dropped line): the engine's stall detector
            # has to see these flat readings, so feed every such frame unscored; it may re-base or
            # fail. Meanwhile re-send the pulses every no_motion_frames WITHOUT resetting the
            # bookkeeping, so the feeding continues until something moves.
            self._apply(self._feed(f, enc, None), enc)
            if self.running and self._since_burst and self._since_burst % self._no_motion_frames == 0:
                self._burst(f.target, enc, fresh=False)
            return
        self._learn_cpp(enc)
        if f.target is not None:
            self._burst(f.target, enc)   # landed short (or overshot): send the remainder

    # -- worker ------------------------------------------------------------------------------------
    def _score(self, rois: list, enc: float) -> None:
        stats = None
        try:
            t0 = time.perf_counter()
            stats = score_rois_raw14(rois)   # crops are sensor-native 14-bit; see the helper
            stats["score_ms"] = (time.perf_counter() - t0) * 1e3
        except Exception:  # noqa: BLE001 -- an unscored frame is a gated frame, not a crash
            log.exception("w10 AF: scoring failed")
        self._scored.emit(enc, stats)

    # -- GUI thread --------------------------------------------------------------------------------
    def _feed(self, f: W10Focus, enc: float, stats):
        """``f.feed`` plus the CSV row for that frame (the engine's view: state before, decision after)."""
        state = f.state.value
        act = f.feed(enc, stats)
        if self._csv is not None:
            s = stats or {}
            gated = int(stats is not None and frame_ok(stats, min_edges=self.cfg.min_edges, min_roi=self.cfg.min_roi))
            fit = f.fit
            num = lambda v: f"{v:.3f}" if isinstance(v, float) and math.isfinite(v) else ("" if isinstance(v, float) else v)
            self._csv.write([time.strftime("%Y-%m-%dT%H:%M:%S") + f".{int(time.time() * 1e3) % 1000:03d}",
                             f.frames, state, f"{enc:.0f}", gated, num(s.get("w10", "")), s.get("n_edges", ""),
                             s.get("n_roi", ""), num(s.get("med_mean", ""))]
                            + [num(s.get(f"r{k}_w10", "")) for k in range(len(ROI_NAMES))]
                            + [f"{fit.e0:.0f}" if fit is not None else "",
                               f"{f.ci_half:.0f}" if math.isfinite(f.ci_half) else "",
                               f"{f.depth:.0f}" if math.isfinite(f.depth) else "",
                               act.kind, f"{act.target:.0f}" if act.target is not None else "", act.reason or ""])
        return act

    def _on_scored(self, enc: float, stats) -> None:
        self._inflight = False
        if self._live:
            # Never fed to a run, even one that started while this score was out.
            self._live = False
            self.live_scored.emit(enc, stats)
            return
        f = self._focus
        if f is None:
            return
        if stats is not None:
            self._last_stats = stats
        act = self._feed(f, enc, stats)
        if stats is not None and frame_ok(stats, min_edges=self.cfg.min_edges, min_roi=self.cfg.min_roi):
            self._sample(int(round(enc)), float(stats["w10"]), True)
            self._roi_sample(int(round(enc)), [float(stats.get(f"r{k}_w10", math.nan)) for k in range(len(ROI_NAMES))])
        for line in f.log[self._log_seen:]:
            self._status("w10 AF " + line)
        self._log_seen = len(f.log)
        if f.frames % 10 == 0:
            self._status(self._progress(enc, stats, act))
        self._apply(act, enc)

    def _apply(self, act, enc: float) -> None:
        """Carry out one engine decision. ``hold`` needs nothing here: a hold for a far target is
        the transit the next ``feed_crops`` calls drive to completion."""
        if act.kind == "move":
            self._burst(act.target, enc)
        elif act.kind == "done":
            self._finish(True, "parked")
        elif act.kind == "fail":
            self._finish(False, act.reason)

    def _burst(self, target: Optional[float], enc: float, fresh: bool = True) -> None:
        if target is None:
            return
        n = burst_pulses(target, enc, self._cpp, self._burst_cap(), self._approach_frac)
        if n == 0:
            return
        sym = "+" if n > 0 else "-"
        if fresh:
            self._send(sym, abs(n), enc)
        else:
            try:
                self._pulse(sym, abs(n))
            except Exception:  # pragma: no cover - actuator guard
                log.exception("w10 AF: pulse failed")

    def _burst_cap(self) -> int:
        """Pulses per line: the pulse cap, or fewer when that many pulses would carry the shaft
        more than ``max_counts_per_burst`` at the current (probed, then learned) counts/pulse."""
        return max(1, min(self._max_pulses, int(self._max_counts / abs(self._cpp))))

    def _progress(self, enc: float, stats, act) -> str:
        f = self._focus
        w = stats.get("w10") if stats else None
        w_s = f"w10 {w:.2f} px ({stats.get('n_edges', 0)} edges)" if isinstance(w, float) and w == w else "w10 --"
        fit = f.fit
        fit_s = (f"  fit e0 {fit.e0:.0f} +-{f.ci_half:.0f} depth +-{f.depth:.0f}"
                 if fit is not None and math.isfinite(f.ci_half) and math.isfinite(f.depth) else "")
        tgt = f"  -> {act.target:.0f}" if act.target is not None else ""
        return f"w10 AF [{f.state.value}] f{f.frames} enc {enc:.0f}  {w_s}{fit_s}{tgt} ({act.kind}{': ' + act.reason if act.reason else ''})"

    def _finish(self, ok: bool, reason: str) -> None:
        f = self._focus
        self._focus = None
        self._inflight = False
        rep = dict(f.report(), reason=reason) if f is not None else {"reason": reason}
        v, w0, ci, dep = rep.get("vertex"), rep.get("w0"), rep.get("ci_half"), rep.get("depth")
        fit_s = (f" vertex {v:.0f} +-{ci:.0f} c, w0 {w0:.2f} px, depth +-{dep:.0f} c"
                 if v is not None and ci is not None and dep is not None else "")
        csv, self._csv = self._csv, None
        if csv is not None:
            csv.close()
            rep["csv"] = csv.path
        # per-ROI widths of the last scored frame (the parked one on DONE): a spread across the five
        # tells a tilted / uneven-depth field from a uniformly soft scene (54.13, the 4.21 px run)
        rep["roi_w10"] = [self._last_stats.get(f"r{k}_w10") for k in range(len(ROI_NAMES))] if self._last_stats else None
        rep["counts_per_pulse"] = self._cpp
        self._status(f"w10 AF: {'DONE' if ok else 'FAILED'} ({reason}){fit_s}; "
                     f"{rep.get('samples', 0)} samples in {rep.get('frames', 0)} frames{_roi_line(self._last_stats)}"
                     f"; '+' = {self._cpp:+.1f} c/pulse"
                     + (f"; CSV {csv.path}" if csv is not None and csv.path else ""))
        self._finished(ok, rep)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True)


class AutoFocusController(QObject):
    """Qt wrapper: binds :class:`AutoFocusEngineW10` to the app's serial / mode plumbing and
    sequences cameras for "Auto-focus all". The app routes every pinned-ROI crop batch for the
    active camera into :meth:`feed_crops`."""

    status = Signal(str)
    finished = Signal(bool, dict)        # one camera completed
    run_state_changed = Signal(bool)     # True = a run (single or all) is active
    sample = Signal(int, float, bool)    # (raw encoder, pooled w10, True) per gated frame
    roi_sample = Signal(int, object)     # (raw encoder, [w10 of the five ROIs, NaN = none])
    live_scored = Signal(float, object)  # (raw encoder, stats or None): a frame scored with no run active

    def __init__(
        self,
        serial,
        modes,
        *,
        counts_per_pulse: float,
        pin_rois_cb: Callable[[Optional[list]], None],
        select_camera_cb: Optional[Callable[[int], None]] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._serial = serial
        self._modes = modes
        self._select_camera_cb = select_camera_cb
        self._engine = AutoFocusEngineW10(
            pulse=serial.focus_step,
            encoder_read=lambda: serial.encoder_position,
            encoder_present=lambda: serial.encoder_present,
            counts_per_pulse=counts_per_pulse,
            pin_rois_cb=pin_rois_cb,
            set_strength=serial.set_strength,
            status=self.status.emit,
            finished=self._on_engine_finished,
            sample=self.sample.emit,
            roi_sample=self.roi_sample.emit,
            parent=self,
        )
        self._engine.live_scored.connect(self.live_scored)
        self._sequence: list[int] = []
        self._seq_index = 0
        # From the preflight (app camera numbers): cam -> +1/-1 sign per '+' pulse, and
        # cam -> {strength: |c/pulse|}. No polarity for a camera = probe as before; a polarity
        # without a magnitude at the AF strength falls back to the config magnitude.
        self.motor_polarity: dict[int, float] = {}
        self.motor_cpp: dict[int, dict[int, float]] = {}

    def _known_cpp(self, camera: Optional[int]) -> Optional[float]:
        pol = self.motor_polarity.get(camera)
        if not pol:
            return None
        mag = self.motor_cpp.get(camera, {}).get(self._engine.strength) or self._engine.config_cpp_mag
        return math.copysign(mag, pol)

    @property
    def config(self) -> W10Config:
        return self._engine.cfg

    def is_running(self) -> bool:
        return self._engine.running

    def start_single(self, camera: Optional[int] = None) -> bool:
        """One w10 run on the active camera (``camera`` looks up its preflight polarity)."""
        if not self._guard():
            return False
        self._sequence = []
        self.run_state_changed.emit(True)
        self._engine.start(self._known_cpp(camera))
        return True

    def start_all(self, cameras: list[int]) -> bool:
        """Focus each camera in ``cameras`` once, in order (relay-selected via ``select_camera_cb``)."""
        if not self._guard():
            return False
        if not cameras:
            self.status.emit("autofocus: no cameras available")
            return False
        self._sequence = list(cameras)
        self._seq_index = 0
        self.run_state_changed.emit(True)
        self._select_for_sequence()
        self._engine.start(self._known_cpp(cameras[0]))
        return True

    def stop(self, reason: str = "stopped by operator") -> None:
        self._sequence = []
        self._engine.stop(reason)

    def feed_crops(self, crops) -> bool:
        """A full-res ROI batch from the pipeline. True when a run owns the batch; otherwise
        it is scored live (``live_scored``) for the operator's manual focusing."""
        if self._engine.feed_crops(crops):
            return True
        self._engine.score_live(crops)
        return False

    def shutdown(self) -> None:
        """Join the engine's scorer thread (non-daemon) before the grabber teardown."""
        self._engine.shutdown()

    def _guard(self) -> bool:
        if self._engine.running:
            self.status.emit("autofocus: already running")
            return False
        if self._modes is not None and not self._modes.is_af_mode():
            self.status.emit("autofocus: only available in M4 (Focus) or M5 (Ground Test) mode")
            return False
        if not getattr(self._serial, "connected", False):
            self.status.emit("autofocus: PCB not connected")
            return False
        return True

    def _select_for_sequence(self) -> None:
        if 0 <= self._seq_index < len(self._sequence) and self._select_camera_cb:
            try:
                self._select_camera_cb(self._sequence[self._seq_index])
            except Exception:
                log.exception("select_camera_cb failed")

    def _on_engine_finished(self, ok: bool, report: dict) -> None:
        self.finished.emit(ok, report)
        if self._sequence and self._seq_index + 1 < len(self._sequence):
            self._seq_index += 1
            self._select_for_sequence()
            self._engine.start(self._known_cpp(self._sequence[self._seq_index]))
        else:
            self._sequence = []
            self.run_state_changed.emit(False)
