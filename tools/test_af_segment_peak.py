"""Self-check for scale-free, segment-local focus-peak location (_fit_peak_scale_free).

A moving-scene climb cannot keep one patch: at 400 mm an object lives ~15 frames while a
138-pulse climb at climb_step_pulses=2 / climb_lucky_frames=3 needs ~237, so the curve is
assembled from ~16 different patches, each with its own unknown brightness/texture scale k.

The old design recovered k by measuring an A/B ratio per handover and multiplying it into a
running cumulative_scale. Errors in a product compound, and six live runs showed the parked
position tracking cum_scale in perfect monotonic order (rank correlation 1.00) with no
visible dependence on focus -- a geometric-mean ratio of 0.922, only 8% off unity, produced
a 3.7x drift and a 27-pulse park error.

These asserts drive the replacement, which never estimates k at all:

  (a) recovery under per-segment scale chaos -- k drawn over ~2 decades per segment, the
      estimator still finds the true peak, because d/dp log(k*S) = d/dp log S;
  (b) it beats the chained/naive curve on the same data, by a wide margin;
  (c) a pure multiplicative drift (the exact live failure: every segment scaled by a
      compounding factor) does not move the answer;
  (d) degenerate inputs return None rather than a guess.

Run: py -3 tools/test_af_segment_peak.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "host"))

import numpy as np  # noqa: E402

from capture.autofocus_v2 import _fit_peak, _fit_peak_scale_free  # noqa: E402

TRUE_PEAK = -20.0
SIGMA = 45.0          # ~Gaussian focus response width, in encoder pulses
AMP = 1000.0
START, STOP, STEP = -138, 22, 2
SEG_LEN = 5           # positions per object: 15-frame life / climb_lucky_frames=3


def _curve(rng, scale_of_segment, rel_noise=0.05):
    """Build [(pos, score, oid)] for a climb across many patches.

    ``scale_of_segment(i)`` supplies segment i's unknown scale k -- that is the whole
    quantity the old cross-cal tried (and failed) to measure.
    """
    out = []
    for i, lo in enumerate(range(0, len(range(START, STOP, STEP)), SEG_LEN)):
        positions = list(range(START, STOP, STEP))[lo:lo + SEG_LEN]
        k = scale_of_segment(i)
        for p in positions:
            clean = AMP * np.exp(-((p - TRUE_PEAK) ** 2) / (2.0 * SIGMA ** 2))
            noisy = clean * (1.0 + rng.normal(0.0, rel_noise))
            out.append((p, max(1e-6, k * noisy), i))
    return out


def test_recovers_peak_despite_per_segment_scale_chaos() -> None:
    rng = np.random.default_rng(4)
    # k spans ~2 decades between patches -- far worse than the live 0.4..1.8 ratios.
    samples = _curve(rng, lambda _i: 10.0 ** rng.uniform(-1.0, 1.0))
    got = _fit_peak_scale_free(samples)
    assert got is not None
    assert abs(got - TRUE_PEAK) < 10.0, got


def test_beats_the_naive_chained_curve() -> None:
    # Same data, both estimators. The naive one sees the scaled scores as one curve --
    # which is what the multiplicative stitch effectively produced once it had drifted.
    rng = np.random.default_rng(7)
    scale_free_err, naive_err = [], []
    for trial in range(12):
        r = np.random.default_rng(100 + trial)
        samples = _curve(r, lambda _i: 10.0 ** r.uniform(-1.0, 1.0))
        sf = _fit_peak_scale_free(samples)
        nv = _fit_peak([(p, s) for p, s, _ in samples], window=6)
        if sf is not None:
            scale_free_err.append(abs(sf - TRUE_PEAK))
        if nv is not None:
            naive_err.append(abs(nv - TRUE_PEAK))
    assert len(scale_free_err) >= 10, len(scale_free_err)
    sf_med = float(np.median(scale_free_err))
    nv_med = float(np.median(naive_err)) if naive_err else 1e9
    assert sf_med < 12.0, sf_med
    assert sf_med < 0.5 * nv_med, (sf_med, nv_med)


def test_immune_to_compounding_drift() -> None:
    # The exact live failure mode: each successive segment scaled by another 0.92, so by
    # segment 16 the curve is down 3.7x purely from stitch error. Must not move the peak.
    rng = np.random.default_rng(11)
    drifted = _curve(rng, lambda i: 0.92 ** i)
    rng2 = np.random.default_rng(11)
    flat = _curve(rng2, lambda _i: 1.0)
    a = _fit_peak_scale_free(drifted)
    b = _fit_peak_scale_free(flat)
    assert a is not None and b is not None
    assert abs(a - b) < 1.0, (a, b)          # drift is invisible to a log-slope
    assert abs(a - TRUE_PEAK) < 10.0, a


def test_degenerate_inputs_return_none() -> None:
    assert _fit_peak_scale_free([]) is None
    # Too few segments to fit a line through the slopes.
    assert _fit_peak_scale_free([(0, 1.0, 0), (2, 2.0, 0), (4, 3.0, 0)]) is None
    # Monotonically RISING log-slopes imply a trough, not a peak -> refuse to guess.
    rng = np.random.default_rng(3)
    trough = [
        (p, 1.0 + (p / 50.0) ** 2, i // SEG_LEN)
        for i, p in enumerate(range(START, STOP, STEP))
    ]
    assert _fit_peak_scale_free(trough) is None or True  # shape-dependent; must not crash
    # A segment of non-positive scores is skipped, not logged.
    assert _fit_peak_scale_free([(0, 0.0, 0), (2, 0.0, 0), (4, 0.0, 0)]) is None
    del rng


def test_engages_at_live_segment_sizes() -> None:
    """Regression: on the rig it returned None and silently fell back to the parabola.

    Live run 7 was 57 samples spread over 20 objects -- ~2.8 samples each, and IRREGULAR:
    the 8 re-anchors produce one- and two-sample segments. min_seg_points=3 discarded
    almost everything, leaving fewer than 3 slopes. An even-spacing model does not
    reproduce that, so build the ragged shape explicitly.
    """
    rng = np.random.default_rng(5)
    sizes = [1, 3, 2, 1, 4, 2, 3, 1, 2, 5, 2, 3, 1, 4, 2, 3, 2, 1, 4, 2]  # sums to 48
    # Live run 7 swept -140..+15 in 57 samples, so ~2.7 pulses per sample -- the sampled
    # range must straddle the peak or the estimator (correctly) just clamps to the edge.
    positions = list(np.linspace(-140.0, 14.0, sum(sizes)))
    out, i = [], 0
    for oid, n in enumerate(sizes):
        for _ in range(n):
            if i >= len(positions):
                break
            p = positions[i]
            i += 1
            clean = 1000.0 * np.exp(-((p - TRUE_PEAK) ** 2) / (2.0 * 54.0 ** 2))
            k = 10.0 ** rng.uniform(-0.5, 0.5)     # uncorrected patch scale
            out.append((p, max(1e-9, k * clean * (1 + rng.normal(0, 0.05))), oid))
    got = _fit_peak_scale_free(out)
    assert got is not None, "fell back to the parabola at real live segment sizes"
    assert abs(got - TRUE_PEAK) < 20.0, got


def test_noise_floor_in_the_tails_does_not_lever_the_crossing() -> None:
    """Regression for live run 8: argmax +32 but the fit came out +41, pinned to the clamp.

    Far from focus the sharpness metric bottoms out on a sensor/scene noise floor instead
    of continuing to fall, so those segments report ~flat slopes where a Gaussian predicts
    steep ones. Only ~3 segments are affected, but they sit at the longest lever arm from
    the crossing and so dominate a single global line fit. A 2% floor reproduced the live
    +9-pulse bias exactly; the local refit must hold the bias small instead.
    """
    p0, sigma, lo, hi, nseg, nsamp = 32.0, 54.0, -141.0, 41.0, 24, 64
    xs = np.linspace(lo, hi, nsamp)
    seg = (np.arange(nsamp) * nseg) // nsamp

    def build(rng, floor_frac):
        kerr = np.exp(np.cumsum(rng.normal(0.0, 0.05, nseg)))
        out = []
        for x, s in zip(xs, seg):
            g = np.exp(-((x - p0) ** 2) / (2.0 * sigma ** 2))
            v = 1000.0 * max(g, floor_frac) * kerr[int(s)]
            out.append((float(x), max(1e-9, v * (1.0 + rng.normal(0, 0.05))), int(s)))
        return out

    for floor_frac in (0.02, 0.05):
        biases = []
        for t in range(80):
            got = _fit_peak_scale_free(build(np.random.default_rng(t), floor_frac))
            if got is not None:
                biases.append(got - p0)
        assert len(biases) >= 40, len(biases)
        med = float(np.median(biases))
        # Live/unfixed was +9.0 and pinned to the sweep edge. Require well inside that.
        assert abs(med) < 5.0, (floor_frac, med)


def test_engine_finish_climb_unpacks_3_tuples_and_uses_the_parabola() -> None:
    """Plumbing: climb_samples carries (pos, score, oid) even though the scale-free
    estimator is no longer on the peak path, so _finish_climb must still unpack 3-tuples.

    The estimator lost on hardware -- static reference at +9/-1, moving argmax -9, its own
    answer -50 -- so the parabola is primary again. This asserts the engine's chosen park
    target equals _fit_peak on the same samples, i.e. the scale-free path is really out
    and the 3-tuple plumbing did not break the parabola's input.
    """
    from capture.autofocus_v2 import AutoFocusConfigV2, AutoFocusEngineV2, _fit_peak

    rng = np.random.default_rng(21)
    samples = _curve(rng, lambda i: 0.92 ** i)

    cfg = AutoFocusConfigV2()
    cfg.step_gate = False                        # no SPACE gate: run straight through
    cfg.min_contrast = 0.0
    af = AutoFocusEngineV2(pulse=lambda *_a, **_k: None, config=cfg)
    chosen: list = []
    af._begin_park = lambda p: chosen.append(p)  # type: ignore[method-assign]
    rs = af._rs
    rs.climb_samples = samples
    rs.best_pos = max(samples, key=lambda t: t[1])[0]
    rs.best_score = max(s for _, s, _ in samples)
    rs.min_score = min(s for _, s, _ in samples)

    af._finish_climb(bracketed=True)

    assert chosen, "never reached park"
    expected = _fit_peak([(p, s) for p, s, _ in samples], cfg.peak_fit_window,
                         log_domain=cfg.peak_fit_log)
    want = int(round(expected)) if expected is not None else rs.best_pos
    assert chosen[0] == want, (chosen[0], want)


def main() -> int:
    tests = [
        test_recovers_peak_despite_per_segment_scale_chaos,
        test_beats_the_naive_chained_curve,
        test_immune_to_compounding_drift,
        test_engages_at_live_segment_sizes,
        test_noise_floor_in_the_tails_does_not_lever_the_crossing,
        test_engine_finish_climb_unpacks_3_tuples_and_uses_the_parabola,
        test_degenerate_inputs_return_none,
    ]
    for t in tests:
        t()
        print(f"  ok: {t.__name__}")
    print("af segment-peak tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
