"""Capture-side decoder for the OrthoSim orthophoto screen bench rig.

Given a camera frame that images the OrthoSim display, this module:

1. detects the four ArUco corner fiducials and computes a homography that
   rectifies the imaged screen back to display pixel coordinates,
2. samples the high-contrast optical ground-truth bar out of the rectified
   image and decodes it with the shared :mod:`orthosim_codec`, and
3. tracks measured speed / distance across frames so a bench operator can
   compare the *measured* flight parameters against the *simulated* ones
   (the display also writes a CSV cross-check).

It also exposes :func:`rectify_screen` so the autofocus feed can optionally be
cropped to just the screen interior (bezel/office removed) before analysis.

Everything here is a bench/validation path -- it does not touch the normal GPS
geotagging pipeline. ``cv2.aruco`` is imported lazily and guarded so the module
still imports on an OpenCV build without the contrib ArUco module (detection
simply returns ``None`` there).
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

try:
    import cv2
    _HAVE_ARUCO = hasattr(cv2, "aruco")
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore
    _HAVE_ARUCO = False

# Shared codec lives at the repo root (like tile_geometry / sharpness).
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
import orthosim_codec as codec  # noqa: E402


# ── Display geometry (must match tools/orthosim_gui.py bar/fiducial layout) ───
@dataclass
class ScreenLayout:
    """Where the optical bar + fiducials sit on the OrthoSim display.

    These mirror ``OrthoSimConfig`` defaults; pass the actual values you ran the
    display with if you changed them.
    """

    screen_w: int
    screen_h: int
    fiducial_px: int = 140
    fiducial_margin_px: int = 24
    bar_edge: str = "top"
    bar_height_px: int = 96
    bar_margin_px: int = 24
    aruco_dict: str = "DICT_4X4_50"

    def fiducial_centers(self) -> np.ndarray:
        """Canonical marker centres in display px, ordered id 0..3 = TL,TR,BR,BL."""
        s = self.fiducial_px
        m = self.fiducial_margin_px
        w, h = self.screen_w, self.screen_h
        return np.array(
            [
                [m + s / 2, m + s / 2],              # 0 TL
                [w - m - s / 2, m + s / 2],          # 1 TR
                [w - m - s / 2, h - m - s / 2],      # 2 BR
                [m + s / 2, h - m - s / 2],          # 3 BL
            ],
            dtype=np.float32,
        )

    def bar_cell_centers(self) -> Tuple[np.ndarray, float]:
        """Return (Nx2 cell-centre coords in display px, cell_w).

        Mirrors ``tools/orthosim_gui.py::_draw_bar`` EXACTLY: the bar shares the
        fiducial row (vertically centred on the marker band, not stacked below it)
        and is inset horizontally to clear each corner marker + its 6 px quiet zone +
        a side margin. These two formulas must track that method; a drift here samples
        the wrong band and every frame fails CRC even with the screen fully visible.
        ponytail: the pixel geometry is duplicated across the display and the decoder
        (they run on different PCs); upgrade path = hoist it into ``orthosim_codec``.
        """
        n = codec.TOTAL_CELLS
        s = self.fiducial_px
        fm = self.fiducial_margin_px
        bh = self.bar_height_px
        band_top = fm if self.bar_edge == "top" else self.screen_h - fm - s
        top = band_top + max(0, (s - bh) // 2)
        clear = fm + s + 6 + self.bar_margin_px   # matches _draw_bar's ``clear``
        x0 = clear
        x1 = self.screen_w - clear
        band_w = x1 - x0
        cell_w = band_w / n
        cy = top + bh / 2.0
        centers = np.array(
            [[x0 + (i + 0.5) * cell_w, cy] for i in range(n)], dtype=np.float32
        )
        return centers, cell_w


# ── low-level image helpers ──────────────────────────────────────────────────
def _to_gray_u8(frame: np.ndarray) -> np.ndarray:
    """Coerce any frame (color / uint16 Bayer-ish / gray) to uint8 grayscale."""
    img = frame
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) if cv2 is not None else img[..., 0]
    if img.dtype == np.uint16:
        # This is right-justified sensor data (e.g. BayerRG14, values 0..16383). A
        # fixed ``>>8`` treats it as full 16-bit and crushes a near-full-scale screen
        # into a 0..63 u8 -- too little contrast for ArUco to resolve the marker bits.
        # Stretch to the full 0..255 range so marker edges keep maximum contrast
        # regardless of bit depth / exposure.
        if cv2 is not None:
            img = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        else:
            m = int(img.max()) or 1
            img = ((img.astype(np.uint32) * 255) // m).astype(np.uint8)
    elif img.dtype != np.uint8:
        img = np.clip(img, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(img)


def detect_fiducials(
    gray_u8: np.ndarray,
    dict_name: str,
    ids_out: Optional[list] = None,
) -> Optional[np.ndarray]:
    """Return detected marker centres ordered id 0..3 (TL,TR,BR,BL), or None.

    Pass ``ids_out`` to receive the marker ids actually seen. All-or-nothing is the
    right answer for rectification, but a caller diagnosing a failure needs to tell
    "display blank / camera moved" (0 ids) from "someone is standing in front of one
    corner" (3 ids), and a bare None cannot.
    """
    if not _HAVE_ARUCO:
        return None
    try:
        const = getattr(cv2.aruco, dict_name)
        dic = cv2.aruco.getPredefinedDictionary(const)
        try:
            params = cv2.aruco.DetectorParameters()
            detector = cv2.aruco.ArucoDetector(dic, params)
            corners, ids, _ = detector.detectMarkers(gray_u8)
        except AttributeError:  # older API
            corners, ids, _ = cv2.aruco.detectMarkers(gray_u8, dic)
    except Exception:
        return None
    if ids_out is not None and ids is not None:
        ids_out.extend(sorted(int(i) for i in ids.flatten()))
    if ids is None or len(ids) < 4:
        return None
    by_id = {}
    for quad, mid in zip(corners, ids.flatten().tolist()):
        by_id[int(mid)] = quad.reshape(4, 2).mean(axis=0)
    if not all(k in by_id for k in (0, 1, 2, 3)):
        return None
    return np.array([by_id[0], by_id[1], by_id[2], by_id[3]], dtype=np.float32)


def measure_scale(
    src: np.ndarray, layout: ScreenLayout
) -> Tuple[float, float]:
    """Imaged camera-px per display-px along each screen axis, from marker centres.

    ``src`` is the id 0..3 (TL,TR,BR,BL) array from :func:`detect_fiducials`. The
    markers sit at *known* display-pixel positions, so their imaged separation
    measures the magnification ``M`` that the display otherwise takes on faith from
    its ``--M`` flag. That matters because the encoded GSD -- and the altitude the
    capture app derives from it -- is ``ortho_gsd_cm / (Z * M)``: an uncalibrated M
    scales the reported altitude by exactly the same factor, with nothing in the
    decode able to notice. Averages the two opposite edges per axis so a modest
    perspective tilt cancels instead of biasing one side.

    Returns (x, y) in camera-px per display-px, in whatever resolution ``src`` was
    detected at -- multiply by the decimation factor for full-res camera px.
    """
    dst = layout.fiducial_centers()

    def edge(a: int, b: int) -> float:
        known = float(np.hypot(*(dst[a] - dst[b])))
        return float(np.hypot(*(src[a] - src[b]))) / known if known else 0.0

    return (
        (edge(0, 1) + edge(3, 2)) / 2.0,   # top and bottom edges
        (edge(0, 3) + edge(1, 2)) / 2.0,   # left and right edges
    )


def rectify_screen(
    frame: np.ndarray, layout: ScreenLayout
) -> Optional[np.ndarray]:
    """Warp the imaged screen back to display pixel coordinates (gray uint8).

    Returns None if the fiducials cannot be found. The output is a
    ``screen_h x screen_w`` grayscale image aligned to the display layout, so
    the bar cells and the ortho content sit at their known positions.
    """
    if cv2 is None:
        return None
    gray = _to_gray_u8(frame)
    src = detect_fiducials(gray, layout.aruco_dict)
    if src is None:
        return None
    dst = layout.fiducial_centers()
    H = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(gray, H, (layout.screen_w, layout.screen_h))


def _sample_bits(rectified_gray: np.ndarray, layout: ScreenLayout) -> List[int]:
    centers, cell_w = layout.bar_cell_centers()
    win = max(1, int(cell_w * 0.3))
    vals = []
    h, w = rectified_gray.shape[:2]
    for cx, cy in centers:
        x, y = int(round(cx)), int(round(cy))
        x0 = max(0, x - win); x1 = min(w, x + win + 1)
        y0 = max(0, y - win); y1 = min(h, y + win + 1)
        patch = rectified_gray[y0:y1, x0:x1]
        vals.append(float(patch.mean()) if patch.size else 0.0)
    arr = np.array(vals, dtype=np.float32)
    return [1 if v >= t else 0 for v, t in zip(arr, _cell_thresholds(arr))]


_ENVELOPE_CELLS = 15
"""Width (in cells) of the local black/white envelope used to threshold the bar."""


def _cell_thresholds(arr: np.ndarray) -> np.ndarray:
    """Per-cell 0/1 decision level, tracking illumination across the bar.

    A single global ``(max+min)/2`` assumes the bar is evenly lit. On the real rig
    it is not: the imaged white level ramps by better than 5:1 end to end (measured
    ~205 at one end against ~43 at the other) while black stays near 15, so the far
    end's whites fall under a global midpoint and read as black -- every frame fails
    CRC even with the screen fully visible and in focus. Thresholding each cell
    against the midpoint of its *local* min/max envelope follows the ramp instead.

    ponytail: a run of >= ``_ENVELOPE_CELLS`` identical cells collapses the local
    envelope (min == max), which would split a flat run down the middle, so those
    windows fall back to the global midpoint. Ceiling: a long run sitting in a badly
    lit stretch can still misread; the CRC catches it and we simply use the next
    frame (decode runs ~1 Hz). Upgrade path = encode with a run-length limit, or fit
    a smooth illumination model over the whole bar rather than a sliding window.
    """
    k = min(_ENVELOPE_CELLS, arr.size)
    if k < 3:
        return np.full(arr.shape, (float(arr.max()) + float(arr.min())) / 2.0)
    pad = np.pad(arr, (k // 2, k - 1 - k // 2), mode="edge")
    win = np.lib.stride_tricks.sliding_window_view(pad, k)
    lo, hi = win.min(axis=1), win.max(axis=1)
    global_span = float(arr.max() - arr.min())
    return np.where(
        (hi - lo) >= 0.25 * global_span,
        (lo + hi) / 2.0,
        (float(arr.max()) + float(arr.min())) / 2.0,
    )


def decode_frame(
    frame: np.ndarray, layout: ScreenLayout
) -> Optional[codec.GroundTruth]:
    """Full pipeline: rectify -> sample bar -> decode. None on any failure."""
    rectified = rectify_screen(frame, layout)
    if rectified is None:
        return None
    bits = _sample_bits(rectified, layout)
    return codec.decode(bits)


def decode_rectified(rectified_gray: np.ndarray, layout: ScreenLayout):
    """Decode from an already-rectified display-space grayscale image."""
    return codec.decode(_sample_bits(rectified_gray, layout))


# ── measured speed / distance across frames ──────────────────────────────────
@dataclass
class MeasuredMotion:
    distance_m: float
    velocity_mps: float          # from Δdistance / Δt (measured)
    encoded_velocity_mps: float  # velocity field the display encoded
    gsd_cm: float                # simulated camera GSD (altitude proxy); 0 if unset
    tick: int
    seam: bool
    ok: bool


class SpeedDistanceTracker:
    """Turn a stream of decoded ground-truth samples into measured motion.

    ``update(gt, now=None)`` returns a :class:`MeasuredMotion`. Measured
    velocity is the derivative of the *absolute* decoded distance over
    wall-clock time (independent of the encoded velocity field, so the two can
    be cross-checked). Seam frames are reported but do not corrupt the estimate.
    """

    def __init__(self, ema: float = 0.3) -> None:
        self._prev_dist_m: Optional[float] = None
        self._prev_t: Optional[float] = None
        self._vel_ema: Optional[float] = None
        self._ema = float(ema)

    def update(
        self, gt: Optional[codec.GroundTruth], now: Optional[float] = None
    ) -> Optional[MeasuredMotion]:
        if gt is None:
            return None
        t = time.perf_counter() if now is None else now
        vel = 0.0
        if (
            self._prev_dist_m is not None
            and self._prev_t is not None
            and not gt.seam
            and t > self._prev_t
        ):
            raw = (gt.distance_m - self._prev_dist_m) / (t - self._prev_t)
            if raw >= 0:  # ignore the wrap discontinuity
                self._vel_ema = (
                    raw if self._vel_ema is None
                    else self._ema * raw + (1 - self._ema) * self._vel_ema
                )
                vel = self._vel_ema
            elif self._vel_ema is not None:
                vel = self._vel_ema
        self._prev_dist_m = gt.distance_m
        self._prev_t = t
        return MeasuredMotion(
            distance_m=gt.distance_m,
            velocity_mps=vel,
            encoded_velocity_mps=gt.velocity_mps,
            gsd_cm=gt.gsd_cm,
            tick=gt.tick,
            seam=gt.seam,
            ok=True,
        )


# ── tiny CLI for offline decode of a saved camera image ──────────────────────
def _main(argv: Optional[List[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Decode an OrthoSim optical bar from an image")
    ap.add_argument("image", type=str, help="camera image that shows the OrthoSim screen")
    ap.add_argument("--screen-w", type=int, required=True)
    ap.add_argument("--screen-h", type=int, required=True)
    ap.add_argument("--bar-edge", choices=["top", "bottom"], default="top")
    args = ap.parse_args(argv)

    if cv2 is None:
        print("OpenCV not available")
        return 2
    frame = cv2.imread(args.image, cv2.IMREAD_UNCHANGED)
    if frame is None:
        print(f"could not read {args.image}")
        return 2
    if frame.ndim == 3:
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    layout = ScreenLayout(screen_w=args.screen_w, screen_h=args.screen_h, bar_edge=args.bar_edge)
    gt = decode_frame(frame, layout)
    if gt is None:
        print("decode FAILED (no fiducials / bad CRC)")
        return 1
    gsd_txt = f"{gt.gsd_cm:.3f} cm/px" if gt.gsd_cm > 0 else "unset"
    print(
        f"distance={gt.distance_m:.3f} m  velocity={gt.velocity_mps:.3f} m/s  "
        f"gsd={gsd_txt}  "
        f"tick={gt.tick}  seam={gt.seam} paused={gt.paused} calib={gt.calibration}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
