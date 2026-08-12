"""Self-checks for the AMT203S encoder path (no hardware).

Covers the two new pieces that turn the open-loop focus drive into a closed-loop
one, plus their end-to-end use in the sweep-and-hold engine:

* :class:`capture.focus_axis.EncoderTracker` -- parses/unwraps the ``$ENC=n*``
  stream (a 12-bit angle that rolls over every motor revolution) into a monotonic
  count, and reports absence on the firmware ``-1`` sentinel.
* :class:`capture.focus_axis.EncoderAxis` -- reads that count back as the axis
  position (so a move that stalls simply doesn't advance), scales counts->pulses,
  and still seats gear lash upward via ``park_upward``.
* AF v2 driven through an ``EncoderAxis`` over the frame simulator, with the
  encoder reading the sim's *carrier* (motor shaft, upstream of the lash), still
  converges on best focus and holds -- the whole point of the abstraction.

Run: ``py -3 tools/test_encoder_axis.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOST = REPO / "host"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HOST))

from capture.autofocus_v2 import AFStateV2, AutoFocusConfigV2, AutoFocusEngineV2  # noqa: E402
from capture.focus_axis import EncoderAxis, EncoderTracker  # noqa: E402
from frame_grabber_sim import FocusSceneSimulator  # noqa: E402


class _FakeMotor:
    """A motor shaft the encoder sits on. ``counts_per_pulse`` shaft counts move per
    pulse; the reported angle wraps at 4096 like the real AMT203S, and an
    :class:`EncoderTracker` folds it back into a monotonic count -- exactly the
    live path (pulse -> shaft turns -> $ENC arrives -> tracker unwraps)."""

    def __init__(self, start: int = 0, counts_per_pulse: int = 1) -> None:
        self.shaft = int(start)
        self.cpp = int(counts_per_pulse)
        self.sent: list[tuple[str, int]] = []
        self.tracker = EncoderTracker()
        self.tracker.update_raw(self.shaft % 4096)  # seed like a running stream

    def pulse(self, sym: str, n: int) -> None:
        self.sent.append((sym, int(n)))
        self.shaft += self.cpp * (int(n) if sym == "+" else -int(n))
        self.tracker.update_raw(self.shaft % 4096)

    def read(self) -> int:
        return self.tracker.position


# ── EncoderTracker ───────────────────────────────────────────────────────────


def test_tracker_unwraps_both_directions() -> None:
    t = EncoderTracker(counts_per_rev=4096)
    t.update_raw(4090)          # seed
    assert t.position == 0 and t.present
    t.update_raw(10)            # +16 across the top (4090 -> 4106 == 10)
    assert t.position == 16, t.position
    t.update_raw(4090)          # -16 back down across the top
    assert t.position == 0, t.position
    t.update_raw(4080)          # plain -10, no wrap
    assert t.position == -10, t.position


def test_tracker_parses_line_and_handles_sentinel() -> None:
    t = EncoderTracker()
    assert t.update_line("garbage without enc") is False
    assert t.present is False
    assert t.update_line("$ENC=1919*") is True
    assert t.present and t.position == 0  # first real reading seeds at 0
    assert t.update_line("$ENC=1925*") is True
    assert t.position == 6, t.position
    # firmware sentinel: encoder stopped responding
    assert t.update_line("$ENC=-1*") is True
    assert t.present is False


# ── EncoderAxis ──────────────────────────────────────────────────────────────


def test_encoder_axis_tracks_true_position_across_wrap() -> None:
    m = _FakeMotor(start=4090)  # sitting just below a wrap boundary
    axis = EncoderAxis(m.pulse, m.read, counts_per_pulse=1.0, backlash_pulses=3)
    axis.reset()
    assert axis.position == 0.0

    axis.move_to(20)  # drives the shaft up across the 4096 rollover
    assert m.shaft == 4110
    assert abs(axis.position - 20) < 1e-6, axis.position
    assert axis.moved_last

    axis.move_to(-10)  # reverse back across the rollover
    assert abs(axis.position - (-10)) < 1e-6, axis.position

    axis.move_to(-10)  # already there -> no pulses, moved_last clears
    assert axis.moved_last is False


def test_encoder_axis_counts_per_pulse_scaling() -> None:
    """Positions are COUNTS; the axis converts to whole pulses to command the move.

    4 encoder counts per pulse, so a 48-count move must be sent as 12 pulses -- not
    48. Positions were once carried in "pulse-equivalent units" (counts/cpp), which
    threw away cpp-1 of every cpp counts to rounding on each read and let distances
    that looked tight (a park tolerance of "4") really be 96 counts wide.
    """
    m = _FakeMotor(start=0, counts_per_pulse=4)
    axis = EncoderAxis(m.pulse, m.read, counts_per_pulse=4.0)
    axis.reset()
    axis.move_to(48)  # 48 counts / 4 counts-per-pulse -> 12 pulses
    assert m.sent[-1] == ("+", 12), m.sent
    assert m.shaft == 48
    assert abs(axis.position - 48) < 1e-6, axis.position

    # Sub-pulse requests round rather than pretending: 5 counts is closer to 1 pulse
    # (4 counts) than to 0, but 1 count is closer to standing still.
    axis.move_to(53)
    assert m.shaft == 52, m.shaft
    axis.move_to(53)
    assert axis.moved_last is False, "a sub-half-pulse request must be a no-op"


def test_encoder_axis_inverted_polarity_tracks_and_reports() -> None:
    # This rig's encoder counts DOWN on a '+' pulse. A NEGATIVE counts_per_pulse must
    # map "drive '+'" -> "position up" so the axis homes instead of walking off (the
    # -409/-580 runaway we saw on-rig). Also exercises total_pulses -- the _report
    # field whose absence on EncoderAxis crashed the finish AND the Stop button.
    m = _FakeMotor(start=0, counts_per_pulse=-24)  # '+' pulse drives the shaft DOWN
    axis = EncoderAxis(m.pulse, m.read, counts_per_pulse=-24.0, backlash_pulses=54)
    axis.reset()
    assert axis.position == 0.0 and axis.total_pulses == 0

    # +240 COUNTS == 10 '+' pulses at 24 counts each, sent '+' though the shaft falls.
    axis.move_to(240)
    assert m.sent[-1] == ("+", 10), m.sent
    assert abs(axis.position - 240) < 1e-6, axis.position
    assert axis.total_pulses == 10

    axis.park_upward(-120)  # land below start on a strictly upward final leg
    assert abs(axis.position - (-120)) < 1e-6, axis.position
    assert m.sent[-1][0] == "+", f"final leg not upward: {m.sent[-1]}"


def test_encoder_axis_park_upward_final_leg_is_up() -> None:
    m = _FakeMotor(start=200)
    axis = EncoderAxis(m.pulse, m.read, counts_per_pulse=1.0, backlash_pulses=3)
    axis.reset()  # position 0 == shaft 200
    m.sent.clear()
    axis.park_upward(-40)  # target below start: must overshoot below then come UP
    assert abs(axis.position - (-40)) < 1e-6, axis.position
    assert m.sent[-1][0] == "+", f"final leg not upward: {m.sent}"


# ── end-to-end: AF v2 through the encoder ─────────────────────────────────────


def _cfg(**kw) -> AutoFocusConfigV2:
    # Dive-then-climb config for the sim; wall-clock gates zeroed for the fast loop.
    base = dict(
        dive_strength=3, dive_bursts=4, dive_burst_pulses=12,
        dive_burst_interval_s=0.0, dive_min_drop_counts=10,
        strength=0, climb_step_pulses=4, climb_max_pulses=200,
        peak_fall_frac=0.10, peak_confirm_samples=2, climb_min_samples=4,
        park_settle_s=0.0, settle_frames=2, backlash_pulses=2, min_contrast=0.05,
        drive_cadence_to_min=False, cadence_steps=0, max_low_texture_frames=80,
        encoder_counts_per_pulse=1.0,
    )
    base.update(kw)
    return AutoFocusConfigV2(**base)


def test_v2_with_encoder_axis_converges_and_holds() -> None:
    # Static (non-panning) scene: blur depends only on focus, so the single-object
    # dive-then-climb curve is clean (the hand-focused bench case).
    sim = FocusSceneSimulator(
        profile="translate", start_focus_pulses=24.0, best_focus_pulses=0.0,
        blur_per_pulse=0.16, step_px=0.0,
    )
    tracker = EncoderTracker()
    tracker.update_raw(int(round(sim.carrier_pulses)) % 4096)  # seed the stream

    def pulse(sym: str, n: int) -> None:
        sim.apply_pulse(sym, n)
        # In flight the $ENC stream reports the shaft asynchronously; here the
        # sim's carrier IS the shaft, so update the tracker right after driving it.
        tracker.update_raw(int(round(sim.carrier_pulses)) % 4096)

    axis = EncoderAxis(pulse, lambda: tracker.position, counts_per_pulse=1.0,
                       backlash_pulses=2)
    engine = AutoFocusEngineV2(
        pulse=lambda *_a: None,  # unused: the injected axis owns all motion
        axis=axis,
        status=lambda _m: None,
        finished=lambda _ok, _r: None,
        config=_cfg(),
    )
    engine.start(continuous=True)
    optical: list[float] = []
    hold_at = None
    for i in range(1500):
        engine.feed_frame(sim.render())
        sim.step()
        optical.append(sim.optical_pulses)
        if hold_at is None and engine.state == AFStateV2.HOLD:
            hold_at = i
        if not engine.running:
            break

    assert engine.state != AFStateV2.FAILED, "encoder-driven AF failed to find focus"
    assert hold_at is not None, "never reached HOLD (converged) state"
    assert abs(optical[-1]) <= 8.0, f"parked at {optical[-1]}, not near best focus"
    tail = optical[hold_at:]
    assert max(tail) - min(tail) <= 1e-6, "lens drifted while holding"
    # The encoder-derived axis position must agree with the true motor shaft
    # (minus the start offset it zeroed to) -- otherwise the closed loop is blind.
    assert abs(axis.position - (sim.carrier_pulses - 24.0)) < 1e-6, axis.position


def test_v2_one_shot_report_has_total_pulses() -> None:
    # Guards the AttributeError that broke both the normal finish AND the Stop
    # button on-rig: _report read self._axis.total_pulses, which EncoderAxis lacked.
    sim = FocusSceneSimulator(
        profile="translate", start_focus_pulses=20.0, best_focus_pulses=0.0,
        blur_per_pulse=0.16, step_px=0.0,
    )
    tracker = EncoderTracker()
    tracker.update_raw(int(round(sim.carrier_pulses)) % 4096)

    def pulse(sym: str, n: int) -> None:
        sim.apply_pulse(sym, n)
        tracker.update_raw(int(round(sim.carrier_pulses)) % 4096)

    axis = EncoderAxis(pulse, lambda: tracker.position, counts_per_pulse=1.0,
                       backlash_pulses=2)
    reports: list[tuple[bool, dict]] = []
    engine = AutoFocusEngineV2(
        pulse=lambda *_a: None, axis=axis, status=lambda _m: None,
        finished=lambda ok, r: reports.append((ok, r)), config=_cfg(),
    )
    engine.start(continuous=False)  # one-shot: _finish -> _report -> _finished
    for _ in range(1500):
        engine.feed_frame(sim.render())
        sim.step()
        if not engine.running:
            break
    assert engine.state == AFStateV2.DONE, engine.state
    assert reports and reports[-1][0] is True, "one-shot did not report success"
    assert reports[-1][1].get("total_pulses", 0) > 0, reports[-1][1]


def main() -> int:
    for t in (
        test_tracker_unwraps_both_directions,
        test_tracker_parses_line_and_handles_sentinel,
        test_encoder_axis_tracks_true_position_across_wrap,
        test_encoder_axis_counts_per_pulse_scaling,
        test_encoder_axis_inverted_polarity_tracks_and_reports,
        test_encoder_axis_park_upward_final_leg_is_up,
        test_v2_with_encoder_axis_converges_and_holds,
        test_v2_one_shot_report_has_total_pulses,
    ):
        t()
        print(f"  ok: {t.__name__}")
    print("encoder-axis tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
