"""Tests for AF3, the station-walk autofocus (no hardware).

Two things need checking and they are quite separate:

* the METRIC -- that ``profile_metric`` is single-peaked in lens position, blind to
  patch content, biased high in a known direction, and refuses a half traverse. That
  lives in ``capture.autofocus_v3._selfcheck`` next to the code it describes, and is
  re-run from here so one command covers everything.

* the STATION LOOP -- that the walk actually advances: stations open, bank the
  traverses they are waiting on, close, step the lens, and terminate the sweep at
  ``sweep_counts`` instead of walking into the end stop. This is the part that would
  fail silently on the rig (a station that never closes just looks like a slow run),
  so it is driven directly here with stubbed scoring rather than through the tracker.

Run: ``py -3 tools/test_autofocus_v3.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOST = REPO / "host"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HOST))

import numpy as np  # noqa: E402

from capture.autofocus_v2 import (  # noqa: E402
    AFStateV2,
    AutoFocusConfigV2,
    _RunState,
    _TrackedObject,
)
from capture.autofocus_v3 import (  # noqa: E402
    AutoFocusConfigV3,
    AutoFocusEngineV3,
    _Station,
    _selfcheck,
    profile_metric,
)

FW, FH = 800, 600


def _cfg(**kw) -> AutoFocusConfigV3:
    """Counts == pulses (cpp 1.0) so the sweep arithmetic is readable, and every
    wall-clock gate zeroed so the loop runs deterministically at full speed."""
    base = dict(
        station_step_pulses=6,
        station_objects=3,
        station_min_traverses=2,
        station_max_seconds=1e9,   # never time out: the test banks real traverses
        sweep_counts=30.0,         # 6 counts a station -> closes after 5 steps
        profile_min_span_frac=0.55,
        encoder_counts_per_pulse=1.0,
        backlash_pulses=0,
        park_settle_s=0.0,
        dive_burst_interval_s=0.0,
        drive_cadence_to_min=False,
    )
    base.update(kw)
    return AutoFocusConfigV3(**base)


def _engine(cfg, pulses=None, profile_rows=None):
    eng = AutoFocusEngineV3(
        pulse=(lambda sym, n: pulses.append((sym, n))) if pulses is not None
        else (lambda sym, n: None),
        status=lambda _m: None,
        finished=lambda _ok, _r: None,
        config=cfg,
        profile_cb=(profile_rows.append if profile_rows is not None else None),
    )
    eng._rs = _RunState(continuous=False)
    return eng


# â”€â”€ the metric â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def test_metric_properties() -> None:
    """Delegates to the self-check that ships beside the metric."""
    _selfcheck()


def test_metric_axis_is_taken_from_the_trail() -> None:
    """A VERTICAL traverse must be measured against frame height, not width -- the
    rig runs OrthoSim both ways and there is no flag telling the engine which."""
    cfg = _cfg()
    # travels in y, x fixed; peak at the middle of the frame height
    trail = [(0.5 * FW, FH * (i + 0.5) / 20,
              1.0 + 2.0 * np.exp(-(((i + 0.5) / 20 - 0.5) / 0.18) ** 2))
             for i in range(20)]
    got = profile_metric(trail, FW, FH, cfg)
    assert got is not None, "a full vertical traverse must be accepted"
    d, u = got
    assert d > 0.5, f"a centre-peaked vertical profile must read as a dome, got D={d}"
    assert u is not None and abs(u - 0.5) < 0.05, f"u* should be mid-frame, got {u}"


# â”€â”€ the station loop â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def _walk(eng, cfg, *, d_of_pos=lambda pos: 0.0, max_frames=4000):
    """Drive the station walk with stubbed scoring.

    Stands in for ``_advance_objects``: objects march across the frame and are
    retired at the far edge, which is the signal AF3 uses to close a traverse. The
    score handed back is shaped so each traverse yields a KNOWN D, namely
    ``d_of_pos(station_pos)`` -- so a test can plant a hill and check the fit.
    """
    rs = eng._rs
    eng.state = AFStateV2.CLIMB
    eng._stations = []
    eng._open_station()
    frame = np.zeros((FH, FW), dtype=np.uint8)
    step = 40                      # px per frame -> 20 frames to cross
    oid = 0
    for _ in range(max_frames):
        if eng.state != AFStateV2.CLIMB:
            break
        # spawn up to the object budget, staggered so traverses complete steadily
        while len(rs.objects) < eng._want_objects():
            rs.objects.append(_TrackedObject(oid=oid, roi=(0, 250, 100, 350), born=0))
            oid += 1
            break                  # one per frame, like the real stagger
        # march, retiring anything that reached the far edge
        alive = []
        for o in rs.objects:
            x0, y0, x1, y1 = o.roi
            if x1 + step > FW:
                continue           # panned off -> AF3 sees the oid vanish
            o.roi = (x0 + step, y0, x1 + step, y1)
            alive.append(o)
        rs.objects = alive
        # A centre-peaked profile whose DEPTH encodes the wanted D for this station.
        amp = np.exp(d_of_pos(eng._station.pos)) - 1.0 if eng._station else 0.0
        def _score(o, amp=amp):
            u = 0.5 * (o.roi[0] + o.roi[2]) / FW
            return 1.0 + max(amp, 0.0) * np.exp(-((u - 0.5) / 0.30) ** 2)
        eng._observe_scores = lambda _f, _objs=None: [_score(o) for o in rs.objects]
        rs.frames += 1
        eng._climb_tick(frame)
    return eng._stations


def test_station_loop_advances_and_ends_the_sweep() -> None:
    cfg = _cfg()
    pulses: list = []
    eng = _engine(cfg, pulses=pulses)
    stations = _walk(eng, cfg)

    # 30 counts of sweep at 6 a station -> 5 steps, so 6 stations get opened and the
    # sixth close triggers _finish_climb.
    assert len(stations) >= 5, f"expected the sweep to close ~6 stations, got {len(stations)}"
    assert all(s.d is not None for s in stations), "every closed station needs a D"
    positions = [s.pos for s in stations]
    assert positions == sorted(positions), f"stations must walk one way, got {positions}"
    assert len(set(positions)) == len(positions), "two stations at one position"
    gaps = {b - a for a, b in zip(positions, positions[1:])}
    assert gaps == {cfg.station_step_pulses}, \
        f"stations should be {cfg.station_step_pulses} counts apart, saw gaps {gaps}"
    assert eng.state != AFStateV2.CLIMB, "the sweep must terminate, not run forever"
    assert any(sym == "+" for sym, _n in pulses), "the walk must drive the lens UP"


def test_a_new_station_starts_with_an_empty_roster() -> None:
    """A patch that was mid-traverse when the lens stepped is worthless AND expensive.

    Its trail was just dropped, so what it has left to cross is a partial arc that
    ``profile_metric`` rejects -- live they were 82 of 167 objects -- and until it pans
    off it holds a roster slot that a fresh leading-edge patch needs.
    """
    eng = _engine(_cfg())
    eng._rs.objects = [_TrackedObject(oid=99, roi=(0, 250, 100, 350), born=0)]
    eng._open_station()
    assert eng._rs.objects == [], "a leftover patch was carried into the new station"


def test_af3_drops_af2s_handover_stagger() -> None:
    """AF3 has no handover to protect, so births go at the acquire path's max rate."""
    assert AutoFocusConfigV3().handover_stagger_frames == 1, \
        "AF3 births must not be spaced out; the stagger was ~20 of a station's 29 frames"
    assert AutoFocusConfigV2().handover_stagger_frames > 1, \
        "AF2 still needs its stagger -- a pair entering the FOV together cannot stitch"


