"""Closed-loop autofocus tests against the software focus simulator (no hardware).

Drives :class:`capture.autofocus.AutoFocusEngine` with frames from
:class:`frame_grabber_sim.FocusSceneSimulator`, wiring the engine's focus-pulse
callback straight to the simulator so the whole focus loop closes in software.

Covers:
* leading-edge derivation from a motion vector (pure function),
* leading-edge tile selection in :class:`tile_geometry.TileGrid`,
* the patch tracker reporting motion,
* convergence for the flight (translate), nadir (oscillate) and oblique
  (circle-with-sky) motion profiles, in both assisted and full-auto modes,
* sky-phase pause/resume and backlash handling.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
HOST = REPO / "host"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HOST))

from capture.autofocus import (  # noqa: E402
    AutoFocusConfig,
    AutoFocusEngine,
    PatchTracker,
    focus_measure_full_res,
    leading_edge_from_motion,
    select_leading_edge_roi,
)
from frame_grabber_sim import FocusSceneSimulator  # noqa: E402
from tile_geometry import TileGrid  # noqa: E402


# ── helpers ─────────────────────────────────────────────────────────────────


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def test_pcb_pulse_noise_filter() -> None:
    """The M5 freeze guard: the per-pulse PCB ack chatter (``ack: +++`` / bare mode
    echo ``5``) must be filtered out of the GUI/session log, while control lines
    (mode banners, sweep markers, command acks, homing) must NOT be -- logging the
    pulse flood synchronously to the Google-Drive session file froze the UI."""
    from capture.app import _is_pcb_pulse_noise  # noqa: PLC0415

    # Dropped: routine motor-pulse chatter.
    for noise in ("ack: +++;  A# 1289", "ack: ---;  A# 1292",
                  "ack: ----------;  A# 1293", "5", "  5  "):
        assert _is_pcb_pulse_noise(noise), f"should drop pulse noise: {noise!r}"
    # Kept: everything with diagnostic / control value.
    for keep in ("ack: $M5*;  A# 43", "Swp# 42;  Total sweep time 9358",
                 "Mode 5: Ground test (stepped sweep + background autofocus)",
                 "M0 homing success", "Camera 3 set", "ack: <<<;  A# 5"):
        assert not _is_pcb_pulse_noise(keep), f"should keep control line: {keep!r}"


def run_servo(
    sim: FocusSceneSimulator,
    *,
    config: AutoFocusConfig | None = None,
    lead_edge_hint: str | None = None,
    continuous: bool = True,
    frames: int = 1600,
) -> list[float]:
    """Run the dither servo against *sim* for up to *frames*; return the optical
    position after each rendered frame (so tests can check convergence + hold)."""
    cfg = config or AutoFocusConfig()
    # The simulator has no cadence/strength serial; keep those callbacks inert.
    engine = AutoFocusEngine(
        pulse=sim.apply_pulse,
        set_strength=None,
        adjust_cadence=None,
        status=lambda _m: None,
        finished=lambda _ok, _r: None,
        config=cfg,
    )
    engine.start(lead_edge_hint=lead_edge_hint, continuous=continuous)
    optical: list[float] = []
    for _ in range(frames):
        engine.feed_frame(sim.render())
        sim.step()
        optical.append(sim.optical_pulses)
        if not engine.running:
            break
    return optical


# ── pure-function tests ──────────────────────────────────────────────────────


def test_leading_edge_from_motion() -> None:
    # Content moving right -> new content enters from the left.
    assert leading_edge_from_motion(5.0, 0.2) == "left"
    assert leading_edge_from_motion(-5.0, 0.2) == "right"
    assert leading_edge_from_motion(0.1, 4.0) == "top"
    assert leading_edge_from_motion(0.1, -4.0) == "bottom"
    assert leading_edge_from_motion(0.0, 0.0) is None


def test_tilegrid_edge_region() -> None:
    fw, fh = 1600, 1200
    grid = TileGrid(
        fw, fh, display_width=fw, display_height=fh, zoom=5.0,
        region="edge", lead_edge="top", edge_band=0.25,
    )
    assert len(grid) > 0
    band = int(round(fh * 0.25))
    # Every tile sits inside the top band.
    for t in grid:
        assert t.y0 >= 0
        assert t.y1 <= max(band, grid.tile_h) + 1

    right = TileGrid(
        fw, fh, display_width=fw, display_height=fh, zoom=5.0,
        region="edge", lead_edge="right", edge_band=0.25,
    )
    assert len(right) > 0
    for t in right:
        assert t.x1 <= fw
        assert t.x0 >= fw - max(int(round(fw * 0.25)), right.tile_w) - 1


def test_select_leading_edge_roi_runs() -> None:
    sim = FocusSceneSimulator(profile="translate")
    frame = sim.render()
    roi, score = select_leading_edge_roi(frame, "right", AutoFocusConfig())
    x0, y0, x1, y1 = roi
    assert 0 <= x0 < x1 <= sim.fw
    assert 0 <= y0 < y1 <= sim.fh
    assert score > 0.0


def test_patch_tracker_reports_motion() -> None:
    sim = FocusSceneSimulator(profile="translate", step_px=20, blur_per_pulse=0.0)
    tracker = PatchTracker()
    tracker.update(sim.render())
    sim.step()
    dx, dy, response = tracker.update(sim.render())
    assert abs(dx) > 1.0  # the steady horizontal pan must register
    assert response > 0.0


# ── closed-loop convergence tests ────────────────────────────────────────────


def test_servo_translate_converges_and_holds() -> None:
    """The real case: a continuously MOVING scene, lens defocused at the start.
    The dither servo must pull focus to the optimum and then HOLD it in the
    wobble band as the ground keeps flowing by (never a one-shot that finishes)."""
    sim = FocusSceneSimulator(
        profile="translate", start_focus_pulses=14.0, best_focus_pulses=0.0,
        backlash_pulses=2, step_px=12.0,
    )
    start_blur = sim.current_blur_sigma()
    cfg = AutoFocusConfig(dither_pulses=2, backlash_pulses=3, coarse_enable=False)
    optical = run_servo(sim, config=cfg, frames=1600)
    tail = optical[-80:]
    center_est = _median(tail)
    assert abs(center_est - sim.best_focus) <= cfg.dither_pulses + 2, (
        f"servo centre {center_est:.1f} not near focus {sim.best_focus} "
        f"(tail {tail[-12:]})"
    )
    assert sim.current_blur_sigma() < start_blur  # genuinely sharper than start
    # Holds within the wobble band -- doesn't wander off once locked.
    assert max(tail) - min(tail) <= 2 * cfg.dither_pulses + 6, (
        f"servo did not hold: tail span {min(tail)}..{max(tail)}"
    )


def test_moving_profile_converges_under_frequent_reacquire() -> None:
    """The M5 fix: on a fast-panning scene a patch leaves the FOV in a few frames,
    forcing frequent reacquires that RESTART the dither cycle. The DEFAULT long
    cycle (settle 8 x measure 3 x avg 2 = 66 frames/nudge) never completes before
    the patch is gone, so the lens only dithers in place and never focuses (the live
    'tracks well but never focuses' bug). The short ``apply_scene_profile(moving)``
    cycle lands a nudge per patch, so focus converges across the stream of patches --
    verified here while reacquires are actually firing."""
    sim = FocusSceneSimulator(
        profile="translate", step_px=16.0, period=90,
        start_focus_pulses=12.0, best_focus_pulses=0.0, backlash_pulses=2,
    )
    cfg = AutoFocusConfig(dither_pulses=2, backlash_pulses=3, coarse_enable=False)
    cfg.apply_scene_profile(moving=True)
    engine = AutoFocusEngine(
        pulse=sim.apply_pulse, set_strength=None, adjust_cadence=None,
        status=lambda _m: None, finished=lambda _o, _r: None, config=cfg,
    )
    engine.start(lead_edge_hint=None, continuous=True)
    optical: list[float] = []
    reacquires = 0
    prev_cx = None
    for _ in range(1600):
        engine.feed_frame(sim.render())
        sim.step()
        optical.append(sim.optical_pulses)
        roi = engine.current_roi
        if roi is not None:
            cx = (roi[0] + roi[2]) / 2.0
            if prev_cx is not None and abs(cx - prev_cx) > sim.fw * 0.3:
                reacquires += 1
            prev_cx = cx
        if not engine.running:
            break
    assert reacquires >= 3, (
        f"scene never forced reacquires ({reacquires}); the test would not exercise "
        f"the cycle-fits-in-dwell property"
    )
    tail = optical[-80:]
    center_est = _median(tail)
    assert abs(center_est - sim.best_focus) <= cfg.dither_pulses + 3, (
        f"moving-profile servo centre {center_est:.1f} not near focus "
        f"{sim.best_focus} despite {reacquires} reacquires (tail {tail[-12:]})"
    )


def test_servo_static_converges_and_finishes() -> None:
    """Bench case: static scene, Lead=Static (full-frame target), one-shot run.
    The servo must lock and finish, resting the lens near true focus."""
    sim = FocusSceneSimulator(
        profile="translate", step_px=0.0, start_focus_pulses=12.0,
        best_focus_pulses=0.0, backlash_pulses=2,
    )
    cfg = AutoFocusConfig(full_frame_target=True, dither_pulses=2, backlash_pulses=3,
                          coarse_enable=False)
    optical = run_servo(sim, config=cfg, continuous=False, frames=4000)
    assert len(optical) < 4000, "one-shot servo never locked/finished"
    assert abs(optical[-1] - sim.best_focus) <= cfg.dither_pulses + 2, (
        f"one-shot servo rested at {optical[-1]}, not near {sim.best_focus} "
        f"({optical[-8:]})"
    )


def test_reacquires_when_patch_pans_off_edge() -> None:
    """A tracked patch that pans to the trailing edge must be dropped for a fresh
    leading-edge tile -- NOT clamped against the border where the object exited (the
    M5 'tile sticks where the object left the FOV' symptom). Translate pans the window
    right, so content drifts left and a reacquire snaps the ROI back toward the right
    (leading) edge -- a big positive jump in the ROI centre x."""
    sim = FocusSceneSimulator(
        profile="translate", step_px=16.0, period=90,
        start_focus_pulses=0.0, best_focus_pulses=0.0,
    )
    cfg = AutoFocusConfig(dither_pulses=2, coarse_enable=False)
    engine = AutoFocusEngine(
        pulse=sim.apply_pulse, set_strength=None, adjust_cadence=None,
        status=lambda _m: None, finished=lambda _o, _r: None, config=cfg,
    )
    engine.start(lead_edge_hint=None, continuous=True)
    reacquired = False
    prev_cx = None
    for _ in range(400):
        engine.feed_frame(sim.render())
        sim.step()
        roi = engine.current_roi
        if roi is not None:
            cx = (roi[0] + roi[2]) / 2.0
            if prev_cx is not None and cx - prev_cx > sim.fw * 0.3:
                reacquired = True  # ROI snapped back to the leading edge
            prev_cx = cx
        if not engine.running:
            break
    assert reacquired, "ROI never reacquired; it stuck at the trailing edge"


def test_seed_roi_is_used_instead_of_repicking() -> None:
    """AF must focus the tile Fine handed it (locked while the operator had the scene
    sharp), NOT re-pick on the possibly-blurry acquisition frame -- the 'Fine picks the
    building, AF picks bushes' report. A supplied seed_roi is honoured verbatim."""
    cfg = AutoFocusConfig(dither_pulses=2, coarse_enable=False)
    engine = AutoFocusEngine(pulse=lambda *_a: None, status=lambda _m: None, config=cfg)
    seed = (100, 80, 240, 200)
    engine.start(continuous=True, seed_roi=seed)
    frame = np.random.default_rng(0).integers(0, 16384, (300, 400), dtype=np.uint16)
    engine.feed_frame(frame)
    assert engine.current_roi == seed, f"seed ignored: roi={engine.current_roi}"


