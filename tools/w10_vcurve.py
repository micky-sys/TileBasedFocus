"""Bench focus V-curve from a flight_edges.py CSV: median w10 per encoder park, best park, PSF sigma/FWHM.
Answers HANDOFF sec.52's question for a stationary rig: how sharp can THIS lens get (bench floor), against how sharp
the flight actually was. Reliability gate per sec.52.4: n_edges >= 300, encoder present and not stale.
    py w10_vcurve.py <bench.csv> [--bin 50] [--flight <flight_edges.csv>] [--png out.png]
    py w10_vcurve.py --self-check"""
import sys, csv, math, numpy as np

FLOOR, K = 1.75, 2.4          # sec.52.5: w10 ~= sqrt(FLOOR^2 + (K*sigma)^2), sigma in raw px
PITCH_UM = 3.45               # sec.28.1
MIN_EDGES = 300               # sec.52.4 frame-level gate
# sec.52.5 flight anchors (cam 2 = 2439008, 300 mm, 2026-09-30): best frames / typical at best focus. The 8-12 px
# "production" figure is deliberately NOT here: those runs were hand-focused under cloud and say nothing about lens or airframe.
ANCHORS = {"flight best frames": 4.5, "flight typical @ best focus": 5.75}


def sigma_fwhm(w10):
    """(sigma px, FWHM px, FWHM um); sigma 0 when w10 is at or below the estimator floor."""
    s = math.sqrt(max(w10 ** 2 - FLOOR ** 2, 0.0)) / K
    return s, 2.355 * s, 2.355 * s * PITCH_UM


def load(path):
    """Reliable frames as (encoder, w10, n_edges, [r0..r4 w10], src). Drops low-edge, stale-encoder and failed rows."""
    rows = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                w, n = float(r["w10"]), int(float(r["n_edges"]))
                e = float(r.get("encoder") or "nan")
            except (ValueError, KeyError):
                continue
            if not math.isfinite(w) or n < MIN_EDGES or r.get("enc_stale") == "1":
                continue
            per_roi = [float(r.get(f"r{k}_w10") or "nan") for k in range(5)]
            rows.append((e, w, n, per_roi, r.get("src", "")))
    return rows


def split_by_direction(rows):
    """{'down': rows, 'up': rows} by the sign of the encoder's motion around each frame (file order = time order).
    The encoder sits on the motor side of ~1000+ counts of lash (HANDOFF: 'measured ~1290-count lash'), so the same
    reading is a different lens position depending on the approach direction. Frames where it is not moving are
    assigned to the last known direction."""
    e = np.array([r[0] for r in rows]); out = {"down": [], "up": []}; cur = None
    for i, r in enumerate(rows):
        lo, hi = max(i - 2, 0), min(i + 2, len(rows) - 1)
        d = e[hi] - e[lo]
        if abs(d) > 1 and math.isfinite(d): cur = "up" if d > 0 else "down"
        if cur: out[cur].append(r)
    return out


def field_split(rows):
    """Median w10 per ROI over the sharpest decile of frames -- the field map at best focus, content-independent."""
    cut = np.percentile([w for _, w, _, _, _ in rows], 10)
    best = [pr for _, w, _, pr, _ in rows if w <= cut]
    return [float(np.nanmedian([pr[k] for pr in best])) if sum(pr[k] == pr[k] for pr in best) >= 3 else float("nan") for k in range(5)]


def report(rows, name, bin_counts):
    """Print one dataset. Returns (headline w10, curve-or-None): best park median if an encoder is present, else frame p10."""
    srcs = sorted({s for *_, s in rows if s})
    print(f"== {name}: {len(rows)} reliable frames" + (f"  [src: {', '.join(srcs)}]" if srcs else ""))
    curve = vcurve(rows, bin_counts)
    if curve:
        print(f"{'encoder':>9} {'frames':>6} {'w10 med':>8} {'w10 min':>8} {'edges':>6}")
        for p, (nf, med, mn, ne) in curve.items():
            print(f"{p:>9.0f} {nf:>6} {med:>8.2f} {mn:>8.2f} {ne:>6.0f}")
        best_p, (nf, head, mn, _) = min(curve.items(), key=lambda kv: kv[1][1])
        print(f"best park {best_p:.0f}: median w10 {head:.2f} px over {nf} frames (min {mn:.2f})")
        if nf < 5:
            print("  (fewer than 5 frames at the best park -- sec.52.5: one frame is not a focus reading, dwell ~13)")
    else:
        w = np.array([w for _, w, _, _, _ in rows]); head = float(np.percentile(w, 10))
        print(f"no encoder -> distribution: p10 {head:.2f}  median {np.median(w):.2f}  p90 {np.percentile(w, 90):.2f}  min {w.min():.2f}")
        print("  (headline = p10, the sharpest decile of frames; without an encoder this stands in for 'at best focus')")
    s, fw, fwum = sigma_fwhm(head)
    print(f"  -> PSF sigma {s:.2f} px, FWHM {fw:.1f} px = {fwum:.1f} um   (estimator floor w10={FLOOR})")
    fs = field_split(rows)
    print("  field at best focus, sensor ROI 0..4 = top-L, top-R, centre, bottom-L, bottom-R (optics invert: sensor top = scene bottom): "
          + "  ".join(f"{v:.2f}" if v == v else "  - " for v in fs))
    return head, curve, fs


