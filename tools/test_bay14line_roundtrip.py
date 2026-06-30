"""End-to-end .bay14line round-trip self-test.

Builds N synthetic uint16 frames, writes them through LineWriter
with the production blosc2 SHUFFLE+ZSTD codec, then reads them back
via the file header + appended index and compares pixel-perfect.

Also exercises the recovery path (close without writing the index)
and confirms recover_line_chunks() walks the file.
"""

from __future__ import annotations

import os
import sys
import tempfile

import numpy as np

# Ensure host/ is on sys.path so `capture` imports work like in the app.
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "host"))
sys.path.insert(0, ROOT)

from capture.raw_bay_writer import (  # noqa: E402
    COMPRESSION_BLOSC2_ZSTD1_SHUFFLE,
    COMPRESSION_NONE,
    LINE_INDEX_ENTRY_SIZE,
    LINE_PIXEL_FORMAT_PACKED_U14,
    LineWriter,
    compress_frame_blosc2,
    decompress_frame_blosc2,
    pack_u14,
    parse_line_header,
    parse_line_header_prefix,
    parse_line_index,
    recover_line_chunks,
    unpack_u14,
)


# Smaller geometry so the test runs in seconds, not a minute.
H, W = 952, 1340  # divisible by 4 (needed for any pack path)
N_FRAMES = 7  # > 1 sweep; emulate mid-line file


