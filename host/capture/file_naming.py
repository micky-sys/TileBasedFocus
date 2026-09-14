"""Filename + target-path construction for captured frames.

Two file scheme variants:

* M0 / M1 sweep frames (one frame per trigger pulse, 1..13 per sweep,
  many sweeps per line; written as raw 14-bit-packed Bayer with an
  embedded JSON header -- see :mod:`raw_bay_writer`)::

      <drive>:\\<project>\\Cam{SN}_{YYYYMMDD}_{project}_L{line}_S{sweep}_{frame}.bay14

  ``sweep`` is 1-indexed and resets to 1 every time the operator
  changes the line (Line up / Line down). ``frame`` is the position
  within the current sweep (1..13). Each (line, sweep, frame)
  triple is therefore globally unique within a project + day.

* M4 focus-view captures (best-tile crops, optional, timestamped, classic
  uncompressed uint16 TIFF since they're tiny ROIs and don't pressure
  the drive)::

      <drive>:\\<project>\\focus\\[{tag}_]Cam{SN}_{YYYYMMDD}_{project}_focus_{HHMMSS_mmm}.tif

  ``tag`` is optional and LEADS the name so a directory sort groups one
  tracked object's whole life together (e.g. ``OID007_y1408_s43254``);
  untagged captures keep the plain timestamped form.

``SN`` is the camera's GenICam ``DeviceSerialNumber`` (e.g. ``2439007``)
read once at startup. The project name is user-entered in the GUI; we
sanitise it to be a legal Windows path component (no ``<>:"/\\|?*`` or
control characters, trimmed, not empty, not a reserved DOS name). The
line number is a signed integer the user bumps with the ``Line up`` /
``Line down`` buttons.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
from dataclasses import dataclass
from typing import Optional

_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_DOS = frozenset(
    {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)

# Subdirectory used for M4 focus-view captures so they can't collide with
# M0/M1 sweep frames in the project root.
FOCUS_SUBDIR = "focus"


def sanitize_project_name(name: str) -> str:
    """Return a Windows-path-safe version of *name*.

    Raises ValueError for empty or reserved names so the GUI can show a clear
    error instead of silently writing to a weird folder.
    """
    stripped = (name or "").strip()
    if not stripped:
        raise ValueError("Project name is empty.")
    cleaned = _BAD_CHARS.sub("_", stripped)
    # Windows forbids trailing spaces / dots on folder names.
    cleaned = cleaned.rstrip(" .")
    if not cleaned:
        raise ValueError("Project name has no usable characters.")
    if cleaned.upper() in _RESERVED_DOS:
        raise ValueError(f"{cleaned!r} is a reserved Windows name.")
    return cleaned


def sanitize_serial(serial: str) -> str:
    """Strip non-alphanumeric chars from a camera serial so it can't break the
    filename. Camera serials are normally pure digits (e.g. "2439007")."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", (serial or "").strip())
    if not cleaned:
        raise ValueError(f"Camera serial is empty/unprintable: {serial!r}")
    return cleaned


# ---------------------------------------------------------------------------
# Sweep frames (M0 / M1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrameName:
    """Everything needed to place one M0/M1 sweep frame on disk.

    The on-disk format is::

        Cam{SN}_{YYYYMMDD}_{project}_L{line}_S{sweep}_{frame}.bay14

    where ``sweep`` is 1-indexed within the current line and resets
    every time the line changes (Line up / Line down), and ``frame``
    is 1..13 within the current sweep.
    """

    drive_root: str  # e.g. "D:\\" -- must already be mounted
    project: str  # sanitised project name (becomes subfolder)
    camera_serial: str  # sanitised GenICam DeviceSerialNumber
    line: int  # user-managed flight-line counter
    sweep: int  # 1-indexed sweep number within this line
    frame: int  # 1..13 within the current sweep
    date: _dt.date  # date used for the YYYYMMDD field

    def directory(self) -> str:
        return os.path.join(self.drive_root, self.project)

    def basename(self) -> str:
        # ".bay14" is the raw 14-bit-packed Bayer format written by
        # raw_bay_writer.write_raw_bay14. Each sweep frame on disk
        # carries an embedded JSON header with full geometry +
        # metadata. See raw_bay_writer.py for the pack layout and
        # tools/bay14_to_tif.py for offline conversion.
        return (
            f"Cam{self.camera_serial}_"
            f"{self.date.strftime('%Y%m%d')}_"
            f"{self.project}_"
            f"L{self.line}_S{self.sweep}_{self.frame}.bay14"
        )

    def full_path(self) -> str:
        return os.path.join(self.directory(), self.basename())


