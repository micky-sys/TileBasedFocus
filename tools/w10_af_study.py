"""How many frames does a w10 autofocus need? Answered from recorded flights (HANDOFF sec.54).

Reads flight_edges.py CSVs (either the sec.52 flight-survey layout with `run`/`enc` columns or the current one
with `encoder`), gates frames per sec.52.4, and reports four things the autofocus design needs:

  [noise]     per-frame w10 scatter at a fixed lens position on the SAME ground (consecutive frames) versus the
              spread of sweep medians across DIFFERENT ground at a fixed lens position (June flight, lens fixed all
              line; production runs) -- the content-bias floor that dwelling at one station cannot average away.
              Also whether regressing w10 on log(n_edges) (sec.52.5 density bias) shrinks it, and the gate yield.
  [vcurve]    per run and sweep direction: w10^2 = w0^2 + c (e - e0)^2 fitted to the gated frames; vertex e0, floor
              w0, depth of focus (counts for +0.3 px), bootstrap CI of e0, lash between directions.
  [bootstrap] vertex error vs TOTAL frames for three sampling designs emulated on the recorded sweep: dwell N
              frames at K stations, one frame per step (creep), coarse-then-fine two-pass.
  [sequential] replay each sweep frame by frame with the stopping rule the engine uses (both flanks bracketed AND
              vertex CI <= tol) and report frames-to-stop and the error at stop.

    py -3 tools/w10_af_study.py <sweep.csv>... [--fixed <fixed-focus.csv>...] [--tol-frac 0.33] [--png DIR]
    py -3 tools/w10_af_study.py --self-check
"""
from __future__ import annotations

import csv, math, os, re, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "host"))
from capture.af_w10 import (  # noqa: E402
    FRAME_S, Vfit, bootstrap_vertex, bracketed, fit_v, depth_counts,
)

MIN_EDGES = 300
CLOUD_MEAN = 22000.0
RISE_PX = 0.3            # "depth of focus": counts either side of the vertex that add 0.3 px to w10
_RUN_RE = re.compile(r"_(FOCUS_C\d_\d{6}|\d{6})_L\d+_S(\d+)_F(\d+)")


# ---------------------------------------------------------------------------------------------- load + gate

def load(path):
    """Gated frames as dicts {run, sweep, frame, t, enc, w10, n, mean, file} sorted by time. Also returns yield."""
    rows, total, dropped = [], 0, {"no_w10": 0, "edges": 0, "stale": 0, "cloud": 0}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            total += 1
            try:
                w = float(r.get("w10") or "nan"); n = int(float(r.get("n_edges") or 0))
            except ValueError:
                w, n = math.nan, 0
            if not math.isfinite(w):
                dropped["no_w10"] += 1; continue
            if n < MIN_EDGES:
                dropped["edges"] += 1; continue
            if r.get("enc_stale") in ("1", "True"):
                dropped["stale"] += 1; continue
            mean = float(r.get("med_mean") or "nan")
            if math.isfinite(mean) and mean > CLOUD_MEAN:
                dropped["cloud"] += 1; continue
            enc = float(r.get("enc") or r.get("encoder") or "nan")
            m = _RUN_RE.search(r["file"])
            run = r.get("run") or (m.group(1) if m else os.path.basename(path))
            roi = [float(r.get(f"r{k}_w10") or "nan") for k in range(5)]
            rows.append(dict(run=run, sweep=int(m.group(2)) if m else 0, frame=int(m.group(3)) if m else 0,
                             t=r.get("ts") or "", enc=enc, w10=w, n=n, mean=mean, roi=roi, file=r["file"], src=path))
    key = (lambda r: r["t"]) if all(r["t"] for r in rows) else (lambda r: (r["sweep"], r["frame"]))
    rows.sort(key=key)
    return rows, total, dropped


def by_run(rows):
    out = {}
    for r in rows:
        out.setdefault(r["run"], []).append(r)
    return out


def split_direction(rows):
    """{'down': [...], 'up': [...]} by the sign of the encoder's motion around each frame (time order), as in
    w10_vcurve.split_by_direction. Frames where it is not moving take the last known direction."""
    e = np.array([r["enc"] for r in rows]); out = {"down": [], "up": []}; cur = None
    for i, r in enumerate(rows):
        lo, hi = max(i - 3, 0), min(i + 3, len(rows) - 1)
        d = e[hi] - e[lo]
        if math.isfinite(d) and abs(d) > 1:
            cur = "up" if d > 0 else "down"
        if cur:
            out[cur].append(r)
    return out


# ---------------------------------------------------------------------------------------------- [noise]

