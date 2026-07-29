"""Core (Qt-free) logic for the OrthoSim orthophoto screen bench rig.

Holds the calibration model that ties the displayed orthophoto's known ground
resolution to a *simulated* flight (height + speed), the run-time configuration,
and a CSV ground-truth logger used as a cross-check against the optical bar that
the Iron661 capture app decodes.

Calibration model
-----------------
Let

* ``ortho_gsd_cm``  = real ground size of one orthophoto pixel (cm / ortho-px),
* ``zoom`` (Z)      = screen pixels drawn per orthophoto pixel (screen-px / ortho-px),
* ``M``             = camera pixels per screen pixel (camera-px / screen-px),
                      a one-time geometry constant recovered from the corner
                      fiducials (or measured with a ruler).

Then one camera pixel sees ``Z * M`` ortho pixels-worth... no: camera-px per
ortho-px = ``Z * M``, so the *simulated* camera GSD is::

    simulated_gsd_cm = ortho_gsd_cm / (Z * M)

which is what "flight height" maps to (smaller GSD = lower altitude). "Flight
speed" maps to how fast we scroll::

    ground_speed_mps -> scroll_screen_px_per_s = ground_speed_mps * 100 / ortho_gsd_cm * Z

and cumulative simulated distance simply integrates ``ground_speed_mps`` over
wall-clock time.
"""

from __future__ import annotations

import csv
import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional, Tuple