def test_servo_holds_when_already_focused() -> None:
    """Start in focus on a moving scene: the servo must dither in place and NOT
    walk the lens off the peak (deadband keeps it put)."""
    sim = FocusSceneSimulator(
        profile="translate", start_focus_pulses=0.0, best_focus_pulses=0.0,
        backlash_pulses=2, step_px=10.0,
    )
    cfg = AutoFocusConfig(dither_pulses=2, backlash_pulses=3, coarse_enable=False)
    optical = run_servo(sim, config=cfg, frames=900)
    tail = optical[-100:]
    assert max(abs(o - sim.best_focus) for o in tail) <= cfg.dither_pulses + 3, (
        f"servo wandered from focus: tail span {min(tail)}..{max(tail)}"
    )


def test_servo_normalised_gradient_ignores_content_scale() -> None:
    """The servo must key off the +d/-d CONTRAST ratio, not the absolute score,
    so a patch that gets busier/dimmer over time (changing scenery, exposure
    hunting) doesn't masquerade as a focus change. Driven through the scorer hook
    as focus_peak(pos) x content(t), content swinging ~5x over the run."""
    peak = 10
    st = {"pos": 0, "t": 0}

    def pulse(sym: str, count: int) -> None:
        st["pos"] += count if sym == "+" else -count

    def score(_roi) -> float:
        st["t"] += 1
        d = st["pos"] - peak
        focus = 5.0 + 100.0 * math.exp(-(d * d) / (2 * 6.0 ** 2))
        content = 1.0 + 2.0 * (1 + math.sin(st["t"] * 0.05))  # ~1..5, slow drift
        return focus * content

    cfg = AutoFocusConfig(dither_pulses=2, backlash_pulses=0, servo_lock_cycles=3,
                          coarse_enable=False)
    engine = AutoFocusEngine(
        pulse=pulse, status=lambda _m: None,
        config=cfg, full_res_score=score,
    )
    # Continuous (flight): the property is that a 5x content swing never PULLS the
    # centre off focus -- it holds in the dither band. (Locking/finishing isn't the
    # point here and an adversarial content period ~= one cycle can defeat the 3-in-a
    # -row latch without ever moving the lens off the peak.)
    engine.start(continuous=True)
    frame = np.random.default_rng(0).integers(0, 16384, (240, 320), dtype=np.uint16)
    centers: list[int] = []
    for _ in range(6000):
        engine.feed_frame(frame)
        centers.append(engine._rs.center)
        if not engine.running:
            break
    tail = centers[-1500:]
    assert max(abs(c - peak) for c in tail) <= cfg.dither_pulses + 1, (
        f"content swing pulled the servo off focus: tail centres "
        f"{min(tail)}..{max(tail)} want ~{peak}"
    )


