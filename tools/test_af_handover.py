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
  (c) overlap gate -- valid() is False until min_overlap pairs are seen.

Run: py -3 tools/test_af_handover.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOST = REPO / "host"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HOST))

import numpy as np  # noqa: E402

from capture.autofocus_v2 import _CrossCal  # noqa: E402

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


def main() -> int:
    tests = [
        test_ratio_recovery,
        test_stitched_peak_lands_on_true_peak,
        test_overlap_gate,
    ]
    for t in tests:
        t()
        print(f"  ok: {t.__name__}")
    print("af handover tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
