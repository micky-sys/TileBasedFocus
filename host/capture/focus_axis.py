"""Focus-actuator abstraction (``FocusAxis``).

The autofocus search logic should never care *how* the lens moves -- only where
it is and how to send it somewhere. This module isolates that so the encoderless
open-loop drive of today and the closed-loop AMT203S encoder of tomorrow are
interchangeable behind one interface (HANDOFF Section 14.8 / 23.5).

* :class:`OpenLoopAxis` -- what we have now. Backlash pulses are sent on a
  direction reversal to take up gear/spur lash before the counted move, and are
  NOT counted toward ``position`` (they take up slack, they don't move the
  optics) -- the same model the legacy ``AutoFocusEngine._drive`` used.
* :class:`EncoderTracker` -- unwraps the AMT203S ``$ENC=`` serial stream (a 12-bit
  angle that rolls over every motor revolution) into a monotonic motor count.
* :class:`EncoderAxis` -- closed-loop focus axis whose "position" is that measured
  motor count, so a stalled/slipped move shows up as the position simply not
  advancing. The search logic above never changes.

UNITS
-----
**Positions are ENCODER COUNTS; drive amounts are PULSES.** They are not the same
thing and must not be interchanged. A pulse is a fixed-duration H-bridge burst --
the only quantum the hardware can be commanded in, and its size in counts depends
on the drive STRENGTH (~24 counts at str0, ~34x that at str3). A count is what the
AMT203S measures, and it means the same thing at every strength. So anything we
*measure* or compare against a measurement belongs in counts, and only the actual
motor commands stay in pulses.

Positions used to be carried in "pulse-equivalent units" (counts/24), which quietly
cost 24x of the encoder's resolution to ``int(round(...))`` on every read and let a
park "tolerance" of 4 be written as if it were tight when it was really 96 counts.
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

    Concrete axes implement :meth:`_drive` (send the motor a signed move, in
    PULSES) and expose :attr:`position` (in COUNTS). Everything else (``move_to``
    / ``nudge`` / ``park_upward``) is defined here in terms of those, so the
    search code is axis-agnostic.
    """

    position: float = 0.0     # ENCODER COUNTS, increasing on a '+' drive
    moved_last: bool = False  # did the last command change position?
    total_pulses: int = 0     # pulses driven this run (for the AF report/telemetry)
    counts_per_pulse: float = 1.0  # SIGNED; magnitude converts counts <-> pulses

    def reset(self) -> None:  # pragma: no cover - trivial
        raise NotImplementedError

    def _drive(self, direction: int, count: int) -> None:  # pragma: no cover
        """Send ``count`` PULSES in ``direction`` (+1/-1)."""
        raise NotImplementedError

    @property
    def counts_per_pulse_mag(self) -> float:
        """Counts per pulse as a positive magnitude, for counts<->pulses conversion.
        The sign of ``counts_per_pulse`` is drive<->encoder polarity, which is the
        axis's own business (see :class:`EncoderAxis`), never the caller's."""
        return abs(float(self.counts_per_pulse)) or 1.0

    def pulses_for(self, counts: float) -> int:
        """How many whole pulses best approximate a move of ``counts``. Rounds, so a
        request smaller than half a pulse honestly answers 0 rather than pretending."""
        return int(round(float(counts) / self.counts_per_pulse_mag))

    def move_to(self, target: float) -> None:
        """Drive to absolute ``target`` COUNTS. No-op (``moved_last=False``) when the
        remaining error is under half a pulse -- that's the finest move that exists."""
        pulses = self.pulses_for(float(target) - float(self.position))
        if pulses == 0:
            self.moved_last = False
            return
        self._drive(1 if pulses > 0 else -1, abs(pulses))

    def nudge(self, delta: float) -> None:
        """Move by a signed relative amount, in COUNTS."""
        self.move_to(float(self.position) + float(delta))

    def step_pulses(self, direction: int, count: int) -> None:
        """Fire ``count`` raw pulses in ``direction`` (+1/-1) OPEN-LOOP -- no servo
        to an absolute target. The incremental dive/climb pace their own fixed
        steps and READ the encoder to see where they landed, rather than
        re-commanding an absolute ``move_to`` every frame. That is what tolerates
        the ~10 Hz encoder lag + slow discrete pulses on this rig; an absolute
        re-issue servo oscillated wildly because it re-commanded on stale feedback."""
        self._drive(1 if direction > 0 else -1, int(count))

    def park_upward(self, target: float) -> None:
        """Come to rest at ``target`` COUNTS on a strictly UPWARD final leg.

        Focus is measured while approaching each position from below (so gear
        lash is seated the same way every time). Landing on ``target`` from the
        other side reseats the lash and parks the lens a hair off the measured
        focus -- the "parked image blurrier than measured" bug. Overshoot just
        below and come up so the resting lash state matches the measurements.
        """
        lash = max(1, self._backlash())  # pulses
        self.move_to(float(target) - lash * self.counts_per_pulse_mag)
        self._drive(1, lash)

    def _backlash(self) -> int:  # pragma: no cover - overridden where relevant
        """Lash take-up, in PULSES."""
        return 0


