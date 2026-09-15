"""Is the lens's field a dome or a bowl? Measured on real flight frames.

AF3's D = ln(centre score) - mean(ln(edge scores)) is one number for the
across-field *shape* of sharpness. On the bench that number turned out to be
94% monitor raster (HANDOFF 45), so the shape it reported was the LCD's, not
the lens's. This measures the same shape on flight TIFs, where there is no
screen to measure.

The obstacle is that terrain is not uniform: a single frame's sharpness map is
mostly a map of what happened to be on the ground. But field curvature is a
fixed property of the optics at a given mirror position, while the ground under
each sweep is new, so averaging the (log) maps over many sweeps keeps the field
term and averages the content term away. Lines are aggregated separately -- two
lines over different ground that agree on the same shape is the optics.

    py -3 tools/field_flatness.py "H:/MickyCrazy/*.tif" --csv out.csv --png out.png
    py -3 tools/field_flatness.py --self-check

The scored plane is the green sub-grid at the true sensor sites, i.e. the exact
image :func:`capture.autofocus.focus_measure_green` sees in flight -- these TIFs
are demosaiced, and the interpolated sites are visibly smoother (Laplacian sd
6675 against 9401 on a probe tile), so scoring all of channel 1 would measure
the debayer's low-pass as much as the lens.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "host"))

GREEN_PHASE = (0, 1)  # true green sensor sites in the demosaiced RGB (probed; RGGB)
TILE = 512
ROWS, COLS = 9, 13
# 14-bit sensor stored left-shifted 2, so the ceiling is 16383<<2. Count from a
# little under it: the last couple of codes are already in the non-linear knee.
CLIP_LEVEL = 65000
# Reject a cell above this clipped fraction. Frame-wide clipping runs 0.007-0.1%
# of green samples on this flight, so anything over a few tenths of a percent in
# one cell is a blown roof or a water glint sitting in that cell, not exposure.
CLIP_REJECT = 0.002
FLAT_BAND = 0.05  # |curv_x + curv_y| below this is called flat, not a shape


def cell_boxes(h: int, w: int, rows: int, cols: int, tile: int):
    """Even-origin tile boxes on a centred rows x cols grid.

    Origins are forced even so the Bayer phase is preserved and the green
    sub-grid lands on real green sites in every cell, the same constraint
    ``focus_measure_full_res`` relies on for its ROI crops.
    """
    boxes = []
    for gy in range(rows):
        for gx in range(cols):
            y0 = min(max(int((gy + 0.5) * h / rows) - tile // 2, 0), h - tile)
            x0 = min(max(int((gx + 0.5) * w / cols) - tile // 2, 0), w - tile)
            boxes.append((gy, gx, y0 & ~1, x0 & ~1))
    return boxes


def grid_scores(img, rows: int = ROWS, cols: int = COLS, tile: int = TILE):
    """Focus score and clipped-sample fraction per grid cell.

    Clipping has to be measured, not assumed away, because it biases this
    measurement in the direction of a false answer: a saturated highlight is a
    *flat* patch, so the Laplacian reads it as defocus. Vignetting makes the
    centre of the field the brightest part, so the cells most likely to clip are
    the centre cells -- uncorrected, a well-exposed dome would report as a bowl.
    """
    from capture.autofocus import focus_measure_green

    dy, dx = GREEN_PHASE
    plane = img[:, :, 1] if img.ndim == 3 else img
    scores = np.zeros((rows, cols), np.float64)
    clip = np.zeros((rows, cols), np.float64)
    for gy, gx, y0, x0 in cell_boxes(*plane.shape[:2], rows, cols, tile):
        patch = plane[y0 : y0 + tile, x0 : x0 + tile]
        green = np.ascontiguousarray(patch[dy::2, dx::2])
        scores[gy, gx] = focus_measure_green(green)
        clip[gy, gx] = float(np.count_nonzero(green >= CLIP_LEVEL)) / green.size
    return scores, clip


def log_field(scores: np.ndarray, clip: np.ndarray | None = None) -> np.ndarray:
    """One frame's map in log units, relative to that frame's own median.

    The log is what makes this additive: a frame that is globally softer -- haze,
    a different lens position, thicker air, lower sun -- shifts every cell by
    nearly the same amount, so subtracting the median removes the frame's
    overall focus level without touching its shape. Same algebra as AF3's D,
    which is itself a difference of logs.

    Cells whose crop is clipped are dropped to NaN rather than corrected: there
    is no way to recover gradient from samples that all read the ceiling, and
    with 18-25 sweeps per line another frame supplies that cell.
    """
    lg = np.log(np.maximum(scores, 1e-9))
    if clip is not None:
        lg = np.where(clip > CLIP_REJECT, np.nan, lg)
    return lg - np.nanmedian(lg)


def quad_fit(field: np.ndarray) -> dict[str, float]:
    """Least-squares a + bx + cy + dx^2 + ey^2 + fxy on coords normalised to +-1.

    Separates the two things that look alike in a raw map: a *tilt* (b, c) is a
    mirror/sensor plane not square to the axis, whereas *curvature* (d, e) is the
    dome-or-bowl. Reading only centre-minus-edge cannot tell them apart, because
    a pure tilt leaves the centre at the mean of the edges.
    """
    rows, cols = field.shape
    gy, gx = np.mgrid[0:rows, 0:cols]
    x = (gx - (cols - 1) / 2) / ((cols - 1) / 2)
    y = (gy - (rows - 1) / 2) / ((rows - 1) / 2)
    terms = np.stack([np.ones_like(x), x, y, x * x, y * y, x * y], -1).reshape(-1, 6)
    vals = field.reshape(-1)
    ok = np.isfinite(vals)
    coef, *_ = np.linalg.lstsq(terms[ok], vals[ok], rcond=None)
    names = ("const", "tilt_x", "tilt_y", "curv_x", "curv_y", "astig")
    return dict(zip(names, (float(c) for c in coef)))


def centre_edge_d(field: np.ndarray) -> float:
    """AF3's D on this map: centre cell minus the mean of the outer ring.

    ``field`` is already in log units, so this is ln(centre) - mean(ln(edge))
    exactly as the station walk computes it; the median subtraction in
    :func:`log_field` cancels in the difference.
    """
    ring = np.concatenate(
        [field[0, :], field[-1, :], field[1:-1, 0], field[1:-1, -1]]
    )
    return float(field[field.shape[0] // 2, field.shape[1] // 2] - np.nanmean(ring))


def describe(field: np.ndarray, label: str, spread: np.ndarray | None = None,
             dropped: np.ndarray | None = None, frames: int | None = None) -> dict:
    fit = quad_fit(field)
    d = centre_edge_d(field)
    curv = fit["curv_x"] + fit["curv_y"]
    # A flat band, so the sign of a noise-sized number does not get read as a
    # shape: +-0.05 in log units is a ~5% centre-to-edge score difference, well
    # under the per-cell terrain scatter this averages over.
    if abs(curv) < FLAT_BAND:
        shape = "FLAT (no curvature above the noise)"
    else:
        shape = "DOME (centre sharp)" if curv < 0 else "BOWL (edges sharp)"
    print(f"\n=== {label} ===")
    print("  field map, 100*ln(score) relative to frame median (+ = sharper):")
    for row in field:
        print("   " + " ".join(f"{100 * v:6.0f}" for v in row))
    if spread is not None:
        print(f"  per-cell scatter across frames (100*ln, IQR): median "
              f"{100 * np.nanmedian(spread):.0f}, worst {100 * np.nanmax(spread):.0f}")
    if dropped is not None and dropped.sum():
        total = dropped.size * int(dropped.max()) if frames is None else dropped.size * frames
        print(f"  clipped cells dropped: {int(dropped.sum())} of {total} "
              f"cell-frames ({100.0 * dropped.sum() / max(total, 1):.1f}%), "
              f"worst single cell lost {int(dropped.max())} frames")
    print(f"  D (centre - edge ring) = {d:+.3f}  -> centre is "
          f"{np.exp(d):.2f}x the edge score")
    print(f"  tilt   across={fit['tilt_x']:+.3f}  along={fit['tilt_y']:+.3f}")
    print(f"  curv   across={fit['curv_x']:+.3f}  along={fit['curv_y']:+.3f}"
          f"  (sum {curv:+.3f})  astig={fit['astig']:+.3f}")
    print(f"  verdict: {shape}")
    return {"label": label, "d": d, "shape": shape, **fit}


def aggregate(frames: list[dict]):
    """Per-cell median across frames -- the content term averages out, the field
    term does not. Median rather than mean because a cell that lands on water or
    bare soil scores near zero and would drag a mean with it.

    Returns the aggregate field, the per-cell interquartile spread (how hard the
    terrain is fighting us) and the per-cell count of clip-rejected frames.
    """
    stack = np.stack([log_field(f["scores"], f["clip"]) for f in frames])
    field = np.nanmedian(stack, axis=0)
    spread = np.nanpercentile(stack, 75, axis=0) - np.nanpercentile(stack, 25, axis=0)
    dropped = np.isnan(stack).sum(axis=0)
    return field, spread, dropped


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 8:
        return float("nan")
    return float(np.corrcoef(a[ok], b[ok])[0, 1])


def reproducibility(by_line: dict[int, list[dict]]) -> None:
    """Does the map repeat? Two questions, and they answer different things.

    Split-half *within* a line asks whether the structure beat the terrain
    noise at all -- if odd sweeps and even sweeps agree, 9-12 frames were
    enough to average the ground away. Line *against* line asks the question
    that actually matters: a fixed optical field shape must be identical over
    two patches of ground 5 km apart at the same mirror position. Structure
    that passes the first test and fails the second is real but not optics.
    """
    print("\n=== reproducibility ===")
    halves = {}
    for line, frames in sorted(by_line.items()):
        odd = aggregate(frames[0::2])[0]
        even = aggregate(frames[1::2])[0]
        halves[line] = (odd, even)
        print(f"  line {line}: split-half r = {_corr(odd, even):+.3f} "
              f"({len(frames[0::2])} vs {len(frames[1::2])} sweeps)   "
              f"D odd={centre_edge_d(odd):+.3f}  D even={centre_edge_d(even):+.3f}")
    lines = sorted(halves)
    for i, a in enumerate(lines):
        for b in lines[i + 1:]:
            fa = aggregate(by_line[a])[0]
            fb = aggregate(by_line[b])[0]
            print(f"  line {a} vs line {b}: r = {_corr(fa, fb):+.3f}  <- must be "
                  f"high for the shape to be the lens")


def scan(paths: list[Path], rows: int, cols: int, tile: int) -> list[dict]:
    import tifffile

    out = []
    for i, p in enumerate(paths, 1):
        with tifffile.TiffFile(p) as t:
            meta = json.loads(t.pages[0].tags[270].value)
        img = tifffile.memmap(p, mode="r")
        scores, clip = grid_scores(img, rows, cols, tile)
        del img
        out.append({
            "path": str(p),
            "line": meta["line"],
            "sweep": meta["sweep"],
            "frame_in_sweep": meta["frame_in_sweep"],
            "scores": scores,
            "clip": clip,
        })
        print(f"  [{i}/{len(paths)}] L{meta['line']} S{meta['sweep']} "
              f"F{meta['frame_in_sweep']}  median={np.median(scores):8.1f}  "
              f"clipped cells={int((clip > CLIP_REJECT).sum())}  "
              f"worst clip={100 * clip.max():.2f}%")
    return out


def write_csv(rows_out: list[dict], path: Path) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["line", "sweep", "frame_in_sweep", "gy", "gx", "score", "clip_frac"])
        for r in rows_out:
            for (gy, gx), v in np.ndenumerate(r["scores"]):
                w.writerow([r["line"], r["sweep"], r["frame_in_sweep"], gy, gx,
                            f"{v:.4f}", f"{r['clip'][gy, gx]:.6f}"])


def read_csv(path: Path) -> list[dict]:
    """Reload a previous scan, so re-analysis does not re-read 33 GB off H:."""
    cells = defaultdict(dict)
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            key = (int(r["line"]), int(r["sweep"]), int(r["frame_in_sweep"]))
            cells[key][(int(r["gy"]), int(r["gx"]))] = (
                float(r["score"]), float(r["clip_frac"]))
    out = []
    for (line, sweep, fis), grid in sorted(cells.items()):
        rows = max(gy for gy, _ in grid) + 1
        cols = max(gx for _, gx in grid) + 1
        scores = np.zeros((rows, cols))
        clip = np.zeros((rows, cols))
        for (gy, gx), (s, c) in grid.items():
            scores[gy, gx], clip[gy, gx] = s, c
        out.append({"line": line, "sweep": sweep, "frame_in_sweep": fis,
                    "scores": scores, "clip": clip})
    return out


def write_png(fields: dict[str, np.ndarray], path: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("  (matplotlib missing, skipping PNG)")
        return
    n = len(fields)
    fig, axes = plt.subplots(1, n, figsize=(6.2 * n, 4.6), squeeze=False)
    lim = max(float(np.abs(f).max()) for f in fields.values())
    for ax, (label, field) in zip(axes[0], fields.items()):
        im = ax.imshow(100 * field, cmap="RdBu_r", vmin=-100 * lim, vmax=100 * lim)
        for (gy, gx), v in np.ndenumerate(field):
            ax.text(gx, gy, f"{100 * v:.0f}", ha="center", va="center", fontsize=6)
        ax.set_title(f"{label}\nD={centre_edge_d(field):+.3f}", fontsize=10)
        ax.set_xlabel("across-track cell")
        ax.set_ylabel("along-track cell")
        fig.colorbar(im, ax=ax, label="100 x ln(score) vs frame median")
    fig.suptitle("Across-field sharpness, flight frames at mirror F7", fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    print(f"  wrote {path}")


def _blurred_field(base: np.ndarray, sigma_at_edge: float) -> np.ndarray:
    """Synthesise a frame whose blur grows (or shrinks) with radius."""
    h, w = base.shape
    gy, gx = np.mgrid[0:h, 0:w]
    r = np.hypot((gy - h / 2) / (h / 2), (gx - w / 2) / (w / 2)) / np.sqrt(2)
    out = np.empty_like(base)
    edges = np.linspace(0, 1, 6)
    for lo, hi in zip(edges[:-1], edges[1:]):
        sigma = abs(sigma_at_edge) * (lo + hi) / 2
        if sigma_at_edge < 0:
            sigma = abs(sigma_at_edge) - sigma
        src = base if sigma < 0.05 else cv2.GaussianBlur(base, (0, 0), sigma)
        m = (r >= lo) & (r < hi if hi < 1 else r <= 2)
        out[m] = src[m]
    return out


def _run(img, label: str) -> dict:
    scores, clip = grid_scores(img, 5, 7, 256)
    field, spread, dropped = aggregate([{"scores": scores, "clip": clip}])
    return describe(field, label, spread, dropped)


def _self_check() -> None:
    """Plant a known dome, a known bowl and a flat field; recover all three.

    Then plant the trap the operator warned about: a flat field whose centre is
    clipped. Without rejection that reads as a bowl, which is the false positive
    this whole measurement is exposed to.
    """
    rng = np.random.default_rng(7)
    base = rng.normal(2**14, 2000, (1536, 2048)).astype(np.float32)
    base = cv2.GaussianBlur(base, (0, 0), 0.8)  # texture with scale, not pure noise

    flat = _run(base, "self-check: flat")
    dome = _run(_blurred_field(base, 2.5), "self-check: planted DOME")
    bowl = _run(_blurred_field(base, -2.5), "self-check: planted BOWL")

    assert abs(flat["d"]) < 0.10, flat["d"]
    assert flat["curv_x"] + flat["curv_y"] > -0.15, flat
    assert dome["d"] > 0.5 and dome["curv_x"] + dome["curv_y"] < -0.5, dome
    assert bowl["d"] < -0.5 and bowl["curv_x"] + bowl["curv_y"] > 0.5, bowl

    # Flat sharpness everywhere, but the middle third is driven into the ceiling.
    blown = base.copy()
    blown[512:1024, 680:1368] = np.maximum(blown[512:1024, 680:1368], 0) + 2**16
    blown = np.clip(blown, 0, 65532)
    scores, clip = grid_scores(blown, 5, 7, 256)
    naive = describe(log_field(scores), "self-check: clipped centre, NOT rejected")
    guarded = _run(blown, "self-check: clipped centre, rejected")
    assert naive["d"] < -0.5, f"clipping should have faked a bowl: {naive['d']}"
    assert np.isnan(guarded["d"]) or abs(guarded["d"]) < 0.10, guarded["d"]
    assert (clip > CLIP_REJECT).sum() >= 3, clip

    print("\nself-check OK: flat/dome/bowl all recovered, and a clipped centre "
          "fakes a bowl (D=%+.2f) until the clip guard drops those cells" % naive["d"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("glob", nargs="?", help="glob of flight TIFs")
    ap.add_argument("--rows", type=int, default=ROWS)
    ap.add_argument("--cols", type=int, default=COLS)
    ap.add_argument("--tile", type=int, default=TILE)
    ap.add_argument("--csv", type=Path)
    ap.add_argument("--png", type=Path)
    ap.add_argument("--from-csv", type=Path, help="re-analyse a previous scan")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()

    if args.self_check:
        _self_check()
        return
    if args.from_csv:
        scanned = read_csv(args.from_csv)
        print(f"reloaded {len(scanned)} frames from {args.from_csv}")
    elif args.glob:
        paths = sorted(Path(args.glob).parent.glob(Path(args.glob).name)
                       if Path(args.glob).is_absolute() else Path().glob(args.glob))
        if not paths:
            ap.error(f"no files matched {args.glob}")
        print(f"scoring {len(paths)} frames on a {args.rows}x{args.cols} grid of "
              f"{args.tile}px tiles")
        scanned = scan(paths, args.rows, args.cols, args.tile)
        if args.csv:
            write_csv(scanned, args.csv)
            print(f"  wrote {args.csv}")
    else:
        ap.error("need a glob of TIFs, --from-csv, or --self-check")

    by_line = defaultdict(list)
    for r in scanned:
        by_line[r["line"]].append(r)

    groups = {f"line {ln} ({len(by_line[ln])} sweeps)": by_line[ln] for ln in sorted(by_line)}
    if len(groups) > 1:
        groups["both lines"] = scanned

    fields = {}
    for label, frames in groups.items():
        field, spread, dropped = aggregate(frames)
        describe(field, label, spread, dropped, len(frames))
        fields[label] = field
    reproducibility(by_line)
    if args.png:
        write_png(fields, args.png)


if __name__ == "__main__":
    main()
