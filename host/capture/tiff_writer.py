"""Write one Bayer / mono frame as a classic-TIFF file.

Two flavours, both classic (non-BigTIFF) uint16 mono:

* Full-frame: 13400 x 9528 (~244 MB), one per trigger pulse in M0/M1.
* Focus crop: a small ROI around the best tile, written by the M4 focus
  recorder.

The 14-bit pixel values live in the low bits of the uint16 sample;
downstream tools get the Bayer pattern + bit depth from the
``ImageDescription`` JSON we embed.

GPS metadata, when available, is recorded two ways:

1. **Always** as a structured ``gps`` block inside the ``ImageDescription``
   JSON. This is universally readable, lossless, and is what downstream
   processing should consume.
2. **Authoritative EXIF GPS sub-IFD** via :func:`exiftool_geotag`. tifffile
   cannot emit a real GPS sub-IFD (its ``extratags`` parameter pins tags
   into IFD0, where tag 1=InteropIndex / 2=InteropVersion / ... collide
   with the GPS namespace and confuse standard readers like Lightroom,
   Capture One, or ``exiftool -GPSLatitude``). Post-processing with the
   external ``exiftool`` binary is the only portable way to materialise
   the canonical sub-IFD layout, so we offload that step to it.

This writer is synchronous; drive the throughput from a background thread.
"""

from __future__ import annotations

import csv
import datetime as _dt
import json
import logging
import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import tifffile

from .gps_fix import GpsFix

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FrameMetadata:
    """User-level metadata bundled into the TIFF ``ImageDescription`` JSON."""

    camera_serial: str
    project: str
    line: int
    frame_in_sweep: int
    bit_depth: int = 14
    bayer_pattern: str = "RG"
    acquire_timestamp: str = ""  # ISO-8601 with ms; filled in by caller
    capture_kind: str = "sweep"  # "sweep" (M0/M1) or "focus" (M4)
    # For a "focus" crop this is the sweep of the FULL frame cut from the same
    # trigger, which is how a crop is matched to its full-res image. 0 when no
    # full frame was written.
    sweep: int = 0


def write_frame(
    path: str,
    frame_u16: np.ndarray,
    meta: FrameMetadata,
    gps: GpsFix | None = None,
) -> None:
    """Write *frame_u16* (H x W, uint16) as classic TIFF to *path*.

    Raises on invalid input rather than silently writing garbage. The parent
    directory must already exist (use ``file_naming.ensure_directory``).
    """
    if frame_u16.dtype != np.uint16:
        raise TypeError(f"frame must be uint16, got {frame_u16.dtype}")
    if frame_u16.ndim != 2:
        raise ValueError(f"frame must be 2-D, got shape {frame_u16.shape}")

    payload: dict[str, Any] = {
        "camera_serial": meta.camera_serial,
        "project": meta.project,
        "line": meta.line,
        "sweep": meta.sweep,
        "frame_in_sweep": meta.frame_in_sweep,
        "bit_depth": meta.bit_depth,
        "bayer_pattern": meta.bayer_pattern,
        "capture_kind": meta.capture_kind,
        "acquire_timestamp": (
            meta.acquire_timestamp
            or _dt.datetime.now().astimezone().isoformat(timespec="milliseconds")
        ),
    }
    if gps is not None:
        payload["gps"] = gps.to_dict()

    description = json.dumps(payload, separators=(",", ":"))

    extratags = _build_gps_extratags(gps) if gps is not None and gps.has_position else []

    tifffile.imwrite(
        path,
        frame_u16,
        photometric="minisblack",
        compression=None,
        bigtiff=False,
        description=description,
        extratags=extratags,
    )


def estimate_bytes_per_frame(width: int, height: int) -> int:
    """Approximate on-disk size of one uint16 uncompressed TIFF."""
    return width * height * 2 + 4096  # +4 KB for the TIFF header / IFD


# ---------------------------------------------------------------------------
# EXIF GPS tags
# ---------------------------------------------------------------------------

# Tag IDs of the EXIF GPS IFD. Writing them at IFD0 is a bit of a stretch of
# the spec (they really belong in a GPS sub-IFD pointed to by tag 34853), but
# tifffile's ``extratags`` parameter does not let us emit a real sub-IFD, so
# we pin them in IFD0. ``exiftool`` and most metadata browsers accept this
# layout for read-only inspection. The canonical, structured copy of the
# fix is the JSON in ImageDescription -- this is the best-effort EXIF view.
_GPS_VERSION_ID = 0  # BYTE x 4
_GPS_LATITUDE_REF = 1  # ASCII (N/S)
_GPS_LATITUDE = 2  # RATIONAL x 3 (deg, min, sec)
_GPS_LONGITUDE_REF = 3  # ASCII (E/W)
_GPS_LONGITUDE = 4  # RATIONAL x 3
_GPS_ALTITUDE_REF = 5  # BYTE (0 = above sea level, 1 = below)
_GPS_ALTITUDE = 6  # RATIONAL
_GPS_TIME_STAMP = 7  # RATIONAL x 3 (h, m, s) UTC
_GPS_SATELLITES = 8  # ASCII
_GPS_DOP = 11  # RATIONAL
_GPS_DATE_STAMP = 29  # ASCII "YYYY:MM:DD"

# tifffile dtype codes (matches TIFF spec):
#   1 = BYTE, 2 = ASCII, 5 = RATIONAL


def _deg_to_dms_flat(deg: float) -> list[int]:
    """Convert decimal degrees to a flat ``[deg_num, deg_den, min_num,
    min_den, sec_num, sec_den]`` list, as tifffile expects for a count-3
    RATIONAL tag (it packs each rational as two consecutive uint32s)."""
    abs_deg = abs(deg)
    d = int(abs_deg)
    m_full = (abs_deg - d) * 60
    m = int(m_full)
    s = (m_full - m) * 60
    # 4-decimal-place precision on seconds (~3 mm at the equator); good enough
    # for image geo-tagging.
    s_num = int(round(s * 10_000))
    return [d, 1, m, 1, s_num, 10_000]


