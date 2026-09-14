"""Self-check for the camera-to-screen tilt measurement (``host/capture/align.py``).

Run: ``py -3 tools/test_align.py``

Everything here goes through a rendered image, not a list of projected points: the
board is drawn at panel resolution, warped by the homography a known pose implies,
blurred and noised, and handed to ``measure()`` exactly as the coarse tap would.
That keeps ``cv2.aruco`` corner detection inside the test, which is where the real
uncertainty lives -- projecting ideal points would only be testing ``solvePnP``.

The load-bearing assertion is ROLL INVARIANCE. The camera on this rig is rolled
~90 deg relative to the panel, so a reading expressed in image axes would arrive
with its two numbers swapped and one sign flipped, and an operator would chase the
tilt in the wrong direction. That is the same class of bug as the field map
trimming the clean end of the frame for two weeks (HANDOFF 36.13), and it is the
reason ``screen_axis_tilts`` reads the columns of R rather than decomposing into
image-relative Euler angles.
"""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "host"))

import cv2  # noqa: E402
import orthosim_codec as codec  # noqa: E402
from capture import align  # noqa: E402

# The coarse tap: 13400x9528 sensor decimated by 4.
COARSE_SHAPE = (2382, 3350)
SERIAL = "2439008"          # the 300 mm lens actually on the bench rig
STANDOFF_MM = 6900.0        # ~7 m, the measured bench standoff


def _rot_x(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=np.float64)


def _rot_y(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=np.float64)


def _rot_z(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=np.float64)


def _pose(tilt_v_deg: float, tilt_h_deg: float, roll_deg: float = 0.0):
    """Build a board->camera pose with the given screen-axis tilts and camera roll.

    Roll is applied on the LEFT (a rotation of the camera about its own optical
    axis), which leaves row 2 of R untouched -- so the ground truth the test
    compares against is the same matrix for every roll, by construction.
    """
    R = _rot_z(math.radians(roll_deg)) @ (
        _rot_x(math.radians(tilt_v_deg)) @ _rot_y(math.radians(-tilt_h_deg))
    )
    b = align.board()
    nx, ny = b.getChessboardSize()
    sq = b.getSquareLength()
    # Centre the board on the optical axis so tilt is the only thing in the image.
    t = -R @ np.array([nx * sq / 2.0, ny * sq / 2.0, 0.0]) + \
        np.array([0.0, 0.0, STANDOFF_MM])
    return R, t


