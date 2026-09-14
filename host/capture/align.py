"""Camera-to-screen orientation from the static ChArUco pattern on the display.

The camera rides a moving platform that is aimed at the bench screen by hand, and
the six field maps of 2026-08-31 measured ~1.6 deg of pitch and ~1.1 deg of yaw
between sensor and panel (HANDOFF 36.14.1). That is 641 encoder counts of focus
ramp across one frame against a ~1460c depth of field: the top and the bottom of a
single frame cannot be in focus together. This module measures that angle off a
pattern the display draws, so it can be nulled by hand while watching the screen.

What is measured, and why it is the keystone
--------------------------------------------
Tilt the panel and its image keystones. The tempting observable -- how far the
pattern shifts up or down -- is useless: to first order a tilt displaces the top
and the bottom of the frame by the SAME amount, which is indistinguishable from
panning the camera. What survives is the *convergence* of the lines that are
parallel on the panel, i.e. the pattern's width measured across the top of the
frame against the bottom. That is first order: 40 px at 1.57 deg, 2.6 px at
0.1 deg, on the 4x-decimated tap. ``solvePnP`` extracts it optimally from every
corner at once, so there is no line fitting here.

Precision, measured rather than hoped for
-----------------------------------------
Simulated end to end against the real 300 mm / 3.45 um geometry at a 7 m standoff:
0.013 deg of scatter at a pessimistic 0.5 px of corner noise. The budget is
0.36 deg, which is what keeps the residual focus ramp under ~0.1 depth of field.

That margin is why this runs on the **coarse 4x tap** rather than the full-res
frame: no full-res emission, no SHM slot, no new pipeline signal, just the frame
``_on_raw_focus_frame`` already receives. It is also why no camera calibration is
needed -- a nominal pinhole model suffices. The systematics were checked and are
all an order of magnitude under the budget: principal point wrong by 200 px is
0.03 deg, focal length wrong by 1% is 0.01 deg, uncorrected radial k1 = 0.10 is
0.04 deg. Radial distortion is symmetric and largely cancels in a top-versus-
bottom difference, which is the same reason the keystone is the right observable.

Self-check: ``py -3 tools/test_align.py``
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

try:
    import cv2
    _HAVE_ARUCO = hasattr(cv2, "aruco") and hasattr(cv2.aruco, "CharucoDetector")
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore
    _HAVE_ARUCO = False

# Shared codec lives at the repo root (like tile_geometry / sharpness).
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
import orthosim_codec as codec  # noqa: E402

# Per-lens focal length keyed by camera serial; the pitch is the shared IMX661
# sensor. Same numbers as the GSD->altitude conversion in ``app.py`` (HANDOFF 28).
CAM_FOCAL_MM = {"2439008": 300.0, "2439007": 400.0, "2439006": 400.0}
PITCH_UM = 3.45


@dataclass
class AlignResult:
    """One orientation measurement, plus what stands behind it."""

    reading: "codec.AlignReading"
    span_v_frac: float      # fraction of frame HEIGHT the detected corners cover
    span_h_frac: float      # fraction of frame WIDTH  the detected corners cover


def board():
    """The one ChArUco board -- the display app draws this same object.

    Its millimetre square length is what makes the solved ``tvec`` a real standoff,
    which is a free by-product that settles the caveat left open in HANDOFF 36.14.1:
    the tilt angle derived from the field map had to assume the curvature term was
    flat-plane geometry rather than lens field curvature.
    """
    if not _HAVE_ARUCO:
        raise RuntimeError("cv2.aruco.CharucoDetector unavailable")
    return codec.charuco_board()


def coarse_intrinsics(serial: Optional[str], shape: Tuple[int, int],
                      subsample: int = 4) -> Optional[np.ndarray]:
    """Nominal pinhole K for the decimated tap, or ``None`` if the lens is unknown.

    ``shape`` is the coarse frame's ``(h, w)``. Decimating by N divides the focal
    length in pixels by N and moves the principal point to the decimated centre;
    everything else about a pinhole is scale-free.
    """
    f_mm = CAM_FOCAL_MM.get(str(serial or "").strip())
    if not f_mm:
        return None
    f_px = f_mm / (PITCH_UM * 1e-3) / float(subsample)
    h, w = float(shape[0]), float(shape[1])
    return np.array([[f_px, 0.0, w / 2.0],
                     [0.0, f_px, h / 2.0],
                     [0.0, 0.0, 1.0]], dtype=np.float64)


def screen_axis_tilts(R: np.ndarray) -> Tuple[float, float]:
    """Tilt of the panel's own two axes out of the plane normal to the optical axis.

    ``R`` maps board coordinates into camera coordinates, so its first two columns
    ARE the panel's x and y axes seen by the camera, and each one's z-component is
    the sine of how far that axis tips toward or away from the lens. Both are zero
    exactly when the panel is perpendicular to the optical axis.

    Named for the SCREEN's axes on purpose. The camera on this rig is rolled ~90
    deg relative to the panel, so anything labelled for an image axis would need
    mentally inverting -- which is precisely the mistake that had the field map
    trimming the clean end of the frame and leaving the ground-truth bar inside its
    tiles for two weeks (HANDOFF 36.13). Taking the columns of ``R`` absorbs roll
    automatically: turn the camera and these two numbers do not move.
    """
    return (math.degrees(math.asin(max(-1.0, min(1.0, float(R[2, 1]))))),
            math.degrees(math.asin(max(-1.0, min(1.0, float(R[2, 0]))))))


def measure(gray_u8: np.ndarray, K: np.ndarray) -> Optional[AlignResult]:
    """Detect the board in ``gray_u8`` and solve for the panel's orientation.

    Returns ``None`` below ``ALIGN_MIN_CORNERS`` rather than a number nobody should
    believe -- an operator who nulls a wrong bubble has spent the session and still
    has the ramp. Precision tracks how much of the FRAME carries pattern, not how
    much of the screen is visible, so a screen that OVERFILLS the field of view is
    the best case here. That is the opposite of the four corner fiducials this
    replaces: those are pinned to the screen's corners, so they left the frame the
    moment the rig moved closer ("0/4 markers found"), and four points read only
    0.28 deg at 0.25 px of corner noise even when all four are in view.
    """
    if not _HAVE_ARUCO or gray_u8 is None or K is None:
        return None
    b = board()
    try:
        corners, ids, _, _ = cv2.aruco.CharucoDetector(b).detectBoard(gray_u8)
    except Exception:
        return None
    if ids is None or len(ids) < codec.ALIGN_MIN_CORNERS:
        return None

    img = np.asarray(corners, dtype=np.float64).reshape(-1, 2)
    obj = np.asarray(b.getChessboardCorners(), dtype=np.float64
                     )[np.asarray(ids).ravel()].reshape(-1, 3)
    ok, rvec, tvec = cv2.solvePnP(obj, img, K, np.zeros(5),
                                  flags=cv2.SOLVEPNP_ITERATIVE)
    if not ok:
        return None

    tilt_v, tilt_h = screen_axis_tilts(cv2.Rodrigues(rvec)[0])
    h, w = gray_u8.shape[:2]
    lo, hi = img.min(axis=0), img.max(axis=0)
    return AlignResult(
        reading=codec.AlignReading(
            tilt_v_deg=tilt_v,
            tilt_h_deg=tilt_h,
            n_corners=int(len(ids)),
            distance_mm=float(np.asarray(tvec).ravel()[2]),
        ),
        span_v_frac=float((hi[1] - lo[1]) / max(1.0, h)),
        span_h_frac=float((hi[0] - lo[0]) / max(1.0, w)),
    )