def _build_gps_extratags(gps: GpsFix) -> list[tuple]:
    """Build a tifffile ``extratags`` list with EXIF GPS tag IDs from a fix.

    tifffile packs each ``RATIONAL`` (TIFF dtype 5) as ``LL`` (two uint32s),
    so a count-N rational tag wants a flat 2*N-int sequence as its value.
    """
    tags: list[tuple] = []

    # GPSVersionID = 2.3.0.0
    tags.append((_GPS_VERSION_ID, 1, 4, b"\x02\x03\x00\x00", True))

    if gps.latitude_deg is not None:
        lat_ref = "N" if gps.latitude_deg >= 0 else "S"
        tags.append((_GPS_LATITUDE_REF, 2, 2, lat_ref, True))
        tags.append(
            (_GPS_LATITUDE, 5, 3, _deg_to_dms_flat(gps.latitude_deg), True)
        )
    if gps.longitude_deg is not None:
        lon_ref = "E" if gps.longitude_deg >= 0 else "W"
        tags.append((_GPS_LONGITUDE_REF, 2, 2, lon_ref, True))
        tags.append(
            (_GPS_LONGITUDE, 5, 3, _deg_to_dms_flat(gps.longitude_deg), True)
        )
    if gps.altitude_m is not None and not math.isnan(gps.altitude_m):
        ref = 0 if gps.altitude_m >= 0 else 1
        tags.append((_GPS_ALTITUDE_REF, 1, 1, bytes([ref]), True))
        alt_num = int(round(abs(gps.altitude_m) * 1000))
        tags.append((_GPS_ALTITUDE, 5, 1, [alt_num, 1000], True))
    if gps.timestamp_utc is not None:
        ts = gps.timestamp_utc
        sec_num = ts.second * 1000 + ts.microsecond // 1000
        tags.append(
            (
                _GPS_TIME_STAMP, 5, 3,
                [ts.hour, 1, ts.minute, 1, sec_num, 1000],
                True,
            )
        )
        tags.append((_GPS_DATE_STAMP, 2, 11, ts.strftime("%Y:%m:%d"), True))
    if gps.satellites is not None:
        s = str(gps.satellites)
        tags.append((_GPS_SATELLITES, 2, len(s) + 1, s, True))
    if gps.hdop is not None and not math.isnan(gps.hdop):
        hdop_num = int(round(gps.hdop * 100))
        tags.append((_GPS_DOP, 5, 1, [hdop_num, 100], True))

    return tags


# ---------------------------------------------------------------------------
# Embed a real EXIF GPS sub-IFD at write time (no full-file rewrite)
# ---------------------------------------------------------------------------
#
# The canonical EXIF GPS layout is a dedicated sub-IFD pointed to by IFD0 tag
# 34853 (GPSIFD). tifffile cannot emit a sub-IFD, and post-processing with
# ExifTool rewrites the whole multi-hundred-MB TIFF (slow, especially over a
# network share). Instead we:
#
#   1. Reserve the 34853 pointer (a LONG, placeholder 0) in IFD0 at write
#      time via :func:`reserved_gpsifd_extratag` so tifffile lays it down in
#      the correct sorted position -- no later IFD insertion / strip shift.
#   2. After the file is written, append a small GPS sub-IFD block at EOF and
#      overwrite the 4-byte pointer to point at it
#      (:func:`embed_exif_gps_subifd`).
#
# Only a few hundred bytes are appended and 4 bytes patched; the pixel strip
# is never moved, so this is effectively free even on SMB.

GPSIFD_TAG = 34853  # IFD0 -> GPS sub-IFD pointer

# TIFF field type codes.
_TT_BYTE = 1
_TT_ASCII = 2
_TT_LONG = 4
_TT_RATIONAL = 5


def _gps_subifd_entries(gps: GpsFix) -> list[tuple[int, int, int, object]]:
    """GPS sub-IFD entries as ``(tag, type, count, value)``, sorted by tag.

    *value* is ``bytes`` for BYTE/ASCII or a flat ``[num, den, ...]`` int list
    for RATIONAL. Mirrors :func:`_build_gps_extratags` exactly, just destined
    for a real sub-IFD instead of IFD0.
    """
    entries: list[tuple[int, int, int, object]] = []
    entries.append((_GPS_VERSION_ID, _TT_BYTE, 4, bytes([2, 3, 0, 0])))

    if gps.latitude_deg is not None:
        lat_ref = b"N" if gps.latitude_deg >= 0 else b"S"
        entries.append((_GPS_LATITUDE_REF, _TT_ASCII, 2, lat_ref + b"\x00"))
        entries.append((_GPS_LATITUDE, _TT_RATIONAL, 3,
                        _deg_to_dms_flat(gps.latitude_deg)))
    if gps.longitude_deg is not None:
        lon_ref = b"E" if gps.longitude_deg >= 0 else b"W"
        entries.append((_GPS_LONGITUDE_REF, _TT_ASCII, 2, lon_ref + b"\x00"))
        entries.append((_GPS_LONGITUDE, _TT_RATIONAL, 3,
                        _deg_to_dms_flat(gps.longitude_deg)))
    if gps.altitude_m is not None and not math.isnan(gps.altitude_m):
        ref = 0 if gps.altitude_m >= 0 else 1
        entries.append((_GPS_ALTITUDE_REF, _TT_BYTE, 1, bytes([ref])))
        alt_num = int(round(abs(gps.altitude_m) * 1000))
        entries.append((_GPS_ALTITUDE, _TT_RATIONAL, 1, [alt_num, 1000]))
    if gps.timestamp_utc is not None:
        ts = gps.timestamp_utc
        if ts.tzinfo is not None:
            ts = ts.astimezone(timezone.utc)
        sec_num = ts.second * 1000 + ts.microsecond // 1000
        entries.append((_GPS_TIME_STAMP, _TT_RATIONAL, 3,
                        [ts.hour, 1, ts.minute, 1, sec_num, 1000]))
        date = ts.strftime("%Y:%m:%d").encode("ascii")
        entries.append((_GPS_DATE_STAMP, _TT_ASCII, len(date) + 1,
                        date + b"\x00"))
    if gps.satellites is not None:
        s = str(gps.satellites).encode("ascii")
        entries.append((_GPS_SATELLITES, _TT_ASCII, len(s) + 1, s + b"\x00"))
    if gps.hdop is not None and not math.isnan(gps.hdop):
        hdop_num = int(round(gps.hdop * 100))
        entries.append((_GPS_DOP, _TT_RATIONAL, 1, [hdop_num, 100]))

    entries.sort(key=lambda e: e[0])
    return entries