def test_servo_does_not_floor_lock_far_from_peak() -> None:
    """The rig failure this design fixes: start deep on the defocused FLOOR with a
    distant peak. A gradient-only servo reads a flat floor and 'locks' while blurred
    (the 8.5-floor lock seen live). The 3-point peak test must refuse to lock on the
    flat floor -- so the run climbs to the real (concave) peak before finishing."""
    peak = 40  # far from the start at 0 -> must travel through the flat floor
    st = {"pos": 0, "t": 0}
    rng = np.random.default_rng(3)

    def pulse(sym: str, count: int) -> None:
        st["pos"] += count if sym == "+" else -count

    def score(_roi) -> float:
        st["t"] += 1
        d = st["pos"] - peak
        focus = 8.5 + 250.0 * math.exp(-(d * d) / (2 * 8.0 ** 2))
        return float(focus * (1.0 + 0.05 * rng.standard_normal()))  # 5% content noise

    cfg = AutoFocusConfig(dither_pulses=2, backlash_pulses=0, servo_lock_cycles=3,
                          coarse_enable=False)
    out: dict = {}
    engine = AutoFocusEngine(
        pulse=pulse, status=lambda _m: None,
        finished=lambda ok, r: out.update({"ok": ok, **r}),
        config=cfg, full_res_score=score,
    )
    engine.start(continuous=False)
    frame = np.random.default_rng(0).integers(0, 16384, (240, 320), dtype=np.uint16)
    for _ in range(12000):
        engine.feed_frame(frame)
        if not engine.running:
            break
    assert out.get("ok"), f"servo never reached the distant peak: {out}"
    # The whole point: it did NOT declare 'in focus' back on the floor.
    assert abs(out["center"] - peak) <= cfg.dither_pulses + 3, (
        f"servo floor-locked / missed the peak: centre={out.get('center')} "
        f"want ~{peak}"
    )