def vcurve(rows, bin_counts):
    """{park_count: (n_frames, median w10, min w10, median n_edges)} over frames with a finite encoder."""
    parks = {}
    for e, w, n, _, _ in rows:
        if math.isfinite(e):
            parks.setdefault(round(e / bin_counts) * bin_counts, []).append((w, n))
    return {p: (len(v), float(np.median([w for w, _ in v])), min(w for w, _ in v), float(np.median([n for _, n in v])))
            for p, v in sorted(parks.items())}


def main(argv):
    if "--self-check" in argv:
        return self_check()
    args = list(argv); opt = {"--bin": "50", "--flight": None, "--png": None, "--dir": None}
    for flag in opt:
        if flag in args:
            i = args.index(flag); opt[flag] = args[i + 1]; del args[i:i + 2]
    bin_counts, flight, png, direction = int(opt["--bin"]), opt["--flight"], opt["--png"], opt["--dir"]
    rows = load(args[0])
    if not rows:
        print("no reliable frames (need w10 finite, n_edges >= %d)" % MIN_EDGES); return 1
    by_dir = split_by_direction(rows)
    if by_dir["up"] and by_dir["down"]:
        bests = {d: min(vcurve(v, bin_counts).items(), key=lambda kv: kv[1][1]) for d, v in by_dir.items() if vcurve(v, bin_counts)}
        if len(bests) == 2:
            lash = bests["up"][0] - bests["down"][0]
            print(f"sweep went both ways: best park going down {bests['down'][0]:.0f} ({bests['down'][1][1]:.2f}), going up "
                  f"{bests['up'][0]:.0f} ({bests['up'][1][1]:.2f}) -> {abs(lash):.0f} counts of lash between directions.")
            if not direction:
                direction = "down" if len(by_dir["down"]) >= len(by_dir["up"]) else "up"
                print(f"  using --dir {direction} ({len(by_dir[direction])} of {len(rows)} frames); pass --dir up|down to choose.")
    if direction:
        rows = by_dir[direction]
    best, curve, bench_fs = report(rows, args[0] + (f" [{direction}]" if direction else ""), bin_counts)
    print("\nflight reference, sec.52.5 (w10 px):")
    for k, v in ANCHORS.items():
        print(f"  {k:<30} {v:>5.2f}   bench - flight = {best - v:+.2f}")
    if flight:
        frows = load(flight)
        if frows:
            print()
            fhead, _, ffs = report(frows, flight, bin_counts)
            print(f"\nbench {best:.2f} - flight {fhead:.2f} = {best - fhead:+.2f} px"
                  f"   (in quadrature the flight adds {math.sqrt(max(fhead ** 2 - best ** 2, 0)):.2f} px of w10)")
            print("field, flight - bench per ROI: " + "  ".join(f"{f - b:+.2f}" if f == f and b == b else "  - " for f, b in zip(ffs, bench_fs)))
        else:
            print(f"\n{flight}: no reliable frames")
    print("\nread: bench ~= flight  -> the lens can do it; whatever the flight adds is flight-side (vibration doubles sec.51.5, "
          "focus wander at a parked encoder sec.51.3, lash, air).\n      bench << flight -> the flight is losing focus; bench >> 4.5 -> the "
          "optics (or the bench path) are the floor.\n      A per-ROI field difference that is the same on both = lens; one only in flight = airframe.")
    if png and curve:
        best_p = min(curve.items(), key=lambda kv: kv[1][1])[0]
        plot(curve, best_p, png, sweep_median=float(np.median([w for _, w, _, _, _ in rows])))
    return 0


