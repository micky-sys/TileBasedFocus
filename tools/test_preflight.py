"""Self-checks for the preflight runner (no hardware).

A fake rig answers the :class:`capture.preflight.PreflightHooks` callables: PCB acks and
mode banners arrive a little after each command, focus pulses move a per-camera encoder
with a per-camera polarity, M4 triggers the live cameras at ~5 fps, ``$Click*`` yields 40
frames per live camera and a ``Swp#``, writers lag acquisition, GPS matches every frame.
The runner is ticked by hand against a fake clock, so a 3-minute preflight runs in a few
hundred milliseconds and every timeout is exercised at its real value.

Run: ``py -3 tools/test_preflight.py``
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "host"))

from PySide6.QtCore import QCoreApplication  # noqa: E402

from capture import preflight as pf  # noqa: E402
from capture.preflight import PreflightHooks, PreflightRunner  # noqa: E402

_app = QCoreApplication.instance() or QCoreApplication([])


class FakeRig:
    FRAMES_PER_SWEEP = 40

    def __init__(self, cams=(1, 2, 3), *, dead=(), dead_after_recovery=(), stall=None,
                 gps=True, ack=True, fault_on_mode=None, cpp=850.0):
        self.t = 0.0
        self.cams = list(cams)
        self.alive = {c: c not in dead for c in cams}
        self.dead_after_recovery = set(dead_after_recovery)
        self.stall = stall or {}            # cam -> counts of '+' travel after which the motor jams
        self.gps = gps
        self.ack = ack
        self.fault_on_mode = fault_on_mode  # mode number whose homing prints a fault line
        self.cpp = cpp
        self.cpp_by_strength = {3: cpp, 2: 300.0, 1: 70.0, 0: 30.0}
        self.strength = 3
        self.mode = 0
        self.selected = None
        self.enc = {c: 1000.0 * c for c in cams}
        self.start_enc = dict(self.enc)
        self.polarity = {c: (1.0 if c != 2 else -1.0) for c in cams}   # '+' moves cam2 the other way
        self.enc_fresh_at = {c: math.inf for c in cams}
        self.acq = {c: 0 for c in cams}
        self.delivered = {c: 0 for c in cams}   # SDK callbacks (every armed camera)
        self.preview = cams[0]                  # the one camera whose frames the host consumes in M4
        self.written = {c: 0 for c in cams}
        self.dropped = {c: 0 for c in cams}
        self.gps_matched = {c: 0 for c in cams}
        self.pending = []           # (due_t, callable)
        self.calls = []             # hook call log
        self.sweeps = 0
        self.recoveries = 0
        self.rotated = 0
        self.last_m4_frame_t = 0.0
        self.runner: PreflightRunner | None = None

    # -- scheduling ----------------------------------------------------------------------
    def later(self, dt, fn):
        self.pending.append((self.t + dt, fn))

    def line(self, text):
        self.runner.on_serial_line(text)

    def step(self, dt=pf.TICK_S):
        self.t += dt
        due = [p for p in self.pending if p[0] <= self.t]
        self.pending = [p for p in self.pending if p[0] > self.t]
        for _, fn in sorted(due, key=lambda p: p[0]):
            fn()
        if self.mode == 4 and self.t - self.last_m4_frame_t >= 0.2:
            self.last_m4_frame_t = self.t
            for c in self.cams:
                if self.alive[c]:
                    self.delivered[c] += 1
                    if c == self.preview:   # the host idle-gates the other cameras in M4: acq stays put
                        self.acq[c] += 1
        self.runner.tick()

    def run(self, max_s=600.0):
        done = {}
        self.runner.finished.connect(lambda ok, res: done.update(ok=ok, res=res))
        assert self.runner.start()
        while not done and self.t < max_s:
            self.step()
        assert done, f"runner did not finish within {max_s} s (fake time)"
        return done["ok"], done["res"]

    # -- hooks -----------------------------------------------------------------------------
    def send_mode(self, n):
        self.calls.append(("mode", n))
        self.mode = None   # homing
        if self.ack:
            self.later(0.2, lambda: self.line(f"ack: $M{n}*;  A# 7"))
        if self.fault_on_mode == n:
            self.later(1.0, lambda: self.line("Motor alert detected."))
            self.later(1.1, lambda: self.line("Motor0:    MotorFaulted "))
            return True

        def homed():
            self.mode = n
            self.line(f"Mode {n}: whatever")
        self.later(4.0, homed)
        return True

    def start_sweep(self):
        self.calls.append(("click",))
        assert self.mode == 0, "$Click* only sweeps in M0"
        self.sweeps += 1
        for k in range(self.FRAMES_PER_SWEEP):
            def frame(k=k):
                for c in self.cams:
                    if self.alive[c]:
                        self.acq[c] += 1
                        self.delivered[c] += 1
                        if self.gps:
                            self.gps_matched[c] += 1
                        self.later(0.4, lambda c=c: self.written.__setitem__(c, self.written[c] + 1))
            self.later(0.2 * (k + 1), frame)
        self.later(0.2 * self.FRAMES_PER_SWEEP + 0.3, lambda: self.line("Swp# 12;  Total sweep time 8400;  Net sweep time 8100"))

    def select_camera(self, cam):
        self.calls.append(("select", cam))
        self.selected = cam
        self.enc_fresh_at[cam] = self.t + 0.2

    def set_strength(self, s):
        self.calls.append(("strength", s))
        self.strength = s

    def pulse(self, sym, n):
        assert self.mode == 4
        cam = self.selected
        self.calls.append(("pulse", cam, sym, n))
        direction = (1.0 if sym == "+" else -1.0) * self.polarity[cam]
        travel = abs(self.enc[cam] - self.start_enc[cam])
        if sym == "+" and cam in self.stall and travel >= self.stall[cam]:
            return   # jammed: nothing moves
        step = self.cpp_by_strength[self.strength]
        self.later(0.3, lambda: self.enc.__setitem__(cam, self.enc[cam] + direction * step * n))

    def encoder_position(self, cam):
        return self.enc[cam]

    def encoder_present(self, cam):
        return self.t >= self.enc_fresh_at[cam]

    def encoder_age(self, cam):
        return 0.05 if self.selected == cam and self.t >= self.enc_fresh_at[cam] else 99.0

    def frame_counts(self, cam):
        return (self.acq[cam], self.written[cam], self.dropped[cam])

    def gps_stats(self):
        return {"cameras": {c: {"matched": self.gps_matched[c], "fallback_latest": 0, "stale": 0,
                                "out_of_future": 0, "no_event": 0} for c in self.cams}}

    def recover_cameras(self):
        self.calls.append(("recover",))
        self.recoveries += 1
        self.mode = None
        self.later(0.2, lambda: self.line("ack: $M0*;  A# 9"))

        def homed():
            self.mode = 0
            self.line("Mode 0: Collection On, Nav point based")
        self.later(4.0, homed)

        def done():
            for c in self.cams:
                self.alive[c] = c not in self.dead_after_recovery
            self.runner.on_recovery_done(len(self.cams))
        self.later(20.0, done)

    def rotate_lines(self):
        self.rotated += 1
        self.calls.append(("rotate",))

    def hooks(self, **over):
        h = dict(
            serial_connected=lambda: True,
            send_mode=self.send_mode, start_sweep=self.start_sweep,
            select_camera=self.select_camera, set_strength=self.set_strength, pulse=self.pulse,
            encoder_position=self.encoder_position, encoder_present=self.encoder_present,
            encoder_age=self.encoder_age, cameras=lambda: list(self.cams), armed=lambda c: True,
            frame_counts=self.frame_counts, frames_delivered=lambda c: self.delivered[c],
            gps_connected=lambda: self.gps, gps_stats=self.gps_stats,
            recording_prerequisites_missing=lambda: [], recover_cameras=self.recover_cameras,
            rotate_lines=self.rotate_lines, log=lambda m: None,
        )
        h.update(over)
        return PreflightHooks(**h)


def make(**kw):
    over = kw.pop("hooks", {})
    rig = FakeRig(**kw)
    rig.runner = PreflightRunner(rig.hooks(**over), clock=lambda: rig.t)
    return rig


def names(res):
    return [r.name for r in res]


def first_fail(res):
    return next(r for r in res if not r.ok)


def test_healthy_rig_passes_and_ends_in_m0_with_fresh_lines():
    rig = make()
    ok, res = rig.run()
    assert ok, [r.line() for r in res]
    assert ("strength", pf.MOTOR_STRENGTH) in rig.calls
    assert rig.calls[0] == ("mode", 4) and ("mode", 0) in rig.calls
    assert rig.calls.index(("mode", 0)) > max(i for i, c in enumerate(rig.calls) if c[0] == "pulse"), "motors before the sweep"
    assert rig.sweeps == 1 and rig.recoveries == 0 and rig.rotated == 1
    n_cal = len(pf.CAL_STRENGTHS) * pf.CAL_REPEATS
    for c in rig.cams:
        plus = [x for x in rig.calls if x[:3] == ("pulse", c, "+")]
        minus = [x for x in rig.calls if x[:3] == ("pulse", c, "-")]
        assert len(plus) == len(minus) == math.ceil(pf.MOTOR_TRAVEL_COUNTS / rig.cpp) + n_cal
        assert abs(rig.enc[c] - rig.start_enc[c]) < 1.0, "lens back where it started"
        assert rig.runner.motor_cpp[c] == rig.cpp_by_strength, rig.runner.motor_cpp[c]
    assert rig.runner.motor_polarity == rig.polarity, "AF polarity note = sign the encoder moved per '+'"
    assert [c[1] for c in rig.calls if c[0] == "strength"] == [3, 3, 2, 1, 0] * len(rig.cams), "S3 travel, then S3..S0 cal"
    assert {"Focus motor cam1", "Focus calibration cam1", "Focus calibration cam3", "Focus motor cam2", "Focus motor cam3", "Sweep images", "GPS tagging",
            "ClearCore motors", "Cameras alive (M4)"} <= set(names(res))
    assert rig.mode == 0


def test_obstructed_motor_fails_that_camera_and_never_sweeps():
    rig = make(stall={2: 3000})
    ok, res = rig.run()
    assert not ok
    f = first_fail(res)
    assert f.name == "Focus motor cam2" and "did not move" in f.detail, f.line()
    assert rig.sweeps == 0 and "Focus motor cam1" in names(res) and "Focus motor cam3" not in names(res)
    assert rig.runner.motor_polarity == {1: rig.polarity[1]}, "only the motor that passed is noted"
    assert list(rig.runner.motor_cpp) == [1]


def test_motor_dead_at_fine_strength_fails_calibration():
    rig = make()
    rig.cpp_by_strength[0] = 0.0   # S0 buzzes but never turns the shaft (the AF strength)
    ok, res = rig.run()
    assert not ok
    f = first_fail(res)
    assert f.name == "Focus calibration cam1" and "S0" in f.detail and "does not move" in f.detail, f.line()
    assert rig.sweeps == 0 and 1 in rig.runner.motor_polarity and 1 not in rig.runner.motor_cpp


def test_dead_camera_in_m4_is_recovered_before_the_sweep_then_passes():
    rig = make(dead=(3,))
    ok, res = rig.run()
    assert ok, [r.line() for r in res]
    assert rig.recoveries == 1 and rig.sweeps == 1
    assert rig.calls.index(("recover",)) < rig.calls.index(("click",)), "recover BEFORE wasting a sweep"
    alive = next(r for r in res if r.name == "Cameras alive (M4)")
    assert alive.ok and "WARN" in alive.detail and "cam(s) [3]" in alive.detail


def test_camera_dead_after_recovery_fails_with_single_recovery():
    rig = make(dead=(1,), dead_after_recovery=(1,))
    ok, res = rig.run()
    assert not ok
    f = first_fail(res)
    assert f.name == "Sweep images" and "even after deep camera recovery" in f.detail, f.line()
    assert rig.recoveries == 1 and rig.sweeps == 1


def test_camera_dying_only_on_the_sweep_gets_one_retry():
    rig = make()
    # alive in M4, nothing on the first $Click*: flip cam2 dead right as the sweep is fired
    real_click = rig.start_sweep

    def click_then_kill():
        if rig.sweeps == 0:
            rig.alive[2] = False
        real_click()
    rig.runner._h.start_sweep = click_then_kill
    ok, res = rig.run()
    assert ok, [r.line() for r in res]
    assert rig.recoveries == 1 and rig.sweeps == 2
    assert any(r.name == "Sweep images (1st try)" and "WARN" in r.detail for r in res)


def test_clearcore_fault_line_aborts_with_the_line():
    rig = make(fault_on_mode=4)
    ok, res = rig.run()
    assert not ok
    f = first_fail(res)
    assert f.name == "ClearCore motors" and "Motor alert detected" in f.detail, f.line()
    assert rig.sweeps == 0 and not any(c[0] == "pulse" for c in rig.calls)


def test_no_ack_fails_fast():
    rig = make(ack=False)
    ok, res = rig.run()
    assert not ok and first_fail(res).name == "ClearCore link"
    assert rig.t < pf.ACK_TIMEOUT_S + 1.0, "a dead UART must not wait out the homing timeout"


def test_gps_disconnected_is_reported_up_front_and_the_rest_still_runs():
    rig = make(gps=False)
    ok, res = rig.run()
    assert not ok and first_fail(res).name == "GPS tagging" and "not connected" in first_fail(res).detail
    assert names(res).index("GPS tagging") < names(res).index("Mirror homing (M4)"), "said before the motors run"
    assert any(r.name == "Sweep images" and r.ok for r in res) and rig.sweeps == 1
    assert sum(r.name == "GPS tagging" for r in res) == 1


def test_missing_prerequisites_fail_before_touching_hardware():
    rig = make(hooks={"recording_prerequisites_missing": lambda: ["project name"]})
    ok, res = rig.run()
    assert not ok and first_fail(res).name == "Prerequisites" and "project name" in first_fail(res).detail
    assert rig.calls == []


def test_abort_ends_the_run_with_a_fail():
    rig = make()
    rig.later(20.0, lambda: rig.runner.abort())
    ok, res = rig.run()
    assert not ok and first_fail(res).name == "Preflight" and "aborted" in first_fail(res).detail
    assert not rig.runner.is_running()


def main() -> int:
    for t in (
        test_healthy_rig_passes_and_ends_in_m0_with_fresh_lines,
        test_obstructed_motor_fails_that_camera_and_never_sweeps,
        test_motor_dead_at_fine_strength_fails_calibration,
        test_dead_camera_in_m4_is_recovered_before_the_sweep_then_passes,
        test_camera_dead_after_recovery_fails_with_single_recovery,
        test_camera_dying_only_on_the_sweep_gets_one_retry,
        test_clearcore_fault_line_aborts_with_the_line,
        test_no_ack_fails_fast,
        test_gps_disconnected_is_reported_up_front_and_the_rest_still_runs,
        test_missing_prerequisites_fail_before_touching_hardware,
        test_abort_ends_the_run_with_a_fail,
    ):
        t()
        print(f"  ok: {t.__name__}")
    print("preflight tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
