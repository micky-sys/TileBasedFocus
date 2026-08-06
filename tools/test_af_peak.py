"""Self-check for the AF v2 curve-fit peak refinement (_fit_peak).

The non-trivial logic is the local parabola fit that turns a noisy climb curve
into a sub-sample peak position. These asserts drive it directly against a known
synthetic peak: the fit must (a) land close to the true vertex, (b) beat the raw
argmax on average over noisy trials, and (c) return None (caller falls back to
argmax) on the degenerate inputs -- monotonic ramp, too few points, flat curve.

Run: py -3 tools/test_af_peak.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOST = REPO / "host"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HOST))

import numpy as np  # noqa: E402

from capture.autofocus_v2 import _fit_peak  # noqa: E402


def _curve(true_peak: float, positions, noise_sd: float, rng) -> list:
    """A concave parabola sampled at ``positions`` with additive Gaussian noise."""
    return [
        (int(p), 100.0 - 0.01 * (p - true_peak) ** 2 + rng.normal(0.0, noise_sd))
        for p in positions
    ]


def test_fit_lands_near_true_vertex() -> None:
    rng = np.random.default_rng(1)
    positions = list(range(0, 100, 5))  # peak at 50 sits between samples 45 and 55
    samples = _curve(50.0, positions, noise_sd=1.0, rng=rng)
    xv = _fit_peak(samples, window=3)
    assert xv is not None, "fit should succeed on a clean concave hump"
    assert abs(xv - 50.0) < 5.0, xv  # within one sample step of the true peak


def test_fit_beats_argmax_on_average() -> None:
    rng = np.random.default_rng(7)
    positions = list(range(0, 100, 5))
    true_peak = 50.0
    fit_err = argmax_err = 0.0
    trials = 200
    for _ in range(trials):
        samples = _curve(true_peak, positions, noise_sd=3.0, rng=rng)
        xs = [p for p, _ in samples]
        ys = [s for _, s in samples]
        argmax_pos = xs[int(np.argmax(ys))]
        argmax_err += abs(argmax_pos - true_peak)
        xv = _fit_peak(samples, window=3)
        fit_err += abs((xv if xv is not None else argmax_pos) - true_peak)
    # Averaging the shoulder must reduce the mean peak error vs a single noisy argmax.
    assert fit_err < argmax_err, (fit_err / trials, argmax_err / trials)


def test_degenerate_inputs_return_none() -> None:
    ramp = [(p, float(p)) for p in range(0, 50, 5)]      # monotonic: a >= 0
    assert _fit_peak(ramp, window=3) is None
    assert _fit_peak([(0, 1.0), (5, 2.0)], window=3) is None  # < 3 points in window
    assert _fit_peak([], window=3) is None                    # empty
    flat = [(p, 42.0) for p in range(0, 50, 5)]          # no curvature
    assert _fit_peak(flat, window=3) is None


def test_log_domain_beats_linear_on_multiplicative_noise() -> None:
    # A Gaussian sharpness peak with MULTIPLICATIVE noise (SD grows with score),
    # which is how the variance-based focus metric actually scatters. log() is the
    # variance-stabilising transform + log(Gaussian) is exactly a parabola, so the
    # log fit should localise the peak at least as well as the plain linear fit.
    rng = np.random.default_rng(11)
    positions = list(range(0, 100, 5))
    # sigma small enough that the +/-3 fit window reaches well down the shoulder
    # (~40% of peak), i.e. real dynamic range -- that's the regime where the peak's
    # bigger absolute scatter actually skews an unweighted linear fit and log helps.
    true_peak, amp, sigma, rel_sd = 50.0, 1000.0, 12.0, 0.15
    log_err = lin_err = 0.0
    trials = 300
    for _ in range(trials):
        samples = []
        for p in positions:
            clean = amp * np.exp(-((p - true_peak) ** 2) / (2.0 * sigma ** 2))
            noisy = clean * (1.0 + rng.normal(0.0, rel_sd))  # SD ∝ score
            samples.append((int(p), max(1e-6, float(noisy))))
        xv_log = _fit_peak(samples, window=3, log_domain=True)
        xv_lin = _fit_peak(samples, window=3, log_domain=False)
        argmax_pos = samples[int(np.argmax([s for _, s in samples]))][0]
        log_err += abs((xv_log if xv_log is not None else argmax_pos) - true_peak)
        lin_err += abs((xv_lin if xv_lin is not None else argmax_pos) - true_peak)
    assert log_err <= lin_err, (log_err / trials, lin_err / trials)


def main() -> int:
    tests = [
        test_fit_lands_near_true_vertex,
        test_fit_beats_argmax_on_average,
        test_degenerate_inputs_return_none,
        test_log_domain_beats_linear_on_multiplicative_noise,
    ]
    for t in tests:
        t()
        print(f"  ok: {t.__name__}")
    print("af peak tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
