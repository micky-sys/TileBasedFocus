"""Shared decode / encode helpers for ``.bay14line`` batch conversion.

Used by ``tools/bay14_to_tif.py`` (CLI, legacy unscaled Bayer TIFFs) and
``tools/bay14_reader_gui.py`` (GUI with optional 14→16 bit scaling).
"""

from __future__ import annotations

import json
import logging
import mmap
import re
import sys
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

import cv2
import numpy as np
import tifffile

_HOST = Path(__file__).resolve().parent.parent / "host"
if str(_HOST) not in sys.path:
    sys.path.insert(0, str(_HOST))

from capture.bayer_demosaic import (  # noqa: E402
    opencv_bayer_rgb_demosaic_code,
    opencv_bayer_rgb_demosaic_label,
)
from capture.gps_fix import GpsFix  # noqa: E402
from capture.raw_bay_writer import (  # noqa: E402
    COMPRESSION_BLOSC2_ZSTD1_SHUFFLE,
    COMPRESSION_NONE,
    LINE_HEADER_FIXED_PREFIX,
    LINE_INDEX_ENTRY_SIZE,
    LINE_PIXEL_FORMAT_PACKED_U14,
    LINE_PIXEL_FORMAT_UINT16_2D,
    decompress_frame_blosc2,
    decompress_packed,
    parse_frame_chunk_header,
    parse_line_header,
    parse_line_header_prefix,
    parse_line_index,
    recover_line_chunks,
    unpack_u14,
)
# GPS is embedded as a real EXIF GPS sub-IFD at write time: the encoders
# reserve the GPSIFD (34853) pointer via ``reserved_gpsifd_extratag`` and then
# append the sub-IFD with ``embed_exif_gps_subifd`` (no full-file rewrite, so
# it is fast even on a network share). tifffile's ``extratags`` cannot emit a
# sub-IFD, hence the manual append. The JSON ``gps`` block in
# ``ImageDescription`` is still written too; the ExifTool post-pass
# (``capture.tiff_writer.apply_exif_gps_from_image_description``) remains as a
# fallback for TIFFs produced by older builds.
from capture.tiff_writer import (  # noqa: E402
    attitude_from_payload,
    embed_exif_gps_subifd,
    exiftool_write_camera_attitude,
)

log = logging.getLogger(__name__)


def _gps_fix_from_meta(merged_meta: dict[str, Any]) -> GpsFix | None:
    """Reconstruct a :class:`GpsFix` from the per-frame ``gps`` JSON, or None.

    The capture pipeline serialises a fix via :meth:`GpsFix.to_dict`
    (numeric fields kept as floats / ints; ``timestamp_utc`` is an
    ISO-8601 string with millisecond precision). We invert that here so
    the export path can hand a real :class:`GpsFix` to
    :func:`_build_gps_extratags` and reuse the capture-side EXIF logic
    verbatim.
    """
    gps_blob = merged_meta.get("gps")
    if not isinstance(gps_blob, dict):
        return None
    import datetime as _dt

    ts_raw = gps_blob.get("timestamp_utc")
    ts_utc: _dt.datetime | None
    if isinstance(ts_raw, str):
        try:
            # ``fromisoformat`` accepts the timespec="milliseconds" form
            # we write and the older datetime.__str__() form.
            ts_utc = _dt.datetime.fromisoformat(ts_raw)
        except ValueError:
            ts_utc = None
    elif isinstance(ts_raw, _dt.datetime):
        ts_utc = ts_raw
    else:
        ts_utc = None

    def _f(name: str) -> float | None:
        v = gps_blob.get(name)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _i(name: str) -> int | None:
        v = gps_blob.get(name)
        if v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    return GpsFix(
        latitude_deg=_f("latitude_deg"),
        longitude_deg=_f("longitude_deg"),
        altitude_m=_f("altitude_m"),
        timestamp_utc=ts_utc,
        satellites=_i("satellites"),
        hdop=_f("hdop"),
        fix_quality=_i("fix_quality"),
    )


# ─── EXIF extratags (ModifyDate, ExifImageWidth / Height) ────────────────────

