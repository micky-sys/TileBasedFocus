"""Per-frame camera yaw / pitch / roll from rig geometry and aircraft heading.

Body frame (aircraft): X forward (flight), Y port (left), Z up (roof).
Heading: degrees from North, positive clockwise (toward East).

Mirror schedules (``frame_in_sweep`` 1..13, linear spacing):
  Oblique (2439006 tail, 2439007 nose): mirror mechanical -25..+25 deg;
  line-of-sight azimuth offset = 2 * mirror (beam sweep -50..+50 deg about Z).
  Vertical (2439008): beam tilt -30..+30 deg about X (no doubling).

Sign constants below are calibration hooks; flip if a test capture disagrees.

Calibration notes (``calibrate-signs``):
  * ``OBLIQUE_AZIMUTH_SIGN`` / ``VERTICAL_TILT_SIGN``: set from oblique
    plan view + one nadir/level flight once available.
  * Oblique cameras (2439006/2439007) produce a mirror-imaged raster (one
    reflection); roll encodes that handedness. Images are **not** flipped
    on export unless a future ``--flip-mirrored`` option is added.
  * Pix4D ``Camera:Yaw/Pitch/Roll`` zero/sign mapping should be validated
    against a known capture; adjust ``_ypr_from_R_enu`` if a systematic
    offset appears.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

# Device serials (Iron661) — see ``serial_comm.PCB_FOCUS_CAMERA_SLOT_BY_SERIAL``.
SERIAL_TAIL_OBLIQUE = "2439006"
SERIAL_NOSE_OBLIQUE = "2439007"
SERIAL_VERTICAL = "2439008"

SWEEP_FRAMES = 13

# Calibration signs (multiply sweep offsets; default matches CAD section views).
OBLIQUE_AZIMUTH_SIGN = 1.0
VERTICAL_TILT_SIGN = 1.0

_OBLIQUE_CONE_DEG = 45.0
_OBLIQUE_MIRROR_MIN_DEG = -25.0
_OBLIQUE_MIRROR_MAX_DEG = 25.0
_VERTICAL_TILT_MIN_DEG = -30.0
_VERTICAL_TILT_MAX_DEG = 30.0

_KNOWN_SERIALS = frozenset(
    {SERIAL_TAIL_OBLIQUE, SERIAL_NOSE_OBLIQUE, SERIAL_VERTICAL}
)


@dataclass(frozen=True)
class AttitudeAngles:
    """Camera orientation for one frame."""

    yaw_deg: float
    pitch_deg: float
    roll_deg: float
    heading_deg: Optional[float]
    heading_source: str
    mirror_angle_deg: float
    camera_serial: str
    frame_in_sweep: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "yaw_deg": self.yaw_deg,
            "pitch_deg": self.pitch_deg,
            "roll_deg": self.roll_deg,
            "heading_deg": self.heading_deg,
            "heading_source": self.heading_source,
            "mirror_angle_deg": self.mirror_angle_deg,
            "camera_serial": self.camera_serial,
            "frame_in_sweep": self.frame_in_sweep,
        }


def _clamp_frame(frame_in_sweep: int, n: int = SWEEP_FRAMES) -> int:
    f = int(frame_in_sweep)
    if f < 1:
        return 1
    if f > n:
        return n
    return f


def _frame_linear(frame_in_sweep: int, n: int = SWEEP_FRAMES) -> float:
    """Map frame 1..n to parameter t in [-1, +1] (endpoints inclusive)."""
    f = _clamp_frame(frame_in_sweep, n)
    if n < 2:
        return 0.0
    return (f - 1) / (n - 1) * 2.0 - 1.0


def mirror_angle_for_frame(camera_serial: str, frame_in_sweep: int) -> float:
    """Mirror mechanical angle (oblique) or beam tilt (vertical), degrees."""
    serial = str(camera_serial)
    t = _frame_linear(frame_in_sweep)
    if serial in (SERIAL_TAIL_OBLIQUE, SERIAL_NOSE_OBLIQUE):
        half = (_OBLIQUE_MIRROR_MAX_DEG - _OBLIQUE_MIRROR_MIN_DEG) * 0.5
        mid = (_OBLIQUE_MIRROR_MAX_DEG + _OBLIQUE_MIRROR_MIN_DEG) * 0.5
        return mid + half * t
    if serial == SERIAL_VERTICAL:
        half = (_VERTICAL_TILT_MAX_DEG - _VERTICAL_TILT_MIN_DEG) * 0.5
        mid = (_VERTICAL_TILT_MAX_DEG + _VERTICAL_TILT_MIN_DEG) * 0.5
        return (mid + half * t) * VERTICAL_TILT_SIGN
    raise ValueError(f"unknown camera serial for attitude: {serial!r}")


def _oblique_beam_azimuth_deg(camera_serial: str, frame_in_sweep: int) -> float:
    """Azimuth offset from mid-sweep look direction, degrees (about body Z)."""
    m = mirror_angle_for_frame(camera_serial, frame_in_sweep)
    return 2.0 * OBLIQUE_AZIMUTH_SIGN * m


def look_direction_body(camera_serial: str, frame_in_sweep: int) -> np.ndarray:
    """Unit look vector (camera toward scene) in body frame."""
    serial = str(camera_serial)
    if serial == SERIAL_VERTICAL:
        tau = math.radians(mirror_angle_for_frame(serial, frame_in_sweep))
        # Nadir at frame 7; rotate about +X (right-hand rule).
        return np.array([0.0, math.sin(tau), -math.cos(tau)], dtype=np.float64)
    if serial == SERIAL_NOSE_OBLIQUE:
        psi = math.radians(_oblique_beam_azimuth_deg(serial, frame_in_sweep))
        c = math.cos(_OBLIQUE_CONE_DEG * math.pi / 180.0)
        s = math.sin(_OBLIQUE_CONE_DEG * math.pi / 180.0)
        return np.array([s * math.cos(psi), s * math.sin(psi), -c], dtype=np.float64)
    if serial == SERIAL_TAIL_OBLIQUE:
        psi = math.radians(_oblique_beam_azimuth_deg(serial, frame_in_sweep))
        c = math.cos(_OBLIQUE_CONE_DEG * math.pi / 180.0)
        s = math.sin(_OBLIQUE_CONE_DEG * math.pi / 180.0)
        return np.array([-s * math.cos(psi), -s * math.sin(psi), -c], dtype=np.float64)
    raise ValueError(f"unknown camera serial for attitude: {serial!r}")


def _normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    if n < 1e-12:
        raise ValueError("zero-length vector")
    return v / n


def _rotation_from_look_and_up(
    look: np.ndarray,
    up_hint: np.ndarray,
) -> np.ndarray:
    """Return 3x3 rotation: columns are camera axes in body frame (+Z = look)."""
    z = _normalize(look)
    x = np.cross(up_hint, z)
    nx = float(np.linalg.norm(x))
    if nx < 1e-9:
        x = np.cross(np.array([1.0, 0.0, 0.0]), z)
        nx = float(np.linalg.norm(x))
    x = x / nx
    y = np.cross(z, x)
    return np.column_stack([x, y, z])


def camera_attitude_body(camera_serial: str, frame_in_sweep: int) -> np.ndarray:
    """3x3 rotation body->camera; oblique paths include one mirror flip (det -1)."""
    serial = str(camera_serial)
    look = look_direction_body(serial, frame_in_sweep)
    # Aircraft up helps define roll; port vector used if look is near vertical.
    up_hint = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    R = _rotation_from_look_and_up(look, up_hint)
    if serial in (SERIAL_TAIL_OBLIQUE, SERIAL_NOSE_OBLIQUE):
        # Single mirror reflection in the oblique path inverts one image axis.
        R = R.copy()
        R[:, 0] *= -1.0
    return R


def body_to_enu_rotation(heading_deg: float) -> np.ndarray:
    """Map body axes into ENU (X East, Y North, Z Up) for aircraft heading."""
    h = math.radians(float(heading_deg))
    sin_h, cos_h = math.sin(h), math.cos(h)
    # Columns: body X (forward), Y (port), Z (up) expressed in ENU.
    return np.array(
        [
            [sin_h, -cos_h, 0.0],
            [cos_h, sin_h, 0.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )


def _ypr_from_R_enu(R: np.ndarray) -> tuple[float, float, float]:
    """Yaw/pitch/roll (deg) from camera orientation in ENU (Pix4D-style)."""
    v = R[:, 2]
    vn = float(np.linalg.norm(v))
    if vn < 1e-12:
        return (0.0, 0.0, 0.0)
    v = v / vn
    yaw = math.degrees(math.atan2(v[0], v[1]))
    horiz = math.hypot(v[0], v[1])
    # Negative when the camera looks below the horizon (Pix4D-style).
    pitch = math.degrees(math.atan2(v[2], horiz))
    cz = math.cos(math.radians(yaw))
    sz = math.sin(math.radians(yaw))
    cp = math.cos(math.radians(pitch))
    sp = math.sin(math.radians(pitch))
    Ry = np.array(
        [[cp, 0.0, sp], [0.0, 1.0, 0.0], [-sp, 0.0, cp]],
        dtype=np.float64,
    )
    Rz = np.array(
        [[cz, sz, 0.0], [-sz, cz, 0.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    R_rem = Rz.T @ R @ Ry.T
    roll = math.degrees(math.atan2(R_rem[1, 0], R_rem[0, 0]))
    return (yaw, pitch, roll)


def yaw_pitch_roll(
    camera_serial: str,
    frame_in_sweep: int,
    heading_deg: Optional[float] = None,
) -> AttitudeAngles:
    """Compute yaw/pitch/roll for one frame."""
    serial = str(camera_serial)
    if serial not in _KNOWN_SERIALS:
        raise ValueError(f"unknown camera serial for attitude: {serial!r}")
    R_body_cam = camera_attitude_body(serial, frame_in_sweep)
    if heading_deg is not None:
        R_enu = body_to_enu_rotation(heading_deg) @ R_body_cam
        yaw, pitch, roll = _ypr_from_R_enu(R_enu)
        source = "gps_bearing_running_avg"
        h_out: Optional[float] = float(heading_deg)
    else:
        yaw, pitch, roll = _ypr_from_R_enu(R_body_cam)
        source = "body_frame"
        h_out = None
    return AttitudeAngles(
        yaw_deg=float(yaw),
        pitch_deg=float(pitch),
        roll_deg=float(roll),
        heading_deg=h_out,
        heading_source=source,
        mirror_angle_deg=float(mirror_angle_for_frame(serial, frame_in_sweep)),
        camera_serial=serial,
        frame_in_sweep=int(frame_in_sweep),
    )


def compute_attitude_dict(
    camera_serial: str,
    frame_in_sweep: int,
    heading_deg: Optional[float] = None,
) -> dict[str, Any]:
    """Plain dict for JSON chunk headers / ImageDescription."""
    return yaw_pitch_roll(
        camera_serial, frame_in_sweep, heading_deg=heading_deg
    ).to_dict()