def build_frame_name(
    *,
    drive_root: str,
    project_raw: str,
    camera_serial: str,
    line: int,
    sweep: int,
    frame: int,
    date: _dt.date | None = None,
) -> FrameName:
    if sweep < 1:
        raise ValueError(f"sweep must be >= 1, got {sweep!r}")
    if not 1 <= frame <= 13:
        raise ValueError(f"frame must be 1..13, got {frame!r}")
    project = sanitize_project_name(project_raw)
    serial = sanitize_serial(camera_serial)
    if date is None:
        date = _dt.date.today()
    return FrameName(
        drive_root=drive_root,
        project=project,
        camera_serial=serial,
        line=int(line),
        sweep=int(sweep),
        frame=int(frame),
        date=date,
    )


@dataclass(frozen=True)
class SweepName:
    """Everything needed to place one batched sweep file on disk.

    The on-disk format is::

        Cam{SN}_{YYYYMMDD}_{project}_L{line}_S{sweep}.bay14sweep

    where ``sweep`` is 1-indexed within the current line and resets
    every time the line changes. The file holds up to 13 frames
    (one full sweep) packed together with an embedded index --
    see ``raw_bay_writer.write_sweep_bay14_into`` for the layout.

    Same naming scheme as :class:`FrameName` minus the per-frame
    suffix, with a different extension. That keeps directory
    listings sorted naturally and makes mixed-format projects
    (legacy per-frame .bay14 + new .bay14sweep) easy to spot.
    """

    drive_root: str
    project: str
    camera_serial: str
    line: int
    sweep: int
    date: _dt.date

    def directory(self) -> str:
        return os.path.join(self.drive_root, self.project)

    def basename(self) -> str:
        return (
            f"Cam{self.camera_serial}_"
            f"{self.date.strftime('%Y%m%d')}_"
            f"{self.project}_"
            f"L{self.line}_S{self.sweep}.bay14sweep"
        )

    def full_path(self) -> str:
        return os.path.join(self.directory(), self.basename())


def build_sweep_name(
    *,
    drive_root: str,
    project_raw: str,
    camera_serial: str,
    line: int,
    sweep: int,
    date: _dt.date | None = None,
) -> SweepName:
    if sweep < 1:
        raise ValueError(f"sweep must be >= 1, got {sweep!r}")
    project = sanitize_project_name(project_raw)
    serial = sanitize_serial(camera_serial)
    if date is None:
        date = _dt.date.today()
    return SweepName(
        drive_root=drive_root,
        project=project,
        camera_serial=serial,
        line=int(line),
        sweep=int(sweep),
        date=date,
    )