# TIFF / EXIF tag IDs we emit beyond what tifffile populates automatically.
# Most structural tags (ImageWidth=256, ImageLength=257, BitsPerSample=258,
# Compression=259, PhotometricInterpretation=262, StripOffsets=273,
# SamplesPerPixel=277, RowsPerStrip=278, StripByteCounts=279,
# PlanarConfiguration=284, XResolution=282, YResolution=283,
# ResolutionUnit=296, Software=305) come from tifffile by default.
_TIFF_DATETIME = 306             # ASCII "YYYY:MM:DD HH:MM:SS" + NUL  (count=20)
_EXIF_IMAGE_WIDTH = 40962        # LONG, 1 entry
_EXIF_IMAGE_HEIGHT = 40963       # LONG, 1 entry

# tifffile dtype codes:
_TIFF_DTYPE_ASCII = 2
_TIFF_DTYPE_LONG = 4


def _exif_datetime_string(ts: Optional[datetime] = None) -> str:
    """``YYYY:MM:DD HH:MM:SS`` per TIFF 6.0 §8 / EXIF 2.32 (local time).

    EXIF defines `DateTime`, `DateTimeOriginal`, and `DateTimeDigitized` all
    as 20-byte ASCII (19 chars + trailing NUL) in this format.
    """
    if ts is None:
        ts = datetime.now()
    return ts.strftime("%Y:%m:%d %H:%M:%S")


def _build_modify_date_extratag(ts: Optional[datetime] = None) -> tuple:
    """Return a tifffile extratag tuple for IFD0 ``DateTime`` (tag 306)."""
    s = _exif_datetime_string(ts)
    return (_TIFF_DATETIME, _TIFF_DTYPE_ASCII, len(s) + 1, s, True)


def _build_exif_dim_extratags(width: int, height: int) -> list[tuple]:
    """Return extratags for ``ExifImageWidth`` (40962) + ``ExifImageHeight``
    (40963) as LONGs.

    The EXIF spec puts these inside the ExifIFD sub-IFD (pointed to by
    IFD0 tag 34665). tifffile's ``extratags`` parameter cannot emit a
    sub-IFD, so we pin them in IFD0 -- the same pragmatic placement the
    GPS tags use (see ``capture.tiff_writer._build_gps_extratags``).
    ExifTool, Capture One, and other standards-tolerant readers pick
    them up from IFD0 even though that placement is non-canonical.
    """
    return [
        (_EXIF_IMAGE_WIDTH, _TIFF_DTYPE_LONG, 1, int(width), True),
        (_EXIF_IMAGE_HEIGHT, _TIFF_DTYPE_LONG, 1, int(height), True),
    ]


def build_standard_extratags(
    *,
    width: int,
    height: int,
    fix: Optional[GpsFix] = None,
    modify_ts: Optional[datetime] = None,
) -> list[tuple]:
    """Bundle the per-image extratags emitted by both TIFF encoders:
    ``ModifyDate`` (IFD0 306), ``ExifImageWidth`` (40962), ``ExifImageHeight``
    (40963).

    GPS is **not** emitted here. tifffile's ``extratags`` cannot create the
    GPS sub-IFD that the EXIF spec requires (and refuses IFD-pointer tags
    like 34853 outright). Instead the encoders call
    :func:`capture.tiff_writer.embed_exif_gps_subifd` right after the write
    to add the canonical GPS sub-IFD in place. *fix* is accepted so callers
    can keep passing it (and so the encoders know whether to embed).
    """
    del fix  # GPS handled by embed_exif_gps_subifd post-write, not extratags
    tags: list[tuple] = []
    tags.append(_build_modify_date_extratag(modify_ts))
    tags.extend(_build_exif_dim_extratags(width, height))
    return tags


# ─── Horizontal flip (per-camera "flip" option in the GUI) ──────────────────

# A horizontal flip of a Bayer mosaic shifts the CFA pattern at (0, 0) /
# (0, 1) / (1, 0) / (1, 1). For the standard "color of the top-left two
# pixels" convention used by GenICam and tifffile, fliplr swaps the
# letters of the top row pair (R<->G or B<->G), which is the same as
# swapping the two-letter pattern code.
_FLIPPED_BAYER_PATTERN: dict[str, str] = {
    "RG": "GR", "GR": "RG",
    "BG": "GB", "GB": "BG",
}

_BAYER_RE = re.compile(r"^(Bayer)(RG|GR|BG|GB)(.*)$")


def _flip_bayer_pattern(label: Optional[str]) -> Optional[str]:
    if not isinstance(label, str):
        return label
    return _FLIPPED_BAYER_PATTERN.get(label, label)


