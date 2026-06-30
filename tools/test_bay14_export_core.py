"""Self-test for bay14_export_core: new EXIF extratags + flip helper.

Run::

    python tools/test_bay14_export_core.py

Covers the May 2026 additions:
  * ``ModifyDate`` (IFD0 tag 306) emitted as ASCII "YYYY:MM:DD HH:MM:SS".
  * ``ExifImageWidth`` (40962) + ``ExifImageHeight`` (40963) emitted at IFD0
    as LONGs (count=1) -- read by ExifTool / Capture One from IFD0 even
    though they technically belong inside the ExifIFD sub-IFD.
  * ``apply_fliplr_to_frame`` mirrors the array and swaps the Bayer
    pattern labels (RG<->GR, BG<->GB) so the demosaicer downstream still
    aligns. JSON metadata also picks up ``flipped_lr=True``.
  * Full round-trip through ``write_tiff_bayer_u16`` and
    ``encode_tiff_rgb16`` produces a TIFF whose IFD0 carries the new
    extratags (verified via ``tifffile.TiffFile``).
"""

from __future__ import annotations

import csv
import json
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
import tifffile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from bay14_export_core import (  # noqa: E402
    _BAYER_RE,
    _FLIPPED_BAYER_PATTERN,
    _build_exif_dim_extratags,
    _build_modify_date_extratag,
    _exif_datetime_string,
    _flip_bayer_pattern,
    _flip_genicam_pixel_format,
    apply_fliplr_to_frame,
    build_standard_extratags,
    encode_tiff_rgb16,
    write_tiff_bayer_u16,
)


def test_exif_datetime_string_format() -> None:
    ts = datetime(2026, 5, 27, 14, 33, 9)
    assert _exif_datetime_string(ts) == "2026:05:27 14:33:09"
    # Spec format: 19 chars (no trailing NUL).
    assert len(_exif_datetime_string(ts)) == 19
    # Regex match guarantees no slashes / dashes have crept in.
    assert re.fullmatch(r"\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}",
                        _exif_datetime_string(ts))


def test_build_modify_date_extratag_tuple_shape() -> None:
    ts = datetime(2026, 5, 27, 14, 33, 9)
    tag, dtype, count, value, writeonce = _build_modify_date_extratag(ts)
    assert tag == 306                       # TIFF DateTime
    assert dtype == 2                       # ASCII
    assert count == len("2026:05:27 14:33:09") + 1  # +1 for trailing NUL byte
    assert value == "2026:05:27 14:33:09"
    assert writeonce is True


def test_build_exif_dim_extratags_long_count_one() -> None:
    tags = _build_exif_dim_extratags(13400, 9528)
    assert len(tags) == 2
    w, h = tags
    assert w[0] == 40962 and h[0] == 40963
    assert w[1] == 4 and h[1] == 4         # LONG
    assert w[2] == 1 and h[2] == 1         # count
    assert w[3] == 13400 and h[3] == 9528  # value


def test_build_standard_extratags_bundles_everything_without_gps() -> None:
    ts = datetime(2026, 5, 27, 14, 33, 9)
    tags = build_standard_extratags(width=100, height=80, modify_ts=ts, fix=None)
    tag_ids = [t[0] for t in tags]
    assert 306 in tag_ids
    assert 40962 in tag_ids
    assert 40963 in tag_ids


def test_build_standard_extratags_does_not_emit_gps_extratags() -> None:
    """Even when a fix is provided, the broken IFD0 GPS extratags must not
    be emitted -- they place the GPS tag IDs in the wrong IFD namespace
    (tag 1 collides with ``InteropIndex`` etc.), making the file unreadable
    by ExifTool / Lightroom. The post-export ExifTool pass is responsible
    for materialising the canonical GPS sub-IFD instead.
    """
    sys.path.insert(0, str(HERE.parent / "host"))
    from capture.gps_fix import GpsFix  # noqa: WPS433  (test-local import)

    fix = GpsFix(
        latitude_deg=32.5,
        longitude_deg=34.7,
        altitude_m=3000.0,
        timestamp_utc=None,
        satellites=10,
        hdop=None,
        fix_quality=1,
    )
    tags = build_standard_extratags(width=10, height=10, fix=fix)
    tag_ids = {t[0] for t in tags}
    # The legacy IDs that would land in IFD0 with the wrong taxonomy:
    for forbidden in (0, 1, 2, 3, 4, 5, 6, 7, 8, 11, 29):
        assert forbidden not in tag_ids, (
            f"build_standard_extratags must not emit IFD0 tag {forbidden} "
            f"(GPS tag ID in the wrong sub-IFD); got {sorted(tag_ids)}"
        )


