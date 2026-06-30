r"""Compute per-frame camera Yaw/Pitch/Roll for already-exported TIFFs.

Input: a folder of TIFFs that

1. carry GPS coordinates in their EXIF (a real GPS sub-IFD, e.g. written by
   Bay14Reader / ``sbf_geotag_exports`` / ``embed_exif_gps_subifd``), and
2. are named with the Bay14Reader scheme so the camera serial + frame number
   are recoverable::

       Cam{serial}_{YYYYMMDD}_{project}_{HHMMSS}_L{line}_S{sweep}_F{frame}.tif

   (the older ``..._L{line}_{HHMMSS}[_p{N}]_S{sweep}_F{frame}`` layout is also
   accepted).

For each frame this tool:

* parses the camera serial + ``frame_in_sweep`` from the filename,
* reads the EXIF GPS latitude/longitude,
* derives the aircraft heading from a causal running-average bearing between
  consecutive frames of the same ``(camera, line)`` track (sequenced by
  ``slot = (sweep-1)*13 + (frame-1)``), and
* computes Yaw/Pitch/Roll via :mod:`capture.attitude` and writes them as
  XMP ``Camera:Yaw/Pitch/Roll`` (Pix4D namespace) with ExifTool.

The heading is causal, so the first frame of every track (and any further
frames until the platform has moved past the minimum-displacement gate) has
no heading. Those frames are written with body-frame-relative angles and
``heading_deg = null`` so the geometry is never lost; pass
``--skip-no-heading`` to leave them untouched instead.

Dry-run by default. Pass ``--write`` to actually patch the TIFFs.

Usage::

    python tools/geotag_attitude_exports.py D:\Exports
    python tools/geotag_attitude_exports.py D:\Exports --write
    python tools/geotag_attitude_exports.py D:\Exports --write --csv out.csv
"""

from __future__ import annotations

import argparse
import csv
import logging
import math
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import tifffile