def _flip_genicam_pixel_format(label: Optional[str]) -> Optional[str]:
    if not isinstance(label, str):
        return label
    m = _BAYER_RE.match(label)
    if not m:
        return label
    return m.group(1) + _FLIPPED_BAYER_PATTERN[m.group(2)] + m.group(3)


def apply_fliplr_to_frame(
    arr: np.ndarray, meta: dict[str, Any]
) -> tuple[np.ndarray, dict[str, Any]]:
    """Flip *arr* left-to-right and return ``(flipped_arr, updated_meta)``.

    Updates the Bayer pattern labels (``bayer_pattern``,
    ``genicam_pixel_format``) so downstream demosaicers still know
    which mosaic colour sits at (0, 0). Also stamps ``flipped_lr=True``
    into the metadata as an audit trail.

    Works for 2-D (Bayer) and 3-D (RGB / RGBA) arrays. The result is
    contiguous so tifffile's strip writer does not have to materialise
    a view.
    """
    flipped = np.ascontiguousarray(arr[:, ::-1, ...])
    new_meta = dict(meta)
    new_meta["bayer_pattern"] = _flip_bayer_pattern(new_meta.get("bayer_pattern"))
    new_meta["genicam_pixel_format"] = _flip_genicam_pixel_format(
        new_meta.get("genicam_pixel_format")
    )
    new_meta["flipped_lr"] = True
    return flipped, new_meta


def discover_lines(
    cam_dir: Path,
    pattern: str = "*.bay14line",
    *,
    recursive: bool = False,
) -> list[Path]:
    """Sorted ``.bay14line`` paths under *cam_dir*.

    By default only the directory itself is scanned (same as
    ``Path.glob(pattern)``). With *recursive* True, the pattern is
    prefixed with ``**/`` unless it already contains ``**``, so nested
    folders are included (e.g. ``E:/Project/cam2439006/``).
    """
    cam_dir = Path(cam_dir).expanduser().resolve(strict=False)
    if not cam_dir.is_dir():
        return []
    pat = (pattern or "*.bay14line").strip() or "*.bay14line"
    if recursive and "**" not in pat:
        pat = f"**/{pat}"
    try:
        found = sorted(cam_dir.glob(pat))
    except OSError as exc:
        log.warning("discover_lines glob failed for %s pattern=%r: %s",
                    cam_dir, pat, exc)
        return []
    return [p for p in found if p.is_file()]


def peek_line_meta(line_path: Path) -> dict[str, Any]:
    """Read line header only (cheap): geometry, codec, indexed frame count."""
    line_path = Path(line_path)
    with open(line_path, "rb") as fh:
        prefix = fh.read(LINE_HEADER_FIXED_PREFIX)
        if len(prefix) < LINE_HEADER_FIXED_PREFIX:
            raise ValueError(f"{line_path}: too short for .bay14line header")
        header_len, n_frames_field, idx_off, _ = parse_line_header_prefix(prefix)
        fh.seek(0)
        hdr = fh.read(header_len)
    meta, _, _, _ = parse_line_header(hdr)
    out = dict(meta)
    out["_indexed_frame_count"] = int(n_frames_field)
    out["_index_offset"] = int(idx_off)
    return out


def shift_14_to_16(arr: np.ndarray) -> np.ndarray:
    """Linear map 14-bit sensor values into a 16-bit container (<< 2)."""
    return (arr.astype(np.uint16, copy=False) << 2)


def count_line_frames(line_path: Path) -> int:
    """Return the number of frames in a line file (index or recovery walk)."""
    line_path = Path(line_path)
    pm = peek_line_meta(line_path)
    n = int(pm["_indexed_frame_count"])
    idx_off = int(pm["_index_offset"])
    if n > 0 and idx_off > 0:
        return n
    _meta, recovered = recover_line_chunks(str(line_path))
    return len(recovered)