def _build_gps_subifd_block(gps: GpsFix, base_offset: int, byteorder: str) -> bytes:
    """Serialise a GPS sub-IFD that will live at *base_offset* in the file.

    Layout: ``count(2) | entries(N*12) | next_ifd(4)=0 | value pool``.
    Pooled values (RATIONALs, and ASCII/BYTE longer than 4 bytes) are
    referenced by absolute file offset; *base_offset* must be even so every
    pooled value lands on a word boundary (TIFF requirement).
    """
    bo = byteorder
    entries = _gps_subifd_entries(gps)
    n = len(entries)
    ifd_size = 2 + n * 12 + 4
    pool_base = base_offset + ifd_size

    out = bytearray()
    out += struct.pack(bo + "H", n)
    pool = bytearray()
    for tag, typ, count, value in entries:
        if typ == _TT_RATIONAL:
            data = b"".join(
                struct.pack(bo + "II", value[i], value[i + 1])
                for i in range(0, len(value), 2)
            )
            off = pool_base + len(pool)
            pool += data
            if len(pool) % 2:
                pool += b"\x00"
            val_field = struct.pack(bo + "I", off)
        elif typ in (_TT_BYTE, _TT_ASCII):
            data = value  # type: ignore[assignment]
            if len(data) <= 4:
                val_field = data + b"\x00" * (4 - len(data))
            else:
                off = pool_base + len(pool)
                pool += data
                if len(pool) % 2:
                    pool += b"\x00"
                val_field = struct.pack(bo + "I", off)
        else:  # pragma: no cover - defensive
            raise ValueError(f"unsupported GPS field type {typ}")
        out += struct.pack(bo + "HHI", tag, typ, count)
        out += val_field
    out += struct.pack(bo + "I", 0)  # next IFD = none
    return bytes(out) + bytes(pool)


def embed_exif_gps_subifd(path: Path, gps: GpsFix, *, fsync: bool = False) -> bool:
    """Embed a canonical EXIF GPS sub-IFD into a classic TIFF *in place*.

    tifffile refuses to write IFD-pointer tags (e.g. 34853) through its
    ``extratags`` API, so we add the pointer ourselves. The image strip is
    never moved: we relocate **only IFD0** to EOF with one extra entry, the
    GPS pointer (34853), then append the GPS sub-IFD after it and repoint the
    TIFF header's first-IFD offset. Existing IFD0 entries are copied
    byte-for-byte, so any values they reference by offset (ImageDescription,
    StripOffsets, BitsPerSample, ...) stay valid where they are.

    The result is the layout every standard reader understands
    (Lightroom / Capture One / ``exiftool -GPSLatitude``), produced by
    appending only a few hundred bytes -- fast even over a network share.

    If a GPSIFD (34853) pointer already exists in IFD0 (e.g. a re-embed),
    its value is patched in place instead of relocating IFD0.

    Returns ``True`` when a sub-IFD was written, ``False`` when *gps* has no
    position. Raises ``ValueError`` for non-classic-TIFF input.
    """
    if gps is None or not gps.has_position:
        return False
    path = Path(path)
    with open(path, "r+b") as fh:
        header = fh.read(8)
        if len(header) < 8:
            raise ValueError(f"{path}: too short for a TIFF header")
        if header[:2] == b"II":
            bo = "<"
        elif header[:2] == b"MM":
            bo = ">"
        else:
            raise ValueError(f"{path}: not a TIFF (bad byte-order mark)")
        magic = struct.unpack(bo + "H", header[2:4])[0]
        if magic != 42:
            raise ValueError(
                f"{path}: not a classic TIFF (magic={magic}); GPS embed "
                f"supports classic TIFF only",
            )
        ifd0_off = struct.unpack(bo + "I", header[4:8])[0]

        fh.seek(ifd0_off)
        n = struct.unpack(bo + "H", fh.read(2))[0]
        entries_blob = fh.read(n * 12)
        if len(entries_blob) < n * 12:
            raise ValueError(f"{path}: truncated IFD0")
        next_ifd = struct.unpack(bo + "I", fh.read(4))[0]

        existing_ptr_pos: Optional[int] = None
        for i in range(n):
            tag = struct.unpack_from(bo + "H", entries_blob, i * 12)[0]
            if tag == GPSIFD_TAG:
                existing_ptr_pos = ifd0_off + 2 + i * 12 + 8
                break

        def _aligned_eof() -> int:
            fh.seek(0, os.SEEK_END)
            pos = fh.tell()
            if pos % 2 == 1:
                fh.write(b"\x00")
                pos += 1
            return pos

        if existing_ptr_pos is not None:
            # Re-embed: keep IFD0 where it is, just append a fresh GPS block
            # and repoint the existing 34853 value.
            gps_off = _aligned_eof()
            fh.seek(gps_off)
            fh.write(_build_gps_subifd_block(gps, gps_off, bo))
            fh.seek(existing_ptr_pos)
            fh.write(struct.pack(bo + "I", gps_off))
        else:
            # Relocate IFD0 (n+1 entries) to EOF, append GPS sub-IFD after it,
            # repoint the header. Image strip untouched.
            entries: list[tuple[int, bytes]] = []
            for i in range(n):
                e = entries_blob[i * 12 : i * 12 + 12]
                tag = struct.unpack_from(bo + "H", e, 0)[0]
                entries.append((tag, e))

            new_ifd0_off = _aligned_eof()
            new_ifd0_size = 2 + (n + 1) * 12 + 4
            gps_off = new_ifd0_off + new_ifd0_size

            gps_entry = (
                GPSIFD_TAG,
                struct.pack(bo + "HHI", GPSIFD_TAG, _TT_LONG, 1)
                + struct.pack(bo + "I", gps_off),
            )
            entries.append(gps_entry)
            entries.sort(key=lambda t: t[0])

            new_ifd0 = bytearray()
            new_ifd0 += struct.pack(bo + "H", n + 1)
            for _, e in entries:
                new_ifd0 += e
            new_ifd0 += struct.pack(bo + "I", next_ifd)

            fh.seek(new_ifd0_off)
            fh.write(bytes(new_ifd0))
            fh.write(_build_gps_subifd_block(gps, gps_off, bo))

            fh.seek(4)
            fh.write(struct.pack(bo + "I", new_ifd0_off))

        fh.flush()
        if fsync:
            os.fsync(fh.fileno())
    return True


