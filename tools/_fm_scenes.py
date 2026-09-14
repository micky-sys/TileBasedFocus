"""One-off: separate OPTICS from CONTENT/SCREEN across N field maps of DIFFERENT frozen scenes.

Two readouts, along two different axes:

  VERTEX (tile minus centre, within a run).  A lens's best-focus position at a field
  point cannot depend on what content sits there, so a vertex pattern that repeats
  across scenes IS the optical field.  Compared tile-minus-centre and never in absolute
  counts: each sweep re-zeros its own axis and returns downward, so the run zero drifts
  while the differences do not.

  OWN-PEAK HEIGHT (each tile scored at its own best focus, so defocus is divided out).
  Whatever is left is not focus.  Content is what averaging over scenes cancels; the
  position-fixed residual is panel/illumination/off-axis lens quality.

Plus the one that connects to the moving runs: each tile's curve normalised by its own
peak makes the curve content-free, so the top:bottom score ratio as a function of where
the lens is parked is a direct, model-free prediction of the y-gradient the observe runs
saw -- no Gaussian assumed, measured.

    Run: py -3 tools/_fm_scenes.py
"""

from __future__ import annotations

import csv
import glob
import re
import statistics as st

TILES = ("centre", "top", "bottom", "left", "right")
LOGS = r"F:\My Drive\Micky\WORK\Cursor\TileBasedFocus\host\Session logs"
PAT = LOGS + r"\Iron661_fieldmap_20260831_14[5-6]*.csv"


def load(path: str) -> dict:
    """-> {'roi': {name: (x0,y0,x1,y1)}, 'pos': [...], 'score': {name: [...]}}"""
    with open(path, newline="", encoding="utf-8") as fh:
        first = fh.readline()
        rois = {}
        for name, nums in re.findall(r'"(\w+)=\(([^)]*)\)"', first):
            rois[name] = tuple(int(v) for v in nums.split(","))
        rd = csv.DictReader(fh)
        pos, score = [], {t: [] for t in TILES}
        for row in rd:
            pos.append(float(row["pos_counts"]))
            for t in TILES:
                score[t].append(float(row[t]))
    return {"roi": rois, "pos": pos, "score": score}


def vertex(pos: list[float], s: list[float]) -> float:
    """Parabola through the argmax and its neighbours +/- W, matching the sweep's own
    fit_window.  Falls back to the argmax when the fit is degenerate."""
    i = max(range(len(s)), key=lambda k: s[k])
    W = 20
    lo, hi = max(0, i - W), min(len(s), i + W + 1)
    xs, ys = pos[lo:hi], s[lo:hi]
    n = len(xs)
    if n < 3:
        return pos[i]
    mx = sum(xs) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return pos[i]
    # least-squares quadratic in centred x
    sx2 = sum((x - mx) ** 2 for x in xs)
    sx4 = sum((x - mx) ** 4 for x in xs)
    sy = sum(ys)
    sx1y = sum((x - mx) * y for x, y in zip(xs, ys))
    sx2y = sum((x - mx) ** 2 * y for x, y in zip(xs, ys))
    det = n * sx4 - sx2 * sx2
    if det == 0:
        return pos[i]
    a = (n * sx2y - sx2 * sy) / det
    b = sx1y / sx2 if sx2 else 0.0
    if a >= 0:
        return pos[i]
    return mx - b / (2 * a)


def interp(pos: list[float], s: list[float], p: float) -> float | None:
    if p < pos[0] or p > pos[-1]:
        return None
    for k in range(1, len(pos)):
        if pos[k] >= p:
            x0, x1 = pos[k - 1], pos[k]
            if x1 == x0:
                return s[k]
            f = (p - x0) / (x1 - x0)
            return s[k - 1] * (1 - f) + s[k] * f
    return s[-1]


runs = [load(p) for p in sorted(glob.glob(PAT))]
print(f"{len(runs)} sweeps, {len(runs[0]['pos'])} samples each\n")

r0 = runs[0]["roi"]
H, W = 9528, 13400
print("geometry (full-res px, frame 13400 x 9528):")
for t in TILES:
    x0, y0, x1, y1 = r0[t]
    print(f"  {t:<7} centre y={(y0+y1)//2:>5} ({(y0+y1)/2/H*100:4.1f}% of H)  "
          f"x={(x0+x1)//2:>5} ({(x0+x1)/2/W*100:4.1f}% of W)")
