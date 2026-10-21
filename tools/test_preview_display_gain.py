"""Preview "Display gain" must brighten the on-screen image only, never wrap, and be a no-op at 1x.

Covers host/capture/pipeline._u16_to_gray8 and the two preview builders that use it
(whole-frame decimated + 100% zoom crop), plus the AcquisitionThread setter clamp.
Run: py -3 tools/test_preview_display_gain.py
"""
import os, sys, numpy as np
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path[:0] = [_ROOT, os.path.join(_ROOT, "host")]  # repo root for `sharpness`, host for `capture`
from capture.pipeline import (  # noqa: E402
    PREVIEW_DISPLAY_GAIN_MAX, PREVIEW_DISPLAY_GAIN_MIN, AcquisitionThread,
    _downsample_bayer_to_gray8, _full_res_center_crop_gray8, _u16_to_gray8,
)

rng = np.random.default_rng(0)
a = rng.integers(0, 16384, (128, 160), dtype=np.uint16)  # 14-bit, as the sensor delivers

# 1x is bit-identical to the historical >> 6 mapping.
assert np.array_equal(_u16_to_gray8(a, 1.0), np.clip(a >> 6, 0, 255).astype(np.uint8))

# 4x brightens a dim scene by ~4 and saturates instead of wrapping.
dim = (a >> 3).astype(np.uint16)  # top value 2047 -> 31 at 1x
g1, g4 = _u16_to_gray8(dim, 1.0), _u16_to_gray8(dim, 4.0)
assert g4.dtype == np.uint8 and g4.max() <= 128 and abs(float(g4.mean()) / max(1e-9, float(g1.mean())) - 4.0) < 0.3
assert (_u16_to_gray8(a, 8.0)[a >= 2048] == 255).all(), "must clip, not wrap"
assert (_u16_to_gray8(a, 8.0) >= _u16_to_gray8(a, 1.0)).all(), "gain must be monotonic"

# Both preview builders honour the gain and agree with each other on scaling.
assert (_downsample_bayer_to_gray8(a, 64, 4.0) >= _downsample_bayer_to_gray8(a, 64, 1.0)).all()
crop = _full_res_center_crop_gray8(a, 40, 20, 4.0)
assert crop.shape == (20, 40) and np.array_equal(crop, _u16_to_gray8(a[54:74, 60:100], 4.0))

# Setter clamps to the published range (GUI spinbox shares it).
acq = AcquisitionThread.__new__(AcquisitionThread)  # no QThread init needed to test the setter
acq.set_preview_gain(0.01); assert acq._preview_gain == PREVIEW_DISPLAY_GAIN_MIN
acq.set_preview_gain(1e9); assert acq._preview_gain == PREVIEW_DISPLAY_GAIN_MAX
acq.set_preview_gain(6); assert acq._preview_gain == 6.0
print("OK preview display gain")