# ---------------------------------------------------------------------------
# ExifTool integration
# ---------------------------------------------------------------------------
#
# The tifffile-side ``_build_gps_extratags`` pins GPS tags into IFD0 because
# tifffile cannot emit a sub-IFD. Most EXIF consumers (Lightroom, Capture One,
# exiftool itself) only look for GPS in the dedicated GPSInfo sub-IFD pointed
# to by IFD0 tag 34853, so those IFD0 entries are read with the wrong taxonomy
# (tag 0x0001 -> "InteropIndex", 0x0002 -> "InteropVersion", ...) and never
# surface as lat/lon.
#
# The fix is to post-process each written TIFF with the standalone ``exiftool``
# binary: it patches the GPSInfo sub-IFD in-place (touching only the IFD bytes,
# not the multi-hundred-MB pixel strip), so the per-file cost stays well under
# one second on local NTFS. The helpers below detect the executable, build a
# single argv per file, and migrate the ``gps`` block from our own
# ``ImageDescription`` JSON into proper EXIF tags for a whole folder.


DEFAULT_EXIFTOOL_PATHS: tuple[str, ...] = (
    r"C:\ExifTool\exiftool-13.58_64\exiftool.exe",
)
"""Filesystem locations to check when ``exiftool`` is not on ``PATH``.

The project ships the standalone Windows build under ``C:\\ExifTool\\...``;
detection probes that first, then anything callers add via *explicit*."""


def detect_exiftool(explicit: Optional[Path] = None) -> Optional[Path]:
    """Return a path to an ``exiftool`` executable, or ``None``.

    Search order:

    1. *explicit*, if provided and the file exists.
    2. The first ``exiftool`` on ``PATH``.
    3. Each entry of :data:`DEFAULT_EXIFTOOL_PATHS`.
    """
    if explicit is not None:
        p = Path(explicit)
        return p if p.is_file() else None
    found = shutil.which("exiftool")
    if found:
        return Path(found)
    for candidate in DEFAULT_EXIFTOOL_PATHS:
        p = Path(candidate)
        if p.is_file():
            return p
    return None


def _fmt_lat_lon_for_exiftool(value: float) -> str:
    """ExifTool accepts a signed decimal degree; sign is interpreted with the
    accompanying ``-GPSLatitudeRef`` / ``-GPSLongitudeRef`` argument."""
    return f"{value:.9f}"


def _exiftool_geotag_argv(
    path: Path,
    fix: GpsFix,
    *,
    exiftool_exe: Path,
    overwrite_original: bool = True,
) -> list[str]:
    """Build the argv used by :func:`exiftool_geotag` (split out for tests)."""
    if fix.latitude_deg is None or fix.longitude_deg is None:
        raise ValueError(f"refusing to write empty GPS for {path}")

    lat_ref = "N" if fix.latitude_deg >= 0 else "S"
    lon_ref = "E" if fix.longitude_deg >= 0 else "W"
    args: list[str] = [
        str(exiftool_exe),
        "-charset", "filename=UTF8",
        "-q", "-q",  # double-quiet
    ]
    if overwrite_original:
        args.append("-overwrite_original")
    # GPSVersionID = 2.3.0.0 matches the live-capture path.
    args.append("-EXIF:GPSVersionID=2 3 0 0")
    args.append(f"-EXIF:GPSLatitude={_fmt_lat_lon_for_exiftool(abs(fix.latitude_deg))}")
    args.append(f"-EXIF:GPSLatitudeRef={lat_ref}")
    args.append(f"-EXIF:GPSLongitude={_fmt_lat_lon_for_exiftool(abs(fix.longitude_deg))}")
    args.append(f"-EXIF:GPSLongitudeRef={lon_ref}")
    if fix.altitude_m is not None and not math.isnan(fix.altitude_m):
        args.append(f"-EXIF:GPSAltitude={fix.altitude_m:.3f}")
        args.append(f"-EXIF:GPSAltitudeRef={0 if fix.altitude_m >= 0 else 1}")
    if fix.timestamp_utc is not None:
        ts = fix.timestamp_utc
        if ts.tzinfo is not None:
            ts = ts.astimezone(timezone.utc)
        args.append(f"-EXIF:GPSDateStamp={ts.strftime('%Y:%m:%d')}")
        args.append(
            "-EXIF:GPSTimeStamp="
            f"{ts.hour:02d}:{ts.minute:02d}:{ts.second:02d}"
            f".{ts.microsecond // 1000:03d}"
        )
    if fix.satellites is not None:
        args.append(f"-EXIF:GPSSatellites={fix.satellites}")
    if fix.hdop is not None and not math.isnan(fix.hdop):
        args.append(f"-EXIF:GPSDOP={fix.hdop:.3f}")
    args.append(str(path))
    return args


def exiftool_geotag(
    path: Path,
    fix: GpsFix,
    *,
    exiftool_exe: Path,
    overwrite_original: bool = True,
    timeout_s: float = 60.0,
) -> None:
    """Write proper EXIF GPS tags into *path* via ExifTool, in-place.

    ExifTool patches just the metadata bytes (no pixel rewrite), so this
    finishes in <1 s for a 720 MB TIFF on local NTFS. The tool handles
    building the correct EXIF GPS sub-IFD (tag 34853 -> sub-IFD with
    GPSLatitude, GPSLongitude, GPSAltitude, GPSDateStamp, GPSTimeStamp,
    GPSVersionID, ...) which is the layout EXIF readers expect.

    Raises ``subprocess.CalledProcessError`` if ExifTool exits non-zero.
    """
    args = _exiftool_geotag_argv(
        path,
        fix,
        exiftool_exe=exiftool_exe,
        overwrite_original=overwrite_original,
    )
    completed = subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode,
            args,
            output=completed.stdout,
            stderr=completed.stderr,
        )


# Pix4D's camera-orientation namespace. ExifTool can READ it but refuses to
# WRITE it without a ``.ExifTool_config`` defining the namespace, which is why
# every ``-XMP-Camera:Yaw=`` assignment came back "doesn't exist or isn't
# writable" and silently wrote nothing. Emitting the packet ourselves declares
# the xmlns inline, so there is no registry to satisfy -- and it rides along in
# ``extratags`` instead of forcing ExifTool to rewrite the whole TIFF.
XMP_CAMERA_NS = "http://pix4d.com/camera/1.0/"
TIFFTAG_XMP = 700