def make_frames(n: int) -> list[np.ndarray]:
    rng = np.random.default_rng(0)
    frames = []
    for i in range(n):
        coarse = rng.integers(0, 1 << 12, size=(H // 16 + 1, W // 16 + 1),
                              dtype=np.uint16)
        base = np.repeat(np.repeat(coarse, 16, axis=0), 16, axis=1)[:H, :W]
        noise = rng.integers(0, 1 << 9, size=(H, W), dtype=np.uint16)
        frame = ((base + noise) & 0x3FFF).astype(np.uint16)
        frames.append(np.ascontiguousarray(frame))
    return frames


def test_clean_close():
    print("=== test_clean_close ===")
    frames = make_frames(N_FRAMES)

    line_meta = {
        "camera_serial": "1234567",
        "project": "RoundTrip",
        "line": 7,
        "capture_kind": "sweep",
        "line_started_at": "2026-05-06T12:00:00",
    }

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "test_clean.bay14line")
        w = LineWriter(
            path, height=H, width=W,
            line_meta=line_meta,
            codec=COMPRESSION_BLOSC2_ZSTD1_SHUFFLE,
        )
        w.open()
        for i, fr in enumerate(frames):
            payload = compress_frame_blosc2(fr)
            w.append_chunk(
                payload,
                sweep=(i // 13) + 1,
                frame_in_sweep=(i % 13) + 1,
                frame_meta={
                    "acquire_timestamp": f"2026-05-06T12:00:{i:02d}",
                    "capture_kind": "sweep",
                },
                gps={"lat": 32.0 + i * 1e-6, "lon": 35.0, "alt": 100.0},
            )
        stats = w.close_with_index(fsync=False)
        print(f"  wrote {stats['n_frames']} frames, {stats['payload_total']:,} payload bytes, "
              f"{stats['wall_s'] * 1000:.0f} ms wall")

        # Reopen and verify.
        with open(path, "rb") as f:
            full = f.read()
        meta, header_len, n_frames, idx_off = parse_line_header(full)
        assert n_frames == N_FRAMES, (n_frames, N_FRAMES)
        assert idx_off > 0
        assert meta["compression"] == COMPRESSION_BLOSC2_ZSTD1_SHUFFLE
        assert meta["width"] == W and meta["height"] == H
        assert meta["camera_serial"] == "1234567"
        assert meta["line"] == 7

        idx_blob = full[idx_off:idx_off + n_frames * LINE_INDEX_ENTRY_SIZE]
        index = parse_line_index(idx_blob, n_frames)
        assert len(index) == N_FRAMES

        for i, (sweep, f_in, off, plen, unc) in enumerate(index):
            assert sweep == (i // 13) + 1
            assert f_in == (i % 13) + 1
            assert unc == H * W * 2
            payload = full[off + 16:off + 16 + plen]
            chunk_hdr_len = int.from_bytes(full[off + 8:off + 12], "little")
            payload = full[off + chunk_hdr_len:off + chunk_hdr_len + plen]
            decoded = decompress_frame_blosc2(payload, height=H, width=W)
            assert np.array_equal(decoded, frames[i]), (
                f"frame {i} mismatch"
            )
        print(f"  OK: round-tripped {N_FRAMES} frames pixel-perfect; "
              f"file_size={len(full):,} B")


def test_recovery_after_abort():
    print("=== test_recovery_after_abort ===")
    frames = make_frames(N_FRAMES)
    line_meta = {"camera_serial": "9", "project": "Abort", "line": 1,
                 "capture_kind": "sweep", "line_started_at": "x"}

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "test_abort.bay14line")
        w = LineWriter(path, height=H, width=W,
                       line_meta=line_meta,
                       codec=COMPRESSION_BLOSC2_ZSTD1_SHUFFLE)
        w.open()
        for i, fr in enumerate(frames):
            payload = compress_frame_blosc2(fr)
            w.append_chunk(payload,
                           sweep=1, frame_in_sweep=i + 1,
                           frame_meta={"acquire_timestamp": "x",
                                       "capture_kind": "sweep"})
        # Simulate kill -9: drop the writer without close_with_index.
        w.abort()

        with open(path, "rb") as f:
            prefix = f.read(32)
        _, n_frames_field, idx_off, _ = parse_line_header_prefix(prefix)
        assert n_frames_field == 0, n_frames_field
        assert idx_off == 0, idx_off

        meta, recovered = recover_line_chunks(path)
        assert len(recovered) == N_FRAMES, (len(recovered), N_FRAMES)
        assert meta["line"] == 1
        for i, (sweep, f_in, off, plen, unc) in enumerate(recovered):
            assert sweep == 1
            assert f_in == i + 1
            assert unc == H * W * 2
            with open(path, "rb") as f:
                f.seek(off)
                head_prefix = f.read(16)
                hdr_len = int.from_bytes(head_prefix[8:12], "little")
                f.seek(off + hdr_len)
                payload = f.read(plen)
            decoded = decompress_frame_blosc2(payload, height=H, width=W)
            assert np.array_equal(decoded, frames[i])
        print(f"  OK: recovered {len(recovered)} chunks via BAY14FRM walk")


def test_codec_none_packed_u14():
    """``codec=none`` MUST write packed-u14 chunks and decode back pixel-perfect.

    This guards the writer-side change that pairs ``codec=none`` with
    packed-14 on disk (the offline reader uses the file header's
    ``format`` field to pick the unpack path; if the writer and reader
    drift on what ``codec=none`` means, every frame decodes as garbage).
    """
    print("=== test_codec_none_packed_u14 ===")
    frames = make_frames(N_FRAMES)
    line_meta = {"camera_serial": "5", "project": "PackedU14",
                 "line": 2, "capture_kind": "sweep",
                 "line_started_at": "x"}

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "test_none.bay14line")
        w = LineWriter(path, height=H, width=W,
                       line_meta=line_meta,
                       codec=COMPRESSION_NONE)
        w.open()
        for i, fr in enumerate(frames):
            payload = pack_u14(fr).tobytes()
            w.append_chunk(
                payload,
                sweep=(i // 13) + 1,
                frame_in_sweep=(i % 13) + 1,
                frame_meta={"acquire_timestamp": f"t{i}",
                            "capture_kind": "sweep"},
            )
        stats = w.close_with_index(fsync=False)
        print(f"  wrote {stats['n_frames']} frames, "
              f"{stats['payload_total']:,} payload bytes")

        with open(path, "rb") as f:
            full = f.read()
        meta, _header_len, n_frames, idx_off = parse_line_header(full)
        assert meta["compression"] == COMPRESSION_NONE, meta["compression"]
        assert meta["format"] == LINE_PIXEL_FORMAT_PACKED_U14, meta["format"]
        idx_blob = full[idx_off:idx_off + n_frames * LINE_INDEX_ENTRY_SIZE]
        index = parse_line_index(idx_blob, n_frames)
        for i, (_sw, _fi, off, plen, unc) in enumerate(index):
            chunk_hdr_len = int.from_bytes(full[off + 8:off + 12], "little")
            packed = full[off + chunk_hdr_len:off + chunk_hdr_len + plen]
            arr = np.frombuffer(packed, dtype=np.uint8)
            assert arr.size == H * W * 7 // 4, (arr.size, H * W * 7 // 4)
            assert unc == H * W * 2, (unc, H * W * 2)
            decoded = unpack_u14(arr, H * W).reshape(H, W)
            assert np.array_equal(decoded, frames[i]), (
                f"packed-u14 round-trip mismatch on frame {i}"
            )
        print(f"  OK: {n_frames} frames round-tripped via packed-u14")


def test_prealloc_truncates_to_actual_size():
    """LineWriter must shrink the file back to actual size on close
    when ``prealloc_bytes`` reserved more than the content uses, so
    we don't leak the over-reservation onto disk after a short line.
    """
    print("=== test_prealloc_truncates_to_actual_size ===")
    frames = make_frames(N_FRAMES)
    line_meta = {"camera_serial": "6", "project": "Prealloc",
                 "line": 3, "capture_kind": "sweep",
                 "line_started_at": "x"}

    prealloc = 256 * 1024 * 1024  # 256 MiB; bigger than any test payload

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "test_prealloc.bay14line")
        w = LineWriter(path, height=H, width=W,
                       line_meta=line_meta,
                       codec=COMPRESSION_NONE,
                       prealloc_bytes=prealloc)
        w.open()
        for i, fr in enumerate(frames):
            payload = pack_u14(fr).tobytes()
            w.append_chunk(payload,
                           sweep=1, frame_in_sweep=i + 1,
                           frame_meta={"acquire_timestamp": "x",
                                       "capture_kind": "sweep"})
        stats = w.close_with_index(fsync=False)

        actual = os.path.getsize(path)
        expected = (stats["header_size"] + stats["payload_total"]
                    + N_FRAMES * 16  # per-chunk header magic+sizes
                    )
        # Per-chunk header layout includes the JSON meta block; we can't
        # compute its byte size cheaply, so just assert the file shrank
        # well below prealloc and matches the writer's reported book-
        # keeping (header + payload + index + per-chunk headers).
        assert actual < prealloc, (actual, prealloc)
        assert actual >= expected, (actual, expected)
        print(f"  OK: prealloc={prealloc:,} B, final file size="
              f"{actual:,} B (writer truncated unused tail)")

        # Round-trip a couple of frames to confirm the truncate didn't
        # touch anything inside the index range.
        with open(path, "rb") as f:
            full = f.read()
        meta, _hl, n_frames, idx_off = parse_line_header(full)
        assert meta["compression"] == COMPRESSION_NONE
        idx_blob = full[idx_off:idx_off + n_frames * LINE_INDEX_ENTRY_SIZE]
        index = parse_line_index(idx_blob, n_frames)
        assert len(index) == N_FRAMES
        sw, fi, off, plen, unc = index[0]
        chunk_hdr_len = int.from_bytes(full[off + 8:off + 12], "little")
        packed = full[off + chunk_hdr_len:off + chunk_hdr_len + plen]
        arr = np.frombuffer(packed, dtype=np.uint8)
        decoded = unpack_u14(arr, H * W).reshape(H, W)
        assert np.array_equal(decoded, frames[0])
        print("  OK: first frame decoded correctly after prealloc+truncate")


