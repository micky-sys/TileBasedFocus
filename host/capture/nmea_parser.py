"""Parse the RS232 NMEA feed from the SBF-to-NMEA bridge into :class:`GpsFix`.

Replaces :mod:`capture.sbf_parser` on the LIVE capture path. The receiver is no
longer read directly over USB: a bridge takes the Septentrio's SBF and emits
plain NMEA on an RS232 line, so the capture app now speaks NMEA and there is no
SBF-to-NMEA translation step left to get wrong.

``sbf_parser`` itself stays -- the offline geotag tools still read recorded SBF
from past flights, and those flights don't stop existing because the live wire
format changed.

Why this looks like the SBF parser
----------------------------------
:class:`GpsCorrelator` matches events to frames using a *receiver-clock*
timestamp of the trigger pulse, because host-arrival time was tried and failed
(USB-CDC delivered events in batches, collapsing the intra-sweep gaps the
segmentation depends on). So :class:`NmeaEvent` deliberately mirrors the shape
the correlator already consumes -- including the field still called ``tow_ms``,
which is now MILLISECONDS OF UTC DAY from the sentence's own time field rather
than GPS time-of-week. Both are monotonic millisecond counters used only for
*differences*, so the correlator's gap logic is unaffected; only the wrap period
changes (daily instead of weekly), and a wrap reads as a large negative gap,
which that code already treats as a new sweep generation.

Resolution matters here: the sweep segmentation distinguishes a ~200 ms
intra-sweep gap from a ~300 ms inter-sweep one, so the time field needs better
than ~50 ms. Standard ``hhmmss.ss`` gives 10 ms, which is enough. A bridge that
emitted whole seconds only would NOT be, and :func:`NmeaParser.stats` exposes
``coarse_time`` so that shows up as a number rather than as mysteriously bad
geotagging.

ponytail: only GGA and RMC are decoded, which is what "straight up NMEA" means
in practice. Ceiling: a proprietary event sentence (``$PSSN`` and friends) is
ignored and counted in ``unknown_sentences``. Upgrade path: add it to
``_SENTENCE_HANDLERS`` -- the dispatch is one entry per type.
"""

from __future__ import annotations

import datetime as _dt
import logging
from dataclasses import dataclass
from typing import Callable, Optional

import pynmea2

from .gps_fix import GpsFix

log = logging.getLogger(__name__)

# Longest credible NMEA sentence is 82 bytes; allow slack for proprietary ones
# but never let a stream with no newlines grow the buffer without bound.
MAX_SENTENCE_LEN = 256
_MS_PER_DAY = 24 * 60 * 60 * 1000

# "No time known" sentinel. The correlator treats any negative segmentation
# clock as unusable, same as it treats the SBF Do-Not-Use value.
NO_TIME_MS = -1


@dataclass(frozen=True)
class NmeaEvent:
    """One decoded position-bearing sentence.

    ``fix`` is built straight from the NMEA fields -- :class:`GpsFix` was already
    defined in NMEA terms (a GGA plus optionally its RMC), so nothing is
    translated between wire formats here.
    """

    fix: GpsFix
    tow_ms: int              # ms of UTC day; NO_TIME_MS when the sentence had no time
    sentence_type: str       # "GGA" / "RMC" -- which sentence produced this

    @property
    def has_position(self) -> bool:
        return self.fix.has_position


def _ms_of_day(t: Optional[_dt.time]) -> int:
    if t is None:
        return NO_TIME_MS
    return (
        t.hour * 3_600_000
        + t.minute * 60_000
        + t.second * 1000
        + t.microsecond // 1000
    )


def _int_or_none(raw) -> Optional[int]:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _float_or_none(raw) -> Optional[float]:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v


def _from_gga(msg, date: Optional[_dt.date]) -> Optional[GpsFix]:
    """GGA: position, altitude, quality, satellites, HDOP -- but no date."""
    t = getattr(msg, "timestamp", None)
    ts = None
    if t is not None and date is not None:
        ts = _dt.datetime.combine(date, t, tzinfo=_dt.timezone.utc)
    return GpsFix(
        latitude_deg=_float_or_none(getattr(msg, "latitude", None)),
        longitude_deg=_float_or_none(getattr(msg, "longitude", None)),
        altitude_m=_float_or_none(getattr(msg, "altitude", None)),
        timestamp_utc=ts,
        satellites=_int_or_none(getattr(msg, "num_sats", None)),
        hdop=_float_or_none(getattr(msg, "horizontal_dil", None)),
        fix_quality=_int_or_none(getattr(msg, "gps_qual", None)),
    )


