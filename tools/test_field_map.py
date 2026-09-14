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
    )                                # fit_window left at the shipping default


class _Scene:
    """Five fixed patches, each sharpest at its own motor position.

    ``dead`` names tiles whose sharpness does NOT vary with focus -- what a tile
    sitting off the screen on a blank wall looks like."""

    def __init__(self, peaks: dict[str, float], dead: tuple[str, ...] = ()) -> None:
        rng = np.random.default_rng(7)
        self.peaks = peaks
        self.dead = set(dead)
        self.base = {
            name: (rng.integers(0, 4096, size=(TILE, TILE)) << 4).astype(np.uint16)
            for name in TILE_NAMES
        }

    def crops(self, pos: float) -> list[np.ndarray]:
        out = []
        for name in TILE_NAMES:
            if name in self.dead:
                sigma = 6.0        # permanently soft, no focus dependence
            else:
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


def test_tiles_stay_inside_the_given_region() -> None:
    """The screen covers only ~76% of the frame height on this rig, so tiles placed
    by frame fraction land off the panel on the room behind -- a different focus
    distance entirely, which reads as a big fake vertical tilt. Every tile must sit
    inside the region the caller passes (the fiducial quad)."""
    # Shipping geometry, not the small test tile: the escape depends on tile size.
    ship = FieldMapConfig()
    region = (258, 1140, 13142, 8388)   # the screen box at M=3.3553 on a 13400x9528
    rois = five_tile_rois(13400, 9528, ship, region)
    rx0, ry0, rx1, ry1 = region
    for name, (x0, y0, x1, y1) in zip(TILE_NAMES, rois):
        assert rx0 <= x0 and x1 <= rx1, f"{name} escapes the region horizontally"
        assert ry0 <= y0 and y1 <= ry1, f"{name} escapes the region vertically"
        assert x0 % 2 == 0 and y0 % 2 == 0
    # Without a region the top tile hangs off the top of that screen box -- the bug
    # this guards against is real at the shipping tile size, not hypothetical.
    naive = five_tile_rois(13400, 9528, ship)
    over = ry0 - naive[TILE_NAMES.index("top")][1]
    assert over > 0, "expected the un-regioned top tile to overhang the screen"
    print(f"   (un-regioned top tile overhangs the screen by {over} px)")
    print("ok: tiles confined to the fiducial region (and would escape without it)")


def test_the_tile_cross_is_centred_and_clears_the_bar_at_either_end() -> None:
    """Two properties, both learned the hard way on 2026-08-31.

    The box used to be trimmed on the single edge the DISPLAY config named. But the
    display names a screen edge and the optics invert, so the bar drawn at the screen's
    top images at the frame's BOTTOM: the trim cut the clean end and left the bar inside
    the box. The operator saw it as "the field map is not symmetric" -- the cross sat
    9.2% of the frame height below centre, measured against 9.5% predicted.

    That is not cosmetic. ``five_tile_rois`` distinguishes a tilted subject plane (top
    and bottom fall on OPPOSITE sides of centre) from a curved field (same side), and
    that reading is only valid if the two are equidistant from centre. It also cost
    baseline: the truncated span reported a 525c vertical tilt where the earlier, even
    more truncated one reported 301c -- the number moved with the geometry, not the
    optics.

    So: symmetric about the frame centre, and clear of a bar band at EITHER end."""
    from capture.field_map import inset_box

    W, H = 13400, 9528
    ship = FieldMapConfig()
    BAR = 0.085                      # measured strip height, fraction of frame height

    box = inset_box((0, 0, W, H), ship.box_inset)
    rois = five_tile_rois(W, H, ship, tuple(int(v) for v in box))
    named = dict(zip(TILE_NAMES, rois))

    def centre(roi):
        return 0.5 * (roi[0] + roi[2]), 0.5 * (roi[1] + roi[3])

    (cx, cy) = centre(named["centre"])
    (_, ty) = centre(named["top"])
    (_, by) = centre(named["bottom"])
    (lx, _) = centre(named["left"])
    (rx, _) = centre(named["right"])

    # Centred: the cross's own centre tile sits on the frame centre, and each pair
    # straddles it by equal amounts. One-sided trimming broke exactly this.
    assert abs(cy - H / 2) <= 2, f"centre tile is {cy - H / 2:+.0f}px off frame centre"
    assert abs(cx - W / 2) <= 2
    assert abs((cy - ty) - (by - cy)) <= 2, (
        f"top is {cy - ty:.0f}px above centre but bottom {by - cy:.0f}px below -- "
        "an off-centre cross reads a tilt as a curvature and vice versa")
    assert abs((cx - lx) - (rx - cx)) <= 2

    # Clear of the bar at EITHER end, since which end it images on is not knowable
    # from the display config. This is the assertion the old one-sided trim failed.
    for name in ("top", "bottom"):
        y0, y1 = named[name][1], named[name][3]
        assert y1 <= (1.0 - BAR) * H, f"{name} tile reaches into a bottom-edge bar"
        assert y0 >= BAR * H, f"{name} tile reaches into a top-edge bar"

    # Close to the edges: the tilt baseline is the measurement, and compounding three
    # insets had squeezed it to 40% of the frame height.
    span = (by - ty) / H
    assert span > 0.60, f"vertical baseline only {span * 100:.0f}% of frame height"
    print(f"ok: cross centred, bar cleared both ends, baseline {span * 100:.0f}% "
          f"of frame height (was 40%)")


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