def test_flip_bayer_pattern_mapping_is_involutive() -> None:
    for src in ("RG", "GR", "BG", "GB"):
        once = _flip_bayer_pattern(src)
        assert once in _FLIPPED_BAYER_PATTERN
        twice = _flip_bayer_pattern(once)
        assert twice == src
    # Non-Bayer label passes through.
    assert _flip_bayer_pattern("mono") == "mono"
    assert _flip_bayer_pattern(None) is None


def test_flip_genicam_pixel_format_swaps_pair_and_preserves_suffix() -> None:
    assert _flip_genicam_pixel_format("BayerRG14") == "BayerGR14"
    assert _flip_genicam_pixel_format("BayerBG12") == "BayerGB12"
    assert _flip_genicam_pixel_format("BayerGR16") == "BayerRG16"
    assert _flip_genicam_pixel_format("Mono14") == "Mono14"
    assert _BAYER_RE.match("BayerRG14") is not None


def test_apply_fliplr_to_frame_inverts_pixels_and_pattern() -> None:
    # 4x4 ascending so fliplr is easy to verify visually.
    arr = np.arange(16, dtype=np.uint16).reshape(4, 4)
    meta = {
        "bayer_pattern": "RG",
        "genicam_pixel_format": "BayerRG14",
        "frame_in_sweep": 3,
    }
    out_arr, out_meta = apply_fliplr_to_frame(arr, meta)
    # Row 0: 0 1 2 3 -> 3 2 1 0
    assert out_arr[0, 0] == 3 and out_arr[0, 3] == 0
    # Other fields untouched.
    assert out_meta["frame_in_sweep"] == 3
    assert out_meta["bayer_pattern"] == "GR"
    assert out_meta["genicam_pixel_format"] == "BayerGR14"
    assert out_meta["flipped_lr"] is True
    # Original meta is not mutated (we make a copy).
    assert meta["bayer_pattern"] == "RG"
    assert meta["genicam_pixel_format"] == "BayerRG14"
    assert "flipped_lr" not in meta
    # Result is C-contiguous so tifffile's strip writer doesn't have to
    # materialise a view.
    assert out_arr.flags["C_CONTIGUOUS"]


def test_apply_fliplr_to_frame_handles_rgb_3d_arrays() -> None:
    arr = np.zeros((2, 3, 3), dtype=np.uint16)
    arr[0, 0, :] = [11, 22, 33]
    arr[0, 2, :] = [99, 88, 77]
    out, _ = apply_fliplr_to_frame(arr, {})
    assert list(out[0, 0, :]) == [99, 88, 77]
    assert list(out[0, 2, :]) == [11, 22, 33]


def test_write_tiff_bayer_u16_emits_new_extratags() -> None:
    arr = np.arange(64 * 96, dtype=np.uint16).reshape(64, 96)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "bayer.tif"
        meta = {
            "camera_serial": "0001",
            "project": "p", "line": 1,
            "sweep": 1, "frame_in_sweep": 1,
            "bayer_pattern": "RG",
            "genicam_pixel_format": "BayerRG14",
            "bit_depth": 14,
            "container": "bay14line_v1",
        }
        write_tiff_bayer_u16(path, arr, meta, scale_14_to_16=True)

        with tifffile.TiffFile(str(path)) as tf:
            page = tf.pages[0]
            # Structural tags tifffile already emits.
            assert page.tags["ImageWidth"].value == 96
            assert page.tags["ImageLength"].value == 64
            assert page.tags["BitsPerSample"].value == 16
            assert page.tags["Compression"].value == 1   # uncompressed
            assert page.tags["SamplesPerPixel"].value == 1
            assert page.tags["RowsPerStrip"].value == 64
            # tifffile omits PlanarConfiguration when SamplesPerPixel==1
            # (it only matters for multi-sample images); the RGB encoder
            # test below pins it on the demosaiced path.
            # New extratags emitted by build_standard_extratags.
            assert page.tags.get(306) is not None, "ModifyDate missing"
            assert re.fullmatch(
                r"\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}",
                page.tags[306].value,
            ), page.tags[306].value
            assert page.tags.get(40962) is not None, "ExifImageWidth missing"
            assert page.tags[40962].value == 96
            assert page.tags.get(40963) is not None, "ExifImageHeight missing"
            assert page.tags[40963].value == 64

            # ImageDescription JSON round-trips and now lacks a flipped flag.
            desc = json.loads(page.tags["ImageDescription"].value)
            assert desc["bayer_pattern"] == "RG"
            assert "flipped_lr" not in desc


