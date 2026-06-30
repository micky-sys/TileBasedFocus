r"""Geotag exported Bay14 TIFFs from a Septentrio SBF ExtEvent CSV log.

The SBF receiver was wired to the camera trigger and recorded one
``ExtEventPVTGeodetic`` block per trigger pulse. Septentrio's offline
exporter (``sbf2asc``) writes those blocks as one CSV line each, with
columns starting ``TOW(s), WNc, Mode, ..., Lat(rad), Lon(rad),
Height(m), Undulation(m), ...``. This tool reads that CSV, lines up
each captured frame on disk with the matching GPS event, and writes
EXIF GPS tags into every TIFF.

Correlation strategy (per the design discussion):

1. Cluster GPS events by inter-event gap (``--cluster-gap-s``) so each
   surveying line shows up as one contiguous run of events.
2. Group exported TIFFs by their parsed line number, sorted by
   ``(sweep, frame_in_sweep)``. The acquisition time embedded in each
   TIFF (``ImageDescription.acquire_timestamp``) is *flight-computer*
   time, the same source as the filename HHMMSS, so we never trust it
   in absolute terms -- but inter-frame deltas inside a single line are
   trustworthy (the host clock barely drifts over a few minutes).
3. Map every frame to a slot index in its line's full capture::

       slot(frame) = (sweep - 1) * frames_per_sweep + (frame_in_sweep - 1)

   This survives sparse / cancelled-mid-export folders (missing F's)
   because we use ``(S, F)`` rather than the export's positional order.
4. Match each line to a candidate event cluster by minimising the RMSE
   between the frames' relative acquire timestamps and the cluster
   events' relative TOWs at the same slot indices (1st frame -> event
   ``slot[0]`` of the cluster, etc.). The cluster's size must be at
   least ``max(slot) + 1``.
5. Cross-check: the implied "flight clock offset" (matched-event UTC
   minus the frame's acquire UTC) should be roughly constant across
   all frames and all lines. Lines whose derived offset deviates from
   the median by more than ``--offset-warn-s`` are flagged.
6. Dry-run by default; ``--write`` actually rewrites the TIFFs.

EXIF write paths (--exif-writer):

* ``exiftool`` (default when an ``exiftool`` binary is found): writes
  GPS tags into a proper EXIF GPS sub-IFD pointed to by IFD0 tag
  ``34853``. The file is patched in place -- only the metadata
  changes, so a 720 MB TIFF is tagged in well under a second even on
  Google Drive. Lightroom, Bridge, QGIS, exiftool, etc. all read the
  GPS afterwards.
* ``tifffile``: legacy fallback. Reads the array, augments the
  ``ImageDescription`` JSON with a ``gps`` block, and rewrites the
  whole TIFF. The EXIF GPS tags it adds via ``extratags`` live at
  IFD0 with their bare numeric IDs (1, 2, 4, ...), which standard
  EXIF readers do NOT recognise; only tools that walk the JSON
  description (Bay14Reader, this tool) see the GPS. Use only when
  you cannot install ExifTool.

Auto-detection looks for ``exiftool`` on PATH and at the bundled path
``C:\ExifTool\exiftool-13.58_64\exiftool.exe``; override with
``--exiftool-exe``.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

import tifffile

_TOOLS = Path(__file__).resolve().parent
_REPO = _TOOLS.parent
_HOST = _REPO / "host"
for p in (str(_HOST), str(_REPO), str(_TOOLS)):
    if p not in sys.path:
        sys.path.insert(0, p)

from capture.gps_fix import GpsFix  # noqa: E402
from capture.tiff_writer import (  # noqa: E402
    DEFAULT_EXIFTOOL_PATHS,
    _build_gps_extratags,
    apply_exif_gps_from_image_description as _tw_apply_exif_gps_from_image_description,
    detect_exiftool as _tw_detect_exiftool,
    exiftool_geotag as _tw_exiftool_geotag,
)

# Re-export so existing callers (CLI, GUI, tests) keep working unchanged
# while there is a single source of truth in capture.tiff_writer.
detect_exiftool = _tw_detect_exiftool
exiftool_geotag = _tw_exiftool_geotag
apply_exif_gps_from_image_description = _tw_apply_exif_gps_from_image_description

log = logging.getLogger("sbf_geotag_exports")


# ─── SBF CSV ─────────────────────────────────────────────────────────────────

# Septentrio's CSV exporter emits ``-2.0e10`` for f4/f8 Do-Not-Use fields.
_SBF_DNU = -2.0e10
_GPS_EPOCH = datetime(1980, 1, 6, tzinfo=timezone.utc)
DEFAULT_LEAP_SECONDS = 18


@dataclass(frozen=True)
class Event:
    """One ExtEventPVTGeodetic row, normalised to degrees and metres."""

    tow_s: float
    wnc: int
    lat_deg: Optional[float]
    lon_deg: Optional[float]
    height_m: Optional[float]
    undulation_m: Optional[float]
    n_sv: Optional[int] = None

    def utc(self, leap_seconds: int = DEFAULT_LEAP_SECONDS) -> datetime:
        """Combine ``(WNc, TOW)`` into UTC, subtracting GPS leap seconds."""
        delta = timedelta(
            weeks=int(self.wnc),
            seconds=float(self.tow_s) - float(leap_seconds),
        )
        return _GPS_EPOCH + delta


def _safe_float(s: str) -> Optional[float]:
    s = s.strip()
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    if math.isclose(v, _SBF_DNU, rel_tol=0, abs_tol=1.0):
        return None
    return v


def parse_sbf_csv(path: Path) -> list[Event]:
    """Parse Septentrio CSV (``sbf2asc -b 4038``) into Events.

    The first six columns are fixed (``TOW, WNc, Mode, SubMode, FixType,
    Error``); the geodetic block lives in columns 6..9 (lat[rad],
    lon[rad], height[m], undulation[m]). Newer firmware appends more
    fields after that — we simply ignore the tail.
    """
    out: list[Event] = []
    with open(path, "r", newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.reader(f):
            if not row or not row[0].strip():
                continue
            try:
                tow = float(row[0])
                wnc = int(row[1])
            except (ValueError, IndexError):
                continue
            lat = _safe_float(row[6]) if len(row) > 6 else None
            lon = _safe_float(row[7]) if len(row) > 7 else None
            h = _safe_float(row[8]) if len(row) > 8 else None
            und = _safe_float(row[9]) if len(row) > 9 else None
            lat_d = math.degrees(lat) if lat is not None else None
            lon_d = math.degrees(lon) if lon is not None else None
            out.append(Event(
                tow_s=tow, wnc=wnc, lat_deg=lat_d, lon_deg=lon_d,
                height_m=h, undulation_m=und,
            ))
    return out


# ─── Clustering ──────────────────────────────────────────────────────────────


@dataclass
class Cluster:
    """A contiguous run of events with all inter-event gaps below the threshold."""

    events: list[Event]
    index: int  # 0-based position among clusters

    @property
    def size(self) -> int:
        return len(self.events)

    @property
    def span_s(self) -> float:
        if not self.events:
            return 0.0
        return float(self.events[-1].tow_s - self.events[0].tow_s)

    def deltas(self) -> list[float]:
        e = self.events
        return [e[i + 1].tow_s - e[i].tow_s for i in range(len(e) - 1)]


def cluster_events(events: list[Event], gap_s: float) -> list[Cluster]:
    """Split *events* into runs where every inter-event gap is < *gap_s*."""
    if not events:
        return []
    clusters: list[Cluster] = []
    start = 0
    for i in range(1, len(events)):
        if events[i].tow_s - events[i - 1].tow_s > gap_s:
            clusters.append(Cluster(events=events[start:i], index=len(clusters)))
            start = i
    clusters.append(Cluster(events=events[start:], index=len(clusters)))
    return clusters


# ─── Exported frames ─────────────────────────────────────────────────────────

# After the renamer:
#   Cam{ID}_{YYYYMMDD}_{project}_{HHMMSS}_L{line}_S{sweep}_F{frame}.{ext}
# We still accept the old layout (``_L{line}_{HHMMSS}_p{N}_S..._F...``) for
# folders that haven't been renamed yet — both forms parse cleanly.
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
class Frame:
    path: Path
    cam: str
    date_yyyymmdd: str
    project: str
    hhmmss: str  # line-start time per the filename
    line: int
    sweep: int
    frame_in_sweep: int
    acquire_ts: Optional[datetime] = None  # flight-computer time, with tz

    @property
    def sort_key(self) -> tuple[int, int]:
        return (self.sweep, self.frame_in_sweep)

    def tiff_export_name(self, ext: str = "tif") -> str:
        """The filename Bay14Reader will give this frame on export.

        Mirrors ``tools.bay14_reader_gui.build_export_path`` exactly:
        ``{cam}_{date}_{project}_{hhmmss}_L{line}_S{sweep}_F{fin}.{ext}``.
        Used by the CSV sidecar in bay14line mode so the operator can
        join the per-frame coordinates against the eventual TIFFs by
        filename without an intermediate lookup table.
        """
        return (
            f"{self.cam}_{self.date_yyyymmdd}_{self.project}"
            f"_{self.hhmmss}_L{self.line}"
            f"_S{self.sweep}_F{self.frame_in_sweep}.{ext}"
        )

    @property
    def is_bay14line_source(self) -> bool:
        return self.path.suffix.lower() == ".bay14line"


def _parse_frame_name(name: str) -> Optional[dict]:
    m = _NEW_NAME_RE.match(name) or _OLD_NAME_RE.match(name)
    if not m:
        return None
    return m.groupdict()


def _read_acquire_ts(path: Path) -> Optional[datetime]:
    """Read ``ImageDescription.acquire_timestamp`` from a TIFF without
    decoding the pixel data. Returns None on any problem."""
    try:
        with tifffile.TiffFile(str(path)) as tf:
            tag = tf.pages[0].tags.get("ImageDescription")
            if tag is None:
                return None
            d = json.loads(tag.value)
        ts = d.get("acquire_timestamp")
        if not ts:
            return None
        return _parse_iso_with_tz(str(ts))
    except Exception:
        log.debug("acquire_timestamp read failed for %s", path, exc_info=True)
        return None


def is_incomplete_tiff(path: Path) -> bool:
    """Return True if *path* fails to open as a TIFF, has no IFD0, or its
    ``ImageDescription`` lacks an ``acquire_timestamp``. A truncated
    (cancelled-mid-write) TIFF will trip on the first two checks; a TIFF
    that wrote header+pixels but never updated metadata trips on the
    third.
    """
    try:
        with tifffile.TiffFile(str(path)) as tf:
            page = tf.pages[0]
            _ = page.shape  # forces IFD parse
            tag = page.tags.get("ImageDescription")
            if tag is None:
                return True
            try:
                d = json.loads(tag.value)
            except Exception:
                return True
        return "acquire_timestamp" not in d
    except Exception:
        return True


def find_incomplete_tiffs(root: Path, *, recursive: bool) -> list[Path]:
    iterator = root.rglob("*.tif") if recursive else root.glob("*.tif")
    return [p for p in iterator if p.is_file() and is_incomplete_tiff(p)]


def _parse_iso_with_tz(s: str) -> Optional[datetime]:
    """Like ``datetime.fromisoformat`` but tolerates ``Z`` suffix."""
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def discover_frames(root: Path, *, recursive: bool = True) -> list[Frame]:
    """Walk *root* and return all parseable TIFF frames (with timestamps)."""
    iterator = root.rglob("*.tif") if recursive else root.glob("*.tif")
    out: list[Frame] = []
    for path in iterator:
        if not path.is_file():
            continue
        parsed = _parse_frame_name(path.name)
        if not parsed:
            continue
        try:
            frame = Frame(
                path=path,
                cam=parsed["cam"],
                date_yyyymmdd=parsed["date"],
                project=parsed["project"],
                hhmmss=parsed["hhmmss"],
                line=int(parsed["line"]),
                sweep=int(parsed["sweep"]),
                frame_in_sweep=int(parsed["frame"]),
            )
        except (KeyError, ValueError):
            continue
        frame.acquire_ts = _read_acquire_ts(path)
        out.append(frame)
    return out


# ─── Raw .bay14line discovery ────────────────────────────────────────────────
#
# Alternate input mode: instead of exported TIFFs, the operator points the
# correlator at the folder of raw ``Cam{SN}_{YYYYMMDD}_{project}_L{line}_
# {HHMMSS}[_pN].bay14line`` containers that came straight off the SSDs.
# Each container holds many frames in one file; the per-frame chunk header
# carries the ``acquire_timestamp`` we need for matching. We don't decode
# pixel payloads here -- only the chunk header (a few hundred bytes each)
# is parsed, so a 100-line flight scans in seconds rather than minutes.

_BAY14LINE_NAME_RE = re.compile(
    r"^(?P<cam>Cam\d+)_(?P<date>\d{8})_(?P<project>.+?)"
    r"_L(?P<line>\d+)_(?P<hhmmss>\d{6})(?:_p\d+)?"
    r"\.bay14line$",
    re.IGNORECASE,
)


def _parse_bay14line_acquire_ts(raw: object) -> Optional[datetime]:
    """``frame_meta['acquire_timestamp']`` is an ISO-8601 string written by
    the capture pipeline (millisecond precision, naive-or-aware depending on
    the rig). Parse leniently and return None on anything unparseable.
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def iter_bay14line_frame_meta(
    line_path: Path,
) -> Iterable[tuple[int, int, Optional[datetime]]]:
    """Yield ``(sweep, frame_in_sweep, acquire_ts)`` for every chunk in a
    ``.bay14line`` container, reading only header bytes -- not pixels.

    Falls back to the BAY14FRM-magic walk in :func:`recover_line_chunks`
    when the file's index table was never written (capture cancelled
    mid-line). That recovery path is the same one the Bay14Reader uses,
    so coverage stays identical between this tool and the export.
    """
    import mmap
    from capture.raw_bay_writer import (  # noqa: E402, deferred so the host
        LINE_HEADER_FIXED_PREFIX,        # path setup at module import is enough
        LINE_INDEX_ENTRY_SIZE,
        parse_frame_chunk_header,
        parse_line_header_prefix,
        parse_line_index,
        recover_line_chunks,
    )
    with open(line_path, "rb") as fh:
        with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            prefix = mm[:LINE_HEADER_FIXED_PREFIX]
            _hdr_len, n_frames, idx_off, _ = parse_line_header_prefix(prefix)
            if n_frames > 0 and idx_off > 0:
                idx_blob = mm[idx_off: idx_off + n_frames * LINE_INDEX_ENTRY_SIZE]
                chunks = parse_line_index(idx_blob, n_frames)
            else:
                _meta, chunks = recover_line_chunks(str(line_path))
            for sweep, f_in, offset, _p_len, _unc in chunks:
                # Chunk header is variable but bounded; 4 KiB is more than
                # enough for the JSON payload our pipeline writes.
                blob = bytes(mm[offset: offset + 4096])
                cmeta, _c_hdr_len, _c_p_len = parse_frame_chunk_header(blob)
                ts = _parse_bay14line_acquire_ts(cmeta.get("acquire_timestamp"))
                yield int(sweep), int(f_in), ts


