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

# ── Frame-step sync channel ──────────────────────────────────────────────────
# The rig is STEPPED, not scrolled: the display holds one scene position until
# the capture app reports a frame, then advances by the interval that frame took.
#
# Why: a 250 us exposure cannot span two 16.7 ms panel refreshes, so a smeared
# read was never the camera integrating two frames -- it was the panel caught
# MID-TRANSITION. LCD overdrive leaves a copy of the previous refresh's content
# behind the current one, bright plus a dark undershoot, one refresh (~21 ms of
# scene motion, measured) away. Static scenes never showed it because both
# copies land in the same place, which is why every frozen-page test came up
# clean. Holding the scene still through the exposure removes it at the source
# instead of characterising it.
#
# Payload: the measured inter-frame interval in seconds, ASCII float, one
# datagram per camera frame. Loopback UDP and fire-and-forget in that order of
# importance -- the capture app must never block, fault, or wait on the display
# app being up, and a lost datagram costs one step rather than a desync, since
# the display's own tick and distance ride the optical bar and land in the log
# by way of the camera.
STEP_HOST = "127.0.0.1"
STEP_PORT = 51711

# ── Lockstep channel (closed-loop handshake) ─────────────────────────────────
# The fire-and-forget step above is open loop: the app says "advance" and hopes.
# That leaves the panel's redraw at an uncontrolled PHASE relative to the shutter,
# and 2026-09-07 showed the failure it cannot prevent -- a nudge that lands after
# the repaint is not lost, ``step_dt`` sums the backlog, so the scene covers two
# intervals in one jump and a shutter open across it records two scene positions
# ~250 rows apart. Frame-global, 8 pairs in 117 intervals (HANDOFF 36.17).
#
# Lockstep closes the loop instead of guarding it: the display reports when the
# picture is painted AND settled, the app triggers into a provably static panel,
# and the display does not move again until the app says the frame is in. There
# is no phase left to land badly, so the alternating step that dodged it can go,
# which doubles the frames that become measurements.
#
# STEP  app -> display, "advance one step":            b"STEP <seq> <dt>"
# READY display -> app, "painted and settled":         b"READY <seq>"
#
# Sequence-numbered because lockstep inverts the property the open-loop channel
# was built on. There, a lost datagram cost one step; here it would deadlock the
# run, and a 90 s run is ~430 round trips. The app is master and owns a timeout:
# an unanswered STEP is re-sent (the display serves the same seq idempotently, so
# a duplicate cannot double-advance) and after a few tries the run FAILS loudly
# rather than hanging. Still UDP on loopback, still non-blocking sends -- the
# capture app must never block or fault on the display app being up.
READY_PORT = 51712                  # display -> app; STEP_PORT is app -> display
STEP_PREFIX = b"STEP "
READY_PREFIX = b"READY "

# How long the display waits after painting before it calls the panel settled.
# The pixels keep moving after the frame is presented, and that transition is the
# artefact we are removing, so the wait has to cover the panel's response, not
# just the swap. Generous by default because it is nearly free: the old rig had
# ~188 ms of slack in a 238 ms period, and the acceptance test is the smallest
# margin that still comes back with no doubled crops.
SETTLE_MS_DEFAULT = 50

# ── Alignment channel (screen-tilt nulling) ──────────────────────────────────
# Second message type on the SAME socket, distinguished by an ASCII prefix. The
# display's listener already does ``float(data)`` inside a try and drops what
# will not parse, so an old display ignores these and a new one keeps stepping.
#
# Why it exists: the camera rides a moving platform aimed at the screen by hand,
# and the six field maps of 2026-08-31 measured ~1.6 deg of pitch and ~1.1 deg of
# yaw between sensor and panel (HANDOFF 36.14.1). That is 641 encoder counts of
# focus ramp across the frame against a ~1460c depth of field, i.e. the top and
# bottom of one frame cannot be in focus together. The capture app measures the
# angle off a static pattern and the display shows it, so the operator can null
# it by hand while watching the very screen being aimed at.
ALIGN_PREFIX = b"ALIGN "

# ChArUco board spec. Lives here because the two apps MUST agree: the display
# renders from these numbers and the capture side builds its object points from
# the same ones, so a mismatch would be a silent geometry error rather than a
# crash. Sized for the bench 4K panel (3840x2160): 25x14 squares of 150 screen
# px is 3750x2100, and 312 interior corners.
ALIGN_SQUARES = (25, 14)            # (x, y) squares
ALIGN_SQUARE_PX = 150               # screen px per square
ALIGN_MARKER_FRAC = 0.7             # ArUco side as a fraction of the square

# NOT DICT_4X4_50 (what the four corner fiducials use). A ChArUco board needs
# ceil(sx*sy/2) distinct markers = 175 here, and DICT_4X4_50 has 50, so the board
# would fail to construct. 5x5 bits also separate better than DICT_4X4_250 at the
# same capacity. Being a DIFFERENT dictionary from the corner fiducials is a
# bonus: orthosim_decode.detect_fiducials() can keep hunting ids 0..3 for the GT
# readout without the board's markers being mistaken for them.
ALIGN_DICT = "DICT_5X5_250"

PANEL_PITCH_MM = 0.2646             # 4K bench panel, 1016 mm / 3840 px (HANDOFF 29.6)