def test_focus_measure_full_res_sharp_beats_blur() -> None:
    """The full-res Bayer focus metric must (a) rank sharp > defocused and
    (b) not be dominated by the raw RGGB mosaic (its whole reason to demosaic)."""
    import cv2

    rng = np.random.default_rng(7)
    # Full-res raw-Bayer-ish tile: fine random texture with high frequencies.
    sharp = rng.integers(0, 16384, (128, 128), dtype=np.uint16)
    # Defocus = optical low-pass. Score must drop.
    blur_u8 = cv2.GaussianBlur((sharp >> 6).astype(np.uint8), (0, 0), 3.0)
    blurred = (blur_u8.astype(np.uint16) << 6)

    s_sharp = focus_measure_full_res(sharp)
    s_blur = focus_measure_full_res(blurred)
    assert s_sharp > s_blur * 1.5, f"sharp {s_sharp:.1f} !>> blurred {s_blur:.1f}"

    # A flat field carrying ONLY a Bayer mosaic offset (R/G/B levels differ, no
    # scene detail) must not read as sharp -- if the Laplacian saw the mosaic it
    # would. Score should be tiny vs the textured sharp tile.
    flat = np.full((128, 128), 4000, dtype=np.uint16)
    flat[0::2, 1::2] = 8000  # G on RGGB
    flat[1::2, 0::2] = 8000  # G
    s_mosaic = focus_measure_full_res(flat)
    assert s_mosaic < s_sharp * 0.25, f"mosaic {s_mosaic:.1f} not suppressed"


def test_saturation_aware_prefers_unclipped_detail() -> None:
    """A near-clipped high-contrast edge (overexposed mast/window) scores huge on
    raw band-pass contrast but is a boring, poor focus target. The saturation-aware
    picker must instead choose a busy UNCLIPPED patch. Two off-centre blocks: a
    clipped bright strip vs mid-grey fine texture."""
    h, w = 480, 640
    rng = np.random.default_rng(1)
    frame = np.full((h, w), 2000, dtype=np.uint16)  # smooth grey background
    # Left block: a clipped bright vertical strip (huge dark->white edge).
    frame[60:420, 120:170] = 16383
    # Right block: busy mid-grey texture, nowhere near clipping.
    frame[60:420, 430:600] = rng.integers(3000, 9000, (360, 170), dtype=np.uint16)

    cfg = AutoFocusConfig(full_frame_target=True)
    roi, _score = select_leading_edge_roi(frame, None, cfg)
    rx = (roi[0] + roi[2]) / 2
    assert rx > w / 2, (
        f"picker chose the clipped strip (x-centre {rx:.0f}) over the busy "
        f"unclipped detail on the right"
    )


def test_partly_clipped_busy_beats_featureless_unclipped() -> None:
    """The real rig regression: at higher exposure a genuinely busy block is PARTLY
    blown (~30% clipped) but still rich with edges in the other 70%, while a smooth
    tile is fully unclipped. Judging each tile on its UNCLIPPED-pixel texture, the
    busy-but-partly-clipped block must win -- a fraction-based fade got this backwards
    and picked the featureless tile (the 'shitty tile' symptom)."""
    h, w = 480, 640
    rng = np.random.default_rng(2)
    frame = np.full((h, w), 5000, dtype=np.uint16)  # smooth unclipped grey everywhere
    # Right block: busy texture, then blow ~30% of it to clipping (bright scene).
    busy = rng.integers(4000, 12000, (360, 170), dtype=np.uint16)
    busy[rng.random(busy.shape) < 0.3] = 16383  # scattered clipping through the detail
    frame[60:420, 430:600] = busy

    cfg = AutoFocusConfig(full_frame_target=True)
    roi, _score = select_leading_edge_roi(frame, None, cfg)
    rx = (roi[0] + roi[2]) / 2
    assert rx > w / 2, (
        f"picker chose a featureless unclipped tile (x-centre {rx:.0f}) over the "
        f"busy partly-clipped detail on the right"
    )


def test_texture_metric_prefers_structure_over_noisy_sky() -> None:
    """The M5 'picks empty sky' regression, at the metric level. Flat hazy sky carries
    full-amplitude sensor NOISE while the real content (buildings) is only slightly
    defocused. A local Laplacian gives per-pixel noise a huge (frequency-squared) gain
    and scores the sky ABOVE the content, so the picker jumps to empty sky; the coarse
    Gaussian high-pass must instead score the coherent (defocused) structure higher.
    Params are the ones that reproduced the live failure (Laplacian favours the sky)."""
    import cv2

    from capture.autofocus import _unclipped_texture  # noqa: PLC0415

    from tile_geometry import Tile  # noqa: PLC0415

    cfg = AutoFocusConfig()
    rng = np.random.default_rng(3)

    def _tile_score(block: np.ndarray) -> float:
        frame = np.full((440, 440), 5000, np.uint16)
        frame[20:400, 20:360] = block
        return _unclipped_texture(frame, Tile(20, 20, 360, 400), cfg, 2)[0]

    sky = np.clip(7000 + rng.integers(-600, 600, (380, 340)), 0, 16383).astype(np.uint16)
    bars = np.full((380, 340), 4000, np.float32)
    bars[:, ::20] = 13000
    bars = cv2.GaussianBlur(bars, (0, 0), 2.0).astype(np.uint16)  # defocused content

    sky_score, struct_score = _tile_score(sky), _tile_score(bars)
    assert struct_score > sky_score, (
        f"texture metric scored noisy sky ({sky_score:.1f}) at/above defocused "
        f"structure ({struct_score:.1f}) -- the noise-sensitive-metric regression"
    )


