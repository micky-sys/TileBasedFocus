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
    leading_edge_from_motion,
    select_leading_edge_roi,
)
from frame_grabber_sim import FocusSceneSimulator  # noqa: E402
from tile_geometry import TileGrid  # noqa: E402


# ── helpers ─────────────────────────────────────────────────────────────────


def run_autofocus(
    sim: FocusSceneSimulator,
    *,
    assisted: bool = True,
    config: AutoFocusConfig | None = None,
    lead_edge_hint: str | None = None,
    max_frames: int = 1500,
) -> dict:
    """Run the AF engine to completion against *sim*; return its report."""
    out: dict = {}

    def finished(ok: bool, report: dict) -> None:
        out["ok"] = ok
        out["report"] = report

    cfg = config or AutoFocusConfig(assisted=assisted)
    # The simulator has no cadence/strength serial; keep those callbacks inert.
    engine = AutoFocusEngine(
        pulse=sim.apply_pulse,
        set_strength=None,
        adjust_cadence=None,
        status=lambda _m: None,
        finished=finished,
        config=cfg,
    )
    engine.start(assisted=assisted, lead_edge_hint=lead_edge_hint)
    frames = 0
    for _ in range(max_frames):
        engine.feed_frame(sim.render())
        sim.step()
        frames += 1
        if not engine.running:
            break
    out.setdefault("ok", False)
    out["frames_run"] = frames
    out["final_optical"] = sim.optical_pulses
    out["final_blur"] = sim.current_blur_sigma()
    return out


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


def _assert_converged(out: dict, sim: FocusSceneSimulator, tol_pulses: float) -> None:
    assert out["ok"], f"autofocus did not finish OK: {out}"
    err = abs(out["final_optical"] - sim.best_focus)
    assert err <= tol_pulses, f"focus error {err:.1f} pulses > {tol_pulses} ({out})"


def test_assisted_translate_converges() -> None:
    sim = FocusSceneSimulator(
        profile="translate", start_focus_pulses=8.0, best_focus_pulses=0.0,
        backlash_pulses=2,
    )
    start_blur = sim.current_blur_sigma()
    out = run_autofocus(sim, assisted=True)
    _assert_converged(out, sim, tol_pulses=4.0)
    assert out["final_blur"] < start_blur  # genuinely sharper than the start


def test_full_auto_translate_converges() -> None:
    sim = FocusSceneSimulator(
        profile="translate", start_focus_pulses=36.0, best_focus_pulses=0.0,
        backlash_pulses=2,
    )
    out = run_autofocus(sim, assisted=False)
    _assert_converged(out, sim, tol_pulses=8.0)


def test_oscillate_converges_with_reversals() -> None:
    sim = FocusSceneSimulator(
        profile="oscillate", start_focus_pulses=8.0, best_focus_pulses=0.0,
        backlash_pulses=2, period=30,
    )
    out = run_autofocus(sim, assisted=True)
    _assert_converged(out, sim, tol_pulses=5.0)


def test_circle_with_sky_pause_resume() -> None:
    sim = FocusSceneSimulator(
        profile="circle", start_focus_pulses=8.0, best_focus_pulses=0.0,
        backlash_pulses=2, period=40,
    )
    out = run_autofocus(sim, assisted=True, max_frames=2500)
    # Must finish OK (paused through the sky sectors, resumed on texture) and
    # land near the optimum.
    _assert_converged(out, sim, tol_pulses=6.0)


def test_backlash_does_not_prevent_convergence() -> None:
    # Larger gear lash than the default compensation still converges.
    sim = FocusSceneSimulator(
        profile="translate", start_focus_pulses=12.0, best_focus_pulses=0.0,
        backlash_pulses=4,
    )
    out = run_autofocus(sim, assisted=True, config=AutoFocusConfig(
        assisted=True, backlash_pulses=4,
    ))
    _assert_converged(out, sim, tol_pulses=5.0)


def main() -> int:
    tests = [
        test_leading_edge_from_motion,
        test_tilegrid_edge_region,
        test_select_leading_edge_roi_runs,
        test_patch_tracker_reports_motion,
        test_assisted_translate_converges,
        test_full_auto_translate_converges,
        test_oscillate_converges_with_reversals,
        test_circle_with_sky_pause_resume,
        test_backlash_does_not_prevent_convergence,
    ]
    for t in tests:
        t()
        print(f"  ok: {t.__name__}")
    print("autofocus tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
