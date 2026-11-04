"""Preflight check for the Iron 661 rig: every focus motor, then one real sweep.

Sequence (``PreflightRunner._run``):

1. Prerequisites: ClearCore connected, cameras online and armed, project + drives set.
2. ``$M4*`` -- wait for the PCB's ``ack:`` (dead UART fails in seconds) and the
   ``Mode 4:`` banner (mirrors homed). The PCB triggers every camera at max fps in M4,
   so the SDK callback counters double as a liveness check for free while step 3 runs.
3. Per online camera: ``$Cn*``, wait for a FRESH ``$ENC`` reading, drive '+' until the
   encoder has moved ``MOTOR_TRAVEL_COUNTS``, then '-' the same distance back. Every
   pulse must move the encoder (obstruction / end stop / dead drive), all moves in one
   direction must agree in sign (slipping encoder), '+' and '-' must be opposite, and
   the lens must end near where it started. Then counts/pulse at S3, S2, S1, S0:
   ``CAL_REPEATS`` bursts each way per strength (the lens stays put), medians kept in
   ``motor_cpp`` with the '+' sign in ``motor_polarity`` so the AF needs no probe.
4. Cameras whose ``acq`` did not advance during M4 get the DEEP camera recovery once.
5. ``$M0*`` (homes again, opens line containers) then ``$Click*`` -- exactly one sweep
   through the same nav-point trigger path the flight uses. Wait for ``Swp#``, let the
   writers catch up, then: every camera got the same non-zero frame count, nothing
   dropped, everything written, and every frame got a GPS fix.
6. A camera with zero frames on the sweep: deep recovery (if not already done) and one
   more sweep; still zero -> fail.

Any ClearCore fault line at any point (``Motor alert detected``, ``homing FAILED``,
``SAFE ABORT`` ...) aborts the run with that line as the pointer.

The runner is a generator-driven state machine ticked at 10 Hz: every wait is a
``yield`` loop with a deadline, so it never blocks the GUI thread and is testable with
a fake rig and a fake clock (``tools/test_preflight.py``). All rig access goes through
:class:`PreflightHooks` callables so the runner knows nothing about MainWindow.
"""

from __future__ import annotations

import logging
import math
import re
import statistics
import time
from dataclasses import dataclass, field
from typing import Callable, Generator, Optional

from PySide6.QtCore import QObject, QTimer, Signal, Slot

log = logging.getLogger(__name__)

# ── tunables ────────────────────────────────────────────────────────────────
MOTOR_TRAVEL_COUNTS = 10_000.0   # each direction, per focus motor (operator spec)
MOTOR_STRENGTH = 3               # fastest: ~850 c per pulse on the 300 mm (focus_panel._MANUAL_MAX_PULSES)
MOTOR_PULSES_PER_BURST = 1       # one S3 pulse per line keeps each move under the 12-bit unwrap envelope
MOTOR_MIN_MOVE_COUNTS = 20.0     # a pulse that moves less than this did not move (10 Hz read is +-2 c)
MOTOR_MAX_MOVE_COUNTS = 1800.0   # more than this per pulse and the unwrap may have folded (half turn = 2048)
MOTOR_RETURN_TOL_COUNTS = 3000.0 # '+' travel then '-' travel should land near the start (lash + slip)
MOTOR_DIRECTION_BUDGET_S = 90.0  # wall-clock per direction per camera before we call it "too slow"
# Counts/pulse calibration after the travel test, at every strength the AF or the operator may use.
CAL_STRENGTHS = (3, 2, 1, 0)
CAL_REPEATS = 3                  # bursts per direction per strength (alternating, so the lens stays put)
CAL_TARGET_COUNTS = 400.0        # burst size aimed for: well above encoder noise, well under the unwrap cap
CAL_MAX_PULSES = 50              # the longest pulse line the PCB has been exercised with (focus_panel)
# Seeds for sizing the bursts only (sec.23.6 / 2026-10-20: str0 24-41 c, str3 ~850); the measured
# value is what gets recorded. A seed off by 3x still lands between MOTOR_MIN/MAX_MOVE_COUNTS.
CAL_NOMINAL_CPP = {0: 24.0, 1: 80.0, 2: 300.0, 3: 850.0}