def test_no_motion_picks_offcentre_texture() -> None:
    """No motion (bench/static, or flight before the pan starts): the busy detail
    is off-centre, the centre is smooth. The picker must search the whole interior
    and land on the textured corner -- NOT lock onto the smooth centre circle (the
    live "why did it pick THIS boring tile?" symptom). This must hold regardless of
    ``full_frame_target`` (Static forces it; Auto falls back to it with no motion)."""
    h, w = 480, 640
    rng = np.random.default_rng(0)
    frame = np.full((h, w), 2000, dtype=np.uint16)  # smooth grey everywhere
    # Busy high-frequency block in the top-left corner (off the centre circle).
    frame[20:180, 20:220] = rng.integers(0, 16000, (160, 200), dtype=np.uint16)

    cx, cy = w / 2, h / 2
    for full_frame in (True, False):
        cfg = AutoFocusConfig(full_frame_target=full_frame)
        roi, _score = select_leading_edge_roi(frame, None, cfg)
        rx = (roi[0] + roi[2]) / 2
        ry = (roi[1] + roi[3]) / 2
        assert rx < cx and ry < cy, (
            f"no-motion pick (full_frame={full_frame}) did not reach the "
            f"off-centre texture: {roi}"
        )


def test_escalates_to_coarse_and_acquires() -> None:
    """The 'AF doesn't work on a blurry start' fix, via ESCALATION. On a flat
    far-field (gross defocus) the dither servo sees no slope even at its widest probe,
    so it must escalate to the coarse sweep-to-max to find the distant peak -- which a
    +-dither probe can never reach. (It does NOT sweep from a good start; see
    test_servo_holds_when_already_focused.) Uses a synthetic Gaussian peak with a
    genuinely flat floor -- the real-rig far-field the sim's smooth blur can't mimic."""
    peak = 130  # far beyond dither reach; only the coarse sweep can find it
    st = {"pos": 0}

    def pulse(sym: str, count: int) -> None:
        st["pos"] += count if sym == "+" else -count

    def score(_roi) -> float:
        d = st["pos"] - peak
        return 5.0 + 200.0 * math.exp(-(d * d) / (2 * 15.0 ** 2))  # flat away from peak

    cfg = AutoFocusConfig(
        dither_pulses=2, backlash_pulses=0, coarse_enable=True,
        coarse_range_pulses=200, coarse_step_pulses=20, coarse_refine_step=5,
        coarse_settle_frames=1, coarse_measure_frames=1,
    )
    assert peak > cfg.dither_max_pulses, "peak within dither reach; test is moot"
    engine = AutoFocusEngine(
        pulse=pulse, status=lambda _m: None, config=cfg, full_res_score=score,
    )
    engine.start(continuous=True)
    frame = np.random.default_rng(0).integers(0, 16384, (240, 320), dtype=np.uint16)
    for _ in range(8000):
        engine.feed_frame(frame)
        if engine._rs.coarse_done and not engine._rs.coarse_active:
            break
    assert engine._rs.coarse_done, "servo never escalated to the coarse sweep"
    for _ in range(2000):  # let the servo settle at the acquired peak
        engine.feed_frame(frame)
        if not engine.running:
            break
    assert abs(engine._rs.center - peak) <= cfg.coarse_step_pulses + cfg.dither_pulses, (
        f"coarse didn't acquire the far peak: centre={engine._rs.center} want ~{peak}"
    )


def test_coarse_leash_caps_servo_runaway() -> None:
    """The 'AF ruined a good focus' fix. After the coarse sweep pins the peak, the
    dither servo centre is leashed to peak +- coarse_servo_leash_pulses, so even a
    PHANTOM gradient (stale/noisy score reading '+ is always better' -- the live
    2800->1300 walk) can't drag the lens far off the acquired focus."""
    peak = 100
    st = {"pos": 0}
    mode = {"phantom": False}

    def pulse(sym: str, count: int) -> None:
        st["pos"] += count if sym == "+" else -count

    def score(_roi) -> float:
        if mode["phantom"]:
            return 100.0 + float(st["pos"])  # monotone ramp: '+' always looks better
        d = st["pos"] - peak
        return 5.0 + 200.0 * math.exp(-(d * d) / (2 * 18.0 ** 2))

    cfg = AutoFocusConfig(
        dither_pulses=2, backlash_pulses=0, coarse_enable=True,
        coarse_range_pulses=180, coarse_step_pulses=20, coarse_refine_step=5,
        coarse_settle_frames=1, coarse_measure_frames=1,
        coarse_servo_leash_pulses=30,
    )
    engine = AutoFocusEngine(
        pulse=pulse, status=lambda _m: None, config=cfg, full_res_score=score,
    )
    engine.start(continuous=True)
    frame = np.random.default_rng(0).integers(0, 16384, (240, 320), dtype=np.uint16)
    # Run until coarse acquisition completes (leash gets set on handoff).
    for _ in range(6000):
        engine.feed_frame(frame)
        if engine._rs.center_lo is not None and not engine._rs.coarse_active:
            break
    best = engine._rs.center
    assert abs(best - peak) <= cfg.coarse_step_pulses, (
        f"coarse didn't find the peak: centre={best} want ~{peak}"
    )
    # Now inject the phantom gradient and let the servo hunt: the leash must hold.
    mode["phantom"] = True
    for _ in range(4000):
        engine.feed_frame(frame)
        if not engine.running:
            break
    assert engine._rs.center <= best + cfg.coarse_servo_leash_pulses + 1, (
        f"leash failed: centre {engine._rs.center} ran past {best}+"
        f"{cfg.coarse_servo_leash_pulses} under a phantom gradient"
    )


