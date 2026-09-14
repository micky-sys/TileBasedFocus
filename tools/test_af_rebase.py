"""Self-checks for the AF run rebase + field-geometry correction.

The correction subtracts a number from every measurement, so if the model is wrong
it does not fail loudly -- it shifts the answer and stays plausible. These checks
pin the two places that could do that quietly: the sweep-to-run frame conversion
(``P = pos - origin``) and the tilt/curvature surface fitted through the five tiles.

Run: py -3 tools/test_af_rebase.py
"""

from __future__ import annotations

import csv
import math
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from af_rebase_runs import COARSE_TO_FULL, FieldModel, expand_scenes, parse  # noqa: E402

# Tile boxes chosen symmetric about the centre so the intended tilt/curvature
# terms map onto the five vertices exactly.
ROIS = {
    "centre": (6400, 4400, 7000, 5000),   # centre (6700, 4700)
    "top": (6400, 2000, 7000, 2600),      # (6700, 2300)  -> v = -1
    "bottom": (6400, 6800, 7000, 7400),   # (6700, 7100)  -> v = +1
    "left": (2000, 4400, 2600, 5000),     # (2300, 4700)  -> u = -1
    "right": (10800, 4400, 11400, 5000),  # (11100, 4700) -> u = +1
}
TILT_H, CURV_H, TILT_V, CURV_V = 150.0, 100.0, 60.0, -20.0
ORIGIN = 500.0


def _write_csv(path: str) -> dict[str, float]:
    """A sweep whose tiles peak at a known tilted+curved surface. Scores are
    Gaussian so that log(score) is exactly the parabola the engine fits."""
    want = {
        "centre": 0.0,
        "right": TILT_H + CURV_H,
        "left": -TILT_H + CURV_H,
        "bottom": TILT_V + CURV_V,
        "top": -TILT_V + CURV_V,
    }
    names = list(ROIS)
    positions = [-400.0 + 40.0 * i for i in range(46)]  # -400 .. +1400
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["# roi"] + [f"{n}={ROIS[n]}".replace("'", "") for n in names])
        w.writerow(["sample", "pos_counts", "raw_counts"] + names)
        for i, pos in enumerate(positions):
            scores = [
                100.0 * math.exp(-(((pos - (want[n] + ORIGIN)) / 500.0) ** 2))
                for n in names
            ]
            w.writerow([i, pos, ORIGIN - pos] + [f"{s:.6f}" for s in scores])
    return want


def test_field_model_recovers_the_surface_it_was_given() -> None:
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "Iron661_fieldmap_20260812_120000.csv")
        want = _write_csv(path)
        m = FieldModel(Path(path), window=20)

        for n, expect in want.items():
            got = m.P[n]
            assert abs(got - expect) < 2.0, f"{n}: vertex {got:+.1f} != {expect:+.1f}"
        assert not m.suspect, f"clean sweep flagged tiles: {m.suspect}"
        for term, expect in (("tiltH", TILT_H), ("curvH", CURV_H),
                             ("tiltV", TILT_V), ("curvV", CURV_V)):
            got = getattr(m, term)
            assert abs(got - expect) < 2.0, f"{term} {got:+.1f} != {expect:+.1f}"

        # Predicting at a tile centre must return that tile's own measurement.
        for n, roi in ROIS.items():
            x, y = (roi[0] + roi[2]) / 2, (roi[1] + roi[3]) / 2
            p, edge = m.predict(x, y)
            assert abs(p - want[n]) < 2.0, f"predict@{n} {p:+.1f} != {want[n]:+.1f}"
            assert edge <= 1.001, f"{n} reported as extrapolated (edge={edge:.2f})"

        # Halfway to the right edge: tilt is linear, curvature quadratic.
        p, edge = m.predict(6700 + 0.5 * 4400, 4700)
        assert abs(p - (0.5 * TILT_H + 0.25 * CURV_H)) < 2.0, f"interior {p:+.1f}"
        assert abs(edge - 0.5) < 0.01

        # Outside the tile span must announce itself rather than quietly extrapolate.
        _p, edge = m.predict(6700 + 2.0 * 4400, 4700)
        assert edge > 1.0, "extrapolation not flagged"