def discover_bay14line_frames(
    root: Path, *, recursive: bool = True,
) -> list[Frame]:
    """Walk *root* and return one :class:`Frame` per chunk found in every
    matching ``*.bay14line`` container. ``frame.path`` is the .bay14line
    file (shared by all chunks from that file); ``(sweep, frame_in_sweep)``
    plus the line number disambiguate downstream.

    Filenames that don't match :data:`_BAY14LINE_NAME_RE` are silently
    skipped (with a warning) so accidental siblings -- e.g. partial
    ``.bay14line.tmp`` files left by a crashed writer -- don't corrupt
    the line grouping.
    """
    iterator = (
        root.rglob("*.bay14line") if recursive else root.glob("*.bay14line")
    )
    out: list[Frame] = []
    for path in iterator:
        if not path.is_file():
            continue
        m = _BAY14LINE_NAME_RE.match(path.name)
        if not m:
            log.warning(
                "Skipping %s: filename doesn't match Cam*_L*_HHMMSS[_pN].bay14line",
                path.name,
            )
            continue
        g = m.groupdict()
        try:
            chunks = list(iter_bay14line_frame_meta(path))
        except Exception as exc:  # pragma: no cover (corrupt / locked files)
            log.warning("Failed to scan %s: %s", path, exc)
            continue
        for sweep, f_in, ts in chunks:
            out.append(Frame(
                path=path,
                cam=g["cam"],
                date_yyyymmdd=g["date"],
                project=g["project"],
                hhmmss=g["hhmmss"],
                line=int(g["line"]),
                sweep=sweep,
                frame_in_sweep=f_in,
                acquire_ts=ts,
            ))
    return out


@dataclass
class LineGroup:
    """One capture session's worth of frames.

    The identity is the triple ``(cam_serial, line, hhmmss)``, not just
    ``line``, so that retries of the same operator-set line number --
    i.e. an aborted attempt followed by a re-shoot under the same
    ``L{N}`` -- become separate :class:`LineGroup` instances and get
    matched to their own SBF clusters. Bundling them under ``line``
    alone would interleave their ``(sweep, frame_in_sweep)`` slots and
    poison the RMSE-based matcher.

    For backward compatibility, ``cam_serial`` and ``hhmmss`` default
    to empty strings; the existing tests that construct
    :class:`LineGroup` by hand keep working without modification.
    """

    line: int
    frames: list[Frame] = field(default_factory=list)
    cam_serial: str = ""
    hhmmss: str = ""

    def sort_in_place(self) -> None:
        self.frames.sort(key=lambda f: f.sort_key)

    def acquire_deltas_s(self) -> Optional[list[float]]:
        """Inter-frame deltas in seconds, derived from acquire_timestamp.

        Returns None if any frame is missing the timestamp; the caller
        should fall back to a count-only match for that line.
        """
        ts = [f.acquire_ts for f in self.frames]
        if any(t is None for t in ts):
            return None
        return [(ts[i + 1] - ts[i]).total_seconds() for i in range(len(ts) - 1)]

    def first_acquire_ts(self) -> Optional[datetime]:
        if self.frames and self.frames[0].acquire_ts is not None:
            return self.frames[0].acquire_ts
        return None

    def label(self) -> str:
        """Human-readable identity. ``"L<N>"`` when no retry context is
        available, otherwise ``"L<N>@<HHMMSS>"`` so a retried line is
        distinguishable in logs / plot legends / CSV diagnostics.
        """
        if self.hhmmss:
            return f"L{self.line}@{self.hhmmss}"
        return f"L{self.line}"