def xmp_attitude_packet(attitude: dict[str, Any]) -> Optional[bytes]:
    """Return an XMP packet carrying ``Camera:Yaw/Pitch/Roll``, or ``None``.

    ``None`` when the block holds no usable angle, so callers write no tag at
    all rather than an empty packet.
    """
    fields: list[str] = []
    for key, name in (("yaw_deg", "Yaw"), ("pitch_deg", "Pitch"), ("roll_deg", "Roll")):
        value = attitude.get(key)
        if value is None:
            continue
        try:
            fields.append(f'Camera:{name}="{float(value):.6f}"')
        except (TypeError, ValueError):
            continue
    if not fields:
        return None
    return (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" '
        f'xmlns:Camera="{XMP_CAMERA_NS}" '
        f'{" ".join(fields)}/>'
        "</rdf:RDF></x:xmpmeta>"
        '<?xpacket end="w"?>'
    ).encode("utf-8")


def xmp_extratag(packet: bytes) -> tuple:
    """tifffile ``extratags`` entry for TIFF tag 700 (XMLPacket).

    Unlike the GPS pointer (34853), 700 is a plain byte array, so tifffile
    writes it directly -- no post-write IFD surgery needed.
    """
    return (TIFFTAG_XMP, 1, len(packet), packet, True)


_JPEG_SOI = b"\xff\xd8"
_JPEG_APP1 = b"\xff\xe1"
_JPEG_XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"


def insert_jpeg_xmp_packet(path: Path, packet: bytes) -> bool:
    """Insert *packet* into a JPEG as an APP1 XMP segment, in place.

    JPEG has no tag 700; XMP rides in its own APP1 segment. Returns ``False``
    if the file is not a JPEG.
    """
    data = path.read_bytes()
    if not data.startswith(_JPEG_SOI):
        log.warning("not a JPEG, skipping XMP insert: %s", path)
        return False
    body = _JPEG_XMP_HEADER + packet
    seg_len = len(body) + 2  # the length field counts itself
    if seg_len > 0xFFFF:
        raise ValueError(f"XMP packet too large for one APP1 segment: {seg_len}")
    segment = _JPEG_APP1 + struct.pack(">H", seg_len) + body
    path.write_bytes(data[:2] + segment + data[2:])
    return True


FOCUS_EXIF_KEYS: tuple[str, ...] = (
    "encoder_count",
    "encoder_epoch",
    "encoder_camera",
)
"""``focus`` keys copied into ``EXIF:UserComment``.

``encoder_epoch`` rides along with the count because counts are only
comparable within one epoch -- differencing two frames from different
epochs is meaningless, and the consumer cannot tell without it.
"""


def _exiftool_frame_tags_argv(
    path: Path,
    *,
    attitude: Optional[dict[str, Any]] = None,
    focus: Optional[dict[str, Any]] = None,
    exiftool_exe: Path,
    overwrite_original: bool = True,
) -> list[str]:
    """Build one argv for the Pix4D attitude XMP tags and/or the lens encoder.

    Both groups go in a *single* invocation on purpose: ExifTool rewrites a
    TIFF wholesale, and these frames run to hundreds of MB, so a second pass
    would double the export cost to write a handful of bytes.

    Only ONE ``-q`` (not the usual double-quiet): ExifTool exits 0 when some
    assignments succeed and others are rejected, so double-quiet would drop a
    silently-unwritten tag on the floor. :func:`_rejected_tag_warnings` reads
    the surviving warnings back out.
    """
    args: list[str] = [
        str(exiftool_exe),
        "-charset", "filename=UTF8",
        "-q",
    ]
    if overwrite_original:
        args.append("-overwrite_original")

    def _f(block: dict[str, Any], key: str) -> Optional[float]:
        v = block.get(key)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    wrote_any = False

    if attitude is not None:
        yaw = _f(attitude, "yaw_deg")
        pitch = _f(attitude, "pitch_deg")
        roll = _f(attitude, "roll_deg")
        if yaw is not None:
            args.append(f"-XMP-Camera:Yaw={yaw:.6f}")
        if pitch is not None:
            args.append(f"-XMP-Camera:Pitch={pitch:.6f}")
        if roll is not None:
            args.append(f"-XMP-Camera:Roll={roll:.6f}")
        if yaw is None and pitch is None and roll is None:
            raise ValueError(f"attitude block has no Yaw/Pitch/Roll for {path}")
        wrote_any = True

    if focus is not None:
        # No standard EXIF tag carries an absolute lens-encoder count, so the
        # reading goes into UserComment as compact JSON.
        payload = {k: focus[k] for k in FOCUS_EXIF_KEYS if focus.get(k) is not None}
        if "encoder_count" not in payload:
            raise ValueError(f"focus block has no encoder_count for {path}")
        args.append(
            "-EXIF:UserComment=" + json.dumps(payload, separators=(",", ":"))
        )
        wrote_any = True

    if not wrote_any:
        raise ValueError(f"no attitude or focus block to write for {path}")
    args.append(str(path))
    return args


def _exiftool_camera_attitude_argv(
    path: Path,
    attitude: dict[str, Any],
    *,
    exiftool_exe: Path,
    overwrite_original: bool = True,
) -> list[str]:
    """Build argv for Pix4D ``Camera:Yaw/Pitch/Roll`` XMP tags."""
    return _exiftool_frame_tags_argv(
        path,
        attitude=attitude,
        exiftool_exe=exiftool_exe,
        overwrite_original=overwrite_original,
    )


def _rejected_tag_warnings(stderr: str) -> list[str]:
    """Return ExifTool warnings that mean a tag was silently NOT written.

    ExifTool still exits 0 when one assignment lands and another is refused,
    so the exit code alone cannot tell us the metadata went missing. The
    benign "Duplicate ImageDescription tag in IFD0" warning (we write that
    tag via tifffile, then ExifTool sees it again) is deliberately excluded --
    it fires on every frame and would drown the real signal.
    """
    return [
        line.strip()
        for line in (stderr or "").splitlines()
        if "isn't writable" in line or "doesn't exist" in line
    ]


