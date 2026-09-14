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

import math
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
from capture.focus_axis import EncoderAxis  # noqa: E402

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


class _FakeShaft:
    """Minimal motor: one pulse moves the shaft by ``counts_per_pulse`` counts."""

    def __init__(self, counts_per_pulse: float = 24.0, start: float = 0.0) -> None:
        self.cpp = float(counts_per_pulse)
        self.count = float(start)

    def pulse(self, sym: str, n: int) -> None:
        self.count += (int(n) if sym == "+" else -int(n)) * self.cpp

    def read(self) -> float:
        return self.count


def test_park_creep_lands_centred_on_the_target_not_below() -> None:
    """The creep must stop at the CLOSEST reachable point to best_pos.

    It only ever moves UP, so any tolerance subtracted from the target is a pure
    one-directional bias rather than a tolerance. The old exit test was
    ``pos >= best_pos - park_tolerance_pulses`` with a tolerance of 4 PULSES, i.e.
    96 counts on this rig, so it quit the instant it crossed the BOTTOM edge of the
    band and never closed the gap: all 14 park runs in the 2026-08 bench logs landed
    2-4 pulses BELOW target, not once above (target +15 -> arrived +11, +6 -> +2,
    -7 -> -11, -14 -> -18...). That offset sat underneath every AF measurement we
    were using to compare peak estimators. Half a pulse is the honest bound -- it is
    the finest step the hardware has, so anything wider is a systematic error.
    """
    cpp = 24.0
    frame = np.zeros((240, 320), dtype=np.uint8)
    # Deliberately not pulse-aligned, and spanning all three creep speeds (single
    # pulse / climb pace / coarse bulk) so the landing is checked after each.
    for target in (7, 100, 313, 1000, 2401):
        shaft = _FakeShaft(counts_per_pulse=cpp, start=-5000.0)
        axis = EncoderAxis(shaft.pulse, shaft.read, counts_per_pulse=cpp,
                           backlash_pulses=2)
        axis.reset()  # position 0 == shaft -5000; every target below is ABOVE us
        cfg = AutoFocusConfigV2(
            encoder_counts_per_pulse=cpp,
            park_slow_band_counts=12 * cpp,
            park_climb_band_counts=36 * cpp,
        )
        af = AutoFocusEngineV2(pulse=lambda *_a, **_k: None, axis=axis, config=cfg)
        af.state = AFStateV2.PARK
        rs = af._rs
        rs.park_phase = "creep"
        rs.best_pos = target
        rs.park_move_since = time.monotonic()
        for _ in range(4000):
            af._park_tick(frame)
            if rs.park_phase != "creep":
                break
        assert rs.park_phase == "settle", f"creep never finished for target {target}"
        err = axis.position - target
        assert abs(err) <= cpp / 2.0 + 1e-9, (
            f"target {target}: landed {err:+.1f} counts away, more than the half-pulse "
            f"({cpp / 2.0:.0f}) that is actually reachable -- a one-way creep stopped "
            f"at a band edge again")


# --- (f) the ratio must match the curve's RESOLUTION, not just its units ------


def _boom(*_a, **_k):
    raise AssertionError("coarse frame consulted despite a full-res pair being wired")


def test_cross_cal_prefers_the_full_res_pair_when_one_is_wired() -> None:
    """The ratio multiplies full-res climb samples, so it must be measured on full-res
    crops. With a same-frame pair on offer the coarse frame must not be touched."""
    calls = []

    def pair(roi_a, roi_b, labels=None):
        calls.append((roi_a, roi_b))
        return (120.0, 30.0)

    af = _climbing_engine()
    af._full_res_pair = pair
    af._coarse_pair_score = _boom
    assert af._pair_scores(_noise_frame(), (0, 0, 10, 10), (20, 20, 30, 30)) == (120.0, 30.0)
    assert calls == [((0, 0, 10, 10), (20, 20, 30, 30))]


def test_a_stale_pair_is_skipped_rather_than_half_measured() -> None:
    """No fresh batch yet -> (None, None), and _CrossCal drops it. Scoring one patch
    full-res and the other coarse would bake the resolution mismatch it is meant to
    remove straight into the ratio."""
    af = _climbing_engine()
    af._full_res_pair = lambda _a, _b, _labels=None: (None, None)
    af._coarse_pair_score = _boom
    assert af._pair_scores(_noise_frame(), (0, 0, 10, 10), (20, 20, 30, 30)) == (None, None)
    xcal = _CrossCal(6)
    xcal.add(None, None)
    assert xcal.count == 0


