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

# Reuse the already-tested servo driver + path setup from the regression suite.
from test_autofocus import _median, run_servo  # noqa: E402  (sets sys.path)

from capture.autofocus import AutoFocusConfig  # noqa: E402
from frame_grabber_sim import FocusSceneSimulator  # noqa: E402

# A resting centre within this many pulses of true best focus counts as
# converged. 1 pulse = 10 ms of open-loop drive; with dither + gear lash a couple
# of pulses of residual is the servo's resolution floor and is acceptable.
CONVERGE_TOL_PULSES = 6.0

# Frames per run. The servo never "finishes" (continuous, like flight), so we run
# a fixed budget and read the resting centre from the tail.
RUN_FRAMES = 1600
TAIL_FRAMES = 80


@dataclass
class RunResult:
    profile: str
    start: float
    seed: int
    error: float          # |resting centre - best focus|, pulses
    final_optical: float
    tail_span: float      # peak-to-peak of the optical tail (wobble amplitude)
    frames: int
    start_blur: float
    final_blur: float

    @property
    def converged(self) -> bool:
        return self.error <= CONVERGE_TOL_PULSES


def _one_run(profile: str, start: float, seed: int, frames: int) -> RunResult:
    sim = FocusSceneSimulator(
        profile=profile, start_focus_pulses=start, best_focus_pulses=0.0,
        backlash_pulses=2, seed=seed,
    )
    start_blur = sim.current_blur_sigma()
    optical = run_servo(sim, config=AutoFocusConfig(), frames=frames)
    tail = optical[-TAIL_FRAMES:] if len(optical) >= TAIL_FRAMES else optical
    center_est = _median(tail)
    return RunResult(
        profile=profile,
        start=start,
        seed=seed,
        error=abs(center_est - sim.best_focus),
        final_optical=center_est,
        tail_span=(max(tail) - min(tail)) if tail else 0.0,
        frames=len(optical),
        start_blur=start_blur,
        final_blur=sim.current_blur_sigma(),
    )


def _summarize(runs: list[RunResult]) -> dict:
    errs = [r.error for r in runs]
    finals = [r.final_optical for r in runs]
    spans = [r.tail_span for r in runs]
    return {
        "n": len(runs),
        "converged": sum(r.converged for r in runs),
        "acc_mean": statistics.fmean(errs),
        "acc_max": max(errs),
        "repeat_std": statistics.pstdev(finals) if len(finals) > 1 else 0.0,
        "span_mean": statistics.fmean(spans),
    }


def run_sweep(quick: bool) -> list[RunResult]:
    seeds = [0] if quick else [0, 1, 2]
    starts = [12.0] if quick else [6.0, 12.0, 24.0]
    profiles = ["translate", "oscillate", "circle"]
    results: list[RunResult] = []
    for profile in profiles:
        for start in starts:
            for seed in seeds:
                results.append(_one_run(profile, start, seed, RUN_FRAMES))
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quick", action="store_true",
                    help="one seed and one start offset per scenario (fast)")
    args = ap.parse_args()

    results = run_sweep(args.quick)

    print(f"\nAutofocus servo ground test (simulator) -- tol {CONVERGE_TOL_PULSES:.0f} pulses\n")
    hdr = (f"{'profile':<10} {'runs':>4} {'conv':>5} "
           f"{'acc_mean':>9} {'acc_max':>8} {'repeat_sd':>10} "
           f"{'wobble':>7} {'blur->':>13}")
    print(hdr)
    print("-" * len(hdr))

    all_converged = True
    for profile in ["translate", "oscillate", "circle"]:
        group = [r for r in results if r.profile == profile]
        if not group:
            continue
        s = _summarize(group)
        blur0 = statistics.fmean(r.start_blur for r in group)
        blur1 = statistics.fmean(r.final_blur for r in group)
        flag = "" if s["converged"] == s["n"] else "  <-- FAIL"
        if s["converged"] != s["n"]:
            all_converged = False
        print(f"{profile:<10} {s['n']:>4} "
              f"{s['converged']:>2}/{s['n']:<2} "
              f"{s['acc_mean']:>9.2f} {s['acc_max']:>8.2f} "
              f"{s['repeat_std']:>10.2f} "
              f"{s['span_mean']:>7.1f} "
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