def group_by_line(frames: Iterable[Frame]) -> list[LineGroup]:
    """Group *frames* by ``(cam_serial, line, hhmmss)`` and sort each group
    by ``(sweep, frame_in_sweep)``.

    Multiple ``.bay14line`` containers (or exported TIFF sets) with the
    same line number but different ``HHMMSS`` -- i.e. retries of the
    same operator-set line -- yield distinct :class:`LineGroup`
    instances. Two cameras capturing the same trigger (same line +
    same HHMMSS) also live in distinct groups because their frames
    have different ``cam`` values; with ``--allow-cluster-reuse`` the
    matcher will assign them to the same SBF cluster.

    Sort order is ``(line, hhmmss, cam_serial)`` so retries of L1
    appear in chronological capture order before L2.
    """
    by_key: dict[tuple[str, int, str], LineGroup] = {}
    for f in frames:
        key = (f.cam, f.line, f.hhmmss)
        grp = by_key.get(key)
        if grp is None:
            grp = LineGroup(
                line=f.line,
                cam_serial=f.cam,
                hhmmss=f.hhmmss,
            )
            by_key[key] = grp
        grp.frames.append(f)
    out = sorted(
        by_key.values(),
        key=lambda g: (g.line, g.hhmmss, g.cam_serial),
    )
    for g in out:
        g.sort_in_place()
    return out


# ─── Matching ────────────────────────────────────────────────────────────────


def _rmse(a: list[float], b: list[float]) -> float:
    if not a or len(a) != len(b):
        return math.inf
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)) / len(a))


def frame_slot(sweep: int, frame_in_sweep: int, frames_per_sweep: int) -> int:
    """Return the 0-based slot of a frame inside its line's full capture."""
    return (int(sweep) - 1) * int(frames_per_sweep) + (int(frame_in_sweep) - 1)


@dataclass
class Match:
    line: LineGroup
    cluster: Optional[Cluster]
    slots: list[int]               # 0-based slot of each frame inside the line
    rmse_s: float                  # of (observed - expected) inter-frame timings
    flight_offset_s: Optional[float]  # matched event UTC - frame acquire UTC, first frame
    slot_offset: int = 0           # starting offset of slot 0 inside ``cluster.events``

    @property
    def n_frames(self) -> int:
        return len(self.line.frames)

    def event_for_slot(self, slot: int) -> Optional["Event"]:
        """Return the SBF event aligned with *slot*, or None if out of range.

        ``cluster.events[slot_offset + slot]``; returns None when the
        cluster is missing or the index is out of range. Callers should
        always go through this helper to avoid sprinkling
        ``slot_offset`` arithmetic across the codebase.
        """
        if self.cluster is None:
            return None
        idx = self.slot_offset + slot
        if 0 <= idx < self.cluster.size:
            return self.cluster.events[idx]
        return None


def _frame_slots(line: LineGroup, frames_per_sweep: int) -> list[int]:
    return [
        frame_slot(f.sweep, f.frame_in_sweep, frames_per_sweep)
        for f in line.frames
    ]


DEFAULT_MAX_FLIGHT_OFFSET_S = 900.0  # 15 minutes; the operator told us
# the flight-computer clock is "up to several minutes off" from GPS, and
# we never see real-world offsets above ~30 s on this rig. This is the
# default at the ``correlate()`` / CLI / GUI boundary; the low-level
# ``match_line`` / ``match_all`` default is +inf so unit tests using
# synthetic events whose absolute UTC doesn't line up with the frame
# timestamps still exercise the RMSE math.

DEFAULT_MAX_RMSE_S = 1.0  # 1 second. Real-world good matches sit at
# ~7-100 ms RMSE; clearly-wrong matches that the absolute-time prior
# couldn't reject (because the wrong cluster happens to fall inside
# the window) come back at 4-26 SECONDS. Two orders of magnitude
# between signal and noise, so a 1 s threshold is well in the gap.

DEFAULT_MAX_MISSING_EVENTS = 10  # How many GPS events at the end (or
# trimmed by a positive slot offset at the start) of a line we allow
# to be unmatched while still claiming the cluster. The real-world
# case is "camera recorded 897 frames but receiver only captured 894
# events" (3 dropped at the tail): we want the first 894 frames to
# come out geotagged, with the last 3 reported as ``no coords`` in
# the CSV / EXIF pass. ``Match.event_for_slot`` already returns None
# for out-of-range slots so writers are safe; this constant just
# controls when ``match_line`` is willing to claim a cluster that's
# slightly smaller than the line. Set to 0 to require exact fits.


def match_line(
    line: LineGroup,
    clusters: list[Cluster],
    *,
    frames_per_sweep: int,
    leap_seconds: int = DEFAULT_LEAP_SECONDS,
    max_flight_offset_s: float = math.inf,
    max_rmse_s: float = math.inf,
    max_missing_events: int = 0,
) -> Match:
    """Find the (cluster, starting offset) whose events best fit *line*.

    Each frame's slot index inside the line is ``(sweep - 1) *
    frames_per_sweep + (frame_in_sweep - 1)``. A line of N+1 slots can
    sit at any starting offset ``k`` inside a candidate cluster as long
    as ``k + max(slot) < cluster.size``. For every (cluster, k) pair we
    compute the TOW of each event at ``cluster.events[k + slot_i]``
    relative to the anchor frame, and compare against the relative
    ``acquire_timestamp`` of each frame. Lowest RMSE wins.

    Why offset scanning is required: flights typically fire a short
    "trigger test" burst (one sweep, ~13 events) **before** the actual
    line starts. With ``cluster_gap_s`` large enough to merge that
    burst into the same cluster as the line, the line's slot 0 is no
    longer at ``cluster.events[0]`` — it lives some offset in. Pinning
    offset=0 would pick a wildly wrong match; scanning finds the real
    position cleanly.

    When no frames have a timestamp we fall back to a size proximity
    score and offset=0 so the operator still gets a best-effort
    pointer (the RMSE comes back as inf in that case).
    """
    n = len(line.frames)
    if n == 0:
        return Match(line=line, cluster=None, slots=[], rmse_s=math.inf,
                     flight_offset_s=None)

    slots = _frame_slots(line, frames_per_sweep)
    max_slot = max(slots)
    # Allow clusters that are slightly smaller than the line: a cluster
    # of size ``max_slot + 1 - max_missing_events`` still covers all
    # but the last ``max_missing_events`` slots of the line at k=0,
    # which is the canonical "receiver dropped the last few triggers"
    # case. Tighter slack would refuse L1@155614 even though cluster
    # #18 covers 894 of its 897 frames cleanly.
    min_cluster_size = max(1, max_slot + 1 - max_missing_events)
    candidates = [c for c in clusters if c.size >= min_cluster_size]
    if not candidates:
        return Match(line=line, cluster=None, slots=slots, rmse_s=math.inf,
                     flight_offset_s=None)

    # Anchor on the earliest frame that actually has a timestamp. This
    # tolerates broken / unfinished TIFFs (cancelled-mid-write export)
    # without throwing the whole line into the size-proximity fallback.
    anchor_idx = next(
        (i for i, f in enumerate(line.frames) if f.acquire_ts is not None),
        None,
    )
    if anchor_idx is None:
        best = min(candidates, key=lambda c: abs(c.size - (max_slot + 1)))
        return Match(line=line, cluster=best, slots=slots, rmse_s=math.inf,
                     flight_offset_s=None, slot_offset=0)

    anchor_frame = line.frames[anchor_idx]
    anchor_acq = anchor_frame.acquire_ts
    anchor_slot = slots[anchor_idx]
    valid: list[tuple[int, float]] = []
    for i, f in enumerate(line.frames):
        if f.acquire_ts is None:
            continue
        valid.append((slots[i], (f.acquire_ts - anchor_acq).total_seconds()))

    valid_slots = [s for s, _ in valid]
    observed = [obs for _, obs in valid]

    # Pre-compute the anchor frame's wall-clock as UTC. This is what we
    # use to reject (cluster, offset) candidates whose implied
    # flight-clock offset is wildly outside the plausible window. Pure
    # RMSE matching can't distinguish two 897-event clusters that share
    # the same intra-line firing pattern (the canonical "all our lines
    # use the same 69-sweep schedule" case); the absolute-time prior
    # is what tells them apart.
    anchor_acq_utc = anchor_acq.astimezone(timezone.utc)

    best: Optional[Cluster] = None
    best_rmse = math.inf
    best_offset = 0
    for c in candidates:
        # ``k`` is the cluster-event offset assigned to the line's
        # slot 0. The cluster index touched by slot ``s`` is ``k + s``,
        # which must lie in ``[0, c.size - 1]``. So ``k`` ranges from
        # 0 up to ``c.size - 1`` (we don't try negative k -- a missing
        # event at the START of the line is much rarer than at the
        # tail, and trying them all would explode the search space).
        # The traditional full-fit case has ``max_k = c.size - max_slot
        # - 1``; with ``max_missing_events > 0`` we extend the upper
        # bound so the line's tail can spill past the cluster.
        max_k = c.size - 1
        for k in range(max_k + 1):
            # Anchor frame must map inside the cluster. If not, this
            # offset isn't usable for the offset-prior check below,
            # so skip it.
            if not (0 <= k + anchor_slot < c.size):
                continue
            # Frames whose mapped event would fall outside the cluster
            # are dropped from the fit; they will end up with
            # ``event_for_slot(...) == None`` at write time. Require a
            # minimum number of in-cluster frames so that a tiny
            # cluster can't claim a huge line.
            fit = [(s, obs) for s, obs in valid if 0 <= k + s < c.size]
            if len(fit) < len(valid) - max_missing_events:
                continue
            # Require at least 3 constraints to validate RMSE -- unless
            # the whole line has fewer than 3 frames, in which case we
            # match on whatever's available (the legacy behavior).
            if len(fit) < min(3, len(valid)):
                continue
            anchor_event = c.events[k + anchor_slot]
            # Reject candidates whose implied flight-clock offset is
            # outside the configured window. This is the key prior that
            # stops a 897-frame line from latching onto the "wrong"
            # 897-event cluster 20+ minutes away from its real capture
            # time. We compute the offset against the anchor frame's
            # absolute UTC time; the line's first frame's
            # acquire_timestamp gives us a +/- few-minute prior on
            # which cluster is "where in time" the line happened.
            anchor_event_utc = anchor_event.utc(leap_seconds=leap_seconds)
            implied_offset_s = (anchor_event_utc - anchor_acq_utc).total_seconds()
            if abs(implied_offset_s) > max_flight_offset_s:
                continue
            anchor_tow = anchor_event.tow_s
            expected = [c.events[k + s].tow_s - anchor_tow for s, _ in fit]
            r = _rmse([obs for _, obs in fit], expected)
            if r < best_rmse:
                best_rmse = r
                best = c
                best_offset = k

    if best is None or best_rmse > max_rmse_s:
        # max_rmse cap. Real matches sit at ~7-100 ms; clearly-wrong
        # matches (cluster within the abs-time window but with the
        # wrong intra-line firing pattern) come back at multiple
        # SECONDS. Returning NONE here lets ``match_all`` move on
        # rather than poisoning the cluster pool with a forced fit.
        return Match(line=line, cluster=None, slots=slots, rmse_s=math.inf,
                     flight_offset_s=None)
    anchor_event_utc = best.events[best_offset + anchor_slot].utc(
        leap_seconds=leap_seconds,
    )
    flight_offset = (anchor_event_utc - anchor_acq).total_seconds()
    return Match(line=line, cluster=best, slots=slots, rmse_s=best_rmse,
                 flight_offset_s=flight_offset, slot_offset=best_offset)


