"""Self-check for the OrthoSim ground-distance ruler math (orthosim_core).

Qt-free, no framework: `py -3 tools/test_orthosim_scale.py`. Guards the two
properties the human-readable scale depends on -- correct round-distance tick
spacing, and ticks locked to the imagery (they shift by exactly px_per_m * the
distance travelled, in the right direction).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orthosim_core import nice_interval_m, scale_ticks  # noqa: E402


def test_nice_interval() -> None:
    for target, ppm in [(200, 0.02), (200, 2.0), (150, 0.5), (300, 13.7)]:
        iv = nice_interval_m(target, ppm)
        assert iv * ppm >= target - 1e-9, (iv, ppm, target)
        # must be a 1/2/5 x 10^n value
        mant = iv / (10.0 ** math.floor(math.log10(iv)))
        assert round(mant, 6) in (1.0, 2.0, 5.0), iv
    assert nice_interval_m(200, 0.0) == 1.0  # degenerate guard


def test_spacing_round() -> None:
    ppm = 100.0 / 5.0 * 0.02  # 5 cm/px ortho, zoom 0.02 -> 0.4 px/m
    ticks = scale_ticks(5000.0, ppm, 1000.0, 200.0, content_from_far=True)
    assert len(ticks) >= 2
    iv = nice_interval_m(200.0, ppm)
    for (d0, p0), (d1, p1) in zip(ticks, ticks[1:]):
        assert abs((d1 - d0) - iv) < 1e-6            # round ground interval
        assert abs((p1 - p0) - iv * ppm) < 1e-6      # constant screen spacing
    # every tick lands on a multiple of the interval
    for d, _ in ticks:
        assert abs(d / iv - round(d / iv)) < 1e-6, d


def test_locked_to_imagery() -> None:
    ppm, axis = 0.4, 1000.0
    delta = 137.0  # metres travelled between two frames
    for far in (True, False):
        t0 = dict(scale_ticks(5000.0, ppm, axis, 200.0, far))
        t1 = dict(scale_ticks(5000.0 + delta, ppm, axis, 200.0, far))
        common = set(t0) & set(t1)
        assert common, far
        sign = -1.0 if far else 1.0  # far edge = new content -> ticks move to 0
        for d in common:
            assert abs((t1[d] - t0[d]) - sign * ppm * delta) < 1e-6, (far, d)


def test_direction() -> None:
    ppm, axis, dm = 0.4, 1000.0, 5000.0
    far = scale_ticks(dm, ppm, axis, 200.0, content_from_far=True)
    near = scale_ticks(dm, ppm, axis, 200.0, content_from_far=False)
    newest_far = max(far, key=lambda t: t[0])
    newest_near = max(near, key=lambda t: t[0])
    assert abs(newest_far[1] - axis) < 1e-6   # newest ground at far edge
    assert abs(newest_near[1] - 0.0) < 1e-6   # newest ground at near edge


def main() -> int:
    test_nice_interval()
    test_spacing_round()
    test_locked_to_imagery()
    test_direction()
    print("orthosim scale self-check: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