def _from_rmc(msg, date: Optional[_dt.date]) -> Optional[GpsFix]:
    """RMC: position plus the DATE that GGA lacks. No altitude or HDOP.

    ``status`` is 'A' (active) or 'V' (void). A void RMC is mapped to
    ``fix_quality=0`` so :attr:`GpsFix.is_valid` rejects it, matching how a GGA
    quality of 0 is treated.
    """
    t = getattr(msg, "timestamp", None)
    d = getattr(msg, "datestamp", None) or date
    ts = None
    if t is not None and d is not None:
        ts = _dt.datetime.combine(d, t, tzinfo=_dt.timezone.utc)
    void = str(getattr(msg, "status", "") or "").upper() == "V"
    return GpsFix(
        latitude_deg=_float_or_none(getattr(msg, "latitude", None)),
        longitude_deg=_float_or_none(getattr(msg, "longitude", None)),
        altitude_m=None,
        timestamp_utc=ts,
        satellites=None,
        hdop=None,
        fix_quality=0 if void else None,
    )


# One entry per sentence type we understand. Add here to support more.
_SENTENCE_HANDLERS: dict[str, Callable] = {
    "GGA": _from_gga,
    "RMC": _from_rmc,
}


class NmeaParser:
    """Incremental NMEA parser: bytes in, :class:`NmeaEvent` list out.

    Mirrors :class:`SbfParser`'s interface (``feed`` / ``stats``) and its stat
    KEY NAMES, so the receiver and the GUI parser-health watcher work unchanged:
    ``bad_crc`` counts checksum failures, ``bad_length`` malformed sentences, and
    ``skipped_bytes`` leading garbage before a ``$``.

    Emits one event per position-bearing sentence, preferring GGA. A GGA carries
    no date, so the date comes from the most recent RMC and falls back to the
    host's UTC date until one arrives -- which is why an RMC is still worth
    parsing even though GGA has the richer position.
    """

    def __init__(self, *, prefer: str = "GGA") -> None:
        self._buf = bytearray()
        self._prefer = prefer
        self._last_date: Optional[_dt.date] = None
        # Suppress a second event for the same instant when the bridge sends
        # both GGA and RMC per trigger: the preferred sentence wins.
        self._last_emitted_ms = NO_TIME_MS
        self._stats = {
            "good_blocks": 0,       # well-formed sentences (any type)
            "events_decoded": 0,    # position-bearing sentences turned into events
            "bad_crc": 0,           # checksum mismatch
            "bad_length": 0,        # unparseable / over-long
            "skipped_bytes": 0,     # garbage before a '$'
            "buffered": 0,
            "unknown_sentences": 0,  # well-formed but not a type we decode
            "coarse_time": 0,       # sentences whose time had no sub-second field
        }

    # ── ingest ──────────────────────────────────────────────────────

    def feed(self, data: bytes) -> list[NmeaEvent]:
        self._buf.extend(data)
        out: list[NmeaEvent] = []
        while True:
            nl = self._buf.find(b"\n")
            if nl < 0:
                # Runaway line (no newline in sight) -- drop it rather than grow
                # the buffer forever on a wrong-baud port spewing noise.
                if len(self._buf) > MAX_SENTENCE_LEN * 4:
                    self._stats["skipped_bytes"] += len(self._buf)
                    self._buf.clear()
                break
            raw = bytes(self._buf[:nl])
            del self._buf[: nl + 1]
            evt = self._handle_line(raw)
            if evt is not None:
                out.append(evt)
        self._stats["buffered"] = len(self._buf)
        return out

    def _handle_line(self, raw: bytes) -> Optional[NmeaEvent]:
        line = raw.strip(b"\r\n")
        if not line:
            return None
        start = line.find(b"$")
        if start < 0:
            self._stats["skipped_bytes"] += len(line)
            return None
        if start > 0:
            self._stats["skipped_bytes"] += start
            line = line[start:]
        if len(line) > MAX_SENTENCE_LEN:
            self._stats["bad_length"] += 1
            return None
        try:
            text = line.decode("ascii")
        except UnicodeDecodeError:
            # Non-ASCII on the wire means this isn't NMEA (wrong baud, or still
            # binary SBF) -- count as skipped so the health watcher complains.
            self._stats["skipped_bytes"] += len(line)
            return None
        try:
            msg = pynmea2.parse(text, check=True)
        except pynmea2.ChecksumError:
            self._stats["bad_crc"] += 1
            return None
        except Exception:
            self._stats["bad_length"] += 1
            return None

        self._stats["good_blocks"] += 1
        stype = str(getattr(msg, "sentence_type", "") or "").upper()
        handler = _SENTENCE_HANDLERS.get(stype)
        if handler is None:
            self._stats["unknown_sentences"] += 1
            return None

        # An RMC's date is what lets later GGAs carry a full timestamp.
        stamped = getattr(msg, "datestamp", None)
        if stamped is not None:
            self._last_date = stamped

        date = self._last_date or _dt.datetime.now(_dt.timezone.utc).date()
        fix = handler(msg, date)
        if fix is None or not fix.has_position:
            return None

        t = getattr(msg, "timestamp", None)
        ms = _ms_of_day(t)
        if t is not None and t.microsecond == 0:
            # Whole-second timestamp: too coarse to segment 200 ms from 300 ms
            # gaps. Surfaced in stats rather than silently degrading matching.
            self._stats["coarse_time"] += 1

        # Same instant already emitted from the preferred sentence -> skip the
        # duplicate rather than let the correlator see two events per trigger.
        if ms != NO_TIME_MS and ms == self._last_emitted_ms and stype != self._prefer:
            return None
        self._last_emitted_ms = ms

        self._stats["events_decoded"] += 1
        return NmeaEvent(fix=fix, tow_ms=ms, sentence_type=stype)

    # ── diagnostics ─────────────────────────────────────────────────

    def stats(self) -> dict:
        return dict(self._stats)