@dataclass
class CorrelationResult:
    """One-shot bundle of everything the GUI / CLI needs after correlation."""

    events: list[Event]
    clusters: list[Cluster]
    lines: list[LineGroup]
    matches: list[Match]
    incomplete_tiffs: list[Path]


SOURCE_TIFF = "tiff"
SOURCE_BAY14LINE = "bay14line"
VALID_SOURCES = (SOURCE_TIFF, SOURCE_BAY14LINE)


def correlate(
    sbf_csv: Path,
    root: Path,
    *,
    source: str = SOURCE_TIFF,
    frames_per_sweep: int = 13,
    cluster_gap_s: float = 60.0,
    cluster_min_size: int = 50,
    leap_seconds: int = DEFAULT_LEAP_SECONDS,
    recursive: bool = True,
    allow_reuse: bool = False,
    max_flight_offset_s: float = DEFAULT_MAX_FLIGHT_OFFSET_S,
    max_rmse_s: float = DEFAULT_MAX_RMSE_S,
    max_missing_events: int = DEFAULT_MAX_MISSING_EVENTS,
) -> CorrelationResult:
    """End-to-end pipeline: parse SBF, walk frame sources, match each line.

    With ``source='tiff'`` (default) we scan exported ``*.tif`` files; with
    ``source='bay14line'`` we scan raw ``*.bay14line`` containers and read
    per-chunk metadata directly. The matcher itself is source-agnostic --
    it only cares about ``(line, sweep, frame_in_sweep, acquire_ts)``.

    Reusable by both the CLI ``main()`` and the PySide6 GUI.
    """
    if source not in VALID_SOURCES:
        raise ValueError(
            f"source={source!r} must be one of {VALID_SOURCES}"
        )
    events = parse_sbf_csv(sbf_csv)
    all_clusters = cluster_events(events, gap_s=cluster_gap_s)
    clusters = [c for c in all_clusters if c.size >= cluster_min_size]
    # Re-index the kept clusters so they remain monotonically numbered.
    for new_idx, c in enumerate(clusters):
        c.index = new_idx
    if source == SOURCE_BAY14LINE:
        # No equivalent of "incomplete TIFF" detection: a half-written
        # .bay14line is handled inside iter_bay14line_frame_meta via the
        # BAY14FRM-magic recovery walk, so its frames still appear here.
        incomplete: list[Path] = []
        frames = discover_bay14line_frames(root, recursive=recursive)
    else:
        incomplete = find_incomplete_tiffs(root, recursive=recursive)
        frames = discover_frames(root, recursive=recursive)
    lines = group_by_line(frames)
    matches = match_all(
        lines, clusters,
        frames_per_sweep=frames_per_sweep,
        leap_seconds=leap_seconds,
        allow_reuse=allow_reuse,
        max_flight_offset_s=max_flight_offset_s,
        max_rmse_s=max_rmse_s,
        max_missing_events=max_missing_events,
    )
    return CorrelationResult(
        events=events,
        clusters=clusters,
        lines=lines,
        matches=matches,
        incomplete_tiffs=incomplete,
    )


def match_all(
    lines: list[LineGroup],
    clusters: list[Cluster],
    *,
    frames_per_sweep: int,
    leap_seconds: int = DEFAULT_LEAP_SECONDS,
    allow_reuse: bool = False,
    max_flight_offset_s: float = math.inf,
    max_rmse_s: float = math.inf,
    max_missing_events: int = 0,
) -> list[Match]:
    """Match each line to a cluster.

    By default a cluster can only be assigned to one line (greedy: we
    process lines in decreasing order of frame count -- so a long
    successful capture wins its cluster before a short aborted retry
    of the same line number can steal one of the remaining clusters).
    Within equal frame counts we tie-break on ``(line, hhmmss)`` for
    determinism. The output list keeps the caller's input order.

    ``allow_reuse=True`` is useful for diagnostics on a single-line
    dataset, and also for the multi-camera case where N cameras share
    every trigger and therefore every SBF cluster.
    """
    # Order line-iteration by information content: bigger captures
    # first. A 900-frame full L1 should win its cluster before any of
    # the 1-2-sweep aborted L1 retries get a chance to pick the wrong
    # one. Tie-break is deterministic on (line, hhmmss, cam_serial).
    order = sorted(
        range(len(lines)),
        key=lambda i: (
            -len(lines[i].frames),
            lines[i].line,
            lines[i].hhmmss,
            lines[i].cam_serial,
        ),
    )
    used: set[int] = set()
    matches: list[Optional[Match]] = [None] * len(lines)
    for i in order:
        line = lines[i]
        pool = [c for c in clusters if allow_reuse or c.index not in used]
        m = match_line(
            line, pool,
            frames_per_sweep=frames_per_sweep,
            leap_seconds=leap_seconds,
            max_flight_offset_s=max_flight_offset_s,
            max_rmse_s=max_rmse_s,
            max_missing_events=max_missing_events,
        )
        if m.cluster is not None:
            used.add(m.cluster.index)
        matches[i] = m
    # By construction every slot is populated; the cast keeps mypy happy.
    return [m for m in matches if m is not None]


# ─── CSV sidecar ─────────────────────────────────────────────────────────────


CSV_FIELDS = (
    "filename",
    "relative_path",
    "line",
    "sweep",
    "frame_in_sweep",
    "assigned",
    "latitude_deg",
    "longitude_deg",
    "altitude_m",
    "timestamp_utc",
    "satellites",
    "cluster_index",
    "event_slot",
    "note",
)


