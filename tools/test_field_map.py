"""Self-checks for the field-map sweep (host/capture/field_map.py).

The claim under test is the whole reason the tool exists: from ONE dive+climb, a
tile that focuses high and a tile that focuses low come back with vertices that
differ by the right amount. So the fixture gives each tile its own true best-focus
position, blurs its texture by how far the motor is from that position, and checks
the recovered vertices -- and, more importantly, the DIFFERENCES between them,
which is what a field map is actually read for.

Run: py -3 tools/test_field_map.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "host"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from capture.field_map import (  # noqa: E402
    FieldMapConfig,
    FieldMapSweep,
    TILE_NAMES,
    five_tile_rois,
)
from capture.focus_axis import OpenLoopAxis  # noqa: E402

TILE = 192
CPP = 24.0            # counts per pulse, as on the rig
BLUR_PER_COUNT = 1.0 / 500.0   # sigma grows this fast as the motor leaves focus


def _cfg() -> FieldMapConfig:
    return FieldMapConfig(
        tile_px=TILE,
        dive_pulses=4,
        dive_burst_interval_s=0.0,   # no wall-clock waiting in a unit test
        climb_step_pulses=2,
        climb_pulses=200,            # 100 frames, ~4800 counts of travel
        fit_window=3,
    )


class _Scene:
    """Five fixed patches, each sharpest at its own motor position."""

    def __init__(self, peaks: dict[str, float]) -> None:
        rng = np.random.default_rng(7)
        self.peaks = peaks
        self.base = {
            name: (rng.integers(0, 4096, size=(TILE, TILE)) << 4).astype(np.uint16)
            for name in TILE_NAMES
        }

    def crops(self, pos: float) -> list[np.ndarray]:
        out = []
        for name in TILE_NAMES:
            sigma = 0.4 + BLUR_PER_COUNT * abs(pos - self.peaks[name])
            img = cv2.GaussianBlur(
                self.base[name].astype(np.float32), (0, 0), sigmaX=sigma
            )
            out.append(img.astype(np.uint16))
        return out


def _run_sweep(peaks: dict[str, float], tmpdir: str):
    axis = OpenLoopAxis(lambda _s, _n: None, backlash_pulses=0, counts_per_pulse=CPP)
    scene = _Scene(peaks)
    done: dict = {}
    sweep = FieldMapSweep(
        axis=axis,
        config=_cfg(),
        csv_dir=tmpdir,
        finished=lambda ok, rep: done.update(rep, ok=ok),
    )
    sweep.start(five_tile_rois(4000, 3000, _cfg()))
    guard = 0
    while sweep.running and guard < 2000:
        sweep.feed(scene.crops(float(axis.position)))
        guard += 1
    assert not sweep.running, "sweep never finished"
    return sweep, done, axis


def test_rois_are_bayer_aligned_and_inside_the_frame() -> None:
    rois = five_tile_rois(4000, 3000, _cfg())
    assert len(rois) == len(TILE_NAMES)
    for (x0, y0, x1, y1) in rois:
        assert x0 % 2 == 0 and y0 % 2 == 0, "green sub-grid phase would shift"
        assert 0 <= x0 < x1 <= 4000 and 0 <= y0 < y1 <= 3000
        assert (x1 - x0) == TILE and (y1 - y0) == TILE
    named = dict(zip(TILE_NAMES, rois))
    assert named["top"][1] < named["centre"][1] < named["bottom"][1]
    assert named["left"][0] < named["centre"][0] < named["right"][0]
    print("ok: five_tile_rois placement + Bayer alignment")


def test_a_flat_field_gives_every_tile_the_same_vertex() -> None:
    """Control: identical true focus everywhere -> spread is fit noise, not signal.
    If this fails, any spread the tool reports on the rig is the tool's own."""
    with tempfile.TemporaryDirectory() as tmp:
        sweep, _, _axis = _run_sweep({n: 1200.0 for n in TILE_NAMES}, tmp)
    v = [r.vertex for r in sweep.results()]
    assert all(x is not None for x in v), f"a tile found no peak: {v}"
    spread = max(v) - min(v)
    assert spread < 40.0, f"flat field reported a {spread:.0f}c spread"
    print(f"ok: flat field -> {spread:.0f}c spread (fit noise floor)")


def test_tilted_field_recovers_each_tile_vertex_and_their_differences() -> None:
    """The real job: top and bottom focus 800 counts apart, and the sweep says so."""
    peaks = {
        "centre": 1200.0,
        "top": 800.0,
        "bottom": 1600.0,
        "left": 1100.0,
        "right": 1300.0,
    }
    with tempfile.TemporaryDirectory() as tmp:
        sweep, report, axis = _run_sweep(peaks, tmp)
        assert report.get("csv") and os.path.exists(report["csv"]), "no CSV written"
        rows = open(report["csv"], encoding="utf-8").read().splitlines()
    # Non-destructive: the lens must come back to where the operator left it,
    # otherwise every field map costs an AF run to undo.
    assert abs(float(axis.position)) <= 6 * CPP, (
        f"sweep ended {axis.position:+.0f}c from the start"
    )
    # header comment + column header + one row per sample
    assert len(rows) == report["samples"] + 2, f"CSV rows {len(rows)}"

    got = {r.name: r.vertex for r in sweep.results()}
    for name, truth in peaks.items():
        assert got[name] is not None, f"{name} found no peak"
        err = abs(got[name] - truth)
        assert err < 60.0, f"{name} vertex {got[name]:.0f}c vs truth {truth:.0f}c"
    # Differences are what a field map is read for, so check them directly.
    tilt = got["bottom"] - got["top"]
    assert abs(tilt - 800.0) < 60.0, f"top->bottom tilt read {tilt:.0f}c, want 800c"
    print(f"ok: tilt recovered as {tilt:.0f}c (truth 800c); "
          + ", ".join(f"{n}={got[n]:+.0f}" for n in TILE_NAMES))


def test_stop_midway_still_reports_what_it_measured() -> None:
    """Operator abort must not throw away the samples already collected."""
    axis = OpenLoopAxis(lambda _s, _n: None, backlash_pulses=0, counts_per_pulse=CPP)
    scene = _Scene({n: 1200.0 for n in TILE_NAMES})
    done: dict = {}
    with tempfile.TemporaryDirectory() as tmp:
        sweep = FieldMapSweep(
            axis=axis, config=_cfg(), csv_dir=tmp,
            finished=lambda ok, rep: done.update(rep, ok=ok),
        )
        sweep.start(five_tile_rois(4000, 3000, _cfg()))
        for _ in range(30):
            sweep.feed(scene.crops(float(axis.position)))
        sweep.stop("operator")
        assert not sweep.running
        assert done.get("ok") is False, "an aborted sweep must not claim success"
        assert done["samples"] > 0 and done["csv"]
    print(f"ok: abort kept {done['samples']} samples")


def main() -> int:
    tests = [
        test_rois_are_bayer_aligned_and_inside_the_frame,
        test_a_flat_field_gives_every_tile_the_same_vertex,
        test_tilted_field_recovers_each_tile_vertex_and_their_differences,
        test_stop_midway_still_reports_what_it_measured,
    ]
    for t in tests:
        t()
    print(f"\n{len(tests)} field-map checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
