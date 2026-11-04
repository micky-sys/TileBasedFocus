"""AMT203S encoder stream tracking.

* :class:`EncoderTracker` -- unwraps the AMT203S ``$ENC=`` serial stream (a 12-bit
  angle that rolls over every motor revolution) into a monotonic motor count.
* :class:`MultiEncoderTracker` -- one such accumulator per camera, routing the
  shared ``$ENC=<cam>,<pos>*`` stream by its camera tag. Needed because the three
  encoders share one SPI bus and the motor-selection relays pick which answers.

UNITS: positions are ENCODER COUNTS (what the AMT203S measures, strength-independent).
Motor commands are PULSES (a fixed-duration H-bridge burst, ~24 counts at str0). The
w10 autofocus (:mod:`capture.autofocus_w10`) converts between them itself.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)

# ``$ENC=<pos>*`` (single-encoder firmware) or ``$ENC=<cam>,<pos>*`` (multi-encoder
# firmware, where the camera-selection relays decide whose shaft answers). The
# camera group is optional so one regex reads both.
_ENC_RE = re.compile(r"\$ENC=(?:(\d+),)?(-?\d+)\*")


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
        it did (so callers can cheaply tell encoder lines from other PCB chatter).

        Any camera tag is ignored here -- this class is one shaft's accumulator.
        Use :class:`MultiEncoderTracker` to route a tagged stream by camera."""
        m = _ENC_RE.search(line)
        if not m:
            return False
        self.update_raw(int(m.group(2)))
        return True

    @property
    def position(self) -> int:
        with self._lock:
            return self._accum

    @property
    def present(self) -> bool:
        with self._lock:
            return self._present


class MultiEncoderTracker:
    """One :class:`EncoderTracker` per camera, fed from the single ``$ENC`` stream.

    Three AMT203S encoders share one SPI bus and the motor-selection relays decide
    which one answers, so the ClearCore can only ever read the SELECTED camera's
    shaft. It tags each line with that camera (``$ENC=<cam>,<pos>*``) and we route
    on the tag.

    Routing is not bookkeeping, it is correctness: :meth:`EncoderTracker.update_raw`
    folds every reading in by the SHORTEST signed path from the previous one, so
    feeding two different shafts into one accumulator fabricates up to half a
    revolution of travel on every camera switch.

    Because only one encoder is live at a time, the other two cameras' counts are
    by definition stale -- :meth:`age_s` says how stale, and callers that stamp a
    count onto imagery must record that (a stale count silently read as current is
    exactly what makes a focus bracket unanalysable afterwards).

    An untagged ``$ENC=<pos>*`` from the older single-encoder firmware is credited
    to ``default_cam``, which is correct on that rig: one encoder, one selection.
    """

    def __init__(
        self,
        cameras: tuple[int, ...] = (1, 2, 3),
        counts_per_rev: int = 4096,
    ) -> None:
        self._cpr = int(counts_per_rev)
        self._trackers = {
            int(c): EncoderTracker(self._cpr) for c in cameras
        }
        self._last_update: dict[int, float] = {}
        self._reported: Optional[int] = None
        self._lock = threading.Lock()

    @property
    def cameras(self) -> tuple[int, ...]:
        return tuple(sorted(self._trackers))

    @property
    def reported_camera(self) -> Optional[int]:
        """Camera the PCB last said it read, or None before the first reading.

        This is the authoritative selection -- the firmware reports what the relays
        actually are, not what the host last asked for."""
        with self._lock:
            return self._reported

    def update_line(self, line: str, default_cam: Optional[int] = None) -> Optional[int]:
        """Fold in an ``$ENC`` reading if *line* carries one.

        Returns the camera number the reading was credited to, or None if the line
        was not an encoder line (or named a camera we do not track).
        """
        m = _ENC_RE.search(line)
        if not m:
            return None
        cam = int(m.group(1)) if m.group(1) is not None else default_cam
        if cam is None:
            return None
        tracker = self._trackers.get(int(cam))
        if tracker is None:
            log.debug("$ENC for untracked camera %s: %r", cam, line)
            return None
        cam = int(cam)
        tracker.update_raw(int(m.group(2)))
        with self._lock:
            self._reported = cam
            # Sentinel reads (-1) prove the bus answered for this camera but the
            # encoder did not, so they are NOT freshness -- `present` stays False
            # and the age must keep growing rather than reset on a failed read.
            if tracker.present:
                self._last_update[cam] = time.monotonic()
        return cam

    def position(self, cam: int) -> int:
        """Unwrapped count for *cam* (0 if that camera has never reported)."""
        tracker = self._trackers.get(int(cam))
        return tracker.position if tracker is not None else 0

    def present(self, cam: int) -> bool:
        """True once *cam* has produced at least one valid (non-sentinel) reading."""
        tracker = self._trackers.get(int(cam))
        return bool(tracker.present) if tracker is not None else False

    def age_s(self, cam: int) -> Optional[float]:
        """Seconds since *cam* last produced a valid reading, or None if never."""
        with self._lock:
            ts = self._last_update.get(int(cam))
        return None if ts is None else max(0.0, time.monotonic() - ts)

    def reset(self) -> None:
        """Drop every accumulator and all freshness.

        Called on (re)connect: the ClearCore re-references its encoders, so counts
        from before a reconnect are not comparable with counts after it -- which is
        what ``encoder_epoch`` exists to tell consumers.
        """
        with self._lock:
            self._last_update.clear()
            self._reported = None
        for cam in list(self._trackers):
            self._trackers[cam] = EncoderTracker(self._cpr)