def robust_sd(x):
    x = np.asarray(x, float)
    return float(1.4826 * np.median(np.abs(x - np.median(x)))) if len(x) >= 3 else float("nan")


def report_noise(sweep_rows, fixed_rows):
    print("\n=== [noise] what one frame is worth ===")
    # (a) consecutive frames, same encoder, within a run: the per-frame noise on the same ground
    d = []
    for run, rs in by_run(sweep_rows).items():
        for a, b in zip(rs, rs[1:]):
            if a["enc"] == b["enc"] and math.isfinite(a["enc"]):
                d.append(b["w10"] - a["w10"])
    if d:
        sd = robust_sd(d) / math.sqrt(2)
        print(f"  (a) frame-to-frame at a FIXED encoder, same ground: sd {sd:.2f} px  (robust, {len(d)} consecutive pairs)")
        # do all five ROIs earn their 160 ms? per-ROI frame sd, validity, and what a 3-ROI (top row + centre)
        # pooled estimate would have cost in noise
        pairs = [(a, b) for run, rs in by_run(sweep_rows).items() for a, b in zip(rs, rs[1:])
                 if a["enc"] == b["enc"] and math.isfinite(a["enc"])]
        for k in range(5):
            dk = [b["roi"][k] - a["roi"][k] for a, b in pairs if math.isfinite(a["roi"][k]) and math.isfinite(b["roi"][k])]
            valid = np.mean([math.isfinite(r["roi"][k]) for r in sweep_rows])
            if len(dk) >= 10:
                print(f"      ROI {k}: frame sd {robust_sd(dk) / math.sqrt(2):.2f} px, valid in {100 * valid:.0f}% of gated frames")
        med3 = [(np.nanmedian(a["roi"][:3]), np.nanmedian(b["roi"][:3])) for a, b in pairs]
        d3 = [y - x for x, y in med3 if math.isfinite(x) and math.isfinite(y)]
        if len(d3) >= 10:
            print(f"      median of ROIs 0-2 only: frame sd {robust_sd(d3) / math.sqrt(2):.2f} px vs {sd:.2f} pooled over 5")
    else:
        sd = float("nan")
        print("  (a) no consecutive same-encoder pairs in the sweep CSVs")
    # (b) fixed-focus runs: spread of per-sweep medians across different ground
    out = {"frame_sd": sd}
    for name, rs in by_run(fixed_rows).items():
        w = np.array([r["w10"] for r in rs]); n = np.array([r["n"] for r in rs], float)
        sw = {}
        for r in rs:
            sw.setdefault(r["sweep"], []).append(r["w10"])
        med = np.array([np.median(v) for v in sw.values() if len(v) >= 5])
        if len(med) < 3:
            continue
        sd_med = robust_sd(med); n_per = float(np.median([len(v) for v in sw.values()]))
        expect = sd / math.sqrt(n_per) if math.isfinite(sd) else float("nan")
        excess = math.sqrt(max(sd_med ** 2 - expect ** 2, 0.0)) if math.isfinite(expect) else float("nan")
        # (c) does the edge-density regression take the content term out?
        X = np.c_[np.ones(len(w)), np.log(n)]
        coef, *_ = np.linalg.lstsq(X, w, rcond=None); resid = w - X @ coef
        r = float(np.corrcoef(np.log(n), w)[0, 1])
        print(f"  (b) {name}: {len(rs)} frames, {len(med)} sweeps of ~{n_per:.0f}; per-frame sd {robust_sd(w):.2f}, "
              f"sweep-median sd {sd_med:.2f} (noise alone would give {expect:.2f}) -> content floor ~{excess:.2f} px")
        print(f"  (c)   w10 vs log(n_edges): r={r:+.2f}, slope {coef[1]:+.2f} px per e-fold; residual sd "
              f"{robust_sd(resid):.2f} vs {robust_sd(w):.2f} raw")
        out.setdefault("content_floor", []).append(excess)
    return out


# ---------------------------------------------------------------------------------------------- [vcurve]

