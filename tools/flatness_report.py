"""Why does one object score 1.6x another, and what would actually flatten it?

The observe plot is a bowl: an object scores highest near the frame edges and worst
crossing the middle, and it is the SAME shape for every object (r=+0.94 across ten of
them on the 2026-09-01 run). Three things can bend it, and the plot alone cannot tell
them apart:

  defocus     the field is curved, so at one parked position the centre and the edges
              sit at different defocus. The field map measures this per tile, and the
              answer is strongly parking-dependent -- a curved field only costs you a
              gradient if you are parked away from the middle of it.
  saturation  the middle of the frame is the brightest part, and a clipped pixel is
              flat, so the Laplacian has nothing left to measure there.
  content     the object genuinely changes as it crosses. Observe follows ONE object,
              which is exactly what makes this small.

Brightness itself is not a candidate. ``focus_measure_full_res`` divides by ``mean**2``
and every step before that is linear, so scaling a crop by k cancels exactly -- verified
here to 0.1% against 145 saved crops. A flat-field gain map on the pixels cannot move
this curve at all; saturation is the only way brightness gets in.

The three causes are collinear inside a single run: brightness, clipped fraction and
field position are all the same function of y. So no amount of re-analysis of one run
separates them. What separates them is a field map from the SAME session (defocus,
measured independently of content) and a second run at lower exposure (saturation,
moved while position is held). This tool reads all three and prints the split.

Run: py -3 tools/flatness_report.py OBSERVE.csv [MORE.csv ...]
                                    [--crops E:/Project/focus] [--fieldmap FM.csv]
     py -3 tools/flatness_report.py --selfcheck
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import statistics as st
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "host"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fieldmap_report as fm  # noqa: E402
import observe_report as obs  # noqa: E402

SAT_LEVEL = 0.98  # matches AutoFocusConfig.sat_level in host/capture/autofocus.py
FULL_SCALE = 16383.0  # BayerRG14
SUBSAMPLE = 4  # preview decimation: observe y is in coarse rows, field-map ROIs are full-res


def _interp(xs: list, ys: list, x: float) -> float:
    """Linear interpolation, clamped at both ends."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(len(xs) - 1):
        a, b = xs[i], xs[i + 1]
        if a <= x <= b:
            f = 0.0 if b == a else (x - a) / (b - a)
            return ys[i] * (1 - f) + ys[i + 1] * f
    return ys[-1]


def _corr(u: list, v: list) -> float:
    mu, mv = st.mean(u), st.mean(v)
    du, dv = [a - mu for a in u], [a - mv for a in v]
    d = (sum(a * a for a in du) * sum(a * a for a in dv)) ** 0.5
    return sum(a * b for a, b in zip(du, dv)) / d if d else float("nan")