def test_a_plane_would_be_wrong_here() -> None:
    """Curvature is not decoration: dropping it misplaces the frame edge by curvH.

    Guards against anyone 'simplifying' the surface back to a tilt-only plane,
    which on this rig would inject ~100c of error -- the size of the effect an
    A/B is trying to measure."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "Iron661_fieldmap_20260812_120000.csv")
        _write_csv(path)
        m = FieldModel(Path(path), window=20)
        plane_only = m.P["centre"] + m.tiltH  # what a tilt-only fit predicts at u=1
        full, _ = m.predict(11100, 4700)
        assert abs(full - plane_only - CURV_H) < 2.0, (
            f"curvature term not carried: {full:+.1f} vs plane {plane_only:+.1f}"
        )


LOG = """\
10:00:01.000 INFO capture.autofocus_v2 | af2-acquire target=top roi=(100, 200, 300, 400) score=42.0
10:00:02.000 INFO capture.autofocus_v2 | af2-dive done pos=-3000c dropped=0.50 raw=4000
10:00:10.000 INFO capture.autofocus_v2 | af2-climb handover #1 @pos=+100c ratio=0.900 cum_scale=0.9 (overlap n=7)
10:00:12.000 INFO capture.autofocus_v2 | af2-climb handover #2 @pos=+200c ratio=0.800 cum_scale=0.72 (overlap n=6)
10:00:15.000 INFO capture.autofocus_v2 | af2-climb primary changed 3->4, no stitchable cross-cal -> re-anchor and continue
10:00:18.000 INFO capture.autofocus_v2 | af2-climb primary changed 4->5, no stitchable cross-cal -> re-anchor and continue
10:00:30.000 INFO capture.autofocus_v2 | af2-climb peak n=40 best_pos=+1200c best=50.00 contrast=0.80 handovers=3 improvement=2.00x peak_roi=(500, 600, 700, 800)
10:00:30.100 INFO capture.autofocus_v2 | af2-climb peak refine argmax=+1200c fit=+1210c
10:00:45.000 INFO capture.autofocus_v2 | af2-park arrived pos=+1205c target=+1210c miss=-5c
10:00:46.000 INFO capture.autofocus_v2 | af2-park pos=+1205c raw=-205 parked=48.00 peak=50.00 ratio=0.96
"""


def test_parser_reads_the_peak_patch_and_rebases_into_P() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "log.txt"
        p.write_text(LOG, encoding="utf-8")
        runs = parse(p)
        assert len(runs) == 1, f"expected one run, got {len(runs)}"
        r = runs[0]
        # origin = raw + pos = 4000 + (-3000); P = pos - origin, so start = -origin.
        assert r["origin"] == 1000, r["origin"]
        assert -r["praw"] == 205, r["praw"]
        assert r["peak_roi"] == (500, 600, 700, 800), r.get("peak_roi")
        # The scale the curve ENDED on, and the seams where it silently changed scale:
        # both are how a varied-terrain climb goes wrong, so both must reach the report.
        assert r["cum"] == 0.72, r.get("cum")
        assert r["reanc"] == 2, r.get("reanc")
        # The peak patch, not the acquire patch: they differ, and using the wrong
        # one puts the correction in the wrong part of a 165c-tilted field.
        assert r["tile"][:2] == (100, 200)
        assert COARSE_TO_FULL == 4


def test_a_log_without_peak_roi_still_parses() -> None:
    """Older logs predate the field: the tool must degrade, not crash."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "log.txt"
        p.write_text(LOG.replace(" peak_roi=(500, 600, 700, 800)", ""), encoding="utf-8")
        runs = parse(p)
        assert len(runs) == 1 and "peak_roi" not in runs[0]
        assert runs[0]["hand"] == 3 and runs[0]["fit"] == 1210


def test_scene_spec_expands_in_order() -> None:
    assert expand_scenes("BB:2,PH:2,BB:2,PH:2") == [
        "BB", "BB", "PH", "PH", "BB", "BB", "PH", "PH"
    ]
    assert expand_scenes("BB,PH") == ["BB", "PH"]


def main() -> int:
    tests = [
        test_field_model_recovers_the_surface_it_was_given,
        test_a_plane_would_be_wrong_here,
        test_parser_reads_the_peak_patch_and_rebases_into_P,
        test_a_log_without_peak_roi_still_parses,
        test_scene_spec_expands_in_order,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"{len(tests)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