def test_static_snap_back_finishes_at_best_not_walked_away() -> None:
    """The 'good focus -> deteriorated' static walk fix. A phantom +d>-d bias (the
    encoderless motor reads '+' as uphill even at the peak) makes the servo step '+'
    off a good focus while the picture actually blurs (the live 153->18 run). The
    static profile's snap-back anchor must notice the absolute centre level has
    collapsed below the best seen, RETURN to the best position, and finish there
    (one-shot) -- not keep walking into blur."""
    st = {"pos": 0}

    def pulse(sym: str, count: int) -> None:
        st["pos"] += count if sym == "+" else -count

    def score(_roi) -> float:
        # Sharp plateau near 0; collapses once the lens walks off it. A small +bias
        # makes the servo prefer '+' (the phantom), so it drifts off the plateau.
        base = 100.0 if abs(st["pos"]) < 3 else 15.0
        return base + 3.0 * st["pos"]

    cfg = AutoFocusConfig(dither_pulses=2, backlash_pulses=0, coarse_enable=False)
    cfg.apply_scene_profile(moving=False)  # static: snap-back on
    assert cfg.snap_back_frac > 0.0, "static profile must arm snap-back"
    engine = AutoFocusEngine(
        pulse=pulse, status=lambda _m: None, config=cfg, full_res_score=score,
    )
    engine.start(continuous=False)  # M4 one-shot
    frame = np.random.default_rng(0).integers(0, 16384, (240, 320), dtype=np.uint16)
    for _ in range(8000):
        engine.feed_frame(frame)
        if not engine.running:
            break
    assert not engine.running, "one-shot static run never finished"
    assert abs(engine._rs.center) < 5, (
        f"servo walked off the sharp plateau to centre={engine._rs.center} instead "
        f"of snapping back to the best focus"
    )


def test_motion_adaptive_profile_follows_scene() -> None:
    """M4 is the FLIGHT mode (moving in the air, static on the bench); M5 is a bench
    flight-sim (moving mirrors). The servo profile must therefore follow DETECTED scene
    motion, NOT the mode label. A static scene stays on the averaged/anchored profile; a
    panning scene is promoted to the fast per-patch tracking profile."""
    # Static scene: must stay on the averaged profile.
    sim = FocusSceneSimulator(profile="translate", step_px=0.0,
                              start_focus_pulses=4.0, best_focus_pulses=0.0)
    eng = AutoFocusEngine(pulse=sim.apply_pulse, status=lambda _m: None,
                          config=AutoFocusConfig(dither_pulses=2, coarse_enable=False))
    eng.adaptive_profile = True
    eng.start(continuous=True)
    for _ in range(400):
        eng.feed_frame(sim.render()); sim.step()
        if not eng.running:
            break
    assert eng._rs.moving_profile is False, "static scene wrongly promoted to moving"

    # Panning scene: must be promoted to the fast tracking profile.
    sim2 = FocusSceneSimulator(profile="translate", step_px=20.0, period=200,
                               start_focus_pulses=4.0, best_focus_pulses=0.0)
    eng2 = AutoFocusEngine(pulse=sim2.apply_pulse, status=lambda _m: None,
                           config=AutoFocusConfig(dither_pulses=2, coarse_enable=False))
    eng2.adaptive_profile = True
    eng2.start(continuous=True)
    saw_moving = False
    for _ in range(800):
        eng2.feed_frame(sim2.render()); sim2.step()
        if eng2._rs.moving_profile:
            saw_moving = True
            break
        if not eng2.running:
            break
    assert saw_moving, "panning scene never promoted to the fast tracking profile"


def test_static_scene_locks_and_stops_not_dithers_forever() -> None:
    """The live 'returned to best but worse, then failed' regression. On the encoderless
    motor, continuously dithering a STATIC scene loses open-loop position every round-trip
    and walks a found focus into blur (centre-0 score 86->12, true peak drifted to -244).
    With motion-adaptation on and no scene motion, the servo must LOCK and STOP -- park at
    the peak -- even though continuous=True was requested (flight-style)."""
    peak = 0
    st = {"pos": 0}

    def pulse(sym: str, count: int) -> None:
        st["pos"] += count if sym == "+" else -count

    def score(_roi) -> float:
        d = st["pos"] - peak
        return 5.0 + 200.0 * math.exp(-(d * d) / (2 * 15.0 ** 2))

    cfg = AutoFocusConfig(dither_pulses=2, backlash_pulses=0, coarse_enable=False)
    engine = AutoFocusEngine(pulse=pulse, status=lambda _m: None, config=cfg,
                             full_res_score=score)
    engine.adaptive_profile = True   # app/flight path: profile follows detected motion
    engine.start(continuous=True)    # flight-style continuous request
    frame = np.random.default_rng(0).integers(0, 16384, (240, 320), dtype=np.uint16)
    stopped_at = None
    for i in range(4000):
        engine.feed_frame(frame)     # SAME frame every step -> zero motion -> static
        if not engine.running:
            stopped_at = i
            break
    assert stopped_at is not None, "static scene dithered forever instead of stopping"
    assert engine._rs.moving_profile is False, "static scene wrongly promoted to moving"
    assert abs(st["pos"] - peak) <= cfg.dither_max_pulses, (
        f"parked at {st['pos']}, not near the peak {peak}"
    )