# ── Configuration ────────────────────────────────────────────────────────────
@dataclass
class OrthoSimConfig:
    """User-tunable parameters for an OrthoSim run (persisted as JSON)."""

    # Scene / calibration
    ortho_gsd_cm: float = 5.0          # cm per orthophoto pixel (KNOWN input)
    panel_pitch_mm: float = 0.0        # screen pixel pitch (mm); 0 = unknown
    magnification: float = 1.0         # M: camera-px per screen-px (from fiducials)

    # Simulated flight
    ground_speed_mps: float = 40.0     # initial simulated ground speed
    speed_step_mps: float = 2.0        # Up/Down increment
    max_speed_mps: float = 400.0
    zoom: float = 0.0                  # 0 => auto "fit width" at startup
    zoom_step: float = 1.05            # multiplicative per key press
    scroll_dir: str = "up"            # content flows: "up","down","left","right"

    # Optical bar + fiducials
    bar_height_px: int = 96            # reserved band height on screen (px)
    bar_edge: str = "top"             # which edge hosts the bar: top/bottom
    bar_margin_px: int = 24            # side margin for the bar
    fiducial_px: int = 140             # ArUco marker side length (screen px)
    fiducial_margin_px: int = 24
    aruco_dict: str = "DICT_4X4_50"

    # Ground-distance scale (human-readable ruler along the scroll axis)
    scale_enabled: bool = False        # toggled live with 'G'
    scale_target_px: int = 200         # desired tick spacing (screen px)

    # HUD / logging
    hud_enabled: bool = True
    csv_log_hz: float = 20.0           # ground-truth CSV sample rate (0 = off)

    def to_json(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def from_json(cls, path: Path) -> "OrthoSimConfig":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        known = {f.name for f in fields_of(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def fields_of(cls):  # tiny helper so from_json ignores unknown/legacy keys
    import dataclasses
    return dataclasses.fields(cls)


# ── Calibration math ─────────────────────────────────────────────────────────
def simulated_gsd_cm(ortho_gsd_cm: float, zoom: float, magnification: float) -> float:
    """Simulated camera GSD (cm per camera pixel). Returns 0 if under-defined."""
    denom = zoom * magnification
    if denom <= 0:
        return 0.0
    return ortho_gsd_cm / denom


def screen_gsd_cm(ortho_gsd_cm: float, zoom: float) -> float:
    """Ground size of one *screen* pixel (cm/screen-px) = ortho_gsd / zoom.

    Independent of the (possibly unknown) camera magnification, so it is always
    displayable in the HUD.
    """
    if zoom <= 0:
        return 0.0
    return ortho_gsd_cm / zoom


def scroll_speed_screen_px_s(
    ground_speed_mps: float, ortho_gsd_cm: float, zoom: float
) -> float:
    """Convert a target ground speed (m/s) to screen scroll speed (screen-px/s)."""
    if ortho_gsd_cm <= 0:
        return 0.0
    ortho_px_s = ground_speed_mps * 100.0 / ortho_gsd_cm  # m/s -> ortho-px/s
    return ortho_px_s * zoom


def simulated_height_hint_m(
    simulated_gsd_cm_value: float,
    focal_length_mm: Optional[float] = None,
    sensor_pixel_um: Optional[float] = None,
) -> Optional[float]:
    """Approximate simulated flight height, if camera optics are provided.

    height = GSD * focal_length / pixel_pitch (all consistent units). Returns
    None when optics are unknown (the rig does not require this; it is a HUD
    nicety).
    """
    if not focal_length_mm or not sensor_pixel_um:
        return None
    gsd_m = simulated_gsd_cm_value / 100.0
    focal_m = focal_length_mm / 1000.0
    pixel_m = sensor_pixel_um / 1_000_000.0
    if pixel_m <= 0:
        return None
    return gsd_m * focal_m / pixel_m


# ── Ground-distance scale (human-readable ruler) ─────────────────────────────
def nice_interval_m(target_px: float, px_per_m: float) -> float:
    """Smallest 1/2/5 x 10^n metre interval whose on-screen spacing is >=
    *target_px*, so the ruler ticks land on round ground distances."""
    if px_per_m <= 0 or target_px <= 0:
        return 1.0
    raw = target_px / px_per_m  # metres needed to span target_px on screen
    exp = math.floor(math.log10(raw))
    base = 10.0 ** exp
    for m in (1.0, 2.0, 5.0):
        if base * m >= raw:
            return base * m
    return base * 10.0


def scale_ticks(
    distance_m: float,
    px_per_m: float,
    axis_len_px: float,
    target_px: float,
    content_from_far: bool,
) -> List[Tuple[float, float]]:
    """Ground-distance ruler ticks locked to the scrolling imagery.

    Returns ``(ground_distance_m, screen_pos_px)`` for every round-distance tick
    currently on screen along the scroll axis. ``distance_m`` is the cumulative
    simulated distance (newest ground, at the leading edge). ``content_from_far``
    is True when new content enters at the far edge (scroll "up"/"left") so the
    newest distance sits at ``axis_len_px``; False (scroll "down"/"right") puts
    it at 0. Both give tick spacing ``interval * px_per_m`` and shift by
    ``px_per_m * d(distance)`` per frame, i.e. exactly the imagery velocity.
    """
    if px_per_m <= 0 or axis_len_px <= 0:
        return []
    interval = nice_interval_m(target_px, px_per_m)
    span_m = axis_len_px / px_per_m
    d = max(0.0, math.ceil((distance_m - span_m) / interval) * interval)
    d_hi = math.floor(distance_m / interval) * interval
    out: List[Tuple[float, float]] = []
    while d <= d_hi + 1e-6:
        pos = (
            axis_len_px - (distance_m - d) * px_per_m
            if content_from_far
            else (distance_m - d) * px_per_m
        )
        out.append((d, pos))
        d += interval
    return out


# ── Ground-truth CSV logger ──────────────────────────────────────────────────
class GroundTruthLogger:
    """Append-only CSV of the simulated ground-truth (cross-check for decode)."""

    HEADER = [
        "wall_time_iso",
        "wall_time_perf_s",
        "tick",
        "distance_m",
        "velocity_mps",
        "screen_gsd_cm",
        "simulated_gsd_cm",
        "zoom",
        "flags",
    ]

    def __init__(self, path: Path, sample_hz: float = 20.0) -> None:
        self._path = Path(path)
        self._min_dt = (1.0 / sample_hz) if sample_hz and sample_hz > 0 else None
        self._last_perf = 0.0
        self._fh = None
        self._writer = None

    def open(self) -> None:
        self._fh = self._path.open("w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._fh)
        self._writer.writerow(self.HEADER)
        self._fh.flush()

    def maybe_log(
        self,
        *,
        tick: int,
        distance_m: float,
        velocity_mps: float,
        screen_gsd_cm_value: float,
        simulated_gsd_cm_value: float,
        zoom: float,
        flags: int,
    ) -> None:
        if self._writer is None or self._min_dt is None:
            return
        now = time.perf_counter()
        if self._last_perf and (now - self._last_perf) < self._min_dt:
            return
        self._last_perf = now
        self._writer.writerow(
            [
                time.strftime("%Y-%m-%dT%H:%M:%S"),
                f"{now:.6f}",
                tick,
                f"{distance_m:.4f}",
                f"{velocity_mps:.4f}",
                f"{screen_gsd_cm_value:.5f}",
                f"{simulated_gsd_cm_value:.5f}",
                f"{zoom:.5f}",
                flags,
            ]
        )
        self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.flush()
                self._fh.close()
            finally:
                self._fh = None
                self._writer = None
