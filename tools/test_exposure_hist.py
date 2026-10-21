"""M4 exposure histogram: stats of the RAW 14-bit coarse frame must report the range used,
clipping and headroom; the overlay must render; the coarse tap must be the GREEN Bayer phase.

Covers host/capture/pipeline.exposure_stats / exposure_line, focus_panel._ExposureHistGraph,
and the demo backend's decimation phase (the kyfg backend uses the same slice).
Run: py -3 tools/test_exposure_hist.py
"""
import os, sys, numpy as np
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path[:0] = [_ROOT, os.path.join(_ROOT, "host")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from capture.pipeline import SENSOR_FULL_SCALE, exposure_line, exposure_stats  # noqa: E402

# Flat frame at 1/8 full scale: 3 stops of headroom, nothing used, nothing clipped.
flat = np.full((400, 600), 2048, dtype=np.uint16)
st = exposure_stats(flat)
assert st["n"] == 200 * 300 and st["clip"] == 0.0
assert st["p50"] == 2048 // 64 * 64 + 63 and st["p01"] == st["p999"] == st["p50"]
assert abs(st["headroom"] - 3.0) < 0.05 and st["used"] == 0.0, st

# Linear ramp over the full range: median ~ half scale, p99.9 ~ full, 1 bin of 256 in the top bin.
ramp = np.tile(np.linspace(0, SENSOR_FULL_SCALE, 1024, dtype=np.uint16), (64, 1))
st = exposure_stats(ramp, sub=1)
assert abs(st["p50"] - SENSOR_FULL_SCALE / 2) < 128 and st["p999"] >= 16320, st
assert abs(st["clip"] - 1 / 256) < 0.002 and st["headroom"] < 0.01 and st["used"] > 7, st

# 5 % of pixels pinned at full scale on a dim scene: clip fraction and p99.9 see them, p50 does not.
rng = np.random.default_rng(0)
dim = rng.integers(500, 1500, (512, 512), dtype=np.uint16)
mask = rng.random((512, 512)) < 0.05
dim[mask] = SENSOR_FULL_SCALE
st = exposure_stats(dim, sub=1)
assert abs(st["clip"] - 0.05) < 0.005 and st["p999"] >= 16320 and st["p50"] < 1600, st
line = exposure_line(st)
assert line.startswith("p50 ") and "clip 4.99%" in line and "head 0.0st" in line, line

# Empty / degenerate input does not raise.
assert exposure_stats(np.zeros((0, 0), dtype=np.uint16))["n"] == 0

# Decimation phase: the coarse tap is G of an RGGB mosaic (col offset 1), not R.
from capture import demo_backend  # noqa: E402
cam = demo_backend.DemoCamera.__new__(demo_backend.DemoCamera)
full = np.zeros((64, 64), dtype=np.uint16)
full[0::2, 1::2] = 9000  # G (row 0, col 1)
full[1::2, 0::2] = 9000  # G (row 1, col 0)
full[0::2, 0::2] = 100   # R
full[1::2, 1::2] = 50    # B
cam._full_template = full
cam._frame_index = 0
small = cam._make_small_frame()
assert small.shape == (16, 16) and (small == 9000).all(), "coarse tap must be the green phase"

# Overlay renders with and without stats (offscreen).
from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from capture.focus_panel import _ExposureHistGraph  # noqa: E402
app = QApplication.instance() or QApplication([])
g = _ExposureHistGraph()
for s in (None, exposure_stats(dim, sub=1)):
    g.set_stats(s)
    img = QImage(g.size(), QImage.Format.Format_ARGB32)
    g.render(img)
    assert not img.isNull()
print("OK exposure hist")
