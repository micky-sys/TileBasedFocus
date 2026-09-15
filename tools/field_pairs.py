"""Field curvature from overlapping sweeps: the same ground at two field positions.

``field_flatness.py`` averaged different terrain at each field position and hit a
wall -- content swings the focus metric 200:1, so 25 sweeps left +-0.1 log units
of noise per cell and any field shape under ~10-15% was invisible.

This estimator removes content instead of averaging it. Overlapping frames image a
given patch of ground TWICE with the lens untouched, at two different places in
the field. Write the log score of a patch as

    ln s = c(patch) + f(field position)

and the difference between the two views cancels ``c`` exactly::

    ln s_a - ln s_b = f(y_a, x_a) - f(y_b, x_b)

What is left is a linear equation in the field shape alone. Fitting f as a
quadratic (tilt + curvature + astigmatism, no constant -- it cancels too) turns
a few hundred matched patches into a least-squares estimate of the curvature
WITH an error bar, which is the thing the averaging approach could not deliver.

Two independent overlaps feed it, and needing both is not a detail -- a single
shift DIRECTION cannot separate tilt from curvature at any signal-to-noise (see
``fit``). Consecutive sweeps at one mirror step overlap ~59%, displaced ~5000 px
along the columns; adjacent mirror steps within ONE sweep overlap ~18%, displaced
~7800 px along the rows. Those two directions are nearly orthogonal.

    py -3 tools/field_pairs.py "H:/MickyCrazy/*.tif" --csv pairs.csv
    py -3 tools/field_pairs.py --self-check

Registration is integer and even-aligned throughout, never interpolated. A
sub-pixel resample would low-pass exactly the high-frequency detail being
measured -- it would make the second view of every patch softer than the first
and manufacture a field shape out of nothing. The cost of refusing to resample
is up to 1 px of residual misalignment, which decorrelates content slightly and
is absorbed as noise rather than bias.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "host"))
sys.path.insert(0, str(ROOT / "tools"))

from field_flatness import CLIP_LEVEL, FLAT_BAND, GREEN_PHASE  # noqa: E402

COARSE_DS = 8      # downsample for the whole-frame shift search
REFINE_DS = 2      # downsample for the per-patch refine; sets the 2 px granularity
TEMPLATE = 512     # full-res template size for the refine
PATCH = 768        # full-res box actually scored
REFINE_WIN = 96    # +-px searched around the affine's prediction
MIN_NCC = 0.60     # below this the two views are not the same ground
MIN_SCORE = 8.0    # a patch too bland to focus on tells us nothing
MIN_INLIERS = 40   # RANSAC support below this is farmland, not an overlap
SCALE_BAND = 0.03  # same camera, same altitude: a fit off this is degenerate
COEFS = ("tilt_x", "tilt_y", "curv_x", "curv_y", "astig")
FRAME_RE = re.compile(r"_L(\d+)_S(\d+)_F(\d+)\.tif$", re.I)


def norm_xy(row: float, col: float, h: int, w: int) -> tuple[float, float]:
    """Field position normalised to +-1, x across the long (along-track) axis."""
    return (col - w / 2) / (w / 2), (row - h / 2) / (h / 2)


def design_row(ya, xa, yb, xb, h, w) -> np.ndarray:
    """f(a) - f(b) for f = tilt_x*x + tilt_y*y + curv_x*x^2 + curv_y*y^2 + astig*xy.

    The constant term is absent because it cancels in the difference -- which is
    also why this estimator needs no absolute reference and no flat-field.
    """
    xa_, ya_ = norm_xy(ya, xa, h, w)
    xb_, yb_ = norm_xy(yb, xb, h, w)
    return np.array([
        xa_ - xb_,
        ya_ - yb_,
        xa_ * xa_ - xb_ * xb_,
        ya_ * ya_ - yb_ * yb_,
        xa_ * ya_ - xb_ * yb_,
    ])


def sub_grid(plane, y0: int, x0: int, n: int) -> np.ndarray:
    """Native green sub-grid of an even-aligned full-res box (no interpolation)."""
    dy, dx = GREEN_PHASE
    return np.ascontiguousarray(plane[y0 : y0 + n, x0 : x0 + n][dy::2, dx::2])


_SIFT = None
_MATCHER = None


def _features():
    global _SIFT, _MATCHER
    if _SIFT is None:
        _SIFT = cv2.SIFT_create(nfeatures=6000)
        _MATCHER = cv2.BFMatcher()
    return _SIFT, _MATCHER


def _feats(frame: dict):
    """SIFT keypoints/descriptors, computed once per frame rather than per pair.

    Each frame takes part in up to 5 pairs (two mirror neighbours, plus its own
    mirror step across the sweep gaps), and detection is ~1.5 s -- caching turns
    ~10 minutes of repeat work into ~3 on a 129-frame set.
    """
    if "feat" not in frame:
        sift, _ = _features()
        frame["feat"] = sift.detectAndCompute(frame["u8"], None)
    return frame["feat"]


def global_affine(a: dict, b: dict) -> tuple[np.ndarray | None, int, float]:
    """Rotation + scale + shift taking frame A's pixels to B's, and its support.

    Translation alone is not enough, which cost real time to learn. A mirror STEP
    turns the field by ~4.9 deg while moving it ~7800 rows, and 4.9 deg walks the
    corner of a 512 px template by ~31 px -- that drops normalised correlation
    into the farmland noise floor and makes frames that visibly overlap look like
    they share no ground at all. Even a same-mirror sweep pair carries up to
    ~0.8 deg of yaw, enough to displace the far end of a 13400 px frame by
    ~110 px, i.e. right out of REFINE_WIN: a constant (dy, dx) was quietly losing
    the patches furthest from wherever the votes were taken.

    Features + RANSAC rather than a correlation peak, because the inlier count is
    itself the evidence of overlap. Repetitive farmland will false-match a single
    large template at ncc 0.9, but it will not put hundreds of features onto one
    consistent transform. The scale window is the second guard: the no-overlap
    cases that still scraped together a few dozen inliers all came back with
    absurd scale (0.006 and 0.000), so anything off unity is a degenerate fit.
    """
    _, matcher = _features()
    ka, da = _feats(a)
    kb, db = _feats(b)
    if da is None or db is None or len(da) < MIN_INLIERS or len(db) < MIN_INLIERS:
        return None, 0, 0.0
    good = [m for m, n in matcher.knnMatch(da, db, k=2)
            if m.distance < 0.75 * n.distance]
    if len(good) < MIN_INLIERS:
        return None, len(good), 0.0
    pa = np.float32([ka[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    pb = np.float32([kb[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    M, inl = cv2.estimateAffinePartial2D(pa, pb, method=cv2.RANSAC,
                                         ransacReprojThreshold=3.0,
                                         maxIters=5000, confidence=0.999)
    if M is None or inl is None:
        return None, 0, 0.0
    n_inl = int(inl.sum())
    scale = float(np.hypot(M[0, 0], M[1, 0]))
    if n_inl < MIN_INLIERS or abs(scale - 1.0) > SCALE_BAND:
        return None, n_inl, scale
    M = M.astype(np.float64).copy()
    M[:, 2] *= COARSE_DS       # translation to full-res px; the 2x2 block is scale-free
    return M, n_inl, scale


def predict(M, ya: int, xa: int) -> tuple[int, int]:
    """Top-left of A's patch box mapped into B, even-aligned to keep Bayer phase."""
    cx, cy = xa + PATCH / 2.0, ya + PATCH / 2.0
    xb = M[0, 0] * cx + M[0, 1] * cy + M[0, 2]
    yb = M[1, 0] * cx + M[1, 1] * cy + M[1, 2]
    return int(round(yb - PATCH / 2.0)) & ~1, int(round(xb - PATCH / 2.0)) & ~1