_TOOLS = Path(__file__).resolve().parent
_ROOT = _TOOLS.parent
for _p in (str(_ROOT / "host"), str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from capture.attitude import (  # noqa: E402
    SWEEP_FRAMES,
    yaw_pitch_roll,
)
from capture.gps_correlator import (  # noqa: E402
    HEADING_BEARING_WINDOW,
    HEADING_MIN_DISPLACEMENT_M,
    _haversine_m,
    _initial_bearing_deg,
)
from capture.tiff_writer import (  # noqa: E402
    detect_exiftool,
    exiftool_write_camera_attitude,
)

log = logging.getLogger("geotag_attitude")

# Bay14Reader export-name layouts (mirrors tools/sbf_geotag_exports.py).
_NEW_NAME_RE = re.compile(
    r"^(?P<cam>Cam\d+)_(?P<date>\d{8})_(?P<project>.+?)"
    r"_(?P<hhmmss>\d{6})_L(?P<line>\d+)_S(?P<sweep>\d+)_F(?P<frame>\d+)"
    r"\.(?P<ext>[A-Za-z0-9]+)$"
)
_OLD_NAME_RE = re.compile(
    r"^(?P<cam>Cam\d+)_(?P<date>\d{8})_(?P<project>.+?)"
    r"_L(?P<line>\d+)_(?P<hhmmss>\d{6})(?:_p\d+)?"
    r"_S(?P<sweep>\d+)_F(?P<frame>\d+)"
    r"\.(?P<ext>[A-Za-z0-9]+)$"
)


@dataclass
class FrameRec:
    path: Path
    camera_serial: str  # digits only, e.g. "2439007"
    line: int
    sweep: int
    frame_in_sweep: int
    lat_deg: Optional[float] = None
    lon_deg: Optional[float] = None
    heading_deg: Optional[float] = None
    yaw_deg: Optional[float] = None
    pitch_deg: Optional[float] = None
    roll_deg: Optional[float] = None
    status: str = ""

    @property
    def slot(self) -> int:
        return (self.sweep - 1) * SWEEP_FRAMES + (self.frame_in_sweep - 1)

    @property
    def track_key(self) -> tuple[str, int]:
        return (self.camera_serial, self.line)


def parse_export_name(name: str) -> Optional[dict]:
    """Parse a Bay14Reader export filename, or None if it doesn't match."""
    m = _NEW_NAME_RE.match(name) or _OLD_NAME_RE.match(name)
    if not m:
        return None
    d = m.groupdict()
    cam = d["cam"]
    d["camera_serial"] = cam[3:] if cam.lower().startswith("cam") else cam
    return d


def _rational_to_float(value) -> Optional[float]:
    """Convert a tifffile RATIONAL value to float (handles tuple/Fraction/num)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if hasattr(value, "numerator") and hasattr(value, "denominator"):
        den = float(value.denominator)
        return float(value.numerator) / den if den else None
    if isinstance(value, (tuple, list)) and len(value) == 2:
        den = float(value[1])
        return float(value[0]) / den if den else None
    return None


def _dms_to_deg(dms, ref: Optional[str]) -> Optional[float]:
    """Convert an EXIF GPS DMS triplet to signed decimal degrees.

    ``dms`` may be a flat 6-int RATIONAL tuple (d_num, d_den, m_num, m_den,
    s_num, s_den) or a 3-tuple of rationals; ``ref`` is N/S/E/W.
    """
    if dms is None:
        return None
    parts: list[float] = []
    if isinstance(dms, (tuple, list)) and len(dms) == 6 and all(
        isinstance(x, (int, float)) for x in dms
    ):
        for i in range(0, 6, 2):
            den = float(dms[i + 1])
            parts.append(float(dms[i]) / den if den else 0.0)
    elif isinstance(dms, (tuple, list)) and len(dms) == 3:
        for comp in dms:
            f = _rational_to_float(comp)
            if f is None:
                return None
            parts.append(f)
    else:
        return None
    deg = parts[0] + parts[1] / 60.0 + parts[2] / 3600.0
    if ref and ref.upper() in ("S", "W"):
        deg = -deg
    return deg


def read_gps_from_exif(path: Path) -> tuple[Optional[float], Optional[float]]:
    """Read (lat_deg, lon_deg) from a TIFF's EXIF GPS sub-IFD, else (None, None)."""
    try:
        with tifffile.TiffFile(str(path)) as tf:
            tag = tf.pages[0].tags.get(34853)  # GPSIFD
            if tag is None:
                return (None, None)
            g = tag.value
            if not isinstance(g, dict):
                return (None, None)
            lat = _dms_to_deg(g.get("GPSLatitude"), g.get("GPSLatitudeRef"))
            lon = _dms_to_deg(g.get("GPSLongitude"), g.get("GPSLongitudeRef"))
            return (lat, lon)
    except Exception:
        log.debug("EXIF GPS read failed for %s", path, exc_info=True)
        return (None, None)


def read_gps_via_exiftool(path: Path, exiftool_exe: Path) -> tuple[Optional[float], Optional[float]]:
    """Fallback GPS read using ExifTool numeric output."""
    import subprocess

    try:
        out = subprocess.run(
            [
                str(exiftool_exe),
                "-n", "-s", "-s", "-s",
                "-GPSLatitude", "-GPSLongitude",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return (None, None)
    lines = [ln.strip() for ln in (out.stdout or "").splitlines() if ln.strip()]
    if len(lines) < 2:
        return (None, None)
    try:
        return (float(lines[0]), float(lines[1]))
    except ValueError:
        return (None, None)


def iter_tiffs(roots: list[Path], *, recursive: bool) -> list[Path]:
    out: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            log.warning("root does not exist: %s", root)
            continue
        if root.is_file():
            cands = [root]
        else:
            it = root.rglob("*") if recursive else root.glob("*")
            cands = [p for p in it if p.is_file()]
        for p in cands:
            if p.suffix.lower() not in (".tif", ".tiff"):
                continue
            rp = p.resolve()
            if rp not in seen:
                seen.add(rp)
                out.append(p)
    return out


class _HeadingTracker:
    """Causal running-average bearing, mirroring GpsCorrelator's logic."""

    def __init__(
        self,
        *,
        min_displacement_m: float = HEADING_MIN_DISPLACEMENT_M,
        window: int = HEADING_BEARING_WINDOW,
    ) -> None:
        self._min_disp = float(min_displacement_m)
        self._window = int(window)
        self._samples: list[tuple[float, float]] = []
        self._last_lat: Optional[float] = None
        self._last_lon: Optional[float] = None
        self._heading: Optional[float] = None

    def update(self, lat: Optional[float], lon: Optional[float]) -> Optional[float]:
        """Feed the next fix; return the heading valid for THIS frame.

        Causal: the returned heading reflects only fixes up to and including
        the previous frame's bearing computation, so the first frame (and any
        frame before the platform has moved past ``min_displacement_m``)
        returns ``None``.
        """
        current = self._heading
        if lat is None or lon is None:
            return current
        if self._last_lat is None or self._last_lon is None:
            self._last_lat, self._last_lon = lat, lon
            return current
        dist = _haversine_m(self._last_lat, self._last_lon, lat, lon)
        if dist < self._min_disp:
            return current
        brg = _initial_bearing_deg(self._last_lat, self._last_lon, lat, lon)
        self._last_lat, self._last_lon = lat, lon
        rad = math.radians(brg)
        self._samples.append((math.sin(rad), math.cos(rad)))
        if len(self._samples) > self._window:
            self._samples.pop(0)
        s = sum(x for x, _ in self._samples)
        c = sum(y for _, y in self._samples)
        if abs(s) >= 1e-15 or abs(c) >= 1e-15:
            self._heading = math.degrees(math.atan2(s, c)) % 360.0
        return self._heading


@dataclass
class RunStats:
    discovered: int = 0
    parsed: int = 0
    unparsed: int = 0
    no_gps: int = 0
    unknown_serial: int = 0
    with_heading: int = 0
    without_heading: int = 0
    written: int = 0
    failed: int = 0
    failures: list[tuple[Path, str]] = field(default_factory=list)


def process(
    roots: list[Path],
    *,
    recursive: bool,
    write: bool,
    skip_no_heading: bool,
    csv_path: Optional[Path],
    exiftool_exe: Optional[Path],
    min_displacement_m: float,
    window: int,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    log_cb: Optional[Callable[[str], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> tuple[list[FrameRec], RunStats]:
    stats = RunStats()
    tiffs = iter_tiffs(roots, recursive=recursive)
    stats.discovered = len(tiffs)
    if log_cb is not None:
        log_cb(f"Discovered {len(tiffs)} TIFF(s); parsing names + GPS ...")

    recs: list[FrameRec] = []
    for path in tiffs:
        parsed = parse_export_name(path.name)
        if parsed is None:
            stats.unparsed += 1
            continue
        stats.parsed += 1
        lat, lon = read_gps_from_exif(path)
        if (lat is None or lon is None) and exiftool_exe is not None:
            lat, lon = read_gps_via_exiftool(path, exiftool_exe)
        rec = FrameRec(
            path=path,
            camera_serial=parsed["camera_serial"],
            line=int(parsed["line"]),
            sweep=int(parsed["sweep"]),
            frame_in_sweep=int(parsed["frame"]),
            lat_deg=lat,
            lon_deg=lon,
        )
        recs.append(rec)

    # Group into (camera, line) tracks and sequence by slot for causal heading.
    tracks: dict[tuple[str, int], list[FrameRec]] = {}
    for rec in recs:
        tracks.setdefault(rec.track_key, []).append(rec)

    total = len(recs)
    done = 0
    for key, track in tracks.items():
        track.sort(key=lambda r: r.slot)
        tracker = _HeadingTracker(
            min_displacement_m=min_displacement_m, window=window
        )
        for rec in track:
            if cancel_cb is not None and cancel_cb():
                if log_cb is not None:
                    log_cb("Cancelled.")
                if csv_path is not None:
                    _write_csv(csv_path, recs)
                return recs, stats
            done += 1
            if progress_cb is not None:
                progress_cb(done, total, rec.path.name)
            rec.heading_deg = tracker.update(rec.lat_deg, rec.lon_deg)
            if rec.lat_deg is None or rec.lon_deg is None:
                stats.no_gps += 1
            try:
                att = yaw_pitch_roll(
                    rec.camera_serial,
                    rec.frame_in_sweep,
                    heading_deg=rec.heading_deg,
                )
            except ValueError:
                stats.unknown_serial += 1
                rec.status = "unknown_serial"
                continue
            rec.yaw_deg = att.yaw_deg
            rec.pitch_deg = att.pitch_deg
            rec.roll_deg = att.roll_deg
            if rec.heading_deg is None:
                stats.without_heading += 1
            else:
                stats.with_heading += 1

            if skip_no_heading and rec.heading_deg is None:
                rec.status = "skipped_no_heading"
                continue

            if not write:
                rec.status = "would_write"
                continue

            if exiftool_exe is None:
                rec.status = "no_exiftool"
                stats.failed += 1
                stats.failures.append((rec.path, "exiftool not found"))
                continue

            ok = exiftool_write_camera_attitude(
                rec.path,
                {
                    "yaw_deg": rec.yaw_deg,
                    "pitch_deg": rec.pitch_deg,
                    "roll_deg": rec.roll_deg,
                },
                exiftool_exe=exiftool_exe,
            )
            if ok:
                rec.status = "written"
                stats.written += 1
            else:
                rec.status = "write_failed"
                stats.failed += 1
                stats.failures.append((rec.path, "exiftool write failed"))

    if csv_path is not None:
        _write_csv(csv_path, recs)

    return recs, stats


def _write_csv(csv_path: Path, recs: list[FrameRec]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "file", "camera_serial", "line", "sweep", "frame_in_sweep",
                "lat_deg", "lon_deg", "heading_deg",
                "yaw_deg", "pitch_deg", "roll_deg", "status",
            ]
        )
        for r in sorted(recs, key=lambda x: (x.camera_serial, x.line, x.slot)):
            w.writerow(
                [
                    str(r.path), r.camera_serial, r.line, r.sweep,
                    r.frame_in_sweep,
                    "" if r.lat_deg is None else f"{r.lat_deg:.8f}",
                    "" if r.lon_deg is None else f"{r.lon_deg:.8f}",
                    "" if r.heading_deg is None else f"{r.heading_deg:.4f}",
                    "" if r.yaw_deg is None else f"{r.yaw_deg:.4f}",
                    "" if r.pitch_deg is None else f"{r.pitch_deg:.4f}",
                    "" if r.roll_deg is None else f"{r.roll_deg:.4f}",
                    r.status,
                ]
            )


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("roots", nargs="+", type=Path, help="Folder(s) or file(s) of exported TIFFs.")
    p.add_argument("--write", action="store_true", help="Patch the TIFFs (default: dry run).")
    p.add_argument("--no-recursive", action="store_true", help="Do not descend into subfolders.")
    p.add_argument(
        "--skip-no-heading",
        action="store_true",
        help="Leave frames with no heading untouched instead of writing body-frame angles.",
    )
    p.add_argument("--csv", type=Path, default=None, help="Write a per-frame sidecar CSV here.")
    p.add_argument("--exiftool-exe", type=Path, default=None, help="Path to exiftool.exe.")
    p.add_argument(
        "--min-displacement-m",
        type=float,
        default=HEADING_MIN_DISPLACEMENT_M,
        help="Minimum movement (m) between fixes before a bearing is taken.",
    )
    p.add_argument(
        "--window",
        type=int,
        default=HEADING_BEARING_WINDOW,
        help="Number of recent bearings averaged for the running heading.",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )
    exiftool_exe = detect_exiftool(args.exiftool_exe)
    if args.write and exiftool_exe is None:
        log.error("ExifTool not found; cannot --write. Install it or pass --exiftool-exe.")
        return 2
    if exiftool_exe is None:
        log.warning("ExifTool not found; GPS will be read from TIFF tags only.")

    recs, stats = process(
        args.roots,
        recursive=not args.no_recursive,
        write=args.write,
        skip_no_heading=args.skip_no_heading,
        csv_path=args.csv,
        exiftool_exe=exiftool_exe,
        min_displacement_m=args.min_displacement_m,
        window=args.window,
    )

    mode = "WRITE" if args.write else "DRY-RUN"
    log.info("[%s] discovered=%d parsed=%d unparsed=%d", mode, stats.discovered, stats.parsed, stats.unparsed)
    log.info(
        "  gps_missing=%d unknown_serial=%d with_heading=%d without_heading=%d",
        stats.no_gps, stats.unknown_serial, stats.with_heading, stats.without_heading,
    )
    if args.write:
        log.info("  written=%d failed=%d", stats.written, stats.failed)
    for path, why in stats.failures[:20]:
        log.warning("  FAILED %s: %s", path, why)
    return 1 if stats.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
