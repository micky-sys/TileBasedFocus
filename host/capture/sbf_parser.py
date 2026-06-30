"""Septentrio Binary Format (SBF) stream parser.

Hand-rolled stream decoder for the subset of SBF this app cares about:
the ``ExtEventPVTGeodetic`` block (ID 4038), one of which is emitted by
a Septentrio receiver each time its EXT_EVENT input sees a trigger
pulse from the ClearCore PCB. Every event-time PVT block carries the
GPS-clock time of the pulse plus the receiver's geodetic position at
that instant.

Reference: Septentrio "SBF Reference Guide" (mosaic-X5 firmware v4.x;
the block layout is firmware-stable).

Wire-level framing (sec. 4.1.1):

* Sync: two bytes ``0x24 0x40`` = ``b"$@"``.
* CRC: 16-bit CRC-CCITT (``poly 0x1021``, seed ``0``, no reflection,
  no final XOR) over every byte from the ID field through the end of
  the block.
* ID: 16-bit little-endian. Low 13 bits = block number, high 3 bits =
  block revision (backwards-compatible additions). We mask on the low
  13 bits before comparing.
* Length: 16-bit little-endian, total block size including the 8-byte
  header. Always a multiple of 4.

ExtEventPVTGeodetic (block 4038) shares its body layout with
PVTGeodetic (block 4007) starting right after the 8-byte block
header; the only difference is the semantics of TOW/WNc (these refer
to the moment of the EXT_EVENT pulse, not the PVT epoch).

Do-Not-Use values for fields we surface:

* unsigned integers: all-ones (``0xFF``/``0xFFFF``/``0xFFFFFFFF``).
* f4 / f8: ``-2.0e10`` exactly (Septentrio convention).

This module is pure stdlib + ``struct`` + ``datetime`` so it can run
inside the writer subprocess or a unit test without dragging in pyserial
or Qt.
"""

from __future__ import annotations

import datetime as _dt
import logging
import struct
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────────

SYNC = b"$@"
HEADER_LEN = 8  # sync(2) + crc(2) + id(2) + length(2)
BLOCK_ID_MASK = 0x1FFF
BLOCK_REV_SHIFT = 13

BLOCK_ID_EXT_EVENT_PVT_GEODETIC = 4038
BLOCK_ID_EXT_EVENT_PVT_CARTESIAN = 4037  # logged but not decoded
BLOCK_ID_EXT_EVENT = 5924  # logged but not decoded

# Septentrio Do-Not-Use values
DNU_U1 = 0xFF
DNU_U2 = 0xFFFF
DNU_U4 = 0xFFFFFFFF
DNU_F = -2.0e10  # both f4 and f8

# GPS epoch (1980-01-06 00:00:00 UTC). Used to convert (WNc, TOW) to
# UTC for the EXIF GPS timestamp. Leap-second offset between GPS time
# and UTC is configurable via env at the receiver layer; for this
# parser we expose a helper that takes an explicit leap_seconds arg.
GPS_EPOCH_UTC = _dt.datetime(1980, 1, 6, tzinfo=_dt.timezone.utc)
DEFAULT_LEAP_SECONDS = 18  # as of 2017-01-01; update if the world adds another


# ── Data classes ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class SbfExtEvent:
    """Decoded ExtEventPVTGeodetic block (subset of fields we use).

    Distances are in metres, angles in degrees (converted from the
    block's native radians for ergonomics). Fields whose source value
    was the SBF Do-Not-Use marker are reported as ``None``.
    """

    tow_ms: int            # Time-of-week in milliseconds; receiver clock time of the trigger pulse
    wnc: int               # GPS week number (continuous, no rollover)
    mode: int              # GNSS PVT mode bit field (low 4 bits = solution type)
    error: int             # PVT error code (0 = no error)
    latitude_deg: Optional[float]
    longitude_deg: Optional[float]
    height_m: Optional[float]      # WGS-84 ellipsoidal
    undulation_m: Optional[float]  # geoid-ellipsoid separation
    n_sv: Optional[int]            # satellites used in the PVT
    block_rev: int                 # SBF block revision (>=0)

    @property
    def has_position(self) -> bool:
        return self.latitude_deg is not None and self.longitude_deg is not None

    @property
    def fix_solution(self) -> int:
        """Low 4 bits of Mode = PVT solution type (0 = no fix, 1 = standalone, ...)."""
        return int(self.mode) & 0x0F

    @property
    def is_valid_fix(self) -> bool:
        return self.error == 0 and self.fix_solution != 0 and self.has_position

    def utc_datetime(
        self, leap_seconds: int = DEFAULT_LEAP_SECONDS
    ) -> Optional[_dt.datetime]:
        """Combine (WNc, TOW) into UTC; returns None on Do-Not-Use values."""
        if self.tow_ms == DNU_U4 or self.wnc == DNU_U2:
            return None
        delta = _dt.timedelta(
            weeks=int(self.wnc),
            milliseconds=int(self.tow_ms) - int(leap_seconds) * 1000,
        )
        return GPS_EPOCH_UTC + delta


