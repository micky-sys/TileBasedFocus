"""Closed-loop tests for the dive-then-climb autofocus engine (AF v2, no hardware).

Drives :class:`capture.autofocus_v2.AutoFocusEngineV2` with frames from
:class:`frame_grabber_sim.FocusSceneSimulator`, wiring the engine's focus-pulse
callback straight to the simulator (via the FocusAxis inside the engine) so the
whole loop closes in software. The simulator models the case v2 is built for: a
best-focus target seen through the optics with a gear-lash band, on an open-loop
motor whose shaft an encoder can read back.

The engine's search is: dive a fixed amount below focus (a few '-' bursts),
climb back up one small '+' step per frame taking a (position, score) sample each
frame, stop when the score has peaked-and-fallen, then park UPWARD onto the peak.

Covers:
* FocusAxis pulse/backlash bookkeeping, ``step_pulses`` (raw open-loop), and the
  upward park (pure unit check),
* dive-then-climb converging to best focus on a static scene and HOLDING (no drift),
* the shipped encoder wiring: a calibrated EncoderAxis (counts/pulse from the
  config) converging and parking on a strictly UPWARD final leg (lash from below),
* a one-shot run (continuous=False) finishing cleanly near best focus,
* a stuck motor (pulses do nothing) FAILING fast at the dive instead of climbing
  from nowhere.

Run: ``py -3 tools/test_autofocus_v2.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOST = REPO / "host"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HOST))

from capture.autofocus_v2 import (  # noqa: E402
    AFStateV2,
    AutoFocusConfigV2,
    AutoFocusEngineV2,
)
from capture.focus_axis import EncoderAxis, EncoderTracker, OpenLoopAxis  # noqa: E402
from frame_grabber_sim import FocusSceneSimulator  # noqa: E402


# ── helpers ─────────────────────────────────────────────────────────────────


def _cfg(**kw) -> AutoFocusConfigV2:
    """A fast, tight dive/climb config for the sim. Wall-clock gates (dive/park)
    are zeroed so the software loop runs at full speed and deterministically."""
    base = dict(
        dive_strength=3,
        dive_bursts=4,
        dive_burst_pulses=12,       # 4 x 12 = 48 pulses down (start is +24 above focus)
        dive_burst_interval_s=0.0,  # no wall-clock wait in the tight test loop
        dive_min_drop_counts=10,    # counts == pulses here (cpp defaults to 1.0)
        strength=0,
        climb_step_pulses=4,
        climb_max_pulses=200,
        peak_fall_frac=0.10,
        peak_confirm_samples=2,
        climb_min_samples=4,
        park_settle_s=0.0,
        settle_frames=2,
        backlash_pulses=2,
        min_contrast=0.05,
        drive_cadence_to_min=False,
        cadence_steps=0,
        max_low_texture_frames=80,
    )
    base.update(kw)
    return AutoFocusConfigV2(**base)


def _static_sim(start: float = 24.0, lash: int = 2) -> FocusSceneSimulator:
    """A NON-panning scene (step_px=0): blur depends only on focus position, so the
    score-vs-position curve is clean -- exactly the hand-focused bench case AF v2 is
    first meant to nail."""
    return FocusSceneSimulator(
        profile="translate", start_focus_pulses=start, best_focus_pulses=0.0,
        blur_per_pulse=0.16, step_px=0.0, backlash_pulses=lash,
    )


def run_v2(sim, *, config=None, continuous=True, frames=1500):
    """Run AF v2 against *sim* via the built-in OpenLoopAxis. Returns
    (engine, optical_positions, hold_frame)."""
    engine = AutoFocusEngineV2(
        pulse=sim.apply_pulse,
        status=lambda _m: None,
        finished=lambda _ok, _r: None,
        config=config or _cfg(),
    )
    engine.start(continuous=continuous)
    optical: list[float] = []
    hold_at: int | None = None
    for i in range(frames):
        engine.feed_frame(sim.render())
        sim.step()
        optical.append(sim.optical_pulses)
        if hold_at is None and engine.state == AFStateV2.HOLD:
            hold_at = i
        if not engine.running:
            break
    return engine, optical, hold_at


# ── unit: the axis ───────────────────────────────────────────────────────────


def test_axis_backlash_park_and_step_pulses() -> None:
    sent: list[tuple[str, int]] = []
    axis = OpenLoopAxis(lambda sym, n: sent.append((sym, n)), backlash_pulses=3)

    axis.move_to(10)  # first move, no reversal
    assert axis.position == 10 and axis.moved_last
    assert sent == [("+", 10)], sent

    sent.clear()
    axis.move_to(5)  # reversal: 3 backlash pulses (uncounted) + the 5-pulse move
    assert axis.position == 5
    assert sent == [("-", 3), ("-", 5)], sent
    assert axis.total_pulses == 10 + 3 + 5

    sent.clear()
    axis.move_to(5)  # already there -> nothing sent, moved_last False
    assert sent == [] and axis.moved_last is False

    # step_pulses: raw open-loop pulses that DO update position (used by dive/climb).
    sent.clear()
    axis.step_pulses(-1, 4)
    assert sent == [("-", 4)], sent
    assert axis.position == 1  # 5 - 4

    # Park upward lands ON the target with a strictly upward final leg.
    axis.park_upward(20)
    assert axis.position == 20, axis.position
    assert sent[-1] == ("+", 3), sent[-1]  # final leg is the +lash seat


# ── convergence + hold (static scene) ────────────────────────────────────────


def _assert_converged_and_held(engine, optical, hold_at, best=0.0, tol=8.0):
    assert engine.state != AFStateV2.FAILED, "engine failed to find focus"
    assert hold_at is not None, "never reached the HOLD (converged) state"
    final = optical[-1]
    assert abs(final - best) <= tol, f"parked at {final}, not near best {best} (tol {tol})"
    tail = optical[hold_at:]
    assert max(tail) - min(tail) <= 1e-6, (
        f"lens drifted while holding: spread {max(tail) - min(tail)}"
    )


def test_v2_static_dive_climb_converges_and_holds() -> None:
    sim = _static_sim(start=24.0)
    start_blur = sim.current_blur_sigma()
    engine, optical, hold_at = run_v2(sim, continuous=True, frames=1500)
    _assert_converged_and_held(engine, optical, hold_at, best=0.0)
    assert sim.current_blur_sigma() < 0.5 * start_blur, "did not sharpen"


def test_v2_static_recovers_from_gross_defocus() -> None:
    sim = _static_sim(start=40.0)  # far above focus; only found by climbing through
    start_blur = sim.current_blur_sigma()
    engine, optical, hold_at = run_v2(
        sim, config=_cfg(dive_burst_pulses=18), continuous=True, frames=1800
    )
    _assert_converged_and_held(engine, optical, hold_at, best=0.0, tol=10.0)
    assert sim.current_blur_sigma() < 0.5 * start_blur, "did not sharpen from gross defocus"


def test_v2_encoder_static_lands_from_below() -> None:
    """The shipped wiring: an EncoderAxis built from the CONFIG (counts/pulse,
    lash), converging on a static scene and parking on a strictly UPWARD final leg
    (gear lash seated from below), exactly as AutoFocusController does."""
    lash_pulses = 2
    cpp = 24.0  # sim polarity: '+' pulse drives the encoder UP (rig is the opposite; -24)
    sim = _static_sim(start=24.0, lash=lash_pulses)
    tracker = EncoderTracker()
    tracker.update_raw(int(round(sim.carrier_pulses * cpp)) % 4096)  # seed the stream
    sent: list[tuple[str, int]] = []

    def pulse(sym: str, n: int) -> None:
        sent.append((sym, int(n)))
        sim.apply_pulse(sym, n)
        tracker.update_raw(int(round(sim.carrier_pulses * cpp)) % 4096)

    # The *_counts defaults in _cfg are sized for the encoderless axis (1 count ==
    # 1 pulse), so scale them by cpp to keep the same PHYSICAL distances once a count
    # is 1/24 of a pulse -- the same scaling app.py does off its measured calibration.
    cfg = _cfg(
        encoder_counts_per_pulse=cpp, backlash_pulses=lash_pulses,
        dive_min_drop_counts=10 * cpp, park_stable_counts=2 * cpp,
        park_slow_band_counts=12 * cpp, park_climb_band_counts=36 * cpp,
        park_descend_margin_counts=20 * cpp,
    )
    axis = EncoderAxis(
        pulse, lambda: tracker.position,
        counts_per_pulse=cfg.encoder_counts_per_pulse,
        backlash_pulses=cfg.backlash_pulses,
    )
    assert axis.counts_per_pulse == cpp and axis.backlash_pulses == lash_pulses

    engine = AutoFocusEngineV2(
        pulse=lambda *_a: None, axis=axis,
        status=lambda _m: None, finished=lambda _ok, _r: None, config=cfg,
    )
    engine.start(continuous=True)
    optical: list[float] = []
    hold_at: int | None = None
    for i in range(2000):
        engine.feed_frame(sim.render())
        sim.step()
        optical.append(sim.optical_pulses)
        if hold_at is None and engine.state == AFStateV2.HOLD:
            hold_at = i
        if not engine.running:
            break

    assert engine.state != AFStateV2.FAILED, "calibrated encoder AF failed to focus"
    assert hold_at is not None, "never reached HOLD (converged) state"
    assert abs(optical[-1]) <= lash_pulses + cfg.climb_step_pulses, optical[-1]
    assert sent and sent[-1][0] == "+", f"final leg not upward (lash from below): {sent[-1]}"


def test_v2_one_shot_finishes_near_best() -> None:
    sim = _static_sim(start=20.0)
    reports: list[tuple[bool, dict]] = []
    engine = AutoFocusEngineV2(
        pulse=sim.apply_pulse, status=lambda _m: None,
        finished=lambda ok, r: reports.append((ok, r)), config=_cfg(),
    )
    engine.start(continuous=False)
    for _ in range(1500):
        engine.feed_frame(sim.render())
        sim.step()
        if not engine.running:
            break
    assert engine.state == AFStateV2.DONE, f"did not finish, state={engine.state}"
    assert reports and reports[-1][0] is True, "one-shot run did not report success"
    assert reports[-1][1].get("total_pulses", 0) > 0, "report missing pulse count"
    assert abs(sim.optical_pulses) <= 8.0, f"finished at {sim.optical_pulses}"


def test_v2_stuck_motor_fails_at_dive() -> None:
    """If pulses do nothing (motor jammed / wrong direction so the encoder never
    drops), the dive's downward-motion check must FAIL fast rather than climb from
    nowhere -- the guard that replaces the old runaway-into-the-end-stop behaviour."""
    sim = _static_sim(start=24.0)

    # An EncoderAxis whose readback never changes = a motor that isn't moving.
    axis = EncoderAxis(
        lambda _s, _n: None, lambda: 0.0,
        counts_per_pulse=24.0, backlash_pulses=2,
    )
    engine = AutoFocusEngineV2(
        pulse=lambda *_a: None, axis=axis,
        status=lambda _m: None, finished=lambda _ok, _r: None, config=_cfg(),
    )
    engine.start(continuous=True)
    for _ in range(400):
        engine.feed_frame(sim.render())
        sim.step()
        if not engine.running:
            break
    assert engine.state == AFStateV2.FAILED, (
        f"stuck motor should fail at the dive, state={engine.state}"
    )


def test_v2_stop_halts_in_place_no_move() -> None:
    """STOP must NOT move the motor. The old stop re-parked via park_upward() -- a big
    absolute burst that yanked the lens off focus and left pulses draining that
    corrupted the next run's dive (operator saw 'crazy rapid moves' on STOP)."""
    sim = _static_sim(start=20.0)
    engine, _optical, hold_at = run_v2(sim, continuous=True, frames=2000)
    assert hold_at is not None, "never reached HOLD"
    pos_before = sim.optical_pulses
    engine.stop()
    assert engine.state == AFStateV2.DONE, f"stop should finish, state={engine.state}"
    assert sim.optical_pulses == pos_before, (
        f"stop moved the motor {pos_before} -> {sim.optical_pulses}"
    )