TICK_S = 0.1
ACK_TIMEOUT_S = 3.0              # PCB echoes 'ack: <cmd>' for every command it parses
BANNER_TIMEOUT_S = 45.0          # setMode = stop + EnableAndHome (homing has its own ~30 s timeout) + banner
ENCODER_FRESH_S = 0.5            # a $ENC at most this old counts as "reporting" (10 Hz stream)
ENCODER_TIMEOUT_S = 3.0
PULSE_NO_MOTION_S = 1.5          # command -> $S3 ack -> pulse -> $ENC is ~0.5 s on the bench
SETTLE_FLAT_TICKS = 3            # encoder unchanged for this many ticks = motor stopped, report caught up
SETTLE_TIMEOUT_S = 3.0
SWEEP_TIMEOUT_S = 90.0
WRITER_CATCHUP_S = 20.0
RECOVERY_TIMEOUT_S = 120.0       # deep recovery is ~25 s; the worker has its own failure reporting

FAULT_RE = re.compile(
    r"alert detected|Alerts present|MotorFaulted|MotionCanceled|homing FAILED|"
    r"homing failure|Move canceled|Motion may not have completed|SAFE ABORT|Faults present",
    re.IGNORECASE,
)
BANNER_RE = re.compile(r"^\s*Mode (\d):")
SWEEP_DONE_RE = re.compile(r"^\s*Swp#")
ACK_RE = re.compile(r"^\s*ack:")


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""

    def line(self) -> str:
        return f"{'PASS' if self.ok else 'FAIL'}  {self.name}" + (f": {self.detail}" if self.detail else "")


@dataclass
class PreflightHooks:
    """Everything the runner needs from the rig, as plain callables (app camera numbers)."""
    serial_connected: Callable[[], bool]
    send_mode: Callable[[int], bool]            # the app's mode-click path; True if the host state followed
    start_sweep: Callable[[], None]             # $Click*  (one sweep in M0)
    select_camera: Callable[[int], None]        # $Cn*
    set_strength: Callable[[int], None]         # $Sn*
    pulse: Callable[[str, int], None]           # '+'/'-' x count
    encoder_position: Callable[[int], float]
    encoder_present: Callable[[int], bool]
    encoder_age: Callable[[int], Optional[float]]
    cameras: Callable[[], list[int]]            # online app camera numbers
    armed: Callable[[int], bool]
    frame_counts: Callable[[int], tuple[int, int, int]]   # (acquired, written, dropped), cumulative
    frames_delivered: Callable[[int], int]      # SDK callbacks since arm: counts in M4 even for cameras the
                                                # host idle-gates (not previewed), unlike frame_counts()[0]
    gps_connected: Callable[[], bool]
    gps_stats: Callable[[], dict]               # GpsCorrelator.stats()
    recording_prerequisites_missing: Callable[[], list[str]]
    recover_cameras: Callable[[], None]         # DEEP, non-interactive; completion via on_recovery_done()
    rotate_lines: Callable[[], None]            # close the preflight line containers, open fresh ones
    log: Callable[[str], None] = field(default=lambda _m: None)


class _Fail(Exception):
    def __init__(self, name: str, detail: str) -> None:
        super().__init__(f"{name}: {detail}")
        self.name = name
        self.detail = detail