# ── CRC-CCITT ─────────────────────────────────────────────────────────


def crc16_ccitt(data: bytes) -> int:
    """SBF flavour: poly 0x1021, seed 0, no reflection, no final XOR.

    Same as XMODEM CRC. Implemented bit-serially to keep the module
    dependency-free; a 256-entry table version is in the comments for
    when the receiver upgrades and we start seeing >1 MB/s SBF.
    """
    crc = 0
    for b in data:
        crc ^= (b & 0xFF) << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


# ── Field helpers ─────────────────────────────────────────────────────


def _f8_or_none(raw: float) -> Optional[float]:
    return None if abs(raw - DNU_F) < 1.0 else float(raw)


def _f4_or_none(raw: float) -> Optional[float]:
    return None if abs(raw - DNU_F) < 1.0 else float(raw)


def _u1_or_none(raw: int) -> Optional[int]:
    return None if int(raw) == DNU_U1 else int(raw)


# Body of ExtEventPVTGeodetic (rev 0+) starting after the 8-byte block
# header. We only unpack the leading 67 bytes (through NrSV). Newer
# revisions append more fields; SBF is forward-compatible so we just
# ignore the tail.
_EXT_EVENT_PVTG_FMT = "<IHBBdddfffffdfBBB"
_EXT_EVENT_PVTG_SIZE = struct.calcsize(_EXT_EVENT_PVTG_FMT)


def _decode_ext_event_pvt_geodetic(
    payload: bytes, block_rev: int
) -> Optional[SbfExtEvent]:
    if len(payload) < _EXT_EVENT_PVTG_SIZE:
        log.warning(
            "ExtEventPVTGeodetic payload truncated: %d < %d bytes",
            len(payload), _EXT_EVENT_PVTG_SIZE,
        )
        return None
    fields = struct.unpack_from(_EXT_EVENT_PVTG_FMT, payload, 0)
    (
        tow_ms, wnc, mode, error,
        lat_rad, lon_rad, height_m,
        undulation_m, _vn, _ve, _vu, _cog,
        _rx_clk_bias, _rx_clk_drift,
        _time_system, _datum, nr_sv,
    ) = fields

    import math

    def _deg(rad: float) -> Optional[float]:
        v = _f8_or_none(rad)
        return None if v is None else math.degrees(v)

    return SbfExtEvent(
        tow_ms=int(tow_ms),
        wnc=int(wnc),
        mode=int(mode),
        error=int(error),
        latitude_deg=_deg(lat_rad),
        longitude_deg=_deg(lon_rad),
        height_m=_f8_or_none(height_m),
        undulation_m=_f4_or_none(undulation_m),
        n_sv=_u1_or_none(nr_sv),
        block_rev=int(block_rev),
    )


# ── Streaming parser ──────────────────────────────────────────────────