def _sentence(body: str) -> str:
    """``'GPGGA,...'`` -> ``'$GPGGA,...*XX'`` with the correct XOR checksum.

    Test sentences are built rather than pasted: editing a pasted sentence's
    fields silently invalidates its checksum, and the parser would then reject
    it as corrupt while the test looked like a logic failure."""
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return f"${body}*{cs:02X}"


def self_check() -> int:
    """Assertions over the pieces that would silently corrupt geotagging."""
    p = NmeaParser()

    # A real GGA: RTK-fixed, 8 sats, with a 10 ms-resolution time field.
    gga = _sentence(
        "GPGGA,123519.50,4807.038,N,01131.000,E,4,08,0.9,545.4,M,46.9,M,,"
    )
    events = p.feed((gga + "\r\n").encode())
    assert len(events) == 1, events
    ev = events[0]
    assert ev.sentence_type == "GGA"
    assert ev.fix.fix_quality == 4, ev.fix.fix_quality
    assert ev.fix.satellites == 8, ev.fix.satellites
    assert abs(ev.fix.latitude_deg - 48.1173) < 1e-3, ev.fix.latitude_deg
    assert abs(ev.fix.longitude_deg - 11.5166) < 1e-3, ev.fix.longitude_deg
    assert abs(ev.fix.altitude_m - 545.4) < 1e-6
    assert ev.fix.is_valid
    # 12:35:19.50 UTC -> ms of day, the correlator's segmentation clock.
    assert ev.tow_ms == ((12 * 3600) + (35 * 60) + 19) * 1000 + 500, ev.tow_ms

    # Gap arithmetic is the only thing the correlator does with tow_ms, so a
    # 200 ms trigger spacing must come out as 200.
    nxt = p.feed(
        (_sentence(
            "GPGGA,123519.70,4807.038,N,01131.000,E,4,08,0.9,545.4,M,46.9,M,,"
        ) + "\r\n").encode()
    )
    assert len(nxt) == 1, nxt
    assert nxt[0].tow_ms - ev.tow_ms == 200, nxt[0].tow_ms - ev.tow_ms

    # A corrupted checksum must be counted, not accepted.
    before = p.stats()["bad_crc"]
    wrong = "01" if not gga.endswith("01") else "02"
    p.feed((gga[:-2] + wrong + "\r\n").encode())
    assert p.stats()["bad_crc"] == before + 1, p.stats()

    # Sentences split across reads must still decode exactly once.
    p2 = NmeaParser()
    half = (gga + "\r\n").encode()
    assert p2.feed(half[:20]) == []
    got = p2.feed(half[20:])
    assert len(got) == 1, got

    # RMC supplies the date a GGA cannot. pynmea2 renders a correct checksum
    # from str(), so we build the void variant rather than hand-computing one.
    fields = ["123519", "A", "4807.038", "N", "01131.000", "E",
              "022.4", "084.4", "230394", "003.1", "W"]
    p3 = NmeaParser()
    evs = p3.feed((str(pynmea2.RMC("GP", "RMC", tuple(fields))) + "\r\n").encode())
    assert len(evs) == 1 and evs[0].sentence_type == "RMC", evs
    assert evs[0].fix.timestamp_utc is not None, "RMC must carry its own date"
    assert evs[0].fix.timestamp_utc.year == 1994, evs[0].fix.timestamp_utc
    assert evs[0].fix.is_valid

    # A void ('V') RMC has a position but no usable fix.
    fields[1] = "V"
    p3b = NmeaParser()
    evs = p3b.feed((str(pynmea2.RMC("GP", "RMC", tuple(fields))) + "\r\n").encode())
    assert len(evs) == 1, evs
    assert evs[0].fix.has_position, "void RMC still parses a position"
    assert not evs[0].fix.is_valid, "void RMC must not count as a valid fix"

    # A GGA arriving after that RMC inherits its date (GGA has none of its own).
    got = p3.feed(
        (gga + "\r\n").encode()
    )
    assert len(got) == 1 and got[0].fix.timestamp_utc is not None, got
    assert got[0].fix.timestamp_utc.year == 1994, got[0].fix.timestamp_utc

    # Unknown sentence types are ignored and counted, never fatal.
    p4 = NmeaParser()
    gsv = _sentence("GPGSV,3,1,11,03,03,111,00,04,15,270,00")
    assert p4.feed((gsv + "\r\n").encode()) == []
    assert p4.stats()["unknown_sentences"] == 1, p4.stats()

    # Garbage with no newline must not grow the buffer without bound.
    p5 = NmeaParser()
    p5.feed(b"\x00\xff" * 1000)
    assert p5.stats()["buffered"] <= MAX_SENTENCE_LEN * 4, p5.stats()

    print("nmea_parser self-check OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_check())
