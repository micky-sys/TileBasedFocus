"""Hardware-free autofocus ground-test harness (HANDOFF §14.7 layer 1).

Unlike ``test_autofocus.py`` (a binary pass/fail regression gate), this sweeps the
AF engine across the three mirror-rig motion profiles and a spread of starting
defocus / scene seeds, then reports the numbers a bench run would produce:

* accuracy      -- |final optical position - true best focus|, in pulses,
* repeatability -- spread (std) of the landing position across runs,
* convergence   -- frames and total commanded pulses to finish,
* sharpening    -- start vs final blur sigma (lower = sharper).

These are the baseline figures to compare the real M5 / OrthoSim rig against.
No hardware, no new deps: the focus loop closes entirely in software by wiring
the engine's pulse callback straight into ``FocusSceneSimulator``.

Run:  py -3 tools/af_ground_test.py            # full sweep
      py -3 tools/af_ground_test.py --quick     # one seed, fewer offsets
"""

from __future__ import annotations

import argparse
import statistics
from dataclasses import dataclass

# Reuse the already-tested driver + path setup from the regression suite.
from test_autofocus import run_autofocus  # noqa: E402  (sets sys.path)

from capture.autofocus import AutoFocusConfig  # noqa: E402
from frame_grabber_sim import FocusSceneSimulator  # noqa: E402

# A landing within this many pulses of true best focus counts as converged.
# 1 pulse = 10 ms of open-loop drive; the engine has no encoder, so a few pulses
# of residual is expected and acceptable (HANDOFF §14.2/§14.8).
CONVERGE_TOL_PULSES = 6.0


@dataclass
class RunResult:
    profile: str
    assisted: bool
    start: float
    seed: int
    ok: bool
    error: float          # |final optical - best focus|, pulses
    final_optical: float
    frames: int
    total_pulses: int
    start_blur: float
    final_blur: float

    @property
    def converged(self) -> bool:
        return self.ok and self.error <= CONVERGE_TOL_PULSES


def _one_run(profile: str, assisted: bool, start: float, seed: int,
             max_frames: int) -> RunResult:
    sim = FocusSceneSimulator(
        profile=profile, start_focus_pulses=start, best_focus_pulses=0.0,
        backlash_pulses=2, seed=seed,
    )
    start_blur = sim.current_blur_sigma()
    cfg = AutoFocusConfig(assisted=assisted)
    out = run_autofocus(sim, assisted=assisted, config=cfg, max_frames=max_frames)
    rep = out.get("report", {}) or {}
    return RunResult(
        profile=profile,
        assisted=assisted,
        start=start,
        seed=seed,
        ok=bool(out.get("ok")),
        error=abs(out["final_optical"] - sim.best_focus),
        final_optical=out["final_optical"],
        frames=int(rep.get("frames", out.get("frames_run", 0))),
        total_pulses=int(rep.get("total_pulses", 0)),
        start_blur=start_blur,
        final_blur=out["final_blur"],
    )


def _summarize(runs: list[RunResult]) -> dict:
    errs = [r.error for r in runs]
    finals = [r.final_optical for r in runs]
    frames = [r.frames for r in runs]
    return {
        "n": len(runs),
        "converged": sum(r.converged for r in runs),
        "ok": sum(r.ok for r in runs),
        "acc_mean": statistics.fmean(errs),
        "acc_max": max(errs),
        "repeat_std": statistics.pstdev(finals) if len(finals) > 1 else 0.0,
        "frames_mean": statistics.fmean(frames),
        "frames_max": max(frames),
    }


def run_sweep(quick: bool) -> list[RunResult]:
    seeds = [0] if quick else [0, 1, 2]
    # Assisted = operator got close first (small excursion); full-auto = bigger hunt.
    starts = {
        True: [6.0] if quick else [4.0, 8.0, 12.0],
        False: [24.0] if quick else [20.0, 30.0, 36.0],
    }
    profiles = ["translate", "oscillate", "circle"]
    results: list[RunResult] = []
    for profile in profiles:
        max_frames = 2500 if profile == "circle" else 1500
        for assisted in (True, False):
            for start in starts[assisted]:
                for seed in seeds:
                    results.append(
                        _one_run(profile, assisted, start, seed, max_frames)
                    )
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quick", action="store_true",
                    help="one seed and one start offset per scenario (fast)")
    args = ap.parse_args()

    results = run_sweep(args.quick)

    print(f"\nAutofocus ground test (simulator) -- tol {CONVERGE_TOL_PULSES:.0f} pulses\n")
    hdr = (f"{'profile':<10} {'mode':<10} {'runs':>4} {'conv':>5} "
           f"{'acc_mean':>9} {'acc_max':>8} {'repeat_sd':>10} "
           f"{'frames_avg':>11} {'blur->':>13}")
    print(hdr)
    print("-" * len(hdr))

    all_converged = True
    for profile in ["translate", "oscillate", "circle"]:
        for assisted in (True, False):
            group = [r for r in results
                     if r.profile == profile and r.assisted == assisted]
            if not group:
                continue
            s = _summarize(group)
            mode = "assisted" if assisted else "full-auto"
            blur0 = statistics.fmean(r.start_blur for r in group)
            blur1 = statistics.fmean(r.final_blur for r in group)
            flag = "" if s["converged"] == s["n"] else "  <-- FAIL"
            if s["converged"] != s["n"]:
                all_converged = False
            print(f"{profile:<10} {mode:<10} {s['n']:>4} "
                  f"{s['converged']:>2}/{s['n']:<2} "
                  f"{s['acc_mean']:>9.2f} {s['acc_max']:>8.2f} "
                  f"{s['repeat_std']:>10.2f} "
                  f"{s['frames_mean']:>7.0f}/{s['frames_max']:<3d} "
                  f"{blur0:>5.2f}->{blur1:<5.2f}{flag}")

    n = len(results)
    conv = sum(r.converged for r in results)
    print("-" * len(hdr))
    print(f"\nTotal: {conv}/{n} runs converged within {CONVERGE_TOL_PULSES:.0f} pulses.\n")

    # Self-check: the whole point of the ground test is that AF converges on the
    # nominal scenarios. Fail loudly (non-zero exit) if it does not.
    assert all_converged, "autofocus did NOT converge on every scenario (see table)"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
