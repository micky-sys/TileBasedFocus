"""What sets the frame period: measured, not assumed.

Every row in an observe CSV is one object on one frame, with a timestamp. Group by
frame and you get (time, patches scored on it); difference the times and bucket by
patch count, and the slope is the MARGINAL cost of scoring one more patch at full
resolution. The intercept is everything else -- trigger period, readout, and (on the
bench) the lockstep handshake.

    py -3 tools/af3_frame_budget.py <observe.csv> [...]
"""
import csv
import statistics
import sys
from collections import defaultdict


def load(path: str):
    with open(path, newline="", encoding="utf-8") as fh:
        meta = {}
        for line in fh:
            if line.startswith("#"):
                parts = line.lstrip("# ").rstrip().split(",", 1)
                if len(parts) == 2:
                    meta[parts[0]] = parts[1]
                continue
            return meta, list(csv.DictReader([line] + fh.readlines()))
    return meta, []


def main(paths) -> None:
    for path in paths:
        meta, rows = load(path)
        frames = defaultdict(list)
        for r in rows:
            frames[int(r["frame"])].append(float(r["t_s"]))
        seq = sorted((f, len(ts), ts[0]) for f, ts in frames.items())

        print(f"\n== {path.split(chr(92))[-1]}"
              f"   mode={meta.get('mode', meta.get('kind', '?'))}"
              f"  objects_cfg={meta.get('objects', '?')}")
        by_n = defaultdict(list)
        for (f0, _n0, t0), (f1, n1, t1) in zip(seq, seq[1:]):
            if f1 - f0 != 1:          # only consecutive frames time anything
                continue
            by_n[n1].append(t1 - t0)
        print("  patches   frames   median dt    implied fps")
        pts = []
        for n in sorted(by_n):
            dts = by_n[n]
            if len(dts) < 5:
                continue
            med = statistics.median(dts)
            pts.append((n, med))
            print(f"{n:9d} {len(dts):8d} {med*1000:9.0f} ms {1.0/med:12.2f}")
        if len(pts) >= 2:
            # Straight two-point slope across the widest span available. A least-squares
            # line would read more precisely than this data deserves.
            (na, ta), (nb, tb) = pts[0], pts[-1]
            slope = (tb - ta) / max(nb - na, 1)
            print(f"  -- marginal cost of one more patch: {slope*1000:.0f} ms")
            print(f"  -- fixed cost with zero patches:    {(ta - na*slope)*1000:.0f} ms")


if __name__ == "__main__":
    main(sys.argv[1:])
