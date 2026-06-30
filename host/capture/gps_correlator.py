"""Match SBF ExtEvent blocks to the camera frames they triggered.

Architecture
============

The ClearCore PCB fires every trigger pulse to all three cameras AND
to the Septentrio receiver's EXT_EVENT input simultaneously, so each
trigger produces:

* 1 ``ExtEventPVTGeodetic`` block on the GPS COM port, and
* 1 raw frame from each of the 3 cameras (300-400 ms later).

The correlator runs as a single shared object that:

1. Receives events from the :class:`GpsReceiver` reader thread (call
   :meth:`add_event`).
2. Is queried per-frame by each :class:`AcquisitionThread`
   (:meth:`pop_fix_for`).

Hybrid matching strategy
========================

The PCB-driven trigger schedule looks like::

    ...|trig 1|...|trig 2|...|trig 3|...|trig 13|                          (sweep 1)
                                            ...|trig 1|...|trig 2|...      (sweep 2)
       ^-- ~200 ms --^                          ^-- 100 ms inter-sweep --^

The GPS receiver sees the SAME pulse train on EXT_EVENT, so the
inter-event spacing matches: ~200 ms intra-sweep, ~300 ms between
sweeps (the ~100 ms inter-sweep pause adds to the normal 200 ms slot).

We exploit that asymmetry. The correlator tags every event as it
arrives with a ``(gps_sweep_gen, gps_event_in_sweep)`` pair:

* On the first event ever, ``gen=1``, ``slot=1``.
* On every subsequent event, if the **GPS-clock TOW gap** since the
  previous event is greater than ``SWEEP_TOW_GAP_MS`` (default 280 ms),
  we start a new generation (``gen += 1``, ``slot = 1``). Otherwise
  ``slot`` advances modulo ``sweep_size``.

  TOW (the receiver's timestamp of the trigger pulse) is used rather
  than host-arrival time because the USB-CDC reader delivers events in
  batches -- a whole ``read()`` worth of events share one host
  timestamp, which collapses intra-sweep gaps to 0 ms and fabricates
  huge inter-batch gaps, shredding host-gap segmentation. The host gap
  (``SWEEP_GAP_MS``) is retained only as a fallback when a TOW is
  Do-Not-Use. See ``tools/sbf_gap_analysis.py`` for the live evidence.

A camera-side ``frame_in_sweep`` is the natural lookup key into that
slot table: trigger ``k`` of sweep ``s`` corresponds to GPS slot ``k``
of the same physical sweep, regardless of which generation index the
GPS happened to assign.

Per-call matching (:meth:`pop_fix_for`):

* **Counting / direct lookup**: search the slot table for entries
  whose slot equals the camera's ``frame_in_sweep``. This is your
  strategy #2 (kth event matches kth frame), made robust by tagging
  events on the GPS side rather than maintaining per-camera FIFO
  pointers (a single-camera drop can't poison the other two cameras
  because none of them maintain shared state).
* **Sweep-pause / gap detection**: the ``SWEEP_TOW_GAP_MS`` threshold
  is your strategy #3, used here to *segment* the GPS event stream
  into sweep generations without depending on the camera side.
* **Staleness timeout**: an entry whose ``host_perf_ns`` is older
  than ``STALE_AHEAD_MS`` relative to the frame is dropped from
  consideration. This is your strategy #1 -- if no event landed
  close to the frame in time, the preview reads "NO GPS" instead
  of getting paired with last sweep's leftover.
* **Future tolerance**: an event that's more than ``FUTURE_MS``
  ahead of the frame is also rejected; SBF event latency is ~5 ms
  in practice so being meaningfully in the future means we haven't
  matched this trigger yet and should return ``None``.
* **TOW cross-check** (diagnostic only): if the matched event's
  GPS-clock TOW differs from the expected ~200 ms slot cadence we
  emit a debug log so systematic skew is visible without failing
  the match itself.
* **Latest-fix fallback**: when the slot lookup finds nothing for
  this frame's ``frame_in_sweep`` but a fresh event of *any* slot is
  still sitting in the table (within ``LATEST_FALLBACK_AHEAD_MS``), we
  return that most-recent fix rather than ``None``. This rescues the
  case where the camera's ``frame_in_sweep`` drifts out of phase with
  the GPS sweep segmentation for a whole line: the correct position is
  present and temporally adjacent, so a near fix beats writing no GPS.
  A genuine GPS outage leaves the latest event older than the bound,
  so the fallback stays silent exactly when it should.

The trade-off is that a *single missed GPS event* in the middle of a
sweep shifts every subsequent slot by one: the GPS-side counter
``slot`` is purely sequential. We accept that rare case as "wrong
fix on that frame" rather than over-engineer for it. The
``SBF_TOW_GAP_MS`` cross-check below logs it.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from .gps_fix import GpsFix
from .sbf_parser import DEFAULT_LEAP_SECONDS, DNU_U4, SbfExtEvent

log = logging.getLogger(__name__)


# ── Tunables ──────────────────────────────────────────────────────────

# Minimum host-arrival gap between consecutive events that signals a
# new sweep. The PCB's intra-sweep spacing is ~200 ms and the
# inter-sweep pause is ~100 ms longer; pick a threshold halfway between
# 200 ms and 300 ms so neither end of the distribution misclassifies.
SWEEP_GAP_MS = 250.0

# Sweep segmentation threshold on the GPS-clock TOW delta between
# consecutive events. This is the PRIMARY signal: the SBF event's TOW is
# the receiver's timestamp of the physical trigger pulse, immune to
# USB-CDC read batching (the host-arrival clock lumps a whole read's worth
# of events into one timestamp, which shreds host-gap segmentation -- see
# tools/sbf_gap_analysis.py). The PCB's intra-sweep cadence measures
# ~210-261 ms and the inter-sweep pause pushes the gap to >=300 ms; 280 ms
# sits in the clean gap between the two and is stable across 270-300 ms on
# live recordings. ``SWEEP_GAP_MS`` (host-arrival) is kept only as a
# fallback for events whose TOW is Do-Not-Use.
SWEEP_TOW_GAP_MS = 280.0

# Reject a slot match whose event is more than this much older than the
# current frame. At 5 Hz triggers we expect ~200 ms event spacing and
# the camera frame lands ~300 ms after its trigger. 1500 ms gives
# generous headroom while still catching "this slot table is from the
# previous sweep and the new one's event hasn't landed yet".
STALE_AHEAD_MS = 1500.0

# Reject a slot match whose event lies more than this far in the future
# of the frame. SBF latency over USB is ~5 ms, so anything beyond
# 200 ms means we're looking at a future event that doesn't apply.
FUTURE_MS = 200.0

# When the per-slot lookup finds nothing for a frame, fall back to the
# most recent fix of *any* slot if it is no older than this relative to
# the frame. Sized to match ``STALE_AHEAD_MS`` so the fallback trusts a
# fix exactly as fresh as a direct slot match would. Set to 0 to disable
# the fallback entirely (revert to "no slot match -> no GPS").
LATEST_FALLBACK_AHEAD_MS = STALE_AHEAD_MS

# Diagnostic only: log when consecutive matched events fall outside
# this TOW gap window. The PCB nominally fires every 200 ms intra-sweep.
SBF_TOW_GAP_MIN_MS = 100.0
SBF_TOW_GAP_MAX_MS = 350.0  # tolerates the ~300 ms inter-sweep slot

# Max GPS generations to keep in the slot table. Each generation has
# at most ``sweep_size`` (=13) slots so this caps the table at ~130
# entries.
MAX_GENERATIONS_KEPT = 10

# Running-average aircraft heading from consecutive GPS fixes.
HEADING_MIN_DISPLACEMENT_M = 0.5
HEADING_BEARING_WINDOW = 8


# ── Internal types ────────────────────────────────────────────────────


@dataclass
class _SlotEntry:
    seq: int                 # monotonic per-receiver event sequence number
    host_perf_ns: int        # time.perf_counter_ns() at parser completion
    event: SbfExtEvent       # decoded SBF block
    fix: GpsFix              # converted to GpsFix once at add_event
    gps_sweep_gen: int       # 1-based; bumped each time a SWEEP_GAP_MS gap is seen
    gps_event_in_sweep: int  # 1..sweep_size


@dataclass
class _CameraState:
    matched: int = 0
    stale: int = 0
    out_of_future: int = 0
    no_event: int = 0
    # Frames rescued by the latest-fix fallback: the per-slot lookup found
    # nothing but a fresh fix of another slot was returned instead.
    fallback_latest: int = 0
    last_block_reason: Optional[str] = None
    # Diagnostic: signed age (ms) = frame_perf - closest_slot_entry_host of
    # the nearest slot candidate on each *stale* miss. Positive = the best
    # candidate event arrived before the frame (we were too late / window
    # too tight); negative = it arrived after the frame (frame ran ahead of
    # the event). Tells us whether a stale storm is "just over the 1500 ms
    # window" (widen it) or a gross clock offset (deeper bug).
    stale_age_min_ms: Optional[float] = None
    stale_age_max_ms: Optional[float] = None
    stale_age_sum_ms: float = 0.0


# ── Correlator ────────────────────────────────────────────────────────


class GpsCorrelator:
    """Thread-safe correlator: GPS events in, per-camera fixes out.

    Construction is cheap; instantiate one per process and share across
    pipelines via the ``gps_provider`` callable. All public methods are
    safe to call concurrently from the receiver reader thread + each
    camera's :class:`AcquisitionThread`.
    """

    def __init__(
        self,
        *,
        sweep_size: int = 13,
        sweep_gap_ms: float = SWEEP_GAP_MS,
        sweep_tow_gap_ms: float = SWEEP_TOW_GAP_MS,
        stale_ahead_ms: float = STALE_AHEAD_MS,
        future_ms: float = FUTURE_MS,
        latest_fallback_ms: float = LATEST_FALLBACK_AHEAD_MS,
        leap_seconds: int = DEFAULT_LEAP_SECONDS,
        max_generations: int = MAX_GENERATIONS_KEPT,
    ) -> None:
        if sweep_size < 1:
            raise ValueError("sweep_size must be >= 1")
        self._sweep_size = int(sweep_size)
        self._sweep_gap_ns = int(sweep_gap_ms * 1_000_000)
        self._sweep_tow_gap_ms = float(sweep_tow_gap_ms)
        self._stale_ahead_ns = int(stale_ahead_ms * 1_000_000)
        self._future_ns = int(future_ms * 1_000_000)
        # <= 0 disables the latest-fix fallback.
        self._fallback_ahead_ns = int(latest_fallback_ms * 1_000_000)
        self._leap_seconds = int(leap_seconds)
        self._max_generations = max(2, int(max_generations))
        self._lock = threading.Lock()
        # Slot table keyed by (gen, slot). Newer-generation entries
        # naturally outrank older ones during lookup because we score
        # by "smallest non-negative age".
        self._by_slot: dict[tuple[int, int], _SlotEntry] = {}
        self._next_seq = 0
        self._gps_sweep_gen = 0   # 0 = no events received yet
        self._gps_event_in_sweep = 0
        self._last_event_host_ns = -1
        self._last_event_tow_ms = -1  # -1 = none yet; else last valid TOW
        self._latest: Optional[_SlotEntry] = None
        self._cams: dict[str, _CameraState] = {}
        self._connected = False
        # Running-average heading (bearing between consecutive fixes).
        self._heading_deg: Optional[float] = None
        self._heading_bearing_samples: list[tuple[float, float]] = []
        self._heading_last_lat: Optional[float] = None
        self._heading_last_lon: Optional[float] = None

    # ── connection state ────────────────────────────────────────────

    def set_connected(self, connected: bool) -> None:
        with self._lock:
            self._connected = bool(connected)
            # On disconnect, keep the slot table so the preview can
            # show "stale" until the staleness check kicks in.

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    # ── ingest ──────────────────────────────────────────────────────

    def add_event(self, event: SbfExtEvent, host_perf_ns: int) -> None:
        """Append a freshly decoded SBF event to the slot table.

        Called from the :class:`GpsReceiver` reader thread (see
        :meth:`GpsReceiver.set_event_callback`). Constant-time.
        """
        fix = self._event_to_fix(event)
        with self._lock:
            host_perf_ns = int(host_perf_ns)
            seq = self._next_seq
            self._next_seq = seq + 1

            # Sweep segmentation. Prefer the GPS-clock TOW gap (immune to
            # USB read batching); fall back to the host-arrival gap only
            # when a TOW is Do-Not-Use. ``tow_valid`` guards both the
            # current and previous event having a usable TOW.
            tow_ms = int(event.tow_ms)
            tow_valid = (
                tow_ms != DNU_U4
                and self._last_event_tow_ms >= 0
            )

            if self._last_event_host_ns < 0:
                # First event ever
                self._gps_sweep_gen = 1
                self._gps_event_in_sweep = 1
            else:
                if tow_valid:
                    tow_gap_ms = tow_ms - self._last_event_tow_ms
                    # A new sweep is signalled by a TOW gap above the
                    # threshold, or any backwards/zero step (week rollover,
                    # receiver reset, out-of-order) which we treat
                    # conservatively as a boundary.
                    is_new_sweep = (
                        tow_gap_ms > self._sweep_tow_gap_ms
                        or tow_gap_ms <= 0
                    )
                else:
                    is_new_sweep = (
                        (host_perf_ns - self._last_event_host_ns)
                        > self._sweep_gap_ns
                    )

                if is_new_sweep:
                    self._gps_sweep_gen += 1
                    self._gps_event_in_sweep = 1
                else:
                    next_slot = self._gps_event_in_sweep + 1
                    if next_slot > self._sweep_size:
                        # Slot overflow without a detected pause: defensively
                        # start a new generation so we don't overwrite the
                        # previous sweep's entries.
                        self._gps_sweep_gen += 1
                        self._gps_event_in_sweep = 1
                    else:
                        self._gps_event_in_sweep = next_slot

            entry = _SlotEntry(
                seq=seq,
                host_perf_ns=host_perf_ns,
                event=event,
                fix=fix,
                gps_sweep_gen=self._gps_sweep_gen,
                gps_event_in_sweep=self._gps_event_in_sweep,
            )
            self._by_slot[(self._gps_sweep_gen, self._gps_event_in_sweep)] = entry
            self._latest = entry
            self._last_event_host_ns = host_perf_ns
            # Remember the last *valid* TOW so a single DNU event doesn't
            # poison the next event's gap computation.
            if tow_ms != DNU_U4:
                self._last_event_tow_ms = tow_ms
            self._update_heading_locked(fix)
            self._trim_locked()

    def _trim_locked(self) -> None:
        """Drop entries from generations older than the keep window.

        ``MAX_GENERATIONS_KEPT`` covers ~10 sweeps (~26 s at 5 Hz),
        which is plenty for the worst-case lag the staleness check
        already enforces. Trimming bounds the dict size on long runs.
        """
        min_gen = self._gps_sweep_gen - self._max_generations + 1
        if min_gen <= 1:
            return
        stale_keys = [k for k in self._by_slot if k[0] < min_gen]
        for k in stale_keys:
            del self._by_slot[k]

    # ── query ───────────────────────────────────────────────────────

    def pop_fix_for(
        self,
        camera_serial: str,
        frame_perf_ns: int,
        sweep: int,
        frame_in_sweep: int,
    ) -> Optional[GpsFix]:
        """Return the GpsFix matching this camera's current frame, or None.

        Looks up ``slot = frame_in_sweep`` in the GPS slot table and
        picks the entry whose host arrival is closest to (and within
        tolerance of) ``frame_perf_ns``. If no slot entry qualifies but
        the freshest event of *any* slot is still within
        ``LATEST_FALLBACK_AHEAD_MS`` of the frame, that latest fix is
        returned instead (see the module docstring's "Latest-fix
        fallback"). Returns ``None`` only when nothing fresh exists; the
        caller is expected to surface that as "NO GPS" in the preview and
        to write the frame without a GPS block.

        Thread-safe: callers from different camera threads + the
        receiver thread can all hit this concurrently.
        """
        with self._lock:
            state = self._cams.setdefault(camera_serial, _CameraState())
            if self._next_seq == 0:
                state.no_event += 1
                return None

            # Walk all entries whose slot matches frame_in_sweep, pick
            # the one with smallest non-negative ``age = frame - host``
            # within ``stale_ahead``. If the only candidates are in the
            # future of the frame and beyond ``future_ms``, return None.
            best: Optional[_SlotEntry] = None
            best_age_ns = self._stale_ahead_ns + 1
            future_best: Optional[_SlotEntry] = None
            future_best_dt = self._future_ns + 1
            # Track the closest slot candidate by |age|, ignoring windows,
            # so a stale miss can report how far off the nearest event was.
            closest_age_ns: Optional[int] = None
            for (gen, slot), entry in self._by_slot.items():
                if slot != frame_in_sweep:
                    continue
                age = frame_perf_ns - entry.host_perf_ns
                if closest_age_ns is None or abs(age) < abs(closest_age_ns):
                    closest_age_ns = age
                if age >= 0:
                    if age <= self._stale_ahead_ns and age < best_age_ns:
                        best_age_ns = age
                        best = entry
                else:
                    dt = -age
                    if dt <= self._future_ns and dt < future_best_dt:
                        future_best_dt = dt
                        future_best = entry

            if best is not None:
                state.matched += 1
                # Diagnostic TOW gap check: compare against the previous
                # matched event on this camera. Logged at debug level so
                # production runs don't fill the log.
                # (No state cost; we just inspect last neighbour.)
                self._maybe_log_tow_gap_locked(state, best)
                state.last_block_reason = None
                return best.fix

            # No qualifying slot entry. Before giving up on this frame,
            # fall back to the most recent fix of *any* slot when it is
            # still fresh. This rescues the case where the camera's
            # frame_in_sweep has drifted out of phase with the GPS sweep
            # segmentation for a whole line: the correct position is
            # present in the table and temporally adjacent, just under a
            # different slot key. A real GPS outage leaves ``_latest``
            # older than the bound, so the fallback declines and the frame
            # still goes out with no fix.
            if self._fallback_ahead_ns > 0 and self._latest is not None:
                fb_age = frame_perf_ns - self._latest.host_perf_ns
                if -self._future_ns <= fb_age <= self._fallback_ahead_ns:
                    state.fallback_latest += 1
                    state.last_block_reason = None
                    return self._latest.fix

            if future_best is not None:
                state.out_of_future += 1
                # Don't return the future event -- the caller will try
                # again for the next frame; by then the event's age
                # will be normal.
                return None

            # Slot has no in-tolerance entry. Could be "GPS missed this
            # event" or "GPS lag transient"; either way the frame goes
            # out without a fix.
            # Distinguish stale vs no-event for stats.
            if closest_age_ns is not None:
                state.stale += 1
                age_ms = closest_age_ns / 1e6
                if (state.stale_age_min_ms is None
                        or age_ms < state.stale_age_min_ms):
                    state.stale_age_min_ms = age_ms
                if (state.stale_age_max_ms is None
                        or age_ms > state.stale_age_max_ms):
                    state.stale_age_max_ms = age_ms
                state.stale_age_sum_ms += age_ms
            else:
                state.no_event += 1
            return None

    def _maybe_log_tow_gap_locked(
        self, state: _CameraState, matched: _SlotEntry
    ) -> None:
        """Best-effort cross-check that TOW deltas match expectations."""
        # Find the previous entry in the same sweep generation, slot-1.
        prev = self._by_slot.get(
            (matched.gps_sweep_gen, matched.gps_event_in_sweep - 1)
        )
        if prev is None:
            return
        if matched.event.tow_ms < 0 or prev.event.tow_ms < 0:
            return
        gap_ms = (matched.event.tow_ms - prev.event.tow_ms)
        if gap_ms < SBF_TOW_GAP_MIN_MS or gap_ms > SBF_TOW_GAP_MAX_MS:
            log.debug(
                "[gps_corr] TOW gap out of expected window: gen=%d slot=%d "
                "gap=%d ms (expected %.0f-%.0f ms). A GPS event may have "
                "been missed; downstream fix could be off by one trigger.",
                matched.gps_sweep_gen, matched.gps_event_in_sweep,
                gap_ms, SBF_TOW_GAP_MIN_MS, SBF_TOW_GAP_MAX_MS,
            )

    # ── preview / status ────────────────────────────────────────────

    def latest_fix(self) -> Optional[GpsFix]:
        """Most recent fix, regardless of camera consumption.

        Used by the preview overlay so the operator always sees the
        current position even between sweeps. The caller is responsible
        for ageing the result (e.g. by comparing ``fix.timestamp_utc``
        with wall-clock now or by checking :meth:`age_of_latest`).
        """
        with self._lock:
            return self._latest.fix if self._latest is not None else None

    def age_of_latest_ns(self, *, now_perf_ns: Optional[int] = None) -> Optional[int]:
        """Nanoseconds elapsed since the most recent event was decoded.

        Returns ``None`` if no event has been received. Pass an explicit
        ``now_perf_ns`` to avoid a ``time`` call (used by tests).
        """
        with self._lock:
            if self._latest is None:
                return None
            now_ns = now_perf_ns if now_perf_ns is not None else time.perf_counter_ns()
            return max(0, int(now_ns) - int(self._latest.host_perf_ns))

    def latest_event(self) -> Optional[tuple[SbfExtEvent, int]]:
        """``(event, host_perf_ns)`` of the freshest entry, or None."""
        with self._lock:
            if self._latest is None:
                return None
            return self._latest.event, self._latest.host_perf_ns

    def heading_deg(self) -> Optional[float]:
        """Running-average bearing from North (deg, clockwise), or None."""
        with self._lock:
            return self._heading_deg

    def stats(self) -> dict:
        with self._lock:
            return {
                "connected": self._connected,
                "total_events": self._next_seq,
                "heading_deg": self._heading_deg,
                "gps_sweep_gen": self._gps_sweep_gen,
                "gps_event_in_sweep": self._gps_event_in_sweep,
                "slot_table_entries": len(self._by_slot),
                "cameras": {
                    cam: {
                        "matched": st.matched,
                        "stale": st.stale,
                        "out_of_future": st.out_of_future,
                        "no_event": st.no_event,
                        "fallback_latest": st.fallback_latest,
                        "stale_age_min_ms": st.stale_age_min_ms,
                        "stale_age_max_ms": st.stale_age_max_ms,
                        "stale_age_avg_ms": (
                            st.stale_age_sum_ms / st.stale
                            if st.stale else None
                        ),
                    }
                    for cam, st in self._cams.items()
                },
            }

    def reset_stats(self) -> None:
        """Zero the per-camera decision counters (diagnostic).

        Leaves the slot table and ``gps_sweep_gen`` untouched -- only the
        ``matched / stale / out_of_future / no_event / fallback_latest``
        tallies are cleared
        so a caller can measure a single capture run in isolation (e.g.
        reset on entering M0/M1, dump on leaving). Safe to call from the
        GUI thread while the receiver thread keeps ingesting events.
        """
        with self._lock:
            for st in self._cams.values():
                st.matched = 0
                st.stale = 0
                st.out_of_future = 0
                st.no_event = 0
                st.fallback_latest = 0
                st.last_block_reason = None
                st.stale_age_min_ms = None
                st.stale_age_max_ms = None
                st.stale_age_sum_ms = 0.0

    # ── conversion ──────────────────────────────────────────────────

    def _update_heading_locked(self, fix: GpsFix) -> None:
        """Causal running-average bearing from consecutive valid fixes."""
        if not fix.has_position:
            return
        lat = fix.latitude_deg
        lon = fix.longitude_deg
        if lat is None or lon is None:
            return
        if self._heading_last_lat is None or self._heading_last_lon is None:
            self._heading_last_lat = float(lat)
            self._heading_last_lon = float(lon)
            return
        dist_m = _haversine_m(
            self._heading_last_lat,
            self._heading_last_lon,
            float(lat),
            float(lon),
        )
        if dist_m < HEADING_MIN_DISPLACEMENT_M:
            return
        brg = _initial_bearing_deg(
            self._heading_last_lat,
            self._heading_last_lon,
            float(lat),
            float(lon),
        )
        self._heading_last_lat = float(lat)
        self._heading_last_lon = float(lon)
        rad = math.radians(brg)
        self._heading_bearing_samples.append((math.sin(rad), math.cos(rad)))
        if len(self._heading_bearing_samples) > HEADING_BEARING_WINDOW:
            self._heading_bearing_samples.pop(0)
        s_sum = sum(s for s, _ in self._heading_bearing_samples)
        c_sum = sum(c for _, c in self._heading_bearing_samples)
        if abs(s_sum) < 1e-15 and abs(c_sum) < 1e-15:
            return
        self._heading_deg = math.degrees(math.atan2(s_sum, c_sum)) % 360.0

    def _event_to_fix(self, event: SbfExtEvent) -> GpsFix:
        """Convert an SBF event into the shared :class:`GpsFix` dataclass.

        Sentinel-aware: any field that was Do-Not-Use in the SBF block
        is ``None`` on the event and therefore on the resulting fix.
        """
        ts_utc = event.utc_datetime(leap_seconds=self._leap_seconds)
        return GpsFix(
            latitude_deg=event.latitude_deg,
            longitude_deg=event.longitude_deg,
            altitude_m=event.height_m,
            timestamp_utc=ts_utc,
            satellites=event.n_sv,
            hdop=None,                 # PVTGeodetic doesn't carry HDOP
            fix_quality=event.fix_solution,
        )


# ── Stand-alone helpers ───────────────────────────────────────────────


def perf_ns_now() -> int:
    """``time.perf_counter_ns()`` wrapper for callers that don't want to import time."""
    return time.perf_counter_ns()


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (
        math.sin(dp * 0.5) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dl * 0.5) ** 2
    )
    return 2.0 * r * math.asin(min(1.0, math.sqrt(a)))


def _initial_bearing_deg(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Forward azimuth from point 1 to 2: degrees from North, clockwise."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    y = math.sin(dlam) * math.cos(phi2)
    x = (
        math.cos(phi1) * math.sin(phi2)
        - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    )
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