def bowl(rows: list, nbins: int = 6) -> dict:
    """The shared score-vs-y curve, and how flat it is.

    Each object is normalised by its OWN mean before pooling. Objects sit on different
    ortho content and their absolute levels differ by more than the effect being
    measured, so pooling raw scores would report content as shape. What survives the
    normalisation is only what all objects have in common: field position.
    """
    per_oid = obs.by_oid(rows)
    norm = []
    for oid, rs in per_oid.items():
        good = [r for r in rs if r["score"] > 0]
        if len(good) < 3:
            continue
        m = st.mean([r["score"] for r in good])
        if m <= 0:
            continue
        for r in good:
            norm.append({"oid": oid, "y": r["y"], "rel": r["score"] / m,
                         "score": r["score"]})
    if len(norm) < nbins * 2:
        return {"n": len(norm), "bins": [], "ratio": float("nan")}

    # Equal-count bins: samples are not spread evenly over y (objects are born in the
    # leading band and die at the far edge), and equal-width bins would put two points
    # in a tail and call their mean a trend.
    norm.sort(key=lambda d: d["y"])
    per = max(1, len(norm) // nbins)
    bins = []
    for i in range(0, len(norm), per):
        chunk = norm[i:i + per]
        if len(chunk) < 3:
            if bins:  # fold a short tail into the last bin rather than reporting it
                bins[-1] = (bins[-1][0], bins[-1][1], bins[-1][2] + len(chunk))
            continue
        bins.append((st.median([c["y"] for c in chunk]),
                     st.median([c["rel"] for c in chunk]), len(chunk)))

    vals = [b[1] for b in bins]
    ratio = (max(vals) / min(vals)) if vals and min(vals) > 0 else float("nan")

    # How much of each object's own variation follows the shared curve. If the bowl were
    # noise this would scatter around zero; on 2026-09-01 the median was +0.94.
    ys, cs = [b[0] for b in bins], vals
    rs_list, swings = [], []
    for oid, rs in per_oid.items():
        good = [r for r in rs if r["score"] > 0]
        if len(good) < 4:
            continue
        mine = [r["score"] for r in good]
        pooled = [_interp(ys, cs, r["y"]) for r in good]
        if len(set(pooled)) > 1:
            rs_list.append(_corr(mine, pooled))
        swings.append(max(mine) / min(mine))

    # Scatter left AFTER the shared curve is divided out: the noise floor the bowl has
    # to be judged against. A bowl smaller than this is not a bowl.
    resid = []
    for oid, rs in per_oid.items():
        good = [r for r in rs if r["score"] > 0]
        if len(good) < 4:
            continue
        q = [r["score"] / _interp(ys, cs, r["y"]) for r in good]
        if len(q) > 2 and st.mean(q) > 0:
            resid.append(st.stdev(q) / st.mean(q))

    return {
        "n": len(norm), "n_obj": len(per_oid), "bins": bins, "ratio": ratio,
        "median_r": st.median(rs_list) if rs_list else float("nan"),
        "median_swing": st.median(swings) if swings else float("nan"),
        "resid_cv": st.median(resid) if resid else float("nan"),
    }


def _crop_key(name: str):
    """-> (oid, y, date, hhmmss) from an OID-named focus crop, or None."""
    m = re.match(r"OID(\d+)_y(\d+)_s(\d+).*?_(\d{8})_Project_focus_(\d{6})_", name)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), m.group(4), m.group(5))


def crop_stats(folder: str, csv_path: str, dur_s: float) -> list:
    """Brightness and clipped fraction per saved crop, for one run's crops only.

    The folder accumulates every run, so crops are filtered to the observe CSV's own
    date and a time window starting at its stamp. Mixing runs here would average an
    exposure change away -- the one thing the second run exists to show.
    """
    import cv2  # only needed with --crops; keeps the CSV-only path import-light
    import numpy as np

    stamp = re.search(r"_(\d{8})_(\d{6})\.csv$", os.path.basename(csv_path))
    if not stamp:
        return []
    date, start = stamp.group(1), stamp.group(2)
    t0 = int(start[:2]) * 3600 + int(start[2:4]) * 60 + int(start[4:])
    t1 = t0 + dur_s + 30  # slack: crops are written slightly behind the CSV row

    out = []
    for path in sorted(Path(folder).glob("OID*.tif")):
        k = _crop_key(path.name)
        if k is None or k[2] != date:
            continue
        hh = k[3]
        t = int(hh[:2]) * 3600 + int(hh[2:4]) * 60 + int(hh[4:])
        if not (t0 <= t <= t1):
            continue
        g = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if g is None or g.ndim != 2:
            continue
        gf = g.astype(np.float32)  # the TIF IS the green plane focus_measure_full_res scores
        out.append({
            "oid": k[0], "y": k[1],
            "mean": float(cv2.mean(gf)[0]),
            "sat": float((gf >= SAT_LEVEL * FULL_SCALE).mean()),
            "p999": float(np.percentile(gf, 99.9)),
            "px": gf,
        })
    return out


def sat_cost(crops: list, target_mean: float) -> tuple:
    """Forward model: what does clipping alone cost, on THIS run's own pixels?

    Takes the crops that clipped nothing, scales them up to the brightness the worst
    band actually reached, re-clips at full scale and re-scores. Any score drop is
    caused by clipping and nothing else, because scaling on its own cannot move a
    mean**2-normalised metric. This is the honest size of the saturation term -- on
    2026-09-01 it was 3%, against a 28% bowl.
    """
    import cv2
    import numpy as np

    def score(a):
        lap = cv2.Laplacian(cv2.GaussianBlur(a, (0, 0), 1.0), cv2.CV_32F, ksize=3)
        _, sd = cv2.meanStdDev(lap)
        m = float(cv2.mean(a)[0])
        return float(sd[0][0]) ** 2 / (m * m + 1.0) * 1e4

    clean = [c for c in crops if c["sat"] == 0.0]
    if not clean:
        return float("nan"), 0, float("nan")
    base_mean = st.mean([c["mean"] for c in clean])
    if base_mean <= 0:
        return float("nan"), len(clean), float("nan")
    k = target_mean / base_mean
    ratios = []
    for c in clean:
        b = score(c["px"])
        if b > 0:
            ratios.append(score(np.minimum(c["px"] * k, FULL_SCALE)) / b)
    return (st.mean(ratios) if ratios else float("nan")), len(clean), k