class _LaggingPosition:
    """Axis whose ``position`` reports the pre-step value for one read after a step.

    The stub axis moves synchronously, so it cannot show this class of bug at all: on
    the rig the position comes back over serial from the encoder and a step issued this
    tick is not visible until a later one.
    """

    def __init__(self, axis) -> None:
        self._axis = axis
        self._shown = axis.position
        self._pending = None

    def __getattr__(self, name):     # everything else passes straight through
        return getattr(self._axis, name)

    @property
    def position(self):
        out = self._shown
        if self._pending is not None:
            self._shown, self._pending = self._pending, None
        return out

    def step_pulses(self, sym, n):
        self._axis.step_pulses(sym, n)
        self._pending = self._axis.position


def test_station_position_is_the_settled_one_not_the_pre_step_read() -> None:
    """Each station must be filed at the position it DWELT at.

    ``_open_station`` runs in the same tick as the step that created the station, so a
    position captured there is the previous station's. That pairs every D with a
    position one step behind, and the parabola fitted through them puts its vertex a
    whole station off -- live on 2026-09-09 the walk logged two stations at -3412c and
    parked +155c from its own D peak.
    """
    cfg = _cfg()
    eng = _engine(cfg)
    eng._axis = _LaggingPosition(eng._axis)
    stations = _walk(eng, cfg)

    positions = [s.pos for s in stations]
    assert len(positions) >= 5, f"the walk did not run: {positions}"
    assert len(set(positions)) == len(positions), \
        f"two stations filed at one position (stale pre-step read): {positions}"
    gaps = {b - a for a, b in zip(positions, positions[1:])}
    assert gaps == {cfg.station_step_pulses}, \
        f"stations should be {cfg.station_step_pulses}c apart, saw {gaps}: {positions}"