dy_t = (r0['centre'][1] + r0['centre'][3]) / 2 - (r0['top'][1] + r0['top'][3]) / 2
dy_b = (r0['bottom'][1] + r0['bottom'][3]) / 2 - (r0['centre'][1] + r0['centre'][3]) / 2
print(f"  symmetry: top is {dy_t:.0f}px above centre, bottom {dy_b:.0f}px below "
      f"(delta {abs(dy_t-dy_b):.0f}px)")
print(f"  vertical baseline {(dy_t+dy_b)/H*100:.1f}% of frame height\n")

# ---------------------------------------------------------------- vertex = optics
verts = [{t: vertex(r["pos"], r["score"][t]) for t in TILES} for r in runs]
print("VERTEX, tile minus centre (counts).  Repeatable across scenes => optics.")
print(f"  {'tile':<8}" + "".join(f"{i+1:>8}" for i in range(len(runs)))
      + f"{'mean':>9}{'stdev':>8}{'CV':>7}")
dmean = {}
for t in ("top", "bottom", "left", "right"):
    ds = [v[t] - v["centre"] for v in verts]
    m, sd = st.mean(ds), st.stdev(ds)
    dmean[t] = m
    print(f"  {t:<8}" + "".join(f"{d:>+8.0f}" for d in ds)
          + f"{m:>+9.0f}{sd:>8.0f}{abs(sd/m)*100:>6.0f}%")

v_tilt, v_curv = (dmean["top"] - dmean["bottom"]) / 2, (dmean["top"] + dmean["bottom"]) / 2
h_tilt, h_curv = (dmean["left"] - dmean["right"]) / 2, (dmean["left"] + dmean["right"]) / 2
print(f"\n  vertical  : tilt +/-{v_tilt:>4.0f}c   curvature {v_curv:>+5.0f}c "
      f"(edges vs centre)   top-to-bottom span {dmean['top']-dmean['bottom']:>+5.0f}c")
print(f"  horizontal: tilt +/-{h_tilt:>4.0f}c   curvature {h_curv:>+5.0f}c "
      f"           left-to-right span {dmean['left']-dmean['right']:>+5.0f}c")

# ------------------------------------------------- own-peak height = not focus at all
print("\nOWN-PEAK SCORE, each tile at its OWN best focus (defocus divided out),")
print("normalised to the run's geometric mean so exposure/brightness drift cancels.")
peaks = [{t: max(r["score"][t]) for t in TILES} for r in runs]
norm = []
for p in peaks:
    g = st.geometric_mean(list(p.values()))
    norm.append({t: p[t] / g for t in TILES})
print(f"  {'tile':<8}" + "".join(f"{i+1:>8}" for i in range(len(runs)))
      + f"{'mean':>9}{'SE':>8}")
for t in TILES:
    vs = [n[t] for n in norm]
    m, se = st.mean(vs), st.stdev(vs) / len(vs) ** 0.5
    flag = "  <-- flat" if abs(m - 1) < 2 * se else ""
    print(f"  {t:<8}" + "".join(f"{v:>8.2f}" for v in vs)
          + f"{m:>9.2f}{se:>8.2f}{flag}")
tb = [n["top"] / n["bottom"] for n in norm]
print(f"\n  top:bottom own-peak ratio {st.mean(tb):.2f} +/- "
      f"{st.stdev(tb)/len(tb)**0.5:.2f} (SE over scenes)")

# --------------------------------- normalised curves = model-free y-gradient prediction
print("\nPREDICTED top:bottom SCORE RATIO vs where the lens is parked.")
print("Each tile's curve divided by its own peak, so content cancels exactly.")
print("Park is relative to the CENTRE tile's best focus.")
print(f"  {'park':>7}  {'top':>6} {'bottom':>7}  {'top:bottom':>10}   (mean over scenes)")
for park in range(-500, 2001, 250):
    ts, bs, rs = [], [], []
    for r, v in zip(runs, verts):
        p = v["centre"] + park
        a = interp(r["pos"], r["score"]["top"], p)
        b = interp(r["pos"], r["score"]["bottom"], p)
        if a is None or b is None:
            continue
        na = a / max(r["score"]["top"])
        nb = b / max(r["score"]["bottom"])
        ts.append(na)
        bs.append(nb)
        rs.append(na / nb)
    if rs:
        print(f"  {park:>+7}  {st.mean(ts):>6.2f} {st.mean(bs):>7.2f}  "
              f"{st.mean(rs):>10.2f}")