def fm_predict(path: str, park_raw: float, window: int = 5) -> dict:
    """What bowl would the measured field curvature alone produce at this parking?

    Each tile is normalised by its own peak first, which divides content out: the tiles
    sit on different ortho and their absolute levels are scene, not field. What is left
    is the pure defocus penalty, read off the tile's own measured curve with no
    assumption about hump shape.
    """
    pos, tiles = fm.read_tiles(path, window)
    terms = fm.field_terms(tiles)

    # raw_counts, not pos_counts: pos is relative to wherever the sweep started, so it
    # is meaningless across sessions. raw is the encoder's own reading -- still only
    # comparable if the axis was not re-homed in between, which is why the field map
    # wants to be from the same session as the run.
    rows = list(csv.reader(open(path, encoding="utf-8")))
    raw = [float(r[2]) for r in rows[2:]]
    order = sorted(range(len(raw)), key=lambda i: raw[i])
    raw_sorted = [raw[i] for i in order]

    norm = {}
    for name, info in tiles.items():
        s = info["scores"]
        peak = max(s) or 1.0
        norm[name] = _interp(raw_sorted, [s[i] / peak for i in order], park_raw)

    rois = {}
    for cell in rows[0][1:]:
        nm, _, coords = cell.partition("=")
        nums = [int(v) for v in coords.strip("() ").split(",")]
        rois[nm.strip()] = nums

    edges = [norm[t] for t in ("top", "bottom", "left", "right") if t in norm]
    vert = [norm[t] for t in ("top", "bottom") if t in norm]
    return {
        "terms": terms, "norm": norm, "rois": rois,
        "raw_span": (min(raw), max(raw)),
        "in_span": min(raw) <= park_raw <= max(raw),
        "centre_over_edges": (norm["centre"] / st.mean(edges))
        if edges and "centre" in norm and st.mean(edges) else float("nan"),
        "centre_over_vert": (norm["centre"] / st.mean(vert))
        if vert and "centre" in norm and st.mean(vert) else float("nan"),
    }