def test_preview_shows_only_this_station_and_only_completed_traverses() -> None:
    """The overlay must show the completed traverses of the CURRENT station only.

    Both halves matter. Carrying a previous station's curves over would invite
    comparing traverses taken at two different lens positions, which is the one
    comparison a station walk exists to avoid; and publishing in-progress trails
    would draw half-crossings that contributed no D.
    """
    cfg = _cfg(sweep_counts=42.0)
    rows: list = []
    eng = _engine(cfg, profile_rows=rows)
    _walk(eng, cfg, d_of_pos=lambda pos: 1.0 - 0.004 * (pos - 18.0) ** 2)

    assert rows, "the preview callback was never fired"
    seen_station = {}
    for r in rows:
        st = r["station"]
        n = len(r["completed"])
        # within a station the count only ever grows; it must RESET on a new station
        assert n >= seen_station.get(st, 0), f"station {st} lost a completed traverse"
        seen_station[st] = n
        assert n <= len(r["banked"]), \
            "a curve is shown that banked no D (in-progress trail leaked in)"
        for _oid, trail in r["completed"]:
            assert len(trail) >= 6, "a partial traverse reached the preview"

    later = [r for r in rows if r["station"] > 1 and r["completed"]]
    assert later, "no station past the first ever completed a traverse"
    assert min(len(r["completed"]) for r in rows if r["station"] == max(seen_station)) == 0, \
        "a fresh station must start with an empty curve set"


def test_station_needs_its_traverses_before_closing() -> None:
    """A station must not close on the first object through -- the median over
    several completed traverses is what makes one mangled patch harmless."""
    cfg = _cfg(station_min_traverses=3, sweep_counts=6.0)
    eng = _engine(cfg)
    stations = _walk(eng, cfg)
    assert stations, "at least one station must close"
    # Rebuild what the loop banked for the first station: it can only have closed
    # once 3 traverses were in hand, so it cannot have closed early.
    assert len(stations) <= 2, f"sweep_counts=6 allows ~2 stations, got {len(stations)}"


def test_finish_climb_parks_at_the_fitted_vertex() -> None:
    """Plant a hill in D across the stations and check AF3 parks at its vertex
    rather than at the argmax station, and that park_bias_counts shifts it."""
    peak_at = 18.0

    def hill(pos: float) -> float:
        return 1.2 - 0.004 * (pos - peak_at) ** 2

    cfg = _cfg(sweep_counts=42.0)
    eng = _engine(cfg)
    _walk(eng, cfg, d_of_pos=hill)
    assert eng.state == AFStateV2.PARK, f"expected PARK, got {eng.state}"
    target = eng._rs.park_descend_target
    assert target is not None
    # The park target is derived from the fitted vertex; the fit must land on the
    # planted peak to within a fraction of the station spacing.
    fitted = [s for s in eng._stations if s.d is not None]
    best = max(fitted, key=lambda s: s.d).pos
    assert abs(best - peak_at) <= cfg.station_step_pulses, \
        f"argmax station {best} should bracket the planted peak {peak_at}"

    # ...and the bias knob must actually move the park target.
    eng2 = _engine(_cfg(sweep_counts=42.0, park_bias_counts=25.0))
    _walk(eng2, eng2.cfg, d_of_pos=hill)
    assert eng2.state == AFStateV2.PARK
    assert eng2._rs.park_descend_target < target, \
        "park_bias_counts must pull the park target DOWN in counts"