def test_anchored_lock_settles_on_flat_topped_peak() -> None:
    """Option A: a sharp focus curve is nearly FLAT over a +-2 pulse dither, so its
    peakedness never reaches peak_frac and the old servo kept nudging on a
    drift-dominated gradient instead of settling (the live run: centre climbed 19->83 to
    the peak but peak_avg stayed ~0.03, never locked, hunted until snap-back). The
    anchored lock must recognise the high plateau (gradient dead + level near the best
    seen) and settle near the peak."""
    peak = 0
    st = {"pos": 6}

    def pulse(sym: str, count: int) -> None:
        st["pos"] += count if sym == "+" else -count

    def score(_roi) -> float:
        d = st["pos"] - peak
        # sigma 8: flat enough at +-2 that peak_avg stays below peak_frac (so plain
        # concave detection can't lock -> exercises the anchored lock), but a few pulses
        # off the gradient is well above the deadband so the servo still climbs in.
        return 10.0 + 90.0 * math.exp(-(d * d) / (2 * 8.0 ** 2))

    cfg = AutoFocusConfig(dither_pulses=2, backlash_pulses=0, coarse_enable=False,
                          settle_frames=1, measure_frames=1, grad_avg_cycles=1)
    engine = AutoFocusEngine(pulse=pulse, status=lambda _m: None, config=cfg,
                             full_res_score=score)
    engine.start(continuous=False)  # one-shot: must reach a lock and finish
    frame = np.random.default_rng(0).integers(0, 16384, (240, 320), dtype=np.uint16)
    for _ in range(6000):
        engine.feed_frame(frame)
        if not engine.running:
            break
    assert not engine.running, "anchored lock never settled on the flat-topped peak"
    assert abs(st["pos"] - peak) <= cfg.dither_pulses + 2, (
        f"settled at {st['pos']}, not near the flat-topped peak {peak}"
    )


def test_adaptive_step_climbs_consistent_slope_decisively() -> None:
    """The live 'positive kept raising the score, it should have pushed up more'. On a
    clear, consistent slope the old servo shrank the probe every cycle and capped the step
    to it, so it crawled +-1 uphill and saw-toothed. The RProp-style adaptive step must
    GROW the probe while the gradient direction holds, so a long slope is climbed in
    bigger-than-base strides, then still homes in and locks near the peak."""
    peak = 0
    st = {"pos": 40}  # start well up the slope, one consistent direction to the peak

    def pulse(sym: str, count: int) -> None:
        st["pos"] += count if sym == "+" else -count

    def score(_roi) -> float:
        d = st["pos"] - peak
        return 5.0 + 200.0 * math.exp(-(d * d) / (2 * 25.0 ** 2))

    cfg = AutoFocusConfig(dither_pulses=2, backlash_pulses=0, coarse_enable=False,
                          settle_frames=1, measure_frames=1, grad_avg_cycles=1)
    engine = AutoFocusEngine(pulse=pulse, status=lambda _m: None, config=cfg,
                             full_res_score=score)
    engine.start(continuous=False)
    frame = np.random.default_rng(0).integers(0, 16384, (240, 320), dtype=np.uint16)
    centers = []
    for _ in range(6000):
        engine.feed_frame(frame)
        centers.append(engine._rs.center)
        if not engine.running:
            break
    steps = [b - a for a, b in zip(centers, centers[1:]) if b != a]
    assert steps, "centre never moved"
    biggest = max(abs(s) for s in steps)
    assert biggest > cfg.dither_pulses, (
        f"largest single-cycle step {biggest} did not exceed the base dither "
        f"{cfg.dither_pulses} -- adaptive step did not grow on the consistent slope"
    )
    assert not engine.running, "did not converge/lock after climbing the slope"
    assert abs(st["pos"] - peak) <= cfg.dither_pulses + 2, (
        f"settled at {st['pos']}, not near the peak {peak}"
    )


