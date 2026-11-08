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

import inspect
import math
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "host"))

from PySide6.QtCore import QCoreApplication  # noqa: E402

from capture import preflight as pf  # noqa: E402
from capture.preflight import PreflightHooks, PreflightRunner  # noqa: E402

_app = QCoreApplication.instance() or QCoreApplication([])


class FakeRig:
    FRAMES_PER_SWEEP = 40

    def __init__(self, cams=(1, 2, 3), *, dead=(), dead_after_recovery=(), stops=None,
                 gps=True, ack=True, fault_on_mode=None, cpp=850.0):
        self.t = 0.0
        self.cams = list(cams)
        self.alive = {c: c not in dead for c in cams}
        self.dead_after_recovery = set(dead_after_recovery)
        self.stops = stops or {}            # cam -> (counts of travel available '-' from start, '+' from start)
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
        self.logged = []            # what the runner wrote to the log
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
        step = self.cpp_by_strength[self.strength]
        # Work in travel along the '+' direction so an end stop is a fixed position, not a
        # distance from start: backing off has to actually buy room back.
        u = (self.enc[cam] - self.start_enc[cam]) * self.polarity[cam]
        u += (1.0 if sym == "+" else -1.0) * step * n
        room = self.stops.get(cam)
        if room is not None:
            u = max(-room[0], min(room[1], u))   # (counts available '-' from start, '+' from start)
        self.later(0.3, lambda v=u: self.enc.__setitem__(
            cam, self.start_enc[cam] + v * self.polarity[cam]))

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
            rotate_lines=self.rotate_lines, camera_serial=lambda c: f"243900{9 - c}",
            log=self.logged.append,
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


def worst_burst(rig):
    """Largest |counts| any single burst asked the encoder to track, replaying the call log at
    the strength in force at the time -- what MOTOR_MAX_MOVE_COUNTS exists to bound."""
    s, worst = None, 0.0
    for c in rig.calls:
        if c[0] == "strength":
            s = c[1]
        elif c[0] == "pulse" and s is not None:
            worst = max(worst, abs(rig.cpp_by_strength[s] * c[3]))
    return worst