def _fmt_iso_utc(dt: Optional[datetime]) -> str:
    if dt is None:
        return ""
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _row_for_frame(
    frame: "Frame",
    *,
    event: Optional["Event"],
    cluster_index: Optional[int],
    slot: Optional[int],
    note: str,
    leap_seconds: int,
    root: Optional[Path],
) -> dict[str, str]:
    """Build one CSV row. Empty strings on missing fields (CSV-friendly).

    In bay14line mode the ``filename`` and ``relative_path`` columns
    don't carry the source ``.bay14line`` name (which is shared by
    every chunk inside the file -- useless for joining against
    geotagged TIFFs downstream). Instead we synthesize the would-be
    ``Bay14Reader`` export name from the frame's parsed metadata, so
    the CSV can be joined against the eventual TIFFs by filename
    alone. For TIFF source we keep the existing on-disk name.
    """
    if frame.is_bay14line_source:
        export_name = frame.tiff_export_name()
        rel = f"{frame.cam}/{export_name}"  # matches Bay14Reader's
        # default per-camera output subfolder layout.
    else:
        export_name = frame.path.name
        try:
            rel = str(frame.path.relative_to(root)) if root else frame.path.name
        except ValueError:
            rel = frame.path.name
    if event is not None and event.lat_deg is not None and event.lon_deg is not None:
        ts = event.utc(leap_seconds=leap_seconds)
        return {
            "filename": export_name,
            "relative_path": rel,
            "line": str(frame.line),
            "sweep": str(frame.sweep),
            "frame_in_sweep": str(frame.frame_in_sweep),
            "assigned": "1",
            "latitude_deg": f"{event.lat_deg:.9f}",
            "longitude_deg": f"{event.lon_deg:.9f}",
            "altitude_m": (
                f"{event.height_m:.3f}"
                if event.height_m is not None and not math.isnan(event.height_m)
                else ""
            ),
            "timestamp_utc": _fmt_iso_utc(ts),
            "satellites": "" if event.n_sv is None else str(event.n_sv),
            "cluster_index": "" if cluster_index is None else str(cluster_index),
            "event_slot": "" if slot is None else str(slot),
            "note": note,
        }
    return {
        "filename": export_name,
        "relative_path": rel,
        "line": str(frame.line),
        "sweep": str(frame.sweep),
        "frame_in_sweep": str(frame.frame_in_sweep),
        "assigned": "0",
        "latitude_deg": "",
        "longitude_deg": "",
        "altitude_m": "",
        "timestamp_utc": "",
        "satellites": "",
        "cluster_index": "" if cluster_index is None else str(cluster_index),
        "event_slot": "" if slot is None else str(slot),
        "note": note,
    }


def build_correlation_rows(
    result: "CorrelationResult",
    *,
    leap_seconds: int = DEFAULT_LEAP_SECONDS,
    root: Optional[Path] = None,
) -> list[dict[str, str]]:
    """Flatten a correlation result into per-frame CSV rows.

    One row per discovered frame. Frames that were successfully mapped
    to an SBF event get a populated lat/lon/alt/timestamp; unmatched
    frames (no line cluster, slot out of range, missing position, ...)
    appear with ``assigned=0`` and a short ``note`` so the spreadsheet
    still accounts for them.

    The output is sorted by ``(line, sweep, frame_in_sweep)`` so a
    spreadsheet reader sees a stable, predictable order.
    """
    rows: list[dict[str, str]] = []
    for m in result.matches:
        if m.cluster is None:
            for frame in m.line.frames:
                rows.append(_row_for_frame(
                    frame,
                    event=None,
                    cluster_index=None,
                    slot=None,
                    note="no cluster matched for this line",
                    leap_seconds=leap_seconds,
                    root=root,
                ))
            continue
        for frame, slot in zip(m.line.frames, m.slots):
            ev = m.event_for_slot(slot)
            if ev is None:
                rows.append(_row_for_frame(
                    frame,
                    event=None,
                    cluster_index=m.cluster.index,
                    slot=slot,
                    note=(f"slot {slot}+offset {m.slot_offset} outside "
                          f"cluster size {m.cluster.size}"),
                    leap_seconds=leap_seconds,
                    root=root,
                ))
                continue
            if ev.lat_deg is None or ev.lon_deg is None:
                rows.append(_row_for_frame(
                    frame,
                    event=None,
                    cluster_index=m.cluster.index,
                    slot=slot,
                    note="event has no position",
                    leap_seconds=leap_seconds,
                    root=root,
                ))
                continue
            rows.append(_row_for_frame(
                frame,
                event=ev,
                cluster_index=m.cluster.index,
                slot=slot,
                note="",
                leap_seconds=leap_seconds,
                root=root,
            ))
    rows.sort(key=lambda r: (
        int(r["line"]) if r["line"] else 0,
        int(r["sweep"]) if r["sweep"] else 0,
        int(r["frame_in_sweep"]) if r["frame_in_sweep"] else 0,
    ))
    return rows


