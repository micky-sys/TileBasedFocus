r"""Core (non-GUI) logic for the combined GPS + Bay14 export studio.

This module is the shared engine behind ``gps_export_studio_gui.py``. It
unifies the two previously separate workflows:

* **Bay14Reader** (``bay14_export_core``) -- decode ``.bay14line`` frames
  from up to three camera folders and encode them to JPEG / raw-Bayer
  TIFF / demosaiced-RGB TIFF.
* **GPS Correlator** (``sbf_geotag_exports``) -- line up each captured
  frame with a GPS position, either read straight out of the line file's
  per-frame ``gps`` block (the coordinates the Iron 661 embeds at capture
  time) **or** correlated from an external Septentrio SBF ExtEvent CSV.

The unifying abstraction is :class:`FrameRecord`: one per captured frame,
carrying the frame's identity (camera / line / sweep / frame-in-sweep),
its GPS position, and -- once :func:`compute_attitude` has run -- its
per-frame camera Yaw/Pitch/Roll. The GUI plots these on a map, and the
export step injects each record's ``gps`` + ``attitude`` back into the
frame metadata so the encoders emit EXIF GPS tags + XMP Camera attitude
into every exported image (all three formats, including JPEG).

The source ``.bay14line`` files are never modified; coordinates and
attitude are injected into the in-memory metadata at export time only.
"""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