def _render(R: np.ndarray, t: np.ndarray, K: np.ndarray,
            blur_px: float = 1.5, noise: float = 2.0,
            keep_frac: float | None = None) -> np.ndarray:
    """Draw the board, warp it through the pose, blur, noise, optionally vignette.

    ``keep_frac`` blacks out everything outside a centred box that size in each
    axis, standing in for a screen that no longer fills the frame.
    """
    b = align.board()
    panel = b.generateImage((3750, 2100))
    # Board plane is z=0, so board mm -> image px is a homography, and generated
    # image px -> board mm is a pure scale (which comes out as the panel pitch).
    mm_per_px = b.getChessboardSize()[0] * b.getSquareLength() / panel.shape[1]
    H = K @ np.column_stack([R[:, 0], R[:, 1], t]) @ np.diag(
        [mm_per_px, mm_per_px, 1.0])
    h, w = COARSE_SHAPE
    img = cv2.warpPerspective(panel, H, (w, h), flags=cv2.INTER_AREA,
                              borderValue=0)
    if blur_px > 0:
        img = cv2.GaussianBlur(img, (0, 0), blur_px)
    if noise > 0:
        rng = np.random.default_rng(12345)
        img = np.clip(img.astype(np.float32) +
                      rng.normal(0, noise, img.shape), 0, 255).astype(np.uint8)
    if keep_frac is not None:
        mask = np.zeros_like(img)
        dy, dx = int(h * keep_frac / 2), int(w * keep_frac / 2)
        mask[h // 2 - dy:h // 2 + dy, w // 2 - dx:w // 2 + dx] = 1
        img = img * mask
    return img


def _measure(tilt_v: float, tilt_h: float, roll: float = 0.0, **kw):
    K = align.coarse_intrinsics(SERIAL, COARSE_SHAPE)
    return align.measure(_render(*_pose(tilt_v, tilt_h, roll), K, **kw), K)


def test_a_known_tilt_comes_back_within_a_twentieth_of_a_degree():
    """The measured 1.57 deg pitch / 1.12 deg yaw, recovered off a rendered frame."""
    res = _measure(1.57, 1.12)
    assert res is not None, "board not detected at all"
    r = res.reading
    assert abs(r.tilt_v_deg - 1.57) < 0.05, r
    assert abs(r.tilt_h_deg - 1.12) < 0.05, r
    # The standoff is a by-product, but a wrong one would mean the object points
    # are in the wrong units, which would quietly rescale nothing and mislead 36.14.1.
    assert abs(r.distance_mm - STANDOFF_MM) < 0.02 * STANDOFF_MM, r.distance_mm
    assert r.n_corners == 312, r.n_corners


def test_rolling_the_camera_ninety_degrees_does_not_move_the_reading():
    """A reading in image axes would arrive swapped and sign-flipped. This is that guard."""
    flat = _measure(1.57, 1.12, roll=0.0)
    rolled = _measure(1.57, 1.12, roll=90.0)
    assert flat is not None and rolled is not None
    assert abs(rolled.reading.tilt_v_deg - flat.reading.tilt_v_deg) < 0.05, \
        (flat.reading, rolled.reading)
    assert abs(rolled.reading.tilt_h_deg - flat.reading.tilt_h_deg) < 0.05, \
        (flat.reading, rolled.reading)
    # And it is not merely stable because both readings are junk.
    assert abs(rolled.reading.tilt_v_deg - 1.57) < 0.05, rolled.reading


def test_a_null_reads_as_a_null():
    """Otherwise the operator nulls the bubble and the focus ramp survives it.

    Tolerance is 0.05 deg rather than zero because of THIS FILE, not the estimator:
    warping a 3750 px panel down to ~3125 px resamples the squares, and the residual
    tracks the interpolation and blur settings (it moves between 0.036 and 0.062 deg
    as they change) while the estimator fed ideal projected points is exact to
    floating point, as the second half of this test asserts. Real corner noise sits
    in the same place, and 0.05 deg is 7x inside the 0.36 deg budget either way.
    """
    res = _measure(0.0, 0.0)
    assert res is not None
    assert abs(res.reading.tilt_v_deg) < 0.05, res.reading
    assert abs(res.reading.tilt_h_deg) < 0.05, res.reading

    # Estimator alone, no pixels involved: a null must be an exact null, and a tilt
    # must come back as the pose that was built, or the two angles are mislabelled.
    b = align.board()
    obj = np.asarray(b.getChessboardCorners(), dtype=np.float64)
    K = align.coarse_intrinsics(SERIAL, COARSE_SHAPE)
    for want_v, want_h in ((0.0, 0.0), (1.57, 1.12)):
        R, t = _pose(want_v, want_h)
        pts, _ = cv2.projectPoints(obj, cv2.Rodrigues(R)[0], t, K, np.zeros(5))
        ok, rvec, _ = cv2.solvePnP(obj, pts, K, np.zeros(5),
                                   flags=cv2.SOLVEPNP_ITERATIVE)
        assert ok
        got_v, got_h = align.screen_axis_tilts(cv2.Rodrigues(rvec)[0])
        assert abs(got_v - want_v) < 1e-3 and abs(got_h - want_h) < 1e-3, \
            (want_v, want_h, got_v, got_h)


def test_partial_coverage_still_reads_but_a_sliver_refuses():
    """Precision tracks how much of the FRAME carries pattern, so it must degrade
    gracefully and then refuse -- a confident wrong angle is worse than no angle."""
    ok = _measure(1.57, 1.12, keep_frac=0.40)
    assert ok is not None, "40% coverage should still read"
    assert abs(ok.reading.tilt_v_deg - 1.57) < 0.36, ok.reading
    assert abs(ok.reading.tilt_h_deg - 1.12) < 0.36, ok.reading
    assert ok.reading.n_corners >= codec.ALIGN_MIN_CORNERS
    assert ok.span_v_frac < 0.5 and ok.span_h_frac < 0.5, ok

    assert _measure(1.57, 1.12, keep_frac=0.25) is None, \
        "a quarter of the frame is not enough evidence; it must refuse"


def test_an_empty_frame_is_refused_rather_than_guessed():
    K = align.coarse_intrinsics(SERIAL, COARSE_SHAPE)
    assert align.measure(np.zeros(COARSE_SHAPE, np.uint8), K) is None
    assert align.measure(np.zeros(COARSE_SHAPE, np.uint8), None) is None


def test_the_wire_survives_a_round_trip_and_rejects_a_step_datagram():
    r = codec.AlignReading(1.5678, -1.1234, 312, 6892.4)
    back = codec.decode_align(codec.encode_align(r))
    assert back is not None
    assert abs(back.tilt_v_deg - r.tilt_v_deg) < 1e-4, back
    assert abs(back.tilt_h_deg - r.tilt_h_deg) < 1e-4, back
    assert back.n_corners == 312 and abs(back.distance_mm - 6892.4) < 0.05
    assert abs(back.magnitude_deg - 1.9286) < 1e-3, back.magnitude_deg
    # The same socket carries the step interval; the two must not be confusable.
    assert codec.decode_align(b"0.041667") is None
    assert codec.decode_align(codec.ALIGN_PREFIX + b"1.0 2.0") is None
    assert codec.decode_align(codec.ALIGN_PREFIX + b"a b c d") is None


# ── the capture-app wiring ───────────────────────────────────────────────────
# Bound to a stub rather than a real MainWindow: these three methods touch only
# the handful of attributes below, and standing up Qt to check a throttle would
# cost more than the throttle.
class _FakeSock:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append(data)


class _Stub:
    def __init__(self, af_mode=True, af_running=False, field_map=False):
        from capture.app import MainWindow
        import types
        for name in ("_align_active", "_on_align_toggled", "_feed_align"):
            setattr(self, name, types.MethodType(getattr(MainWindow, name), self))
        self.sock = _FakeSock()
        self.logs = []
        self.status = []
        self.reset_calls = 0
        self._autofocus = type("A", (), {"is_running": lambda s: af_running})()
        self._modes = type("M", (), {"is_af_mode": lambda s: af_mode})()
        stub = self
        self._focus_panel = type("P", (), {
            "reset_align": lambda s: setattr(stub, "reset_calls",
                                             stub.reset_calls + 1),
            "set_autofocus_status": lambda s, t: stub.status.append(t),
        })()
        self._fm = field_map

    def _log(self, msg):
        self.logs.append(msg)

    def _field_map_running(self):
        return self._fm

    def _active_pipeline(self):
        return type("Pipe", (), {"camera_serial": SERIAL})()

    def _orthosim_sock(self):
        return self.sock


def test_the_toggle_refuses_rather_than_sitting_pressed_and_dead():
    """A button left down while nothing measures leaves the operator adjusting a
    screw against a bubble that will never move."""
    for kwargs in (dict(af_mode=False), dict(af_running=True), dict(field_map=True)):
        s = _Stub(**kwargs)
        s._on_align_toggled(True)
        assert not s._align_active(), kwargs
        assert s.reset_calls == 1, kwargs
        assert s.logs and s.logs[0].startswith("Align:"), kwargs

    s = _Stub()
    s._on_align_toggled(True)
    assert s._align_active() and s.reset_calls == 0
    s._on_align_toggled(False)
    assert not s._align_active()


def test_the_readout_is_throttled_and_the_datagram_is_the_measured_angle():
    from capture import app as app_mod

    K = align.coarse_intrinsics(SERIAL, COARSE_SHAPE)
    frame = _render(*_pose(1.57, 1.12), K)

    clock = [1000.0]
    real_time = app_mod.time
    app_mod.time = type("T", (), {"monotonic": staticmethod(lambda: clock[0])})()
    try:
        s = _Stub()
        s._on_align_toggled(True)
        s._feed_align(frame)
        assert len(s.sock.sent) == 1, "first frame should measure"
        # Same instant, and a third of the period later: neither may re-measure, or
        # a ~130 ms detection at ~5 fps eats the GUI thread.
        s._feed_align(frame)
        clock[0] += app_mod._ALIGN_PERIOD_S / 3.0
        s._feed_align(frame)
        assert len(s.sock.sent) == 1, s.sock.sent
        clock[0] += app_mod._ALIGN_PERIOD_S
        s._feed_align(frame)
        assert len(s.sock.sent) == 2, s.sock.sent
    finally:
        app_mod.time = real_time

    r = codec.decode_align(s.sock.sent[0])
    assert r is not None, s.sock.sent[0]
    assert abs(r.tilt_v_deg - 1.57) < 0.05 and abs(r.tilt_h_deg - 1.12) < 0.05, r
    assert r.n_corners == 312, r
    # Mirrored locally as well, so a dead display does not look like a dead measurement.
    assert s.status and "align:" in s.status[-1], s.status


def test_an_unreadable_frame_says_so_instead_of_going_quiet():
    s = _Stub()
    s._on_align_toggled(True)
    s._feed_align(np.zeros(COARSE_SHAPE, np.uint8))
    assert not s.sock.sent
    assert any("not enough pattern" in m for m in s.logs), s.logs
    assert s._align_active(), "a blank frame is a scene problem, not a reason to stop"


def test_the_display_listener_tells_the_two_message_types_apart():
    """Both ride one socket, so a step must never be read as an angle or vice versa --
    an angle parsed as a step would jump the scene, and a step parsed as an angle
    would park a bogus bubble on the screen the operator is trusting."""
    import socket
    from orthosim_gui import StepListener   # noqa: E402  (imports Qt, no window)

    port = 51799  # not codec.STEP_PORT: a live OrthoSim owns that one
    sl = StepListener(port=port)
    assert sl.error is None, sl.error
    sl.start()
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        tx.sendto(b"0.208333", (codec.STEP_HOST, port))
        tx.sendto(codec.encode_align(codec.AlignReading(1.57, -1.12, 312, 6892.4)),
                  (codec.STEP_HOST, port))
        tx.sendto(b"junk", (codec.STEP_HOST, port))
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and (sl.received < 1 or sl.align is None):
            time.sleep(0.01)
    finally:
        tx.close()
    assert sl.received == 1, f"steps={sl.received} (an angle leaked into the steps?)"
    assert [round(v, 6) for v in sl.drain()] == [0.208333]
    assert sl.align is not None, "the angle never arrived"
    assert abs(sl.align.tilt_v_deg - 1.57) < 1e-4, sl.align
    assert sl.align.n_corners == 312, sl.align
    assert sl.align_t > 0, "arrival must be stamped, or staleness cannot be shown"


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok  {name}")
        except Exception as exc:
            failed += 1
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
