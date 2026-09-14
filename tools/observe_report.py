"""Read Iron661_observe_*.csv and answer: why is a new patch worth 1.6x an old one?

Background. The cross-object stitch multiplies the climb curve by a ratio measured
between the outgoing and incoming patch while both are visible. That ratio has a median
of 0.625 over 131 live samples, so every seam shrinks the curve, and 20 seams shrank it
to 0.00024 -- the climb then "never rose" and the run was refused. Fixing the timing
made it worse, because more successful handovers meant more multiplications.

Three candidate causes, indistinguishable in an AF log:

  age       a patch is scored worse the longer it has been tracked (drift, blur)
  position  a patch is scored worse where it sits in the field (tilt, illumination)
  content   the incoming patch is genuinely sharper (it is picked as the best of
            `tiles_per_frame` candidates, and picked later, on a sharper image)

Age and position are the same number on a forward-scrolling scene: a patch travels at a
constant rate, so its age IS its height. Reversing the scroll (OrthoSim --dir down)
flips that correlation without touching anything else, which is what separates them --
so this tool wants a forward CSV and a reverse CSV, and reports what changes between.

Usage
    py -3 tools/observe_report.py FORWARD.csv [REVERSE.csv] [--focus COUNTS] [--png OUT]
    py -3 tools/observe_report.py --dir "C:\\Iron661Logs"      # list what is there
"""
from __future__ import annotations

import argparse
import csv
import glob
import math
import os
import statistics as st
import sys
from collections import defaultdict


def read(path: str) -> tuple:
    """-> (rows, meta). Rows are dicts with numbers already parsed."""
    meta, rows = {}, []
    with open(path, newline="", encoding="utf-8") as fh:
        for rec in csv.reader(fh):
            if not rec:
                continue
            if rec[0].startswith("# "):
                meta[rec[0][2:]] = rec[1] if len(rec) > 1 else ""
                continue
            if rec[0] == "frame":
                cols = rec
                continue
            d = dict(zip(cols, rec))
            rows.append({
                "frame": int(d["frame"]), "t": float(d["t_s"]),
                "pos": float(d["pos_counts"]), "raw": float(d["raw_counts"]),
                "oid": int(d["oid"]), "born": int(d["born_frame"]),
                "age": int(d["age_frames"]),
                "y": float(d["y_centre"]), "x": 0.5 * (float(d["x0"]) + float(d["x1"])),
                "score": float(d["score"]), "resp": float(d["track_response"]),
            })
    return rows, meta


def direction(rows: list) -> str:
    """Which way patches travel, read off the data rather than trusted from a flag.

    A mislabelled direction would invert the very comparison the report exists to make,
    and the rows already say it: track how each patch's y moves as it ages."""
    slopes = []
    for oid, rs in by_oid(rows).items():
        if len(rs) < 3:
            continue
        rs = sorted(rs, key=lambda r: r["age"])
        dy = rs[-1]["y"] - rs[0]["y"]
        da = rs[-1]["age"] - rs[0]["age"]
        if da:
            slopes.append(dy / da)
    if not slopes:
        return "unknown"
    m = st.median(slopes)
    if abs(m) < 0.5:
        return f"static ({m:+.2f} px/frame)"
    return f"{'down' if m > 0 else 'up'} ({m:+.1f} px/frame)"


def by_oid(rows: list) -> dict:
    out = defaultdict(list)
    for r in rows:
        out[r["oid"]].append(r)
    return out


def by_frame(rows: list) -> dict:
    out = defaultdict(list)
    for r in rows:
        out[r["frame"]].append(r)
    return out


def _fmt(vals: list, unit: str = "") -> str:
    if not vals:
        return "n/a"
    if len(vals) == 1:
        return f"{vals[0]:.3g}{unit}"
    return (f"median {st.median(vals):.3g}{unit}  "
            f"[{min(vals):.3g}, {max(vals):.3g}]  n={len(vals)}")