def write_correlation_csv(
    result: "CorrelationResult",
    out_path: Path,
    *,
    leap_seconds: int = DEFAULT_LEAP_SECONDS,
    root: Optional[Path] = None,
) -> int:
    """Write the per-frame correlation rows to *out_path*.

    Returns the number of rows written (= number of frames discovered
    under *root*, matched or not). Empty / missing fields are emitted
    as empty strings so spreadsheets can sort numeric columns cleanly.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = build_correlation_rows(result, leap_seconds=leap_seconds, root=root)
    # ``newline=""`` is the standard Python recipe for csv.writer on
    # Windows; without it the file gets blank lines between rows when
    # opened by Excel.
    with out_path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(CSV_FIELDS))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return len(rows)


# ─── EXIF write ──────────────────────────────────────────────────────────────


def event_to_fix(
    event: Event, *, leap_seconds: int = DEFAULT_LEAP_SECONDS,
) -> GpsFix:
    """Map an SBF event to a :class:`GpsFix` (the shape ``_build_gps_extratags``
    expects). Altitude is reported as ellipsoidal height; subtract the
    undulation if you want orthometric (we leave the choice to the caller's
    workflow and keep height as-is, matching the live capture path)."""
    return GpsFix(
        latitude_deg=event.lat_deg,
        longitude_deg=event.lon_deg,
        altitude_m=event.height_m,
        timestamp_utc=event.utc(leap_seconds=leap_seconds),
        satellites=event.n_sv,
        hdop=None,
        fix_quality=1 if (event.lat_deg is not None and event.lon_deg is not None) else 0,
    )


_PHOTOMETRIC_FROM_INT = {0: "miniswhite", 1: "minisblack", 2: "rgb"}


# ``detect_exiftool``, ``exiftool_geotag``, ``DEFAULT_EXIFTOOL_PATHS``, and
# ``apply_exif_gps_from_image_description`` are re-exported from
# ``capture.tiff_writer`` (see the import block above). The single source of
# truth lives there so Bay14Reader can call them without depending on this
# whole geotag CLI module.


def exiftool_clear_bogus_ifd0_tags(path: Path, *, exiftool_exe: Path) -> None:
    """Remove the bogus IFD0 tags that an earlier ``tifffile`` write may
    have planted at numeric IDs 1, 2, 3, 4, 5, 6, 7, 8, 11, and 29.

    The legacy tifffile path stuffed EXIF-GPS-style values into those
    raw IFD0 slots (since tifffile can't emit a real GPS sub-IFD).
    They're harmless but noisy in ExifTool dumps; this helper wipes
    them so the file ends up looking like a clean post-hoc geotag.
    """
    # ExifTool can only erase tags it knows. The leftover legacy slots
    # we wrote are not in its taxonomy at IFD0, so the safest cleanup
    # is to leave them be -- they're not malformed enough to break the
    # TIFF and they don't appear in -EXIF:all dumps. This function is a
    # no-op placeholder so the call site stays explicit about intent.
    return None


def write_gps_into_tiff(path: Path, fix: GpsFix, *, atomic: bool = True) -> None:
    """Rewrite *path* with EXIF GPS tags + ``gps`` block in the JSON description.

    Reads the pixel data + ImageDescription once, writes the new TIFF to
    a sibling temp file, then atomically replaces the original. The
    pixel pipeline is identity (``compression=None``); only metadata
    changes.
    """
    with tifffile.TiffFile(str(path)) as tf:
        page = tf.pages[0]
        arr = page.asarray()
        photometric_int = int(page.photometric)
        desc_tag = page.tags.get("ImageDescription")
        desc_str = desc_tag.value if desc_tag is not None else ""

    try:
        desc_obj = json.loads(desc_str) if desc_str else {}
        if not isinstance(desc_obj, dict):
            desc_obj = {"_legacy_description": desc_obj}
    except Exception:
        desc_obj = {"_legacy_description": desc_str}
    desc_obj["gps"] = fix.to_dict()
    new_desc = json.dumps(desc_obj, separators=(",", ":"))

    extratags = _build_gps_extratags(fix) if fix.has_position else []

    photometric_name = _PHOTOMETRIC_FROM_INT.get(photometric_int, "minisblack")

    if atomic:
        tmp_dir = path.parent
        with tempfile.NamedTemporaryFile(
            "wb", dir=tmp_dir, prefix=f".{path.name}.tmp", delete=False,
        ) as fp:
            tmp_path = Path(fp.name)
        try:
            tifffile.imwrite(
                str(tmp_path),
                arr,
                photometric=photometric_name,
                compression=None,
                bigtiff=False,
                description=new_desc,
                extratags=extratags,
            )
            shutil.move(str(tmp_path), str(path))
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
    else:
        tifffile.imwrite(
            str(path),
            arr,
            photometric=photometric_name,
            compression=None,
            bigtiff=False,
            description=new_desc,
            extratags=extratags,
        )


# ─── .bay14line in-place GPS embedding ───────────────────────────────────────


def _patch_chunk_header_in_place(
    fh,
    chunk_offset: int,
    *,
    fix: GpsFix,
    extra: Optional[dict] = None,
) -> tuple[int, int]:
    """Re-render one frame-chunk header to carry a ``"gps": {...}`` block.

    Reads the existing header at *chunk_offset*, parses its JSON, adds /
    overwrites the ``gps`` key with :meth:`GpsFix.to_dict` (the same
    schema the live capture path writes and that ``bay14_export_core``
    consumes during TIFF export), and writes the new header back at the
    SAME byte offset and SAME byte length.

    The fixed 16-byte prefix (magic + header_len + payload_len) is left
    untouched, so file-level chunk offsets, the line index, and the
    payload position never shift. If the augmented JSON would not fit
    inside the existing header padding (``header_len - 16`` bytes),
    raises :class:`ValueError` -- callers should treat this as a hard
    failure for the chunk rather than silently dropping data.

    Returns ``(header_len, len(new_json))`` for diagnostics / logging.
    """
    from capture.raw_bay_writer import (  # noqa: E402
        SWEEP_FRAME_HEADER_MAGIC,
    )
    fh.seek(chunk_offset)
    raw = fh.read(4096)  # initial peek; sweep headers are <= 4 KiB by design
    if len(raw) < 16:
        raise ValueError(f"chunk at offset {chunk_offset} truncated "
                         f"({len(raw)} byte(s))")
    if raw[:8] != SWEEP_FRAME_HEADER_MAGIC:
        raise ValueError(
            f"chunk at offset {chunk_offset}: expected magic "
            f"{SWEEP_FRAME_HEADER_MAGIC!r}, found {raw[:8]!r}"
        )
    header_len = int.from_bytes(raw[8:12], "little")
    payload_len = int.from_bytes(raw[12:16], "little")
    if header_len > len(raw):
        # Direct-I/O writer aligns to 4 KiB so 4 KiB is enough in practice,
        # but a future writer with a bigger alignment would land here.
        fh.seek(chunk_offset)
        raw = fh.read(header_len)
        if len(raw) < header_len:
            raise ValueError(
                f"chunk at offset {chunk_offset}: needed {header_len} "
                f"header bytes, got {len(raw)}"
            )
    json_blob = raw[16:header_len].rstrip(b"\x00")
    meta = json.loads(json_blob.decode("utf-8")) if json_blob else {}
    gps_dict: dict = fix.to_dict()
    if extra:
        gps_dict.update(extra)
    # Drop keys whose value is None so the JSON stays compact and Bay14Reader's
    # _gps_fix_from_meta() / from_dict() sees only populated fields.
    gps_dict = {k: v for k, v in gps_dict.items() if v is not None}
    meta["gps"] = gps_dict
    new_json = json.dumps(meta, separators=(",", ":")).encode("utf-8")
    if 16 + len(new_json) > header_len:
        raise ValueError(
            f"chunk at offset {chunk_offset}: augmented JSON "
            f"({len(new_json)} B) does not fit in existing header "
            f"padding ({header_len - 16} B). In-place patch would "
            f"need to shift downstream chunk offsets."
        )
    out = bytearray(header_len)
    out[0:8] = SWEEP_FRAME_HEADER_MAGIC
    out[8:12] = header_len.to_bytes(4, "little")
    out[12:16] = payload_len.to_bytes(4, "little")
    out[16:16 + len(new_json)] = new_json
    # Bytes [16 + len(new_json) : header_len] stay as zeros (the
    # default ``bytearray(N)`` initialises to NUL), preserving the
    # NUL-padding the readers strip out via ``.rstrip(b"\x00")``.
    fh.seek(chunk_offset)
    fh.write(bytes(out))
    return header_len, len(new_json)


@dataclass
class Bay14LineWriteStats:
    """Per-file outcome of :func:`write_gps_into_bay14line`."""

    line_path: Path
    written: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def write_gps_into_bay14line(
    line_path: Path,
    gps_by_frame: dict[tuple[int, int], GpsFix],
    *,
    extra_by_frame: Optional[dict[tuple[int, int], dict]] = None,
    fsync: bool = True,
) -> Bay14LineWriteStats:
    """In-place embed a per-frame ``gps`` block into a ``.bay14line``.

    *gps_by_frame* maps ``(sweep, frame_in_sweep)`` to the :class:`GpsFix`
    that should ride along with that chunk. *extra_by_frame* is an
    optional parallel dict whose values are merged into the per-chunk
    ``gps`` JSON (handy for traceability: cluster index, event slot,
    flight-clock offset, ...).

    The chunk-header rewrite is byte-length preserving so every chunk
    offset, the line index, and the payload position stay valid -- the
    file size is unchanged. Downstream, ``Bay14Reader`` already reads
    ``frame_meta["gps"]`` via :func:`bay14_export_core._gps_fix_from_meta`
    and emits proper EXIF GPS tags on TIFF export, so this function is
    all that's needed to make raw-mode geotagging round-trip into the
    final TIFFs.
    """
    import mmap
    from capture.raw_bay_writer import (  # noqa: E402
        LINE_HEADER_FIXED_PREFIX,
        LINE_INDEX_ENTRY_SIZE,
        parse_line_header_prefix,
        parse_line_index,
        recover_line_chunks,
    )
    stats = Bay14LineWriteStats(line_path=line_path)
    if not gps_by_frame:
        return stats
    extra_by_frame = extra_by_frame or {}
    # Resolve every chunk's on-disk offset. Use the file's index when
    # available; fall back to the BAY14FRM-magic recovery walk when the
    # index was never written (cancelled-mid-line capture) -- the same
    # path :func:`iter_bay14line_frame_meta` uses, so coverage matches.
    with open(line_path, "rb") as fh_read:
        with mmap.mmap(fh_read.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            prefix = mm[:LINE_HEADER_FIXED_PREFIX]
            _hdr_len, n_frames, idx_off, _ = parse_line_header_prefix(prefix)
            if n_frames > 0 and idx_off > 0:
                idx_blob = mm[idx_off: idx_off + n_frames * LINE_INDEX_ENTRY_SIZE]
                chunks = parse_line_index(idx_blob, n_frames)
            else:
                _meta, chunks = recover_line_chunks(str(line_path))
    offset_by_frame: dict[tuple[int, int], int] = {
        (int(sweep), int(f_in)): int(offset)
        for sweep, f_in, offset, _p_len, _unc in chunks
    }

    with open(line_path, "r+b") as fh:
        for key, fix in gps_by_frame.items():
            offset = offset_by_frame.get(key)
            if offset is None:
                stats.skipped += 1
                stats.errors.append(
                    f"S{key[0]}F{key[1]}: no chunk with this (sweep, frame_in_sweep)"
                )
                continue
            try:
                _patch_chunk_header_in_place(
                    fh, offset,
                    fix=fix,
                    extra=extra_by_frame.get(key),
                )
                stats.written += 1
            except Exception as exc:
                stats.failed += 1
                stats.errors.append(f"S{key[0]}F{key[1]} @ {offset}: {exc}")
        if fsync:
            fh.flush()
            os.fsync(fh.fileno())
    return stats


def write_gps_into_bay14line_matches(
    matches: list[Match],
    *,
    leap_seconds: int = DEFAULT_LEAP_SECONDS,
    include_traceability: bool = True,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> tuple[int, int, int, list[str]]:
    """Apply :func:`write_gps_into_bay14line` to every matched frame.

    Groups frames by their owning ``.bay14line`` file so each container is
    opened, patched, and fsync'd exactly once. Returns
    ``(written, skipped, failed, errors)`` aggregated across all files.

    *progress_cb* is invoked as ``cb(done, total, message)`` after every
    file (a chunk-level callback would be too chatty for 10 000+ frames).
    *cancel_cb* is polled before each file; returning True aborts mid-run.
    """
    # Bucket: path -> (key -> (GpsFix, extra_dict))
    by_file: dict[Path, dict[tuple[int, int], tuple[GpsFix, dict]]] = {}
    skipped_pre = 0
    for m in matches:
        if m.cluster is None:
            skipped_pre += len(m.line.frames)
            continue
        for frame, slot in zip(m.line.frames, m.slots):
            if not frame.is_bay14line_source:
                skipped_pre += 1
                continue
            ev = m.event_for_slot(slot)
            if ev is None:
                skipped_pre += 1
                continue
            fix = event_to_fix(ev, leap_seconds=leap_seconds)
            if not fix.has_position:
                skipped_pre += 1
                continue
            extra: dict = {}
            if include_traceability:
                extra = {
                    "cluster_index": m.cluster.index,
                    "event_slot": slot + m.slot_offset,
                    "flight_offset_s": (
                        round(m.flight_offset_s, 3)
                        if m.flight_offset_s is not None else None
                    ),
                }
                extra = {k: v for k, v in extra.items() if v is not None}
            by_file.setdefault(frame.path, {})[(frame.sweep, frame.frame_in_sweep)] = (
                fix, extra,
            )

    total_files = len(by_file)
    total_written = 0
    total_skipped = skipped_pre
    total_failed = 0
    all_errors: list[str] = []
    done_files = 0
    for path, entries in by_file.items():
        if cancel_cb is not None and cancel_cb():
            log.info("cancelled before patching %s", path.name)
            break
        gps_by_frame = {k: pair[0] for k, pair in entries.items()}
        extra_by_frame = {k: pair[1] for k, pair in entries.items()}
        try:
            stats = write_gps_into_bay14line(
                path, gps_by_frame, extra_by_frame=extra_by_frame,
            )
            total_written += stats.written
            total_skipped += stats.skipped
            total_failed += stats.failed
            for e in stats.errors:
                all_errors.append(f"{path.name}: {e}")
        except Exception as exc:
            total_failed += len(entries)
            all_errors.append(f"{path.name}: {exc}")
            log.exception("write_gps_into_bay14line failed for %s", path)
        done_files += 1
        if progress_cb is not None:
            progress_cb(done_files, total_files, path.name)
    return total_written, total_skipped, total_failed, all_errors


# ─── CLI ─────────────────────────────────────────────────────────────────────


def _format_dt(dt: Optional[datetime]) -> str:
    if dt is None:
        return "?"
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + "Z"


def _print_plan(
    matches: list[Match], *, offset_warn_s: float, leap_seconds: int,
) -> tuple[int, int, list[str]]:
    """Print a per-line summary; return (good_lines, total_frames, warnings)."""
    warnings: list[str] = []
    offsets = [m.flight_offset_s for m in matches if m.flight_offset_s is not None]
    median_off = statistics.median(offsets) if offsets else None

    log.info("")
    log.info("Plan (median flight-clock offset: %s s):",
             f"{median_off:.2f}" if median_off is not None else "n/a")
    good = 0
    total_frames = 0
    for m in matches:
        n = m.n_frames
        total_frames += n
        label = m.line.label()
        if m.cluster is None:
            warnings.append(
                f"{label}: no candidate cluster (size < {max(m.slots) + 1 if m.slots else n})"
            )
            log.warning("  %-18s frames=%-4d cluster=NONE", label, n)
            continue
        good += 1
        c = m.cluster
        first_utc = c.events[0].utc(leap_seconds=leap_seconds)
        off_txt = f"{m.flight_offset_s:+.2f}s" if m.flight_offset_s is not None else "n/a"
        rmse_txt = f"{m.rmse_s*1000:.0f}ms" if math.isfinite(m.rmse_s) else "n/a"
        flag = ""
        if median_off is not None and m.flight_offset_s is not None:
            if abs(m.flight_offset_s - median_off) > offset_warn_s:
                flag = "  ! offset outlier"
                warnings.append(
                    f"{label}: offset {m.flight_offset_s:+.2f}s deviates "
                    f">{offset_warn_s}s from median {median_off:+.2f}s"
                )
        # Partial-fit count: frames whose mapped event would fall
        # outside the cluster -- they keep their filename but get no
        # coords. Surface so the operator can spot a 894-event cluster
        # claiming a 897-frame line.
        unmatched = sum(1 for s in m.slots if m.event_for_slot(s) is None)
        partial_txt = f"  ({unmatched} no-coords)" if unmatched else ""
        if unmatched:
            warnings.append(
                f"{label}: {unmatched} frame(s) at the line tail have "
                f"no matching GPS event"
            )
        log.info(
            "  %-18s frames=%-4d cluster=#%-3d size=%-5d slot_off=%-4d "
            "start=%s rmse=%s offset=%s%s%s",
            label, n, c.index, c.size, m.slot_offset, _format_dt(first_utc),
            rmse_txt, off_txt, flag, partial_txt,
        )
    return good, total_frames, warnings


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("sbf_csv", type=Path, help="Septentrio ExtEventPVTGeodetic CSV log.")
    p.add_argument(
        "root", type=Path,
        help="Folder of frames. Either exported TIFFs (default) or raw "
             ".bay14line containers; see --source.",
    )
    p.add_argument(
        "--source", choices=VALID_SOURCES, default=SOURCE_TIFF,
        help="Frame source layout. 'tiff' (default) walks *.tif exports from "
             "Bay14Reader; 'bay14line' walks *.bay14line containers straight "
             "off the SSDs and reads per-chunk acquire_timestamps without "
             "decoding pixels. With --write in bay14line mode each matched "
             "chunk header is patched IN PLACE to embed a 'gps' JSON block "
             "(same schema GpsFix.to_dict produces) -- Bay14Reader's TIFF "
             "export then reads that block and emits standard EXIF GPS tags. "
             "The chunk-header rewrite is byte-length preserving, so chunk "
             "offsets and the line index stay valid.",
    )
    p.add_argument(
        "--cluster-gap-s", type=float, default=60.0,
        help="Inter-event gap (s) that splits one event cluster from the next. "
             "Must be LONGER than the inter-sweep pause inside a single line "
             "(seen up to ~43 s on the May 2026 rig) and SHORTER than the "
             "inter-line pause (typically minutes). Default 60.0 absorbs "
             "wider inter-sweep gaps and any pre-line trigger-test burst "
             "into the same cluster as its line; ``match_line`` then scans "
             "starting offsets inside the cluster to find the real line "
             "boundary. Tune if your inter-sweep timing exceeds 60 s.",
    )
    p.add_argument(
        "--cluster-min-size", type=int, default=50,
        help="Discard clusters smaller than this many events before matching. "
             "With cluster-gap-s=60 the trigger bursts get merged into the "
             "following line cluster, but standalone bursts (without a line "
             "after them) still need filtering. Default 50.",
    )
    p.add_argument(
        "--frames-per-sweep", type=int, default=13,
        help="Frames captured per sweep at acquisition time. Used to convert "
             "(sweep, frame_in_sweep) into the slot index inside the line's "
             "event cluster (default 13).",
    )
    p.add_argument(
        "--leap-seconds", type=int, default=DEFAULT_LEAP_SECONDS,
        help=f"GPS-UTC leap-second offset (default {DEFAULT_LEAP_SECONDS}).",
    )
    p.add_argument(
        "--offset-warn-s", type=float, default=30.0,
        help="Warn when a line's implied flight-clock offset deviates from the "
             "median by more than this many seconds (default 30).",
    )
    p.add_argument(
        "--max-missing-events", type=int,
        default=DEFAULT_MAX_MISSING_EVENTS,
        help="Allow up to this many GPS events to be MISSING from the "
             f"matched cluster relative to the line's frame count "
             f"(default {DEFAULT_MAX_MISSING_EVENTS}). The canonical "
             "case is 897 recorded frames but only 894 GPS events in "
             "the real cluster -- the receiver dropped the last few "
             "triggers; the first 894 frames still get coords, the "
             "last 3 come out as no-coords rows. Set to 0 for strict "
             "exact-fit matching.",
    )
    p.add_argument(
        "--max-rmse-s", type=float, default=DEFAULT_MAX_RMSE_S,
        help="REJECT a cluster match whose intra-line RMSE exceeds this "
             f"many seconds (default {DEFAULT_MAX_RMSE_S}s). Real-world "
             "good matches sit at ~7-100 ms; clearly-wrong matches the "
             "absolute-time prior couldn't reject come back at "
             "multiple SECONDS. Widening this trades robustness for "
             "more (possibly bogus) matches.",
    )
    p.add_argument(
        "--max-flight-offset-s", type=float,
        default=DEFAULT_MAX_FLIGHT_OFFSET_S,
        help="REJECT cluster candidates whose implied flight-clock offset "
             "(matched event UTC minus frame acquire UTC) exceeds this "
             f"many seconds (default {int(DEFAULT_MAX_FLIGHT_OFFSET_S)}s = "
             "15 min). This is the absolute-time prior that stops a 897-"
             "frame line from latching onto the wrong 897-event cluster "
             "20+ min from its real capture time when pure RMSE matching "
             "can't distinguish two clusters with similar intra-line "
             "firing patterns. Widen if your flight-computer clock can "
             "drift more than 15 min from GPS.",
    )
    p.add_argument(
        "--no-recursive", action="store_true",
        help="Do not recurse into subfolders of *root*.",
    )
    p.add_argument(
        "--allow-cluster-reuse", action="store_true",
        help="Let multiple lines map to the same cluster (diagnostic only; "
             "default off so each cluster is consumed at most once).",
    )
    p.add_argument(
        "--write", action="store_true",
        help="Actually rewrite the TIFFs with EXIF GPS tags. Default is "
             "dry-run (print the plan and exit).",
    )
    p.add_argument(
        "--exif-writer", choices=("auto", "exiftool", "tifffile"),
        default="auto",
        help="EXIF write backend. 'auto' uses ExifTool if available (fast, "
             "in-place, EXIF-correct), else falls back to tifffile (slow, "
             "GPS tags placed at IFD0 with raw IDs and won't be read by "
             "standard EXIF viewers).",
    )
    p.add_argument(
        "--exiftool-exe", type=Path, default=None,
        help="Explicit path to an exiftool.exe (overrides auto-detection).",
    )
    p.add_argument(
        "--limit-frames", type=int, default=None,
        help="During --write, only tag the first N frames (in line + slot "
             "order). Useful for spot-checking the EXIF output on one frame "
             "before processing the whole flight.",
    )
    p.add_argument(
        "--csv-output", type=Path, default=None,
        help="Write a per-frame CSV (filename, line, sweep, frame_in_sweep, "
             "latitude_deg, longitude_deg, altitude_m, timestamp_utc, ...) "
             "alongside the run. Generated even on a dry-run, so the CSV "
             "can serve as a sidecar coordinate list when full EXIF writes "
             "are impractical (e.g. BigTIFF, very large frames on cloud "
             "storage). Unmatched frames appear with empty coordinates and "
             "a short note.",
    )
    p.add_argument(
        "--delete-incomplete-tiffs", action="store_true",
        help="Delete TIFFs that fail to open with tifffile or lack an "
             "``acquire_timestamp`` in their ImageDescription (typical of "
             "an export cancelled mid-write). Files are listed during "
             "dry-run; deletion only happens together with --write.",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true",
        help="DEBUG logging.",
    )
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    if not args.sbf_csv.is_file():
        log.error("SBF CSV not found: %s", args.sbf_csv)
        return 2
    if not args.root.is_dir():
        log.error("Root folder not found: %s", args.root)
        return 2

    log.info("Reading SBF CSV: %s", args.sbf_csv)
    events = parse_sbf_csv(args.sbf_csv)
    log.info("  %d events", len(events))
    if not events:
        log.error("No events parsed; aborting.")
        return 2

    clusters_all = cluster_events(events, gap_s=args.cluster_gap_s)
    clusters = [c for c in clusters_all if c.size >= args.cluster_min_size]
    log.info(
        "  %d clusters (gap_s=%.1f); %d after size >= %d",
        len(clusters_all), args.cluster_gap_s, len(clusters), args.cluster_min_size,
    )

    if args.source == SOURCE_TIFF:
        log.info("Scanning for incomplete / cancelled-mid-write TIFFs: %s", args.root)
        bad = find_incomplete_tiffs(args.root, recursive=not args.no_recursive)
        if bad:
            log.warning("  %d incomplete TIFF(s):", len(bad))
            for p in bad:
                log.warning("    %s", p)
            if args.delete_incomplete_tiffs and args.write:
                for p in bad:
                    try:
                        p.unlink()
                        log.info("    deleted %s", p.name)
                    except OSError as exc:
                        log.error("    delete failed for %s: %s", p, exc)
            elif args.delete_incomplete_tiffs and not args.write:
                log.info(
                    "    (dry-run: would delete %d file(s); re-run with --write to "
                    "actually remove them)", len(bad),
                )
        else:
            log.info("  no incomplete TIFFs found")

        log.info("Scanning frames in: %s", args.root)
        frames = discover_frames(args.root, recursive=not args.no_recursive)
        log.info("  %d TIFFs with parseable names", len(frames))
    else:
        bad = []
        log.info("Scanning raw .bay14line containers in: %s", args.root)
        frames = discover_bay14line_frames(
            args.root, recursive=not args.no_recursive,
        )
        n_files = len({f.path for f in frames})
        log.info(
            "  %d chunk(s) across %d .bay14line file(s) with parseable names",
            len(frames), n_files,
        )

    lines = group_by_line(frames)
    log.info("  %d line(s): %s", len(lines), [g.line for g in lines])
    if not lines:
        log.error("No frames discovered; aborting.")
        return 2

    matches = match_all(
        lines, clusters,
        frames_per_sweep=args.frames_per_sweep,
        leap_seconds=args.leap_seconds,
        allow_reuse=args.allow_cluster_reuse,
        max_flight_offset_s=args.max_flight_offset_s,
        max_rmse_s=args.max_rmse_s,
        max_missing_events=args.max_missing_events,
    )
    good, total_frames, warnings = _print_plan(
        matches,
        offset_warn_s=args.offset_warn_s,
        leap_seconds=args.leap_seconds,
    )
    for w in warnings:
        log.warning("WARN: %s", w)

    if args.csv_output is not None:
        result = CorrelationResult(
            events=events,
            clusters=clusters,
            lines=lines,
            matches=matches,
            incomplete_tiffs=bad,
        )
        n_rows = write_correlation_csv(
            result, args.csv_output,
            leap_seconds=args.leap_seconds,
            root=args.root,
        )
        log.info("Wrote %d-row CSV: %s", n_rows, args.csv_output)

    if args.source == SOURCE_TIFF:
        # Pick the EXIF backend even on dry-run, so users get told which
        # one would run before they commit to --write.
        exiftool_exe = detect_exiftool(args.exiftool_exe)
        if args.exif_writer == "exiftool" and exiftool_exe is None:
            log.error(
                "--exif-writer exiftool requested but no exiftool executable found"
            )
            return 2
        if args.exif_writer == "tifffile":
            backend = "tifffile"
        elif args.exif_writer == "exiftool":
            backend = "exiftool"
        else:  # auto
            backend = "exiftool" if exiftool_exe is not None else "tifffile"
        log.info("EXIF backend: %s%s",
                 backend, f" ({exiftool_exe})" if backend == "exiftool" else "")
    else:
        backend = "bay14line"
        exiftool_exe = None
        log.info("Write backend: in-place .bay14line chunk-header patch")

    if not args.write:
        log.info("")
        log.info("Dry-run: %d/%d line(s) matched, covering %d frame(s). "
                 "Re-run with --write to apply.",
                 good, len(matches), total_frames)
        return 0 if not warnings else 1

    written = 0
    failed = 0
    skipped = 0
    if backend == "bay14line":
        # Single-shot per-file patch path: opens each .bay14line once,
        # patches every matched chunk, fsync, close. Bay14Reader's TIFF
        # encoder picks up the embedded ``gps`` block and emits EXIF GPS
        # tags on export.
        if args.limit_frames is not None:
            log.warning("--limit-frames is ignored in bay14line write mode")
        def _on_file_done(done: int, total: int, name: str) -> None:
            log.info("  patched (%d/%d) %s", done, total, name)
        written, skipped, failed, errors = write_gps_into_bay14line_matches(
            matches,
            leap_seconds=args.leap_seconds,
            progress_cb=_on_file_done,
        )
        for e in errors:
            log.warning("  %s", e)
        log.info("")
        log.info(
            "Done. Patched %d chunk(s); skipped %d; %d failure(s).",
            written, skipped, failed,
        )
        return 0 if failed == 0 else 1

    limit_left = args.limit_frames if args.limit_frames is not None else None
    for m in matches:
        if m.cluster is None:
            skipped += len(m.line.frames)
            continue
        for frame, slot in zip(m.line.frames, m.slots):
            if limit_left is not None and limit_left <= 0:
                break
            ev = m.event_for_slot(slot)
            if ev is None:
                log.warning("Skipping %s: slot %d+offset %d outside matched "
                            "cluster (size %d)",
                            frame.path.name, slot, m.slot_offset, m.cluster.size)
                skipped += 1
                continue
            if not frame.path.is_file():
                log.warning("Skipping %s: file vanished (deleted as incomplete?)",
                            frame.path.name)
                skipped += 1
                continue
            fix = event_to_fix(ev, leap_seconds=args.leap_seconds)
            if not fix.has_position:
                log.warning("Skipping %s: matched event has no position", frame.path.name)
                skipped += 1
                continue
            try:
                if backend == "exiftool":
                    exiftool_geotag(frame.path, fix, exiftool_exe=exiftool_exe)
                else:
                    write_gps_into_tiff(frame.path, fix)
                written += 1
                if limit_left is not None:
                    limit_left -= 1
                log.info(
                    "WROTE %s  lat=%.6f lon=%.6f alt=%.2f utc=%s",
                    frame.path.name, fix.latitude_deg, fix.longitude_deg,
                    fix.altitude_m if fix.altitude_m is not None else float("nan"),
                    fix.timestamp_utc.isoformat(timespec="milliseconds")
                    if fix.timestamp_utc is not None else "?",
                )
            except subprocess.CalledProcessError as exc:
                failed += 1
                log.error("FAILED %s: exiftool exit %d\n%s",
                          frame.path.name, exc.returncode,
                          (exc.stderr or "").strip())
            except Exception as exc:
                failed += 1
                log.exception("FAILED %s: %s", frame.path.name, exc)
        if limit_left is not None and limit_left <= 0:
            break
    log.info("")
    log.info("Wrote EXIF GPS into %d TIFF(s); skipped %d; %d failure(s).",
             written, skipped, failed)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