# â”€â”€ the early exit (both of run B's failure modes) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

# D per station from the real 2026-09-09 AF3 run, stations 4..34 in walk order
# (host/Session logs/Iron661_observe_20260909_101055.csv, recomputed by
# tools/af3_run_report.py). Its sweep ended on the BUDGET with the raw centre score
# still at its maximum, so this sequence is a genuine one-sided bracket -- and its
# pedestal contains the wobbles that a naive "D fell" stop would fire on.
RUN_B_D = [
    -0.396, -0.318, -0.326, -0.420, -0.374, -0.368, -0.319, -0.290, -0.450,
    -0.316, -0.337, -0.187, -0.130, -0.208, -0.156, -0.207, -0.099, -0.087,
    -0.089, -0.164, -0.086, -0.010, 0.085, 0.196, 0.294, 0.360, 0.476, 0.448,
    0.599, 0.546, 0.451,
]


def _with_stations(eng, ds):
    eng._stations = [_Station(pos=6 * i, t0=0.0, d=d) for i, d in enumerate(ds)]
    return eng


def test_early_exit_never_fires_on_run_b() -> None:
    """Run B never saw a far flank, so no prefix of it may be called bracketed.

    This is the guard that matters: three of its pedestal stations DO sit below a
    running max (-0.318, then -0.326/-0.420/-0.374), 1300 counts short of focus.
    """
    eng = _engine(_cfg())
    for n in range(1, len(RUN_B_D) + 1):
        _with_stations(eng, RUN_B_D[:n])
        assert not eng._past_the_peak(), (
            f"false bracket after {n} stations (D={RUN_B_D[n - 1]:+.3f}); run B's "
            f"score was still climbing at its last station"
        )


def test_early_exit_fires_once_the_far_flank_is_real() -> None:
    """Continue run B past its budget with a genuine descent and it must stop."""
    eng = _engine(_cfg())
    # run B ended at 0.451, already 0.148 below its 0.599 max -> two more clear
    # drops make three stations past the argmax.
    _with_stations(eng, RUN_B_D + [0.34, 0.21])
    assert eng._past_the_peak(), "three stations below the max must bracket the peak"


def test_early_exit_stays_disarmed_on_a_flat_sweep() -> None:
    """D that never climbs out of its pedestal must fall through to the backstop --
    a dip in flat noise says nothing, because D is even on both flanks."""
    eng = _engine(_cfg(station_arm_rise=0.35))
    flat = [-0.30, -0.28, -0.33, -0.29, -0.35, -0.31, -0.34, -0.30, -0.36]
    assert max(flat) - min(flat) < 0.35, "fixture must stay inside the arm threshold"
    _with_stations(eng, flat)
    assert not eng._past_the_peak(), "noise on the pedestal is not a far flank"


def test_walk_stops_early_and_reports_it_as_bracketed() -> None:
    """End to end: plant a hill well inside the budget and the walk must stop on the
    peak rather than spending the whole sweep, and must report bracketed=True."""
    peak_at = 30.0

    def hill(pos: float) -> float:
        return 1.0 - 0.003 * (pos - peak_at) ** 2

    cfg = _cfg(sweep_counts=300.0)      # 50 stations of budget, far more than needed
    eng = _engine(cfg)
    _walk(eng, cfg, d_of_pos=hill)
    assert eng.state == AFStateV2.PARK, f"expected PARK, got {eng.state}"
    n = len(eng._stations)
    assert n < 40, f"early exit should end the walk well before 50 stations, got {n}"
    ds = [s.d for s in eng._stations if s.d is not None]
    past = len(ds) - 1 - ds.index(max(ds))
    assert past >= cfg.station_descend_stop, \
        f"must stop only after {cfg.station_descend_stop} stations past the peak, got {past}"


