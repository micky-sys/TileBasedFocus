"""Where each part of the field comes to focus, in ENCODER COUNTS.

The flight estimator (``field_pairs.py``) measures the field at one fixed lens
position, so it answers "which part of the frame is sharper" in log-score units.
That is the wrong unit for ``park_bias_counts``, which is a distance along the
focus axis.

The bench fieldmap sweeps already hold the missing half: five ROIs scored while
the lens walks through focus. Fit a parabola to ln(score) per ROI and its vertex
IS that ROI's best-focus position. The spread of those vertices is the field
curvature in counts, with no conversion factor to assume.

The five ROIs land on the same normalised field coordinates the flight fit uses
(centre, and +-0.67 along each axis), so the two measurements are directly
comparable in shape -- though the bench is at finite conjugate and the flight at
infinity, and field curvature is not the same at both.

    py -3 tools/fieldmap_counts.py
    py -3 tools/fieldmap_counts.py --self-check
"""

from __future__ import annotations

import argparse
import csv
import glob
from pathlib import Path

import numpy as np

ROIS = ("centre", "top", "bottom", "left", "right")
# normalised field position of each ROI, from the roi header in the logs
FIELD_XY = {"centre": (0.0, 0.0), "top": (0.0, -0.666), "bottom": (0.0, +0.666),
            "left": (-0.666, 0.0), "right": (+0.666, 0.0)}
MIN_SPAN = 1200     # counts; a sweep shorter than this cannot locate a vertex
MIN_SAMPLES = 25
BAND = 0.7         # fraction of the half-span kept around the peak for the fit


def load(path: Path) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    pos: list[float] = []
    cols: dict[str, list[float]] = {k: [] for k in ROIS}
    with open(path, newline="") as fh:
        rows = [r for r in fh if not r.startswith("#")]
    for r in csv.DictReader(rows):
        try:
            p = float(r["pos_counts"])
            vals = {k: float(r[k]) for k in ROIS}
        except (KeyError, TypeError, ValueError):
            continue
        if any(v <= 0 for v in vals.values()):
            continue
        pos.append(p)
        for k in ROIS:
            cols[k].append(vals[k])
    return np.array(pos), {k: np.array(v) for k, v in cols.items()}


