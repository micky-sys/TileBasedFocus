"""Live w10 autofocus engine: the glue between ``af_w10.W10Focus`` and the app (HANDOFF sec.54.8).

Same duck-typed surface as the v2/v3 engines (``start`` / ``stop`` / ``feed_frame`` / ``running`` /
``cfg`` / ``current_roi``), so ``AutoFocusController`` drives it unchanged. What is different:

* It does not score the coarse tap. At ``start`` it asks the app (``pin_rois_cb``) to pin the five
  sec.52.2 ROIs on the active pipeline; the acquisition thread cuts them from the held full-res slot
  and the app hands each batch to :meth:`feed_crops`. One batch is scored at a time (~150 ms on one
  worker thread, sec.54.2); a batch arriving while one is in flight is skipped, not queued.
* Positions are RAW encoder counts (the frame metadata's ``encoder_count``), the frame the recorded
  V-curves and ``W10Focus`` live in -- not the per-run axis position of the other engines.
* Polarity is MEASURED, every run. The focus motors are brushed DC motors and each lens may have
  its leads the other way round, so which of '+'/'-' raises the count is a property of the camera,
  not of the rig (2026-10-18: cam 2439007 ran its back-off the wrong way on the sec.23.6 sign). A
  ``probe_pulses`` burst of '+' goes out first; once the encoder has settled, the sign of its move
  fixes the counts/pulse sign for the run. The encoder sits on the motor side of the lash, so the
  probe reads cleanly whatever the gear state; it costs a few frames and ~200 counts of travel
  that the 3500-count back-off swallows anyway.
* Drive. ``W10Focus`` assumes a commanded move has LANDED by the next frame it is fed (that is how
  the replay harness serves it), so the glue owns the transit: a ``move`` becomes a burst of str0
  pulses toward the target, and frames that arrive while the encoder is still far from it are
  neither scored nor fed. The next burst goes out only once the encoder has read flat for
  ``settle_ticks`` frames (motor stopped, 10 Hz encoder caught up), so nothing is re-commanded on
  stale feedback (sec.23.7). Bursts are capped (``max_pulses_per_burst``) and, beyond a few pulses,
  aim ``approach_frac`` of the way, so an uncalibrated long-burst counts/pulse cannot overshoot the
  final approach onto the vertex -- the one place an overshoot costs focus. A burst that moves the
  encoder not at all is handed to the engine unscored, so its own stall detector sees the limit.
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
from .psf_w10 import frame_ok, roi_boxes, score_rois

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
        approach_frac: float = 0.75,
        settle_ticks: int = 2,
        no_motion_frames: int = 6,
        probe_pulses: int = 8,
        csv_dir: Optional[str] = None,
        parent: Optional[QObject] = None,
        **_unused,   # the controller's common kwargs the other engines take (full_res_score, adjust_cadence)
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
        self.adaptive_profile = False   # interface parity; nothing to adapt
        self.current_roi = None         # no single winning tile: the five fixed ROIs are the target

    # -- controller surface ------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._focus is not None or self._probe is not None

    def start(self, **_ignored) -> None:
        """Begin a run on the selected camera: polarity probe first, then the sweep.
        ``lead_edge_hint`` / ``continuous`` / ``seed_roi`` are accepted for parity and ignored: w10
        needs no target and always parks (one-shot)."""
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
        self._focus = W10Focus(cfg=self.cfg)
        self._log_seen = 0
        self._burst_enc = None
        self._last_stats = None
        self._csv = FocusCsvLogger(self._csv_dir, header=CSV_HEADER, prefix="Iron661_w10af")
        d = "down" if self.cfg.direction < 0 else "up"
        self._status(f"w10 AF: polarity: '{p['sym']}' x{self._probe_pulses} moved the encoder {delta:+.0f} c "
                     f"({delta / self._probe_pulses:+.1f} c/pulse) -> '+' = {self._cpp:+.0f} c/pulse; "
                     f"start at enc {enc:.0f}, sweeping {d}"
                     + (f"; CSV {self._csv.path}" if self._csv.path else ""))

    def stop(self, reason: str = "stopped by operator") -> None:
        """Halt in place -- no motor move on stop (the same rule as v2)."""
        if self.running:
            self._probe = None
            self._finish(False, reason)

    def feed_frame(self, frame) -> None:
        """The coarse tap carries nothing w10 needs; the crops come through :meth:`feed_crops`."""

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
            stats = score_rois(rois)
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
        n = burst_pulses(target, enc, self._cpp, self._max_pulses, self._approach_frac)
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