def test_healthy_rig_passes_and_ends_in_m0_with_fresh_lines():
    rig = make()
    ok, res = rig.run()
    assert ok, [r.line() for r in res]
    assert ("strength", pf.MOTOR_STRENGTH) in rig.calls
    assert rig.calls[0] == ("mode", 4) and ("mode", 0) in rig.calls
    assert rig.calls.index(("mode", 0)) > max(i for i, c in enumerate(rig.calls) if c[0] == "pulse"), "motors before the sweep"
    assert rig.sweeps == 1 and rig.recoveries == 0 and rig.rotated == 1
    # Per strength and direction: half the probe bursts, then CAL_REPEATS measured ones.
    n_cal = len(pf.CAL_STRENGTHS) * (pf.CAL_PROBE_BURSTS // 2 + pf.CAL_REPEATS)
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
    """Wedged: no room either way, so the back-off cannot buy any and it really is a fault."""
    rig = make(stops={2: (0, 0)})
    ok, res = rig.run()
    assert not ok
    f = first_fail(res)
    assert f.name == "Focus motor cam2" and "did not move" in f.detail, f.line()
    assert "will not move on '-' either" in f.detail, f.line()
    assert rig.sweeps == 0 and "Focus motor cam1" in names(res) and "Focus motor cam3" not in names(res)
    assert rig.runner.motor_polarity == {1: rig.polarity[1]}, "only the motor that passed is noted"
    assert list(rig.runner.motor_cpp) == [1]


def test_a_lens_parked_at_the_end_stop_is_backed_off_and_passes():
    """cam2 on 2026-11-05: ~5600 c of '+' room left and plenty behind it. Running out of travel
    is where the operator parked the lens, not a fault -- back off and redo the leg."""
    rig = make(stops={2: (20000, 5600)})
    ok, res = rig.run()
    assert ok, [r.line() for r in res]
    detail = next(r for r in res if r.name == "Focus motor cam2").detail
    assert "backed off" in detail, detail
    assert rig.sweeps == 1, "the sweep still runs once the motor checks out"
    assert 2 in rig.runner.motor_cpp and rig.runner.motor_polarity[2] == rig.polarity[2]
    # The retry has to have been driven from further back than the stop it hit.
    assert any(c[:3] == ("pulse", 2, "-") for c in rig.calls)


def test_travel_shorter_than_the_check_needs_fails_with_the_span():
    """Room in both directions, but 9600 c of it in total -- the check drives 10000."""
    rig = make(stops={2: (4000, 5600)})
    ok, res = rig.run()
    assert not ok
    f = first_fail(res)
    # 5600 c of '+' room plus 4000 c behind it: the back-off measures the span as it goes.
    assert f.name == "Focus motor cam2", f.line()
    assert "usable travel is only ~9600 c" in f.detail, f.line()
    assert rig.sweeps == 0


def test_motor_dead_at_fine_strength_fails_calibration():
    rig = make()
    rig.cpp_by_strength[0] = 0.0   # S0 buzzes but never turns the shaft (the AF strength)
    ok, res = rig.run()
    assert not ok
    f = first_fail(res)
    assert f.name == "Focus calibration cam1" and "S0" in f.detail and "does not move" in f.detail, f.line()
    assert rig.sweeps == 0 and 1 in rig.runner.motor_polarity and 1 not in rig.runner.motor_cpp


def test_bursts_stay_under_the_unwrap_cap_on_a_motor_no_table_predicted():
    """2026-11-05 cam1: S1 ran ~330 c/pulse, peaking at 371, where the retired nominal table
    said 80. That sized a 5-pulse burst, which read 1854 c and failed a healthy motor on the
    unwrap guard. Sizing each burst from a probe of THIS motor keeps them all inside it."""
    rig = make()
    rig.cpp_by_strength = {3: 876.0, 2: 526.0, 1: 371.0, 0: 45.0}
    ok, res = rig.run()
    assert ok, [r.line() for r in res]
    assert worst_burst(rig) <= pf.MOTOR_MAX_MOVE_COUNTS, worst_burst(rig)
    for c in rig.cams:
        assert rig.runner.motor_cpp[c] == rig.cpp_by_strength, rig.runner.motor_cpp[c]


def test_gps_counters_are_read_by_camera_number_not_serial():
    """GpsCorrelator counts per camera SERIAL. Read under an app camera number every counter
    reads zero, so the check failed all three cameras on 2026-11-05 while the files on disk had
    13/13 frames tagged. int("2439008") is a valid int, so nothing raised to give it away."""
    raw = {"cameras": {"2439008": {"matched": 13}, "2439007": {"matched": 13}}}
    serial_by_cam = {1: "2439008", 2: "2439007", 3: "2439006"}
    assert pf._gps_per_cam(raw).get(1) is None, "the bug: camera 1's counters are filed under 2439008"
    keyed = pf._gps_per_cam(pf.gps_stats_by_camera(raw, serial_by_cam))
    assert keyed == {1: {"matched": 13}, 2: {"matched": 13}}, keyed


def test_a_motor_finer_than_the_encoder_noise_is_still_calibrated():
    """One pulse at 8 c reads as 'did not move', so the probe has to grow the burst until the
    encoder can see it -- with no table left to tell it how far to grow."""
    rig = make()
    rig.cpp_by_strength[0] = 8.0
    ok, res = rig.run()
    assert ok, [r.line() for r in res]
    assert worst_burst(rig) <= pf.MOTOR_MAX_MOVE_COUNTS, worst_burst(rig)
    for c in rig.cams:
        assert rig.runner.motor_cpp[c][0] == 8.0, rig.runner.motor_cpp[c]


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


# ── the operator's report ───────────────────────────────────────────────────

def test_a_clean_run_says_one_line_and_keeps_the_numbers_in_the_log():
    rig = make()
    ok, res = rig.run()
    assert ok
    assert pf.format_report(res) == "Preflight checks pass"
    assert any("c/pulse" in m for m in rig.logged), "the calibration figures still go somewhere"


def test_a_wedged_motor_sends_the_operator_to_that_lens_by_serial():
    """cam2 is an app index nobody can read off the rig; 2439007 is printed on the camera."""
    rig = make(stops={2: (0, 0)})
    ok, res = rig.run()
    report = pf.format_report(res)
    assert not ok
    assert report.splitlines()[0] == "Focus motor failed on cam2 (2439007) -- the lens will not turn"
    assert "  \u2022 Check the focus ring on cam2 (2439007) is not pulled forward" in report
    assert "pulse #" not in report, "encoder counts belong in the log, not in front of the operator"
    assert any("did not move on" in m for m in rig.logged), "...and they are in the log"


def test_short_travel_asks_for_a_re_park_rather_than_a_cable_check():
    """Both end stops found: the lens works and is parked badly, which is nearly the opposite
    of the advice every other focus failure gives."""
    rig = make(stops={2: (4000, 5600)})
    ok, res = rig.run()
    report = pf.format_report(res)
    assert not ok and "not enough travel to test" in report
    assert "Set the lens to roughly mid-focus by hand" in report
    assert "wires" not in report and "fouling" not in report


def test_no_gps_gives_the_three_things_to_check():
    rig = make(gps=False)
    ok, res = rig.run()
    report = pf.format_report(res)
    assert not ok and report.startswith("GPS failed -- no data from the receiver")
    for bullet in ("Check the main harness cable, at the control box and at the PCB",
                   "Check the orange indicator light on the control box",
                   "Check the antenna connection"):
        assert f"  \u2022 {bullet}" in report, report


def test_every_pcb_fault_string_earns_its_own_advice():
    """FAULT_RE and the advice groups come off the same table, so a line that trips the
    detector cannot fall through to the generic block -- and homing/cancelled are checked
    before the generic alert that usually accompanies them."""
    for key, pattern in pf._FAULT_ADVICE:
        for alt in pattern.split("|"):
            assert pf.FAULT_RE.search(alt), alt
            assert pf._fault_advice(alt) == key, (alt, pf._fault_advice(alt))
    assert pf._fault_advice("Motor0: homing FAILED, alert detected") == "mirror-homing"
    assert pf.ADVICE["mirror-homing"][1][0].startswith("Check for obstructions")
    assert pf.ADVICE["mirror-fault"][1][0].startswith("Check the system voltage")


def test_every_advice_key_the_runner_names_exists_and_every_one_is_used():
    """The raise sites name their advice by string literal. A typo would silently drop the
    operator back to the raw technical message -- the thing this report exists to remove."""
    used = set(re.findall(r'advice=["\'](.*?)["\']', inspect.getsource(pf)))
    used |= {k for k, _p in pf._FAULT_ADVICE}
    assert len(used) > 10, "the call sites stopped matching the regex"
    assert used == set(pf.ADVICE), sorted(used ^ set(pf.ADVICE))


def test_a_failure_with_no_advice_still_says_something():
    r = pf.CheckResult("Some new check", False, "the thing went wrong")
    assert pf.advice_block(r) == "FAIL  Some new check: the thing went wrong"


def main() -> int:
    for t in (
        test_healthy_rig_passes_and_ends_in_m0_with_fresh_lines,
        test_obstructed_motor_fails_that_camera_and_never_sweeps,
        test_a_lens_parked_at_the_end_stop_is_backed_off_and_passes,
        test_travel_shorter_than_the_check_needs_fails_with_the_span,
        test_motor_dead_at_fine_strength_fails_calibration,
        test_bursts_stay_under_the_unwrap_cap_on_a_motor_no_table_predicted,
        test_a_motor_finer_than_the_encoder_noise_is_still_calibrated,
        test_gps_counters_are_read_by_camera_number_not_serial,
        test_dead_camera_in_m4_is_recovered_before_the_sweep_then_passes,
        test_camera_dead_after_recovery_fails_with_single_recovery,
        test_camera_dying_only_on_the_sweep_gets_one_retry,
        test_clearcore_fault_line_aborts_with_the_line,
        test_no_ack_fails_fast,
        test_gps_disconnected_is_reported_up_front_and_the_rest_still_runs,
        test_missing_prerequisites_fail_before_touching_hardware,
        test_abort_ends_the_run_with_a_fail,
        test_a_clean_run_says_one_line_and_keeps_the_numbers_in_the_log,
        test_a_wedged_motor_sends_the_operator_to_that_lens_by_serial,
        test_short_travel_asks_for_a_re_park_rather_than_a_cable_check,
        test_no_gps_gives_the_three_things_to_check,
        test_every_pcb_fault_string_earns_its_own_advice,
        test_every_advice_key_the_runner_names_exists_and_every_one_is_used,
        test_a_failure_with_no_advice_still_says_something,
    ):
        t()
        print(f"  ok: {t.__name__}")
    print("preflight tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