def iter_line_frames(
    line_path: Path,
    *,
    recovery_notice: Callable[[str], None] | None = print,
    meta_only: bool = False,
) -> Iterator[tuple[dict[str, Any], np.ndarray | None]]:
    """Yield ``(merged_meta, arr_u16_2d)`` per frame from a ``.bay14line``.

    *arr_u16_2d* holds raw Bayer samples as captured (typically 14-bit
    values stored in uint16 cells — **no** automatic left-shift).

    *recovery_notice* is called once when falling back to
    :func:`recover_line_chunks` (pass ``None`` to silence). Defaults to
    ``print`` for CLI parity with historical ``bay14_to_tif`` behaviour.

    When *meta_only* is True, the per-frame pixel payload is **not**
    decoded or copied; the yielded array is ``None``. This reads only the
    4 KB chunk header per frame (sweep / frame_in_sweep / acquire_timestamp
    / gps), which is orders of magnitude faster than decoding multi-hundred
    MB frames -- use it for metadata audits like ``bay14_gps_check.py``.
    """
    line_path = Path(line_path)
    with open(line_path, "rb") as fh:
        with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            header_len, n_frames_field, idx_off, _ = parse_line_header_prefix(
                mm[:LINE_HEADER_FIXED_PREFIX]
            )
            line_meta, _, _n_frames_hdr, _ = parse_line_header(mm[:header_len])

            if n_frames_field > 0 and idx_off > 0:
                idx_blob = mm[
                    idx_off : idx_off + n_frames_field * LINE_INDEX_ENTRY_SIZE
                ]
                chunks = parse_line_index(idx_blob, n_frames_field)
            else:
                meta_dict, recovered = recover_line_chunks(str(line_path))
                line_meta = meta_dict
                chunks = recovered
                if recovery_notice is not None:
                    recovery_notice(
                        f"  {line_path}: index missing; recovered "
                        f"{len(chunks)} chunks via BAY14FRM walk"
                    )

            h = int(line_meta["height"])
            w = int(line_meta["width"])
            codec = str(line_meta.get("compression", COMPRESSION_NONE))
            pixel_format = str(
                line_meta.get("format", LINE_PIXEL_FORMAT_UINT16_2D),
            )

            for sweep, f_in, offset, payload_len, _unc in chunks:
                chunk_blob = mm[offset : offset + 4096]
                cmeta, c_hdr_len, c_payload_len = parse_frame_chunk_header(
                    chunk_blob
                )
                if c_payload_len != payload_len:
                    raise ValueError(
                        f"{line_path}: chunk @offset {offset} payload_len "
                        f"mismatch (chunk header says {c_payload_len}, "
                        f"index says {payload_len})"
                    )
                payload_start = offset + c_hdr_len

                if meta_only:
                    merged_meta = dict(line_meta)
                    merged_meta.update(cmeta)
                    merged_meta["sweep"] = int(sweep)
                    merged_meta["frame_in_sweep"] = int(f_in)
                    yield merged_meta, None
                    continue

                if codec == COMPRESSION_BLOSC2_ZSTD1_SHUFFLE:
                    payload = mm[payload_start : payload_start + payload_len]
                    arr = decompress_frame_blosc2(payload, height=h, width=w)
                elif codec == COMPRESSION_NONE:
                    if pixel_format == LINE_PIXEL_FORMAT_PACKED_U14:
                        expected = h * w * 7 // 4
                        if payload_len != expected:
                            raise ValueError(
                                f"{line_path}: packed_u14 payload len "
                                f"{payload_len} != expected {expected}"
                            )
                        payload = mm[payload_start : payload_start + payload_len]
                        arr = unpack_u14(
                            np.frombuffer(payload, dtype=np.uint8),
                            h * w,
                        ).reshape(h, w)
                    else:
                        # Uncompressed uint16 plane: use frombuffer + copy so the
                        # array does not alias the mmap past tight loops on Windows.
                        need = h * w * 2
                        if payload_len != need:
                            raise ValueError(
                                f"{line_path}: uint16_2d payload len "
                                f"{payload_len} != expected {need}"
                            )
                        arr = np.frombuffer(
                            mm,
                            dtype=np.uint16,
                            count=h * w,
                            offset=payload_start,
                        ).reshape(h, w)
                else:
                    payload = mm[payload_start : payload_start + payload_len]
                    packed = decompress_packed(
                        payload,
                        codec=codec,
                        expected_size=h * w * 7 // 4,
                    )
                    arr = unpack_u14(packed, h * w).reshape(h, w)

                merged_meta: dict[str, Any] = dict(line_meta)
                merged_meta.update(cmeta)
                merged_meta["sweep"] = int(sweep)
                merged_meta["frame_in_sweep"] = int(f_in)
                # Must not yield arrays that alias *mm*: on Windows, closing the
                # mmap raises BufferError ("cannot close exported pointers exist")
                # if NumPy/tifffile still exports the underlying buffer (common
                # when np.frombuffer(mm, ...) + np.ascontiguousarray returns a
                # view). An explicit copy keeps mmap lifetime independent of the
                # consumer thread.
                arr = np.array(arr, dtype=arr.dtype, copy=True, order="C")
                yield merged_meta, arr