class SbfParser:
    """Stateful byte-stream parser. Feed it bytes, get decoded events.

    Decoding strategy follows section 4.1.12 of the SBF Reference Guide:

    1. Find the ``$@`` sync.
    2. Read CRC + ID + Length.
    3. Read ``Length`` total bytes; verify CRC.
    4. If the block is ExtEventPVTGeodetic, decode; otherwise count and
       move on. Any of these checks can fail (the sync pattern can
       happen inside a payload), in which case we advance one byte past
       the bad sync and try again -- the SBF spec recommends exactly
       this resync behaviour.

    Thread safety: not safe to feed from multiple threads concurrently;
    the receiver wrapper owns a single instance and pumps it from the
    reader thread.
    """

    # Soft cap on the internal buffer; an SBF block is at most 64 KiB
    # (Length is u16) so 256 KiB is plenty of slack.
    MAX_BUFFER_BYTES = 256 * 1024

    def __init__(self) -> None:
        self._buf = bytearray()
        self.good_blocks = 0
        self.bad_crc = 0
        self.bad_length = 0
        self.skipped_bytes = 0
        self.events_decoded = 0
        self.events_skipped_unknown = 0

    def feed(self, data: bytes) -> list[SbfExtEvent]:
        """Append *data* to the internal buffer and decode complete blocks.

        Returns a list of ``SbfExtEvent`` for every fully-validated
        ExtEventPVTGeodetic block the new data completes. Other blocks
        are still consumed (and counted) but not returned.
        """
        if not data:
            return []
        self._buf.extend(data)
        if len(self._buf) > self.MAX_BUFFER_BYTES:
            # Defensive: a stream of garbage with no syncs would otherwise
            # grow without bound. Drop the oldest half and reset the
            # search.
            self.skipped_bytes += len(self._buf) // 2
            del self._buf[: len(self._buf) // 2]
        out: list[SbfExtEvent] = []
        while True:
            idx = self._buf.find(SYNC)
            if idx < 0:
                # No sync found. Keep at most the last byte (it might be
                # the leading ``$`` of a future sync) so we don't miss a
                # sync split across feeds.
                if self._buf and self._buf[-1:] == b"$":
                    keep = bytes(self._buf[-1:])
                    self.skipped_bytes += len(self._buf) - 1
                    self._buf.clear()
                    self._buf.extend(keep)
                else:
                    self.skipped_bytes += len(self._buf)
                    self._buf.clear()
                return out
            if idx > 0:
                self.skipped_bytes += idx
                del self._buf[:idx]
            if len(self._buf) < HEADER_LEN:
                return out
            crc_field = struct.unpack_from("<H", self._buf, 2)[0]
            id_field = struct.unpack_from("<H", self._buf, 4)[0]
            length = struct.unpack_from("<H", self._buf, 6)[0]
            if length < HEADER_LEN or length % 4 != 0:
                # Bogus length -> not a real block, false sync; skip past.
                self.bad_length += 1
                self.skipped_bytes += 2
                del self._buf[:2]
                continue
            if len(self._buf) < length:
                return out  # wait for more bytes
            # CRC covers ID + Length + payload (i.e. bytes[4:length]).
            crc_input = bytes(self._buf[4:length])
            if crc16_ccitt(crc_input) != crc_field:
                self.bad_crc += 1
                self.skipped_bytes += 2
                del self._buf[:2]
                continue
            self.good_blocks += 1
            block_id = id_field & BLOCK_ID_MASK
            block_rev = (id_field >> BLOCK_REV_SHIFT) & 0x07
            payload = bytes(self._buf[HEADER_LEN:length])
            del self._buf[:length]

            if block_id == BLOCK_ID_EXT_EVENT_PVT_GEODETIC:
                evt = _decode_ext_event_pvt_geodetic(payload, block_rev)
                if evt is not None:
                    self.events_decoded += 1
                    out.append(evt)
                else:
                    self.events_skipped_unknown += 1
            else:
                self.events_skipped_unknown += 1
        # unreachable

    def stats(self) -> dict:
        """Snapshot the cumulative parser counters (for telemetry / logs)."""
        return {
            "good_blocks": self.good_blocks,
            "bad_crc": self.bad_crc,
            "bad_length": self.bad_length,
            "skipped_bytes": self.skipped_bytes,
            "events_decoded": self.events_decoded,
            "events_skipped_unknown": self.events_skipped_unknown,
            "buffered": len(self._buf),
        }


# ── Encoder helpers (for tests) ───────────────────────────────────────


def encode_block(block_id: int, body: bytes, block_rev: int = 0) -> bytes:
    """Build a complete SBF block: ``sync + crc + id + length + body + padding``.

    Pads *body* with zero bytes so ``length`` is a multiple of 4. Useful
    for unit tests that synthesise SBF frames without a real receiver.
    """
    if block_id < 0 or block_id > BLOCK_ID_MASK:
        raise ValueError(f"block_id {block_id} out of 13-bit range")
    if block_rev < 0 or block_rev > 7:
        raise ValueError(f"block_rev {block_rev} out of 3-bit range")
    id_field = (block_id & BLOCK_ID_MASK) | ((block_rev & 0x07) << BLOCK_REV_SHIFT)
    raw_len = HEADER_LEN + len(body)
    pad = (-raw_len) % 4
    body_padded = body + b"\x00" * pad
    length = HEADER_LEN + len(body_padded)
    head_after_crc = struct.pack("<HH", id_field, length)
    crc = crc16_ccitt(head_after_crc + body_padded)
    return SYNC + struct.pack("<H", crc) + head_after_crc + body_padded


def encode_ext_event_pvt_geodetic(
    *,
    tow_ms: int,
    wnc: int,
    mode: int = 1,
    error: int = 0,
    latitude_deg: float = 32.0853,
    longitude_deg: float = 34.7818,
    height_m: float = 41.2,
    undulation_m: float = 17.5,
    n_sv: int = 12,
    block_rev: int = 0,
) -> bytes:
    """Synthesise an ExtEventPVTGeodetic block for tests / replay."""
    import math
    body = struct.pack(
        _EXT_EVENT_PVTG_FMT,
        int(tow_ms), int(wnc), int(mode) & 0xFF, int(error) & 0xFF,
        math.radians(latitude_deg), math.radians(longitude_deg), float(height_m),
        float(undulation_m), 0.0, 0.0, 0.0, 0.0,
        0.0, 0.0,
        0, 0, int(n_sv) & 0xFF,
    )
    return encode_block(BLOCK_ID_EXT_EVENT_PVT_GEODETIC, body, block_rev=block_rev)