class PreflightRunner(QObject):
    status = Signal(str)
    finished = Signal(bool, object)   # (all_ok, list[CheckResult])

    def __init__(self, hooks: PreflightHooks, *, clock: Callable[[], float] = time.monotonic,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._h = hooks
        self._clock = clock
        self._gen: Optional[Generator] = None
        self._results: list[CheckResult] = []
        self._timer = QTimer(self)
        self._timer.setInterval(int(TICK_S * 1000))
        self._timer.timeout.connect(self.tick)
        # serial-line observations, read by the generator
        self._banner_seq = 0
        self._banner_mode: Optional[int] = None
        self._ack_seq = 0
        self._sweep_seq = 0
        self._fault_line: Optional[str] = None
        self._recovery_done: Optional[int] = None
        self._recovered = False
        self._abort_reason: Optional[str] = None
        # cam -> +1/-1: sign the RAW encoder moves per '+' pulse, as measured by the motor test.
        # Kept across runs (a failed re-run does not forget a motor it never reached); the AF
        # takes it instead of its own polarity probe.
        self.motor_polarity: dict[int, float] = {}
        # cam -> {strength: |counts per pulse|} from the calibration bursts (median of 2*CAL_REPEATS).
        self.motor_cpp: dict[int, dict[int, float]] = {}

    # ── control ───────────────────────────────────────────────────────────
    def is_running(self) -> bool:
        return self._gen is not None

    def start(self) -> bool:
        """False only if a run is already in progress. The first tick runs synchronously, so a
        prerequisite failure emits ``finished`` before this returns -- connect first."""
        if self._gen is not None:
            return False
        self._results = []
        self._fault_line = None
        self._recovered = False
        self._abort_reason = None
        self._gen = self._run()
        self._timer.start()
        self.tick()
        return True

    def abort(self, reason: str = "aborted by operator") -> None:
        """Operator abort: the run ends on the next tick with a FAIL carrying *reason*."""
        if self._gen is not None:
            self._abort_reason = reason

    def cancel(self) -> None:
        """Drop the run with no ``finished`` emit (window closing: no dialog, no mode traffic)."""
        self._timer.stop()
        self._gen = None

    @Slot(str)
    def on_serial_line(self, line: str) -> None:
        if self._gen is None:
            return
        if ACK_RE.match(line):
            self._ack_seq += 1
        m = BANNER_RE.match(line)
        if m:
            self._banner_seq += 1
            self._banner_mode = int(m.group(1))
        if SWEEP_DONE_RE.match(line):
            self._sweep_seq += 1
        if FAULT_RE.search(line) and self._fault_line is None:
            self._fault_line = line.strip()

    @Slot(int)
    def on_recovery_done(self, succeeded: int) -> None:
        self._recovery_done = int(succeeded)

    @Slot()
    def tick(self) -> None:
        gen = self._gen
        if gen is None:
            return
        try:
            if self._abort_reason is not None:
                raise _Fail("Preflight", self._abort_reason)
            if self._fault_line is not None:
                raise _Fail("ClearCore motors", f"PCB reported: {self._fault_line}")
            next(gen)
            return
        except StopIteration:
            pass
        except _Fail as f:
            self._results.append(CheckResult(f.name, False, f.detail))
        except Exception as exc:   # a hook raised: still a result, never a hung run
            log.exception("preflight step raised")
            self._results.append(CheckResult("Preflight", False, f"internal error: {exc}"))
        self._gen = None
        self._timer.stop()
        if self._fault_line is None and not any(r.name == "ClearCore motors" for r in self._results):
            self._results.append(CheckResult("ClearCore motors", True, "no fault lines during the run"))
        ok = all(r.ok for r in self._results)
        self.finished.emit(ok, list(self._results))

    # ── helpers for the generator ─────────────────────────────────────────
    def _say(self, msg: str) -> None:
        self.status.emit(msg)
        self._h.log(f"preflight: {msg}")

    def _pass(self, name: str, detail: str = "") -> None:
        self._results.append(CheckResult(name, True, detail))
        self._h.log(f"preflight: PASS {name}" + (f" -- {detail}" if detail else ""))

    def _wait(self, pred: Callable[[], bool], timeout_s: float) -> Generator[None, None, bool]:
        deadline = self._clock() + timeout_s
        while not pred():
            if self._clock() >= deadline:
                return False
            yield
        return True

    def _wait_banner(self, mode: int, name: str,
                     since: Optional[tuple[int, int]] = None) -> Generator[None, None, None]:
        """Wait for the ``Mode <mode>:`` banner that follows a ``$M<mode>*`` sent after *since*
        (an ``(ack_seq, banner_seq)`` snapshot; default: now)."""
        ack, seq = since if since is not None else (self._ack_seq, self._banner_seq)
        ok = yield from self._wait(lambda: self._ack_seq > ack or self._banner_seq > seq, ACK_TIMEOUT_S)
        if not ok:
            raise _Fail("ClearCore link", f"no 'ack:' from the PCB within {ACK_TIMEOUT_S:.0f} s of $M{mode}* "
                                          "-- USB/UART link or firmware not responding")
        ok = yield from self._wait(lambda: self._banner_seq > seq and self._banner_mode == mode, BANNER_TIMEOUT_S)
        if not ok:
            raise _Fail(name, f"no 'Mode {mode}:' banner within {BANNER_TIMEOUT_S:.0f} s "
                              "-- mirror homing did not complete")

    def _enter_mode(self, mode: int, name: str) -> Generator[None, None, None]:
        since = (self._ack_seq, self._banner_seq)   # before the send: the ack can beat the next tick
        if not self._h.send_mode(mode):
            raise _Fail(name, f"host refused to enter M{mode} (see log)")
        yield from self._wait_banner(mode, name, since=since)
        self._pass(name)

    def _enc(self, cam: int) -> float:
        return float(self._h.encoder_position(cam))

    def _wait_fresh_encoder(self, cam: int) -> Generator[None, None, None]:
        def fresh() -> bool:
            age = self._h.encoder_age(cam)
            return bool(self._h.encoder_present(cam)) and age is not None and age <= ENCODER_FRESH_S
        ok = yield from self._wait(fresh, ENCODER_TIMEOUT_S)
        if not ok:
            raise _Fail(f"Focus motor cam{cam}", "encoder not reporting after $C select "
                                                 "(no fresh $ENC) -- relay / SPI / encoder wiring")

    def _settle(self, cam: int) -> Generator[None, None, float]:
        last, flat = self._enc(cam), 0
        deadline = self._clock() + SETTLE_TIMEOUT_S
        while flat < SETTLE_FLAT_TICKS:
            if self._clock() >= deadline:
                raise _Fail(f"Focus motor cam{cam}", f"encoder never settled after a pulse (last {last:.0f} c) "
                                                     "-- noisy encoder or motor still turning")
            yield
            e = self._enc(cam)
            flat = flat + 1 if abs(e - last) < 2.0 else 0
            last = e
        return last

    def _move(self, cam: int, sym: str) -> Generator[None, None, tuple[float, int]]:
        """Pulse *sym* until the encoder has travelled MOTOR_TRAVEL_COUNTS from where it is now.
        Returns (signed travel, pulses)."""
        name = f"Focus motor cam{cam}"
        start = self._enc(cam)
        sign = 0.0
        n = 0
        deadline = self._clock() + MOTOR_DIRECTION_BUDGET_S
        while abs(self._enc(cam) - start) < MOTOR_TRAVEL_COUNTS:
            if self._clock() >= deadline:
                raise _Fail(name, f"'{sym}' moved only {self._enc(cam) - start:+.0f} c in "
                                  f"{MOTOR_DIRECTION_BUDGET_S:.0f} s -- motor too slow / slipping")
            before = self._enc(cam)
            self._h.pulse(sym, MOTOR_PULSES_PER_BURST)
            n += 1
            moved = yield from self._wait(lambda: abs(self._enc(cam) - before) >= MOTOR_MIN_MOVE_COUNTS,
                                          PULSE_NO_MOTION_S)
            if not moved:
                raise _Fail(name, f"did not move on '{sym}' pulse #{n} at enc {before:.0f} "
                                  f"(moved {self._enc(cam) - before:+.0f} c after {before - start:+.0f} c of travel) "
                                  "-- obstructed, at an end stop, or drive/relay not reaching this motor")
            after = yield from self._settle(cam)
            d = after - before
            if abs(d) > MOTOR_MAX_MOVE_COUNTS:
                raise _Fail(name, f"one '{sym}' pulse read as {d:+.0f} c -- more than the encoder can track "
                                  "per pulse (lost revolution / bad reading)")
            if sign == 0.0:
                sign = math.copysign(1.0, d)
            elif math.copysign(1.0, d) != sign:
                raise _Fail(name, f"encoder reversed mid-'{sym}' run ({d:+.0f} c after {sign:+.0f} direction) "
                                  "-- encoder slipping on the shaft or motor stalling")
            self._say(f"cam{cam} focus '{sym}': {after - start:+.0f} c ({n} pulses)")
        return self._enc(cam) - start, n

    def _calibrate(self, cam: int, polarity: float) -> Generator[None, None, dict[int, list[float]]]:
        """|counts per pulse| at every strength in CAL_STRENGTHS: CAL_REPEATS bursts each way,
        sized from the nominal table to ~CAL_TARGET_COUNTS, read off the settled encoder. Each
        burst must move, must not exceed the unwrap cap, and must go the way *polarity* says.
        Returns the per-strength magnitudes (all bursts, in order)."""
        name = f"Focus calibration cam{cam}"
        out: dict[int, list[float]] = {}
        for s in CAL_STRENGTHS:
            n = max(1, min(CAL_MAX_PULSES, round(CAL_TARGET_COUNTS / CAL_NOMINAL_CPP[s])))
            self._h.set_strength(s)
            mags: list[float] = []
            for i in range(2 * CAL_REPEATS):
                sym = "+" if i % 2 == 0 else "-"
                before = self._enc(cam)
                self._h.pulse(sym, n)
                moved = yield from self._wait(lambda: abs(self._enc(cam) - before) >= MOTOR_MIN_MOVE_COUNTS,
                                              PULSE_NO_MOTION_S)
                if not moved:
                    raise _Fail(name, f"S{s} x{n} '{sym}' moved the encoder only {self._enc(cam) - before:+.0f} c "
                                      f"at enc {before:.0f} -- motor does not move at this strength")
                d = (yield from self._settle(cam)) - before
                if abs(d) > MOTOR_MAX_MOVE_COUNTS:
                    raise _Fail(name, f"S{s} x{n} '{sym}' read as {d:+.0f} c -- more than the encoder can "
                                      "track per burst (lost revolution / bad reading)")
                if math.copysign(1.0, d) != (polarity if sym == "+" else -polarity):
                    raise _Fail(name, f"S{s} '{sym}' moved {d:+.0f} c, against the polarity measured at S3 "
                                      "-- encoder slipping or motor stalling")
                mags.append(abs(d) / n)
                self._say(f"cam{cam} S{s} x{n} '{sym}': {d:+.0f} c ({abs(d) / n:.0f} c/pulse)")
            out[s] = mags
        return out

    # ── the sequence ──────────────────────────────────────────────────────
    def _run(self) -> Generator[None, None, None]:
        h = self._h
        # 1. prerequisites -- fail fast before touching hardware
        if not h.serial_connected():
            raise _Fail("Prerequisites", "ClearCore not connected")
        cams = sorted(h.cameras())
        if not cams:
            raise _Fail("Prerequisites", "no cameras online")
        not_armed = [c for c in cams if not h.armed(c)]
        if not_armed:
            raise _Fail("Prerequisites", f"cam(s) {not_armed} not armed -- run 'Reset cameras' first")
        missing = h.recording_prerequisites_missing()
        if missing:
            raise _Fail("Prerequisites", "nothing would be saved: set " + "; ".join(missing))
        self._pass("Prerequisites", f"cams {cams} armed; ClearCore connected; project + drives set")
        if not h.gps_connected():
            # Known now, so said now -- but not fatal: the motors and the sweep are still
            # worth checking while the operator sorts the receiver out.
            self._results.append(CheckResult("GPS tagging", False,
                                             "GPS receiver not connected -- frames would be written without a fix "
                                             "(rest of the check continues)"))

        # 2. M4 -- mirrors home, PCB starts triggering every camera at max fps
        self._say("entering M4 (mirror homing)...")
        yield from self._enter_mode(4, "Mirror homing (M4)")
        delivered_at_m4 = {c: h.frames_delivered(c) for c in cams}

        # 3. focus motors: travel test at S3, then counts/pulse at every strength
        for cam in cams:
            name = f"Focus motor cam{cam}"
            self._say(f"cam{cam}: selecting focus motor...")
            h.select_camera(cam)
            h.set_strength(MOTOR_STRENGTH)   # the previous camera's calibration left the PCB at S0
            yield from self._wait_fresh_encoder(cam)
            start = self._enc(cam)
            fwd, n_fwd = yield from self._move(cam, "+")
            back, n_back = yield from self._move(cam, "-")
            if math.copysign(1.0, fwd) == math.copysign(1.0, back):
                raise _Fail(name, f"'+' ({fwd:+.0f} c) and '-' ({back:+.0f} c) moved the encoder the same way")
            err = self._enc(cam) - start
            if abs(err) > MOTOR_RETURN_TOL_COUNTS:
                raise _Fail(name, f"lens did not return: {err:+.0f} c from start after +/- {MOTOR_TRAVEL_COUNTS:.0f} c "
                                  "-- slipping drive or encoder")
            self.motor_polarity[cam] = math.copysign(1.0, fwd)
            self._pass(name, f"'+' {fwd:+.0f} c / {n_fwd} pulses ({fwd / n_fwd:+.0f} c/pulse), "
                             f"'-' {back:+.0f} c / {n_back} pulses, back to {err:+.0f} c of start; "
                             f"polarity noted for AF")
            cal = yield from self._calibrate(cam, self.motor_polarity[cam])
            self.motor_cpp[cam] = {s: statistics.median(m) for s, m in cal.items()}
            self._pass(f"Focus calibration cam{cam}",
                       ", ".join(f"S{s} {statistics.median(m):.0f} c/pulse ({min(m):.0f}-{max(m):.0f})"
                                 for s, m in cal.items())
                       + f"; median of {2 * CAL_REPEATS} bursts each, noted for AF")

        # 4. cameras alive? (M4 has been triggering them the whole time). Read at the SDK
        # callback, not the GUI's acq counter: in M4 the host only consumes the previewed
        # camera's frames, the other two are drained uncounted.
        dead = [c for c in cams if h.frames_delivered(c) <= delivered_at_m4[c]]
        if dead:
            # Not a verdict yet: the sweep after recovery decides. Recorded so the report says
            # the rig needed a reset to get here.
            self._results.append(CheckResult("Cameras alive (M4)", True,
                                             f"WARN cam(s) {dead} acquired nothing while M4 triggered at max fps; "
                                             "deep recovery run"))
            yield from self._recover(f"cam(s) {dead} not acquiring in M4")
        else:
            self._pass("Cameras alive (M4)", ", ".join(
                f"cam{c} +{h.frames_delivered(c) - delivered_at_m4[c]} frames" for c in cams))

        # 5. M0 + one sweep
        self._say("entering M0 (mirror homing, opening line containers)...")
        yield from self._enter_mode(0, "Mirror homing (M0)")

        for attempt in (1, 2):
            zero = yield from self._sweep(cams)
            if not zero:
                break
            if attempt == 2 or self._recovered:
                raise _Fail("Sweep images", f"cam(s) {zero} acquired no frames on the sweep"
                                            + (" even after deep camera recovery" if self._recovered else ""))
            self._results.append(CheckResult("Sweep images (1st try)", True,
                                             f"WARN cam(s) {zero} acquired no frames; deep recovery, then one more sweep"))
            since = yield from self._recover(f"cam(s) {zero} acquired nothing on the sweep")
            # recovery's preamble re-sent $M0* (another homing); its banner may already have
            # landed while the cameras were being reset, hence the pre-recovery snapshot
            yield from self._wait_banner(0, "Mirror homing (M0 after recovery)", since=since)

        h.rotate_lines()
        self._say("done")

    def _recover(self, why: str) -> Generator[None, None, tuple[int, int]]:
        """Deep camera recovery. Returns the (ack, banner) snapshot from before it started,
        for waiting on the ``Mode 0:`` banner its ``$M0*`` preamble produces."""
        self._say(f"{why} -> DEEP camera recovery (~25 s)...")
        since = (self._ack_seq, self._banner_seq)
        self._recovery_done = None
        self._recovered = True
        self._h.recover_cameras()
        ok = yield from self._wait(lambda: self._recovery_done is not None, RECOVERY_TIMEOUT_S)
        if not ok:
            raise _Fail("Camera recovery", f"did not complete within {RECOVERY_TIMEOUT_S:.0f} s")
        self._pass("Camera recovery", f"{self._recovery_done} camera(s) recovered + re-armed")
        return since

    def _sweep(self, cams: list[int]) -> Generator[None, None, list[int]]:
        """One $Click* sweep. Returns the cameras that acquired nothing (the retry trigger);
        every other problem raises."""
        h = self._h
        base = {c: h.frame_counts(c) for c in cams}
        gps0 = _gps_per_cam(h.gps_stats())
        sweep_seq = self._sweep_seq
        self._say("firing one sweep ($Click*)...")
        h.start_sweep()
        ok = yield from self._wait(lambda: self._sweep_seq > sweep_seq, SWEEP_TIMEOUT_S)
        if not ok:
            raise _Fail("Sweep", f"no 'Swp#' from the PCB within {SWEEP_TIMEOUT_S:.0f} s -- sweep never ran or never finished")

        def delta(c: int) -> tuple[int, int, int]:
            a, w, d = h.frame_counts(c)
            return a - base[c][0], w - base[c][1], d - base[c][2]

        self._say("sweep done; waiting for writers to catch up...")
        yield from self._wait(lambda: all(delta(c)[1] >= delta(c)[0] for c in cams), WRITER_CATCHUP_S)
        per = {c: delta(c) for c in cams}
        zero = [c for c in cams if per[c][0] == 0]
        if zero:
            return zero
        counts = {per[c][0] for c in cams}
        summary = ", ".join(f"cam{c} acq {a} wr {w} drop {d}" for c, (a, w, d) in per.items())
        if len(counts) > 1:
            raise _Fail("Sweep images", f"frame counts differ between cameras (missed triggers): {summary}")
        bad = [c for c in cams if per[c][2] > 0 or per[c][1] < per[c][0]]
        if bad:
            raise _Fail("Sweep images", f"cam(s) {bad} dropped or did not write every frame: {summary}")
        self._pass("Sweep images", summary)

        if not h.gps_connected():
            if any(r.name == "GPS tagging" for r in self._results):
                return []   # already reported at the prerequisites stage
            raise _Fail("GPS tagging", "GPS receiver disconnected during the check -- frames were written without a fix")
        gps1 = _gps_per_cam(h.gps_stats())
        gps_bad, gps_txt = [], []
        for c in cams:
            d = {k: gps1.get(c, {}).get(k, 0) - gps0.get(c, {}).get(k, 0)
                 for k in ("matched", "fallback_latest", "stale", "out_of_future", "no_event")}
            missing = d["stale"] + d["out_of_future"] + d["no_event"]
            gps_txt.append(f"cam{c} matched {d['matched']}/{per[c][0]}"
                           + (f" fallback {d['fallback_latest']}" if d["fallback_latest"] else "")
                           + (f" NO FIX {missing}" if missing else ""))
            if missing or d["matched"] + d["fallback_latest"] < per[c][0]:
                gps_bad.append(c)
        if gps_bad:
            raise _Fail("GPS tagging", f"cam(s) {gps_bad} have frames without a GPS event: " + ", ".join(gps_txt)
                                       + " -- event wire / receiver / NMEA bridge")
        self._pass("GPS tagging", ", ".join(gps_txt))
        return []


def _gps_per_cam(stats: dict) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for k, v in (stats or {}).get("cameras", {}).items():
        try:
            out[int(k)] = dict(v)
        except (TypeError, ValueError):
            continue
    return out


def format_report(results: list[CheckResult]) -> str:
    """Failures first, so the dialog's first line is the pointer."""
    fails = [r.line() for r in results if not r.ok]
    passes = [r.line() for r in results if r.ok]
    return "\n".join(fails + passes)
