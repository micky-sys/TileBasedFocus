"""Tests for the offline per-frame attitude tagger (geotag_attitude_exports)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import tifffile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "host"))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from capture.tiff_writer import GpsFix, embed_exif_gps_subifd  # noqa: E402
from geotag_attitude_exports import (  # noqa: E402
    _HeadingTracker,
    parse_export_name,
    process,
    read_gps_from_exif,
)


def _write_tiff_with_gps(path: Path, lat: float, lon: float) -> None:
    arr = np.zeros((16, 16), dtype=np.uint16)
    tifffile.imwrite(str(path), arr, photometric="minisblack", bigtiff=False)
    embed_exif_gps_subifd(path, GpsFix(latitude_deg=lat, longitude_deg=lon, altitude_m=40.0))


class NameParseTests(unittest.TestCase):
    def test_new_scheme(self) -> None:
        d = parse_export_name(
            "Cam2439007_20260525_Proj_144545_L13_S2_F7.tif"
        )
        self.assertIsNotNone(d)
        self.assertEqual(d["camera_serial"], "2439007")
        self.assertEqual(d["line"], "13")
        self.assertEqual(d["sweep"], "2")
        self.assertEqual(d["frame"], "7")

    def test_old_scheme(self) -> None:
        d = parse_export_name(
            "Cam2439006_20260525_Proj_L1_144545_p0_S1_F3.tif"
        )
        self.assertIsNotNone(d)
        self.assertEqual(d["camera_serial"], "2439006")
        self.assertEqual(d["frame"], "3")

    def test_non_match(self) -> None:
        self.assertIsNone(parse_export_name("random.tif"))


class HeadingTrackerTests(unittest.TestCase):
    def test_first_frame_has_no_heading(self) -> None:
        t = _HeadingTracker()
        self.assertIsNone(t.update(32.0, 34.0))

    def test_eastward_track(self) -> None:
        t = _HeadingTracker()
        base_lat, base_lon = 32.0, 34.0
        headings = []
        for i in range(5):
            headings.append(t.update(base_lat, base_lon + i * 0.01))
        self.assertIsNone(headings[0])
        self.assertIsNotNone(headings[-1])
        self.assertGreater(headings[-1], 80.0)
        self.assertLess(headings[-1], 100.0)


class ExifGpsTests(unittest.TestCase):
    def test_roundtrip_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "f.tif"
            _write_tiff_with_gps(p, 32.0853, 34.7818)
            lat, lon = read_gps_from_exif(p)
            self.assertAlmostEqual(lat, 32.0853, places=4)
            self.assertAlmostEqual(lon, 34.7818, places=4)


class ProcessDryRunTests(unittest.TestCase):
    def test_dry_run_sequences_heading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # One vertical-camera line, 13 frames sweeping east.
            for f in range(1, 14):
                p = root / f"Cam2439008_20260604_Proj_120000_L1_S1_F{f}.tif"
                _write_tiff_with_gps(p, 32.0, 34.0 + (f - 1) * 0.001)
            recs, stats = process(
                [root],
                recursive=False,
                write=False,
                skip_no_heading=False,
                csv_path=None,
                exiftool_exe=None,
                min_displacement_m=0.5,
                window=8,
            )
            self.assertEqual(stats.parsed, 13)
            by_frame = {r.frame_in_sweep: r for r in recs}
            # First frame has no heading; all carry YPR regardless.
            self.assertIsNone(by_frame[1].heading_deg)
            self.assertIsNotNone(by_frame[13].heading_deg)
            for r in recs:
                self.assertIsNotNone(r.yaw_deg)
                self.assertIsNotNone(r.pitch_deg)
                self.assertIsNotNone(r.roll_deg)
                self.assertEqual(r.status, "would_write")
            # Mid sweep frame 7 = nadir => pitch near -90.
            self.assertAlmostEqual(by_frame[7].pitch_deg, -90.0, delta=2.0)

    def test_skip_no_heading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for f in range(1, 4):
                p = root / f"Cam2439007_20260604_Proj_120000_L2_S1_F{f}.tif"
                _write_tiff_with_gps(p, 32.0, 34.0 + (f - 1) * 0.001)
            recs, stats = process(
                [root],
                recursive=False,
                write=False,
                skip_no_heading=True,
                csv_path=None,
                exiftool_exe=None,
                min_displacement_m=0.5,
                window=8,
            )
            statuses = {r.frame_in_sweep: r.status for r in recs}
            self.assertEqual(statuses[1], "skipped_no_heading")


if __name__ == "__main__":
    unittest.main(verbosity=2)