def report_vcurve(sweep_rows, tol_frac):
    print("\n=== [vcurve] one V per run and direction ===")
    fits = {}
    for run, rs in by_run(sweep_rows).items():
        rs = [r for r in rs if math.isfinite(r["enc"])]
        if len(rs) < 30:
            continue
        dirs = split_direction(rs)
        got = {}
        for dname, sub in dirs.items():
            if len(sub) < 30:
                continue
            e = np.array([r["enc"] for r in sub]); w = np.array([r["w10"] for r in sub])
            fit = fit_v(e, w)
            if fit is None:
                print(f"  {run} [{dname}] n={len(sub)} enc {e.min():.0f}..{e.max():.0f}: no convex fit (not bracketed)")
                continue
            lo, hi = bootstrap_vertex(e, w, n_boot=200, seed=1)
            dep = depth_counts(fit, RISE_PX)
            brk = bracketed(e, w, fit, RISE_PX)
            print(f"  {run} [{dname}] n={len(sub)} enc {e.min():.0f}..{e.max():.0f}: vertex {fit.e0:.0f} "
                  f"(95% CI {lo:.0f}..{hi:.0f}), w0 {fit.w0:.2f} px, +{RISE_PX} px at +-{dep:.0f} c, "
                  f"both flanks {'yes' if brk else 'NO'}, resid sd {fit.resid_sd:.2f} px")
            got[dname] = fit
            fits[(run, dname)] = (e, w, fit, dep)
        if "up" in got and "down" in got:
            print(f"     lash (up - down vertex): {got['up'].e0 - got['down'].e0:+.0f} counts")
    return fits


# ---------------------------------------------------------------------------------------------- [bootstrap]

def _pick_near(e, w, target, half, rng, n):
    m = np.abs(e - target) <= half
    if not m.any():
        return np.zeros(0), np.zeros(0)
    idx = rng.choice(np.flatnonzero(m), n, replace=True)
    return e[idx], w[idx]