def resize_to_width(arr: np.ndarray, resize_width: Optional[int]) -> np.ndarray:
    """Downscale *arr* to *resize_width* px wide, preserving aspect ratio.

    No-op when *resize_width* is None, non-positive, or not smaller than
    the current width (we never upscale). Uses ``INTER_AREA``, the correct
    interpolation for downsampling. Handles both single-plane (HxW) and
    multi-channel (HxWxC) arrays.
    """
    if resize_width is None:
        return arr
    h, w = arr.shape[:2]
    if resize_width < 1 or resize_width >= w:
        return arr
    new_h = max(1, round(h * resize_width / w))
    return cv2.resize(arr, (resize_width, new_h), interpolation=cv2.INTER_AREA)


def write_tiff_bayer_u16(
    out_path: Path,
    arr: np.ndarray,
    merged_meta: dict[str, Any],
    *,
    scale_14_to_16: bool,
    flip_lr: bool = False,
    resize_width: Optional[int] = None,
) -> None:
    """Write single-channel uint16 Bayer TIFF with embedded JSON metadata.

    When *scale_14_to_16* is True, applies :func:`shift_14_to_16` and
    annotates metadata. When False, pixels match the capture writer output
    (legacy ``bay14_to_tif`` behaviour).

    The per-frame ``gps`` block from the source chunk (if any) is
    preserved verbatim in the TIFF's ``ImageDescription`` JSON, and a
    post-export ExifTool pass
    (:func:`capture.tiff_writer.apply_exif_gps_from_image_description`)
    migrates it into a real EXIF GPS sub-IFD that Lightroom / Capture
    One / ``exiftool -GPSLatitude`` all read. We deliberately do **not**
    write EXIF GPS tags via tifffile's ``extratags`` here, because that
    path pins them into IFD0 with the wrong namespace and confuses
    every standard EXIF reader.

    Always emits ``ModifyDate`` (IFD0 306) + ``ExifImageWidth``
    (40962) + ``ExifImageHeight`` (40963) so the metadata panel in
    Capture One / ExifTool / Lightroom has something to display.

    With *flip_lr* True the pixel buffer is horizontally mirrored before
    encode and the Bayer pattern labels in metadata are swapped to
    match (RG<->GR, BG<->GB) so a downstream demosaicer still aligns.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    meta_out = dict(merged_meta)
    if scale_14_to_16:
        meta_out["pixel_container_bits"] = 16
        meta_out["sensor_bit_depth"] = 14
        meta_out["value_linear_scale"] = "left_shift_2_full_range_u16"
        arr_w = shift_14_to_16(arr)
    else:
        arr_w = np.ascontiguousarray(arr)
    if flip_lr:
        arr_w, meta_out = apply_fliplr_to_frame(arr_w, meta_out)
    # ponytail: downscaling the raw Bayer mosaic blends adjacent
    # colour sites, so the low-res copy is a preview, not a re-mosaicable
    # capture. Upgrade path if fidelity matters: demosaic -> resize ->
    # re-mosaic. Fine for the "_S" preview folder this feeds.
    arr_w = resize_to_width(arr_w, resize_width)
    description = json.dumps(meta_out, separators=(",", ":"))

    fix = _gps_fix_from_meta(meta_out)
    extratags = build_standard_extratags(
        width=arr_w.shape[1],
        height=arr_w.shape[0],
        fix=fix,
    )

    tifffile.imwrite(
        str(out_path),
        arr_w,
        photometric="minisblack",
        compression=None,
        bigtiff=False,
        description=description,
        extratags=extratags,
    )
    # Materialise the canonical EXIF GPS sub-IFD in place (only appends a
    # small block + patches the reserved 34853 pointer; no strip rewrite).
    if fix is not None and fix.has_position:
        embed_exif_gps_subifd(out_path, fix)
    attitude = attitude_from_payload(meta_out)
    if attitude is not None:
        exiftool_write_camera_attitude(out_path, attitude)


def encode_tiff_rgb16(
    out_path: Path,
    arr14_u16: np.ndarray,
    merged_meta: dict[str, Any],
    *,
    flip_lr: bool = False,
    resize_width: Optional[int] = None,
) -> None:
    """Demosaic Bayer RG → RGB uint16 TIFF (classic, NOT BigTIFF); applies 14→16 shift first.

    The per-frame ``gps`` block (if any) is carried through in the
    ``ImageDescription`` JSON; run the post-export ExifTool pass
    (:func:`capture.tiff_writer.apply_exif_gps_from_image_description`)
    to materialise a proper EXIF GPS sub-IFD that standard EXIF
    readers recognise.

    The output is forced to classic TIFF (32-bit offsets) even at the
    13400x9528x3xu16 = 766 MB size point. ExifTool, Lightroom, GIS, etc.
    handle classic TIFF in-place metadata edits far better than BigTIFF,
    and our pixel buffer is comfortably under the 4 GB classic ceiling.

    Always emits ``ModifyDate`` + ``ExifImageWidth/Height`` extratags so
    Capture One's metadata panel surfaces something.

    With *flip_lr* True the **demosaiced** RGB buffer is mirrored
    horizontally. Demosaic happens on the original Bayer mosaic first
    so the colour estimates are still correct; the flip is purely a
    presentation step on the RGB result.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shifted = shift_14_to_16(arr14_u16)
    dcode = opencv_bayer_rgb_demosaic_code(merged_meta)
    rgb = cv2.cvtColor(shifted, dcode)
    meta_out = dict(merged_meta)
    meta_out["pixel_container_bits"] = 16
    meta_out["sensor_bit_depth"] = 14
    meta_out["value_linear_scale"] = "left_shift_2_full_range_u16"
    meta_out["demosaic_opencv_rgb"] = opencv_bayer_rgb_demosaic_label(merged_meta)
    if flip_lr:
        # The Bayer pattern label rotation is irrelevant once we've
        # demosaiced, but stamp ``flipped_lr=True`` so downstream tools
        # see the audit trail.
        rgb = np.ascontiguousarray(rgb[:, ::-1, :])
        meta_out["flipped_lr"] = True
    rgb = resize_to_width(rgb, resize_width)
    description = json.dumps(meta_out, separators=(",", ":"))

    fix = _gps_fix_from_meta(merged_meta)
    extratags = build_standard_extratags(
        width=rgb.shape[1],
        height=rgb.shape[0],
        fix=fix,
    )

    tifffile.imwrite(
        str(out_path),
        rgb,
        photometric="rgb",
        compression=None,
        bigtiff=False,
        description=description,
        extratags=extratags,
    )
    if fix is not None and fix.has_position:
        embed_exif_gps_subifd(out_path, fix)
    attitude = attitude_from_payload(merged_meta)
    if attitude is not None:
        exiftool_write_camera_attitude(out_path, attitude)


