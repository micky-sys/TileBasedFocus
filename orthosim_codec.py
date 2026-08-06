"""Shared optical ground-truth bar codec for the orthophoto screen bench rig.

The OrthoSim display app (``tools/orthosim_gui.py``) renders a high-contrast
horizontal barcode band that encodes the *absolute* ground-truth of the
simulated flight: cumulative distance, a monotonic display tick, instantaneous
velocity and a few status flags. The Iron661 capture side
(``host/capture/orthosim_decode.py``) samples that band out of the camera frame
and decodes it back with this same codec.

Keeping the codec at the repo root (like ``tile_geometry`` / ``sharpness``) lets
both the ``tools/`` display app and the ``host/capture`` decoder import one
authoritative implementation, so the two can never drift out of sync.

Wire format (a single row of equal-width cells, left to right)::

    [SYNC (16 cells)] [PAYLOAD (88 cells)] [CRC8 (8 cells)]

Each cell is one bit: 1 = white, 0 = black. The payload is packed MSB-first in
this fixed field order:

    version       :  4 bits   format version (currently 2)
    tick          : 20 bits   monotonic display-refresh counter (wraps at 2^20)
    distance_mm   : 32 bits   absolute cumulative simulated ground distance (mm)
    velocity_mmps : 20 bits   instantaneous simulated ground speed (mm/s)
    gsd_cm_e2     : 16 bits   simulated camera GSD in 0.01 cm/cam-px (0..655.35);
                              the "altitude" proxy -- smaller GSD = lower flight
                              height. 0 = under-defined (display M not calibrated).
    flags         :  4 bits   bit0=seam, bit1=paused, bit2=calibration, bit3=rsvd

The CRC8 (poly 0x07, init 0x00) is computed over the 12 packed payload bytes so
a partially-smeared read is rejected rather than silently mis-decoded.

Format v2 (2026-08-05) replaced the v1 8-bit ``reserved`` pad with the 16-bit
``gsd_cm_e2`` altitude field; the display and capture sides both import this one
module, so the wire format can never drift between them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

# ── Field layout ─────────────────────────────────────────────────────────────
FORMAT_VERSION = 2

_FIELDS = (
    ("version", 4),
    ("tick", 20),
    ("distance_mm", 32),
    ("velocity_mmps", 20),
    ("gsd_cm_e2", 16),
    ("flags", 4),
)
PAYLOAD_BITS = sum(width for _, width in _FIELDS)   # 96
PAYLOAD_BYTES = PAYLOAD_BITS // 8                    # 12
CRC_BITS = 8

# A 16-cell sync/anchor prefix. Starts and ends with a white then black cell so
# the decoder gets a local white/black reference for thresholding, and the
# middle is a distinctive (Barker-like) run for correlation-based framing.
SYNC_PATTERN: List[int] = [1, 0, 1, 1, 1, 0, 0, 1, 0, 1, 0, 0, 1, 1, 0, 1]
SYNC_BITS = len(SYNC_PATTERN)

TOTAL_CELLS = SYNC_BITS + PAYLOAD_BITS + CRC_BITS   # 112

# Flag bit positions
FLAG_SEAM = 0x1
FLAG_PAUSED = 0x2
FLAG_CALIBRATION = 0x4

_DIST_MAX_MM = (1 << 32) - 1
_VEL_MAX_MMPS = (1 << 20) - 1
_GSD_MAX_E2 = (1 << 16) - 1
_TICK_MOD = 1 << 20


@dataclass
class GroundTruth:
    """Decoded (or to-be-encoded) ground-truth sample."""

    distance_mm: int = 0
    tick: int = 0
    velocity_mmps: int = 0
    gsd_cm_e2: int = 0
    flags: int = 0
    version: int = FORMAT_VERSION

    # ── convenience accessors ────────────────────────────────────────────
    @property
    def distance_m(self) -> float:
        return self.distance_mm / 1000.0

    @property
    def velocity_mps(self) -> float:
        return self.velocity_mmps / 1000.0

    @property
    def gsd_cm(self) -> float:
        """Simulated camera GSD in cm/cam-px (0.0 when the display left it unset)."""
        return self.gsd_cm_e2 / 100.0

    @property
    def seam(self) -> bool:
        return bool(self.flags & FLAG_SEAM)

    @property
    def paused(self) -> bool:
        return bool(self.flags & FLAG_PAUSED)

    @property
    def calibration(self) -> bool:
        return bool(self.flags & FLAG_CALIBRATION)


def crc8(data: bytes, poly: int = 0x07, init: int = 0x00) -> int:
    """Standard CRC-8 (poly 0x07, a.k.a. CRC-8/SMBUS with init 0x00)."""
    crc = init
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ poly) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def _clamp(value: int, lo: int, hi: int) -> int:
    return lo if value < lo else hi if value > hi else value


def _pack_payload(gt: GroundTruth) -> bytes:
    values = {
        "version": _clamp(int(gt.version), 0, (1 << 4) - 1),
        "tick": int(gt.tick) % _TICK_MOD,
        "distance_mm": _clamp(int(gt.distance_mm), 0, _DIST_MAX_MM),
        "velocity_mmps": _clamp(int(gt.velocity_mmps), 0, _VEL_MAX_MMPS),
        "gsd_cm_e2": _clamp(int(gt.gsd_cm_e2), 0, _GSD_MAX_E2),
        "flags": _clamp(int(gt.flags), 0, (1 << 4) - 1),
    }
    bits = 0
    nbits = 0
    for name, width in _FIELDS:
        bits = (bits << width) | (values[name] & ((1 << width) - 1))
        nbits += width
    return bits.to_bytes(PAYLOAD_BYTES, "big")


def _unpack_payload(payload: bytes) -> GroundTruth:
    bits = int.from_bytes(payload, "big")
    out = {}
    pos = PAYLOAD_BITS
    for name, width in _FIELDS:
        pos -= width
        out[name] = (bits >> pos) & ((1 << width) - 1)
    return GroundTruth(
        distance_mm=out["distance_mm"],
        tick=out["tick"],
        velocity_mmps=out["velocity_mmps"],
        gsd_cm_e2=out["gsd_cm_e2"],
        flags=out["flags"],
        version=out["version"],
    )


def _bytes_to_bits(data: bytes, nbits: int) -> List[int]:
    value = int.from_bytes(data, "big")
    return [(value >> (nbits - 1 - i)) & 1 for i in range(nbits)]


def _bits_to_bytes(bits: List[int], nbytes: int) -> bytes:
    value = 0
    for b in bits:
        value = (value << 1) | (1 if b else 0)
    return value.to_bytes(nbytes, "big")


def encode(gt: GroundTruth) -> List[int]:
    """Encode a :class:`GroundTruth` into a list of ``TOTAL_CELLS`` bits."""
    payload = _pack_payload(gt)
    crc = crc8(payload)
    cells: List[int] = list(SYNC_PATTERN)
    cells += _bytes_to_bits(payload, PAYLOAD_BITS)
    cells += _bytes_to_bits(bytes([crc]), CRC_BITS)
    return cells


def decode(cells: List[int]) -> Optional[GroundTruth]:
    """Decode ``TOTAL_CELLS`` bits back into a :class:`GroundTruth`.

    Returns ``None`` if the sync prefix or CRC does not match.
    """
    if len(cells) != TOTAL_CELLS:
        return None
    if [int(b) & 1 for b in cells[:SYNC_BITS]] != SYNC_PATTERN:
        return None
    payload_bits = [int(b) & 1 for b in cells[SYNC_BITS:SYNC_BITS + PAYLOAD_BITS]]
    crc_bits = [int(b) & 1 for b in cells[SYNC_BITS + PAYLOAD_BITS:]]
    payload = _bits_to_bytes(payload_bits, PAYLOAD_BYTES)
    crc_rx = _bits_to_bytes(crc_bits, 1)[0]
    if crc8(payload) != crc_rx:
        return None
    return _unpack_payload(payload)
