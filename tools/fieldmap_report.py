"""Read Iron661 field-map CSVs and say what they mean.

Two jobs the in-app log line doesn't do:

* **Curve quality per tile.** A vertex is only worth reading if the tile actually
  had a focus hump. Contrast (peak/floor) and the half-max width say whether it
  did, so a tile that sat on flat texture or a saturated highlight can be thrown
  out instead of quietly skewing the verdict.
* **Tilt vs curvature.** Opposite edges tell the two apart. Antisymmetric (top and
  bottom on opposite sides of centre) is TILT -- camera not square to the screen,
  a bench alignment error that does not exist in flight over flat ground.
  Symmetric (both edges the same side) is FIELD CURVATURE -- a property of the
  lens that flight will see too. Only the second one is AF's problem to live with.

Run: py -3 tools/fieldmap_report.py [path-to-csv] [--window=N]
     (no argument = the newest fieldmap CSV in the session-log folder)

     py -3 tools/fieldmap_report.py --stats [--last=N] [--gap=SECONDS]
     Group several sweeps into scenes and split the scatter into the part that is
     the instrument and the part that is the scene. Sweeps closer together than
     ``--gap`` are taken to share a scene.
"""

from __future__ import annotations

import csv
import datetime as _dt
import glob
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))          # capture.autofocus imports top-level sharpness
sys.path.insert(0, str(REPO / "host"))


from capture.field_map import FieldMapConfig  # noqa: E402

TERMS = ("tiltH", "tiltV", "curvH", "curvV", "spread")


def all_csvs() -> list[str]:
    from capture.session_log import default_log_dir

    hits = glob.glob(os.path.join(default_log_dir(), "Iron661_fieldmap_*.csv"))
    if not hits:
        raise SystemExit("no field-map CSV found")
    return sorted(hits, key=stamp_of)


def stamp_of(path: str) -> _dt.datetime:
    """Timestamp from the filename, not the mtime -- copying a CSV must not
    reorder a run or silently merge two scenes."""
    m = re.search(r"(\d{8}_\d{6})", os.path.basename(path))
    if not m:
        return _dt.datetime.fromtimestamp(os.path.getmtime(path))
    return _dt.datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")


def newest_csv() -> str:
    return all_csvs()[-1]


def fit_vertex(xs: list[float], ys: list[float], window: int):
    """The engine's own fit, not a lookalike.

    This was a reimplementation that clamped an out-of-span vertex where the engine
    rejects it, so the tool quietly disagreed with the log it was explaining -- on
    one sweep it reported a vertex 200c from the one the app had just printed.
    """
    from capture.autofocus_v2 import _fit_peak

    return _fit_peak(list(zip(xs, ys)), window)


def read_tiles(path: str, window: int):
    """Per-tile curve summary for one sweep. Returns (positions, {name: info})."""
    rows = list(csv.reader(open(path, encoding="utf-8")))
    names = rows[1][3:]
    data = rows[2:]
    pos = [float(r[1]) for r in data]
    out: dict[str, dict] = {}
    for i, n in enumerate(names):
        s = [float(r[3 + i]) for r in data]
        mx, mn = max(s), min(s)
        j = s.index(mx)
        half = (mx + mn) / 2.0
        above = [pos[k] for k in range(len(s)) if s[k] >= half]
        # Midpoint of the above-half-max region: a model-free peak locator that,
        # unlike the argmax, is not thrown by noise on a broad flat top.
        mid = (max(above) + min(above)) / 2.0 if above else 0.0
        vert = fit_vertex(pos, s, window)
        contrast = mx / max(mn, 1e-9)
        out[n] = {
            # Still rising at an end of the sweep: the peak was never bracketed, so
            # this is a lower bound, not a vertex.
            "clipped": (j == 0 or j == len(pos) - 1) and contrast >= 5.0,
            "vertex": vert if vert is not None else pos[j],
            "fitted": vert is not None,
            "argmax": pos[j],
            "mid": mid,
            "width": (max(above) - min(above)) if above else 0.0,
            "contrast": contrast,
            "n_above": len(above),
            # The curve itself, for callers that need the score at an arbitrary
            # parked position rather than just the vertex (flatness_report).
            "scores": s,
        }
    return pos, out