def plot(curve, best_p, png, sweep_median=None):
    try:
        import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed; no plot"); return
    xs = list(curve); med = [v[1] for v in curve.values()]; mn = [v[2] for v in curve.values()]
    lowest = curve[best_p][1]
    plt.figure(figsize=(7, 4)); plt.plot(xs, med, "o-", label="median w10 per park"); plt.plot(xs, mn, ".", alpha=0.5, label="min w10 per park")
    for k, v in ANCHORS.items(): plt.axhline(v, ls="--", lw=0.8, alpha=0.6, label=k)
    # the sweep's own summary: lowest score (best park median) and the median over every frame of the sweep
    plt.axhline(lowest, color="g", lw=1.0, label=f"lowest score {lowest:.2f} px @ {best_p:.0f}")
    if sweep_median is not None:
        plt.axhline(sweep_median, color="C1", ls=":", lw=1.0, label=f"sweep median {sweep_median:.2f} px")
    plt.axvline(best_p, color="k", lw=0.6)
    plt.annotate(f"{best_p:.0f}", (best_p, lowest), textcoords="offset points", xytext=(4, -12), fontsize=8)
    plt.xlabel("encoder count"); plt.ylabel("w10 (px)"); plt.legend(fontsize=8); plt.grid(alpha=0.3)
    plt.tight_layout(); plt.savefig(png, dpi=120); print("wrote", png)


def self_check():
    """Synthetic V: parks 1000..3000 step 200, w10 = sqrt(4.4^2 + ((e-2000)/300)^2) + noise; a stale and a sparse frame must drop."""
    import tempfile, os
    rng = np.random.default_rng(0); rows = []
    def row(w, e, **kw):
        r = dict(file="f", src="bay14line", n_edges=800, w10=w, encoder=e, enc_stale=0, r0_w10=w + 0.8, r1_w10=w, r2_w10=w, r3_w10="", r4_w10=w)
        r.update(kw); return r                                                    # left field 0.8 px softer, ROI3 empty
    for e in range(1000, 3001, 200):
        for _ in range(8):
            rows.append(row(math.hypot(4.4, (e - 2000) / 300) + rng.normal(0, 0.05), e + rng.normal(0, 4)))
    rows.append(row(1.0, 1000, file="stale", enc_stale=1))                        # would win if not dropped
    rows.append(row(1.0, 1000, file="sparse", n_edges=50))                        # likewise
    # a "flight" CSV with no encoder: 100 frames, w10 = 6 +- 1 with a 5.0 floor; its headline must be the p10
    flight = [row(max(5.0, rng.normal(6.5, 1.0)), "", file="fl", src="rgb:BayerBG2RGB:unflipped") for _ in range(100)]
    paths = []
    for data in (rows, flight):
        fd, p = tempfile.mkstemp(suffix=".csv"); os.close(fd); paths.append(p)
        with open(p, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=data[0].keys()); w.writeheader(); w.writerows(data)
    try:
        curve = vcurve(load(paths[0]), 50)
        assert len(curve) == 11, curve
        best_p, (nf, best, _, _) = min(curve.items(), key=lambda kv: kv[1][1])
        assert best_p == 2000 and nf == 8 and abs(best - 4.4) < 0.1, (best_p, nf, best)
        s, fw, fwum = sigma_fwhm(best)
        assert abs(s - math.sqrt(4.4 ** 2 - FLOOR ** 2) / K) < 0.05 and abs(fwum - fw * PITCH_UM) < 1e-9
        assert sigma_fwhm(1.0) == (0.0, 0.0, 0.0)
        fs = field_split(load(paths[0]))
        assert abs(fs[0] - fs[1] - 0.8) < 0.1 and fs[3] != fs[3], fs             # asymmetry recovered, empty ROI stays NaN
        frows = load(paths[1]); assert not vcurve(frows, 50)                        # no encoder -> no curve
        # lash: a down pass with its bowl at 2000 followed by an up pass with its bowl at 3000 (encoder leads the lens)
        down = [(e, math.hypot(4.4, (e - 2000) / 300), 800, [math.nan] * 5, "") for e in range(4000, 999, -100)]
        up = [(e, math.hypot(4.4, (e - 3000) / 300), 800, [math.nan] * 5, "") for e in range(1000, 4001, 100)]
        bd = split_by_direction(down + up)                                          # the turnaround frame may go either way
        assert abs(len(bd["down"]) - 31) <= 1 and abs(len(bd["up"]) - 30) <= 1, {k: len(v) for k, v in bd.items()}
        pd = min(vcurve(bd["down"], 50).items(), key=lambda kv: kv[1][1])[0]; pu = min(vcurve(bd["up"], 50).items(), key=lambda kv: kv[1][1])[0]
        assert (pd, pu) == (2000, 3000), (pd, pu)
        p10 = np.percentile([w for _, w, _, _, _ in frows], 10); assert 5.0 <= p10 < 5.6, p10
        assert main([paths[0], "--flight", paths[1]]) == 0
    finally:
        for p in paths: os.remove(p)
    print("ok"); return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
