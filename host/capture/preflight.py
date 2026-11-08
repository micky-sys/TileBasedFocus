"""Preflight check for the Iron 661 rig: every focus motor, then one real sweep.

Sequence (``PreflightRunner._run``):

1. Prerequisites: ClearCore connected, cameras online and armed, project + drives set.
2. ``$M4*`` -- wait for the PCB's ``ack:`` (dead UART fails in seconds) and the
   ``Mode 4:`` banner (mirrors homed). The PCB triggers every camera at max fps in M4,
   so the SDK callback counters double as a liveness check for free while step 3 runs.
3. Per online camera: ``$Cn*``, wait for a FRESH ``$ENC`` reading, drive '+' until the
   encoder has moved ``MOTOR_TRAVEL_COUNTS``, then '-' the same distance back. A '+' leg
   that runs out of room is where the lens was parked, not a fault, so it backs off past
   the far side of the test and retries once (``_move_out``); only a motor that moves
   neither way, or whose total travel is short of the test, fails. Otherwise every
   pulse must move the encoder, all moves in one
   direction must agree in sign (slipping encoder), '+' and '-' must be opposite, and
   the lens must end near where it started. Then counts/pulse at S3, S2, S1, S0: a probe
   sizes the burst for that motor at that strength, then ``CAL_REPEATS`` bursts each way
   are measured at that size (the lens stays put), medians kept in ``motor_cpp`` with the
   '+' sign in ``motor_polarity`` so the AF needs no probe.
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
# Extra '-' travel before retrying a '+' leg that ran out of room, so the retry does not arrive
# back at the same stop on its final pulse (cam2, 2026-11-05: only ~5600 c of '+' room left).
MOTOR_BACKOFF_MARGIN_COUNTS = 3000.0
MOTOR_DIRECTION_BUDGET_S = 90.0  # wall-clock per direction per camera before we call it "too slow"
# Counts/pulse calibration after the travel test, at every strength the AF or the operator may use.
CAL_STRENGTHS = (3, 2, 1, 0)
CAL_REPEATS = 3                  # bursts per direction per strength (alternating, so the lens stays put)
CAL_TARGET_COUNTS = 400.0        # burst size aimed for: well above encoder noise, well under the unwrap cap
CAL_MAX_PULSES = 50              # the longest pulse line the PCB has been exercised with (focus_panel)
CAL_PROBE_BURSTS = 4             # bursts that size the measuring burst (median; even, so the lens stays put)
# Hard ceiling on a PREDICTED burst, well under MOTOR_MAX_MOVE_COUNTS: the prediction comes from a
# probe, so a motor running harder than it probed must still land inside the unwrap guard.
CAL_MAX_BURST_COUNTS = 900.0

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

# ClearPath fault lines, grouped by the advice they earn. Checked in this order: a homing
# failure or a cancelled move usually arrives with a generic alert on the same line, and
# "look for an obstruction" is the more useful thing to say when it does.
_FAULT_ADVICE = (
    ("mirror-homing", r"homing FAILED|homing failure"),
    ("mirror-cancelled", r"MotionCanceled|Move canceled|Motion may not have completed|SAFE ABORT"),
    ("mirror-fault", r"alert detected|Alerts present|MotorFaulted|Faults present"),
)
FAULT_RE = re.compile("|".join(p for _k, p in _FAULT_ADVICE), re.IGNORECASE)
BANNER_RE = re.compile(r"^\s*Mode (\d):")
SWEEP_DONE_RE = re.compile(r"^\s*Swp#")
ACK_RE = re.compile(r"^\s*ack:")


def _fault_advice(line: str) -> str:
    for key, pat in _FAULT_ADVICE:
        if re.search(pat, line, re.IGNORECASE):
            return key
    return "mirror-fault"


# What the operator reads in the dialog, keyed by fault type: a headline and the things to
# go and check, in the order worth checking them. Everything the checks MEASURED stays in
# the log -- a counts-per-pulse figure has never told anyone which cable to reseat.
# ``{subject}`` is the camera the fault belongs to ("cam2 (2439007)"), ``{detail}`` the raw
# message. A failure whose advice key is missing here falls back to that raw message, so a
# new check is never silently swallowed.
ADVICE: dict[str, tuple[str, tuple[str, ...]]] = {
    "setup": (
        "Not ready to start",
        ("{detail}",),
    ),
    "gps-no-data": (
        "GPS failed -- no data from the receiver",
        ("Check the main harness cable, at the control box and at the PCB",
         "Check the orange indicator light on the control box",
         "Check the antenna connection"),
    ),
    "gps-untagged": (
        "GPS failed -- the receiver is talking, but the frames are not tagged",
        ("Check the orange indicator light on the receiver -- fast blinking means the "
         "antenna is not seeing sky",
         "Check the antenna connection",
         "Check the main harness cable -- the camera event wire runs inside it"),
    ),
    "pcb-silent": (
        "No reply from the control PCB",
        ("Check the USB cable between the PC and the PCB",
         "Check the system voltage",
         "Power-cycle the PCB, then run preflight again"),
    ),
    "mirror-fault": (
        "Mirror motors failed -- a drive reported a fault",
        ("Check the system voltage",
         "Check for obstructions on the mirror",
         "Check the data and power cables, at the drive and at the PCB"),
    ),
    "mirror-homing": (
        "Mirror motors failed -- homing did not finish",
        ("Check for obstructions on the mirror and at its home position",
         "Check the system voltage",
         "Check the data and power cables, at the drive and at the PCB"),
    ),
    "mirror-cancelled": (
        "Mirror motors failed -- a move was cancelled part-way",
        ("Check for obstructions on the mirror",
         "Check the system voltage -- a sag during the move trips this",
         "Check the data and power cables, at the drive and at the PCB"),
    ),
    "lens-stuck": (
        "Focus motor failed on {subject} -- the lens will not turn",
        ("Check the focus ring on {subject} is not pulled forward",
         "Check nothing is fouling the ring -- hood, cable, shroud",
         "Check the focus motor wires, at the lens and at the PCB"),
    ),
    "encoder-silent": (
        "Focus encoder failed on {subject} -- no reading from the encoder",
        ("Check the encoder cable, at the lens and at the PCB",
         "Check the focus motor wires, at the lens and at the PCB",
         "Run preflight again -- a single bad read will clear"),
    ),
    "encoder-wrong": (
        "Focus encoder failed on {subject} -- the reading does not match the movement",
        ("Check the focus ring on {subject} is not pulled forward",
         "Check the encoder is seated on the focus ring and not slipping on it",
         "Run preflight again -- if it repeats, the encoder needs servicing"),
    ),
    "lens-travel": (
        "Focus motor on {subject} -- not enough travel to test",
        ("Check the focus ring on {subject} is not pulled forward",
         "Set the lens to roughly mid-focus by hand, then run preflight again"),
    ),
    "camera-no-frames": (
        "Cameras failed -- {subject} produced no frames",
        ("Check the CoaXPress cables, at the camera and at the frame grabber",
         "Check the camera power",
         "Run 'Reset cameras', then run preflight again"),
    ),
    "frames-lost": (
        "Frames were lost -- not every frame reached the disk",
        ("Check the capture drives are connected and have free space",
         "Check the CoaXPress cables, at the camera and at the frame grabber",
         "Run preflight again"),
    ),
    "aborted": ("Preflight aborted.", ()),
    "internal": ("Preflight hit an internal error -- send the log file.", ()),
}


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    advice: str = ""    # key into ADVICE; what the operator is told to go and check
    subject: str = ""   # who the advice is about, e.g. "cam2 (2439007)"

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
    # The serial printed on the camera body. The operator is being sent to a specific lens,
    # and "cam2" is an app index they have no way to read off the rig.
    camera_serial: Callable[[int], str] = field(default=lambda _c: "")
    log: Callable[[str], None] = field(default=lambda _m: None)


class _Fail(Exception):
    def __init__(self, name: str, detail: str, advice: str = "", subject: str = "") -> None:
        super().__init__(f"{name}: {detail}")
        self.name = name
        self.detail = detail
        self.advice = advice
        self.subject = subject


class _Stalled(Exception):
    """A pulse that moved the encoder less than ``MOTOR_MIN_MOVE_COUNTS``.

    Raised by :meth:`PreflightRunner._move` instead of failing on the spot, because at an end
    stop this is where the operator parked the lens rather than anything wrong with the rig.
    Every caller either recovers from it or turns it into a :class:`_Fail` via :meth:`as_fail`;
    it must never reach ``tick``."""

    def __init__(self, sym: str, travel: float, pulses: int, moved: float, enc: float) -> None:
        super().__init__(f"stalled on '{sym}' after {travel:+.0f} c")
        self.sym = sym
        self.travel = travel      # signed counts covered before the stall
        self.pulses = pulses
        self.moved = moved        # signed counts the stalled pulse did manage
        self.enc = enc            # where the lens sat when it stalled

    def as_fail(self, name: str, why: str, subject: str = "") -> _Fail:
        return _Fail(name, f"did not move on '{self.sym}' pulse #{self.pulses} at enc {self.enc:.0f} "
                           f"(moved {self.moved:+.0f} c after {self.travel:+.0f} c of travel) -- {why}",
                     advice="lens-stuck", subject=subject)


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
                raise _Fail("Preflight", self._abort_reason, advice="aborted")
            if self._fault_line is not None:
                raise _Fail("ClearCore motors", f"PCB reported: {self._fault_line}",
                            advice=_fault_advice(self._fault_line))
            next(gen)
            return
        except StopIteration:
            pass
        except _Fail as f:
            self._fail(f.name, f.detail, f.advice, f.subject)
        except Exception as exc:   # a hook raised: still a result, never a hung run
            log.exception("preflight step raised")
            self._fail("Preflight", f"internal error: {exc}", advice="internal")
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

    def _fail(self, name: str, detail: str, advice: str = "", subject: str = "") -> None:
        """Record a failure and log what it measured. The dialog only gets the advice, so
        this log line is the only place the numbers behind a FAIL survive."""
        self._results.append(CheckResult(name, False, detail, advice, subject))
        self._h.log(f"preflight: FAIL {name}" + (f" -- {detail}" if detail else ""))

    def _cams(self, *cams: int) -> str:
        """``cam2 (2439007)``: the operator reads serials off the cameras, not app indices."""
        out = []
        for c in cams:
            ser = self._h.camera_serial(c)
            out.append(f"cam{c} ({ser})" if ser else f"cam{c}")
        return ", ".join(out)

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
                                          "-- USB/UART link or firmware not responding", advice="pcb-silent")
        ok = yield from self._wait(lambda: self._banner_seq > seq and self._banner_mode == mode, BANNER_TIMEOUT_S)
        if not ok:
            raise _Fail(name, f"no 'Mode {mode}:' banner within {BANNER_TIMEOUT_S:.0f} s "
                              "-- mirror homing did not complete", advice="mirror-homing")

    def _enter_mode(self, mode: int, name: str) -> Generator[None, None, None]:
        since = (self._ack_seq, self._banner_seq)   # before the send: the ack can beat the next tick
        if not self._h.send_mode(mode):
            raise _Fail(name, f"host refused to enter M{mode} (see log)", advice="pcb-silent")
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
                                                 "(no fresh $ENC) -- relay / SPI / encoder wiring",
                        advice="encoder-silent", subject=self._cams(cam))

    def _settle(self, cam: int) -> Generator[None, None, float]:
        last, flat = self._enc(cam), 0
        deadline = self._clock() + SETTLE_TIMEOUT_S
        while flat < SETTLE_FLAT_TICKS:
            if self._clock() >= deadline:
                raise _Fail(f"Focus motor cam{cam}", f"encoder never settled after a pulse (last {last:.0f} c) "
                                                     "-- noisy encoder or motor still turning",
                            advice="encoder-silent", subject=self._cams(cam))
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
                                  f"{MOTOR_DIRECTION_BUDGET_S:.0f} s -- motor too slow / slipping",
                            advice="lens-stuck", subject=self._cams(cam))
            before = self._enc(cam)
            self._h.pulse(sym, MOTOR_PULSES_PER_BURST)
            n += 1
            moved = yield from self._wait(lambda: abs(self._enc(cam) - before) >= MOTOR_MIN_MOVE_COUNTS,
                                          PULSE_NO_MOTION_S)
            if not moved:
                raise _Stalled(sym, before - start, n, self._enc(cam) - before, before)
            after = yield from self._settle(cam)
            d = after - before
            if abs(d) > MOTOR_MAX_MOVE_COUNTS:
                raise _Fail(name, f"one '{sym}' pulse read as {d:+.0f} c -- more than the encoder can track "
                                  "per pulse (lost revolution / bad reading)",
                            advice="encoder-wrong", subject=self._cams(cam))
            if sign == 0.0:
                sign = math.copysign(1.0, d)
            elif math.copysign(1.0, d) != sign:
                raise _Fail(name, f"encoder reversed mid-'{sym}' run ({d:+.0f} c after {sign:+.0f} direction) "
                                  "-- encoder slipping on the shaft or motor stalling",
                            advice="encoder-wrong", subject=self._cams(cam))
            self._say(f"cam{cam} focus '{sym}': {after - start:+.0f} c ({n} pulses)")
        return self._enc(cam) - start, n

    def _back_off(self, cam: int, sym: str, want: float) -> Generator[None, None, float]:
        """Drive *sym* until the encoder has moved *want* counts, or until the motor stops.

        Unlike :meth:`_move`, a stall here is the answer rather than a failure: it means the far
        end of travel, which is exactly what the caller needs in order to tell "parked at a stop"
        apart from "cannot move at all". Returns the signed counts moved."""
        begin = self._enc(cam)
        deadline = self._clock() + MOTOR_DIRECTION_BUDGET_S
        while abs(self._enc(cam) - begin) < want:
            if self._clock() >= deadline:
                break
            before = self._enc(cam)
            self._h.pulse(sym, MOTOR_PULSES_PER_BURST)
            moved = yield from self._wait(lambda: abs(self._enc(cam) - before) >= MOTOR_MIN_MOVE_COUNTS,
                                          PULSE_NO_MOTION_S)
            if not moved:
                break
            yield from self._settle(cam)
            self._say(f"cam{cam} backing off '{sym}': {self._enc(cam) - begin:+.0f} c")
        return self._enc(cam) - begin

    def _move_out(self, cam: int) -> Generator[None, None, tuple[float, float, int, float]]:
        """The '+' leg of the travel test, with one recovery.

        A lens parked near its '+' end stop has nowhere to travel MOTOR_TRAVEL_COUNTS, which is a
        parking accident and not a broken motor, so a stall backs the lens off past the far side
        of the test and runs the leg again. The back-off is also the diagnosis: a motor that will
        not move either way is faulted, and one that runs out of room both ways has less usable
        travel than the check drives. Returns (start, signed travel, pulses, back-off counts)."""
        name = f"Focus motor cam{cam}"
        want = MOTOR_TRAVEL_COUNTS + MOTOR_BACKOFF_MARGIN_COUNTS
        backed = 0.0
        for attempt in (1, 2):
            start = self._enc(cam)
            try:
                fwd, n = yield from self._move(cam, "+")
                return start, fwd, n, backed
            except _Stalled as st:
                if attempt == 2:
                    raise st.as_fail(name, f"obstructed, or drive/relay not reaching this motor -- it had "
                                           f"{abs(backed):.0f} c of room backed off before this attempt",
                                     self._cams(cam))
                self._say(f"cam{cam} '+' stopped after {st.travel:+.0f} c (likely end of travel) -- "
                          f"backing off up to {want:.0f} c to retry")
                backed = yield from self._back_off(cam, "-", want)
                if abs(backed) < MOTOR_MIN_MOVE_COUNTS:
                    raise st.as_fail(name, "and will not move on '-' either: obstructed, or drive/relay "
                                           "not reaching this motor", self._cams(cam))
                if abs(backed) < MOTOR_TRAVEL_COUNTS:
                    # The back-off started AT the '+' stop and stopped at the '-' one, so what it
                    # covered IS the whole usable span -- and all the room any retry could get.
                    raise _Fail(name, f"usable travel is only ~{abs(backed):.0f} c -- less than the "
                                      f"{MOTOR_TRAVEL_COUNTS:.0f} c this check drives ('+' stopped after "
                                      f"{st.travel:+.0f} c, then '-' stopped {backed:+.0f} c later)",
                                advice="lens-travel", subject=self._cams(cam))
        raise AssertionError("unreachable")   # pragma: no cover

    def _burst(self, cam: int, s: int, n: int, sym: str) -> Generator[None, None, float]:
        """One burst of *n* '*sym*' pulses at strength *s*; returns the signed counts the encoder
        moved once it settled. Validates only what is true of every burst (the unwrap cap). A
        burst that did not move is the caller's to read: the probe grows, the measurement fails."""
        before = self._enc(cam)
        self._h.pulse(sym, n)
        yield from self._wait(lambda: abs(self._enc(cam) - before) >= MOTOR_MIN_MOVE_COUNTS,
                              PULSE_NO_MOTION_S)
        d = (yield from self._settle(cam)) - before
        if abs(d) > MOTOR_MAX_MOVE_COUNTS:
            raise _Fail(f"Focus calibration cam{cam}",
                        f"S{s} x{n} '{sym}' read as {d:+.0f} c -- more than the encoder can "
                        "track per burst (lost revolution / bad reading)",
                        advice="encoder-wrong", subject=self._cams(cam))
        self._say(f"cam{cam} S{s} x{n} '{sym}': {d:+.0f} c ({abs(d) / n:.0f} c/pulse)")
        return d

    def _probe_cpp(self, cam: int, s: int) -> Generator[None, None, float]:
        """Rough |counts per pulse| at strength *s*, only to size the measuring burst from:
        CAL_PROBE_BURSTS alternating bursts, median of the lot.

        Starts at one pulse and doubles after any burst too small to read, so a motor whose
        finest step sits under the encoder noise is still sized. Doubling only ever follows a
        burst that moved less than MOTOR_MIN_MOVE_COUNTS, so the probe cannot climb anywhere
        near the unwrap guard."""
        n, mags, i = 1, [], 0
        while len(mags) < CAL_PROBE_BURSTS:
            d = yield from self._burst(cam, s, n, "+" if i % 2 == 0 else "-")
            i += 1
            if abs(d) >= MOTOR_MIN_MOVE_COUNTS:
                mags.append(abs(d) / n)
                continue
            if n * 2 > CAL_MAX_PULSES:
                raise _Fail(f"Focus calibration cam{cam}",
                            f"S{s} x{n} moved the encoder only {d:+.0f} c at enc {self._enc(cam):.0f} "
                            "-- motor does not move at this strength",
                            advice="lens-stuck", subject=self._cams(cam))
            n, mags = n * 2, []   # the samples so far were unreadable, not small
        return statistics.median(mags)

    def _calibrate(self, cam: int, polarity: float) -> Generator[None, None, dict[int, list[float]]]:
        """|counts per pulse| at every strength in CAL_STRENGTHS: a probe sizes the burst, then
        CAL_REPEATS bursts each way are measured at that size and the median kept. Each measured
        burst must move, must not exceed the unwrap cap, and must go the way *polarity* says.
        Returns the per-strength magnitudes (measured bursts only, in order).

        The burst is sized from what this motor just did rather than from a table of nominals:
        on 2026-11-05 the S1 nominal was 4x low, which sized a 5-pulse burst that read 1854 c
        and failed a healthy motor on the unwrap guard (sec.55.9)."""
        name = f"Focus calibration cam{cam}"
        out: dict[int, list[float]] = {}
        for s in CAL_STRENGTHS:
            self._h.set_strength(s)
            n = _burst_pulses((yield from self._probe_cpp(cam, s)))
            mags: list[float] = []
            for i in range(2 * CAL_REPEATS):
                sym = "+" if i % 2 == 0 else "-"
                d = yield from self._burst(cam, s, n, sym)
                if abs(d) < MOTOR_MIN_MOVE_COUNTS:
                    raise _Fail(name, f"S{s} x{n} '{sym}' moved the encoder only {d:+.0f} c "
                                      f"at enc {self._enc(cam):.0f} -- motor does not move at this strength",
                                advice="lens-stuck", subject=self._cams(cam))
                if math.copysign(1.0, d) != (polarity if sym == "+" else -polarity):
                    raise _Fail(name, f"S{s} '{sym}' moved {d:+.0f} c, against the polarity measured at S3 "
                                      "-- encoder slipping or motor stalling",
                                advice="encoder-wrong", subject=self._cams(cam))
                mags.append(abs(d) / n)
            out[s] = mags
        return out

    # ── the sequence ──────────────────────────────────────────────────────
    def _run(self) -> Generator[None, None, None]:
        h = self._h
        # 1. prerequisites -- fail fast before touching hardware
        if not h.serial_connected():
            raise _Fail("Prerequisites", "ClearCore not connected", advice="setup")
        cams = sorted(h.cameras())
        if not cams:
            raise _Fail("Prerequisites", "no cameras online", advice="setup")
        not_armed = [c for c in cams if not h.armed(c)]
        if not_armed:
            raise _Fail("Prerequisites", f"cam(s) {not_armed} not armed -- run 'Reset cameras' first",
                        advice="setup")
        missing = h.recording_prerequisites_missing()
        if missing:
            raise _Fail("Prerequisites", "nothing would be saved: set " + "; ".join(missing), advice="setup")
        self._pass("Prerequisites", f"cams {cams} armed; ClearCore connected; project + drives set")
        if not h.gps_connected():
            # Known now, so said now -- but not fatal: the motors and the sweep are still
            # worth checking while the operator sorts the receiver out.
            self._fail("GPS tagging",
                       "GPS receiver not connected -- frames would be written without a fix "
                       "(rest of the check continues)", advice="gps-no-data")

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
            start, fwd, n_fwd, backed = yield from self._move_out(cam)
            try:
                back, n_back = yield from self._move(cam, "-")
            except _Stalled as st:
                # The '-' leg retraces ground the '+' leg just covered, so it has room by
                # construction: a stall here is the rig, not where the lens was parked.
                raise st.as_fail(name, "on the way back -- obstructed, or drive/relay not reaching this motor",
                                 self._cams(cam))
            if math.copysign(1.0, fwd) == math.copysign(1.0, back):
                raise _Fail(name, f"'+' ({fwd:+.0f} c) and '-' ({back:+.0f} c) moved the encoder the same way",
                            advice="encoder-wrong", subject=self._cams(cam))
            err = self._enc(cam) - start
            if abs(err) > MOTOR_RETURN_TOL_COUNTS:
                raise _Fail(name, f"lens did not return: {err:+.0f} c from start after +/- {MOTOR_TRAVEL_COUNTS:.0f} c "
                                  "-- slipping drive or encoder",
                            advice="encoder-wrong", subject=self._cams(cam))
            self.motor_polarity[cam] = math.copysign(1.0, fwd)
            self._pass(name, f"'+' {fwd:+.0f} c / {n_fwd} pulses ({fwd / n_fwd:+.0f} c/pulse), "
                             f"'-' {back:+.0f} c / {n_back} pulses, back to {err:+.0f} c of start; "
                             f"polarity noted for AF"
                             + (f"; backed off {backed:+.0f} c off the '+' end stop first" if backed else ""))
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
            yield from self._recover(f"cam(s) {dead} not acquiring in M4", self._cams(*dead))
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
                                            + (" even after deep camera recovery" if self._recovered else ""),
                            advice="camera-no-frames", subject=self._cams(*zero))
            self._results.append(CheckResult("Sweep images (1st try)", True,
                                             f"WARN cam(s) {zero} acquired no frames; deep recovery, then one more sweep"))
            since = yield from self._recover(f"cam(s) {zero} acquired nothing on the sweep", self._cams(*zero))
            # recovery's preamble re-sent $M0* (another homing); its banner may already have
            # landed while the cameras were being reset, hence the pre-recovery snapshot
            yield from self._wait_banner(0, "Mirror homing (M0 after recovery)", since=since)

        h.rotate_lines()
        self._say("done")

    def _recover(self, why: str, subject: str = "") -> Generator[None, None, tuple[int, int]]:
        """Deep camera recovery. Returns the (ack, banner) snapshot from before it started,
        for waiting on the ``Mode 0:`` banner its ``$M0*`` preamble produces."""
        self._say(f"{why} -> DEEP camera recovery (~25 s)...")
        since = (self._ack_seq, self._banner_seq)
        self._recovery_done = None
        self._recovered = True
        self._h.recover_cameras()
        ok = yield from self._wait(lambda: self._recovery_done is not None, RECOVERY_TIMEOUT_S)
        if not ok:
            raise _Fail("Camera recovery", f"did not complete within {RECOVERY_TIMEOUT_S:.0f} s",
                        advice="camera-no-frames", subject=subject)
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
            raise _Fail("Sweep", f"no 'Swp#' from the PCB within {SWEEP_TIMEOUT_S:.0f} s -- sweep never ran or never finished",
                        advice="frames-lost")

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
            raise _Fail("Sweep images", f"frame counts differ between cameras (missed triggers): {summary}",
                        advice="frames-lost")
        bad = [c for c in cams if per[c][2] > 0 or per[c][1] < per[c][0]]
        if bad:
            raise _Fail("Sweep images", f"cam(s) {bad} dropped or did not write every frame: {summary}",
                        advice="frames-lost")
        self._pass("Sweep images", summary)

        if not h.gps_connected():
            if any(r.name == "GPS tagging" for r in self._results):
                return []   # already reported at the prerequisites stage
            raise _Fail("GPS tagging", "GPS receiver disconnected during the check -- frames were written without a fix",
                        advice="gps-no-data")
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
                                       + " -- event wire / receiver / NMEA bridge",
                        advice="gps-untagged")
        self._pass("GPS tagging", ", ".join(gps_txt))
        return []