def pairs(rows: list) -> list:
    """Every co-visible pair, same frame: (dy, ratio new/old, pos, age_old, age_new).

    Same frame is the whole point. Two scores a frame apart differ by whatever happened
    between the frames, which is exactly what we are trying to attribute."""
    out = []
    for _f, rs in by_frame(rows).items():
        if len(rs) < 2:
            continue
        rs = sorted(rs, key=lambda r: r["oid"])          # oid increases with birth
        for i in range(len(rs)):
            for j in range(i + 1, len(rs)):
                old, new = rs[i], rs[j]
                if old["score"] <= 0:
                    continue
                out.append({
                    "dy": new["y"] - old["y"],
                    "ratio": new["score"] / old["score"],
                    "pos": old["pos"],
                    "age_old": old["age"], "age_new": new["age"],
                    "y_old": old["y"], "y_new": new["y"],
                })
    return out


def _bucket(items: list, key: str, val: str, nbins: int = 5) -> list:
    """-> [(centre, median value, n)]. Equal-count bins: the samples are not evenly
    spread over lens position, and equal-width bins would put one point in the tails
    and call its value a trend."""
    items = sorted(items, key=lambda d: d[key])
    if not items:
        return []
    n = max(1, len(items) // nbins)
    out = []
    for i in range(0, len(items), n):
        chunk = items[i:i + n]
        if len(chunk) < 3:
            continue
        out.append((st.median([c[key] for c in chunk]),
                    st.median([c[val] for c in chunk]), len(chunk)))
    return out


def report_one(path: str, focus: float | None) -> dict:
    rows, meta = read(path)
    print(f"\n=== {os.path.basename(path)} ===")
    if meta:
        print("    " + "  ".join(f"{k}={v}" for k, v in sorted(meta.items())))
    if not rows:
        print("    (no rows)")
        return {"rows": [], "path": path}

    oids = by_oid(rows)
    frames = by_frame(rows)
    covis = sum(1 for rs in frames.values() if len(rs) >= 2)
    span = max(r["pos"] for r in rows) - min(r["pos"] for r in rows)
    print(f"    {len(rows)} rows, {len(oids)} patches, {len(frames)} frames, "
          f"{covis} of them co-visible")
    print(f"    scroll: {direction(rows)}   lens travel: {span:.0f}c "
          f"({'FIXED focus' if span < 50 else 'a real run -- focus moved'})")

    lifetimes = [max(r['age'] for r in rs) for rs in oids.values()]
    print(f"    patch lifetime (frames): {_fmt(lifetimes)}")

    # 1. Does a patch decay as it ages?  Per-patch, so content cancels out.
    decay = []
    for rs in oids.values():
        rs = sorted(rs, key=lambda r: r["age"])
        if len(rs) < 6 or rs[0]["score"] <= 0:
            continue
        decay.append(rs[-1]["score"] / rs[0]["score"])
    print(f"    last/first score WITHIN one patch: {_fmt(decay, 'x')}")
    print("      (<1 => a tracked patch really does decay as it crosses the frame;")
    print("       ~1 => the new-over-old ratio is about WHICH patch, not about age)")

    # 2. The stitch ratio itself, and what it tracks.
    ps = pairs(rows)
    if ps:
        rat = [p["ratio"] for p in ps]
        print(f"    new/old ratio, same frame:  {_fmt(rat, 'x')}")
        print("      vs vertical separation dy (coarse px):")
        for c, v, n in _bucket(ps, "dy", "ratio"):
            print(f"        dy {c:+8.0f}   ratio {v:.3f}   n={n}")
        if span >= 50:
            print("      vs lens position (counts):")
            for c, v, n in _bucket(ps, "pos", "ratio"):
                print(f"        pos {c:+8.0f}c  ratio {v:.3f}   n={n}")
    else:
        print("    no co-visible pairs -- observe_objects was 1, or the log is an AF "
              "run with the stitch off")

    # 3. The nuisance function the stitch has to cancel: score vs where it sits.
    print("    score vs height in frame (all patches pooled):")
    for c, v, n in _bucket(rows, "y", "score", nbins=6):
        print(f"        y {c:7.0f}   score {v:9.3g}   n={n}")

    if focus is not None and span >= 50:
        near = [r for r in rows if abs(r["pos"] - focus) <= 150]
        print(f"    rows within 150c of true focus ({focus:+.0f}c): {len(near)}")
        if near:
            print(f"      their scores: {_fmt([r['score'] for r in near])}")

    return {"rows": rows, "path": path, "pairs": ps, "meta": meta}


def compare(a: dict, b: dict) -> None:
    """Forward vs reverse: the only comparison that separates age from position."""
    print("\n=== forward vs reverse ===")
    ra, rb = a["rows"], b["rows"]
    if not ra or not rb:
        print("    need rows in both")
        return
    print(f"    A {os.path.basename(a['path'])}: scroll {direction(ra)}")
    print(f"    B {os.path.basename(b['path'])}: scroll {direction(rb)}")

    pa, pb = [p["ratio"] for p in a["pairs"]], [p["ratio"] for p in b["pairs"]]
    if pa and pb:
        ma, mb = st.median(pa), st.median(pb)
        print(f"    new/old ratio:  A {ma:.3f}   B {mb:.3f}   "
              f"difference {mb - ma:+.3f}")
        print("      Reversing swaps which END of the frame an old patch sits at while")
        print("      leaving age alone. So:")
        print("        ratio roughly UNCHANGED  -> age or content drives it; a")
        print("                                    position-only correction cannot fix it")
        print("        ratio FLIPS about 1.0    -> pure field position; a y-indexed")
        print("                                    correction (or the field map) fixes it")
        print("        ratio moves PART WAY     -> both, and the split is the difference")
        if ma > 0 and mb > 0:
            geo = math.sqrt(ma * mb)
            print(f"      direction-independent part (geometric mean): {geo:.3f}")
            print(f"      position-dependent part: {math.sqrt(mb / ma):.3f} per direction")

    # Score vs y, both directions. If the field is what matters, these overlay.
    print("    score vs height, A then B (same y bins => field; offset => not):")
    for label, rows in (("A", ra), ("B", rb)):
        vals = _bucket(rows, "y", "score", nbins=5)
        norm = st.median([v for _c, v, _n in vals]) if vals else 1.0
        cells = "  ".join(f"y{c:.0f}:{v / norm:.2f}" for c, v, _n in vals)
        print(f"        {label}  {cells}")


def plot_frames(run: dict, focus: float | None, out: str) -> None:
    """Every patch's raw score against frame number, one line each.

    The picture the whole diagnostic was asked for. Each patch traces an arc as it
    crosses the sensor, and consecutive arcs overlap where a handover would happen --
    so the vertical gap between two arcs at one frame IS the stitch ratio, drawn.

    With the lens held still, a step between overlapping arcs cannot be focus; it is
    the patch. With the lens sweeping (an AF run), the frames where it crossed true
    focus are marked, so a peak that sits away from the mark is a peak in the wrong
    place."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import cm
    except Exception as exc:  # pragma: no cover
        print(f"(no plot: {exc})")
        return
    rows = run["rows"]
    if not rows:
        return
    oids = by_oid(rows)
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(15, 9), sharex=True,
                                  gridspec_kw={"height_ratios": [3, 1]})
    colours = cm.turbo([i / max(1, len(oids) - 1) for i in range(len(oids))])
    for c, (oid, rs) in zip(colours, sorted(oids.items())):
        rs = sorted(rs, key=lambda r: r["frame"])
        ax.plot([r["frame"] for r in rs], [r["score"] for r in rs],
                marker=".", ms=4, lw=1.4, color=c)
        ax.annotate(str(oid), (rs[-1]["frame"], rs[-1]["score"]), fontsize=6,
                    color=c, xytext=(2, 2), textcoords="offset points")
        ax2.plot([r["frame"] for r in rs], [r["y"] for r in rs], lw=1.2, color=c)

    span = max(r["pos"] for r in rows) - min(r["pos"] for r in rows)
    if focus is not None and span >= 50:
        prev = None
        for r in sorted(rows, key=lambda r: r["frame"]):
            if prev is not None and (prev - focus) * (r["pos"] - focus) <= 0:
                ax.axvline(r["frame"], color="k", ls="--", lw=1.2)
                ax.annotate("true focus", (r["frame"], ax.get_ylim()[1]), fontsize=8,
                            rotation=90, va="top", ha="right")
            prev = r["pos"]
    sub = (f"lens held still ({span:.0f}c of travel) -- every step between patches is "
           f"the patch, not the focus" if span < 50 else
           f"lens swept {span:.0f}c -- focus and patch both move")
    ax.set(ylabel="raw focus score (full-res, mean^2-normalised)",
           title=f"{os.path.basename(run['path'])}\n{len(oids)} patches, "
                 f"scroll {direction(rows)} -- {sub}")
    ax2.set(xlabel="frame", ylabel="patch y centre\n(coarse px)")
    ax2.invert_yaxis()
    for a in (ax, ax2):
        a.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    print(f"plot -> {out}")


def plot(runs: list, focus: float | None, out: str) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover
        print(f"(no plot: {exc})")
        return
    fig, axes = plt.subplots(2, 2, figsize=(15, 9))
    for run in runs:
        rows, tag = run["rows"], os.path.basename(run["path"])[-10:-4]
        if not rows:
            continue
        # score vs frame, one line per patch
        for oid, rs in sorted(by_oid(rows).items()):
            rs = sorted(rs, key=lambda r: r["frame"])
            axes[0][0].plot([r["frame"] for r in rs], [r["score"] for r in rs],
                            marker=".", lw=1, label=f"{tag} oid{oid}")
        axes[0][1].scatter([r["y"] for r in rows], [r["score"] for r in rows],
                           s=8, alpha=0.5, label=tag)
        ps = run.get("pairs") or []
        if ps:
            axes[1][0].scatter([p["dy"] for p in ps], [p["ratio"] for p in ps],
                               s=8, alpha=0.5, label=tag)
            axes[1][1].scatter([p["pos"] for p in ps], [p["ratio"] for p in ps],
                               s=8, alpha=0.5, label=tag)
    if focus is not None:
        axes[1][1].axvline(focus, color="k", ls="--", lw=1, label="true focus")
    axes[0][0].set(xlabel="frame", ylabel="raw score", title="score vs frame, per patch")
    axes[0][1].set(xlabel="patch y centre (coarse px)", ylabel="raw score",
                   title="score vs height -- the nuisance the stitch must cancel")
    axes[1][0].axhline(1.0, color="k", lw=0.8)
    axes[1][0].set(xlabel="dy new - old (coarse px)", ylabel="new/old",
                   title="stitch ratio vs separation")
    axes[1][1].axhline(1.0, color="k", lw=0.8)
    axes[1][1].set(xlabel="lens position (counts)", ylabel="new/old",
                   title="stitch ratio vs lens position")
    for ax in axes.ravel():
        if ax.get_legend_handles_labels()[0]:
            ax.legend(fontsize=6, ncol=2)
        ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    print(f"\nplot -> {out}")


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="*", help="observe CSVs; 2 = forward then reverse")
    ap.add_argument("--dir", help="list Iron661_observe_*.csv in this folder and exit")
    ap.add_argument("--focus", type=float, default=None,
                    help="true focus in encoder counts, from the frozen twins")
    ap.add_argument("--png", default=None, help="write the four plots here")
    a = ap.parse_args(argv)

    if a.dir:
        hits = sorted(glob.glob(os.path.join(a.dir, "Iron661_observe_*.csv")))
        for h in hits:
            rows, _m = read(h)
            print(f"{os.path.basename(h):40s} {len(rows):6d} rows  {direction(rows)}")
        if not hits:
            print(f"no observe CSVs in {a.dir}")
        return 0
    if not a.csv:
        ap.error("give at least one CSV, or --dir")

    runs = [report_one(p, a.focus) for p in a.csv]
    if len(runs) >= 2:
        compare(runs[0], runs[1])
    if a.png:
        plot(runs, a.focus, a.png)
        stem, ext = os.path.splitext(a.png)
        for i, run in enumerate(runs):
            suffix = "" if len(runs) == 1 else f"_{i}"
            plot_frames(run, a.focus, f"{stem}_frames{suffix}{ext}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
