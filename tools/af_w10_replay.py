"""Drive the w10 autofocus decision module (host/capture/af_w10.py) with recorded flights, and a synthetic lens.

The recorded sweep is turned into a VIRTUAL LENS: the engine commands an encoder target, the lens goes there, and
the frame served is a recorded frame whose encoder is nearest that position (a random one among those within
``--window`` counts, so repeated holds see different ground). The engine therefore gets exactly the frames the
camera would have delivered at the positions it chose, with the flight's real noise, cloud and content bias.
Truth is the V fitted to ALL recorded frames of that sweep direction; the engine must park within half a depth
of focus of it from several random start positions.

    py -3 tools/af_w10_replay.py <edges.csv>... [--starts 5] [--window 120] [--tol-frac 0.5] [--verbose]
                                 [--from ENC] [--trace] [--min-edges 300] [--min-roi 1] [--all]
    py -3 tools/af_w10_replay.py --self-check       synthetic V, 0.6 px noise, content bias, 1000-count lash
"""
from __future__ import annotations

import math, os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "host"))
from capture.af_w10 import FRAME_S, W10Config, W10Focus, bracketed, depth_counts, fit_v_local  # noqa: E402
from w10_af_study import RISE_PX, by_run, load, split_direction  # noqa: E402


class RecordedLens:
    """Serve recorded frames by commanded encoder position."""

    def __init__(self, rows, window, rng, direction):
        self.e = np.array([r["enc"] for r in rows]); self.rows = rows
        self.window, self.rng, self.d = window, rng, direction
        self.pos = float(self.e[len(self.e) // 2])
        self.lo, self.hi = float(self.e.min()), float(self.e.max())

    def move(self, target):
        self.pos = min(max(float(target), self.lo), self.hi)
        self.clamped = self.pos != float(target)

    def frame(self):
        near = np.flatnonzero(np.abs(self.e - self.pos) <= self.window)
        if getattr(self, "clamped", False):
            # the lens is against the end of the recording. A real lens against a stop keeps seeing new ground
            # at one flat encoder value, so serve varied end frames but report the clamp position, and let the
            # engine's stall detector do its job
            r = self.rows[self.rng.choice(near)] if len(near) else self.rows[int(np.argmin(np.abs(self.e - self.pos)))]
            return self.pos, dict(w10=r["w10"], n_edges=r["n"], n_roi=5, med_mean=r["mean"], enc_stale=0)
        if len(near) == 0:
            # a gap in the recording: serve the nearest frame AHEAD in the sweep direction (the lens keeps
            # going), falling back to the nearest overall at the range end
            ahead = np.flatnonzero(self.d * (self.e - self.pos) >= 0)
            cand = ahead if len(ahead) else np.arange(len(self.e))
            near = [int(cand[np.argmin(np.abs(self.e[cand] - self.pos))])]
        r = self.rows[self.rng.choice(near)]
        stats = dict(w10=r["w10"], n_edges=r["n"], n_roi=5, med_mean=r["mean"], enc_stale=0)
        return r["enc"], stats


def run_engine(lens, cfg, max_frames=400, verbose=False, trace=False):
    af = W10Focus(cfg=cfg)
    for i in range(max_frames):
        enc, stats = lens.frame()
        n_before = len(af.enc)
        act = af.feed(enc, stats)
        if trace:
            used = "sample" if len(af.enc) > n_before else (act.reason or "gated out")
            fit = f" fit e0 {af.fit.e0:.0f} +-{af.ci_half:.0f}" if af.fit is not None and len(af.enc) >= cfg.min_frames else ""
            tgt = f" -> {act.target:.0f}" if act.kind == "move" else ""
            print(f"      f{i + 1:3d} {af.state.value:6s} enc {enc:7.0f} w10 {stats['w10']:5.2f} n_edges {stats['n_edges']:5.0f}"
                  f" {used:9s}{fit} {act.kind}{tgt}")
        if act.kind == "move":
            lens.move(act.target)
        elif act.kind in ("done", "fail"):
            break
    if verbose:
        print("\n".join("      " + l for l in af.log))
    return af


def binned_min(e, w, bin_counts, min_per_bin=5):
    """Encoder value of the minimum of the 3-bin running mean of per-bin median w10 -- a model-free vertex."""
    edges = np.arange(e.min(), e.max() + bin_counts, bin_counts)
    idx = np.digitize(e, edges)
    centres, meds = [], []
    for i in np.unique(idx):
        if (idx == i).sum() >= min_per_bin:
            centres.append(edges[i - 1] + bin_counts / 2); meds.append(np.median(w[idx == i]))
    meds = np.asarray(meds); centres = np.asarray(centres)
    if len(meds) < 3:
        return float(centres[np.argmin(meds)])
    sm = np.convolve(meds, np.ones(3) / 3, mode="valid")
    return float(centres[1:-1][np.argmin(sm)])


def replay(paths, starts, window, tol_frac, verbose, start_from=None, trace=False, gate=None, include_all=False):
    """``start_from``: force every start to this encoder (default: random inside the recording). ``gate``: dict
    of W10Config gate overrides (min_edges, min_roi). ``include_all``: also replay sweeps whose recording does
    not bracket the bowl (no trustworthy truth there; the engine will usually run off the recording's end)."""
    rows = []
    for p in paths:
        r, total, dropped = load(p); rows += r
        print(f"{os.path.basename(p)}: {len(r)}/{total} gated")
    fails = 0; n_sweeps = 0
    for run, rs in by_run(rows).items():
        if not run.startswith("FOCUS"):
            continue
        rs = [r for r in rs if math.isfinite(r["enc"])]
        for dname, sub in split_direction(rs).items():
            if len(sub) < 40:
                continue
            e = np.array([r["enc"] for r in sub]); w = np.array([r["w10"] for r in sub])
            # Two references for the vertex: the bowl-local parabola (what the engine fits) and the model-free
            # minimum of binned medians. On the recorded Vs they disagree by 100-270 c (one flank is a shelf, and
            # a sparse sweep has ~8 frames per bin), and the data cannot say which is right, so a park counts as
            # good when it is within half a depth of the NEARER reference; the park spread across starts is the
            # engine's precision and is reported separately.
            truth, _ = fit_v_local(e, w, RISE_PX, W10Config().fit_window_depths)
            is_br = truth is not None and bracketed(e, w, truth, RISE_PX)
            if truth is None or not (is_br or include_all):
                continue
            dep = depth_counts(truth, RISE_PX); n_sweeps += 1
            refs = (truth.e0, binned_min(e, w, max(100.0, dep / 3)))
            if not is_br:
                print(f"\n[not bracketed in the recording: references are indicative only]", end="")
            d = -1 if dname == "down" else 1
            # the recorded frames were all taken in ONE direction, so there is no take-up to skip here
            # (takeup_counts=0); the flight's real take-up is exercised by the synthetic self-check
            # span budget = the recording plus the back-off (a start clamped at the range end still counts the
            # unreached back-off as travel); the real 7000 c budget is a flight safety, not a harness one
            start_offset = min(2500.0, (e.max() - e.min()) * 0.45)
            cfg = W10Config(direction=d, tol_frac=tol_frac, takeup_counts=0.0, start_offset=start_offset,
                            span_counts=(e.max() - e.min()) + start_offset + 500, **(gate or {}))
            print(f"\n{run} [{dname}] {len(sub)} frames, enc {e.min():.0f}..{e.max():.0f}; vertex refs: fit {refs[0]:.0f} / "
                  f"binned min {refs[1]:.0f}, w0 {truth.w0:.2f}, depth +-{dep:.0f}")
            errs, parks, frames = [], [], []
            for s in range(starts):
                rng = np.random.default_rng(s)
                lens = RecordedLens(sub, window, rng, d)
                # start where the back-off lands inside the recorded range (beyond it the data simply do not exist)
                lo_s, hi_s = (e.min(), e.max() - cfg.start_offset) if d < 0 else (e.min() + cfg.start_offset, e.max())
                lens.pos = float(rng.uniform(lo_s, hi_s)) if start_from is None else float(start_from)
                if trace:
                    print(f"   start #{s + 1} at {lens.pos:.0f}")
                af = run_engine(lens, cfg, verbose=verbose, trace=trace)
                rep = af.report()
                if rep["state"] != "done":
                    print(f"   {rep['state']} after {rep['frames']} frames -- {af.log[-1]}")
                    fails += 1; continue
                err = min((rep["vertex"] - r for r in refs), key=abs)
                errs.append(err); parks.append(rep["vertex"]); frames.append(rep["frames"])
                flag = "" if abs(err) <= 0.5 * dep else "   <-- outside half a depth"
                print(f"   {af.backoffs} back-off(s): parked {rep['vertex']:.0f} (err {err:+5.0f} c, "
                      f"CI +-{rep['ci_half']:.0f}) in {rep['frames']} frames = {rep['seconds']:.0f} s, {rep['samples']} gated{flag}")
                if abs(err) > 0.5 * dep:
                    fails += 1
            if errs:
                print(f"   => rms err {np.sqrt(np.mean(np.square(errs))):.0f} c vs nearer ref; parks {np.median(parks):.0f} "
                      f"+-{np.std(parks):.0f} c (median +-sd over starts); frames median {np.median(frames):.0f} "
                      f"({np.median(frames) * FRAME_S:.0f} s)")
    print(f"\n{n_sweeps} bracketed sweeps replayed, {fails} failures (not done, or parked outside half a depth)")
    return 1 if fails else 0


# ---------------------------------------------------------------------------------------------- synthetic

class SyntheticLens:
    """A lens with backlash: the motor-side encoder can sit anywhere within +-lash/2 of the lens without the lens
    moving; the lens only follows once the motor takes up the slack (sec.49/53.5). Scenes change every 8 frames
    (content bias), frames carry 0.6 px noise, a few are cloud."""

    def __init__(self, e0_lens=12000.0, w0=5.0, depth=400.0, lash=1000.0, seed=0):
        self.e0, self.w0, self.lash = e0_lens, w0, lash
        self.c = ((w0 + 0.3) ** 2 - w0 ** 2) / depth ** 2
        self.rng = np.random.default_rng(seed)
        self.pos = 14000.0; self.lens = self.pos + lash / 2; self.n = 0; self.bias = 0.0   # as if last moved down

    def move(self, target):
        self.pos = float(target)
        if self.pos - self.lens > self.lash / 2:
            self.lens = self.pos - self.lash / 2
        elif self.pos - self.lens < -self.lash / 2:
            self.lens = self.pos + self.lash / 2

    def lens_pos(self):
        return self.lens

    def frame(self):
        self.n += 1
        if self.n % 8 == 1:
            self.bias = self.rng.normal(0, 0.4)
        w = math.sqrt(self.w0 ** 2 + self.c * (self.lens_pos() - self.e0) ** 2) + self.bias + self.rng.normal(0, 0.6)
        cloud = self.rng.random() < 0.08
        stats = dict(w10=max(w, 1.75), n_edges=0 if cloud else int(self.rng.integers(400, 3000)), n_roi=5,
                     med_mean=9000.0, enc_stale=0)
        return self.pos + self.rng.normal(0, 2), stats


def self_check():
    for seed in range(4):
        lens = SyntheticLens(seed=seed)
        cfg = W10Config(direction=-1, start_offset=2500, span_counts=7000, lash_counts=1500)
        af = run_engine(lens, cfg)
        rep = af.report()
        assert rep["state"] == "done", (seed, rep, af.log[-3:])
        # the lens, approached downward exactly as the curve was measured, must sit at ITS vertex
        lens_err = lens.lens_pos() - lens.e0
        assert abs(lens_err) < 200, (seed, lens_err, af.log)
        print(f"seed {seed}: done in {rep['frames']} frames ({rep['seconds']:.0f} s), {rep['samples']} gated, "
              f"lens error {lens_err:+.0f} c, motor vertex {rep['vertex']:.0f}, CI +-{rep['ci_half']:.0f}")
    # the metric saturates at ~11 px: a start far from focus records a flat ceiling before the bowl
    # (2026-10-21, f/4), which bends the all-samples parabola concave; the bowl must still be found
    class Shelf(SyntheticLens):
        def frame(self):
            e, s = super().frame(); s["w10"] = min(s["w10"], 11.0); return e, s
    lens = Shelf(seed=0); lens.pos = 15500.0; lens.lens = lens.pos + lens.lash / 2   # back-off lands 6000 c out
    af = run_engine(lens, W10Config(direction=-1, start_offset=2500, span_counts=9000, lash_counts=1500))
    rep = af.report(); lens_err = lens.lens_pos() - lens.e0
    assert rep["state"] == "done" and abs(lens_err) < 200, (rep, lens_err, af.log[-3:])
    print(f"shelf: done in {rep['frames']} frames, lens error {lens_err:+.0f} c, CI +-{rep['ci_half']:.0f}")
    # a lens that never comes into focus inside the budget must FAIL, not park
    lens = SyntheticLens(e0_lens=-20000.0)
    af = run_engine(lens, W10Config(direction=-1, span_counts=5000))
    assert af.state.value == "fail", af.log[-2:]
    # a long run of cloud must FAIL rather than hold forever
    class Cloud(SyntheticLens):
        def frame(self):
            e, s = super().frame(); s["n_edges"] = 0; return e, s
    af = run_engine(Cloud(), W10Config(direction=-1))
    assert af.state.value == "fail" and "gate" in af.log[-1], af.log[-1]
    print("ok"); return 0


def main(argv):
    if "--self-check" in argv:
        return self_check()
    args = list(argv); opt = {"--starts": 5, "--window": 120.0, "--tol-frac": W10Config().tol_frac,
                              "--from": float("nan"), "--min-edges": W10Config().min_edges, "--min-roi": W10Config().min_roi}
    for k in list(opt):
        if k in args:
            i = args.index(k); opt[k] = type(opt[k])(args[i + 1]); del args[i:i + 2]
    verbose = "--verbose" in args; trace = "--trace" in args; include_all = "--all" in args
    args = [a for a in args if a not in ("--verbose", "--trace", "--all")]
    return replay(args, opt["--starts"], opt["--window"], opt["--tol-frac"], verbose,
                  start_from=None if math.isnan(opt["--from"]) else opt["--from"], trace=trace,
                  gate=dict(min_edges=opt["--min-edges"], min_roi=opt["--min-roi"]), include_all=include_all)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
