"""Self-check for the AF v2 cross-object score handover (_CrossCal + stitching).

The non-trivial logic is the overlap cross-calibration that lets the climb stitch a
second object's focus scores onto the first object's scale, so the peak detector can
span a handover. These asserts drive it directly:

  (a) ratio recovery -- _CrossCal.ratio() recovers the known A/B scale from noisy
      same-frame overlap pairs (median, so a bad frame can't skew it);
  (b) stitched-peak location -- when the true focus peak lands in the SECOND object's
      segment, multiplying that segment by the recovered ratio moves the combined
      argmax onto the true peak, whereas the UN-stitched curve peaks at the seam
      (the bug this feature fixes);
  (c) overlap gate -- valid() is False until min_overlap pairs are seen;
  (d) promotion-frame ordering -- _advance_objects must NOT refill the on-deck slot on
      the same frame it promotes the old on-deck into objects[0], because that would
      overwrite ondeck_oid / cross_cal before _handle_handover (later that frame) can
      recognise and stitch the promotion. (a)-(c) all passed while this was broken: they
      drive _CrossCal directly and never run the engine, so the ratio was always fine and
      simply never reached. Live symptom was handovers=0 with 38 re-anchors, a climb curve
      mixing patches of differing texture, two static-bench runs disagreeing by 38 pulses,
      and parks landing at 0.36/0.52 of their own declared peak.

Run: py -3 tools/test_af_handover.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOST = REPO / "host"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HOST))

import numpy as np  # noqa: E402

from capture.autofocus_v2 import (  # noqa: E402
    AFStateV2,
    AutoFocusConfigV2,
    AutoFocusEngineV2,
    _CrossCal,
)

# Object B reads this fraction of object A at the same focus (B is less textured),
# so the ratio the calibration must recover is 1 / B_FRAC.
B_FRAC = 0.4
TRUE_RATIO = 1.0 / B_FRAC


def _gauss(pos: float, peak: float, amp: float, sigma: float) -> float:
    return amp * np.exp(-((pos - peak) ** 2) / (2.0 * sigma ** 2))


def test_ratio_recovery() -> None:
    # Same-frame overlap pairs: A and B image the same scene at the same focus, B at
    # B_FRAC of A, each with independent multiplicative noise (the metric's real noise
    # shape). The median A/B ratio must land on TRUE_RATIO despite the noise.
    rng = np.random.default_rng(3)
    xcal = _CrossCal(min_overlap=6)
    amp, sigma, peak, rel_sd = 1000.0, 20.0, 60.0, 0.15
    for pos in range(40, 56, 2):  # 8 overlap frames on the rising shoulder
        clean = _gauss(pos, peak, amp, sigma)
        a = clean * (1.0 + rng.normal(0.0, rel_sd))
        b = B_FRAC * clean * (1.0 + rng.normal(0.0, rel_sd))
        xcal.add(a, b)
    assert xcal.valid(), xcal.count
    r = xcal.ratio()
    assert abs(r - TRUE_RATIO) < 0.35 * TRUE_RATIO, (r, TRUE_RATIO)


def test_stitched_peak_lands_on_true_peak() -> None:
    # Climb: object A carries the rising shoulder (pos 0..50), object B carries the
    # peak + fall (pos 40..100), scaled to B_FRAC. They overlap on 40..50. After the
    # handover at pos 50 we fold in the recovered ratio; the stitched curve's argmax
    # must land on the true peak (60), while the un-stitched curve peaks back at the
    # seam because B's raw scores are too small to out-rank A's shoulder.
    rng = np.random.default_rng(9)
    amp, sigma, peak, rel_sd = 1000.0, 20.0, 60.0, 0.05

    def score(pos: float, frac: float) -> float:
        return max(1e-6, frac * _gauss(pos, peak, amp, sigma) * (1.0 + rng.normal(0.0, rel_sd)))

    xcal = _CrossCal(min_overlap=3)
    a_seg = [(p, score(p, 1.0)) for p in range(0, 55, 5)]          # A: shoulder
    for p in range(40, 55, 5):                                     # overlap 40,45,50
        xcal.add(score(p, 1.0), score(p, B_FRAC))
    assert xcal.valid()
    r = xcal.ratio()
    b_raw = [(p, score(p, B_FRAC)) for p in range(55, 105, 5)]     # B: peak + fall

    stitched = a_seg + [(p, s * r) for p, s in b_raw]
    unstitched = a_seg + b_raw

    def argmax_pos(curve):
        return curve[int(np.argmax([s for _, s in curve]))][0]

    assert abs(argmax_pos(stitched) - peak) <= 5.0, argmax_pos(stitched)
    # Without the stitch the peak is mis-located at (or before) the seam, proving the
    # normalization is what makes cross-object peak-finding work.
    assert argmax_pos(unstitched) <= 50, argmax_pos(unstitched)


def test_overlap_gate() -> None:
    xcal = _CrossCal(min_overlap=6)
    for _ in range(5):
        xcal.add(100.0, 40.0)
    assert not xcal.valid(), xcal.count      # 5 < 6 -> untrusted
    xcal.add(100.0, 40.0)
    assert xcal.valid(), xcal.count          # 6th pair crosses the gate
    assert abs(xcal.ratio() - 2.5) < 1e-6, xcal.ratio()
    # A near-zero denominator is rejected (would blow up the ratio), so it doesn't count.
    dead = _CrossCal(min_overlap=1)
    dead.add(100.0, 0.0)
    dead.add(None, 5.0)
    assert not dead.valid(), dead.count


# --- (d) engine-level: the promotion frame must stay stitchable ---------------

# Content drifts UP the frame, so the leading edge is the bottom and a patch pinned
# against y=0 has left the FOV.
DY = -12.0
FRAME_HW = (480, 640)


def _climbing_engine() -> AutoFocusEngineV2:
    cfg = AutoFocusConfigV2()
    cfg.enable_handover = True
    cfg.full_frame_target = False   # use the motion-derived leading edge
    cfg.restrict_interior = False   # plain edge-band grid; no bench bezel masking
    cfg.min_texture_score = 0.0     # random noise always qualifies
    cfg.xcal_min_overlap_frames = 6
    cfg.handover_stagger_frames = 8
    af = AutoFocusEngineV2(pulse=lambda *_a, **_k: None, config=cfg)
    af.state = AFStateV2.CLIMB
    rs = af._rs
    rs.motion_dy = DY
    # 15 frames since the last acquire: past the stagger, so the on-deck refill is armed.
    rs.frames, rs.last_acquire_frame = 20, 5
    return af


def _noise_frame() -> np.ndarray:
    return (np.random.default_rng(11).random(FRAME_HW) * 255.0).astype(np.uint8)


def test_promotion_frame_preserves_cross_cal_and_stitches() -> None:
    af = _climbing_engine()
    rs = af._rs
    primary = af._add_object((100, 2, 200, 100))    # hugs y=0 -> pans off this frame
    ondeck = af._add_object((300, 200, 400, 300))   # mid-frame -> survives
    rs.primary_oid, rs.ondeck_oid = primary.oid, ondeck.oid
    rs.cross_cal = _CrossCal(cfg_min := af.cfg.xcal_min_overlap_frames)
    for _ in range(8):                              # 8 >= 6 -> already trustworthy
        rs.cross_cal.add(100.0, 40.0)               # ratio 2.5
    assert rs.cross_cal.valid() and cfg_min == 6

    af._advance_objects(_noise_frame(), 0.0, DY, 0.5)

    # The primary is gone and the on-deck has been promoted...
    assert [o.oid for o in rs.objects] == [ondeck.oid], rs.objects
    # ...and crucially the evidence _handle_handover needs is intact: no same-frame refill
    # clobbered ondeck_oid, and the 8 accumulated overlap pairs were not reset.
    assert rs.ondeck_oid == ondeck.oid, rs.ondeck_oid
    assert rs.cross_cal.count == 8, rs.cross_cal.count

    assert af._handle_handover() is False            # continues the climb, doesn't end it
    assert rs.handovers == 1, rs.handovers           # the stitch actually happened
    assert abs(rs.cumulative_scale - 2.5) < 1e-6, rs.cumulative_scale
    assert rs.primary_oid == ondeck.oid


def test_ordinary_frame_still_refills_the_ondeck_slot() -> None:
    # The guard must only DEFER the refill by one frame, not disable handover prep: with
    # no promotion pending, a lone primary past the stagger still gets a partner.
    af = _climbing_engine()
    rs = af._rs
    primary = af._add_object((300, 200, 400, 300))   # mid-frame -> survives, stays primary
    rs.primary_oid = primary.oid

    af._advance_objects(_noise_frame(), 0.0, DY, 0.5)

    assert len(rs.objects) == 2, rs.objects
    assert rs.objects[0].oid == primary.oid          # primary unchanged -> no promotion
    assert rs.ondeck_oid == rs.objects[1].oid
    assert rs.ondeck_oid != primary.oid


# --- (e) the ratio must be measured in the curve's own units ------------------


def test_ratio_metric_is_brightness_invariant() -> None:
    """The A/B ratio is applied to samples from focus_measure_full_res, which divides
    mean^2 out. Measuring it with raw Laplacian variance instead makes it track the two
    patches' *brightness* as well as their texture -- and since the on-deck patch is
    always freshly picked near frame centre while the outgoing primary has drifted to a
    vignetted edge, that error has a fixed sign and compounds over every handover."""
    from capture.autofocus import focus_measure, focus_measure_normalized

    rng = np.random.default_rng(5)
    # Same texture, different illumination: B is the identical patch 1.6x brighter, so
    # an honest same-focus ratio is 1.0 -- the patches are equally sharp.
    texture = rng.random((120, 160)).astype(np.float32) * 40.0
    a = (texture + 60.0).astype(np.uint8)
    b = (texture * 1.6 + 96.0).astype(np.uint8)
    frame = np.hstack([a, b])
    roi_a, roi_b = (0, 0, 160, 120), (160, 0, 320, 120)

    raw = focus_measure(frame, roi_a) / focus_measure(frame, roi_b)
    norm = focus_measure_normalized(frame, roi_a) / focus_measure_normalized(frame, roi_b)

    # Raw variance is fooled by the brightness difference -- biased well below 1...
    assert raw < 0.75, raw
    # ...while the normalised metric recovers the honest ~1.0 the curve needs.
    assert 0.85 < norm < 1.18, norm
    # And the bias is what compounds: unchecked over 13 handovers it is catastrophic.
    assert raw ** 13 < 1e-3, raw ** 13


# --- (f) the park verdict must know when it cannot compare --------------------


def _parking_engine(msgs: list) -> AutoFocusEngineV2:
    """An engine sitting in PARK's settle phase with the hard cap already tripped, so
    _park_tick goes straight to the parked-vs-peak verdict."""
    cfg = AutoFocusConfigV2()
    af = AutoFocusEngineV2(pulse=lambda *_a, **_k: None, config=cfg, status=msgs.append)
    rs = af._rs
    rs.continuous = True                 # park -> HOLD (skips the _report() path)
    af.state = AFStateV2.PARK
    rs.park_phase = "settle"
    rs.best_score, rs.best_pos = 1000.0, -11
    rs.park_scores = [40.0] * 5          # 4% of peak: would trip "parked but soft"
    rs.park_settle_since = time.monotonic() - (cfg.park_settle_max_s + 1.0)
    rs.park_last_pos = 0.0
    return af


def test_park_does_not_cry_soft_when_the_object_changed() -> None:
    msgs: list = []
    af = _parking_engine(msgs)
    rs = af._rs
    obj = af._add_object((100, 100, 200, 200))
    rs.best_oid = obj.oid + 1            # peak was measured on a patch since panned off
    af._park_tick(np.zeros((240, 320), dtype=np.uint8))
    # 4% of peak, but on a different patch -- that is a content difference, not defocus.
    assert not any("soft" in m for m in msgs), msgs


def test_park_still_warns_on_the_same_object() -> None:
    msgs: list = []
    af = _parking_engine(msgs)
    rs = af._rs
    obj = af._add_object((100, 100, 200, 200))
    rs.best_oid = obj.oid                # same patch -> the comparison is meaningful
    af._park_tick(np.zeros((240, 320), dtype=np.uint8))
    assert any("soft" in m for m in msgs), msgs


def main() -> int:
    tests = [
        test_ratio_recovery,
        test_stitched_peak_lands_on_true_peak,
        test_overlap_gate,
        test_promotion_frame_preserves_cross_cal_and_stitches,
        test_ordinary_frame_still_refills_the_ondeck_slot,
        test_ratio_metric_is_brightness_invariant,
        test_park_does_not_cry_soft_when_the_object_changed,
        test_park_still_warns_on_the_same_object,
    ]
    for t in tests:
        t()
        print(f"  ok: {t.__name__}")
    print("af handover tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
