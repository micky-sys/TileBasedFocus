"""Self-checks for the 2026-11-02 flight fixes to the w10 autofocus (no hardware).

That flight lost whole motor revolutions in the host's 12-bit encoder unwrap during the 50-pulse back-off
bursts; the engine then drove the lens into its end stop, re-based there and swept 7000 c of blur, or ran its
budget out 600-1500 c short of focus while w10 was still falling. Three rules came out of it:

* :class:`capture.autofocus_w10.AutoFocusEngineW10` caps every burst in COUNTS (``max_counts_per_burst``),
  so a single line can never carry the shaft near half a turn whatever the probe said about counts/pulse.
* :class:`capture.af_w10.W10Focus` FAILS a back-off that stalls after moving more than ``backoff_stall_counts``
  (the count has lost revolutions, or the lens hit a stop far from where it started); a stall at the start
  point is still a legitimate re-base.
* ``W10Focus`` extends the sweep budget once when w10 is still falling at its end, instead of failing on the
  way down the flank.

And from the flight's five DONE runs (0.87 px frame scatter, three of them one-sided), 2026-11-09:

* a flank counts as seen only ``far_depths`` (2) depths past the vertex and from ``min_far`` (5) frames
  (``flank_seen``): the V has risen ~1.1 px there, not the 0.15 px the old 1-depth/3-frame rule accepted;
* ``dwell_frames`` holds the lens for N gated frames per station (the panel's "Dwell x3"), with the vertex CI
  bootstrapped by station, not by frame.

Run: ``py -3 tools/test_af_w10_flight.py``
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "host"))

from af_w10_replay import FlightLens, SyntheticLens, run_engine  # noqa: E402
from capture.af_w10 import W10Config, bootstrap_vertex  # noqa: E402
from capture.autofocus_w10 import AutoFocusController, AutoFocusEngineW10  # noqa: E402


class StoppedLens(SyntheticLens):
    """A lens with a hard stop on the back-off side: the motor cannot carry the count past ``stop``."""

    def __init__(self, stop: float, **kw) -> None:
        super().__init__(**kw)
        self.stop = float(stop)

    def move(self, target):
        super().move(min(float(target), self.stop))


def test_burst_cap_in_counts() -> None:
    sent: list[int] = []
    eng = AutoFocusEngineW10(pulse=lambda _s, n: sent.append(int(n)), encoder_read=lambda: 0.0,
                             counts_per_pulse=-24.0)
    try:
        for cpp in (14.0, 23.0, 35.0, 77.0, -30.0):
            sent.clear()
            eng._cpp = cpp
            eng._burst(target=3500.0, enc=0.0)
            assert len(sent) == 1, sent
            assert sent[0] <= 50 and sent[0] * abs(cpp) <= 700.0, (cpp, sent[0], sent[0] * abs(cpp))
            # the cap must not starve the drive: a few pulses at least, even at the 77 c/pulse mis-probe
            assert sent[0] >= 5, (cpp, sent[0])
        # a short move is untouched by the cap (exact pulses within exact_within_pulses)
        sent.clear(); eng._cpp = -24.0
        eng._burst(target=-72.0, enc=0.0)
        assert sent == [3], sent
        print(f"burst cap: 700 c holds for probe c/pulse 14..77 (e.g. 23 c -> {int(700 / 23)} pulses, not 50)")
    finally:
        eng.shutdown()


def test_known_polarity_skips_the_probe() -> None:
    sent: list[tuple[str, int]] = []
    msgs: list[str] = []
    eng = AutoFocusEngineW10(pulse=lambda s, n: sent.append((s, int(n))), encoder_read=lambda: 5000.0,
                             counts_per_pulse=-24.0, status=msgs.append, csv_dir=os.environ.get("TEMP", "."))
    try:
        eng.start(+31.0)           # preflight: '+' raises the count on this motor, 31 c at S0 (config: -24)
        assert eng._probe is None and eng._focus is not None, "straight into the sweep, no probe burst"
        assert eng._cpp == +31.0, eng._cpp
        assert sent == [], sent    # the first pulses come from W10Focus on its first frame, not here
        assert any("preflight" in m for m in msgs), msgs
        eng.stop("test")
        eng.start()                # no note -> probe as before
        assert eng._probe is not None and sent and sent[0][0] == "+", (eng._probe, sent)
        eng.stop("test")
    finally:
        eng.shutdown()

    # the controller composes the engine's signed c/pulse from the preflight notes at ITS strength
    class _Serial:
        connected = True
        encoder_position = 0.0
        encoder_present = True
        focus_step = staticmethod(lambda _s, _n: None)
        set_strength = staticmethod(lambda _s: None)

    ctl = AutoFocusController(_Serial(), None, counts_per_pulse=-24.0, pin_rois_cb=lambda _r: None)
    try:
        assert ctl._known_cpp(1) is None, "no note -> probe"
        ctl.motor_polarity[1] = -1.0
        assert ctl._known_cpp(1) == -24.0, "sign only -> config magnitude"
        ctl.motor_cpp[1] = {3: 850.0, 0: 31.0}
        assert ctl._engine.strength == 0 and ctl._known_cpp(1) == -31.0, "S0 magnitude from the calibration"
        print("known polarity: probe skipped, sign and S0 magnitude from preflight; probe still runs without a note")
    finally:
        ctl.shutdown()


def test_backoff_stall_far_from_start_fails() -> None:
    # 14000 -> back off to 17500 (start_offset 3500); the motor stops at 16800 = 2800 c out: not the start point's
    # limit, the count has run a revolution long (the flight) -- FAIL, do not sweep from the stop
    lens = StoppedLens(stop=16800.0)
    af = run_engine(lens, W10Config(direction=-1))
    assert af.state.value == "fail", af.log[-3:]
    assert "lost revolutions" in af.log[-1], af.log[-1]
    print(f"back-off stall after 2800 c: {af.log[-1]}")


def test_backoff_stall_at_start_rebases() -> None:
    # the lens is already against its stop 300 c out: the legitimate case -- re-base and sweep, and with the
    # vertex 2000 c below the start the run still parks
    lens = StoppedLens(stop=14300.0)
    af = run_engine(lens, W10Config(direction=-1))
    assert any("sweeping from here" in line for line in af.log), af.log[:4]
    assert af.state.value == "done", af.log[-3:]
    err = lens.lens_pos() - lens.e0
    assert abs(err) < 200, (err, af.log[-3:])
    print(f"back-off stall at 300 c: re-based, done in {af.frames} frames, lens error {err:+.0f} c")


def test_span_extends_while_falling() -> None:
    # vertex 7900 c below the back-off point: with the 7000 c budget the old engine failed here every time with
    # w10 falling 12 -> 5 px (14:41, 14:42, 15:49 on the flight). Still falling at the budget -> extend once.
    lens = SyntheticLens(e0_lens=17500.0 - 7900.0 - 500.0)   # -500: lash/2, the lens trails the count going down
    af = run_engine(lens, W10Config(direction=-1), max_frames=600)
    assert any("extending the sweep once" in line for line in af.log), af.log
    assert af.state.value == "done", af.log[-3:]
    err = lens.lens_pos() - lens.e0
    # within one depth of focus (+0.3 px): a 7900 c one-sided descent leans the V fit more than the
    # short sweeps the replay self-check parks to +-200 c from
    assert abs(err) < 400, (err, af.log[-3:])
    print(f"span extension: done in {af.frames} frames, lens error {err:+.0f} c")
    # ... but only once, and only while falling: a lens that never comes into focus still FAILS
    af = run_engine(SyntheticLens(e0_lens=-20000.0), W10Config(direction=-1, span_counts=5000), max_frames=600)
    assert af.state.value == "fail", af.log[-2:]
    assert af.span_extended, "the extension should have been spent before failing"
    print(f"span extension spent once, then: {af.log[-1]}")


_FLIGHT = dict(direction=-1, start_offset=2500, span_counts=7000, lash_counts=1500)


def _parks(seeds, **kw):
    errs, frames = [], []
    for s in seeds:
        lens = FlightLens(seed=s)
        af = run_engine(lens, W10Config(**_FLIGHT, **kw), max_frames=600)
        assert af.state.value == "done", (s, kw, af.log[-2:])
        errs.append(lens.lens_pos() - lens.e0); frames.append(af.frames)
    errs = np.array(errs)
    return float(np.sqrt(np.mean(errs ** 2))), float(np.abs(errs).max()), float(np.median(frames))


def test_far_flank_brackets_in_flight_noise() -> None:
    # The old rule (1 depth, 3 frames, +0.15 px) on the flight's noise: 5 of 40 parks outside the depth of
    # focus, worst 1036 c. The 2-depth / 5-frame rule: none outside, for ~5 more frames per run.
    seeds = range(20)
    rms_old, worst_old, fr_old = _parks(seeds, far_depths=1.0, min_far=3)
    rms, worst, fr = _parks(seeds)
    depth = 400.0   # FlightLens default
    assert worst <= depth, (worst, "a park outside the depth of focus with the far-flank rule")
    assert rms < rms_old, (rms, rms_old)
    assert fr - fr_old < 15, (fr, fr_old, "walking two depths out should cost a few frames, not a sweep")
    print(f"far flank: old rule rms {rms_old:.0f} c worst {worst_old:.0f} c in {fr_old:.0f} frames; "
          f"new rule rms {rms:.0f} c worst {worst:.0f} c in {fr:.0f} frames")


def test_dwell_holds_per_station_and_refines_finer() -> None:
    lens = FlightLens(seed=3)
    af = run_engine(lens, W10Config(**_FLIGHT, dwell_frames=3, tol_frac=0.1), max_frames=600)
    assert af.state.value == "done", af.log[-2:]
    per_station = np.bincount(np.asarray(af.station))
    per_station = per_station[per_station > 0]
    # every station the sweep finished got exactly 3 gated samples (the last may be cut short by the park)
    assert (per_station[:-1] == 3).all(), per_station
    assert len(af.station) >= 3 * 30, len(af.station)
    # the station bootstrap is the honest one: frames of a station share their ground, so counting them as
    # independent shrinks the CI by ~sqrt(3)
    e, w, st = np.array(af.enc), np.array(af.w10), np.array(af.station)
    lo_f, hi_f = bootstrap_vertex(e, w, 100, seed=1)
    lo_s, hi_s = bootstrap_vertex(e, w, 100, seed=1, blocks=st)
    assert (hi_s - lo_s) > 1.2 * (hi_f - lo_f), (hi_s - lo_s, hi_f - lo_f)
    # and with the park tolerance tightened to go with it, dwell x3 parks finer than creep (sim: 65 vs 96 c
    # rms over 40 seeds), at roughly 3x the frames
    seeds = range(12)
    rms_c, _, fr_c = _parks(seeds)
    rms_d, _, fr_d = _parks(seeds, dwell_frames=3, tol_frac=0.1)
    assert rms_d < rms_c, (rms_d, rms_c)
    assert fr_d < 3.5 * fr_c, (fr_d, fr_c)
    print(f"dwell x3: {len(per_station)} stations, CI by station {hi_s - lo_s:.0f} c vs by frame {hi_f - lo_f:.0f} c; "
          f"parks rms {rms_d:.0f} c in {fr_d:.0f} frames vs creep {rms_c:.0f} c in {fr_c:.0f}")


if __name__ == "__main__":
    test_burst_cap_in_counts()
    test_known_polarity_skips_the_probe()
    test_backoff_stall_far_from_start_fails()
    test_backoff_stall_at_start_rebases()
    test_span_extends_while_falling()
    test_far_flank_brackets_in_flight_noise()
    test_dwell_holds_per_station_and_refines_finer()
    print("OK")
