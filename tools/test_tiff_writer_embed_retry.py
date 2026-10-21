"""Tests for the in-place EXIF embed surviving a transient file lock.

An AV scanner holding a just-written TIFF for a moment used to abort a whole
export run with ``PermissionError``; see ``_open_rplus_waiting_out_locks``.
"""

from __future__ import annotations

import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import tifffile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "host"))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from capture import tiff_writer  # noqa: E402
from capture.tiff_writer import embed_exif_user_comment  # noqa: E402

EXIFIFD_TAG = 34665


def _write_plain_tiff(path: Path) -> None:
    tifffile.imwrite(
        str(path), np.zeros((16, 16), dtype=np.uint16), photometric="minisblack",
    )


def _ifd0_tags(path: Path) -> list[int]:
    with open(path, "rb") as fh:
        header = fh.read(8)
        bo = "<" if header[:2] == b"II" else ">"
        fh.seek(struct.unpack(bo + "I", header[4:8])[0])
        n = struct.unpack(bo + "H", fh.read(2))[0]
        blob = fh.read(n * 12)
    return [struct.unpack_from(bo + "H", blob, i * 12)[0] for i in range(n)]


def _lock_for_n_calls(n: int):
    """Patch ``open`` so the first *n* ``r+b`` opens raise PermissionError."""
    real_open = open
    calls = {"n": 0}

    def fake_open(file, mode="r", *args, **kwargs):  # noqa: ANN001
        if "+" in mode:
            calls["n"] += 1
            if calls["n"] <= n:
                raise PermissionError(13, "Permission denied")
        return real_open(file, mode, *args, **kwargs)

    return mock.patch.object(tiff_writer, "open", fake_open, create=True), calls


FOCUS = {"encoder_count": 12345, "encoder_epoch": 7, "encoder_camera": 1}


class EmbedRetryTests(unittest.TestCase):
    def test_transient_lock_is_waited_out(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "f.tif"
            _write_plain_tiff(p)
            patch, calls = _lock_for_n_calls(3)
            with patch:
                self.assertTrue(embed_exif_user_comment(p, FOCUS))
            self.assertEqual(calls["n"], 4)  # 3 refusals, then success
            self.assertIn(EXIFIFD_TAG, _ifd0_tags(p))

    def test_no_lock_means_no_retry(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "f.tif"
            _write_plain_tiff(p)
            patch, calls = _lock_for_n_calls(0)
            with patch:
                self.assertTrue(embed_exif_user_comment(p, FOCUS))
            self.assertEqual(calls["n"], 1)

    def test_permanent_lock_still_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "f.tif"
            _write_plain_tiff(p)
            patch, _calls = _lock_for_n_calls(10_000)
            with mock.patch.object(
                tiff_writer, "_EMBED_REOPEN_DELAYS_S", (0.0, 0.0, 0.0),
            ), patch:
                with self.assertRaises(PermissionError):
                    embed_exif_user_comment(p, FOCUS)


if __name__ == "__main__":
    unittest.main()