def test_write_tiff_bayer_u16_flip_lr_updates_pattern_and_pixels() -> None:
    arr = np.arange(64 * 96, dtype=np.uint16).reshape(64, 96)
    meta = {
        "camera_serial": "0001",
        "project": "p", "line": 1,
        "sweep": 1, "frame_in_sweep": 1,
        "bayer_pattern": "RG",
        "genicam_pixel_format": "BayerRG14",
        "bit_depth": 14,
        "container": "bay14line_v1",
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "bayer_flipped.tif"
        write_tiff_bayer_u16(path, arr, meta, scale_14_to_16=True, flip_lr=True)

        with tifffile.TiffFile(str(path)) as tf:
            page = tf.pages[0]
            out_arr = page.asarray()
            # First row of the original was 0..95 (then <<2 -> 0,4,8,...).
            # After fliplr the first element should be the last of the
            # original row.
            assert int(out_arr[0, 0]) == (95 << 2)
            assert int(out_arr[0, -1]) == 0
            desc = json.loads(page.tags["ImageDescription"].value)
            assert desc["bayer_pattern"] == "GR"
            assert desc["genicam_pixel_format"] == "BayerGR14"
            assert desc["flipped_lr"] is True


def test_encode_tiff_rgb16_emits_extratags_and_flip_when_requested() -> None:
    # Small RG-pattern Bayer mosaic so cv2.cvtColor doesn't choke.
    bayer = np.zeros((8, 8), dtype=np.uint16)
    bayer[0::2, 0::2] = 4000  # R (after 14->16 shift below it becomes 16000)
    bayer[0::2, 1::2] = 2000  # G
    bayer[1::2, 0::2] = 2000  # G
    bayer[1::2, 1::2] = 100   # B
    meta = {
        "camera_serial": "0001",
        "project": "p", "line": 1,
        "sweep": 1, "frame_in_sweep": 1,
        "bayer_pattern": "RG",
        "genicam_pixel_format": "BayerRG14",
        "bit_depth": 14,
        "container": "bay14line_v1",
    }
    with tempfile.TemporaryDirectory() as tmp:
        path_a = Path(tmp) / "rgb.tif"
        path_b = Path(tmp) / "rgb_flipped.tif"
        encode_tiff_rgb16(path_a, bayer, meta)
        encode_tiff_rgb16(path_b, bayer, meta, flip_lr=True)

        with tifffile.TiffFile(str(path_a)) as tf:
            page_a = tf.pages[0]
            arr_a = page_a.asarray()
            assert arr_a.shape == (8, 8, 3)
            # Structural tags including PlanarConfiguration for 3-sample
            # RGB (Chunky=1, Planar=2 -- we always emit Chunky).
            assert page_a.tags["SamplesPerPixel"].value == 3
            assert page_a.tags["PlanarConfiguration"].value == 1
            assert page_a.tags["BitsPerSample"].value == (16, 16, 16)
            assert page_a.tags["Compression"].value == 1
            # Extratags present.
            assert page_a.tags[40962].value == 8
            assert page_a.tags[40963].value == 8
            assert page_a.tags.get(306) is not None
            desc_a = json.loads(page_a.tags["ImageDescription"].value)
            assert "flipped_lr" not in desc_a

        with tifffile.TiffFile(str(path_b)) as tf:
            page_b = tf.pages[0]
            arr_b = page_b.asarray()
            # Horizontal flip is observable on the RGB.
            assert (arr_b == arr_a[:, ::-1, :]).all()
            desc_b = json.loads(page_b.tags["ImageDescription"].value)
            # After demosaic we just stamp the audit flag; pattern label
            # is no longer meaningful in an RGB TIFF.
            assert desc_b["flipped_lr"] is True


def _make_tiff_with_gps_json(
    path: Path,
    *,
    lat: float,
    lon: float,
    alt: float,
) -> dict:
    """Write a tiny TIFF whose ImageDescription JSON carries a ``gps`` block.

    Mirrors the schema Bay14Reader's encoders write so the migrator under
    test sees production-shaped metadata.
    """
    arr = np.zeros((4, 4), dtype=np.uint16)
    payload = {
        "camera_serial": "0001",
        "project": "p",
        "line": 1,
        "sweep": 1,
        "frame_in_sweep": 1,
        "bayer_pattern": "RG",
        "bit_depth": 14,
        "acquire_timestamp": "2026-05-25T14:45:00.437+03:00",
        "gps": {
            "latitude_deg": lat,
            "longitude_deg": lon,
            "altitude_m": alt,
            "timestamp_utc": "2026-05-25T11:44:53.108+00:00",
            "fix_quality": 1,
        },
    }
    tifffile.imwrite(
        str(path),
        arr,
        photometric="minisblack",
        compression=None,
        bigtiff=False,
        description=json.dumps(payload, separators=(",", ":")),
    )
    return payload


def _read_gps_subifd(path: Path) -> dict:
    """Self-contained EXIF GPS sub-IFD reader (no tifffile/exiftool needed).

    Follows IFD0 tag 34853 to the GPS sub-IFD and decodes each entry into
    raw bytes (BYTE/ASCII) or a list of ``(num, den)`` tuples (RATIONAL).
    Returns ``{}`` if the file has no GPSIFD pointer.
    """
    import struct

    data = Path(path).read_bytes()
    bo = "<" if data[:2] == b"II" else ">"
    ifd0 = struct.unpack_from(bo + "I", data, 4)[0]
    n = struct.unpack_from(bo + "H", data, ifd0)[0]
    gps_off = None
    for i in range(n):
        base = ifd0 + 2 + i * 12
        tag = struct.unpack_from(bo + "H", data, base)[0]
        if tag == 34853:
            gps_off = struct.unpack_from(bo + "I", data, base + 8)[0]
            break
    if not gps_off:
        return {}
    gn = struct.unpack_from(bo + "H", data, gps_off)[0]
    out: dict = {}
    for i in range(gn):
        base = gps_off + 2 + i * 12
        tag, typ, count = struct.unpack_from(bo + "HHI", data, base)
        val_at = base + 8
        if typ == 5:  # RATIONAL
            off = struct.unpack_from(bo + "I", data, val_at)[0]
            out[tag] = [
                struct.unpack_from(bo + "II", data, off + 8 * k)
                for k in range(count)
            ]
        elif typ in (1, 2):  # BYTE / ASCII
            if count <= 4:
                raw = data[val_at : val_at + count]
            else:
                off = struct.unpack_from(bo + "I", data, val_at)[0]
                raw = data[off : off + count]
            out[tag] = raw
    return out


def _dms_to_deg(rats: list) -> float:
    d = rats[0][0] / rats[0][1]
    m = rats[1][0] / rats[1][1]
    s = rats[2][0] / rats[2][1]
    return d + m / 60.0 + s / 3600.0


def test_write_tiff_bayer_u16_embeds_real_gps_subifd() -> None:
    """A gps block in meta must produce a canonical EXIF GPS sub-IFD
    (IFD0 tag 34853 -> sub-IFD with GPSLatitude/Longitude/Altitude/...),
    decodable without ExifTool."""
    arr = np.arange(32 * 48, dtype=np.uint16).reshape(32, 48)
    meta = {
        "camera_serial": "2439007",
        "project": "p", "line": 14,
        "sweep": 10, "frame_in_sweep": 10,
        "bayer_pattern": "RG",
        "genicam_pixel_format": "BayerRG14",
        "bit_depth": 14,
        "container": "bay14line_v1",
        "gps": {
            "latitude_deg": 32.03295228686263,
            "longitude_deg": 34.74615704309357,
            "altitude_m": 3784.064,
            "timestamp_utc": "2026-05-25T11:44:53.108+00:00",
            "fix_quality": 1,
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "frame.tif"
        write_tiff_bayer_u16(path, arr, meta, scale_14_to_16=True)

        # tifffile can still open the file (pixels + IFD0 intact).
        with tifffile.TiffFile(str(path)) as tf:
            assert tf.pages[0].asarray().shape == (32, 48)

        gps = _read_gps_subifd(path)
        assert gps, "no GPS sub-IFD was embedded"
        # Version + refs.
        assert gps[0] == b"\x02\x03\x00\x00"
        assert gps[1].rstrip(b"\x00") == b"N"
        assert gps[3].rstrip(b"\x00") == b"E"
        # Lat / lon decode within ~1e-5 deg of source.
        assert abs(_dms_to_deg(gps[2]) - 32.03295228686263) < 1e-5
        assert abs(_dms_to_deg(gps[4]) - 34.74615704309357) < 1e-5
        # Altitude rational num/den.
        alt = gps[6][0][0] / gps[6][0][1]
        assert abs(alt - 3784.064) < 1e-3
        # Date stamp ASCII.
        assert gps[29].rstrip(b"\x00") == b"2026:05:25"


def test_encode_tiff_rgb16_embeds_real_gps_subifd() -> None:
    bayer = np.zeros((8, 8), dtype=np.uint16)
    bayer[0::2, 0::2] = 4000
    bayer[0::2, 1::2] = 2000
    bayer[1::2, 0::2] = 2000
    bayer[1::2, 1::2] = 100
    meta = {
        "camera_serial": "0001", "project": "p", "line": 1,
        "sweep": 1, "frame_in_sweep": 1,
        "bayer_pattern": "RG", "genicam_pixel_format": "BayerRG14",
        "bit_depth": 14, "container": "bay14line_v1",
        "gps": {
            "latitude_deg": -12.5, "longitude_deg": -77.0,
            "altitude_m": 100.0,
            "timestamp_utc": "2026-05-25T11:44:53.108+00:00",
            "fix_quality": 1,
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "rgb.tif"
        encode_tiff_rgb16(path, bayer, meta)
        with tifffile.TiffFile(str(path)) as tf:
            assert tf.pages[0].asarray().shape == (8, 8, 3)
        gps = _read_gps_subifd(path)
        assert gps
        # Southern / western hemisphere refs.
        assert gps[1].rstrip(b"\x00") == b"S"
        assert gps[3].rstrip(b"\x00") == b"W"
        assert abs(_dms_to_deg(gps[2]) - 12.5) < 1e-5
        assert abs(_dms_to_deg(gps[4]) - 77.0) < 1e-5


def test_write_tiff_bayer_u16_no_gps_has_no_subifd() -> None:
    """No gps block -> no reserved pointer and no sub-IFD."""
    arr = np.zeros((8, 8), dtype=np.uint16)
    meta = {
        "camera_serial": "0001", "project": "p", "line": 1,
        "sweep": 1, "frame_in_sweep": 1, "bayer_pattern": "RG",
        "bit_depth": 14, "container": "bay14line_v1",
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "nogps.tif"
        write_tiff_bayer_u16(path, arr, meta, scale_14_to_16=True)
        assert _read_gps_subifd(path) == {}


def _mock_exiftool_batch_read_from_filelist(argv, **kwargs):  # noqa: ANN001
    """Fake ExifTool ``-json -ImageDescription -@ list`` for unit tests."""
    out = kwargs.get("stdout")
    if out is None:
        return type("_R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    # Argfile path is the token immediately after a standalone ``-@``.
    at_idx = argv.index("-@")
    list_path = Path(argv[at_idx + 1])
    entries = []
    for line in list_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        p = Path(line)
        with tifffile.TiffFile(str(p)) as tf:
            desc = tf.pages[0].tags["ImageDescription"].value
        entries.append({"SourceFile": str(p.resolve()), "ImageDescription": desc})
    out.write(json.dumps(entries).encode("utf-8"))
    return type("_R", (), {"returncode": 0, "stdout": "", "stderr": b""})()


def test_apply_exif_gps_from_image_description_unit_argv_and_counts(
    monkeypatch,
) -> None:
    """Batch migrator: one metadata read + one CSV import; stats and CSV row."""
    sys.path.insert(0, str(HERE.parent / "host"))
    from capture import tiff_writer as tw  # noqa: WPS433

    captured: list[list[str]] = []
    csv_rows: list[dict[str, str]] = []

    def _fake_run(argv, **kwargs):  # noqa: ANN001
        captured.append(list(argv))
        if kwargs.get("stdout") is not None:
            return _mock_exiftool_batch_read_from_filelist(argv, **kwargs)
        csv_arg = next((a for a in argv if a.startswith("-csv=")), None)
        if csv_arg is not None:
            with Path(csv_arg[5:]).open(encoding="utf-8") as fcsv:
                csv_rows.extend(csv.DictReader(fcsv))
        return type("_R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(tw.subprocess, "run", _fake_run)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        good = root / "Cam0001" / "good.tif"
        good.parent.mkdir(parents=True)
        _make_tiff_with_gps_json(good, lat=32.5, lon=34.7, alt=3000.0)

        nogps = root / "Cam0001" / "nogps.tif"
        arr = np.zeros((4, 4), dtype=np.uint16)
        tifffile.imwrite(
            str(nogps),
            arr,
            photometric="minisblack",
            description=json.dumps({"line": 1}),
        )

        stats = tw.apply_exif_gps_from_image_description(
            root, exiftool_exe=Path("exiftool.exe"),
        )

    assert stats.scanned == 2
    assert stats.written == 1
    assert stats.missing_gps == 1
    assert stats.failed == 0
    assert len(captured) == 2
    assert "-fast" in captured[0] and "-json" in captured[0]
    assert any(a.startswith("-csv=") for a in captured[1])
    assert len(csv_rows) == 1
    assert csv_rows[0]["GPSLatitudeRef"] == "N"
    assert csv_rows[0]["GPSLongitudeRef"] == "E"
    assert float(csv_rows[0]["GPSLatitude"]) == 32.5
    assert float(csv_rows[0]["GPSLongitude"]) == 34.7
    assert csv_rows[0]["GPSAltitude"] == "3000.000"
    assert csv_rows[0]["GPSDateStamp"] == "2026:05:25"
    assert csv_rows[0]["GPSTimeStamp"].startswith("11:44:53.")


def test_apply_exif_gps_records_subprocess_failure(monkeypatch) -> None:
    """CSV import failure must be recorded in stats but not raise."""
    sys.path.insert(0, str(HERE.parent / "host"))
    import subprocess as _sp

    from capture import tiff_writer as tw  # noqa: WPS433

    def _fake_run(argv, **kwargs):  # noqa: ANN001
        if kwargs.get("stdout") is not None:
            return _mock_exiftool_batch_read_from_filelist(argv, **kwargs)
        if any(a.startswith("-csv=") for a in argv):
            raise _sp.CalledProcessError(
                2, argv, output="", stderr="Error: simulated\n",
            )
        return type("_R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(tw.subprocess, "run", _fake_run)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bad = root / "bad.tif"
        _make_tiff_with_gps_json(bad, lat=32.0, lon=34.0, alt=10.0)
        stats = tw.apply_exif_gps_from_image_description(
            root, exiftool_exe=Path("exiftool.exe"),
        )

    assert stats.scanned == 1
    assert stats.failed == 1
    assert stats.written == 0
    assert stats.failures and stats.failures[0][0].name == "bad.tif"


def test_apply_exif_gps_real_exiftool_round_trip() -> None:
    """If ExifTool is installed, do a real round-trip: write a TIFF with
    a gps JSON block, run the migrator, then assert ExifTool can read the
    canonical EXIF GPS tags back from a proper GPS sub-IFD (not from IFD0).
    """
    sys.path.insert(0, str(HERE.parent / "host"))
    from capture import tiff_writer as tw  # noqa: WPS433

    exiftool_exe = tw.detect_exiftool()
    if exiftool_exe is None:
        return  # nothing installed; soft-skip so the suite still passes.

    import subprocess as _sp

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tif = root / "Cam0001" / "frame.tif"
        tif.parent.mkdir(parents=True)
        _make_tiff_with_gps_json(tif, lat=32.5, lon=34.7, alt=3000.0)

        stats = tw.apply_exif_gps_from_image_description(
            root, exiftool_exe=exiftool_exe,
        )
        assert stats.written == 1, stats

        # -gps:all walks the dedicated GPS sub-IFD only (not IFD0, not the
        # synthetic Composite group). If our fix lands the tags in the
        # wrong IFD the output here would be empty.
        completed = _sp.run(
            [
                str(exiftool_exe), "-G1", "-n", "-s", "-gps:all",
                str(tif),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        out = completed.stdout
        assert "[GPS]" in out, out
        assert "GPSLatitudeRef" in out, out
        assert "GPSLongitudeRef" in out, out
        assert "GPSAltitude" in out, out


def main() -> int:
    tests = [v for k, v in globals().items()
             if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {fn.__name__}: {exc!r}")
    print(f"\n{len(tests) - failed} / {len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