_TOOLS = Path(__file__).resolve().parent
_REPO = _TOOLS.parent
_HOST = _REPO / "host"
for _p in (str(_HOST), str(_REPO), str(_TOOLS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from bay14_export_core import (  # noqa: E402
    count_line_frames,
    discover_lines,
    iter_line_frames,
    peek_line_meta,
)
from sbf_geotag_exports import (  # noqa: E402
    DEFAULT_LEAP_SECONDS,
    DEFAULT_MAX_FLIGHT_OFFSET_S,
    DEFAULT_MAX_MISSING_EVENTS,
    DEFAULT_MAX_RMSE_S,
    correlate,
    event_to_fix,
)
from capture.attitude import (  # noqa: E402
    SERIAL_NOSE_OBLIQUE,
    SERIAL_TAIL_OBLIQUE,
    SERIAL_VERTICAL,
    SWEEP_FRAMES,
    yaw_pitch_roll,
)
from capture.gps_fix import GpsFix  # noqa: E402
from geotag_attitude_exports import (  # noqa: E402
    HEADING_BEARING_WINDOW,
    HEADING_MIN_DISPLACEMENT_M,
    _HeadingTracker,
)

log = logging.getLogger(__name__)


SOURCE_EMBEDDED = "embedded"
SOURCE_EXTERNAL = "external"


# Camera kinds (drive the attitude model + the default oblique flip). The
# kind -- not the serial read off the file -- decides how attitude is
# computed, so the operator can tell the studio exactly which optical path
# each folder came from.
KIND_OBLIQUE_NOSE = "oblique_nose"
KIND_OBLIQUE_TAIL = "oblique_tail"
KIND_VERTICAL = "vertical"

CAMERA_KIND_LABELS: dict[str, str] = {
    KIND_OBLIQUE_NOSE: "Oblique, nose facing",
    KIND_OBLIQUE_TAIL: "Oblique, tail facing",
    KIND_VERTICAL: "Vertical",
}

# Map each kind to the serial whose attitude model (mirror schedule +
# look direction) matches it, per ``capture.attitude``.
CAMERA_KIND_SERIAL: dict[str, str] = {
    KIND_OBLIQUE_NOSE: SERIAL_NOSE_OBLIQUE,
    KIND_OBLIQUE_TAIL: SERIAL_TAIL_OBLIQUE,
    KIND_VERTICAL: SERIAL_VERTICAL,
}


def kind_is_oblique(kind: str) -> bool:
    return kind in (KIND_OBLIQUE_NOSE, KIND_OBLIQUE_TAIL)


# ``.bay14line`` filename layout produced by the capture writer, e.g.
#   Cam2439007_20260525_ParallaxTestRishon_L13_144545_p0.bay14line
_LINE_NAME_RE = re.compile(
    r"^(?P<cam>Cam\d+)_(?P<date>\d{8})_(?P<project>.+?)"
    r"_L(?P<line>\d+)_(?P<hhmmss>\d{6})(?:_p\d+)?"
    r"\.bay14line$",
    re.IGNORECASE,
)


def normalize_serial(value: str) -> str:
    """Return the bare digit serial, stripping a leading ``Cam`` if present."""
    s = str(value or "").strip()
    if s.lower().startswith("cam"):
        s = s[3:]
    return s


@dataclass
class CameraInput:
    """One camera's export configuration, as collected from the GUI."""

    index: int                      # 0-based camera slot (Cam 1..3 -> 0..2)
    directory: Path
    kind: str = KIND_VERTICAL       # optical path -> attitude model
    flip_lr: bool = False           # mirror frames horizontally on export

    @property
    def attitude_serial(self) -> str:
        """Serial whose attitude model matches this camera's kind."""
        return CAMERA_KIND_SERIAL.get(self.kind, "")


@dataclass
class CorrelationParams:
    """Tunables for the external-SBF correlation path (mirrors the CLI)."""

    frames_per_sweep: int = SWEEP_FRAMES
    cluster_gap_s: float = 60.0
    cluster_min_size: int = 50
    leap_seconds: int = DEFAULT_LEAP_SECONDS
    max_flight_offset_s: float = DEFAULT_MAX_FLIGHT_OFFSET_S
    max_rmse_s: float = DEFAULT_MAX_RMSE_S
    max_missing_events: int = DEFAULT_MAX_MISSING_EVENTS
    recursive: bool = False
    allow_reuse: bool = True        # one camera reuses the shared trigger clusters


@dataclass
class FrameRecord:
    """One captured frame: identity + GPS + (optional) attitude.

    The single currency of the studio. Built during the load/correlate
    phase, augmented by :func:`compute_attitude`, plotted on the map, and
    finally consumed at export time to stamp EXIF GPS + XMP attitude.
    """

    cam_index: int
    cam_serial: str                 # real serial off the file, e.g. "2439007"
    line_path: Path
    line: int
    hhmmss: str
    sweep: int
    frame_in_sweep: int
    attitude_serial: str = ""       # serial of the attitude model (from kind)

    lat_deg: Optional[float] = None
    lon_deg: Optional[float] = None
    altitude_m: Optional[float] = None
    timestamp_utc: Optional[str] = None  # ISO-8601 string
    satellites: Optional[int] = None
    gps_source: str = ""            # "embedded" | "external" | ""

    heading_deg: Optional[float] = None
    yaw_deg: Optional[float] = None
    pitch_deg: Optional[float] = None
    roll_deg: Optional[float] = None
    attitude_status: str = ""

    @property
    def has_position(self) -> bool:
        return self.lat_deg is not None and self.lon_deg is not None

    @property
    def slot(self) -> int:
        return (self.sweep - 1) * SWEEP_FRAMES + (self.frame_in_sweep - 1)

    @property
    def track_key(self) -> tuple[int, int, str]:
        """Identity for heading derivation + map colouring.

        Keyed by physical camera slot (not serial) so two folders that
        happen to share a kind / serial still stay separate tracks, and
        by ``hhmmss`` (line-start time) so that two passes over the same
        line number stay distinct passes -- exactly as the standalone GPS
        Correlator's ``(cam_serial, line, hhmmss)`` LineGroup does. Merging
        the passes would interleave their slots and corrupt the causal
        heading bearing.
        """
        return (self.cam_index, self.line, self.hhmmss)

    @property
    def export_key(self) -> tuple[str, int, int]:
        """Key used to look this record up while iterating a line file."""
        return (str(self.line_path), self.sweep, self.frame_in_sweep)

    def to_gps_fix(self) -> GpsFix:
        ts: Optional[datetime] = None
        if self.timestamp_utc:
            try:
                ts = datetime.fromisoformat(self.timestamp_utc)
            except ValueError:
                ts = None
        return GpsFix(
            latitude_deg=self.lat_deg,
            longitude_deg=self.lon_deg,
            altitude_m=self.altitude_m,
            timestamp_utc=ts,
            satellites=self.satellites,
            hdop=None,
            fix_quality=1 if self.has_position else 0,
        )

    def attitude_dict(self) -> Optional[dict]:
        if self.yaw_deg is None and self.pitch_deg is None and self.roll_deg is None:
            return None
        return {
            "yaw_deg": self.yaw_deg,
            "pitch_deg": self.pitch_deg,
            "roll_deg": self.roll_deg,
            "heading_deg": self.heading_deg,
            "attitude_status": self.attitude_status,
        }


@dataclass
class FrameStore:
    """All discovered frames plus an export-time lookup index."""

    records: list[FrameRecord] = field(default_factory=list)
    _by_export_key: dict[tuple[str, int, int], FrameRecord] = field(
        default_factory=dict
    )

    def add(self, rec: FrameRecord) -> None:
        self.records.append(rec)
        self._by_export_key[rec.export_key] = rec

    def lookup(self, line_path: Path, sweep: int, frame_in_sweep: int) -> Optional[FrameRecord]:
        return self._by_export_key.get((str(line_path), int(sweep), int(frame_in_sweep)))

    def positioned(self) -> list[FrameRecord]:
        return [r for r in self.records if r.has_position]

    def tracks(self) -> dict[tuple[int, int, str], list[FrameRecord]]:
        """Group records by ``(camera_index, line, hhmmss)``, sorted by slot."""
        out: dict[tuple[int, int, str], list[FrameRecord]] = {}
        for r in self.records:
            out.setdefault(r.track_key, []).append(r)
        for recs in out.values():
            recs.sort(key=lambda r: r.slot)
        return out


# ─── Embedded-coordinate load ────────────────────────────────────────────────


def _parse_line_name(name: str) -> Optional[dict]:
    m = _LINE_NAME_RE.match(name)
    return m.groupdict() if m else None


def _serial_for_line(line_path: Path, fallback_cam: str) -> str:
    """Prefer the header's ``camera_serial``; else the filename's Cam token."""
    try:
        meta = peek_line_meta(line_path)
        s = meta.get("camera_serial") or meta.get("serial")
        if s:
            return normalize_serial(str(s))
    except Exception:
        log.debug("peek_line_meta failed for %s", line_path, exc_info=True)
    return normalize_serial(fallback_cam)


def load_embedded_camera(
    cam: CameraInput,
    *,
    pattern: str = "*.bay14line",
    recursive: bool = False,
    log_cb: Optional[Callable[[str], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> list[FrameRecord]:
    """Read per-frame GPS straight out of each line file's ``gps`` block.

    Only the per-frame chunk headers are read (``meta_only=True``), so a
    whole flight scans in seconds without decoding any pixels.
    """
    records: list[FrameRecord] = []
    lines = discover_lines(cam.directory, pattern, recursive=recursive)
    if log_cb is not None:
        log_cb(f"Cam {cam.index + 1}: {len(lines)} line file(s) (embedded GPS).")
    for line_path in lines:
        if cancel_cb is not None and cancel_cb():
            break
        parsed = _parse_line_name(line_path.name)
        if parsed is None:
            if log_cb is not None:
                log_cb(f"  skipped (unparseable name): {line_path.name}")
            continue
        serial = _serial_for_line(line_path, parsed["cam"])
        line_no = int(parsed["line"])
        hhmmss = parsed["hhmmss"]
        n_pos = 0
        try:
            for merged_meta, _arr in iter_line_frames(
                line_path, recovery_notice=None, meta_only=True,
            ):
                gps = merged_meta.get("gps")
                rec = FrameRecord(
                    cam_index=cam.index,
                    cam_serial=serial,
                    line_path=line_path,
                    line=line_no,
                    hhmmss=hhmmss,
                    sweep=int(merged_meta.get("sweep", 0)),
                    frame_in_sweep=int(merged_meta.get("frame_in_sweep", 0)),
                    attitude_serial=cam.attitude_serial,
                )
                if isinstance(gps, dict):
                    rec.lat_deg = _as_float(gps.get("latitude_deg"))
                    rec.lon_deg = _as_float(gps.get("longitude_deg"))
                    rec.altitude_m = _as_float(gps.get("altitude_m"))
                    ts = gps.get("timestamp_utc")
                    rec.timestamp_utc = str(ts) if ts else None
                    rec.satellites = _as_int(gps.get("satellites"))
                    if rec.has_position:
                        rec.gps_source = SOURCE_EMBEDDED
                        n_pos += 1
                records.append(rec)
        except Exception as exc:
            if log_cb is not None:
                log_cb(f"  ERROR reading {line_path.name}: {exc}")
            log.exception("embedded load failed for %s", line_path)
            continue
        if log_cb is not None:
            log_cb(f"  L{line_no}@{hhmmss} ({serial}): {n_pos} frame(s) with embedded GPS.")
    return records


# ─── External-SBF correlation load ───────────────────────────────────────────


def load_external_camera(
    cam: CameraInput,
    sbf_csv: Path,
    params: CorrelationParams,
    *,
    log_cb: Optional[Callable[[str], None]] = None,
) -> list[FrameRecord]:
    """Correlate one camera folder's frames against the external SBF CSV.

    Uses :func:`sbf_geotag_exports.correlate` in ``bay14line`` source mode,
    then flattens the per-line matches into :class:`FrameRecord` objects.
    """
    if log_cb is not None:
        log_cb(f"Cam {cam.index + 1}: correlating against external SBF ...")
    result = correlate(
        sbf_csv,
        cam.directory,
        source="bay14line",
        frames_per_sweep=params.frames_per_sweep,
        cluster_gap_s=params.cluster_gap_s,
        cluster_min_size=params.cluster_min_size,
        leap_seconds=params.leap_seconds,
        recursive=params.recursive,
        allow_reuse=params.allow_reuse,
        max_flight_offset_s=params.max_flight_offset_s,
        max_rmse_s=params.max_rmse_s,
        max_missing_events=params.max_missing_events,
    )
    matched = sum(1 for m in result.matches if m.cluster is not None)
    if log_cb is not None:
        log_cb(
            f"  {len(result.events)} events, {len(result.clusters)} clusters, "
            f"{matched}/{len(result.matches)} line(s) matched."
        )

    records: list[FrameRecord] = []
    for m in result.matches:
        serial = normalize_serial(
            m.line.cam_serial or (m.line.frames[0].cam if m.line.frames else "")
        )
        for frame, slot in zip(m.line.frames, m.slots):
            rec = FrameRecord(
                cam_index=cam.index,
                cam_serial=serial,
                line_path=frame.path,
                line=frame.line,
                hhmmss=frame.hhmmss,
                sweep=frame.sweep,
                frame_in_sweep=frame.frame_in_sweep,
                attitude_serial=cam.attitude_serial,
            )
            ev = m.event_for_slot(slot) if m.cluster is not None else None
            if ev is not None and ev.lat_deg is not None and ev.lon_deg is not None:
                fix = event_to_fix(ev, leap_seconds=params.leap_seconds)
                rec.lat_deg = fix.latitude_deg
                rec.lon_deg = fix.longitude_deg
                rec.altitude_m = fix.altitude_m
                rec.timestamp_utc = (
                    fix.timestamp_utc.isoformat(timespec="milliseconds")
                    if fix.timestamp_utc is not None else None
                )
                rec.satellites = fix.satellites
                rec.gps_source = SOURCE_EXTERNAL
            records.append(rec)
    return records


# ─── Attitude ────────────────────────────────────────────────────────────────


@dataclass
class AttitudeStats:
    points: int = 0
    with_heading: int = 0
    without_heading: int = 0
    no_gps: int = 0
    unknown_serial: int = 0


def compute_attitude(
    store: FrameStore,
    *,
    min_displacement_m: float = HEADING_MIN_DISPLACEMENT_M,
    window: int = HEADING_BEARING_WINDOW,
) -> AttitudeStats:
    """Derive per-frame heading + Yaw/Pitch/Roll for every track in *store*.

    For each ``(camera_serial, line)`` track the frames are ordered by
    slot and a causal running-average GPS bearing supplies the heading,
    exactly as the standalone GPS Correlator / live capture path does. The
    angles are written back onto each :class:`FrameRecord` in place.
    """
    stats = AttitudeStats()
    for _key, track in store.tracks().items():
        tracker = _HeadingTracker(
            min_displacement_m=min_displacement_m, window=window,
        )
        for rec in track:
            heading = tracker.update(rec.lat_deg, rec.lon_deg)
            rec.heading_deg = heading
            if not rec.has_position:
                stats.no_gps += 1
            try:
                att = yaw_pitch_roll(
                    rec.attitude_serial, rec.frame_in_sweep, heading_deg=heading,
                )
            except ValueError:
                stats.unknown_serial += 1
                rec.attitude_status = "unknown_serial"
                rec.yaw_deg = rec.pitch_deg = rec.roll_deg = None
                continue
            rec.yaw_deg = att.yaw_deg
            rec.pitch_deg = att.pitch_deg
            rec.roll_deg = att.roll_deg
            rec.heading_deg = att.heading_deg
            stats.points += 1
            if att.heading_deg is None:
                stats.without_heading += 1
                rec.attitude_status = "ok_no_heading"
            else:
                stats.with_heading += 1
                rec.attitude_status = "ok"
    return stats


# ─── small helpers ───────────────────────────────────────────────────────────


def _as_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_int(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def count_camera_frames(
    cam: CameraInput, *, pattern: str = "*.bay14line", recursive: bool = False,
) -> int:
    """Total frame count across a camera's line files (for progress totals)."""
    total = 0
    for line_path in discover_lines(cam.directory, pattern, recursive=recursive):
        try:
            total += count_line_frames(line_path)
        except Exception:
            log.debug("count_line_frames failed for %s", line_path, exc_info=True)
    return total