# Below this the geometry is too cramped to trust. Placed at the measured accuracy
# cliff (tools/test_align.py renders the board through the real imaging chain and
# masks the frame down): 312 corners over the full frame reads to 0.02 deg, 50
# corners over the central 40% still reads to 0.04 deg, but 30 corners over the
# central 30% is already 0.33 deg and 12 corners is 0.50 deg, against a 0.36 deg
# budget. So accept the 40%-coverage case and refuse the 30% one. Refusing beats
# reporting an angle nobody should believe -- the operator would null a wrong
# bubble and the focus ramp would survive the whole exercise.
ALIGN_MIN_CORNERS = 40

_align_board = None


def charuco_board():
    """The one alignment board, built once and shared by both apps.

    Lives here rather than in either app because the display renders from it and the
    capture side builds its object points from it: two constructions from the same
    constants would still be two places for the marker fraction or the dictionary to
    drift, and the failure would be a silent geometry error rather than a crash.

    ``cv2`` is imported lazily so the wire codec itself stays dependency-free -- the
    bar encode/decode is used in places that have no business needing OpenCV.

    Square length is in MILLIMETRES via the panel pitch, which costs nothing and
    makes the solved translation a real standoff. Tilt is scale-free, so a wrong
    pitch would not corrupt the angles.
    """
    global _align_board
    if _align_board is None:
        import cv2
        dic = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, ALIGN_DICT))
        sq_mm = ALIGN_SQUARE_PX * PANEL_PITCH_MM
        _align_board = cv2.aruco.CharucoBoard(
            tuple(ALIGN_SQUARES), sq_mm, sq_mm * ALIGN_MARKER_FRAC, dic)
    return _align_board

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


@dataclass
class AlignReading:
    """One camera-to-screen orientation sample, in the SCREEN's own axes.

    Both angles are zero exactly when the panel is perpendicular to the optical
    axis. They are named for the screen axis each one tips, not for an image
    axis, because the camera on this rig is rolled ~90 deg relative to the panel:
    a reading labelled for the image would need mentally inverting, which is the
    same trap that made the field map trim the wrong edge of the frame for two
    weeks (HANDOFF 36.13). The measuring side derives roll from the pattern and
    rotates into these axes, so the label cannot go stale if the camera is turned.
    """

    tilt_v_deg: float = 0.0     # screen's VERTICAL axis tipping in depth
    tilt_h_deg: float = 0.0     # screen's HORIZONTAL axis tipping in depth
    n_corners: int = 0          # how much evidence stands behind the two angles
    distance_mm: float = 0.0    # camera-to-panel standoff, a free by-product

    @property
    def magnitude_deg(self) -> float:
        """Combined tilt about the resultant axis -- what the bubble's radius shows."""
        return (self.tilt_v_deg ** 2 + self.tilt_h_deg ** 2) ** 0.5


def encode_align(reading: AlignReading) -> bytes:
    """``b'ALIGN <v_deg> <h_deg> <n_corners> <distance_mm>'``, ASCII, one datagram."""
    return ALIGN_PREFIX + b"%.4f %.4f %d %.1f" % (
        float(reading.tilt_v_deg),
        float(reading.tilt_h_deg),
        int(reading.n_corners),
        float(reading.distance_mm),
    )


def decode_align(data: bytes) -> Optional[AlignReading]:
    """Parse an alignment datagram, or ``None`` if it is not one / is malformed.

    Returns ``None`` rather than raising so the display's receive loop can treat
    "not for me" and "corrupt" identically and just wait for the next datagram --
    the sender repeats twice a second, so there is nothing to recover.
    """
    if not data.startswith(ALIGN_PREFIX):
        return None
    try:
        v, h, n, dist = data[len(ALIGN_PREFIX):].split()
        return AlignReading(float(v), float(h), int(n), float(dist))
    except (ValueError, TypeError):
        return None


# ── Lockstep handshake ───────────────────────────────────────────────────────
# Both directions return None on anything unparseable, for the same reason
# decode_align does: a receive loop should not have to tell "not mine" from
# "corrupt", and the master's timeout is the recovery path for both.

def encode_step(seq: int, dt: float) -> bytes:
    """``b'STEP <seq> <dt>'`` -- advance the scene one step of ``dt`` simulated seconds."""
    return STEP_PREFIX + b"%d %.6f" % (int(seq), float(dt))


def decode_step(data: bytes) -> Optional[tuple]:
    """``(seq, dt)`` from a lockstep step request, else ``None``.

    Deliberately does NOT fall back to parsing a bare float. The open-loop channel
    sends one, and silently accepting it here would let a stale sender advance a
    lockstep display with no handshake -- the exact open-loop behaviour this
    replaces, reappearing as a hard-to-see mixed mode.
    """
    if not data.startswith(STEP_PREFIX):
        return None
    try:
        seq, dt = data[len(STEP_PREFIX):].split()
        return int(seq), float(dt)
    except (ValueError, TypeError):
        return None


def encode_ready(seq: int) -> bytes:
    """``b'READY <seq>'`` -- the picture for ``seq`` is painted and the panel has settled."""
    return READY_PREFIX + b"%d" % int(seq)


def decode_ready(data: bytes) -> Optional[int]:
    """The sequence number a READY answers, else ``None``."""
    if not data.startswith(READY_PREFIX):
        return None
    try:
        return int(data[len(READY_PREFIX):].split()[0])
    except (ValueError, TypeError, IndexError):
        return None
