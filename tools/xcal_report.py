"""Read af2-xcal lines from a capture console log and ask why the cross-cal ratio
sits below 1.

The ratio is primary/on-deck measured on the same frame. It should scatter around 1 if
the two patches were exchangeable; live it is below 1 nearly every time, and twenty of
those compounded collapse the climb curve.

Age and field position cannot be told apart by staring at them -- the patch moves at a
constant rate, so its age IS its height in the frame. What DOES separate the candidate
causes is where on the focus curve the measurement was taken:

  * A field-tilt cause (the two patches sit at heights whose best focus differs) hurts
    most on the steep flank far below focus and vanishes at the peak, because near the
    peak the curve is flat and a small focus offset costs almost no score. So r -> 1 as
    pos -> focus.
  * An age/content cause (drift, the on-deck being the winner of a best-of-five pick)
    is a property of the patches, not of the lens, so it is flat in pos.

Usage:  py -3 tools/xcal_report.py <console-log> [more logs...]
"""
from __future__ import annotations

import re
import sys
from statistics import median

LINE = re.compile(
    r"af2-xcal pos=([+-]?\d+)c agep=(\d+) aged=(\d+) "
    r"a=([\d.eE+-]+) b=([\d.eE+-]+) r=([\d.eE+-]+) "
    r"oidp=(\d+) oidd=(\d+) yp=(-?\d+) yd=(-?\d+)"
)


def parse(paths):
    rows = []
    for p in paths:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = LINE.search(line)
                if not m:
                    continue
                pos, agep, aged, a, b, r, oidp, oidd, yp, yd = m.groups()
                rows.append(dict(pos=int(pos), agep=int(agep), aged=int(aged),
                                 a=float(a), b=float(b), r=float(r),
                                 oidp=int(oidp), oidd=int(oidd),
                                 yp=int(yp), yd=int(yd)))
    return rows


def bin_by(rows, key, nbins):
    """Split rows into nbins equal-width bins over `key`, dropping empties."""
    vals = [r[key] for r in rows]
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return [((lo, hi), rows)]
    width = (hi - lo) / nbins
    out = []
    for i in range(nbins):
        a = lo + i * width
        b = hi if i == nbins - 1 else lo + (i + 1) * width
        grp = [r for r in rows if (a <= r[key] <= b if i == nbins - 1 else a <= r[key] < b)]
        if grp:
            out.append(((a, b), grp))
    return out


def slope(xs, ys):
    """Least-squares slope + intercept, no numpy needed."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return 0.0, my
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    m = sxy / sxx
    return m, my - m * mx


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    rows = parse(argv[1:])
    if not rows:
        print("no af2-xcal lines found -- is this a log from an instrumented run?")
        return 1

    rs = [r["r"] for r in rows]
    below = sum(1 for r in rs if r < 1.0)
    print(f"{len(rows)} overlap samples, {len({(r['oidp'], r['oidd']) for r in rows})} "
          f"windows")
    print(f"ratio: median={median(rs):.3f}  min={min(rs):.3f}  max={max(rs):.3f}  "
          f"below 1: {below}/{len(rs)}")
    print(f"compounded over the {len({r['oidp'] for r in rows})} primaries seen: "
          f"{median(rs) ** len({r['oidp'] for r in rows}):.2e}")

    print("\n-- ratio vs lens position (the discriminator) --")
    print("  a tilt cause fades to 1.0 near focus; an age/content cause stays flat")
    print(f"  {'pos range (counts)':>24}   {'n':>3}  {'median r':>8}  {'med agep':>8}")
    for (a, b), grp in bin_by(rows, "pos", 6):
        print(f"  {int(a):+8d} .. {int(b):+8d}   {len(grp):3d}  "
              f"{median([g['r'] for g in grp]):8.3f}  "
              f"{median([g['agep'] for g in grp]):8.1f}")

    xs = [r["pos"] for r in rows]
    ys = [r["r"] for r in rows]
    m, c = slope(xs, ys)
    span = max(xs) - min(xs)
    print(f"\n  linear fit: r = {c:.3f} {m:+.3e} * pos")
    print(f"  -> r at the dive floor ({min(xs):+d}c): {c + m * min(xs):.3f}")
    print(f"  -> r at the climb top  ({max(xs):+d}c): {c + m * max(xs):.3f}")
    print(f"  -> change across the {span}c climb: {m * span:+.3f}")

    print("\n-- ratio vs primary age (confounded with height, shown for completeness) --")
    print(f"  {'agep':>6}  {'n':>3}  {'median r':>8}")
    ages = sorted({r["agep"] for r in rows})
    for age in ages:
        grp = [r for r in rows if r["agep"] == age]
        print(f"  {age:6d}  {len(grp):3d}  {median([g['r'] for g in grp]):8.3f}")

    print("\n-- geometry of the comparison --")
    gaps = [r["yp"] - r["yd"] for r in rows]
    print(f"  primary height yp : {min(r['yp'] for r in rows)} .. "
          f"{max(r['yp'] for r in rows)} (median {median([r['yp'] for r in rows]):.0f})")
    print(f"  on-deck height yd : {min(r['yd'] for r in rows)} .. "
          f"{max(r['yd'] for r in rows)} (median {median([r['yd'] for r in rows]):.0f})")
    print(f"  gap yp-yd         : median {median(gaps):.0f} coarse px "
          f"(the primary sits ABOVE the on-deck by this much)")

    print("\n-- is the on-deck simply brighter/more textured, or is the primary decaying? --")
    print("  b is the fresh patch's raw score, a is the old one's. If the on-deck were")
    print("  merely a better patch, a and b would each be steady within a window and the")
    print("  ratio would be a property of the pair. If the primary is decaying, a falls")
    print("  through the window while b holds.")
    print(f"  {'window':>12}  {'n':>3}  {'a first->last':>18}  {'b first->last':>18}  {'r':>6}")
    seen = []
    for r in rows:
        k = (r["oidp"], r["oidd"])
        if k not in seen:
            seen.append(k)
    for k in seen[:12]:
        grp = [r for r in rows if (r["oidp"], r["oidd"]) == k]
        if len(grp) < 2:
            continue
        print(f"  {k[0]:5d}->{k[1]:<5d}  {len(grp):3d}  "
              f"{grp[0]['a']:8.1f} ->{grp[-1]['a']:8.1f}  "
              f"{grp[0]['b']:8.1f} ->{grp[-1]['b']:8.1f}  "
              f"{median([g['r'] for g in grp]):6.3f}")

    # Within-window trends, pooled: does the OLD patch fall while the FRESH one holds?
    da, db = [], []
    for k in seen:
        grp = [r for r in rows if (r["oidp"], r["oidd"]) == k]
        if len(grp) < 3:
            continue
        ma, _ = slope([g["agep"] for g in grp], [g["a"] for g in grp])
        mb, _ = slope([g["agep"] for g in grp], [g["b"] for g in grp])
        da.append(ma / grp[0]["a"] if grp[0]["a"] else 0.0)
        db.append(mb / grp[0]["b"] if grp[0]["b"] else 0.0)
    if da:
        print(f"\n  pooled over {len(da)} windows with >=3 samples, per-frame fractional change:")
        print(f"    old patch a: {median(da):+.3%} / frame")
        print(f"    new patch b: {median(db):+.3%} / frame")
        print("  (both rise while the lens climbs toward focus; the one that rises SLOWER")
        print("   is the one being penalised by something other than focus)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
