"""Post-run report for an AF3 station walk.

Answers the three things a station walk can get wrong and the CSV can see:
does the lens actually dwell at distinct stations, does each station collect
whole traverses, and is D single-peaked across them (so the parabola fit has
something real to sit on).

Usage: py -3 tools/af3_run_report.py "<observe csv>"
"""
from __future__ import annotations

import csv
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from host.capture.autofocus_v3 import AutoFocusConfigV3, profile_metric

# ObserveLog writes boxes in the COARSE preview frame the tracker runs on
# (subsample=4 of the 13400x9528 sensor), so the span fractions the metric
# gates on must be measured against these dims, not the full-res ones.
FRAME_W, FRAME_H = 3350, 2382


def load(path: Path):
    hdr, rows = {}, []
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("#"):
                k, _, v = line[1:].strip().partition(",")
                hdr[k.strip()] = v.strip()
            else:
                break
        rows = list(csv.DictReader(fh, fieldnames=None) if False else [])
    # re-read the body with a DictReader positioned past the comments
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        body = [ln for ln in fh if not ln.startswith("#")]
    rows = list(csv.DictReader(body))
    return hdr, rows


def coerce(rows) -> None:
    """CSV strings -> numbers, in place."""
    for r in rows:
        for k in ("frame", "born_frame", "age_frames", "oid"):
            r[k] = int(float(r[k]))
        for k in ("t_s", "pos_counts", "raw_counts", "score", "x0", "y0", "x1", "y1"):
            r[k] = float(r[k])


def dwells(rows, tol: float = 40.0):
    """Split the run into lens dwells. A station holds the lens dead still, so a jump
    of more than ``tol`` counts is a step; anything less is encoder noise."""
    stations, cur = [], None
    for r in rows:
        p = r["raw_counts"]
        if cur is None or abs(p - cur["pos"]) > tol:
            cur = {"pos": p, "t0": r["t_s"], "t1": r["t_s"], "rows": 0,
                   "f0": r["frame"], "f1": r["frame"]}
            stations.append(cur)
        cur["pos"] = p
        cur["t1"] = r["t_s"]
        cur["f1"] = r["frame"]
        cur["rows"] += 1
    return stations


def by_object(rows):
    """rows grouped by object id, in frame order."""
    out = defaultdict(list)
    for r in rows:
        out[r["oid"]].append(r)
    return out


def travel_axis(rows) -> str:
    """Which image axis the scene scrolls along, from the objects' own travel."""
    by_oid = by_object(rows)
    sx = st.median([max(0.5 * (r["x0"] + r["x1"]) for r in tr)
                    - min(0.5 * (r["x0"] + r["x1"]) for r in tr)
                    for tr in by_oid.values()])
    sy = st.median([max(0.5 * (r["y0"] + r["y1"]) for r in tr)
                    - min(0.5 * (r["y0"] + r["y1"]) for r in tr)
                    for tr in by_oid.values()])
    return "x" if sx > sy else "y"


def station_curve(rows, stations, axis: str, cfg=None):
    """Per-station ``{pos, n, d, iqr, u, s_ctr}``, recomputed with the ENGINE's own
    metric rather than anything this file invents -- so a metric change shows up here
    instead of being silently re-implemented. ``d``/``u``/``iqr`` are None where the
    station banked no usable traverse; ``s_ctr`` is the raw centre-band score, i.e.
    the thing plain hill-climbing would have peaked on."""
    cfg = cfg or AutoFocusConfigV3()
    by_oid = by_object(rows)
    ext = FRAME_W if axis == "x" else FRAME_H
    lo, hi = 0.5 * (1 - cfg.profile_centre_frac), 0.5 * (1 + cfg.profile_centre_frac)
    out = []
    for s in stations:
        ctr = [r["score"] for r in rows
               if s["f0"] <= r["frame"] <= s["f1"]
               and lo <= ((0.5 * (r["x0"] + r["x1"]) if axis == "x"
                           else 0.5 * (r["y0"] + r["y1"])) / ext) <= hi]
        ds, us = [], []
        for tr in by_oid.values():
            seg = [r for r in tr if s["f0"] <= r["frame"] <= s["f1"]]
            if len(seg) < 4:
                continue
            got = profile_metric(
                [(0.5 * (r["x0"] + r["x1"]), 0.5 * (r["y0"] + r["y1"]), r["score"])
                 for r in seg], FRAME_W, FRAME_H, cfg)
            if got is None:
                continue
            d, u = got
            ds.append(d)
            if u is not None:
                us.append(u)
        out.append({
            "pos": s["pos"],
            "n": len(ds),
            "d": st.median(ds) if ds else None,
            "iqr": None if not ds else (
                (max(ds) - min(ds)) if len(ds) < 4
                else st.quantiles(ds, n=4)[2] - st.quantiles(ds, n=4)[0]),
            "u": st.median(us) if us else None,
            "s_ctr": st.median(ctr) if ctr else None,
        })
    return out