def test_snap_back_escalates_to_coarse_and_finds_far_peak() -> None:
    """The live 'said in focus but it's ~half the best score; I improved it 3 str3 clicks'.
    The fine servo chased a small local bump / phantom gradient, fell below its best, and
    snap-back used to just park there and declare focus -- while the TRUE peak was far
    outside the +-2 dither window. Now snap-back escalates to one coarse sweep-to-max,
    which must find and settle on the far global peak."""
    # Faithful to the live dynamic: the servo establishes a high best, then the absolute
    # level at the fixed commanded position collapses (there it was open-loop lash drift;
    # here we move the true peak far away mid-run). That is exactly the snap-back trigger
    # -- c falls well below best -- and the true focus is now outside the +-2 window, so
    # only the escalated coarse sweep can recover it.
    state = {"pos": 0, "peak": 0}

    def pulse(sym: str, count: int) -> None:
        state["pos"] += count if sym == "+" else -count

    def score(_roi) -> float:
        d = state["pos"] - state["peak"]
        return 5.0 + 200.0 * math.exp(-(d * d) / (2 * 15.0 ** 2))

    cfg = AutoFocusConfig(dither_pulses=2, backlash_pulses=0, coarse_enable=True,
                          coarse_range_pulses=120, coarse_step_pulses=10,
                          coarse_refine_step=3, coarse_settle_frames=1,
                          coarse_measure_frames=1, settle_frames=1, measure_frames=1,
                          grad_avg_cycles=1, snap_back_frac=0.85)
    engine = AutoFocusEngine(pulse=pulse, status=lambda _m: None, config=cfg,
                             full_res_score=score)
    engine.start(continuous=False)
    frame = np.random.default_rng(0).integers(0, 16384, (240, 320), dtype=np.uint16)
    moved_peak = False
    for _ in range(12000):
        engine.feed_frame(frame)
        if (not moved_peak and engine._rs.best_c_score >= 150.0
                and not engine._rs.coarse_active):
            state["peak"] = -60  # true focus jumps far; current pos now reads low
            moved_peak = True
        if not engine.running:
            break
    assert moved_peak, "never established a high best to fall from"
    assert engine._rs.coarse_done, "snap-back did not escalate to the coarse sweep"
    assert abs(state["pos"] - (-60)) <= 6, (
        f"settled at {state['pos']}, not on the far global peak -60 (missed by coarse)"
    )


def test_snap_back_after_confirmed_peak_does_not_sweep() -> None:
    """The live 'strong peak at centre 0, then a phantom nudge and it swept the whole
    range'. When a genuine concave peak WAS confirmed this run, a later snap-back must
    just return to best and stop -- launching a full +-range coarse sweep there would
    thrash a good focus through blur. Coarse must NOT run in that case."""
    state = {"pos": 0, "peak": 0}

    def pulse(sym: str, count: int) -> None:
        state["pos"] += count if sym == "+" else -count

    def score(_roi) -> float:
        d = state["pos"] - state["peak"]
        return 5.0 + 200.0 * math.exp(-(d * d) / (2 * 5.0 ** 2))  # sharp: peak confirms fast

    cfg = AutoFocusConfig(dither_pulses=2, backlash_pulses=0, coarse_enable=True,
                          coarse_range_pulses=120, coarse_step_pulses=10,
                          coarse_refine_step=3, coarse_settle_frames=1,
                          coarse_measure_frames=1, settle_frames=1, measure_frames=1,
                          grad_avg_cycles=1, snap_back_frac=0.85)
    engine = AutoFocusEngine(pulse=pulse, status=lambda _m: None, config=cfg,
                             full_res_score=score)
    engine.start(continuous=False)
    frame = np.random.default_rng(0).integers(0, 16384, (240, 320), dtype=np.uint16)
    dropped = False
    for _ in range(12000):
        engine.feed_frame(frame)
        if not dropped and engine._rs.peak_confirmed:
            state["peak"] = -60  # collapse level at current pos -> forces a snap-back
            dropped = True
        if not engine.running:
            break
    assert dropped, "a genuine peak was never confirmed"
    assert not engine._rs.coarse_done, "confirmed peak wrongly launched a coarse sweep"
    assert not engine.running, "did not finish after returning to best"


def test_moving_profile_disables_snap_back() -> None:
    """Snap-back must be OFF for a moving scene: its absolute level drifts with the
    content, so a best-so-far anchor is a stale trap. Moving relies on the coarse
    leash + wide deadband instead."""
    cfg = AutoFocusConfig()
    cfg.apply_scene_profile(moving=True)
    assert cfg.snap_back_frac == 0.0, "moving profile must not arm snap-back"


def main() -> int:
    tests = [
        test_leading_edge_from_motion,
        test_pcb_pulse_noise_filter,
        test_tilegrid_edge_region,
        test_select_leading_edge_roi_runs,
        test_saturation_aware_prefers_unclipped_detail,
        test_partly_clipped_busy_beats_featureless_unclipped,
        test_texture_metric_prefers_structure_over_noisy_sky,
        test_no_motion_picks_offcentre_texture,
        test_patch_tracker_reports_motion,
        test_servo_does_not_floor_lock_far_from_peak,
        test_focus_measure_full_res_sharp_beats_blur,
        test_servo_translate_converges_and_holds,
        test_reacquires_when_patch_pans_off_edge,
        test_moving_profile_converges_under_frequent_reacquire,
        test_servo_static_converges_and_finishes,
        test_servo_holds_when_already_focused,
        test_servo_normalised_gradient_ignores_content_scale,
        test_escalates_to_coarse_and_acquires,
        test_static_snap_back_finishes_at_best_not_walked_away,
        test_motion_adaptive_profile_follows_scene,
        test_static_scene_locks_and_stops_not_dithers_forever,
        test_anchored_lock_settles_on_flat_topped_peak,
        test_adaptive_step_climbs_consistent_slope_decisively,
        test_snap_back_escalates_to_coarse_and_finds_far_peak,
        test_snap_back_after_confirmed_peak_does_not_sweep,
        test_moving_profile_disables_snap_back,
        test_coarse_leash_caps_servo_runaway,
    ]
    for t in tests:
        t()
        print(f"  ok: {t.__name__}")
    print("autofocus tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