# â”€â”€ the acquisition lane â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def test_lane_filter_keeps_the_middle_of_the_cross_axis() -> None:
    """Vertical flow must select the lane on x, horizontal flow on y -- and the
    filter must be a no-op when the knob is off, so AF2/flight are untouched."""
    from capture.autofocus import _tiles_in_centre_lane
    from tile_geometry import Tile

    # five tile columns across, all in one row
    cols = [Tile(x0=c, y0=0, x1=c + 100, y1=100) for c in (0, 200, 400, 600, 800)]
    kept = _tiles_in_centre_lane(cols, "top", 0.34)
    xs = [(t.x0 + t.x1) / 2 for t in kept]
    assert xs == [450.0], f"vertical flow should keep the centre column, got {xs}"

    # same tiles, horizontal flow: they are all in ONE row, so the cross-axis (y)
    # spread is zero and nothing may be discarded.
    assert len(_tiles_in_centre_lane(cols, "left", 0.34)) == len(cols), \
        "a zero cross-axis spread must not filter anything out"

    rows = [Tile(x0=0, y0=r, x1=100, y1=r + 100) for r in (0, 200, 400, 600, 800)]
    kept = _tiles_in_centre_lane(rows, "left", 0.34)
    ys = [(t.y0 + t.y1) / 2 for t in kept]
    assert ys == [450.0], f"horizontal flow should keep the centre row, got {ys}"

    assert len(_tiles_in_centre_lane(cols, "top", 1.0)) == len(cols), \
        "lane_frac=1 must keep everything"
    assert _tiles_in_centre_lane([], "top", 0.34) == [], "empty in, empty out"


def test_lane_filter_never_starves_acquisition() -> None:
    """An absurdly narrow lane must still yield a tile -- returning nothing would
    stall every station rather than merely widen its spread."""
    from capture.autofocus import _tiles_in_centre_lane
    from tile_geometry import Tile

    # centres at 50 and 850: nothing sits near the midpoint 450
    edges = [Tile(x0=0, y0=0, x1=100, y1=100), Tile(x0=800, y0=0, x1=900, y1=100)]
    kept = _tiles_in_centre_lane(edges, "top", 0.001)
    assert len(kept) == 1, f"must fall back to one tile, got {len(kept)}"


def test_af3_turns_the_lane_on_and_af2_leaves_it_off() -> None:
    """AutoFocusConfigV2 is a SEPARATE dataclass, not a subclass of AutoFocusConfig,
    so the shared picker has to read this knob defensively -- and does. Pinned here
    because the day V2 grows the field, the getattr default stops being the thing
    that keeps AF2 and flight on the old behaviour."""
    from capture.autofocus import AutoFocusConfig
    from capture.autofocus_v2 import AutoFocusConfigV2

    assert AutoFocusConfig().acquire_lane_frac == 0.0, "flight default must stay off"
    assert not hasattr(AutoFocusConfigV2(), "acquire_lane_frac"), \
        "V2 gained the field: the picker's getattr default no longer protects AF2"
    assert 0.0 < AutoFocusConfigV3().acquire_lane_frac < 1.0, \
        "AF3 must restrict acquisition to a lane"


def test_af3_lands_in_done_not_hold() -> None:
    """A one-shot AF3 run must reach DONE and FIRE finished, not sit in HOLD.

    HOLD looks harmless -- reverify_interval_s is 0, so the lens does not move -- but
    `finished` is what closes the observe CSV, and ObserveLog only flushes every 50
    rows, so a run that never finishes leaves its tail in the buffer. AF3 sat in HOLD
    for its first day because app.py named the one-shot engines instead of naming the
    one that cannot land.
    """
    for continuous, want in ((False, AFStateV2.DONE), (True, AFStateV2.HOLD)):
        eng = _engine(_cfg())
        eng._rs = _RunState(continuous=continuous)
        fired: list = []
        eng._finished = lambda ok, rep, _f=fired: _f.append(ok)
        eng._settle_and_hold(1234)
        assert eng.state == want, \
            f"continuous={continuous} should land in {want}, got {eng.state}"
        assert bool(fired) is (not continuous), \
            f"continuous={continuous}: finished fired={bool(fired)}"


def test_too_few_stations_fails_rather_than_parking_on_noise() -> None:
    cfg = _cfg(sweep_counts=0.0)   # terminate after the very first station
    eng = _engine(cfg)
    _walk(eng, cfg)
    assert eng.state == AFStateV2.FAILED, \
        f"a sweep with <3 usable stations must FAIL, not park; got {eng.state}"


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print("ok   %s" % fn.__name__)
    print("\n%d AF3 tests passed" % len(tests))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
