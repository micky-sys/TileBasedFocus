"""Focus-actuator abstraction (``FocusAxis``).

The autofocus search logic should never care *how* the lens moves -- only where
it is and how to send it somewhere. This module isolates that so the encoderless
open-loop drive of today and the closed-loop AMT203S encoder of tomorrow are
interchangeable behind one interface (HANDOFF Section 14.8 / 23.5).

* :class:`OpenLoopAxis` -- what we have now. "Position" is a cumulative signed
  pulse count (1 pulse = 10 ms of H-bridge drive). Backlash pulses are sent on a
  direction reversal to take up gear/spur lash before the counted move, and are
  NOT counted toward ``position`` (they take up slack, they don't move the
  optics) -- the same model the legacy ``AutoFocusEngine._drive`` used.
* :class:`EncoderTracker` -- unwraps the AMT203S ``$ENC=`` serial stream (a 12-bit
  angle that rolls over every motor revolution) into a monotonic motor count.
* :class:`EncoderAxis` -- closed-loop focus axis whose "position" is that measured
  motor count (in pulse-equivalent units), so a stalled/slipped move shows up as
  the position simply not advancing. The search logic above never changes.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Callable, Optional

log = logging.getLogger(__name__)

_ENC_RE = re.compile(r"\$ENC=(-?\d+)\*")


class FocusAxis:
    """Interface: a 1-D focus actuator with a readable position.

    Concrete axes implement :meth:`_drive` (send the motor a signed move) and
    expose :attr:`position`. Everything else (``move_to`` / ``nudge`` /
    ``park_upward``) is defined here in terms of those, so the search code is
    axis-agnostic.
    """

    position: float = 0.0
    moved_last: bool = False  # did the last command change position?
    total_pulses: int = 0     # pulses driven this run (for the AF report/telemetry)

    def reset(self) -> None:  # pragma: no cover - trivial
        raise NotImplementedError

    def _drive(self, direction: int, count: int) -> None:  # pragma: no cover
        raise NotImplementedError

    def move_to(self, target: int) -> None:
        """Drive to absolute ``target``. No-op (``moved_last=False``) if already there."""
        delta = int(round(target)) - int(round(self.position))
        if delta == 0:
            self.moved_last = False
            return
        self._drive(1 if delta > 0 else -1, abs(delta))

    def nudge(self, delta: int) -> None:
        """Move by a signed relative amount."""
        self.move_to(int(round(self.position)) + int(delta))

    def step_pulses(self, direction: int, count: int) -> None:
        """Fire ``count`` raw pulses in ``direction`` (+1/-1) OPEN-LOOP -- no servo
        to an absolute target. The incremental dive/climb pace their own fixed
        steps and READ the encoder to see where they landed, rather than
        re-commanding an absolute ``move_to`` every frame. That is what tolerates
        the ~10 Hz encoder lag + slow discrete pulses on this rig; an absolute
        re-issue servo oscillated wildly because it re-commanded on stale feedback."""
        self._drive(1 if direction > 0 else -1, int(count))

    def park_upward(self, target: int) -> None:
        """Come to rest at ``target`` on a strictly UPWARD final leg.

        Focus is measured while approaching each position from below (so gear
        lash is seated the same way every time). Landing on ``target`` from the
        other side reseats the lash and parks the lens a hair off the measured
        focus -- the "parked image blurrier than measured" bug. Overshoot just
        below and come up so the resting lash state matches the measurements.
        """
        lash = max(1, self._backlash())
        self.move_to(int(round(target)) - lash)
        self._drive(1, lash)

    def _backlash(self) -> int:  # pragma: no cover - overridden where relevant
        return 0


class OpenLoopAxis(FocusAxis):
    """Encoderless open-loop focus drive: position is a cumulative pulse count.

    ``pulse_cb(sym, count)`` sends ``count`` pulses in direction ``sym`` ("+"/"-")
    to the ClearCore. On a direction reversal, ``backlash_pulses`` extra pulses
    are sent first to take up gear/spur lash; those are not counted toward
    :attr:`position` (they take up slack, not optics).
    """

    def __init__(
        self,
        pulse_cb: Callable[[str, int], None],
        *,
        backlash_pulses: int = 3,
    ) -> None:
        self._pulse = pulse_cb
        self.backlash_pulses = int(backlash_pulses)
        self.position = 0
        self.total_pulses = 0
        self.moved_last = False
        self._last_dir = 0

    def reset(self) -> None:
        self.position = 0
        self.total_pulses = 0
        self.moved_last = False
        self._last_dir = 0

    def _backlash(self) -> int:
        return self.backlash_pulses

    def _safe_pulse(self, sym: str, count: int) -> None:
        try:
            self._pulse(sym, int(count))
        except Exception:  # pragma: no cover - actuator guard
            log.exception("focus pulse failed")

    def _drive(self, direction: int, count: int) -> None:
        if count <= 0:
            self.moved_last = False
            return
        sym = "+" if direction > 0 else "-"
        if (
            self._last_dir != 0
            and direction != self._last_dir
            and self.backlash_pulses > 0
        ):
            self._safe_pulse(sym, self.backlash_pulses)
            self.total_pulses += self.backlash_pulses
        self._safe_pulse(sym, count)
        self.position += direction * count
        self.total_pulses += count
        self._last_dir = direction
        self.moved_last = True


class EncoderTracker:
    """Accumulate the AMT203S single-turn absolute count into an unwrapped total.

    The encoder reports a 12-bit absolute angle (0..``counts_per_rev``-1) that
    *wraps* every motor revolution, and the motor turns many revolutions across
    the focus range (~69:1 gearing). So we unwrap: each new reading is folded in
    by the SHORTEST signed path from the last one, giving a monotonic count that
    tracks position across wraps.

    ``update_line`` is fed every ClearCore serial line; it picks out ``$ENC=n*``
    and folds it in. Updated on the serial reader thread and read (as
    :attr:`position`) from the AF/GUI thread -- a lock guards the two ints.

    ponytail: the shortest-path unwrap assumes the shaft moves < half a rev
    between samples (true at the ~10 Hz stream and real focus slew rates). A
    faster slew than that would alias; the fix is a faster ``$ENC`` cadence.
    """

    def __init__(self, counts_per_rev: int = 4096) -> None:
        self._cpr = int(counts_per_rev)
        self._half = self._cpr // 2
        self._last_raw: Optional[int] = None
        self._accum = 0
        self._present = False
        self._lock = threading.Lock()

    def update_raw(self, raw: int) -> None:
        with self._lock:
            if raw < 0:  # firmware sentinel: encoder not responding
                self._present = False
                return
            raw %= self._cpr
            self._present = True
            if self._last_raw is None:
                self._last_raw = raw
                return
            d = raw - self._last_raw
            if d > self._half:
                d -= self._cpr
            elif d < -self._half:
                d += self._cpr
            self._accum += d
            self._last_raw = raw

    def update_line(self, line: str) -> bool:
        """Fold in an ``$ENC=n*`` reading if *line* carries one. Returns True if
        it did (so callers can cheaply tell encoder lines from other PCB chatter)."""
        m = _ENC_RE.search(line)
        if not m:
            return False
        self.update_raw(int(m.group(1)))
        return True

    @property
    def position(self) -> int:
        with self._lock:
            return self._accum

    @property
    def present(self) -> bool:
        with self._lock:
            return self._present


class EncoderAxis(FocusAxis):
    """Closed-loop-ish focus axis backed by the AMT203S motor-shaft encoder.

    :attr:`position` is the *measured* motor position (from :class:`EncoderTracker`,
    expressed in pulse-equivalent units via ``counts_per_pulse`` so the pulse-sized
    AF config carries over unchanged), zeroed at :meth:`reset`. Because the search
    reads the true position back after every move, a stalled or slipped move simply
    shows up as the position not advancing -- unlike ``OpenLoopAxis`` which can only
    assume the commanded pulses landed.

    Drive is still open in the sense that we command pulses (the encoder is on the
    MOTOR, upstream of the gear lash, so it cannot see the lens ring directly).
    Lens-lash seating stays the job of ``park_upward`` (approach every measured
    point from below by ``backlash_pulses``); we deliberately do NOT inject lash
    pulses in :meth:`_drive` the way ``OpenLoopAxis`` does, because here those
    pulses would really turn the motor and the encoder would count them.

    ``counts_per_pulse`` is SIGNED: its magnitude is counts/pulse, its sign the
    drive<->encoder polarity. Make it NEGATIVE when a '+' pulse drives the encoder
    DOWN so that ``position`` still INCREASES on a '+' drive (``move_to`` /
    ``park_upward`` assume that; a wrong sign walks the axis off instead of homing).

    ponytail: ``counts_per_pulse`` (magnitude AND sign) must be CALIBRATED ON-RIG
    (drive a known pulse burst, read the signed encoder delta) before this is
    trusted; the default 1.0 only makes the software self-checks line up. Left
    open-loop-command (no per-move convergence loop) so an AF step never blocks the
    GUI thread; the upgrade path is a bounded settle-and-verify retry once real
    counts/pulse and slew timing are known.
    """

    def __init__(
        self,
        pulse_cb: Callable[[str, int], None],
        read_count: Callable[[], float],
        *,
        counts_per_pulse: float = 1.0,
        backlash_pulses: int = 3,
    ) -> None:
        self._pulse = pulse_cb
        self._read = read_count
        self._cpp = float(counts_per_pulse) or 1.0
        self.backlash_pulses = int(backlash_pulses)
        self._origin = 0.0
        self.total_pulses = 0
        self.moved_last = False
        self._last_dir = 0

    def reset(self) -> None:
        # Zero to the current reading so position starts at 0 like OpenLoopAxis
        # (the sweep grid is built relative to the start position).
        self._origin = float(self._read())
        self.total_pulses = 0
        self.moved_last = False
        self._last_dir = 0

    @property
    def position(self) -> float:
        return (float(self._read()) - self._origin) / self._cpp

    @property
    def raw_count(self) -> float:
        """Live absolute encoder count (pre-origin, pre-scale) -- for diagnostics
        that need to tell 'the tracker is frozen' apart from 'origin/scale math
        cancelled the move'."""
        return float(self._read())

    def _backlash(self) -> int:
        return self.backlash_pulses

    def _drive(self, direction: int, count: int) -> None:
        if count <= 0:
            self.moved_last = False
            return
        sym = "+" if direction > 0 else "-"
        try:
            self._pulse(sym, int(count))
        except Exception:  # pragma: no cover - actuator guard
            log.exception("encoder-axis pulse failed")
        self.total_pulses += int(count)
        self._last_dir = direction
        self.moved_last = True