def exiftool_write_frame_tags(
    path: Path,
    *,
    attitude: Optional[dict[str, Any]] = None,
    focus: Optional[dict[str, Any]] = None,
    exiftool_exe: Optional[Path] = None,
    overwrite_original: bool = True,
    timeout_s: float = 60.0,
) -> bool:
    """Write XMP attitude and/or the lens encoder reading in ONE ExifTool pass.

    Returns False if ExifTool is unavailable, there was nothing to write, or
    the write failed.
    """
    if attitude is None and focus is None:
        return False
    exe = exiftool_exe or detect_exiftool()
    if exe is None:
        log.debug("exiftool not found; skipping frame tags for %s", path)
        return False
    args = _exiftool_frame_tags_argv(
        path,
        attitude=attitude,
        focus=focus,
        exiftool_exe=exe,
        overwrite_original=overwrite_original,
    )
    completed = subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    rejected = _rejected_tag_warnings(completed.stderr)
    if rejected:
        log.warning(
            "ExifTool refused %d tag(s) for %s: %s",
            len(rejected),
            path,
            "; ".join(rejected),
        )
    if completed.returncode != 0:
        log.warning(
            "ExifTool frame tags failed for %s (exit %s): %s",
            path,
            completed.returncode,
            (completed.stderr or "").strip(),
        )
        return False
    return not rejected


def exiftool_write_camera_attitude(
    path: Path,
    attitude: dict[str, Any],
    *,
    exiftool_exe: Optional[Path] = None,
    overwrite_original: bool = True,
    timeout_s: float = 60.0,
) -> bool:
    """Write XMP ``Camera:Yaw/Pitch/Roll`` via ExifTool. Returns False if unavailable."""
    return exiftool_write_frame_tags(
        path,
        attitude=attitude,
        exiftool_exe=exiftool_exe,
        overwrite_original=overwrite_original,
        timeout_s=timeout_s,
    )


