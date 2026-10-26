"""The live w10 scorer must score at the 16-bit scale psf_w10 is calibrated for.

The SHM slot the acquisition thread offers holds RAW right-justified BayerRG14
(0..16383, pipeline.SENSOR_FULL_SCALE); psf_w10.array_edges documents its input as
"16-bit scale (14-bit << 2)", which is why the offline reader applies
bay14_export_core.shift_14_to_16 before scoring a .bay14line.

w10 itself is scale-invariant (the ESF is normalised by its own contrast), so the bug is not a
wrong number -- it is that both reliability gates are dead, because they compare against
absolute 16-bit levels that 14-bit data can never reach:
  * the saturation gate (g >= 65000) -- 14-bit data tops out at 16383, so a clipped ROI is
    scored instead of skipped,
  * the cloud gate (med_mean > CLOUD_MEAN=22000) -- so a cloud frame is reported as a good one.

    py -3 tools/test_w10_live_scale.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "host"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402

from capture.psf_w10 import (  # noqa: E402
    CLOUD_MEAN, crop_rois, frame_ok, score_rois, score_rois_raw14,
)
from test_w10_live import synthetic_frame  # noqa: E402


def main() -> int:
    # What the sensor actually puts in the slot: the same scene, right-justified 14-bit.
    frame16 = synthetic_frame()
    frame14 = (frame16 >> 2).astype(np.uint16)
    assert int(frame14.max()) <= 16383

    rois = crop_rois(frame14, copy=True)
    unshifted = score_rois(rois)          # scoring a slot at its own scale
    shifted = score_rois_raw14(rois)      # what the live scorer and the w10 AF must do

    for name, s in (("slot as-is (14-bit)", unshifted), ("<< 2 (16-bit scale)", shifted)):
        print(f"{name}: n_edges={s['n_edges']:6d}  n_roi={s['n_roi']}  "
              f"w10={s['w10']:.3f}  med_mean={s['med_mean']:8.0f}  ok={frame_ok(s)}")

    # The width is the same either way (well inside the 1.75 px floor): scoring unshifted does
    # not corrupt the measurement, so the damage is confined to the gates below.
    assert abs(shifted["w10"] - unshifted["w10"]) < 0.01, (unshifted["w10"], shifted["w10"])

    # ...but the gates go with the scale. This frame is bright enough to be rejected at the
    # scale psf_w10 is calibrated for, and is silently accepted at the slot's own scale.
    assert 16383 < CLOUD_MEAN, "a 14-bit frame can never reach the cloud threshold"
    assert shifted["med_mean"] > CLOUD_MEAN and not frame_ok(shifted)
    assert unshifted["med_mean"] < CLOUD_MEAN and frame_ok(unshifted)

    print("ok -- live scorer must shift 14 -> 16 before score_rois")
    return 0


if __name__ == "__main__":
    sys.exit(main())
