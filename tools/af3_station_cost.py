"""Where a station's ~18 s actually goes.

Reads an AF3 run CSV and, per station (a lens dwell), reports how many objects were
alive, how many produced a usable traverse, and how long after the station opened the
Nth usable traverse landed. The point is to separate the two things that set the dwell:
the traverse itself (scene speed) and the wait for roster slots to free up.

    py -3 tools/af3_station_cost.py <observe.csv> [min_span_frac] [need]
"""
import csv
import sys
from collections import defaultdict

SPAN = float(sys.argv[2]) if len(sys.argv) > 2 else 0.55
NEED = int(sys.argv[3]) if len(sys.argv) > 3 else 4
FH = 2382.0          # coarse preview height; travel is along y on a forward run


def main(path: str) -> None:
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        for line in fh:
            if not line.startswith("#"):
                rows = list(csv.DictReader([line] + fh.readlines()))
                break

    # A station is a run of frames at one encoder position.
    stations, cur, last = [], [], None
    for r in sorted(rows, key=lambda r: int(r["frame"])):
        raw = round(float(r["raw_counts"]))
        if last is not None and abs(raw - last) > 40:
            stations.append(cur)
            cur = []
        cur.append(r)
        last = raw
    stations.append(cur)
    stations = [s for s in stations if len(s) >= 20]      # drop the fast dive moves

    print(f"== {len(stations)} stations, span gate {SPAN:.0%} of {FH:.0f}px, "
          f"need {NEED}\n")
    hdr = ("  #      raw  objs  ok  rej   fill_f  done_f  dwell_f   wasted_f")
    print(hdr)
    tot = defaultdict(float)
    for i, st in enumerate(stations):
        f0 = int(st[0]["frame"])
        raw = round(float(st[0]["raw_counts"]))
        by_oid = defaultdict(list)
        for r in st:
            by_oid[int(r["oid"])].append(r)
        ok_at = []                     # frame each usable traverse completed
        births = []
        for oid, rs_ in by_oid.items():
            ys = [float(r["y_centre"]) for r in rs_]
            births.append(int(rs_[0]["frame"]) - f0)
            if (max(ys) - min(ys)) >= SPAN * FH:
                ok_at.append(int(rs_[-1]["frame"]) - f0)
        ok_at.sort()
        n_ok, n_rej = len(ok_at), len(by_oid) - len(ok_at)
        # frames from station open until the roster was full of FRESH objects
        fill = sorted(births)[min(NEED - 1, len(births) - 1)] if births else -1
        done = ok_at[NEED - 1] if len(ok_at) >= NEED else (ok_at[-1] if ok_at else -1)
        dwell = int(st[-1]["frame"]) - f0
        print(f"{i:3d} {raw:8d} {len(by_oid):5d} {n_ok:3d} {n_rej:4d} "
              f"{fill:8d} {done:7d} {dwell:8d} {fill:10d}")
        tot["objs"] += len(by_oid); tot["ok"] += n_ok; tot["rej"] += n_rej
        tot["fill"] += fill; tot["dwell"] += dwell
        tot["n"] += 1

    # One station, object by object: born BEFORE the station opened, or after?
    st = stations[len(stations) // 2]
    f0, fN = int(st[0]["frame"]), int(st[-1]["frame"])
    print(f"\n== station {len(stations)//2} in detail (frames {f0}..{fN})")
    print("  oid  born_f  first_f  last_f  frames    y0     y1   span  span%  verdict")
    by_oid = defaultdict(list)
    for r in st:
        by_oid[int(r["oid"])].append(r)
    for oid, rs_ in sorted(by_oid.items()):
        ys = [float(r["y_centre"]) for r in rs_]
        span = max(ys) - min(ys)
        born = int(rs_[0]["born_frame"])
        fa, fb = int(rs_[0]["frame"]), int(rs_[-1]["frame"])
        ok = span >= SPAN * FH
        why = "OK" if ok else ("born pre-step, trail reset mid-traverse"
                               if born < f0 else "born too late to finish")
        print(f"{oid:5d} {born:7d} {fa:8d} {fb:7d} {len(rs_):7d} "
              f"{ys[0]:6.0f} {ys[-1]:6.0f} {span:6.0f} {span/FH:6.0%}  {why}")

    n = tot["n"]
    print(f"\n-- per station: {tot['objs']/n:.1f} objects, {tot['ok']/n:.1f} usable, "
          f"{tot['rej']/n:.1f} REJECTED ({tot['rej']/max(tot['objs'],1):.0%} of them)")
    print(f"-- dwell {tot['dwell']/n:.1f} frames, of which {tot['fill']/n:.1f} are "
          f"spent waiting for the {NEED}th fresh object to be born")
    print(f"-- total {tot['rej']:.0f} of {tot['objs']:.0f} objects banked nothing")


if __name__ == "__main__":
    main(sys.argv[1])