def test_direct_io_packed_u14_roundtrip():
    """Direct-I/O writer must produce a file that:
      * is sector-aligned (size % 4096 == 0 until close-time truncate)
      * has every chunk on a sector boundary
      * decodes pixel-perfect via the index path
      * walks correctly via recover_line_chunks() (no index)
    Skipped on non-Windows where direct I/O is unavailable.
    """
    if sys.platform != "win32":
        print("=== test_direct_io_packed_u14_roundtrip === SKIPPED (non-Windows)")
        return
    print("=== test_direct_io_packed_u14_roundtrip ===")
    frames = make_frames(N_FRAMES)
    line_meta = {"camera_serial": "7", "project": "DirectIO",
                 "line": 4, "capture_kind": "sweep",
                 "line_started_at": "x"}

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "test_direct.bay14line")
        w = LineWriter(
            path, height=H, width=W,
            line_meta=line_meta,
            codec=COMPRESSION_NONE,
            prealloc_bytes=128 * 1024 * 1024,  # 128 MiB
            direct_io=True,
        )
        w.open()
        # Hot path: io_writer passes a sector-aligned view; the slot is
        # always at least padded_payload_size bytes long.
        for i, fr in enumerate(frames):
            packed = pack_u14(fr).tobytes()
            padded_len = ((len(packed) + 4095) // 4096) * 4096
            buf = bytearray(padded_len)  # fresh padded scratch (zeros after)
            buf[:len(packed)] = packed
            w.append_chunk(
                buf,
                sweep=(i // 13) + 1,
                frame_in_sweep=(i % 13) + 1,
                frame_meta={"acquire_timestamp": f"t{i}",
                            "capture_kind": "sweep"},
                payload_view_size=len(packed),
            )
        stats = w.close_with_index(fsync=False)
        print(f"  wrote {stats['n_frames']} frames, "
              f"{stats['payload_total']:,} payload bytes, "
              f"alignment=4096")

        with open(path, "rb") as f:
            full = f.read()
        meta, _hl, n_frames, idx_off = parse_line_header(full)
        assert meta["compression"] == COMPRESSION_NONE
        assert meta["format"] == LINE_PIXEL_FORMAT_PACKED_U14
        assert int(meta["chunk_alignment"]) == 4096, meta.get("chunk_alignment")
        idx_blob = full[idx_off:idx_off + n_frames * LINE_INDEX_ENTRY_SIZE]
        index = parse_line_index(idx_blob, n_frames)
        assert len(index) == N_FRAMES

        for i, (_sw, _fi, off, plen, unc) in enumerate(index):
            # Every chunk header starts on a sector boundary.
            assert off % 4096 == 0, (off, "chunk offset must be sector-aligned")
            chunk_hdr_len = int.from_bytes(full[off + 8:off + 12], "little")
            # Chunk header is padded to one sector.
            assert chunk_hdr_len == 4096, chunk_hdr_len
            packed = full[off + chunk_hdr_len:off + chunk_hdr_len + plen]
            arr = np.frombuffer(packed, dtype=np.uint8)
            decoded = unpack_u14(arr, H * W).reshape(H, W)
            assert np.array_equal(decoded, frames[i]), (
                f"direct-I/O round-trip mismatch on frame {i}"
            )
        print(f"  OK: {n_frames} frames round-tripped via direct-I/O packed-u14")

        # Recovery walk must also work (covers the dropped-index case).
        meta2, recovered = recover_line_chunks(path)
        assert int(meta2["chunk_alignment"]) == 4096
        assert len(recovered) == N_FRAMES, (len(recovered), N_FRAMES)
        for i, (_sw, _fi, off, plen, unc) in enumerate(recovered):
            assert off == index[i][2], (off, index[i][2])
            assert plen == index[i][3], (plen, index[i][3])
        print(f"  OK: recover_line_chunks walked {len(recovered)} chunks via padded BAY14FRM scan")


def test_attitude_chunk_roundtrip():
    """Attitude block written into chunk JSON survives parse_line walk."""
    print("=== test_attitude_chunk_roundtrip ===")
    from capture.attitude import compute_attitude_dict  # noqa: E402
    from capture.raw_bay_writer import parse_frame_chunk_header  # noqa: E402

    frames = make_frames(3)
    attitude = compute_attitude_dict("2439008", 7, heading_deg=45.0)
    line_meta = {
        "camera_serial": "2439008",
        "project": "AttitudeRT",
        "line": 1,
        "capture_kind": "sweep",
        "line_started_at": "2026-06-04T12:00:00",
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "att.bay14line")
        w = LineWriter(
            path, height=H, width=W,
            line_meta=line_meta,
            codec=COMPRESSION_BLOSC2_ZSTD1_SHUFFLE,
        )
        w.open()
        for i, fr in enumerate(frames):
            payload = compress_frame_blosc2(fr)
            w.append_chunk(
                payload,
                sweep=1,
                frame_in_sweep=i + 1,
                frame_meta={"acquire_timestamp": "t", "capture_kind": "sweep"},
                attitude=attitude if i == 0 else None,
            )
        w.close_with_index(fsync=False)
        with open(path, "rb") as f:
            full = f.read()
        _meta, _hl, n_frames, idx_off = parse_line_header(full)
        idx_blob = full[idx_off:idx_off + n_frames * LINE_INDEX_ENTRY_SIZE]
        index = parse_line_index(idx_blob, n_frames)
        off = index[0][2]
        hdr_len = int.from_bytes(full[off + 8:off + 12], "little")
        chunk_meta, _, _ = parse_frame_chunk_header(full[off:off + hdr_len])
        assert "attitude" in chunk_meta
        assert chunk_meta["attitude"]["yaw_deg"] == attitude["yaw_deg"]
        assert chunk_meta["attitude"]["heading_deg"] == 45.0
    print("  OK: attitude block round-tripped in chunk JSON")


def test_gps_exif_roundtrip():
    """``write_tiff_bayer_u16`` + ``encode_tiff_rgb16`` MUST embed the
    per-frame ``gps`` block as EXIF GPS tags so external tools (exiftool,
    image viewers) read the fix without parsing ImageDescription JSON.

    We synthesise a small Bayer frame and a ``merged_meta`` dict that
    looks like what :func:`iter_line_frames` would yield for a chunk
    captured with GPS available, then export to TIFF and assert the
    EXIF GPS tags decode back to the original lat/lon/alt.
    """
    print("=== test_gps_exif_roundtrip ===")
    import json
    import tifffile  # local import: keeps the module light for tests above

    from bay14_export_core import (  # noqa: E402  (added to sys.path below)
        encode_tiff_rgb16,
        write_tiff_bayer_u16,
    )
    from capture.tiff_writer import detect_exiftool  # noqa: E402

    arr = np.zeros((H, W), dtype=np.uint16)
    merged_meta = {
        "camera_serial": "9999999",
        "project": "GpsExifRT",
        "line": 1,
        "sweep": 1,
        "frame_in_sweep": 1,
        "height": H,
        "width": W,
        "format": "uint16_2d",
        "compression": "none",
        "bayer_pattern": "RG",
        "genicam_pixel_format": "BayerRG14",
        "acquire_timestamp": "2026-05-26T12:00:00.000+00:00",
        "gps": {
            "latitude_deg": 32.0853,
            "longitude_deg": 34.7818,
            "altitude_m": 41.2,
            "timestamp_utc": "2026-05-26T12:00:00.123+00:00",
            "satellites": 11,
            "hdop": None,
            "fix_quality": 1,
        },
        "attitude": {
            "yaw_deg": 88.5,
            "pitch_deg": -89.0,
            "roll_deg": 1.3,
            "heading_deg": 88.5,
            "heading_source": "gps_bearing_running_avg",
            "mirror_angle_deg": 0.0,
            "camera_serial": "2439008",
            "frame_in_sweep": 7,
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        bayer_path = os.path.join(tmp, "frame.tif")
        rgb_path = os.path.join(tmp, "frame_rgb.tif")
        write_tiff_bayer_u16(bayer_path, arr, merged_meta, scale_14_to_16=False)
        encode_tiff_rgb16(rgb_path, arr, merged_meta)

        for label, path in (("bayer", bayer_path), ("rgb", rgb_path)):
            with tifffile.TiffFile(path) as tif:
                tags = tif.pages[0].tags
                gps_ifd = tags.get(34853)
                assert gps_ifd is not None, f"{label}: GPS sub-IFD pointer missing"
                gps_tags = gps_ifd.value
                lat_rational = gps_tags["GPSLatitude"]
                lon_rational = gps_tags["GPSLongitude"]

                def _r2deg(r):
                    d = r[0] / r[1]
                    m = r[2] / r[3]
                    s = r[4] / r[5]
                    return d + m / 60.0 + s / 3600.0

                lat = _r2deg(lat_rational)
                lon = _r2deg(lon_rational)
                assert abs(lat - 32.0853) < 1e-5, (label, lat)
                assert abs(lon - 34.7818) < 1e-5, (label, lon)
                desc = tags.get("ImageDescription")
                assert desc is not None
                payload = json.loads(desc.value)
                assert payload["attitude"]["yaw_deg"] == 88.5
            et = detect_exiftool()
            if et is not None:
                import subprocess

                out = subprocess.run(
                    [
                        str(et),
                        "-s", "-s", "-s",
                        "-XMP-Camera:Yaw",
                        "-XMP-Camera:Pitch",
                        "-XMP-Camera:Roll",
                        str(path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                lines = [
                    ln.strip() for ln in (out.stdout or "").splitlines() if ln.strip()
                ]
                if len(lines) >= 3:
                    assert abs(float(lines[0]) - 88.5) < 0.01, (label, lines)
            print(f"  OK: {label}: EXIF GPS sub-IFD + attitude JSON round-trip")


if __name__ == "__main__":
    test_clean_close()
    test_recovery_after_abort()
    test_codec_none_packed_u14()
    test_prealloc_truncates_to_actual_size()
    test_direct_io_packed_u14_roundtrip()
    # Make tools/ importable for bay14_export_core (test runs from repo
    # root or tools/ depending on the invocation; either way, prepend).
    sys.path.insert(0, HERE)
    test_attitude_chunk_roundtrip()
    test_gps_exif_roundtrip()
    print("all .bay14line round-trip tests passed.")