# ---------------------------------------------------------------------------
# Per-line containers (.bay14line)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LineName:
    """Path for a per-line `.bay14line` container.

    On-disk format::

        Cam{SN}_{YYYYMMDD}_{project}_L{line}_{HHMMSS}.bay14line

    The ``HHMMSS`` suffix is the wall-clock time the line was opened.
    Including it means the operator can re-select a previously used
    line number (Line down then Line up) and still get a fresh,
    non-overwritten file -- the new container coexists with the old.

    See :mod:`raw_bay_writer` for the on-disk container layout
    (file header + chunks + index).
    """

    drive_root: str
    project: str
    camera_serial: str
    line: int
    when: _dt.datetime

    def directory(self) -> str:
        return os.path.join(self.drive_root, self.project)

    def basename(self, part_idx: Optional[int] = None) -> str:
        """Per-line container basename.

        When ``part_idx`` is None the legacy single-file name is
        returned (back-compat). When a non-None part index is passed
        we suffix ``_pN`` before the extension; this is how the
        per-camera "two io_writers, one .bay14line each" path lays
        the partial files down on disk so the two streams can fan
        out to a single drive without coordinating an offset.
        Reader tools can rejoin parts by sorting on the suffix.
        """
        suffix = "" if part_idx is None else f"_p{int(part_idx)}"
        return (
            f"Cam{self.camera_serial}_"
            f"{self.when.strftime('%Y%m%d')}_"
            f"{self.project}_"
            f"L{self.line}_"
            f"{self.when.strftime('%H%M%S')}{suffix}.bay14line"
        )

    def full_path(self, part_idx: Optional[int] = None) -> str:
        return os.path.join(self.directory(), self.basename(part_idx=part_idx))


def build_line_name(
    *,
    drive_root: str,
    project_raw: str,
    camera_serial: str,
    line: int,
    when: _dt.datetime | None = None,
) -> LineName:
    """Build a :class:`LineName` for a freshly-opened line container."""
    project = sanitize_project_name(project_raw)
    serial = sanitize_serial(camera_serial)
    if when is None:
        when = _dt.datetime.now()
    return LineName(
        drive_root=drive_root,
        project=project,
        camera_serial=serial,
        line=int(line),
        when=when,
    )


# ---------------------------------------------------------------------------
# Focus-view captures (M4, optional)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FocusFrameName:
    """Everything needed to place one M4 focus-view crop on disk."""

    drive_root: str
    project: str
    camera_serial: str
    when: _dt.datetime  # full timestamp; sub-second precision used in filename
    tag: str = ""
    """Optional PREFIX, e.g. ``OID007_y1408_s43254``. Several patches can be saved from
    ONE frame, so the timestamp alone no longer identifies a file; and putting the object
    id and score in the name is the point -- it turns "is every crop of object 7 really
    the same object, and is a 43000-scoring patch actually sharp?" into sorting a
    directory by filename."""

    def directory(self) -> str:
        return os.path.join(self.drive_root, self.project, FOCUS_SUBDIR)

    def basename(self) -> str:
        ms = f"{self.when.microsecond // 1000:03d}"
        # Tag FIRST. Trailing it (the original) sorted the folder by time, interleaving
        # every tracked object, so following one object through its life meant hand-picking
        # its files out of the listing. Leading with it groups each object's whole life
        # together, which is what the folder is opened to check.
        tag = f"{sanitize_tag(self.tag)}_" if self.tag else ""
        return (
            f"{tag}"
            f"Cam{self.camera_serial}_"
            f"{self.when.strftime('%Y%m%d')}_"
            f"{self.project}_focus_"
            f"{self.when.strftime('%H%M%S')}_{ms}.tif"
        )

    def full_path(self) -> str:
        return os.path.join(self.directory(), self.basename())


def sanitize_tag(tag: str) -> str:
    """Keep a filename tag to characters Windows will accept, and keep it short."""
    keep = [c for c in str(tag) if c.isalnum() or c in "-_"]
    return "".join(keep)[:48]


def build_focus_frame_name(
    *,
    drive_root: str,
    project_raw: str,
    camera_serial: str,
    when: _dt.datetime | None = None,
    tag: str = "",
) -> FocusFrameName:
    project = sanitize_project_name(project_raw)
    serial = sanitize_serial(camera_serial)
    if when is None:
        when = _dt.datetime.now()
    return FocusFrameName(
        drive_root=drive_root,
        project=project,
        camera_serial=serial,
        when=when,
        tag=tag,
    )


# ---------------------------------------------------------------------------


def ensure_directory(
    fname: FrameName | FocusFrameName | SweepName | LineName,
) -> str:
    """Create the file's parent directory if missing; return that directory."""
    path = fname.directory()
    os.makedirs(path, exist_ok=True)
    return path