def field_terms(tiles: dict[str, dict]) -> dict[str, float] | None:
    """Split the five vertices into tilt and curvature about the centre."""
    if not {"centre", "top", "bottom", "left", "right"} <= set(tiles):
        return None
    v = {k: tiles[k]["vertex"] for k in tiles}
    c = v["centre"]
    return {
        "tiltV": (v["bottom"] - v["top"]) / 2.0,
        "curvV": (v["top"] + v["bottom"]) / 2.0 - c,
        "tiltH": (v["right"] - v["left"]) / 2.0,
        "curvH": (v["left"] + v["right"]) / 2.0 - c,
        "spread": max(v.values()) - min(v.values()),
    }


def _sd(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def report_one(path: str, window: int) -> int:
    pos, tiles = read_tiles(path, window)
    step = (pos[-1] - pos[0]) / max(1, len(pos) - 1)
    print(f"{os.path.basename(path)}")
    print(f"{len(pos)} samples, {pos[0]:+.0f} .. {pos[-1]:+.0f} counts, "
          f"step ~{step:.0f}c\n")
    print(f"{'tile':<8}{'vertex':>9}{'argmax':>9}{'midpt':>8}{'|v-mid|':>9}"
          f"{'contrast':>10}{'halfwidth':>11}{'pts>half':>10}")
    for n, t in tiles.items():
        shown = f"{t['vertex']:+.0f}" + ("" if t["fitted"] else "*")
        print(f"{n:<8}{shown:>9}{t['argmax']:>+9.0f}{t['mid']:>+8.0f}"
              f"{abs(t['vertex'] - t['mid']):>9.0f}{t['contrast']:>10.1f}"
              f"{t['width']:>11.0f}{t['n_above']:>10}")

    terms = field_terms(tiles)
    if terms is None:
        print("\n(need all five tiles for the tilt/curvature split)")
        return 0
    tilt = (terms["tiltV"] ** 2 + terms["tiltH"] ** 2) ** 0.5
    print(f"\nspread          {terms['spread']:>7.0f}c   (everything below adds up to this)")
    print(f"tilt  vertical  {terms['tiltV']:>+7.0f}c   bottom-vs-top, antisymmetric")
    print(f"tilt  horizontal{terms['tiltH']:>+7.0f}c   right-vs-left, antisymmetric")
    print(f"tilt  combined  {tilt:>7.0f}c   <- ALIGNMENT: absent in flight, shim it out")
    print(f"curv  vertical  {terms['curvV']:>+7.0f}c   edges-vs-centre, symmetric")
    print(f"curv  horizontal{terms['curvH']:>+7.0f}c   <- OPTICS: flight sees this too")
    dominant = "tilt" if tilt > max(abs(terms["curvV"]), abs(terms["curvH"])) \
        else "curvature"
    print(f"\ndominated by {dominant}.")
    return 0


def report_stats(paths: list[str], window: int, gap_s: float,
                 sizes: list[int] | None = None) -> int:
    """Split the run-to-run scatter into instrument and scene.

    Repeats on ONE frozen scene measure the instrument: same optics, same content,
    so anything that moves is noise. Across scenes the optics are still identical,
    so what moves on top of that is the metric's dependence on image content -- and
    that part is a floor under any contrast-detection AF, not a tool defect.
    """
    runs = []
    for p in paths:
        _pos, tiles = read_tiles(p, window)
        t = field_terms(tiles)
        if t is None:
            print(f"skipping {os.path.basename(p)} (incomplete tiles)")
            continue
        bad = [n for n, x in tiles.items() if x["clipped"]]
        if bad:
            print(f"skipping {os.path.basename(p)} -- never bracketed focus on "
                  f"{', '.join(bad)}")
            continue
        runs.append((stamp_of(p), p, t, tiles))
    if len(runs) < 2:
        raise SystemExit("need at least two complete sweeps")

    if sizes:
        if sum(sizes) != len(runs):
            raise SystemExit(
                f"--groups sums to {sum(sizes)} but there are {len(runs)} usable "
                f"sweeps; pass the CSVs explicitly or fix the counts")
        groups, i = [], 0
        for k in sizes:
            groups.append(runs[i:i + k])
            i += k
        how = "as given by --groups"
    else:
        # Timing is a poor proxy: refreezing a scene can take no longer than the
        # pause between two repeats, so a gap rule silently merges or splits scenes.
        groups = [[runs[0]]]
        for prev, cur in zip(runs, runs[1:]):
            if (cur[0] - prev[0]).total_seconds() > gap_s:
                groups.append([])
            groups[-1].append(cur)
        how = f"guessed from gaps > {gap_s:.0f}s -- pass --groups=3,3,3 to be sure"

    print(f"grouping {len(runs)} sweeps into {len(groups)} scene(s), {how}\n")
    hdr = "{:<10}" + "{:>9}" * len(TERMS)
    print(hdr.format("run", *TERMS))
    for gi, g in enumerate(groups, 1):
        print(f"-- scene {gi} --")
        for stamp, _p, t, _tiles in g:
            print(hdr.format(stamp.strftime("%H:%M:%S"),
                             *[f"{t[k]:+.0f}" for k in TERMS]))
        if len(g) > 1:
            print(hdr.format("  mean", *[
                f"{sum(t[k] for _s, _p, t, _q in g) / len(g):+.0f}" for k in TERMS]))

    usable = [g for g in groups if len(g) > 1]
    print("\nerror budget, in counts (1 str0 pulse = 24c)")
    print(f"{'term':<10}{'within':>9}{'between':>9}{'total':>9}   verdict")
    for k in TERMS:
        # Pooled within-scene SD: deviations from each scene's OWN mean, so a real
        # scene-to-scene shift cannot inflate what we call instrument noise.
        ss, dof = 0.0, 0
        for g in usable:
            xs = [t[k] for _s, _p, t, _q in g]
            m = sum(xs) / len(xs)
            ss += sum((x - m) ** 2 for x in xs)
            dof += len(xs) - 1
        within = (ss / dof) ** 0.5 if dof else 0.0
        means = [sum(t[k] for _s, _p, t, _q in g) / len(g) for g in groups]
        between = _sd(means)
        total = _sd([t[k] for _s, _p, t, _q in runs])
        # Between-scene SD already contains a within-scene share of within/sqrt(n).
        n = min(len(g) for g in groups) or 1
        scene_only = max(0.0, between ** 2 - within ** 2 / n) ** 0.5
        verdict = ("scene-dominated" if scene_only > within else
                   "instrument-dominated" if within > 0 else "-")
        print(f"{k:<10}{within:>9.0f}{scene_only:>9.0f}{total:>9.0f}   {verdict}")
    print(f"\nwithin  = repeat sweeps, one frozen scene ({sum(len(g) - 1 for g in usable)} dof)")
    print(f"between = scene-to-scene, instrument share removed ({len(groups) - 1} dof)")
    return 0


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    flags = [a for a in argv[1:] if a.startswith("--")]
    window = FieldMapConfig.fit_window       # match the engine unless overridden
    last, gap, sizes = 0, 180.0, None
    for a in flags:
        if a.startswith("--window="):
            window = int(a.split("=", 1)[1])
        elif a.startswith("--last="):
            last = int(a.split("=", 1)[1])
        elif a.startswith("--gap="):
            gap = float(a.split("=", 1)[1])
        elif a.startswith("--groups="):
            sizes = [int(x) for x in a.split("=", 1)[1].split(",")]
    if "--stats" in flags:
        paths = args or all_csvs()
        if last:
            paths = paths[-last:]
        return report_stats(paths, window, gap, sizes)
    return report_one(args[0] if args else newest_csv(), window)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