def test_without_a_pair_callback_it_falls_back_to_the_coarse_frame() -> None:
    """Sim and these self-checks have no pipeline. Both patches then come from the one
    frame at its only resolution, so the ratio is at least self-consistent."""
    af = _climbing_engine()
    assert af._full_res_pair is None
    a, b = af._pair_scores(_noise_frame(), (0, 0, 120, 120), (200, 200, 320, 320))
    assert a is not None and b is not None and a > 0.0 and b > 0.0


def test_decimation_changes_the_ratio_when_the_patches_differ_in_texture_scale() -> None:
    """Why the pair had to move to full res, as a measurement rather than an argument.

    The score IS high-frequency energy and the coarse tap is 4x-decimated, so a ratio
    read there equals the full-res ratio only while both patches have the same SHAPE of
    spatial-frequency spectrum. Fine texture against coarse texture -- a city block
    against a ploughed field -- breaks that badly, which is why varied terrain was the
    case to worry about. Equal-spectrum patches are the control: there, decimation is
    harmless, which is why the bug hid on uniform urban scenes for so long."""
    from capture.autofocus import focus_measure_normalized

    rng = np.random.default_rng(17)
    n = 240

    def ratio_at_both_resolutions(pa, pb) -> tuple[float, float]:
        frame = np.hstack([pa, pb]).astype(np.uint8)
        roi_a, roi_b = (0, 0, n, n), (n, 0, 2 * n, n)
        full = (focus_measure_normalized(frame, roi_a)
                / focus_measure_normalized(frame, roi_b))
        dec = frame[::4, ::4]  # what the coarse tap ships (SUBSAMPLE_FACTOR = 4)
        coarse = (focus_measure_normalized(dec, (0, 0, n // 4, n // 4))
                  / focus_measure_normalized(dec, (n // 4, 0, n // 2, n // 4)))
        return full, coarse

    fine = rng.random((n, n)) * 60.0 + 100.0                       # energy at Nyquist
    blocks = np.kron(rng.random((n // 8, n // 8)) * 60.0 + 100.0,
                     np.ones((8, 8)))                              # energy 8 px wide
    full, coarse = ratio_at_both_resolutions(fine, blocks)
    disagreement = max(full / coarse, coarse / full)
    assert disagreement > 1.5, (
        f"expected the decimated ratio to diverge on mismatched spectra, "
        f"full={full:.3f} coarse={coarse:.3f}"
    )

    # Control: same spectrum, different contrast -> decimation is ~neutral.
    dim = rng.random((n, n)) * 20.0 + 100.0
    full2, coarse2 = ratio_at_both_resolutions(fine, dim)
    agreement = max(full2 / coarse2, coarse2 / full2)
    assert agreement < 1.25, (
        f"equal-spectrum patches should survive decimation, "
        f"full={full2:.3f} coarse={coarse2:.3f}"
    )


# --- (g) the climb must not act on a curve it doesn't have -------------------


def _finished_engine(samples: list, best_pos: int, best_score: float):
    """An engine parked at the end of a climb carrying `samples`, ready for
    _finish_climb. Positions are counts, as the engine stores them."""
    af = _climbing_engine()
    rs = af._rs
    rs.climb_samples = [(p, s, False) for p, s in samples]
    rs.best_pos, rs.best_score = best_pos, best_score
    rs.min_score = min(s for _p, s in samples)
    outcome = {}
    af._fail = lambda reason: outcome.update(failed=True, reason=reason)
    af._begin_park = lambda target: outcome.update(parked_at=target)
    return af, rs, outcome


def test_a_climb_that_never_rose_is_refused_instead_of_parked() -> None:
    """The live failure of 2026-08-13: 7 handovers, 11 re-anchors, and a curve whose
    peak came in BELOW its own first sample (improvement 0.68x). contrast was 0.86 --
    a wide range -- so min_contrast waved it through and the lens parked 3000 counts
    low. Improvement is the number that knows the difference."""
    # The live curve's actual shape: starts high on the original object's scale (110.7),
    # gets crushed to 10.5 as re-anchored segments pile on unrelated scales, then climbs
    # back to 75.3. contrast = (75.3-10.5)/75.3 = 0.86, comfortably past min_contrast,
    # while improvement = 75.3/110.7 = 0.68 -- the peak is below where it began.
    vals = ([110.7]
            + [110.7 + (10.5 - 110.7) * i / 20.0 for i in range(1, 21)]
            + [10.5 + (75.3 - 10.5) * i / 35.0 for i in range(1, 36)])
    samples = [(-3270 + 65 * i, v) for i, v in enumerate(vals)]
    # best_score is what the engine's running peak tracker held (75.3 at +305c), NOT the
    # max of the stored samples -- that divergence is the anomaly itself.
    af, rs, outcome = _finished_engine(samples, best_pos=+305, best_score=75.3)
    smax, smin = rs.best_score, rs.min_score
    assert abs((smax - smin) / smax - 0.86) < 0.01, "contrast must clear min_contrast"

    af._finish_climb(bracketed=False)

    assert outcome.get("failed"), outcome
    assert "never rose" in outcome["reason"], outcome["reason"]
    assert "parked_at" not in outcome, "a curve with no hill must not reach park"


def test_a_real_hill_still_parks() -> None:
    # Same shape as a healthy live run (improvement 3-6x): the gate must not bite.
    samples = [(-3270 + 60 * i, 20.0 + 200.0 * np.exp(-((i - 40) ** 2) / 200.0))
               for i in range(56)]
    peak_i = max(range(56), key=lambda i: samples[i][1])
    af, rs, outcome = _finished_engine(samples, samples[peak_i][0], samples[peak_i][1])

    af._finish_climb(bracketed=True)

    assert not outcome.get("failed"), outcome
    assert "parked_at" in outcome, outcome


def test_a_fit_that_lands_outside_its_own_window_is_rejected() -> None:
    """_fit_peak sees only [argmax-w, argmax+w]; a vertex outside that span is an
    extrapolation, and on a seam-broken climb it is unbounded. Live it came back 3313
    counts from the argmax and AF drove there."""
    af = _climbing_engine()
    rs = af._rs
    step = 65
    rs.climb_samples = [(-3270 + step * i, 1.0, False) for i in range(56)]  # ends at +305
    w = af.cfg.peak_fit_window

    # The live case: argmax at the last sample, fit 3311 counts below it.
    lo, hi = af._fit_window_span(+305)
    assert lo <= 305 <= hi, (lo, hi)
    assert not (lo <= -3006 <= hi), "the live bad fit must fall outside the window"
    assert hi == 305 and lo == 305 - w * step, "must clamp at the last sample"

    # Interior argmax: w samples either side, so 2*w*step counts wide.
    mid = -3270 + step * 25
    lo_m, hi_m = af._fit_window_span(mid)
    assert hi_m - lo_m == 2 * w * step, (lo_m, hi_m, w)

    # First sample: clamp the other way rather than running off the front.
    lo0, hi0 = af._fit_window_span(-3270)
    assert lo0 == -3270 and hi0 == -3270 + w * step, (lo0, hi0)


def _finish_with_fit(fit_at: float, argmax: int = +305):
    """Run _finish_climb over a real climb's sample grid with _fit_peak pinned to
    `fit_at`, and report where it decided to park."""
    import capture.autofocus_v2 as v2

    step = 65
    samples = [(-3270 + step * i, 1.0 + 0.001 * i) for i in range(56)]  # ends at +305
    af, rs, outcome = _finished_engine(samples, best_pos=argmax, best_score=9.0)
    rs.min_score = 0.1                      # clear min_contrast
    rs.climb_start_pos = -3270
    rs.climb_samples[0] = (-3270, 0.2, False)   # clear min_climb_improvement
    orig = v2._fit_peak
    v2._fit_peak = lambda *_a, **_k: fit_at
    try:
        af._finish_climb(bracketed=True)
    finally:
        v2._fit_peak = orig
    return outcome.get("parked_at"), outcome


def test_a_fit_outside_its_window_but_inside_the_sweep_is_trusted_over_the_argmax() -> None:
    """Measured, not assumed, and the cleanest evidence is two FROZEN-scene runs 17
    minutes apart on 2026-08-13 -- same motionless scene, so any disagreement is the
    estimator. Their parabola fits landed at raw -1062 and -1039, 23 counts apart. Their
    argmaxes disagreed by 712. The second run's fit was rejected by this gate, so it
    parked on the argmax, and the pair then read as 712 counts of focus drift that never
    happened.

    Half-width on this rig is ~1700c, so a sample 700c off the peak still scores 0.87 of
    it: a few percent of noise on a flat top moves the single highest sample hundreds of
    counts, while a 41-sample parabola averages it away."""
    # 900 counts below the argmax: far outside a 20-sample window, far inside the sweep.
    parked, _ = _finish_with_fit(-595.0, argmax=+305)
    assert parked == -595, f"the fit was inside the swept range and should be used: {parked}"


def test_a_fit_off_the_end_of_the_sweep_is_still_refused() -> None:
    """The case the gate was written for, which is extrapolation rather than a wide
    interpolation: a crushed curve returned a vertex 3311 counts from the argmax and
    the lens drove to the dive floor. Below the first sample = off the measured world."""
    parked, _ = _finish_with_fit(-9000.0, argmax=+305)
    assert parked == +305, f"an extrapolation must not be followed: {parked}"


def test_a_fit_inside_its_own_window_is_used_as_before() -> None:
    parked, _ = _finish_with_fit(+280.0, argmax=+305)
    assert parked == +280, parked


def test_full_res_measure_survived_being_made_fast() -> None:
    """The AF metric was costing 58 ms per patch, three patches per frame, which is
    what took the camera from 4.7 fps to 3.4 during a run. Two changes bought that
    back, and each needs its own guard.

    Swapping numpy's var/mean for cv2's must not move the number: check against a
    plain reference. Dropping the >>6 must be a no-op: it can only be, if a constant
    scaling of the input cancels, so check that directly rather than trusting it."""
    import cv2
    from capture.autofocus import focus_measure_full_res

    rng = np.random.default_rng(7)
    # Bayer-ish crop with real structure, even origin so the green sub-grid lands right.
    crop = np.zeros((240, 320), np.uint16)
    crop[:] = rng.integers(200, 3000, size=crop.shape, dtype=np.uint16)
    crop[60:180, 80:240] += rng.integers(0, 8000, size=(120, 160), dtype=np.uint16)

    def reference(c):
        g = c[0::2, 1::2].astype(np.float32)
        g = cv2.GaussianBlur(g, (0, 0), 1.0)
        lap = cv2.Laplacian(g, cv2.CV_32F, ksize=3)
        return float(lap.var() / (float(g.mean()) ** 2 + 1.0) * 1e4)

    got, want = focus_measure_full_res(crop), reference(crop)
    assert abs(got - want) <= 0.005 * want, f"cv2 reductions moved the score: {got} vs {want}"

    # Constant scaling cancels -> the dropped >>6 could not have mattered.
    dim = (crop // 4).astype(np.uint16)
    a, b = focus_measure_full_res(crop), focus_measure_full_res(dim)
    assert abs(a - b) <= 0.02 * a, f"score is not scale-free: {a} vs {b}"

    # And it still has to be a focus metric: blur must lower it.
    blurred = cv2.GaussianBlur(crop.astype(np.float32), (0, 0), 3.0).astype(np.uint16)
    assert focus_measure_full_res(blurred) < 0.5 * a


def _diving_engine(focus_counts: float, halfwidth: float = 1700.0):
    """An engine in DIVE whose patch scores a Gaussian hill centred `focus_counts`
    from the start. Negative = focus is BELOW the lens, i.e. diving approaches it."""
    # 55 counts/pulse is the DIVE strength's measured rate on the rig (a 4-burst dive
    # of 15 pulses drops 3316 counts), not the 24 of the fine climb strength. The dive
    # depth the cap allows only makes sense in the coarse units it actually moves in.
    cpp = 55.0
    shaft = _FakeShaft(counts_per_pulse=cpp)
    cfg = AutoFocusConfigV2()
    cfg.dive_burst_interval_s = 0.0        # no wall-clock waits in a self-check
    axis = EncoderAxis(shaft.pulse, shaft.read, counts_per_pulse=cpp)
    axis.reset()
    af = AutoFocusEngineV2(pulse=lambda *_a, **_k: None, axis=axis, config=cfg)
    af._add_object((100, 100, 200, 200))
    af._coarse_pair_score = lambda _f, _r: math.exp(
        -math.log(2) * ((axis.position - focus_counts) / halfwidth) ** 2)
    af._begin_dive()
    return af, axis


def _run_dive(af, axis, max_ticks: int = 200) -> int:
    frame = _noise_frame()
    for _ in range(max_ticks):
        if af.state is not AFStateV2.DIVE:
            break
        af._dive_tick(frame)
    return af._rs.dive_bursts_done


def test_a_dive_that_starts_above_focus_keeps_going_until_the_score_collapses() -> None:
    """The failure mode of 2026-08-13, in miniature.

    A fixed burst count assumes the lens starts near focus. It did not -- the bench
    shifted and left it 2558 counts high -- so a 3316-count dive ended ~760 counts
    below the peak, the climb crested within a few samples and then ran downhill for
    its whole length. Four runs failed identically before a field map showed why.

    Diving on the measured score handles it without knowing where the lens started:
    on the way down the score RISES through focus, the running peak records it, and
    the dive only stops once we are properly past it."""
    af, axis = _diving_engine(focus_counts=-2558.0)
    af._begin_climb = lambda: setattr(af, "state", AFStateV2.CLIMB)
    bursts = _run_dive(af, axis)
    rs = af._rs

    assert bursts > af.cfg.dive_bursts, (
        f"stopped at the nominal {af.cfg.dive_bursts} bursts with focus 2558c below "
        f"the start -- this is the bug")
    assert axis.position < -2558.0 - 900, (
        f"climb would start at {axis.position:+.0f}c, barely below focus at -2558c")
    assert rs.dive_last_score <= af.cfg.dive_stop_fraction * rs.dive_peak_score
    # The peak recorded on the way down IS the pass through focus, near enough.
    assert rs.dive_peak_score > 0.9, f"never saw focus on the way down ({rs.dive_peak_score:.3f})"


def test_a_dive_that_starts_at_focus_still_stops_at_the_nominal_depth() -> None:
    """Starting near focus the score falls from the first burst, so the adaptive rule
    must not extend the dive -- every extra burst is climb length, and climb length is
    cross-object seams."""
    af, axis = _diving_engine(focus_counts=0.0)
    af._begin_climb = lambda: setattr(af, "state", AFStateV2.CLIMB)
    assert _run_dive(af, axis) == af.cfg.dive_bursts


def test_a_textureless_dive_is_capped_rather_than_driven_into_the_stop() -> None:
    """A flat score never collapses. Without the cap the adaptive rule would keep
    firing bursts until the mechanism hit its end stop."""
    af, axis = _diving_engine(focus_counts=0.0, halfwidth=1e9)  # ~flat
    af._begin_climb = lambda: setattr(af, "state", AFStateV2.CLIMB)
    assert _run_dive(af, axis) == af.cfg.dive_max_bursts


def test_objects_carry_a_birth_frame_so_age_is_measurable() -> None:
    af = _climbing_engine()
    rs = af._rs
    rs.frames = 10
    first = af._add_object((0, 0, 64, 64))
    rs.frames = 17
    second = af._add_object((64, 0, 128, 64))

    assert (first.born, second.born) == (10, 17)
    # The ratio at frame 22 is comparing a 12-frame-old patch against a 5-frame-old one;
    # that asymmetry is the thing under test, so it has to survive into the log.
    rs.frames = 22
    assert rs.frames - first.born == 12
    assert rs.frames - second.born == 5


def main() -> int:
    tests = [
        test_ratio_recovery,
        test_stitched_peak_lands_on_true_peak,
        test_overlap_gate,
        test_promotion_frame_preserves_cross_cal_and_stitches,
        test_ordinary_frame_still_refills_the_ondeck_slot,
        test_ratio_metric_is_brightness_invariant,
        test_park_creep_lands_centred_on_the_target_not_below,
        test_park_does_not_cry_soft_when_the_object_changed,
        test_park_still_warns_on_the_same_object,
        test_cross_cal_prefers_the_full_res_pair_when_one_is_wired,
        test_a_stale_pair_is_skipped_rather_than_half_measured,
        test_without_a_pair_callback_it_falls_back_to_the_coarse_frame,
        test_decimation_changes_the_ratio_when_the_patches_differ_in_texture_scale,
        test_a_climb_that_never_rose_is_refused_instead_of_parked,
        test_a_real_hill_still_parks,
        test_a_fit_that_lands_outside_its_own_window_is_rejected,
        test_a_fit_outside_its_window_but_inside_the_sweep_is_trusted_over_the_argmax,
    test_a_fit_off_the_end_of_the_sweep_is_still_refused,
    test_a_fit_inside_its_own_window_is_used_as_before,
    test_a_dive_that_starts_above_focus_keeps_going_until_the_score_collapses,
    test_a_dive_that_starts_at_focus_still_stops_at_the_nominal_depth,
    test_a_textureless_dive_is_capped_rather_than_driven_into_the_stop,
    test_objects_carry_a_birth_frame_so_age_is_measurable,
        test_full_res_measure_survived_being_made_fast,
    ]
    for t in tests:
        t()
        print(f"  ok: {t.__name__}")
    print("af handover tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