def test_a_tile_with_no_focus_hump_is_flagged_and_excluded() -> None:
    """A tile that fell off the screen onto a blank wall has no focus dependence, so
    its 'vertex' is noise. It must not be allowed to set the reported spread -- that
    is how a 999c spread got reported off a tile that never had a peak."""
    peaks = {"centre": 1200.0, "top": 1000.0, "bottom": 1400.0,
             "left": 1100.0, "right": 1300.0}
    axis = OpenLoopAxis(lambda _s, _n: None, backlash_pulses=0, counts_per_pulse=CPP)
    scene = _Scene(peaks, dead=("bottom",))
    done: dict = {}
    with tempfile.TemporaryDirectory() as tmp:
        sweep = FieldMapSweep(
            axis=axis, config=_cfg(), csv_dir=tmp,
            finished=lambda ok, rep: done.update(rep, ok=ok),
        )
        sweep.start(five_tile_rois(4000, 3000, _cfg()))
        guard = 0
        while sweep.running and guard < 2000:
            sweep.feed(scene.crops(float(axis.position)))
            guard += 1
    res = {r.name: r for r in sweep.results()}
    assert not res["bottom"].trusted, "a flat tile was reported as trustworthy"
    assert res["bottom"].contrast < 5.0, f"contrast {res['bottom'].contrast:.1f}"
    for n in ("centre", "top", "left", "right"):
        assert res[n].trusted, f"{n} should still be trusted"
    joined = "\n".join(done["lines"])
    assert "SUSPECT" in joined and "bottom" in joined
    assert "excluded from the fit spread: bottom" in joined
    print("ok: dead tile flagged SUSPECT and kept out of the spread")


def test_abort_walks_the_lens_back_to_the_start() -> None:
    """Aborting mid-dive must undo itself. Stopping where it stands strands the lens
    a full dive below start; the next sweep zeroes there and its fixed-length climb
    runs out before reaching focus -- which is exactly how a clipped sweep reported
    a tight 20c spread off five tiles pinned to the same end sample."""
    peaks = {n: 1200.0 for n in TILE_NAMES}
    scene = _Scene(peaks)
    for stop_after in (2, 6, 40):        # mid-dive, end of dive, mid-climb
        axis = OpenLoopAxis(lambda _s, _n: None, backlash_pulses=0,
                            counts_per_pulse=CPP)
        # csv_dir MUST be set: None falls back to the real session-log folder, and
        # synthetic sweeps landing there get picked up by the analysis tools as if
        # they were rig data.
        with tempfile.TemporaryDirectory() as tmp:
            sweep = FieldMapSweep(axis=axis, config=_cfg(), csv_dir=tmp)
            sweep.start(five_tile_rois(4000, 3000, _cfg()))
            n = 0
            while sweep.running and n < 3000:
                sweep.feed(scene.crops(float(axis.position)))
                n += 1
                if n == stop_after:
                    sweep.stop("operator")
        home = abs(float(axis.position))
        assert home <= _cfg().return_step_pulses * CPP, (
            f"abort at frame {stop_after} left the lens {home:.0f}c from start"
        )
    print("ok: abort returns the lens home from mid-dive and mid-climb")


def test_a_peak_at_the_edge_of_the_sweep_is_refused() -> None:
    """A sweep whose range never reached focus must say so, not report a spread."""
    # The sweep spans about -96..+4704c here, so a peak at 5400 is still rising at
    # the top sample: sharp enough to give real contrast, but never bracketed.
    peaks = {n: 5400.0 for n in TILE_NAMES}
    axis = OpenLoopAxis(lambda _s, _n: None, backlash_pulses=0, counts_per_pulse=CPP)
    scene = _Scene(peaks)
    done: dict = {}
    with tempfile.TemporaryDirectory() as tmp:
        sweep = FieldMapSweep(axis=axis, config=_cfg(), csv_dir=tmp,
                              finished=lambda ok, rep: done.update(rep, ok=ok))
        sweep.start(five_tile_rois(4000, 3000, _cfg()))
        n = 0
        while sweep.running and n < 3000:
            sweep.feed(scene.crops(float(axis.position)))
            n += 1
    res = sweep.results()
    assert all(r.clipped for r in res), \
        f"clipped tiles: {[r.name for r in res if r.clipped]}"
    assert not any(r.trusted for r in res), "a clipped tile was called trusted"
    joined = "\n".join(done["lines"])
    assert "DID NOT BRACKET FOCUS" in joined
    assert "SPREAD (fit" not in joined, "reported a fit spread off clipped tiles"
    print("ok: an un-bracketed sweep is refused instead of reporting a spread")


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
        n = 0
        while sweep.running and n < 3000:   # abort returns home before reporting
            sweep.feed(scene.crops(float(axis.position)))
            n += 1
        assert not sweep.running
        assert done.get("ok") is False, "an aborted sweep must not claim success"
        assert done["samples"] > 0 and done["csv"]
        assert done["reason"] == "operator", f"lost the reason: {done['reason']}"
    print(f"ok: abort kept {done['samples']} samples and reported the abort")


def main() -> int:
    tests = [
        test_rois_are_bayer_aligned_and_inside_the_frame,
        test_tiles_stay_inside_the_given_region,
        test_a_flat_field_gives_every_tile_the_same_vertex,
        test_tilted_field_recovers_each_tile_vertex_and_their_differences,
        test_the_tile_cross_is_centred_and_clears_the_bar_at_either_end,
        test_a_tile_with_no_focus_hump_is_flagged_and_excluded,
        test_abort_walks_the_lens_back_to_the_start,
        test_a_peak_at_the_edge_of_the_sweep_is_refused,
        test_stop_midway_still_reports_what_it_measured,
    ]
    for t in tests:
        t()
    print(f"\n{len(tests)} field-map checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