def attitude_from_payload(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Return the ``attitude`` block from ImageDescription / chunk JSON."""
    blob = payload.get("attitude")
    return blob if isinstance(blob, dict) else None


def focus_from_payload(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Return the ``focus`` block from ImageDescription / chunk JSON."""
    blob = payload.get("focus")
    return blob if isinstance(blob, dict) else None


# ---------------------------------------------------------------------------
# Migrate gps block from ImageDescription JSON -> EXIF GPS sub-IFD
# ---------------------------------------------------------------------------


def _read_image_description_json(path: Path) -> Optional[dict[str, Any]]:
    """Return the parsed ``ImageDescription`` JSON, or ``None`` if absent.

    Uses tifffile to crack IFD0 (no pixel decode) so we touch as little of
    the multi-hundred-MB strip as possible.
    """
    try:
        with tifffile.TiffFile(str(path)) as tf:
            desc = tf.pages[0].tags.get("ImageDescription")
            if desc is None:
                return None
            text = desc.value
    except (OSError, ValueError) as exc:
        log.warning("could not read ImageDescription from %s: %s", path, exc)
        return None
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not text.startswith("{"):
        return None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _gps_fix_from_image_description(payload: dict[str, Any]) -> Optional[GpsFix]:
    """Reconstruct a :class:`GpsFix` from a parsed ``ImageDescription`` JSON.

    Schema mirrors :meth:`GpsFix.to_dict` (used by both the capture writer and
    the GPS Correlator's ``.bay14line`` patcher), so we accept either source.
    """
    blob = payload.get("gps")
    if not isinstance(blob, dict):
        return None

    ts_raw = blob.get("timestamp_utc")
    ts_utc: Optional[_dt.datetime]
    if isinstance(ts_raw, str):
        try:
            ts_utc = _dt.datetime.fromisoformat(ts_raw)
        except ValueError:
            ts_utc = None
    elif isinstance(ts_raw, _dt.datetime):
        ts_utc = ts_raw
    else:
        ts_utc = None

    def _f(name: str) -> Optional[float]:
        v = blob.get(name)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _i(name: str) -> Optional[int]:
        v = blob.get(name)
        if v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    fix = GpsFix(
        latitude_deg=_f("latitude_deg"),
        longitude_deg=_f("longitude_deg"),
        altitude_m=_f("altitude_m"),
        timestamp_utc=ts_utc,
        satellites=_i("satellites"),
        hdop=_f("hdop"),
        fix_quality=_i("fix_quality"),
    )
    if not fix.has_position:
        return None
    return fix


@dataclass
class ExifGpsMigrationStats:
    """Counts produced by :func:`apply_exif_gps_from_image_description`."""

    scanned: int = 0          # total TIFFs inspected
    written: int = 0          # ExifTool patched GPS into this many files
    missing_gps: int = 0      # ImageDescription had no ``gps`` block
    no_description: int = 0   # no ImageDescription tag / not JSON
    failed: int = 0           # ExifTool exited non-zero
    failures: list[tuple[Path, str]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.failures is None:
            self.failures = []


def iter_tiff_paths(folder: Path, *, recursive: bool = True) -> Iterable[Path]:
    """Yield ``*.tif`` / ``*.tiff`` paths under *folder*.

    Recursive by default because Bay14Reader writes a ``<cam>/`` subfolder
    per camera serial; a flat scan would miss those.
    """
    folder = Path(folder)
    if not folder.is_dir():
        return
    pattern_iter = folder.rglob if recursive else folder.glob
    seen: set[Path] = set()
    for pat in ("*.tif", "*.tiff", "*.TIF", "*.TIFF"):
        for p in pattern_iter(pat):
            if p.is_file() and p not in seen:
                seen.add(p)
                yield p


def _gps_fix_to_exiftool_csv_row(path: Path, fix: GpsFix) -> dict[str, str]:
    """One ExifTool CSV-import row (``SourceFile`` + EXIF GPS columns)."""
    if fix.latitude_deg is None or fix.longitude_deg is None:
        raise ValueError(f"refusing to write empty GPS for {path}")
    lat_ref = "N" if fix.latitude_deg >= 0 else "S"
    lon_ref = "E" if fix.longitude_deg >= 0 else "W"
    row: dict[str, str] = {
        "SourceFile": str(path.resolve()),
        "GPSVersionID": "2 3 0 0",
        "GPSLatitude": _fmt_lat_lon_for_exiftool(abs(fix.latitude_deg)),
        "GPSLatitudeRef": lat_ref,
        "GPSLongitude": _fmt_lat_lon_for_exiftool(abs(fix.longitude_deg)),
        "GPSLongitudeRef": lon_ref,
    }
    if fix.altitude_m is not None and not math.isnan(fix.altitude_m):
        row["GPSAltitude"] = f"{fix.altitude_m:.3f}"
        row["GPSAltitudeRef"] = "0" if fix.altitude_m >= 0 else "1"
    if fix.timestamp_utc is not None:
        ts = fix.timestamp_utc
        if ts.tzinfo is not None:
            ts = ts.astimezone(timezone.utc)
        row["GPSDateStamp"] = ts.strftime("%Y:%m:%d")
        row["GPSTimeStamp"] = (
            f"{ts.hour:02d}:{ts.minute:02d}:{ts.second:02d}"
            f".{ts.microsecond // 1000:03d}"
        )
    if fix.satellites is not None:
        row["GPSSatellites"] = str(fix.satellites)
    if fix.hdop is not None and not math.isnan(fix.hdop):
        row["GPSDOP"] = f"{fix.hdop:.3f}"
    return row


def _exiftool_batch_read_image_descriptions(
    paths: list[Path],
    *,
    exiftool_exe: Path,
    timeout_s: float,
    log_cb: Optional[Callable[[str], None]] = None,
) -> dict[Path, str]:
    """Read ``ImageDescription`` for many TIFFs in **one** ExifTool process.

    Uses ``-fast`` (header-only, no pixel scan) and an ``-@ argfile`` so we
    do not spawn ~N separate processes or open each 700+ MB file twice.

    ExifTool returns a non-zero exit status if **any** single file produced
    a warning (very likely across 30k+ files on a network share), yet it
    still writes valid JSON for every file it *could* read. So we parse the
    JSON regardless of the exit code and only treat the run as a hard
    failure when it produced **zero** usable entries.

    The argfile path is passed as ``-@`` + a *separate* token (the
    documented form); the joined ``-@FILE`` spelling is not reliably
    parsed and was the original cause of the immediate exit-1 fallback.
    """
    if not paths:
        return {}

    list_path: Optional[str] = None
    json_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".txt",
            delete=False,
            encoding="utf-8",
            newline="\n",
        ) as flist:
            for p in paths:
                # In an argfile each whole line is one argument, so paths
                # with spaces ("After GPS") and UNC prefixes need no quoting.
                flist.write(str(p.resolve()))
                flist.write("\n")
            list_path = flist.name

        json_path = tempfile.mktemp(suffix=".json")
        with open(json_path, "wb") as jout:
            completed = subprocess.run(
                [
                    str(exiftool_exe),
                    "-charset", "filename=UTF8",
                    "-fast",
                    "-m",            # ignore minor warnings (don't escalate)
                    "-json",
                    "-ImageDescription",
                    "-n",
                    "-@", list_path,
                ],
                check=False,
                stdout=jout,
                stderr=subprocess.PIPE,
                timeout=timeout_s,
            )

        raw = ""
        try:
            raw = Path(json_path).read_text(encoding="utf-8")
        except OSError:
            raw = ""

        entries: list = []
        if raw.strip():
            try:
                parsed = json.loads(raw)
                entries = parsed if isinstance(parsed, list) else [parsed]
            except json.JSONDecodeError:
                entries = []

        out: dict[Path, str] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            src = entry.get("SourceFile")
            desc = entry.get("ImageDescription")
            if not src or not isinstance(desc, str):
                continue
            out[Path(src).resolve()] = desc

        if completed.returncode != 0 and log_cb is not None:
            stderr = (completed.stderr or b"").decode("utf-8", errors="replace")
            tail = stderr.strip().splitlines()[-1] if stderr.strip() else ""
            log_cb(
                f"ExifTool read returned exit {completed.returncode} "
                f"(parsed {len(out)}/{len(paths)} files)"
                + (f": {tail}" if tail else ""),
            )

        # Only a true hard failure (no usable JSON at all) bubbles up so the
        # caller can fall back to the slow per-file path.
        if not out:
            stderr = (completed.stderr or b"").decode("utf-8", errors="replace")
            raise subprocess.CalledProcessError(
                completed.returncode or 1,
                [str(exiftool_exe), "-@", str(list_path)],
                output=None,
                stderr=stderr,
            )
        return out
    finally:
        if list_path is not None:
            try:
                os.unlink(list_path)
            except OSError:
                pass
        if json_path is not None:
            try:
                os.unlink(json_path)
            except OSError:
                pass


def _exiftool_apply_gps_csv(
    csv_path: Path,
    *,
    exiftool_exe: Path,
    timeout_s: float,
    expected: int = 0,
    log_cb: Optional[Callable[[str], None]] = None,
) -> int:
    """Apply GPS tags from an ExifTool CSV import file in one process.

    Returns the number of files ExifTool reported as updated. A non-zero
    exit code is tolerated when ExifTool still updated at least one file
    (it returns 1 if any single file warned); only a run that updated
    nothing is raised as a hard failure.
    """
    completed = subprocess.run(
        [
            str(exiftool_exe),
            "-charset", "filename=UTF8",
            "-fast",
            "-m",  # ignore minor warnings so one bad file doesn't abort all
            "-overwrite_original",
            f"-csv={csv_path}",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""

    # ExifTool prints e.g. "    32750 image files updated" / "31 files weren't
    # updated due to errors". Parse the updated count so partial success is
    # visible instead of looking like a total failure.
    updated = 0
    m = re.search(r"(\d+)\s+image files? updated", stdout + "\n" + stderr)
    if m:
        updated = int(m.group(1))

    if completed.returncode != 0 and log_cb is not None:
        tail = stderr.strip().splitlines()[-1] if stderr.strip() else ""
        log_cb(
            f"ExifTool CSV import exit {completed.returncode}; "
            f"updated {updated}"
            + (f"/{expected}" if expected else "")
            + (f": {tail}" if tail else ""),
        )

    if updated == 0 and completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode,
            [str(exiftool_exe), f"-csv={csv_path}"],
            output=stdout,
            stderr=stderr,
        )
    return updated


def apply_exif_gps_from_image_description(
    folder: Path,
    *,
    exiftool_exe: Path,
    recursive: bool = True,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
    log_cb: Optional[Callable[[str], None]] = None,
) -> ExifGpsMigrationStats:
    """Walk TIFFs under *folder* and copy their ``gps`` block into EXIF tags.

    For every ``*.tif`` / ``*.tiff`` that carries a ``gps`` object in its
    ``ImageDescription`` JSON, materialise a real EXIF GPS sub-IFD via
    ExifTool.

    **Batch path (default):** one ExifTool invocation reads
    ``ImageDescription`` from every file (``-fast -@ filelist``), then a
    single ``-csv=import.csv`` write patches all GPS tags. This avoids
    spawning ~30k separate ExifTool processes (each of which reloads Perl and
    re-opens the TIFF),     which is what made large exports appear to take ~30 s per file.

    *progress_cb* is invoked as ``(done, total, current_filename)`` while
    building the import table; *cancel_cb* can stop before the final CSV
    write; *log_cb* gets failure messages.
    """
    paths = list(iter_tiff_paths(folder, recursive=recursive))
    total = len(paths)
    stats = ExifGpsMigrationStats()
    if total == 0:
        if progress_cb is not None:
            progress_cb(0, 0, "")
        return stats

    # Generous timeouts for huge folders (30k × 766 MB TIFFs).
    read_timeout_s = max(600.0, total * 0.25)
    write_timeout_s = max(600.0, total * 0.5)

    if log_cb is not None:
        log_cb(
            f"ExifTool batch: reading ImageDescription from {total} file(s) "
            f"(one process, -fast)…",
        )

    try:
        descriptions = _exiftool_batch_read_image_descriptions(
            paths,
            exiftool_exe=exiftool_exe,
            timeout_s=read_timeout_s,
            log_cb=log_cb,
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as exc:
        if log_cb is not None:
            log_cb(f"ExifTool batch read failed ({exc}); falling back per-file")
        return _apply_exif_gps_from_image_description_per_file(
            paths,
            exiftool_exe=exiftool_exe,
            stats=stats,
            progress_cb=progress_cb,
            cancel_cb=cancel_cb,
            log_cb=log_cb,
        )

    csv_rows: list[dict[str, str]] = []
    for idx, path in enumerate(paths, start=1):
        if cancel_cb is not None and cancel_cb():
            if log_cb is not None:
                log_cb(f"ExifTool pass cancelled after {idx - 1}/{total} files")
            break
        stats.scanned += 1
        desc = descriptions.get(path.resolve())
        if desc is None:
            stats.no_description += 1
            if progress_cb is not None:
                progress_cb(idx, total, path.name)
            continue
        text = desc.strip()
        if not text.startswith("{"):
            stats.no_description += 1
            if progress_cb is not None:
                progress_cb(idx, total, path.name)
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            stats.no_description += 1
            if progress_cb is not None:
                progress_cb(idx, total, path.name)
            continue
        if not isinstance(payload, dict):
            stats.no_description += 1
            if progress_cb is not None:
                progress_cb(idx, total, path.name)
            continue
        fix = _gps_fix_from_image_description(payload)
        if fix is None:
            stats.missing_gps += 1
            if progress_cb is not None:
                progress_cb(idx, total, path.name)
            continue
        try:
            csv_rows.append(_gps_fix_to_exiftool_csv_row(path, fix))
        except ValueError as exc:
            stats.failed += 1
            stats.failures.append((path, str(exc)))
            if log_cb is not None:
                log_cb(f"Skipping {path.name}: {exc}")
        if progress_cb is not None:
            progress_cb(idx, total, path.name)

    if cancel_cb is not None and cancel_cb():
        return stats

    if not csv_rows:
        if log_cb is not None:
            log_cb("ExifTool batch: no files with a gps block to write")
        return stats

    if log_cb is not None:
        log_cb(
            f"ExifTool batch: writing GPS EXIF into {len(csv_rows)} file(s) "
            f"(one CSV import)…",
        )

    csv_path: Optional[str] = None
    try:
        fieldnames = list(csv_rows[0].keys())
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".csv",
            delete=False,
            encoding="utf-8",
            newline="",
        ) as fcsv:
            writer = csv.DictWriter(fcsv, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(csv_rows)
            csv_path = fcsv.name
        updated = _exiftool_apply_gps_csv(
            Path(csv_path),
            exiftool_exe=exiftool_exe,
            timeout_s=write_timeout_s,
            expected=len(csv_rows),
            log_cb=log_cb,
        )
        # ExifTool reports the real count; if it didn't print one (older
        # builds), assume all rows succeeded since the call did not raise.
        stats.written += updated if updated > 0 else len(csv_rows)
        not_updated = len(csv_rows) - stats.written
        if not_updated > 0:
            stats.failed += not_updated
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as exc:
        stats.failed += len(csv_rows)
        msg = str(exc)
        if isinstance(exc, subprocess.CalledProcessError):
            stderr = (exc.stderr or "").strip()
            if stderr:
                msg = stderr.splitlines()[-1]
        if log_cb is not None:
            log_cb(f"ExifTool CSV import failed: {msg}")
        for row in csv_rows:
            stats.failures.append((Path(row["SourceFile"]), msg))
    finally:
        if csv_path is not None:
            try:
                os.unlink(csv_path)
            except OSError:
                pass

    if progress_cb is not None:
        progress_cb(total, total, "")
    return stats


def _apply_exif_gps_from_image_description_per_file(
    paths: list[Path],
    *,
    exiftool_exe: Path,
    stats: ExifGpsMigrationStats,
    progress_cb: Optional[Callable[[int, int, str], None]],
    cancel_cb: Optional[Callable[[], bool]],
    log_cb: Optional[Callable[[str], None]],
) -> ExifGpsMigrationStats:
    """Legacy per-file path (slow on large folders; used only as fallback)."""
    total = len(paths)
    for idx, path in enumerate(paths, start=1):
        if cancel_cb is not None and cancel_cb():
            if log_cb is not None:
                log_cb(f"ExifTool pass cancelled after {idx - 1}/{total} files")
            break
        stats.scanned += 1
        payload = _read_image_description_json(path)
        if payload is None:
            stats.no_description += 1
            if progress_cb is not None:
                progress_cb(idx, total, path.name)
            continue
        fix = _gps_fix_from_image_description(payload)
        if fix is None:
            stats.missing_gps += 1
            if progress_cb is not None:
                progress_cb(idx, total, path.name)
            continue
        try:
            exiftool_geotag(path, fix, exiftool_exe=exiftool_exe)
            stats.written += 1
        except subprocess.CalledProcessError as exc:
            stats.failed += 1
            stderr = (exc.stderr or "").strip()
            msg = stderr.splitlines()[-1] if stderr else f"exit {exc.returncode}"
            stats.failures.append((path, msg))
            if log_cb is not None:
                log_cb(f"ExifTool failed on {path.name}: {msg}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            stats.failed += 1
            msg = str(exc)
            stats.failures.append((path, msg))
            if log_cb is not None:
                log_cb(f"ExifTool failed on {path.name}: {msg}")
        if progress_cb is not None:
            progress_cb(idx, total, path.name)
    return stats