def test_v2_climb_settle_frames_still_converges() -> None:
    """With climb_settle_frames>1 the climb takes N frames per step and scores only
    the last -- must still bracket the peak and land in focus (just slower)."""
    sim = _static_sim(start=20.0)
    engine, _optical, hold_at = run_v2(
        sim, config=_cfg(climb_settle_frames=3), continuous=True, frames=4000
    )
    assert hold_at is not None, "settled climb never reached HOLD"
    assert abs(sim.optical_pulses) <= 8.0, f"settled climb parked at {sim.optical_pulses}"


def test_v2_step_gate_pauses_each_phase_until_step() -> None:
    """With step_gate on, the engine must PAUSE at every phase boundary (no motor
    moves) and only advance when step() is called -- the debug harness the operator
    uses to read the encoder between phases. Here: it must sit in ACQUIRE (not dive)
    until the first step(), then move on."""
    sim = _static_sim(start=20.0)
    engine = AutoFocusEngineV2(
        pulse=sim.apply_pulse, status=lambda _m: None,
        finished=lambda _ok, _r: None, config=_cfg(step_gate=True),
    )
    engine.start(continuous=False)
    # Feed many frames -- without a step() it must stay parked in ACQUIRE.
    for _ in range(30):
        engine.feed_frame(sim.render())
        sim.step()
    assert engine.state == AFStateV2.ACQUIRE, (
        f"step_gate should hold at ACQUIRE until step(), state={engine.state}"
    )
    assert engine._paused, "engine should report paused at the gate"
    # Release gates until the run completes; each phase must require its own step().
    steps = 0
    for _ in range(3000):
        engine.feed_frame(sim.render())
        sim.step()
        if engine._paused:
            engine.step()
            steps += 1
        if not engine.running:
            break
    assert steps >= 4, f"expected a gate at each phase boundary, only saw {steps}"
    assert engine.state == AFStateV2.DONE, f"did not finish, state={engine.state}"
    assert abs(sim.optical_pulses) <= 8.0, f"finished at {sim.optical_pulses}"


def main() -> int:
    tests = [
        test_axis_backlash_park_and_step_pulses,
        test_v2_static_dive_climb_converges_and_holds,
        test_v2_static_recovers_from_gross_defocus,
        test_v2_encoder_static_lands_from_below,
        test_v2_one_shot_finishes_near_best,
        test_v2_stuck_motor_fails_at_dive,
        test_v2_stop_halts_in_place_no_move,
        test_v2_climb_settle_frames_still_converges,
        test_v2_step_gate_pauses_each_phase_until_step,
    ]
    for t in tests:
        t()
        print(f"  ok: {t.__name__}")
    print("autofocus v2 tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
