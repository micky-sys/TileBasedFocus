"""Unit tests for per-frame camera yaw/pitch/roll geometry."""

from __future__ import annotations

import math
import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "host"))
sys.path.insert(0, ROOT)

from capture.attitude import (  # noqa: E402
    SERIAL_NOSE_OBLIQUE,
    SERIAL_TAIL_OBLIQUE,
    SERIAL_VERTICAL,
    SWEEP_FRAMES,
    camera_attitude_body,
    look_direction_body,
    mirror_angle_for_frame,
    yaw_pitch_roll,
)
from capture.tiff_writer import _exiftool_camera_attitude_argv  # noqa: E402


class MirrorScheduleTests(unittest.TestCase):
    def test_oblique_mirror_endpoints(self) -> None:
        self.assertAlmostEqual(
            mirror_angle_for_frame(SERIAL_NOSE_OBLIQUE, 1), -25.0, places=6
        )
        self.assertAlmostEqual(
            mirror_angle_for_frame(SERIAL_NOSE_OBLIQUE, 13), 25.0, places=6
        )
        self.assertAlmostEqual(
            mirror_angle_for_frame(SERIAL_NOSE_OBLIQUE, 7), 0.0, places=6
        )

    def test_vertical_tilt_endpoints(self) -> None:
        self.assertAlmostEqual(
            mirror_angle_for_frame(SERIAL_VERTICAL, 1), -30.0, places=6
        )
        self.assertAlmostEqual(
            mirror_angle_for_frame(SERIAL_VERTICAL, 13), 30.0, places=6
        )
        self.assertAlmostEqual(
            mirror_angle_for_frame(SERIAL_VERTICAL, 7), 0.0, places=6
        )


class LookDirectionTests(unittest.TestCase):
    def test_vertical_nadir_at_mid(self) -> None:
        d = look_direction_body(SERIAL_VERTICAL, 7)
        np.testing.assert_allclose(d, [0.0, 0.0, -1.0], atol=1e-9)

    def test_vertical_tilt_frame_1(self) -> None:
        d = look_direction_body(SERIAL_VERTICAL, 1)
        self.assertAlmostEqual(d[2], -math.cos(math.radians(30)), places=6)
        self.assertAlmostEqual(d[1], math.sin(math.radians(-30)), places=6)

    def test_nose_oblique_forward_at_mid(self) -> None:
        d = look_direction_body(SERIAL_NOSE_OBLIQUE, 7)
        c = math.cos(math.radians(45))
        s = math.sin(math.radians(45))
        np.testing.assert_allclose(d, [s, 0.0, -c], atol=1e-9)

    def test_tail_oblique_aft_at_mid(self) -> None:
        d = look_direction_body(SERIAL_TAIL_OBLIQUE, 7)
        c = math.cos(math.radians(45))
        s = math.sin(math.radians(45))
        np.testing.assert_allclose(d, [-s, 0.0, -c], atol=1e-9)

    def test_oblique_azimuth_sweep_span(self) -> None:
        d1 = look_direction_body(SERIAL_NOSE_OBLIQUE, 1)
        d13 = look_direction_body(SERIAL_NOSE_OBLIQUE, 13)
        # Horizontal projection should differ by ~100 deg beam swing.
        def horiz_az(d):
            return math.degrees(math.atan2(d[1], d[0]))
        gap = abs(horiz_az(d13) - horiz_az(d1))
        if gap > 180:
            gap = 360 - gap
        self.assertGreater(gap, 90.0)


class YprTests(unittest.TestCase):
    def test_vertical_nadir_pitch_with_heading(self) -> None:
        att = yaw_pitch_roll(SERIAL_VERTICAL, 7, heading_deg=90.0)
        self.assertAlmostEqual(att.pitch_deg, -90.0, delta=2.0)
        self.assertEqual(att.heading_source, "gps_bearing_running_avg")

    def test_heading_rotates_yaw(self) -> None:
        a0 = yaw_pitch_roll(SERIAL_NOSE_OBLIQUE, 7, heading_deg=0.0)
        a90 = yaw_pitch_roll(SERIAL_NOSE_OBLIQUE, 7, heading_deg=90.0)
        self.assertNotAlmostEqual(a0.yaw_deg, a90.yaw_deg, delta=5.0)

    def test_body_frame_without_heading(self) -> None:
        att = yaw_pitch_roll(SERIAL_NOSE_OBLIQUE, 7, heading_deg=None)
        self.assertIsNone(att.heading_deg)
        self.assertEqual(att.heading_source, "body_frame")

    def test_oblique_reflection_det_negative(self) -> None:
        R = camera_attitude_body(SERIAL_NOSE_OBLIQUE, 7)
        self.assertAlmostEqual(np.linalg.det(R), -1.0, places=6)

    def test_vertical_no_reflection_flip(self) -> None:
        R = camera_attitude_body(SERIAL_VERTICAL, 7)
        self.assertAlmostEqual(abs(np.linalg.det(R)), 1.0, places=6)


class ExiftoolArgvTests(unittest.TestCase):
    def test_camera_xmp_argv(self) -> None:
        from pathlib import Path

        argv = _exiftool_camera_attitude_argv(
            Path("x.tif"),
            {"yaw_deg": 10.0, "pitch_deg": -45.0, "roll_deg": 2.5},
            exiftool_exe=Path("exiftool.exe"),
        )
        self.assertIn("-XMP-Camera:Yaw=10.000000", argv)
        self.assertIn("-XMP-Camera:Pitch=-45.000000", argv)
        self.assertIn("-XMP-Camera:Roll=2.500000", argv)


if __name__ == "__main__":
    unittest.main(verbosity=2)