def vertex(pos: np.ndarray, score: np.ndarray) -> tuple[float, float, float] | None:
    """Best-focus position, curvature `a`, and peak ln score for one ROI.

    Fitted near the peak only: the tails of a focus curve are not parabolic and
    including them flattens `a` and drags the vertex toward the sweep centre.
    """
    if len(pos) < MIN_SAMPLES or np.ptp(pos) < MIN_SPAN:
        return None
    ln = np.log(score)
    # smooth before picking the peak, so shot noise on one frame cannot set it
    order = np.argsort(pos)
    p, l = pos[order], ln[order]
    k = max(3, len(l) // 25) | 1
    smooth = np.convolve(l, np.ones(k) / k, mode="same")
    guess = p[int(np.argmax(smooth[k:-k])) + k] if len(p) > 3 * k else p[np.argmax(l)]
    band = np.abs(p - guess) <= BAND * np.ptp(p) / 2
    if band.sum() < MIN_SAMPLES:
        return None
    c = np.polyfit(p[band] - guess, l[band], 2)
    a = -c[0]
    if a <= 0:
        return None                      # curve opens upward: no peak in range
    off = c[1] / (2 * a)                 # vertex of a*(-x^2) + c1*x + c2
    if abs(off) > np.ptp(p):
        return None                      # vertex extrapolated outside the sweep
    return float(guess + off), float(a), float(c[2] + a * off * off)


def analyse(path: Path) -> dict | None:
    pos, cols = load(path)
    if len(pos) == 0:
        return None
    out = {}
    for k in ROIS:
        v = vertex(pos, cols[k])
        if v is None:
            return None                  # need all five to talk about a field
        out[k] = v
    return {"path": path, "span": float(np.ptp(pos)), "n": len(pos), "roi": out}


def report(results: list[dict]) -> None:
    print(f"\n{'sweep':>36} {'span':>6} "
          + " ".join(f"{k:>9}" for k in ROIS) + f" {'sag':>7}")
    print(f"{'':>36} {'':>6} " + " ".join(f"{'counts':>9}" for _ in ROIS)
          + f" {'counts':>7}")
    sags, per_roi = [], {k: [] for k in ROIS}
    for r in results:
        c0 = r["roi"]["centre"][0]
        rel = {k: r["roi"][k][0] - c0 for k in ROIS}
        edges = [rel[k] for k in ROIS if k != "centre"]
        sag = float(np.mean(edges))
        sags.append(sag)
        for k in ROIS:
            per_roi[k].append(rel[k])
        print(f"{r['path'].name[:36]:>36} {r['span']:6.0f} "
              + " ".join(f"{rel[k]:+9.0f}" for k in ROIS) + f" {sag:+7.0f}")

    print(f"\n{len(results)} usable sweeps. Best-focus position relative to the "
          f"centre ROI, in counts:")
    for k in ROIS:
        v = np.array(per_roi[k])
        print(f"  {k:<7} median {np.median(v):+7.0f}   spread (IQR) "
              f"{np.percentile(v, 75) - np.percentile(v, 25):6.0f}   "
              f"n={len(v)}")
    sag = np.array(sags)
    print(f"\n  mean edge sag: median {np.median(sag):+.0f} counts, "
          f"IQR {np.percentile(sag, 75) - np.percentile(sag, 25):.0f}, "
          f"range [{sag.min():+.0f}, {sag.max():+.0f}]")
    a = np.array([r["roi"]["centre"][1] for r in results])
    print(f"  centre curve a: median {np.median(a):.3e} per count^2 -> "
          f"598 counts of defocus costs {np.median(a) * 598**2:.4f} log units "
          f"({100 * (1 - np.exp(-np.median(a) * 598**2)):.1f}% of score)")
    sign = "BEHIND" if np.median(sag) > 0 else "IN FRONT OF"
    print(f"\n  verdict: the edges focus {abs(np.median(sag)):.0f} counts {sign} "
          f"the centre.")
    print("  A park offset can only ever trade one against the other; it cannot "
          "\n  make both sharp at once.")


def _self_check() -> None:
    """Plant a known field in counts and see if the vertices come back."""
    rng = np.random.default_rng(5)
    a_true, sag_true, centre_focus = 2.4e-8, -900.0, 1500.0
    pos = np.linspace(centre_focus - 3000, centre_focus + 3000, 400)
    cols = {}
    for k in ROIS:
        focus = centre_focus + (0.0 if k == "centre" else sag_true)
        ln = 6.0 - a_true * (pos - focus) ** 2
        cols[k] = np.exp(ln + rng.normal(0, 0.05, len(pos)))   # 5% score noise
    got = {k: vertex(pos, cols[k]) for k in ROIS}
    assert all(got.values()), "self-check could not locate a vertex"
    c0 = got["centre"][0]
    for k in ROIS:
        want = 0.0 if k == "centre" else sag_true
        err = got[k][0] - c0 - want
        print(f"  {k:<7} vertex {got[k][0] - c0:+8.1f} counts "
              f"(planted {want:+.0f}, error {err:+.1f})")
        assert abs(err) < 150, f"{k} vertex off by {err:.0f} counts"
    a_err = abs(got["centre"][1] / a_true - 1)
    print(f"  centre a {got['centre'][1]:.3e} vs planted {a_true:.3e} "
          f"({100 * a_err:.1f}% off)")
    assert a_err < 0.25, "curve width badly recovered"
    print("\nself-check OK: a planted -900 count edge sag is recovered to "
          "within 150 counts through 5% score noise")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("glob", nargs="?", default="host/Session logs/*fieldmap*.csv")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()
    if args.self_check:
        _self_check()
        return
    paths = sorted(Path(p) for p in glob.glob(args.glob))
    if not paths:
        ap.error(f"no fieldmap logs matched {args.glob}")
    results = [r for r in (analyse(p) for p in paths) if r]
    print(f"{len(paths)} fieldmap logs, {len(results)} with a locatable peak in "
          f"all five ROIs")
    if not results:
        print("  none of these sweeps bracket focus in every ROI -- nothing to say")
        return
    report(results)


if __name__ == "__main__":
    main()
