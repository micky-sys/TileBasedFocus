"""Self-check: an M4 focus crop carries the sweep position of the full frame
it was cut from, in both its filename and its embedded metadata.

That pairing is the whole point -- without it a folder of focus crops cannot be
matched against the full-res frames exported from the .bay14line.

    py -3 tools/test_focus_correlation.py
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))

import tifffile  # noqa: E402

from capture.file_naming import build_focus_frame_name  # noqa: E402
from capture.tiff_writer import FrameMetadata, write_frame  # noqa: E402

WHEN = _dt.datetime(2026, 9, 28, 12, 35, 6, 123000)


def _name(**kw) -> str:
    return build_focus_frame_name(
        drive_root="D:\\", project_raw="Project", camera_serial="2439008",
        when=WHEN, **kw,
    ).basename()


def test_position_in_filename() -> None:
    assert _name(sweep=1, frame_in_sweep=4).endswith("_S1_F4.tif"), _name(
        sweep=1, frame_in_sweep=4)


def test_position_omitted_when_no_full_frame() -> None:
    # M4 without Record: nothing to point at, so no misleading S0_F0.
    base = _name()
    assert base.endswith("123506_123.tif"), base
    assert "_S" not in base, base


def test_half_a_position_is_not_written() -> None:
    assert "_S" not in _name(sweep=1), "frame_in_sweep missing -> no pair"
    assert "_S" not in _name(frame_in_sweep=4), "sweep missing -> no pair"


def test_tag_still_leads() -> None:
    base = _name(tag="OID007", sweep=2, frame_in_sweep=9)
    assert base.startswith("OID007_Cam2439008_"), base
    assert base.endswith("_S2_F9.tif"), base


def test_sweep_reaches_the_tiff() -> None:
    import numpy as np

    meta = FrameMetadata(
        camera_serial="2439008", project="Project", line=2,
        frame_in_sweep=4, sweep=1, capture_kind="focus",
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "crop.tif")
        write_frame(path, np.zeros((8, 8), dtype=np.uint16), meta)
        with tifffile.TiffFile(path) as tf:
            payload = json.loads(tf.pages[0].tags["ImageDescription"].value)
    assert payload["sweep"] == 1, payload
    assert payload["frame_in_sweep"] == 4, payload


def test_sweep_defaults_to_zero() -> None:
    meta = FrameMetadata(
        camera_serial="2439008", project="Project", line=2, frame_in_sweep=0)
    assert meta.sweep == 0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all focus-correlation checks passed")