def _burst_pulses(cpp: float) -> int:
    """Pulses per measuring burst for a motor probed at *cpp* counts per pulse: enough travel to
    read over the encoder noise (CAL_TARGET_COUNTS), never enough to fold the 12-bit unwrap even
    if the motor turns out to run harder than it probed (CAL_MAX_BURST_COUNTS)."""
    if cpp <= 0.0:
        return 1
    return max(1, min(CAL_MAX_PULSES,
                      int(CAL_MAX_BURST_COUNTS / cpp),
                      round(CAL_TARGET_COUNTS / cpp)))


def gps_stats_by_camera(stats: dict, serial_by_cam: dict[int, str]) -> dict:
    """``GpsCorrelator.stats()`` re-keyed from camera serial to app camera number.

    The correlator counts per camera SERIAL (``pop_fix_for`` is handed the acquisition thread's
    ``_camera_serial``), while every preflight hook speaks app camera numbers. Reading those
    counters under a camera number finds nothing and reports zero matches for every camera --
    which failed the GPS check on a rig whose frames were all correctly tagged (sec.55.9).
    Nothing raised to give it away: ``int("2439008")`` is a perfectly good int."""
    by_serial = (stats or {}).get("cameras") or {}
    out = dict(stats or {})
    out["cameras"] = {cam: by_serial[ser] for cam, ser in serial_by_cam.items() if ser in by_serial}
    return out


def _gps_per_cam(stats: dict) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for k, v in (stats or {}).get("cameras", {}).items():
        try:
            out[int(k)] = dict(v)
        except (TypeError, ValueError):
            continue
    return out


def advice_block(r: CheckResult) -> str:
    """One failure as the operator's instruction: a headline and what to go and check."""
    headline, bullets = ADVICE.get(r.advice, ("", ()))
    if not headline:   # no advice written for this one yet: say the raw thing rather than nothing
        return r.line()
    fmt = {"subject": r.subject or r.name, "detail": r.detail}
    return "\n".join([headline.format(**fmt)] + [f"  \u2022 {b.format(**fmt)}" for b in bullets])


def format_report(results: list[CheckResult]) -> str:
    """The operator's dialog: what to go and check, and nothing else.

    What the checks measured -- counts per pulse, encoder travel, frame tallies, GPS match
    ratios -- is written to the log as each one finishes (``_pass`` / ``_fail``), which is
    where it belongs: no number here has ever told anyone which cable to reseat. Only
    failures get a block, in the order they happened, so the top of the dialog is the thing
    to fix first."""
    fails = [r for r in results if not r.ok]
    if not fails:
        return "Preflight checks pass"
    return "\n\n".join(advice_block(r) for r in fails)
