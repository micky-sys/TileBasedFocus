"""How much of the frame does one traverse actually cross?

AF3's profile metric needs a traverse to span profile_min_span_frac of the
frame along the travel axis, or it returns None and the station banks nothing.
That gate is the difference between a working station walk and one that stalls
on every station, so it is worth measuring per direction BEFORE a run rather
than discovering it 45 s per station into one.

Usage: py -3 tools/af3_span_check.py <csv> [csv ...]
"""
from __future__ import annotations

import csv
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

FRAME_W, FRAME_H = 3350, 2382  # coarse preview the tracker runs on
GATE = 0.55                    # AutoFocusConfigV3.profile_min_span_frac


def report(path: Path) -> None:
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    hdr = {}
    for ln in lines:
        if not ln.startswith("#"):
            break
        k, _, v = ln[1:].strip().partition(",")
        hdr[k.strip()] = v.strip()
    rows = list(csv.DictReader([ln for ln in lines if not ln.startswith("#")]))
    if not rows or "x0" not in rows[0]:
        print(f"{path.name:<40} (no per-object boxes)")
        return

    by_oid = defaultdict(list)
    for r in rows:
        try:
            by_oid[int(float(r["oid"]))].append(r)
        except (TypeError, ValueError):
            continue

    sx, sy, lives = [], [], []
    for tr in by_oid.values():
        if len(tr) < 4:
            continue
        xs = [0.5 * (float(r["x0"]) + float(r["x1"])) for r in tr]
        ys = [0.5 * (float(r["y0"]) + float(r["y1"])) for r in tr]
        sx.append(max(xs) - min(xs))
        sy.append(max(ys) - min(ys))
        lives.append(len(tr))
    if not sx:
        print(f"{path.name:<40} (no traverse >=4 frames)")
        return

    mx, my = st.median(sx), st.median(sy)
    axis, frac = (("x", mx / FRAME_W) if mx > my else ("y", my / FRAME_H))
    t = [float(r["t_s"]) for r in rows]
    fps = (len(set(int(float(r["frame"])) for r in rows))
           / max(max(t) - min(t), 1e-9))
    verdict = "OK" if frac >= GATE else f"REJECTED (needs {GATE:.0%})"
    print(f"{path.name:<40} {hdr.get('kind', '?'):<8} "
          f"{hdr.get('algorithm', '?'):<14} axis={axis} "
          f"span={frac:>4.0%} life={st.median(lives):>4.0f}f "
          f"fps={fps:>4.2f} objs={len(by_oid):>4}  {verdict}")


if __name__ == "__main__":
    for a in sys.argv[1:]:
        report(Path(a))
