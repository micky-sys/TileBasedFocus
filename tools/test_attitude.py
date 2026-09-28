"""Unit tests for per-frame camera yaw/pitch/roll geometry."""

from __future__ import annotations

import json
import math
import os
import sys
import unittest
from pathlib import Path

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
from capture.tiff_writer import (  # noqa: E402
    _exiftool_camera_attitude_argv,
    _exiftool_frame_tags_argv,
    _rejected_tag_warnings,
    XMP_CAMERA_NS,
    focus_from_payload,
    insert_jpeg_xmp_packet,
    xmp_attitude_packet,
    xmp_extratag,
)


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


class FocusEncoderArgvTests(unittest.TestCase):
    """The lens encoder reading -> EXIF:UserComment."""

    FOCUS = {
        "encoder_count": 930.0,
        "encoder_epoch": "20260928T122656",
        "encoder_camera": 1,
        "selected_camera": 1,
        "encoder_stale": False,
        "encoder_age_s": 0.151,
    }

    def _user_comment(self, argv: list[str]) -> dict:
        prefix = "-EXIF:UserComment="
        hits = [a[len(prefix):] for a in argv if a.startswith(prefix)]
        self.assertEqual(len(hits), 1, f"expected one UserComment arg, got {hits}")
        return json.loads(hits[0])

    def test_encoder_written_as_json(self) -> None:
        argv = _exiftool_frame_tags_argv(
            Path("x.tif"), focus=self.FOCUS, exiftool_exe=Path("exiftool.exe"),
        )
        self.assertEqual(
            self._user_comment(argv),
            {
                "encoder_count": 930.0,
                "encoder_epoch": "20260928T122656",
                "encoder_camera": 1,
            },
        )

    def test_zero_count_is_written(self) -> None:
        """0.0 is a real encoder position, not a missing value."""
        argv = _exiftool_frame_tags_argv(
            Path("x.tif"),
            focus={"encoder_count": 0.0, "encoder_epoch": "e"},
            exiftool_exe=Path("exiftool.exe"),
        )
        self.assertEqual(self._user_comment(argv)["encoder_count"], 0.0)

    def test_attitude_and_focus_share_one_invocation(self) -> None:
        """ExifTool rewrites the whole TIFF; two passes would double export cost."""
        argv = _exiftool_frame_tags_argv(
            Path("x.tif"),
            attitude={"yaw_deg": 10.0, "pitch_deg": None, "roll_deg": None},
            focus=self.FOCUS,
            exiftool_exe=Path("exiftool.exe"),
        )
        self.assertIn("-XMP-Camera:Yaw=10.000000", argv)
        self.assertIn("encoder_count", "".join(argv))
        self.assertEqual(argv[0], "exiftool.exe")
        self.assertEqual(argv[-1], "x.tif")

    def test_focus_without_count_raises(self) -> None:
        with self.assertRaises(ValueError):
            _exiftool_frame_tags_argv(
                Path("x.tif"),
                focus={"encoder_epoch": "20260928T122656"},
                exiftool_exe=Path("exiftool.exe"),
            )

    def test_nothing_to_write_raises(self) -> None:
        with self.assertRaises(ValueError):
            _exiftool_frame_tags_argv(
                Path("x.tif"), exiftool_exe=Path("exiftool.exe"),
            )

    def test_focus_from_payload_ignores_non_dict(self) -> None:
        self.assertIsNone(focus_from_payload({}))
        self.assertIsNone(focus_from_payload({"focus": "nope"}))
        self.assertEqual(focus_from_payload({"focus": self.FOCUS}), self.FOCUS)


class NativeXmpTests(unittest.TestCase):
    """We emit the XMP packet ourselves; ExifTool cannot write XMP-Camera."""

    ATT = {"yaw_deg": 123.456, "pitch_deg": -45.0, "roll_deg": 1.5}

    def test_packet_declares_namespace_and_values(self) -> None:
        pkt = xmp_attitude_packet(self.ATT).decode("utf-8")
        self.assertIn(XMP_CAMERA_NS, pkt)
        self.assertIn('Camera:Yaw="123.456000"', pkt)
        self.assertIn('Camera:Pitch="-45.000000"', pkt)
        self.assertIn('Camera:Roll="1.500000"', pkt)

    def test_partial_attitude_writes_only_known_angles(self) -> None:
        pkt = xmp_attitude_packet({"yaw_deg": 1.0}).decode("utf-8")
        self.assertIn("Camera:Yaw=", pkt)
        self.assertNotIn("Camera:Pitch=", pkt)

    def test_no_angles_yields_no_packet(self) -> None:
        """Better no tag than an empty one."""
        self.assertIsNone(xmp_attitude_packet({}))
        self.assertIsNone(xmp_attitude_packet({"yaw_deg": None, "attitude_status": "x"}))

    def test_extratag_is_tag_700_bytes(self) -> None:
        pkt = xmp_attitude_packet(self.ATT)
        code, dtype, count, value, writeonce = xmp_extratag(pkt)
        self.assertEqual(code, 700)
        self.assertEqual(dtype, 1)  # BYTE
        self.assertEqual(count, len(pkt))
        self.assertIs(value, pkt)
        self.assertTrue(writeonce)

    def test_jpeg_insert_adds_app1_after_soi(self) -> None:
        import tempfile

        pkt = xmp_attitude_packet(self.ATT)
        p = Path(tempfile.mkdtemp()) / "t.jpg"
        p.write_bytes(b"\xff\xd8" + b"\xff\xdb" + b"payload")
        self.assertTrue(insert_jpeg_xmp_packet(p, pkt))
        out = p.read_bytes()
        self.assertTrue(out.startswith(b"\xff\xd8\xff\xe1"))
        self.assertIn(b"http://ns.adobe.com/xap/1.0/\x00", out)
        self.assertTrue(out.endswith(b"\xff\xdb" + b"payload"))
        # Segment length field counts itself but not the 0xFFE1 marker.
        declared = int.from_bytes(out[4:6], "big")
        self.assertEqual(declared, len(b"http://ns.adobe.com/xap/1.0/\x00") + len(pkt) + 2)

    def test_jpeg_insert_rejects_non_jpeg(self) -> None:
        import tempfile

        p = Path(tempfile.mkdtemp()) / "t.tif"
        p.write_bytes(b"II*\x00rest")
        self.assertFalse(insert_jpeg_xmp_packet(p, xmp_attitude_packet(self.ATT)))
        self.assertEqual(p.read_bytes(), b"II*\x00rest")


class RejectedTagWarningTests(unittest.TestCase):
    """ExifTool exits 0 when one tag lands and another is refused."""

    def test_unwritable_tag_is_surfaced(self) -> None:
        stderr = "Warning: Sorry, XMP-Camera:Yaw doesn't exist or isn't writable\n"
        self.assertEqual(len(_rejected_tag_warnings(stderr)), 1)

    def test_duplicate_imagedescription_is_ignored(self) -> None:
        """Fires on every frame we write; would drown the real signal."""
        stderr = "Warning: Duplicate ImageDescription tag in IFD0 - x.tif\n"
        self.assertEqual(_rejected_tag_warnings(stderr), [])

    def test_empty_stderr(self) -> None:
        self.assertEqual(_rejected_tag_warnings(""), [])
        self.assertEqual(_rejected_tag_warnings(None), [])  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main(verbosity=2)
