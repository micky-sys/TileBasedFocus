"""Which edge-width statistic locates focus best? For each candidate column: frame noise at a fixed encoder,
V-curve slope (so noise can be read in encoder counts), bootstrap vertex error for the creep design, and
content floor across different ground at fixed focus (June line). sec.54.11.

    py -3 tools/w10_pct_study.py pct_sweep.csv pct_fixed.csv"""
import csv, math, sys
import numpy as np
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "host"))
from capture.af_w10 import fit_v, depth_counts

STATS = ["w2", "w5", "w10", "w15", "w20", "w30", "w50", "k30", "k100", "k300"]
MIN_EDGES, CLOUD = 300, 22000.0


def load(path):
    rows = []
    for r in csv.DictReader(open(path)):
        try:
            n = float(r["n_edges"]); mm = float(r["med_mean"] or "nan")
        except ValueError:
            continue
        if n < MIN_EDGES or (math.isfinite(mm) and mm > CLOUD) or r.get("enc_stale") == "1":
            continue
        d = {k: float(r[k] or "nan") for k in STATS}
        d["enc"] = float(r["encoder"] or "nan"); d["ts"] = r["ts"]; d["n"] = n
        rows.append(d)
    rows.sort(key=lambda d: d["ts"])
    return rows


def robust_sd(x):
    x = np.asarray(x, float); x = x[np.isfinite(x)]
    return 1.4826 * np.median(np.abs(x - np.median(x))) if len(x) else float("nan")


def frame_sd(rows, k):
    d = [rows[i + 1][k] - rows[i][k] for i in range(len(rows) - 1)
         if rows[i]["enc"] == rows[i + 1]["enc"] and math.isfinite(rows[i][k]) and math.isfinite(rows[i + 1][k])]
    return robust_sd(d) / math.sqrt(2), len(d)


def creep_boot(e, w, n_frames, n_boot=300, seed=0):
    """Pick n_frames frames spread evenly over the encoder range (one per station, random frame at each),
    fit the V, vertex error vs the full-data fit."""
    rng = np.random.default_rng(seed)
    full = fit_v(e, w)
    if full is None:
        return float("nan"), float("nan")
    order = np.argsort(e); e_s, w_s = e[order], w[order]
    edges = np.linspace(e_s.min(), e_s.max(), n_frames + 1)
    errs = []
    for _ in range(n_boot):
        idx = []
        for a, b in zip(edges[:-1], edges[1:]):
            c = np.flatnonzero((e_s >= a) & (e_s <= b))
            if len(c):
                idx.append(rng.choice(c))
        f = fit_v(e_s[idx], w_s[idx])
        if f is not None and math.isfinite(f.e0):
            errs.append(f.e0 - full.e0)
    errs = np.asarray(errs)
    return (math.sqrt(np.mean(np.square(np.clip(errs, -3000, 3000)))), np.percentile(np.abs(errs), 90)) if len(errs) else (float("nan"), float("nan"))


def main(sweep_csv, fixed_csv):
    sw = load(sweep_csv); fx = load(fixed_csv)
    print(f"sweep {sweep_csv}: {len(sw)} gated frames; fixed {fixed_csv}: {len(fx)} gated frames\n")
    e_all = np.array([r["enc"] for r in sw])
    print(f"{'stat':>5} | {'value@focus':>11} | {'frame sd':>8} {'pairs':>5} | {'slope px/kc':>11} {'sd in counts':>12} | "
          f"{'creep20 rms/p90':>15} {'creep30 rms/p90':>15} | {'content floor':>13} {'floor in counts':>15}")
    for k in STATS:
        w = np.array([r[k] for r in sw]); m = np.isfinite(w) & np.isfinite(e_all)
        e, w = e_all[m], w[m]
        sd, npairs = frame_sd(sw, k)
        f = fit_v(e, w)
        if f is None:
            print(f"{k:>5} | no convex fit"); continue
        # slope of the statistic one cam-2 depth (300 c) from the vertex: d/de sqrt(w0^2 + c x^2) at x = 300
        x = 300.0; slope = f.c * x / math.sqrt(f.w0 ** 2 + f.c * x ** 2)    # px per count
        r20, p20 = creep_boot(e, w, 20); r30, p30 = creep_boot(e, w, 30)
        # content floor: sweep-median spread across different ground, lens fixed (June), minus the noise share
        fw = np.array([r[k] for r in fx]); fw = fw[np.isfinite(fw)]
        chunks = [fw[i:i + 10] for i in range(0, len(fw) - 9, 10)]
        med_sd = robust_sd([np.median(c) for c in chunks]) if len(chunks) >= 4 else float("nan")
        noise_share = robust_sd(fw) / math.sqrt(10)
        floor = math.sqrt(max(med_sd ** 2 - noise_share ** 2, 0.0)) if math.isfinite(med_sd) else float("nan")
        print(f"{k:>5} | {f.w0:>11.2f} | {sd:>8.3f} {npairs:>5d} | {slope * 1000:>11.2f} {sd / slope:>12.0f} | "
              f"{r20:>7.0f}/{p20:<7.0f} {r30:>7.0f}/{p30:<7.0f} | {floor:>13.3f} {floor / slope:>15.0f}")
    print("\nread: 'sd in counts' = frame noise / local slope = how far one frame's noise moves a single reading in encoder "
          "counts;\n      'creep' = bootstrap vertex error (counts) with N one-frame stations spread over the sweep (the engine's "
          "design);\n      'content floor' = spread of 10-frame medians across different ground at FIXED focus (June) with the noise "
          "share removed.")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