def encode_jpeg(
    out_path: Path,
    arr14_u16: np.ndarray,
    merged_meta: dict[str, Any],
    *,
    quality: int,
    flip_lr: bool = False,
    resize_width: Optional[int] = None,
) -> None:
    """Lossy JPEG from demosaiced RGB; quality 1–100 (OpenCV scale).

    With *flip_lr* True the BGR buffer is mirrored before JPEG encoding,
    so the "flip" GUI option behaves consistently across all three
    export formats. (JPEG metadata is not emitted by this path -- the
    full-fidelity metadata copy lives in the matching TIFF export.)
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    q = int(np.clip(quality, 1, 100))
    shifted = shift_14_to_16(arr14_u16)
    dcode = opencv_bayer_rgb_demosaic_code(merged_meta)
    rgb16 = cv2.cvtColor(shifted, dcode)
    rgb8 = (rgb16 >> 8).astype(np.uint8)
    bgr8 = cv2.cvtColor(rgb8, cv2.COLOR_RGB2BGR)
    if flip_lr:
        bgr8 = np.ascontiguousarray(bgr8[:, ::-1, :])
    bgr8 = resize_to_width(bgr8, resize_width)
    ok, buf = cv2.imencode(
        ".jpg",
        bgr8,
        [int(cv2.IMWRITE_JPEG_QUALITY), q],
    )
    if not ok:
        raise RuntimeError(f"{out_path}: cv2.imencode failed")
    out_path.write_bytes(buf.tobytes())


@runtime_checkable
class FrameFilter(Protocol):
    """Future hook: mutate a Bayer uint16 plane before encoding."""

    name: str

    def apply(self, arr_u16: np.ndarray, meta: dict[str, Any]) -> np.ndarray:
        ...


FILTER_REGISTRY: dict[str, type[FrameFilter]] = {}
"""Populate with concrete filter classes when implementing batch filters."""