def report(csv_path: str, crops_dir: str | None, fmap: str | None,
           nbins: int) -> None:
    rows, meta = obs.read(csv_path)
    if not rows:
        print(f"\n{os.path.basename(csv_path)}: no rows")
        return
    dur = max(r["t"] for r in rows)
    parks = sorted({round(r["raw"], 1) for r in rows})
    b = bowl(rows, nbins)

    print("\n" + "=" * 78)
    print(f"{os.path.basename(csv_path)}   {len(rows)} rows, {b.get('n_obj', 0)} objects,"
          f" {dur:.0f}s, scroll {obs.direction(rows)}")
    print(f"  parked at raw {parks[0]:+.0f}c"
          + ("" if len(parks) == 1 else f" .. {parks[-1]:+.0f}c ({len(parks)} values!)")
          + f"   meta: {', '.join(f'{k}={v}' for k, v in meta.items())}")

    if not b["bins"]:
        print(f"  too few samples to bin ({b['n']})")
        return

    print("\n  MEASURED bowl (each object normalised by its own mean, so this is the"
          " shape they share)")
    print("      y      rel score    n")
    for y, v, n in b["bins"]:
        bar = "#" * int(round(v * 30))
        print(f"   {y:6.0f}   {v:8.3f}  {n:4d}  {bar}")
    print(f"  bowl {b['ratio']:.3f}x  (max/min bin)"
          f"   per-object r vs shared curve {b['median_r']:+.3f}"
          f"   within-object swing {b['median_swing']:.2f}x")
    print(f"  residual CV after dividing the shared curve out: {b['resid_cv'] * 100:.1f}%"
          "  <- the floor the bowl must beat to be real")

    sat_term = float("nan")
    if crops_dir:
        cs = crop_stats(crops_dir, csv_path, dur)
        if not cs:
            print(f"\n  no crops in {crops_dir} matching this run's stamp")
        else:
            print(f"\n  CROPS ({len(cs)} matched) -- brightness and clipping by y")
            print("      y       mean   sat_frac    p99.9")
            cs.sort(key=lambda d: d["y"])
            per = max(1, len(cs) // nbins)
            worst = 0.0
            for i in range(0, len(cs), per):
                ch = cs[i:i + per]
                if len(ch) < 3:
                    continue
                mn = st.mean([c["mean"] for c in ch])
                worst = max(worst, mn)
                print(f"   {st.median([c['y'] for c in ch]):6.0f}   {mn:6.0f}"
                      f"   {st.mean([c['sat'] for c in ch]):8.4f}"
                      f"   {st.mean([c['p999'] for c in ch]):6.0f}")
            n_sat = sum(1 for c in cs if c["sat"] > 0)
            print(f"  {n_sat}/{len(cs)} crops clip at least one pixel"
                  f"   (rail = {SAT_LEVEL * FULL_SCALE:.0f} of {FULL_SCALE:.0f})")
            ratio, n_clean, k = sat_cost(cs, worst)
            if n_clean:
                sat_term = ratio
                print(f"  forward model: {n_clean} zero-clip crops scaled x{k:.2f} to the"
                      f" brightest band and re-clipped -> score x{ratio:.3f}")
                print(f"  => SATURATION explains {(1 - ratio) * 100:.1f}% of a"
                      f" {(1 - 1 / b['ratio']) * 100:.1f}% bowl")
            else:
                print("  forward model: no zero-clip crops, cannot size saturation"
                      " (drop exposure and re-run)")

    if fmap:
        p = fm_predict(fmap, parks[0])
        t = p["terms"]
        print(f"\n  FIELD MAP {os.path.basename(fmap)}")
        if t:
            print(f"   tiltV {t['tiltV']:+.0f}c  curvV {t['curvV']:+.0f}c"
                  f"   tiltH {t['tiltH']:+.0f}c  curvH {t['curvH']:+.0f}c"
                  f"   spread {t['spread']:.0f}c")
            print("   tilt = rig not square (bench only);  curv = field curvature"
                  " (follows to flight)")
        if not p["in_span"]:
            print(f"   !! run parked at raw {parks[0]:+.0f}c, OUTSIDE the sweep"
                  f" [{p['raw_span'][0]:+.0f}, {p['raw_span'][1]:+.0f}]c."
                  " Prediction is clamped and not trustworthy.")
        print("   each tile as a fraction of its OWN peak, at this run's parking:")
        for k2 in ("top", "centre", "bottom", "left", "right"):
            if k2 in p["norm"]:
                r = p["rois"].get(k2)
                yc = (0.5 * (r[1] + r[3]) / SUBSAMPLE) if r else float("nan")
                print(f"     {k2:<7} y~{yc:6.0f}  {p['norm'][k2]:.3f}")
        print(f"  => DEFOCUS alone predicts centre/edges {p['centre_over_edges']:.3f}"
              f"  (vertical axis only: {p['centre_over_vert']:.3f})")

        meas = 1.0 / b["ratio"] if b["ratio"] else float("nan")
        pred = p["centre_over_vert"]
        if pred == pred and meas == meas:
            print(f"\n  DECOMPOSITION   measured centre/edge {meas:.3f}"
                  f"   defocus {pred:.3f}"
                  + (f"   saturation {sat_term:.3f}" if sat_term == sat_term else "")
                  )
            expl = pred * (sat_term if sat_term == sat_term else 1.0)
            print(f"                  defocus x saturation = {expl:.3f}"
                  f"   residual {meas / expl if expl else float('nan'):.3f}")
            print("                  residual near 1.0 = fully explained;"
                  " below 1.0 = a fourth mechanism")


def selfcheck() -> int:
    """Asserts on synthetic data: the numbers this tool prints are easy to get subtly
    wrong, and the whole plan's acceptance test reads them."""
    def mk(bowl_depth, n_obj=6, span=2000, noise=0.0):
        """Objects crossing a frame whose response dips to `bowl_depth` at mid-y."""
        rows, frame = [], 0
        for oid in range(n_obj):
            level = 100.0 * (1 + oid)          # wildly different content per object
            for step in range(12):
                y = span * step / 11.0
                # parabola: 1.0 at both edges, bowl_depth at the centre
                u = (y - span / 2) / (span / 2)
                shape = bowl_depth + (1 - bowl_depth) * u * u
                rows.append({
                    "frame": frame, "t": frame * 0.2, "pos": 0.0, "raw": 2000.0,
                    "oid": oid, "born": 0, "age": step, "y": y, "x": 0.0,
                    "score": level * shape * (1 + noise * ((step % 3) - 1)),
                    "resp": 0.9,
                })
                frame += 1
        return rows

    # Equal-count bins attenuate the extremes on purpose -- the edge bin averages the
    # y=0 sample with the next one up -- so a true 1/0.70 = 1.43 bowl reads ~1.33. What
    # has to hold is that the number tracks the depth, not that it equals it.
    b = bowl(mk(0.70), nbins=6)
    assert 1.25 < b["ratio"] < 1.45, b["ratio"]
    assert b["median_r"] > 0.9, b["median_r"]          # every object shares the shape
    assert b["median_swing"] > 1.3, b["median_swing"]
    assert bowl(mk(0.85), nbins=6)["ratio"] < b["ratio"], "deeper bowl must read deeper"

    # resid_cv does NOT go to zero on noiseless data: the shared curve is 6 binned
    # medians joined by straight lines, and that cannot trace a parabola exactly, so
    # ~2.5% of shape leaks into the residual. Worth remembering when reading resid_cv
    # as a noise floor -- some of it is this tool's own resolution, not the rig's.
    assert b["resid_cv"] < 0.035, b["resid_cv"]
    noiseless = b["resid_cv"]

    flat = bowl(mk(1.0), nbins=6)
    assert abs(flat["ratio"] - 1.0) < 0.03, flat["ratio"]
    assert flat["resid_cv"] < 0.02, flat["resid_cv"]    # flat curve, no shape to leak

    # Content must not leak into the shape: per-object normalisation is the only thing
    # stopping a 6x spread in object level from being reported as a bowl.
    assert abs(bowl(mk(1.0, n_obj=12), nbins=6)["ratio"] - 1.0) < 0.03

    # Noise must land in resid_cv, not in the bowl, and clearly above the binning floor.
    noisy = bowl(mk(1.0, noise=0.10), nbins=6)
    assert noisy["resid_cv"] > 2 * noiseless, (noisy["resid_cv"], noiseless)
    assert abs(noisy["ratio"] - 1.0) < 0.12, noisy["ratio"]

    assert abs(_interp([0, 10], [1.0, 2.0], 5) - 1.5) < 1e-9
    assert _interp([0, 10], [1.0, 2.0], -5) == 1.0     # clamped, not extrapolated
    assert _interp([0, 10], [1.0, 2.0], 50) == 2.0

    assert _crop_key(
        "OID027_y0352_s1981_Cam2439008_20260907_Project_focus_102917_251.tif"
    ) == (27, 352, "20260907", "102917")
    assert _crop_key("Cam2439008_20260907_Project_focus_102814_198.tif") is None

    # A pure scale is invisible to the metric, so the forward model must read 1.000 at
    # k=1 -- if this ever fails, the metric stopped being mean**2-normalised.
    try:
        import cv2  # noqa: F401
        import numpy as np
        rng = np.random.default_rng(0)
        px = (rng.random((64, 64)).astype("float32") * 4000 + 1000)
        fake = [{"sat": 0.0, "mean": float(px.mean()), "px": px}]
        r, n, k = sat_cost(fake, float(px.mean()))
        assert n == 1 and abs(k - 1.0) < 1e-6 and abs(r - 1.0) < 1e-6, (r, n, k)
        r2, _, k2 = sat_cost(fake, float(px.mean()) * 4.0)   # forced hard into the rail
        assert k2 > 3.9 and r2 < 0.95, (r2, k2)
    except ImportError:
        print("  (cv2 absent: skipped the saturation forward-model checks)")

    print("flatness_report selfcheck: OK")
    return 0


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("csv", nargs="*", help="observe CSV(s); pass two to compare exposures")
    ap.add_argument("--crops", help="folder of saved focus crops (brightness + clipping)")
    ap.add_argument("--fieldmap", help="field-map CSV, ideally from the same session")
    ap.add_argument("--bins", type=int, default=6)
    ap.add_argument("--selfcheck", action="store_true")
    a = ap.parse_args(argv)

    if a.selfcheck:
        return selfcheck()
    if not a.csv:
        ap.error("give at least one observe CSV, or --selfcheck")
    for c in a.csv:
        report(c, a.crops, a.fieldmap, a.bins)
    if len(a.csv) > 1:
        print("\n" + "=" * 78)
        print("Compare the two bowls above. If only exposure changed between them, the")
        print("difference IS the saturation term, measured rather than modelled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