class OpenLoopAxis(FocusAxis):
    """Encoderless open-loop focus drive: position is the commanded travel, in the
    same COUNTS unit the encoder axis reports, inferred from the pulses we sent.

    ``pulse_cb(sym, count)`` sends ``count`` pulses in direction ``sym`` ("+"/"-")
    to the ClearCore. On a direction reversal, ``backlash_pulses`` extra pulses
    are sent first to take up gear/spur lash; those are not counted toward
    :attr:`position` (they take up slack, not optics).

    ``counts_per_pulse`` defaults to 1.0, which makes counts and pulses numerically
    identical -- convenient for the self-checks, and honest for a rig with no
    encoder to define a count in the first place.
    """

    def __init__(
        self,
        pulse_cb: Callable[[str, int], None],
        *,
        backlash_pulses: int = 3,
        counts_per_pulse: float = 1.0,
    ) -> None:
        self._pulse = pulse_cb
        self.backlash_pulses = int(backlash_pulses)
        self.counts_per_pulse = float(counts_per_pulse) or 1.0
        self.position = 0.0
        self.total_pulses = 0
        self.moved_last = False
        self._last_dir = 0

    def reset(self) -> None:
        self.position = 0.0
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
        self.position += direction * count * self.counts_per_pulse_mag
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

    :attr:`position` is the *measured* motor position in raw encoder COUNTS (from
    :class:`EncoderTracker`), zeroed at :meth:`reset`. Because the search reads the
    true position back after every move, a stalled or slipped move simply shows up
    as the position not advancing -- unlike ``OpenLoopAxis`` which can only assume
    the commanded pulses landed.

    Drive is still open in the sense that we command pulses (the encoder is on the
    MOTOR, upstream of the gear lash, so it cannot see the lens ring directly).
    Lens-lash seating stays the job of ``park_upward`` (approach every measured
    point from below by ``backlash_pulses``); we deliberately do NOT inject lash
    pulses in :meth:`_drive` the way ``OpenLoopAxis`` does, because here those
    pulses would really turn the motor and the encoder would count them.

    ``counts_per_pulse`` is SIGNED. Its SIGN is the drive<->encoder polarity and is
    the only part that touches :attr:`position`: make it NEGATIVE when a '+' pulse
    drives the raw count DOWN, and ``position`` is negated so it still INCREASES on
    a '+' drive (``move_to`` / ``park_upward`` assume that; a wrong sign walks the
    axis off instead of homing). Its MAGNITUDE is used only to size commands, i.e.
    to turn a requested move in counts into whole pulses, and is calibrated at the
    CLIMB/PARK strength (str0) -- a str3 pulse is ~34x larger, which is exactly why
    the dive commands pulses directly and just reads the counts back.

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
        self.counts_per_pulse = float(counts_per_pulse) or 1.0
        self._sign = -1.0 if self.counts_per_pulse < 0 else 1.0
        self.backlash_pulses = int(backlash_pulses)
        self._origin = 0.0
        self.total_pulses = 0
        self.moved_last = False
        self._last_dir = 0

    def reset(self) -> None:
        # Zero to the current reading so position starts at 0 like OpenLoopAxis
        # (the sweep grid is built relative to the start position).
        #
        # NOTE this runs on every AutoFocusEngineV2.start(), i.e. every AF press, so
        # a position logged by one run is NOT comparable to one logged by another --
        # each run's zero is wherever the lens was parked when the button was hit.
        # Cross-run comparisons must go through raw_count (see :attr:`raw_count`).
        self._origin = float(self._read())
        self.total_pulses = 0
        self.moved_last = False
        self._last_dir = 0

    @property
    def position(self) -> float:
        """Measured position in encoder COUNTS since :meth:`reset`, signed so that a
        '+' drive increases it regardless of the encoder's wiring polarity."""
        return (float(self._read()) - self._origin) * self._sign

    @property
    def raw_count(self) -> float:
        """Live absolute encoder count, BEFORE the per-run origin is subtracted.

        Two uses: telling 'the tracker is frozen' apart from 'the origin maths
        cancelled the move', and comparing results ACROSS AF runs -- this is the
        only position that survives :meth:`reset`, so it is the common frame for
        e.g. checking a moving-scene run against a frozen-scene reference."""
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