def _template(a2, cy2: int, cx2: int, n: int, angle: float):
    """A's patch centre as a template, turned to match B's orientation.

    Cut from a 1.5x box so the rotation pulls in real neighbouring ground rather
    than padding at the corners. This resamples the TEMPLATE only -- the pixels
    that get scored are never interpolated, since a resample would low-pass
    exactly the high-frequency detail being measured.
    """
    m = int(n * 1.5) // 2 * 2
    y0, x0 = cy2 - m // 2, cx2 - m // 2
    if y0 < 0 or x0 < 0 or y0 + m > a2.shape[0] or x0 + m > a2.shape[1]:
        return None
    box = a2[y0 : y0 + m, x0 : x0 + m]
    if abs(angle) > 0.2:
        rot = cv2.getRotationMatrix2D(((m - 1) / 2.0, (m - 1) / 2.0), -angle, 1.0)
        box = cv2.warpAffine(box, rot, (m, m), flags=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_REPLICATE)
    o = (m - n) // 2
    return np.ascontiguousarray(box[o : o + n, o : o + n])


def refine(a2, b2, ya: int, xa: int, yb0: int, xb0: int,
           angle: float) -> tuple[int, int, float]:
    """Local re-match, so terrain relief and crab don't rely on the global fit."""
    n = TEMPLATE // REFINE_DS
    win = REFINE_WIN // REFINE_DS
    off = (PATCH - TEMPLATE) // 2 // REFINE_DS      # template is centred in the patch
    tpl = _template(a2, (ya + PATCH // 2) // REFINE_DS,
                    (xa + PATCH // 2) // REFINE_DS, n, angle)
    if tpl is None:
        return 0, 0, 0.0
    y0, x0 = yb0 // REFINE_DS + off - win, xb0 // REFINE_DS + off - win
    if y0 < 0 or x0 < 0:
        return 0, 0, 0.0
    search = b2[y0 : y0 + n + 2 * win, x0 : x0 + n + 2 * win]
    if search.shape[0] < n or search.shape[1] < n:
        return 0, 0, 0.0
    res = cv2.matchTemplate(search, tpl, cv2.TM_CCOEFF_NORMED)
    _, ncc, _, loc = cv2.minMaxLoc(res)
    yb = (y0 + loc[1] - off) * REFINE_DS
    xb = (x0 + loc[0] - off) * REFINE_DS
    return yb & ~1, xb & ~1, float(ncc)


def pairs_for(a: dict, b: dict, *, step: int = 900) -> list[dict]:
    """Every usable (same ground, two field positions) observation for one pair."""
    from capture.autofocus import focus_measure_green

    a_plane, b_plane = a["plane"], b["plane"]
    h, w = a_plane.shape
    M, n_inl, _ = global_affine(a, b)
    if M is None:
        return []
    angle = float(np.degrees(np.arctan2(M[1, 0], M[0, 0])))
    a2, b2 = a["p2"], b["p2"]
    out = []
    for ya in range(0, h - PATCH, step):
        for xa in range(0, w - PATCH, step):
            yb0, xb0 = predict(M, ya, xa)
            if not (0 <= yb0 <= h - PATCH and 0 <= xb0 <= w - PATCH):
                continue      # A's patch falls outside the overlap; skip the match
            yb, xb, ncc = refine(a2, b2, ya, xa, yb0, xb0, angle)
            if ncc < MIN_NCC:
                continue
            if not (0 <= yb <= h - PATCH and 0 <= xb <= w - PATCH):
                continue
            ga = sub_grid(a_plane, ya, xa, PATCH)
            gb = sub_grid(b_plane, yb, xb, PATCH)
            if ga.max() >= CLIP_LEVEL or gb.max() >= CLIP_LEVEL:
                continue  # one saturated roof flattens a patch; drop the pair
            sa, sb = focus_measure_green(ga), focus_measure_green(gb)
            if sa < MIN_SCORE or sb < MIN_SCORE:
                continue
            out.append({
                "ya": ya + PATCH // 2, "xa": xa + PATCH // 2,
                "yb": yb + PATCH // 2, "xb": xb + PATCH // 2,
                "score_a": sa, "score_b": sb,
                "dlog": float(np.log(sa / sb)), "ncc": ncc, "h": h, "w": w,
                "angle": angle, "inliers": n_inl,
            })
    return out


def fit(rows: list[dict], sigma_clip: float = 2.5, iters: int = 4) -> dict:
    """Robust least squares for the field shape, with standard errors.

    Iterated sigma clipping because a handful of observations are not the model:
    a mis-registration, or a tall building whose parallax puts different pixels
    in the two views. Those are outliers, not evidence, and one of them can pull
    a quadratic coefficient a long way.

    Identifiability matters more here than robustness, and it is not obvious.
    For a pair whose shift is a constant (dx, dy) in normalised units, writing
    xb = xa + dx and yb = ya + dy collapses the model to

        f(a) - f(b) = const + xa*(-2*curv_x*dx - astig*dy)
                            + ya*(-2*curv_y*dy - astig*dx)

    so ONE pair yields only three numbers and the tilt terms vanish into the
    constant -- curvature cannot be separated from tilt within a single pair, at
    any signal-to-noise. It becomes separable only by pooling pairs whose shift
    DIRECTION differs, which this flight supplies (the row shift runs 689 to
    3408 px across consecutive sweeps while the column shift stays near -5500).
    The covariance therefore goes through a pseudo-inverse: if the geometry on
    offer does not pin a coefficient down, the honest output is a huge error bar,
    not a crash or a confident wrong number.
    """
    if len(rows) < 30:
        return {"n": len(rows), "ok": False}
    design = np.array([design_row(r["ya"], r["xa"], r["yb"], r["xb"], r["h"], r["w"])
                       for r in rows])
    delta = np.array([r["dlog"] for r in rows])
    keep = np.ones(len(delta), bool)
    coef = np.zeros(5)
    for _ in range(iters):
        coef, *_ = np.linalg.lstsq(design[keep], delta[keep], rcond=None)
        resid = delta - design @ coef
        med = np.median(resid[keep])
        mad = 1.4826 * np.median(np.abs(resid[keep] - med))
        keep = np.abs(resid - med) < sigma_clip * max(mad, 1e-9)
        if keep.sum() < 20:
            break
    resid = delta - design @ coef
    dof = max(int(keep.sum()) - 5, 1)
    sigma2 = float((resid[keep] ** 2).sum() / dof)
    normal = design[keep].T @ design[keep]
    cov = sigma2 * np.linalg.pinv(normal)
    se = np.sqrt(np.clip(np.diag(cov), 0, None))
    cond = float(np.linalg.cond(normal))
    # curv_x + curv_y is the dome/bowl number; its error needs the covariance,
    # not the two standard errors added -- the two are strongly correlated.
    curv = float(coef[2] + coef[3])
    curv_se = float(np.sqrt(cov[2, 2] + cov[3, 3] + 2 * cov[2, 3]))
    out = {"n": len(rows), "n_kept": int(keep.sum()), "ok": True,
           "curv": curv, "curv_se": curv_se, "cond": cond,
           "resid_sd": float(np.sqrt(sigma2))}
    out.update({k: float(v) for k, v in zip(COEFS, coef)})
    out.update({f"{k}_se": float(v) for k, v in zip(COEFS, se)})
    return out


def read_csv(path: Path) -> list[dict]:
    """Reload paired observations, so re-inference does not re-read 33 GB."""
    ints = {"ya", "xa", "yb", "xb", "h", "w", "line", "pair", "gap", "mirror",
            "inliers"}
    out = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            out.append({k: (int(v) if k in ints else float(v)) for k, v in r.items()})
    return out


def cluster_bootstrap(rows: list[dict], n_boot: int = 400, seed: int = 3) -> dict:
    """Error bar on the curvature that resamples PAIRS, not patches.

    The least-squares standard error assumes independent observations. These are
    not: the ~40 patches from one sweep pair share a single registration and a
    single shift geometry, so an error in either moves all of them together. With
    24 pairs the effective sample size is nearer 24 than 931, and resampling
    whole pairs is what measures that. Where the two disagree, this one is right.
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["line"], r["pair"])].append(r)
    keys = list(groups)
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(n_boot):
        picked = [groups[keys[i]] for i in rng.integers(0, len(keys), len(keys))]
        flat = [r for g in picked for r in g]
        res = fit(flat)
        if res.get("ok"):
            draws.append(res)
    if len(draws) < 20:
        return {"ok": False, "n_groups": len(keys)}
    arr = np.array([[d[k] for k in COEFS] + [d["curv"]] for d in draws])
    out = {"ok": True, "n_groups": len(keys), "n_draws": len(draws)}
    for i, name in enumerate(list(COEFS) + ["curv"]):
        col = arr[:, i]
        out[f"{name}_mean"] = float(col.mean())
        out[f"{name}_sd"] = float(col.std(ddof=1))
        out[f"{name}_lo"] = float(np.percentile(col, 2.5))
        out[f"{name}_hi"] = float(np.percentile(col, 97.5))
    # kept for the report's headline line
    out.update({"mean": out["curv_mean"], "sd": out["curv_sd"],
                "lo": out["curv_lo"], "hi": out["curv_hi"]})
    return out


def shape(res: dict) -> dict:
    """Classify the fitted quadratic, and say what AF3's D would inherit from it.

    Five coefficients do not read as a surface. The quadratic part is a matrix
    whose determinant settles the question the whole exercise asks: positive
    means a true bowl or dome, negative means a SADDLE -- sharper along one axis
    and softer along the perpendicular one. That distinction decides whether a
    park offset is even the right tool, because a scalar offset can shift the
    whole field but cannot flatten a saddle. Moving focus to help one axis hurts
    the other by construction.

    ``d_field`` is the same centre-minus-edges combination AF3's D metric forms,
    evaluated on the fitted surface, i.e. the part of D that is pure optics.
    """
    A = np.array([[res["curv_x"], res["astig"] / 2],
                  [res["astig"] / 2, res["curv_y"]]])
    ev, evec = np.linalg.eigh(A)
    det = float(np.linalg.det(A))
    strong = evec[:, int(np.argmax(np.abs(ev)))]
    def f(x, y):
        return (res["tilt_x"] * x + res["tilt_y"] * y + res["curv_x"] * x * x
                + res["curv_y"] * y * y + res["astig"] * x * y)
    return {
        "det": det,
        "kind": "SADDLE (astigmatic)" if det < 0 else
                ("BOWL (edges sharp)" if ev.sum() > 0 else "DOME (centre sharp)"),
        "ev_hi": float(ev.max()), "ev_lo": float(ev.min()),
        "axis_deg": float(np.degrees(np.arctan2(strong[0], strong[1]))),
        "d_field": float(f(0, 0) - np.mean([f(1, 0), f(-1, 0), f(0, 1), f(0, -1)])),
    }


def report(res: dict, label: str, boot: dict | None = None) -> None:
    print(f"\n=== {label} ===")
    if not res.get("ok"):
        print(f"  only {res['n']} paired observations -- not enough to fit")
        return
    print(f"  {res['n_kept']} of {res['n']} paired patches kept "
          f"(residual sd {res['resid_sd']:.3f} log units)")
    print(f"  design condition number {res['cond']:.3g}"
          + ("   <- shift geometry barely separates the terms"
             if res["cond"] > 1e4 else ""))
    for k in COEFS:
        line = f"  {k:<8} {res[k]:+.4f} +- {res[k + '_se']:.4f} (per-patch)"
        if boot and boot.get("ok"):
            sd = boot[f"{k}_sd"]
            line += (f"   bootstrap +- {sd:.4f}  "
                     f"[{boot[f'{k}_lo']:+.3f}, {boot[f'{k}_hi']:+.3f}]"
                     f"  {abs(res[k]) / max(sd, 1e-9):4.1f}s")
        print(line)
    print(f"  curv_x+curv_y = {res['curv']:+.4f} +- {res['curv_se']:.4f}  "
          f"(per-patch SE, optimistic)")
    se = res["curv_se"]
    if boot and boot.get("ok"):
        se = boot["sd"]
        print(f"  cluster bootstrap over {boot['n_groups']} sweep pairs: "
              f"{boot['mean']:+.4f} +- {boot['sd']:.4f}  "
              f"95% CI [{boot['lo']:+.4f}, {boot['hi']:+.4f}]")
    sigmas = abs(res["curv"]) / max(se, 1e-9)
    sh = shape(res)
    print(f"  shape: {sh['kind']}  (det {sh['det']:+.5f}, eigenvalues "
          f"{sh['ev_hi']:+.4f} / {sh['ev_lo']:+.4f}, strong axis "
          f"{sh['axis_deg']:+.0f} deg from across-track)")
    print(f"         {100 * (np.exp(sh['ev_hi']) - 1):+.0f}% along that axis, "
          f"{100 * (np.exp(sh['ev_lo']) - 1):+.0f}% across it")
    print(f"         D inherited by AF3 from this field: {sh['d_field']:+.4f} "
          f"log units")
    if sigmas < 2.0:
        verdict = f"net curvature is {sigmas:.1f} sigma, not resolved"
    elif abs(res["curv"]) < FLAT_BAND:
        verdict = f"{sigmas:.1f} sigma but under the {FLAT_BAND} flat band"
    else:
        verdict = (f"net {'bowl' if res['curv'] > 0 else 'dome'} "
                   f"{res['curv']:+.3f} at {sigmas:.1f} sigma")
    print(f"  verdict: {verdict}")
    if sh["det"] < 0 and sigmas >= 2.0:
        print("           -- but the sign-split eigenvalues mean this is a saddle, "
              "not a bowl:\n              no single park offset can flatten it")


def _frame(plane) -> dict:
    """The three resolutions a pair needs.

    Native for scoring (never resampled), /2 to re-match each patch, /8 for the
    feature registration. The stretch is only so the matchers see a sensible
    contrast range; it cannot affect a score, which reads the native plane, and
    TM_CCOEFF_NORMED is invariant to a linear rescale anyway.

    Both reduced planes are 8-bit, and the /2 one matters: this was written
    against 3 mirror positions, but a real sweep carries ~14, so the rolling
    window in ``scan`` would hold ~28 of them. As float32 that is 128 MB each
    (3.6 GB retained); as uint8 it is 32 MB, and cheaper to build too.
    """
    p8 = plane[::COARSE_DS, ::COARSE_DS]
    lo, hi = np.percentile(p8[::4, ::4], (1, 99))   # /32 sample: same answer, 36 -> 2 ms
    gain = 255.0 / max(float(hi - lo), 1.0)

    def stretch(a):
        return np.clip((a.astype(np.float32) - lo) * gain, 0, 255).astype(np.uint8)

    return {
        "plane": plane,
        "p2": stretch(plane[::REFINE_DS, ::REFINE_DS]),
        "u8": stretch(p8),
    }


def scan(paths: list[Path], max_gap: int = 1, mirror: bool = True) -> list[dict]:
    """Pair up every frame combination that shares ground, in both geometries.

    Same mirror step, consecutive sweeps: ~59% overlap at gap 1 and ~18% at gap 2
    (the sweep step is ~186 m against a ~453 m along-track footprint); gap 3
    clears the frame. Displacement is ~5000 px along the columns.

    Adjacent mirror steps, ONE sweep: the step moves the field ~7800 rows and
    turns it ~4.9 deg, leaving ~1730 rows (~18%, about 58 m) of overlap displaced
    almost purely along the ROWS. Two steps clear the frame, so F6-F8 never
    matches -- only the neighbours do.

    That second geometry is the point of pairing mirror steps at all. One shift
    direction cannot separate tilt from curvature at any signal-to-noise (see
    ``fit``), and these two directions are nearly orthogonal. The mirror pairs
    are a bonus 0.22 s apart instead of 3 s, so sun angle and shadows are
    identical between the two views -- but they do sit at look angles ~4.7 deg
    apart, a confound the sweep pairs do not have. Hence ``--no-mirror``: the two
    geometries carry different systematics and must be comparable separately.
    """
    import tifffile

    by_line: dict[int, dict[int, dict[int, Path]]] = defaultdict(
        lambda: defaultdict(dict))
    for p in paths:
        m = FRAME_RE.search(p.name)
        if not m:
            print(f"  skipping {p.name}: no _L<line>_S<sweep>_F<frame> in the name")
            continue
        line, sweep, frame = (int(g) for g in m.groups())
        by_line[line][sweep][frame] = p

    keep = []      # memmaps must outlive the views taken from them

    def load(path):
        img = tifffile.memmap(path, mode="r")
        keep.append(img)
        return _frame(img[:, :, 1])

    rows = []

    def add(a, b, line, pair, gap, is_mirror, label):
        got = pairs_for(a, b)
        for r in got:
            r["line"], r["pair"] = line, pair
            r["gap"], r["mirror"] = gap, int(is_mirror)
        rows.extend(got)
        note = f" {got[0]['angle']:+.2f}deg {got[0]['inliers']:4d} inl" if got else ""
        print(f"  {label:<18} {len(got):3d} patches{note}   total {len(rows)}")

    for line in sorted(by_line):
        sweeps = sorted(by_line[line])
        print(f"\nline {line}: {len(sweeps)} sweeps, gaps 1..{max_gap}"
              f"{', plus mirror-step pairs' if mirror else ''}")
        # Rolling window holds one sweep's frames x (max_gap+1) sweeps. The /2
        # plane is uint8 (see _frame), so a full 14-position sweep at gap 1 is
        # ~900 MB rather than the 3.6 GB it would be in float32.
        window: list[tuple[int, dict]] = []
        for sweep in sweeps:
            cur = {f: load(p) for f, p in sorted(by_line[line][sweep].items())}
            if mirror:
                order = sorted(cur)
                for fa, fb in zip(order, order[1:]):
                    add(cur[fa], cur[fb], line,
                        1_000_000 + sweep * 100 + fa * 10 + fb, 0, True,
                        f"S{sweep} F{fa}->F{fb}")
            for prev_sweep, prev in window:
                for f in sorted(set(cur) & set(prev)):
                    add(prev[f], cur[f], line,
                        prev_sweep * 10000 + sweep * 100 + f,
                        sweep - prev_sweep, False,
                        f"F{f} S{prev_sweep}->S{sweep}")
            window.append((sweep, cur))
            window = window[-max_gap:]
            keep[:] = keep[-len(cur) * (max_gap + 1):]   # frames per sweep, not 3
    return rows


PLANT_SIGMA = 2.2      # px of blur the planted field shape reaches at the corners


def _synth(curv_sign: float, h=1600, w=2400, dy=400, dx=-1200, rot=0.0):
    """Two views of one synthetic ground, each blurred by a PLANTED field shape.

    The planted shape is the only thing that differs between the two views of a
    patch, so if the estimator cannot recover its sign and rough size here, it
    cannot be trusted on the flight frames. ``rot`` turns the second view, which
    is what a mirror step does; ``curv_sign=0`` plants no shape at all, for a
    null test.
    """
    rng = np.random.default_rng(11)
    pad = int(max(abs(dy), abs(dx)) + (abs(rot) > 0) * max(h, w) * 0.1)
    gh, gw = h + abs(dy) + pad, w + abs(dx) + pad
    # Detail at many scales, not one. Single-scale noise has no scale-space
    # structure, so SIFT found 144 keypoints on it and only 13 survived the ratio
    # test -- the registration starved on a scene no real terrain resembles.
    # Summing one noise field at several blurs gives 1/f-ish ground that
    # registers like farmland while keeping the fine detail the score needs.
    base = rng.normal(0.0, 1.0, (gh, gw)).astype(np.float32)
    ground = np.zeros((gh, gw), np.float32)
    for sigma, weight in ((0.8, 1.0), (2.0, 1.1), (5.0, 1.4), (13.0, 1.9), (34.0, 2.6)):
        ground += weight * cv2.GaussianBlur(base, (0, 0), sigma)
    ground /= max(float(ground.std()), 1e-6)
    # Posterised mid-scale noise = sharp-edged parcels with corners, which is
    # both what farmland looks like from 3 km and what SIFT can actually pin
    # down. Without them the ground was too self-similar for the ratio test: a
    # rotated pair fell to ONE inlier and matched nothing.
    parcels = cv2.GaussianBlur(base, (0, 0), 18.0)
    ground += 2.0 * np.round(parcels / max(float(parcels.std()), 1e-6))
    ground = (2**14 + 2000 * ground).astype(np.float32)
    gy0a, gx0a = (pad // 2 + (dy if dy > 0 else 0), pad // 2)
    gy0b, gx0b = gy0a - dy, gx0a - dx

    def view(src, gy0, gx0):
        raw = src[gy0 : gy0 + h, gx0 : gx0 + w]
        yy, xx = np.mgrid[0:h, 0:w]
        x, y = (xx - w / 2) / (w / 2), (yy - h / 2) / (h / 2)
        r2 = np.clip(x * x + y * y, 0, 2) / 2
        # sigma rises with radius for a dome (centre sharp), falls for a bowl
        target = abs(curv_sign) * PLANT_SIGMA * (r2 if curv_sign < 0 else 1.0 - r2)
        out = np.empty_like(raw)
        edges = np.linspace(float(target.min()), float(target.max()) + 1e-6, 8)
        for lo, hi in zip(edges[:-1], edges[1:]):
            s = 0.5 * (lo + hi)
            blurred = raw if s < 0.05 else cv2.GaussianBlur(raw, (0, 0), s)
            m = (target >= lo) & (target < hi)
            out[m] = blurred[m]
        return np.ascontiguousarray(out)

    ground_b = ground
    if rot:
        centre = (gx0b + w / 2.0, gy0b + h / 2.0)
        ground_b = cv2.warpAffine(ground, cv2.getRotationMatrix2D(centre, -rot, 1.0),
                                  (gw, gh), flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_REFLECT)
    return view(ground, gy0a, gx0a), view(ground_b, gy0b, gx0b)


def _self_check() -> None:
    global PATCH, TEMPLATE, REFINE_WIN
    PATCH, TEMPLATE, REFINE_WIN = 256, 192, 32
    # Two shift geometries, because one is provably not enough: with a single
    # constant shift the tilt terms vanish into a constant and curvature is not
    # identifiable. Mirrors the real flight, where the row shift varies sweep to
    # sweep while the column shift does not.
    shifts = ((400, -1200), (1100, -900))
    results = {}
    for name, sign in (("planted DOME", -1.0), ("planted BOWL", +1.0)):
        rows = []
        for dy, dx in shifts:
            a, b = _synth(sign, dy=dy, dx=dx)
            rows.extend(pairs_for(_frame(a), _frame(b), step=140))
        res = fit(rows)
        report(res, f"self-check: {name}")
        results[name] = res

    dome, bowl = results["planted DOME"], results["planted BOWL"]
    assert dome["ok"] and bowl["ok"], "self-check produced too few pairs"
    assert dome["curv"] < 0, f"dome recovered with wrong sign: {dome['curv']}"
    assert bowl["curv"] > 0, f"bowl recovered with wrong sign: {bowl['curv']}"
    for r in (dome, bowl):
        assert abs(r["curv"]) / r["curv_se"] > 3.0, f"planted shape not resolved: {r}"

    # A mirror step turns the field ~4.9 deg. If the template were rotated the
    # WRONG WAY the error doubles to ~10 deg and nothing matches, so "did we find
    # the patches at all" is the test that pins the sign down. Flat field here,
    # so a curvature that came out significant would mean rotation handling is
    # manufacturing a shape.
    a, b = _synth(0.0, dy=400, dx=-1200, rot=4.9)
    rot_rows = pairs_for(_frame(a), _frame(b), step=140)
    flat_a, flat_b = _synth(0.0, dy=400, dx=-1200)
    straight = pairs_for(_frame(flat_a), _frame(flat_b), step=140)
    print(f"\n=== self-check: 4.9 deg rotated pair, flat field ===\n"
          f"  {len(rot_rows)} patches matched through the rotation "
          f"({len(straight)} on the same pair unrotated)")
    assert len(rot_rows) > 0.5 * len(straight), (
        f"rotation handling lost most patches ({len(rot_rows)} vs {len(straight)}) "
        "-- template rotated the wrong way?")
    rot_res = fit(rot_rows)
    if rot_res.get("ok"):
        report(rot_res, "self-check: rotated + flat")
        assert abs(rot_res["curv"]) / rot_res["curv_se"] < 6.0, (
            f"flat field read as curved through a rotation: {rot_res['curv']:+.3f}")
    print("\nself-check OK: planted dome and bowl recovered with the right sign at "
          ">3 sigma, and a 4.9 deg rotated pair still registers without inventing "
          "a field shape")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("glob", nargs="?")
    ap.add_argument("--csv", type=Path, help="write the paired observations")
    ap.add_argument("--from-csv", type=Path, help="re-infer from a previous scan")
    ap.add_argument("--boot", type=int, default=400, help="cluster bootstrap draws")
    ap.add_argument("--min-ncc", type=float, default=0.0,
                    help="keep only pairs matched at least this well")
    ap.add_argument("--max-gap", type=int, default=1,
                    help="also pair sweeps this many apart (2 still overlaps ~18%%)")
    ap.add_argument("--no-mirror", action="store_true",
                    help="skip adjacent-mirror-step pairs (drops the across-track "
                         "geometry, so tilt and curvature stop being separable)")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()

    if args.self_check:
        _self_check()
        return
    if args.from_csv:
        rows = read_csv(args.from_csv)
        print(f"reloaded {len(rows)} paired patches from {args.from_csv}")
    elif args.glob:
        paths = sorted(Path(args.glob).parent.glob(Path(args.glob).name)
                       if Path(args.glob).is_absolute() else Path().glob(args.glob))
        if not paths:
            ap.error(f"no files matched {args.glob}")
        rows = scan(paths, args.max_gap, mirror=not args.no_mirror)
        if args.csv and rows:
            with open(args.csv, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
            print(f"\nwrote {args.csv}")
    else:
        ap.error("need a glob of TIFs, --from-csv, or --self-check")

    if args.min_ncc > 0:
        before = len(rows)
        rows = [r for r in rows if r["ncc"] >= args.min_ncc]
        print(f"ncc >= {args.min_ncc}: kept {len(rows)} of {before} patches")

    by_line = defaultdict(list)
    for r in rows:
        by_line[r["line"]].append(r)
    for line in sorted(by_line):
        sub = by_line[line]
        report(fit(sub), f"line {line}", cluster_bootstrap(sub, args.boot))

    # The two geometries carry different systematics -- the sweep pairs compare
    # across a 3 s gap at one look angle, the mirror pairs across 0.22 s at two.
    # Agreeing is evidence; disagreeing localises which systematic is biting.
    for flag, label in ((0, "sweep pairs only (along-track shift)"),
                        (1, "mirror pairs only (across-track shift)")):
        sub = [r for r in rows if int(r.get("mirror", 0)) == flag]
        if len(sub) >= 30:
            report(fit(sub), label, cluster_bootstrap(sub, args.boot))
    if len(by_line) > 1 or any(int(r.get("mirror", 0)) for r in rows):
        report(fit(rows), "ALL pairs, both geometries",
               cluster_bootstrap(rows, args.boot))


if __name__ == "__main__":
    main()