def main(argv: list[str]) -> int:
    path = Path(argv[1])
    hdr, rows = load(path)
    print(f"== {path.name}")
    for k, v in hdr.items():
        print(f"   {k}: {v}")

    coerce(rows)

    dur = rows[-1]["t_s"] - rows[0]["t_s"]
    frames = rows[-1]["frame"] - rows[0]["frame"] + 1
    print(f"\n== run: {frames} frames, {dur:.1f} s, "
          f"{frames / max(dur, 1e-9):.2f} fps, {len(rows)} object-rows")

    # -- stations: the lens holds still, so quantise raw_counts into dwells ------
    stations = dwells(rows)

    print(f"\n== {len(stations)} lens dwells (>40c apart)")
    print(f"{'#':>3} {'raw':>9} {'step':>7} {'t0':>7} {'dwell_s':>8} {'rows':>5}")
    prev = None
    for i, s in enumerate(stations):
        step = "" if prev is None else f"{s['pos'] - prev:+.0f}"
        print(f"{i:>3} {s['pos']:>9.0f} {step:>7} {s['t0']:>7.1f} "
              f"{s['t1'] - s['t0']:>8.1f} {s['rows']:>5}")
        prev = s["pos"]

    # -- object lifetimes: what a traverse actually gets you -------------------
    by_oid = by_object(rows)
    lives, spans_x, spans_y = [], [], []
    for oid, tr in sorted(by_oid.items()):
        lives.append(len(tr))
        xs = [0.5 * (r["x0"] + r["x1"]) for r in tr]
        ys = [0.5 * (r["y0"] + r["y1"]) for r in tr]
        spans_x.append(max(xs) - min(xs))
        spans_y.append(max(ys) - min(ys))
    print(f"\n== {len(by_oid)} objects")
    print(f"   life frames : median {st.median(lives):.0f}  "
          f"min {min(lives)}  max {max(lives)}")
    print(f"   x span px   : median {st.median(spans_x):.0f}  "
          f"max {max(spans_x):.0f}  (frame w {FRAME_W})")
    print(f"   y span px   : median {st.median(spans_y):.0f}  "
          f"max {max(spans_y):.0f}  (frame h {FRAME_H})")
    axis = "x" if st.median(spans_x) > st.median(spans_y) else "y"
    frac = (st.median(spans_x) / FRAME_W if axis == "x"
            else st.median(spans_y) / FRAME_H)
    print(f"   travel axis : {axis}, median traverse covers {frac:.0%} of the frame")

    # -- D per station, recomputed from the CSV with the engine's own metric ----
    cfg = AutoFocusConfigV3()
    print(f"\n== D per station (profile_min_span_frac={cfg.profile_min_span_frac})")
    print(f"{'#':>3} {'raw':>9} {'n_D':>4} {'D_med':>8} {'D_iqr':>8} {'u*_med':>8} "
          f"{'S_ctr':>9}")
    curve, scurve = [], []
    for i, c in enumerate(station_curve(rows, stations, axis, cfg)):
        stxt = "-" if c["s_ctr"] is None else f"{c['s_ctr']:9.0f}"
        if c["s_ctr"] is not None:
            scurve.append((c["pos"], c["s_ctr"]))
        if not c["n"]:
            print(f"{i:>3} {c['pos']:>9.0f} {0:>4}        -        -        - {stxt:>9}")
            continue
        curve.append((c["pos"], c["d"], c["n"]))
        umed = c["u"]
        print(f"{i:>3} {c['pos']:>9.0f} {c['n']:>4} {c['d']:>8.3f} {c['iqr']:>8.3f} "
              f"{('-' if umed is None else f'{umed:>8.3f}')} {stxt:>9}")

    if len(curve) >= 3:
        peak = max(curve, key=lambda c: c[1])
        print(f"\n   D peaks at raw {peak[0]:.0f} (D={peak[1]:.3f}, n={peak[2]})")
        rises = sum(1 for a, b in zip(curve, curve[1:]) if b[1] > a[1])
        print(f"   monotone segments: {rises} up / {len(curve) - 1 - rises} down"
              f"  -> {'single-peaked' if _single_peaked([c[1] for c in curve]) else 'NOT single-peaked'}")
        print(f"   parked (last pos_counts): {rows[-1]['pos_counts']:.0f}, "
              f"raw {rows[-1]['raw_counts']:.0f}")
    if len(scurve) >= 3:
        walk = [c for c in scurve if any(abs(c[0] - k[0]) < 1 for k in
                                         [(p, 0) for p, _, _ in curve])]
        peak = max(walk or scurve, key=lambda c: c[1])
        print(f"   raw centre score peaks at raw {peak[0]:.0f} "
              f"(score {peak[1]:.0f})")
        if curve:
            dpk = max(curve, key=lambda c: c[1])[0]
            print(f"   D vertex vs score peak: {dpk - peak[0]:+.0f} counts apart")
    return 0


def _single_peaked(ys: list[float]) -> bool:
    i = ys.index(max(ys))
    return (all(b >= a - 1e-9 for a, b in zip(ys[:i + 1], ys[1:i + 1]))
            and all(b <= a + 1e-9 for a, b in zip(ys[i:], ys[i + 1:])))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