def emulate(e, w, truth, design, budget, rng, dep, n_rep=150):
    """rms and p90 vertex error (counts) of a sampling design with `budget` frames, drawn from a recorded sweep."""
    lo, hi = e.min(), e.max(); errs = []
    for _ in range(n_rep):
        if design == "dwell":            # K stations x N frames, N=5
            N = 5; K = max(4, budget // N); half = (hi - lo) / K / 2
            cen = np.linspace(lo + half, hi - half, K)
            parts = [_pick_near(e, w, c, half, rng, N) for c in cen]
        elif design == "creep":          # one frame per station, budget stations
            K = budget; half = (hi - lo) / K / 2
            cen = np.linspace(lo + half, hi - half, K)
            parts = [_pick_near(e, w, c, half, rng, 1) for c in cen]
        else:                            # two-pass: coarse K1 x 1 to find the min, fine budget-K1 within +-1.5 depth
            K1 = max(5, budget // 3); half = (hi - lo) / K1 / 2
            cen = np.linspace(lo + half, hi - half, K1)
            parts = [_pick_near(e, w, c, half, rng, 1) for c in cen]
            ee = np.concatenate([p[0] for p in parts]); ww = np.concatenate([p[1] for p in parts])
            if len(ee) < 5:
                continue
            f0 = fit_v(ee, ww); centre = f0.e0 if f0 is not None else ee[np.argmin(ww)]
            K2 = max(3, budget - K1); span = 1.5 * dep
            cen2 = np.linspace(max(lo, centre - span), min(hi, centre + span), K2); half2 = span / K2
            parts += [_pick_near(e, w, c, max(half2, 15.0), rng, 1) for c in cen2]
        ee = np.concatenate([p[0] for p in parts]); ww = np.concatenate([p[1] for p in parts])
        if len(ee) < 5:
            continue
        f = fit_v(ee, ww)
        errs.append(abs(f.e0 - truth) if f is not None else (hi - lo))
    errs = np.array(errs)
    return (float(np.sqrt(np.mean(errs ** 2))), float(np.percentile(errs, 90))) if len(errs) else (math.nan, math.nan)


def report_bootstrap(fits):
    print("\n=== [bootstrap] vertex error (counts) vs total frames, sampled from the recorded sweeps ===")
    rng = np.random.default_rng(0)
    budgets = (10, 15, 20, 30, 45, 60, 90)
    for (run, dname), (e, w, fit, dep) in fits.items():
        if not bracketed(e, w, fit, RISE_PX):
            continue
        print(f"  {run} [{dname}]  truth {fit.e0:.0f}, depth +-{dep:.0f} c, {len(e)} frames available")
        print(f"    {'frames':>6} | {'dwell 5/station':>18} | {'creep 1/step':>18} | {'two-pass':>18}   (rms / p90)")
        for b in budgets:
            cells = []
            for d in ("dwell", "creep", "twopass"):
                rms, p90 = emulate(e, w, fit.e0, d, b, rng, dep)
                cells.append(f"{rms:7.0f} / {p90:6.0f}")
            print(f"    {b:>6} | " + " | ".join(f"{c:>18}" for c in cells))


# ---------------------------------------------------------------------------------------------- [sequential]

def report_sequential(fits, tol_frac):
    print(f"\n=== [sequential] replay the stopping rule (both flanks + CI <= {tol_frac:.2f} x depth) ===")
    for (run, dname), (e, w, fit, dep) in fits.items():
        if not bracketed(e, w, fit, RISE_PX):
            continue
        tol = tol_frac * dep
        stop = None
        for k in range(8, len(e) + 1):
            f = fit_v(e[:k], w[:k])
            if f is None or not bracketed(e[:k], w[:k], f, RISE_PX):
                continue
            lo, hi = bootstrap_vertex(e[:k], w[:k], n_boot=80, seed=k)
            if (hi - lo) / 2 <= tol:
                stop = (k, f.e0, (hi - lo) / 2); break
        if stop is None:
            print(f"  {run} [{dname}]: never met tol +-{tol:.0f} c within {len(e)} frames (full-data CI "
                  f"+-{(lambda lh: (lh[1] - lh[0]) / 2)(bootstrap_vertex(e, w, 200, 1)):.0f})")
            continue
        k, e0, ci = stop
        print(f"  {run} [{dname}]: stopped after {k} gated frames ({k * FRAME_S:.0f} s at full rate), vertex {e0:.0f} "
              f"+-{ci:.0f} vs full-data {fit.e0:.0f} (err {e0 - fit.e0:+.0f} c, tol +-{tol:.0f}, depth +-{dep:.0f})")


# ---------------------------------------------------------------------------------------------- main

def main(argv):
    if "--self-check" in argv:
        return self_check()
    args = list(argv); fixed = []; tol_frac = 0.33
    while "--fixed" in args:
        i = args.index("--fixed"); fixed.append(args[i + 1]); del args[i:i + 2]
    if "--tol-frac" in args:
        i = args.index("--tol-frac"); tol_frac = float(args[i + 1]); del args[i:i + 2]
    sweep_rows, fixed_rows = [], []
    for p in args:
        rows, total, dropped = load(p); sweep_rows += rows
        print(f"{os.path.basename(p)}: {len(rows)}/{total} frames pass the gates; dropped {dropped}")
    for p in fixed:
        rows, total, dropped = load(p); fixed_rows += rows
        print(f"{os.path.basename(p)} [fixed focus]: {len(rows)}/{total} frames pass; dropped {dropped}")
    # production runs inside the sweep CSVs are fixed-focus too: an encoder that never moves
    for run, rs in by_run(sweep_rows).items():
        encs = {r["enc"] for r in rs if math.isfinite(r["enc"])}
        if len(encs) <= 2 and len(rs) >= 30:
            fixed_rows += rs
    sweep_rows = [r for r in sweep_rows if r["run"].startswith("FOCUS")]
    report_noise(sweep_rows, fixed_rows)
    fits = report_vcurve(sweep_rows, tol_frac)
    report_bootstrap(fits)
    report_sequential(fits, tol_frac)
    return 0


def self_check():
    """A synthetic V with 0.6 px frame noise, per-station content bias and a known vertex must come back: the fit,
    the bracket test, the direction split and the sequential rule all have to agree with the truth."""
    rng = np.random.default_rng(0)
    e0, w0 = 5000.0, 5.0
    c = (0.3 ** 2 + 2 * w0 * 0.3) / 400 ** 2     # +0.3 px at +-400 counts
    e = np.linspace(3000, 7000, 200)
    w = np.sqrt(w0 ** 2 + c * (e - e0) ** 2) + rng.normal(0, 0.6, e.size) + np.repeat(rng.normal(0, 0.4, 20), 10)
    fit = fit_v(e, w)
    assert fit is not None and abs(fit.e0 - e0) < 120, fit
    assert abs(depth_counts(fit, 0.3) - 400) < 80, depth_counts(fit, 0.3)
    assert bracketed(e, w, fit, 0.3)
    assert not bracketed(e[e < e0 + 100], w[e < e0 + 100], fit_v(e[e < e0 + 100], w[e < e0 + 100]) or fit, 0.3), \
        "one-sided data must not read as bracketed"
    lo, hi = bootstrap_vertex(e, w, 100, 0)
    # the frame bootstrap sees the frame noise, NOT the per-station content bias (the blocks above), so the
    # interval can sit beside the truth; what it must do is be narrow and land within the fit's own error
    assert hi - lo < 400 and abs((lo + hi) / 2 - fit.e0) < 60 and abs(fit.e0 - e0) < 150, (lo, hi, fit.e0)
    rows = [dict(enc=float(v), w10=1.0) for v in np.r_[np.arange(0, 500, 5), np.arange(500, 0, -5)]]
    d = split_direction(rows)
    assert abs(len(d["up"]) - 100) <= 4 and abs(len(d["down"]) - 100) <= 4, {k: len(v) for k, v in d.items()}
    rms, p90 = emulate(e, w, e0, "creep", 30, rng, 400)
    assert rms < 300, rms
    print("ok"); return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
