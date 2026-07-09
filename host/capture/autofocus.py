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
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

import cv2
import numpy as np

# Repo-root modules (see sys.path bootstrap in app.py / tests).
from sharpness import find_best_tile, score_tile_subsampled
from tile_geometry import Tile, TileGrid

log = logging.getLogger(__name__)

Roi = tuple[int, int, int, int]  # (x0, y0, x1, y1)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class AutoFocusConfig:
    """Tunables for one autofocus run."""

    # Workflow
    assisted: bool = True
    """Operator-assisted: search only a small bracket around the start position.
    When False (full-auto), allow a wider coarse search from any start."""

    # Step sizes (pulses; 1 pulse = 10 ms of drive)
    step_pulses: int = 2
    coarse_step_pulses: int = 6

    # Search bounds
    assisted_bracket_pulses: int = 24
    max_total_pulses: int = 400  # hard safety cap on total commanded motion

    # Measurement cadence
    settle_frames: int = 1   # frames to skip after a move before measuring
    measure_frames: int = 2  # frames whose scores are combined per measurement

    # Decision thresholds
    improve_frac: float = 0.03  # min relative gain to count as "better"
    patience: int = 2           # non-improving steps before reversing / stopping

    # Backlash (brushed DC + gears)
    backlash_pulses: int = 3

    # Drive strength ($Sn level) used during the run
    strength: int = 2

    # Texture / failure detection
    min_texture_score: float = 1.5
    max_low_texture_frames: int = 40

    # Leading-edge target selection
    edge_band: float = 0.25
    tiles_per_frame: float = 5.0  # TileGrid zoom -> tile ~ frame/this

    # Bench rig (orthophoto screen): restrict tiles to an inset interior,
    # excluding the screen bezel and the optical ground-truth strip band.
    # Off by default so normal M4/M5 flight behaviour is unchanged.
    restrict_interior: bool = False
    interior_inset: float = 0.12
    exclude_edge: Optional[str] = None  # top/bottom/left/right = strip location

    # Tracking
    min_track_response: float = 0.05  # phaseCorrelate response floor

    # Cadence
    drive_cadence_to_min: bool = True
    cadence_steps: int = 26  # number of "<" to send to reach the FW floor


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
    elif lead_edge is None:
        grid = TileGrid(
            frame_width=fw,
            frame_height=fh,
            display_width=fw,
            display_height=fh,
            zoom=cfg.tiles_per_frame,
            region="circle",
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
    if len(grid) == 0:
        # Degenerate frame/tile sizing -- fall back to a centred ROI.
        tw = max(2, fw // 4) & ~1
        th = max(2, fh // 4) & ~1
        x0 = (fw // 2 - tw // 2) & ~1
        y0 = (fh // 2 - th // 2) & ~1
        roi = (x0, y0, x0 + tw, y0 + th)
        return roi, roi_sharpness(frame, roi)
    result = find_best_tile(frame, grid)
    t = grid[result.best_index]
    return (t.x0, t.y0, t.x1, t.y1), float(result.best_score)


def roi_sharpness(frame: np.ndarray, roi: Roi, subsample: int = 2) -> float:
    """High-pass std sharpness of a fixed ROI (same metric as the winning tile)."""
    x0, y0, x1, y1 = roi
    return score_tile_subsampled(frame, Tile(x0, y0, x1, y1), subsample=subsample)


def _clamp_roi(roi: Roi, fw: int, fh: int) -> Optional[Roi]:
    x0, y0, x1, y1 = roi
    w = x1 - x0
    h = y1 - y0
    if w < 2 or h < 2 or w > fw or h > fh:
        return None
    x0 = int(round(min(max(0, x0), fw - w)))
    y0 = int(round(min(max(0, y0), fh - h)))
    return (x0, y0, x0 + w, y0 + h)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class AFState(Enum):
    IDLE = "idle"
    ACQUIRE = "acquire"
    MEASURE = "measure"
    MOVE_SETTLE = "move_settle"
    RETURN_SETTLE = "return_settle"
    DONE = "done"
    FAILED = "failed"


@dataclass
class _RunState:
    roi: Optional[Roi] = None
    lead_edge: Optional[str] = None
    position: int = 0          # effective optical position (signed pulses)
    best_score: Optional[float] = None
    best_pos: int = 0
    cur_dir: int = 1
    last_move_dir: int = 0
    reversed_once: bool = False
    non_improve: int = 0
    total_pulses: int = 0
    low_texture_frames: int = 0
    measure_buf: list[float] = field(default_factory=list)
    settle_left: int = 0
    frames: int = 0
    motion_dx: float = 0.0
    motion_dy: float = 0.0


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
    ) -> None:
        self._pulse = pulse
        self._set_strength = set_strength
        self._adjust_cadence = adjust_cadence
        self._status = status or (lambda _m: None)
        self._finished = finished or (lambda _ok, _r: None)
        self.cfg = config or AutoFocusConfig()
        self.state = AFState.IDLE
        self._tracker = PatchTracker()
        self._rs = _RunState()
        self._lead_hint: Optional[str] = None

    # ── lifecycle ───────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self.state not in (AFState.IDLE, AFState.DONE, AFState.FAILED)

    def start(self, *, assisted: Optional[bool] = None, lead_edge_hint: Optional[str] = None) -> None:
        if assisted is not None:
            self.cfg.assisted = bool(assisted)
        self._lead_hint = lead_edge_hint
        self._tracker.reset()
        self._rs = _RunState()
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
        self._fail(reason)

    # ── per-frame step ──────────────────────────────────────────────────

    def feed_frame(self, frame: np.ndarray) -> None:
        if not self.running:
            return
        if frame is None or frame.ndim < 2:
            return
        rs = self._rs
        rs.frames += 1
        fh, fw = frame.shape[:2]

        dx, dy, response = self._tracker.update(frame)
        rs.motion_dx, rs.motion_dy = dx, dy

        # Global "featureless frame" guard (e.g. an oblique camera pointing at
        # sky at the top of its orbit). Pause without disturbing search state;
        # abort only if it persists.
        if self._frame_is_featureless(frame):
            rs.low_texture_frames += 1
            if rs.low_texture_frames > self.cfg.max_low_texture_frames:
                self._fail("low texture (featureless frame)")
            return

        if self.state == AFState.ACQUIRE:
            self._acquire(frame)
            return

        # Track the patch: move ROI with the content.
        if rs.roi is not None:
            moved = (rs.roi[0] + dx, rs.roi[1] + dy, rs.roi[2] + dx, rs.roi[3] + dy)
            clamped = _clamp_roi(
                (int(round(moved[0])), int(round(moved[1])),
                 int(round(moved[2])), int(round(moved[3]))),
                fw, fh,
            )
            if clamped is None or response < self.cfg.min_track_response:
                # Lost the patch (or it hit the trailing edge): re-acquire a
                # fresh leading-edge target, keeping the search progress.
                self._reacquire(frame)
                return
            rs.roi = clamped

        score = roi_sharpness(frame, rs.roi) if rs.roi is not None else 0.0

        # Intermittent low texture (e.g. oblique sky phase): pause -- don't
        # count this frame, hold focus, resume when texture returns.
        if score < self.cfg.min_texture_score:
            rs.low_texture_frames += 1
            if rs.low_texture_frames > self.cfg.max_low_texture_frames:
                self._fail("low texture (no focusable detail)")
            return
        rs.low_texture_frames = 0

        if self.state in (AFState.MOVE_SETTLE, AFState.RETURN_SETTLE):
            rs.settle_left -= 1
            if rs.settle_left > 0:
                return
            if self.state == AFState.RETURN_SETTLE:
                self._complete(frame)
                return
            self.state = AFState.MEASURE
            rs.measure_buf = []
            # fall through and measure this frame

        if self.state == AFState.MEASURE:
            rs.measure_buf.append(score)
            if len(rs.measure_buf) < self.cfg.measure_frames:
                return
            measurement = float(np.median(rs.measure_buf))
            rs.measure_buf = []
            self._decide(measurement)

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
        lead = self._lead_hint or leading_edge_from_motion(rs.motion_dx, rs.motion_dy)
        roi, score = select_leading_edge_roi(frame, lead, self.cfg)
        rs.roi = roi
        rs.lead_edge = lead
        if score < self.cfg.min_texture_score:
            rs.low_texture_frames += 1
            if rs.low_texture_frames > self.cfg.max_low_texture_frames:
                self._fail("low texture at acquisition")
            # stay in ACQUIRE; try again next frame (e.g. through sky phase)
            self.state = AFState.ACQUIRE
            return
        rs.low_texture_frames = 0
        edge_txt = lead if lead is not None else "centre"
        self._status(f"autofocus: locked target on {edge_txt} (score={score:.1f})")
        self.state = AFState.MEASURE
        rs.measure_buf = []

    def _reacquire(self, frame: np.ndarray) -> None:
        rs = self._rs
        lead = rs.lead_edge or leading_edge_from_motion(rs.motion_dx, rs.motion_dy)
        roi, _score = select_leading_edge_roi(frame, lead, self.cfg)
        rs.roi = roi
        self._tracker.reset()
        # Resume measuring at the current position.
        if self.state in (AFState.MOVE_SETTLE,):
            return
        if self.state != AFState.RETURN_SETTLE:
            self.state = AFState.MEASURE
            rs.measure_buf = []

    def _decide(self, measurement: float) -> None:
        rs = self._rs
        cfg = self.cfg
        if rs.best_score is None:
            rs.best_score = measurement
            rs.best_pos = rs.position
            rs.cur_dir = 1
            self._issue_move(rs.cur_dir)
            return

        improved = measurement > rs.best_score * (1.0 + cfg.improve_frac)
        if improved:
            rs.best_score = measurement
            rs.best_pos = rs.position
            rs.non_improve = 0
            self._issue_move(rs.cur_dir)
            return

        rs.non_improve += 1
        if not rs.reversed_once:
            # Passed (or started on the wrong side of) the peak: reverse once.
            rs.reversed_once = True
            rs.non_improve = 0
            rs.cur_dir = -rs.cur_dir
            self._issue_move(rs.cur_dir)
            return

        if rs.non_improve >= cfg.patience:
            self._begin_return()
        else:
            self._issue_move(rs.cur_dir)

    def _step_for_dir(self) -> int:
        return self.cfg.step_pulses if self.cfg.assisted else self.cfg.coarse_step_pulses

    def _issue_move(self, direction: int) -> None:
        rs = self._rs
        cfg = self.cfg
        count = self._step_for_dir()
        target = rs.position + direction * count

        if cfg.assisted and abs(target) > cfg.assisted_bracket_pulses:
            self._begin_return()
            return
        if rs.total_pulses + count + cfg.backlash_pulses > cfg.max_total_pulses:
            self._status("autofocus: hit travel cap; returning to best")
            self._begin_return()
            return

        self._drive(direction, count)
        self.state = AFState.MOVE_SETTLE
        rs.settle_left = cfg.settle_frames
        rs.measure_buf = []

    def _begin_return(self) -> None:
        rs = self._rs
        delta = rs.best_pos - rs.position
        if delta == 0:
            self._complete(None)
            return
        direction = 1 if delta > 0 else -1
        self._drive(direction, abs(delta))
        self.state = AFState.RETURN_SETTLE
        rs.settle_left = self.cfg.settle_frames
        self._status("autofocus: returning to best focus")

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

    def _complete(self, frame: Optional[np.ndarray]) -> None:
        rs = self._rs
        if frame is not None and rs.roi is not None:
            final = roi_sharpness(frame, rs.roi)
        else:
            final = rs.best_score or 0.0
        self.state = AFState.DONE
        report = {
            "ok": True,
            "best_score": rs.best_score,
            "best_pos": rs.best_pos,
            "final_score": final,
            "total_pulses": rs.total_pulses,
            "frames": rs.frames,
            "lead_edge": rs.lead_edge,
        }
        self._status(
            f"autofocus: done (score={rs.best_score:.1f}, "
            f"pos={rs.best_pos:+d} pulses, frames={rs.frames})"
            if rs.best_score is not None else "autofocus: done"
        )
        self._finished(True, report)

    def _fail(self, reason: str) -> None:
        rs = self._rs
        self.state = AFState.FAILED
        report = {
            "ok": False,
            "reason": reason,
            "best_score": rs.best_score,
            "best_pos": rs.best_pos,
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

        def __init__(
            self,
            serial,
            modes,
            *,
            select_camera_cb: Optional[Callable[[int], None]] = None,
            set_roi_cb: Optional[Callable[[Optional[Roi]], None]] = None,
            config: Optional[AutoFocusConfig] = None,
            parent: Optional[QObject] = None,
        ) -> None:
            super().__init__(parent)
            self._serial = serial
            self._modes = modes
            self._select_camera_cb = select_camera_cb
            self._set_roi_cb = set_roi_cb
            self._engine = AutoFocusEngine(
                pulse=self._serial.focus_step,
                set_strength=self._serial.set_strength,
                adjust_cadence=self._serial.adjust_trigger_period,
                status=self.status.emit,
                finished=self._on_engine_finished,
                config=config,
            )
            self._sequence: list[int] = []
            self._seq_index = 0
            self._assisted = True

        @property
        def config(self) -> AutoFocusConfig:
            return self._engine.cfg

        def is_running(self) -> bool:
            return self._engine.running

        # ── start / stop ────────────────────────────────────────────────

        def start_single(self, *, assisted: bool = True, lead_edge_hint: Optional[str] = None) -> bool:
            if not self._guard():
                return False
            self._sequence = []
            self._assisted = assisted
            self.run_state_changed.emit(True)
            self._engine.start(assisted=assisted, lead_edge_hint=lead_edge_hint)
            return True

        def start_all(self, cameras: list[int], *, assisted: bool = True) -> bool:
            if not self._guard():
                return False
            if not cameras:
                self.status.emit("autofocus: no cameras available")
                return False
            self._sequence = list(cameras)
            self._seq_index = 0
            self._assisted = assisted
            self.run_state_changed.emit(True)
            self._select_for_sequence()
            self._engine.start(assisted=assisted)
            return True

        def stop(self) -> None:
            self._sequence = []
            self._engine.stop()

        def feed_frame(self, frame) -> None:
            if self._engine.running:
                self._engine.feed_frame(frame)

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
                self._engine.start(assisted=self._assisted)
            else:
                self._sequence = []
                self.run_state_changed.emit(False)

except ImportError:  # pragma: no cover - PySide6 absent (pure-logic tests)
    AutoFocusController = None  # type: ignore[assignment, misc]
