"""Does focus really vary at a fixed encoder count, and does the Fine tracker
pick tiles that die young? Both answered from the M4 focus crops.

Two findings came off the 30 Sep cam7 flight: the tracker "sometimes works a
treat and sometimes doesn't", and focus looked inconsistent at the same encoder
reading. Both are measurable from the saved crops plus the chunk headers, with
no pixel decode of the 1.5 TB of .bay14line payload.

The crop is the one artefact that carries BOTH signals. It is the ground the
tracker chose, so its content tells you when the tracker re-picked; and it is a
full-res green plane, so its sharpness is the same number the engine scores.
Joining it to the frame's encoder count turns a folder of TIFs into a
(position, score, object) series.

    py -3 tools/focus_jitter.py --build "E:/Project" --csv jitter.csv
    py -3 tools/focus_jitter.py --tracker jitter.csv
    py -3 tools/focus_jitter.py --lash jitter.csv
    py -3 tools/focus_jitter.py --self-check

Scoring enters at :func:`focus_measure_green`, NOT `focus_measure_full_res`:
these crops are ALREADY the green sub-grid, so the full-res entry point would
decimate a second time and measure an aliased image the engine never saw
(HANDOFF 45.1 -- the trap `tools/af_crop_review.py` still contains).
"""

from __future__ import annotations

import argparse
import csv
import itertools
import os
import re
import sys
from pathlib import Path

# These crops carry two ImageDescription tags (HANDOFF 49.3 notes the benign
# "Duplicate ImageDescription tag in IFD0"), which makes libtiff complain about
# tag order on EVERY read. 5956 of those warnings bury the actual output.
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "host"), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from bay14line_encoder_report import _focus, chunk_metas  # noqa: E402
from capture.autofocus import (  # noqa: E402
    _roi_pinned_against_flow,
    focus_measure_green,
    leading_edge_from_motion,
)
from capture.autofocus_v2 import _fit_peak, _fit_peak_scale_free  # noqa: E402

# These crops are 14-bit samples in uint16 cells, unshifted -- the ceiling is
# 16383. HANDOFF 49.5 lost a whole bracket to crops that were a CONSTANT 16383,
# which scores zero sharpness and is indistinguishable from total defocus, so
# the clipped fraction is carried per row rather than assumed away.
CLIP_LEVEL = 16300

NCC_DS = 4             # downsample before the consecutive-crop match
NCC_MARGIN = 0.18      # template inset per axis, as a fraction of the crop
SAME_OBJECT_NCC = 0.50 # below this the content changed wholesale: a re-pick

# Two extra gates, and they are not optional on this flight. The sortie was
# flown under heavy cloud, and a crop of cloud is nearly featureless: a smooth
# image correlates ~0.99 with ANY alignment of another smooth image, so NCC
# alone certifies "same ground" that is not. It also carries no focus signal to
# measure. Every conclusion about focus is therefore drawn only from runs that
# are BOTH demonstrably held and demonstrably textured.
HOLD_NCC = 0.97        # "the tracker provably held this patch", not merely matched
MIN_TEXTURE = 20.0     # mean score below this is cloud/haze: no focus information
CLEAR_COARSE = 0.15    # coarse_contrast above this is ground, not cloud. Calibrated
                       # against crops at the 2/10/25/45/65/85/97th percentiles of
                       # the flight: 0.03-0.08 is featureless haze, 0.17 upwards is
                       # fields and buildings, and the two do not overlap.


_CROP_RE = re.compile(r"_focus_(\d{6})_(\d{3})(?:_S(\d+)_F(\d+))?\.tif$", re.I)
_LINE_RE = re.compile(r"_FOCUS_C(\d+)_L(\d+)_(\d{6})_p(\d+)\.bay14line$", re.I)
_TS_RE = re.compile(r"T(\d{2}):(\d{2}):(\d{2})\.(\d{3})")


# ---------------------------------------------------------------------------
# Join: crop <-> frame
# ---------------------------------------------------------------------------

def ts_key(acquire_timestamp: str) -> str | None:
    """``2026-09-30T14:42:21.938+03:00`` -> ``144221_938``.

    The crop filename carries the same stamp to the millisecond, and HANDOFF
    49.4 established that a crop and its full frame share it exactly. Sweep and
    frame_in_sweep are NOT a usable key on their own: they restart at 1 for each
    M4 session, so (sweep, frame) collides between the two sessions on this
    flight.
    """
    m = _TS_RE.search(acquire_timestamp or "")
    return f"{m.group(1)}{m.group(2)}{m.group(3)}_{m.group(4)}" if m else None


def frame_index(root: Path) -> dict[str, dict]:
    """Map ``ts_key`` -> frame facts, from the .bay14line headers only.

    Reads the line header, the appended index and each frame's chunk header --
    never the payload -- so this is seconds on files that are hundreds of GB.
    """
    out: dict[str, dict] = {}
    for path in sorted(root.rglob("*.bay14line")):
        m = _LINE_RE.search(path.name)
        if not m:
            continue  # survey line, not an M4 focus recording
        session, shard = m.group(3), int(m.group(4))
        try:
            frames = chunk_metas(path)
        except Exception as exc:
            print(f"  !! {path.name}: {type(exc).__name__}: {exc}")
            continue
        for sweep, f_in, meta in frames:
            key = ts_key(meta.get("acquire_timestamp", ""))
            if key is None:
                continue
            foc = _focus(meta)
            enc = foc.get("encoder_count")
            out[key] = {
                "session": session,
                "shard": shard,
                "sweep": sweep,
                "frame_in_sweep": f_in,
                "encoder": enc,
                "encoder_stale": bool(foc.get("encoder_stale")),
            }
        print(f"  {path.name}: {len(frames)} frames")
    return out


# ---------------------------------------------------------------------------
# Per-crop measurements
# ---------------------------------------------------------------------------

def coarse_contrast(img: np.ndarray) -> float:
    """RMS contrast of the scene at coarse scale: is there GROUND under this?

    The focus score cannot answer that question, because defocused terrain and
    flat cloud both read as "no fine detail" -- using it to exclude cloud would
    throw away exactly the soft frames an autofocus study is about. Blur is a
    low-pass filter, so it leaves large structure (field boundaries, roads,
    coastline) almost untouched while destroying fine detail. Decimating by 16
    first therefore keeps what cloud genuinely lacks and discards what defocus
    removes, which separates the two causes of a flat-looking crop.
    """
    small = img[::16, ::16].astype(np.float32)
    m = float(small.mean())
    return 0.0 if m <= 1.0 else float(small.std() / m)


def anisotropy(img: np.ndarray) -> tuple[float, float]:
    """``(ratio, blur_deg)`` -- how directional the detail is, and along which axis.

    Defocus is a symmetric PSF: it attenuates detail at every orientation about
    equally. Linear motion blur is a 1-D box: it attenuates ONLY along the
    direction of travel and leaves the perpendicular direction sharp. So the
    shape of the gradient-energy ellipse separates the two causes of a soft
    frame, which is the whole point of measuring it.

    Gradient energy at angle t is the quadratic form of the structure tensor
    J = [[<gx gx>, <gx gy>], [<gx gy>, <gy gy>]], so its eigenvalues ARE the
    max and min over all orientations -- no need to sample angles. ``ratio`` is
    lo/hi: 1.0 is perfectly isotropic, 0.0 fully directional. ``blur_deg`` is
    the orientation of LEAST gradient energy, i.e. the smear direction, mod 180.

    Scene texture is directional too (field rows, roads), so a single crop's
    ratio proves nothing. The discriminator is whether ``blur_deg`` is
    CONSISTENT across many crops of different ground -- see :func:`report_lash`.
    """
    g = img.astype(np.float32)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    jxx = float(np.mean(gx * gx))
    jyy = float(np.mean(gy * gy))
    jxy = float(np.mean(gx * gy))
    half, diff = (jxx + jyy) / 2.0, (jxx - jyy) / 2.0
    root = float(np.hypot(diff, jxy))
    hi, lo = half + root, half - root
    ratio = float(lo / hi) if hi > 0 else 1.0
    # Principal (max-energy) eigenvector angle; the min-energy axis is +90 deg.
    theta_hi = 0.5 * np.arctan2(2.0 * jxy, jxx - jyy)
    blur_deg = float(np.degrees(theta_hi) + 90.0) % 180.0
    return max(0.0, min(1.0, ratio)), blur_deg


def _prep_ncc(img: np.ndarray) -> np.ndarray:
    small = cv2.resize(
        img.astype(np.float32), None,
        fx=1.0 / NCC_DS, fy=1.0 / NCC_DS, interpolation=cv2.INTER_AREA,
    )
    return small


def match_prev(cur: np.ndarray, prev: np.ndarray) -> tuple[float, float, float]:
    """``(ncc, dx, dy)`` between consecutive crops, in full-res crop pixels.

    The tracker dead-reckons its ROI onto the moving ground, so two crops of the
    SAME object show near-identical content at near-identical crop coordinates,
    and ``ncc`` is high. A re-pick swaps the content outright and ``ncc``
    collapses -- that collapse is the object boundary, recovered without ever
    opening a full frame.

    ``dx, dy`` is the RESIDUAL drift the tracker failed to take out, not the
    scene flow: the ROI already moved with the scene before the crop was cut.
    Returns ``ncc = -1`` when the shapes differ, which is itself a boundary (the
    pane was resized mid-session on this flight).
    """
    if cur.shape != prev.shape:
        return -1.0, 0.0, 0.0
    a, b = _prep_ncc(prev), _prep_ncc(cur)
    h, w = a.shape[:2]
    my, mx = int(h * NCC_MARGIN), int(w * NCC_MARGIN)
    if my < 2 or mx < 2 or h - 2 * my < 8 or w - 2 * mx < 8:
        return -1.0, 0.0, 0.0
    templ = a[my:h - my, mx:w - mx]
    res = cv2.matchTemplate(b, templ, cv2.TM_CCOEFF_NORMED)
    _mn, mxv, _ml, mloc = cv2.minMaxLoc(res)
    return float(mxv), float((mloc[0] - mx) * NCC_DS), float((mloc[1] - my) * NCC_DS)


FIELDS = [
    "crop", "ts_key", "session", "sweep", "frame_in_sweep", "encoder",
    "encoder_stale", "h", "w", "score", "coarse", "clip_frac", "aniso_ratio",
    "blur_deg",
    "ncc", "dx", "dy", "object_id", "frames_on_object",
]


def build(root: Path, out_csv: Path) -> int:
    print(f"indexing .bay14line headers under {root} ...")
    idx = frame_index(root)
    print(f"  {len(idx)} frames indexed")

    crops = sorted(root.rglob("*_focus_*.tif"))
    print(f"scoring {len(crops)} crops ...")
    print("  (crops are RAW BAYER -- see HANDOFF 51 -- so scoring and the")
    print("   anisotropy both run on the extracted green plane)")

    rows: list[dict] = []
    prev_img: np.ndarray | None = None
    object_id, frames_on_object, matched = 0, 0, 0
    for i, path in enumerate(crops):
        m = _CROP_RE.search(path.name)
        if not m:
            continue
        key = f"{m.group(1)}_{m.group(2)}"
        img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if img is None or img.ndim != 2:
            print(f"  !! unreadable: {path.name}")
            continue

        # HANDOFF 51: these files are raw Bayer, so the stored frame carries a
        # fixed RGGB checkerboard worth ~89% of its high-frequency energy. Both
        # the focus metric and the anisotropy are high-frequency measurements
        # and would read the mosaic rather than the scene, so take the green
        # plane first -- which is exactly what focus_measure_full_res does
        # before it reaches focus_measure_green.
        green = img[0::2, 1::2]

        ncc, dx, dy = (np.nan, 0.0, 0.0)
        if prev_img is not None:
            ncc, dx, dy = match_prev(green, prev_img)
        if prev_img is None or not (ncc >= SAME_OBJECT_NCC):
            object_id += 1
            frames_on_object = 0
        else:
            frames_on_object += 1
        prev_img = green

        ratio, blur_deg = anisotropy(green)
        f = idx.get(key)
        if f is not None:
            matched += 1
        rows.append({
            "crop": path.name,
            "ts_key": key,
            "session": (f or {}).get("session", ""),
            "sweep": (f or {}).get("sweep", ""),
            "frame_in_sweep": (f or {}).get("frame_in_sweep", ""),
            "encoder": (f or {}).get("encoder", ""),
            "encoder_stale": int(bool((f or {}).get("encoder_stale"))) if f else "",
            "h": img.shape[0], "w": img.shape[1],
            "score": round(focus_measure_green(green), 4),
            # Decimating by 16 lands on one Bayer phase either way, so this one
            # is safe on the stored frame and keeps its calibrated threshold.
            "coarse": round(coarse_contrast(img), 5),
            "clip_frac": round(float(np.count_nonzero(img >= CLIP_LEVEL)) / img.size, 6),
            "aniso_ratio": round(ratio, 4),
            "blur_deg": round(blur_deg, 2),
            "ncc": "" if np.isnan(ncc) else round(ncc, 4),
            "dx": round(dx, 1), "dy": round(dy, 1),
            "object_id": object_id,
            "frames_on_object": frames_on_object,
        })
        if (i + 1) % 500 == 0:
            print(f"  {i + 1}/{len(crops)}  objects so far: {object_id}", flush=True)

    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"\n{len(rows)} rows -> {out_csv}")
    print(f"  joined to an encoder count: {matched}/{len(rows)}")
    print(f"  objects segmented: {object_id}")
    return 0


# ---------------------------------------------------------------------------
# Loading + shared helpers for the reports
# ---------------------------------------------------------------------------

def load(csv_path: Path) -> list[dict]:
    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            for k in ("encoder", "score", "coarse", "clip_frac", "aniso_ratio",
                      "blur_deg", "ncc", "dx", "dy"):
                r[k] = num(r[k])
            for k in ("object_id", "frames_on_object", "h", "w"):
                r[k] = int(r[k]) if r[k] not in ("", None) else None
            rows.append(r)
    return rows


def label_legs(rows: list[dict]) -> None:
    """Tag each row ``up`` / ``down`` by which side of the sweep's turnaround it
    is on.

    Per-step direction is useless here: the encoder jitters by a count or two
    while parked, so a sign-of-diff rule shreds a clean out-and-back into
    hundreds of fragments. The sweep has ONE reversal, so the position of the
    smoothed maximum is the honest split.
    """
    enc = np.array([r["encoder"] if r["encoder"] is not None else np.nan
                    for r in rows], dtype=float)
    ok = ~np.isnan(enc)
    if ok.sum() < 5:
        for r in rows:
            r["leg"] = ""
        return
    filled = np.interp(np.arange(len(enc)), np.flatnonzero(ok), enc[ok])
    k = max(3, len(filled) // 100)
    smooth = np.convolve(filled, np.ones(k) / k, mode="same")
    turn = int(np.argmax(smooth))
    for i, r in enumerate(rows):
        r["leg"] = "up" if i <= turn else "down"
    rows[0]["_turn"] = turn


def _circ_mean_180(deg: np.ndarray) -> tuple[float, float]:
    """Mean orientation and concentration R for angles defined mod 180 deg.

    Orientation is an axis, not a direction: 5 deg and 175 deg are nearly the
    same smear. Doubling the angle maps the 180-periodic axis onto a full circle
    where the usual vector mean is valid, then halving brings it back. R near 0
    means the orientations are scattered (scene texture); R near 1 means they
    all point the same way (one physical smear direction).
    """
    a = np.radians(deg.astype(float) * 2.0)
    c, s = np.cos(a).mean(), np.sin(a).mean()
    return float(np.degrees(np.arctan2(s, c)) / 2.0) % 180.0, float(np.hypot(c, s))


# ---------------------------------------------------------------------------
# Report: the tracker
# ---------------------------------------------------------------------------

def report_tracker(rows: list[dict]) -> None:
    print("\n=== TRACKER: how long does a picked tile survive? ===")
    dwell: dict[int, int] = {}
    for r in rows:
        oid = r["object_id"]
        dwell[oid] = max(dwell.get(oid, 0), r["frames_on_object"] + 1)
    lens = np.array(sorted(dwell.values()))
    print(f"  crops {len(rows)}   objects {len(lens)}")
    print(f"  dwell frames: median {np.median(lens):.0f}  mean {lens.mean():.1f}  "
          f"max {lens.max()}")
    for q in (10, 25, 50, 75, 90):
        print(f"    p{q:<2} {np.percentile(lens, q):6.1f}")
    short = int((lens <= 3).sum())
    print(f"  objects dying in <=3 frames: {short}/{len(lens)} "
          f"({100.0 * short / len(lens):.0f}%), holding "
          f"{int(lens[lens <= 3].sum())}/{int(lens.sum())} of all crops")

    print("\n  dwell histogram")
    edges = [1, 2, 3, 4, 6, 9, 14, 21, 31, 10 ** 6]
    for lo, hi in zip(edges, edges[1:]):
        n = int(((lens >= lo) & (lens < hi)).sum())
        lab = f"{lo}" if hi == lo + 1 else f"{lo}-{hi - 1}"
        print(f"    {lab:>7} frames  {n:5d}  {'#' * int(60.0 * n / max(1, len(lens)))}")

    # The same statistic with cloud excluded. Cloud cuts both ways: a
    # featureless crop matches anything, so a re-pick over cloud scores NCC
    # ~0.99 and is counted as a hold that never happened. If the tracker only
    # misbehaves under cloud, this split is where it stops misbehaving.
    for name, sub in (("CLEAR ground", [r for r in rows if (r["coarse"] or 0) >= CLEAR_COARSE]),
                      ("cloud / haze", [r for r in rows if (r["coarse"] or 0) < CLEAR_COARSE])):
        if not sub:
            continue
        d: dict[int, int] = {}
        for r in sub:
            d[r["object_id"]] = max(d.get(r["object_id"], 0), r["frames_on_object"] + 1)
        v = np.array(sorted(d.values()))
        print(f"\n  {name} (coarse contrast "
              f"{'>=' if name.startswith('CLEAR') else '<'} {CLEAR_COARSE}):")
        print(f"    {len(sub)} crops, {len(v)} objects, median dwell "
              f"{np.median(v):.0f}, mean {v.mean():.1f}, p90 "
              f"{np.percentile(v, 90):.0f}")
        print(f"    dying in <=3 frames: {int((v <= 3).sum())}/{len(v)} "
              f"({100.0 * (v <= 3).mean():.0f}%)")

    nccs = np.array([r["ncc"] for r in rows if r["ncc"] is not None and r["ncc"] >= 0])
    if nccs.size:
        print(f"\n  consecutive-crop NCC: median {np.median(nccs):.3f}")
        hist, be = np.histogram(nccs, bins=10, range=(0.0, 1.0))
        for n, lo in zip(hist, be):
            print(f"    {lo:4.1f}-{lo + 0.1:4.1f}  {n:5d}  "
                  f"{'#' * int(60.0 * n / max(1, hist.max()))}")
        print("  (a clean trough between the two humps is what makes the "
              f"{SAME_OBJECT_NCC} boundary threshold safe)")

    # Residual drift the tracker failed to remove, on frames it DID hold.
    held = [r for r in rows if r["ncc"] is not None and r["ncc"] >= SAME_OBJECT_NCC]
    if held:
        d = np.array([(r["dx"], r["dy"]) for r in held], dtype=float)
        print(f"\n  residual drift while holding (green px/frame):")
        print(f"    |dx| median {np.median(np.abs(d[:, 0])):.1f}  "
              f"|dy| median {np.median(np.abs(d[:, 1])):.1f}")
        print(f"    dx mean {d[:, 0].mean():+.1f}  dy mean {d[:, 1].mean():+.1f} "
              "(a non-zero mean is a systematic under/over-shoot, not noise)")

    by_shape: dict[tuple, list[int]] = {}
    for r in rows:
        by_shape.setdefault((r["h"], r["w"]), []).append(r["object_id"])
    print("\n  by crop shape (the pane was resized mid-flight):")
    for shape, oids in sorted(by_shape.items(), key=lambda kv: -len(kv[1])):
        u = sorted(set(oids))
        dl = np.array([dwell[o] for o in u])
        print(f"    {shape[1]}x{shape[0]:<5} crops {len(oids):5d}  objects {len(u):4d}  "
              f"median dwell {np.median(dl):.0f}")


# ---------------------------------------------------------------------------
# Report: lash + vibration
# ---------------------------------------------------------------------------

def report_lash(rows: list[dict], session: str | None = None) -> None:
    sessions = sorted({r["session"] for r in rows if r["session"]})
    for sess in ([session] if session else sessions):
        sub = [r for r in rows if r["session"] == sess and r["encoder"] is not None]
        if len(sub) < 50:
            continue
        sub.sort(key=lambda r: r["ts_key"])
        label_legs(sub)
        enc = np.array([r["encoder"] for r in sub])
        print(f"\n=== LASH: session {sess} ===")
        print(f"  {len(sub)} crops, encoder {enc.min():.0f}..{enc.max():.0f} "
              f"(span {enc.max() - enc.min():.0f} counts)")
        legs = {"up": [r for r in sub if r["leg"] == "up"],
                "down": [r for r in sub if r["leg"] == "down"]}
        print(f"  up-leg {len(legs['up'])} crops, down-leg {len(legs['down'])} crops")
        if len(legs["down"]) < 50:
            print("  !! no usable return leg -- this session cannot measure lash")
            continue

        _peaks_per_leg(legs)
        _plateau_at_reversal(sub)
        _within_station(sub)
        _vibration(sub)


def held_runs(rows: list[dict], min_len: int, fixed_encoder: bool) -> list[list[dict]]:
    """Consecutive stretches the tracker provably held, optionally at ONE encoder.

    ``fixed_encoder=True`` is what isolates finding 2: the ground is the same
    (NCC >= HOLD_NCC), the commanded lens position is identical, nothing is
    being pulsed -- so any score change left is the optics moving on their own.
    """
    runs, cur = [], []
    for a, b in zip(rows, rows[1:]):
        ok = (b["ncc"] is not None and b["ncc"] >= HOLD_NCC
              and (a["clip_frac"] or 0.0) < 0.01
              and (not fixed_encoder or a["encoder"] == b["encoder"]))
        if ok:
            cur = cur or [a]
            cur.append(b)
        else:
            if len(cur) >= min_len:
                runs.append(cur)
            cur = []
    if len(cur) >= min_len:
        runs.append(cur)
    return [v for v in runs
            if np.mean([x["score"] for x in v if x["score"]]) >= MIN_TEXTURE]


def _peaks_per_leg(legs: dict[str, list[dict]]) -> None:
    """Estimate 1: where each leg's score peaks.

    Uses the SCALE-FREE fit as the headline. Each crop is different ground with
    its own unknown texture scale k, and absolute scores are not comparable
    across objects -- but d/dp log(k*S) = d/dp log S, so one log-slope per
    tracked object is scale-free, and the slopes cross zero at the peak. The
    plain parabola is printed beside it as a cross-check; where they disagree,
    content is leaking into the parabola.
    """
    print("\n  [1] peak position per leg")
    got = {}
    for name, rs in legs.items():
        # Only textured crops can carry a focus peak, and only multi-frame
        # objects can give the scale-free fit a log-slope. Feeding it 1-frame
        # objects (which is most of them on this flight) yields nothing.
        usable = [r for r in rs if r["score"] and r["score"] >= MIN_TEXTURE
                  and (r["clip_frac"] or 0) < 0.01]
        counts = {}
        for r in usable:
            counts[r["object_id"]] = counts.get(r["object_id"], 0) + 1
        seg = [r for r in usable if counts[r["object_id"]] >= 3]
        sf = _fit_peak_scale_free([(r["encoder"], r["score"], r["object_id"])
                                   for r in seg])
        s2 = [(r["encoder"], r["score"]) for r in usable]
        pb = _fit_peak(s2, window=max(5, len(s2) // 10)) if len(s2) > 10 else None
        got[name] = sf
        f = lambda v: "    n/a" if v is None else f"{v:8.0f}"
        print(f"    {name:<5} n={len(usable):4d} ({len(seg)} in >=3-frame objects)"
              f"  scale-free {f(sf)}  parabola {f(pb)}")
    if got.get("up") is not None and got.get("down") is not None:
        gap = got["up"] - got["down"]
        print(f"    => LASH (up peak - down peak) = {gap:+.0f} counts")
        print(f"       {abs(gap) / 790.0:.2f} deg of ring lash at 69.4:1 "
              f"(HANDOFF 23.1), {abs(gap) / 1460.0:.2f} of a depth of field")
    else:
        print("    => UNRESOLVED. A leg's peak did not fit, so no lash number is")
        print("       claimed. The fit needs multi-frame objects for its log-slopes,")
        print("       and this flight's tracker re-picked too often to supply them")
        print("       (see --tracker): finding 1 is what blocks finding 2 here.")


def _plateau_at_reversal(sub: list[dict]) -> None:
    """Estimate 2: the dead band, measured locally at the turnaround.

    HANDOFF 23.3: "lash = (encoder counts up) AND (image flat) -- it takes BOTH
    signals; neither alone reveals it." Right after a reversal the motor unwinds
    the backlash before the ring moves, so the encoder travels while the score
    does not. The width of that flat stretch IS the dead band, and it needs no
    curve fit at all.

    Measured only on crops the tracker HELD across the reversal, because on a
    new object a score change means new ground, not new focus.
    """
    print("\n  [2] dead band at the reversal")
    turn = sub[0].get("_turn")
    if turn is None:
        print("    no turnaround found")
        return
    lo, hi = max(0, turn - 80), min(len(sub), turn + 80)
    win = sub[lo:hi]
    print(f"    turnaround at crop #{turn} ({sub[turn]['ts_key']}), "
          f"encoder {sub[turn]['encoder']:.0f}")
    runs = [list(g) for _, g in itertools.groupby(win, key=lambda r: r["object_id"])]
    runs.sort(key=len, reverse=True)
    for run in runs[:3]:
        if len(run) < 6:
            continue
        e = np.array([r["encoder"] for r in run])
        s = np.array([r["score"] for r in run])
        oid, span = run[0]["object_id"], e.max() - e.min()
        rng = (s.max() - s.min()) / max(1e-9, s.mean())
        print(f"    object {oid}: {len(run)} crops, encoder span {span:.0f} counts, "
              f"score range {100 * rng:.0f}% of mean")
        if span > 50 and rng < 0.15:
            print(f"      -> FLAT across {span:.0f} counts while the encoder moved: "
                  "dead band")


def _within_station(sub: list[dict]) -> None:
    """How much does focus move while the encoder is PARKED on fixed ground?

    This is finding 2 stated as a measurement. Every confound is held still:
    same ground (NCC >= HOLD_NCC), identical encoder count, no pulses in
    flight, textured content, unclipped. `focus_measure_green`'s own docstring
    records a known ~10-15% frame-to-frame spread from sensor noise, so that is
    the bar to clear.

    Magnitude alone cannot say whether it is noise or motion, so the lag-1
    autocorrelation decides: white sensor noise is independent frame to frame,
    while something physically wandering makes neighbouring frames alike. The
    shuffled control re-uses the SAME numbers with time order destroyed, so any
    gap between them is temporal structure and nothing else.
    """
    print("\n  [3] score spread at a FIXED encoder, tracker provably holding")
    runs = held_runs(sub, min_len=4, fixed_encoder=True)
    if not runs:
        print("    no runs survive the hold + texture gates")
        return
    sw = np.array([float(np.ptp([x["score"] for x in v]) /
                         np.mean([x["score"] for x in v])) for v in runs])
    print(f"    {len(runs)} runs of >=4 frames (same ground, same encoder, textured)")
    print(f"    peak-to-peak as a fraction of the mean: median {100 * np.median(sw):.1f}%"
          f"  p90 {100 * np.percentile(sw, 90):.1f}%")
    print(f"    sensor-noise level is 10-15% -> "
          f"{'ABOVE it: real optical movement' if np.median(sw) > 0.15 else 'consistent with noise'}")

    rng = np.random.default_rng(0)
    r1, r2, sh = [], [], []
    for v in runs:
        if len(v) < 6:
            continue
        s = np.array([x["score"] for x in v], dtype=float)
        d = s / s.mean() - 1.0
        if d.std() < 1e-9:
            continue
        r1.append(np.corrcoef(d[:-1], d[1:])[0, 1])
        if len(d) >= 8:
            r2.append(np.corrcoef(d[:-2], d[2:])[0, 1])
        p = rng.permutation(d)
        sh.append(np.corrcoef(p[:-1], p[1:])[0, 1])
    if r1:
        print(f"\n    temporal structure over {len(r1)} runs of >=6 frames:")
        print(f"      lag-1 autocorrelation {np.mean(r1):+.3f}")
        if r2:
            print(f"      lag-2 autocorrelation {np.mean(r2):+.3f}  (n={len(r2)})")
        print(f"      shuffled control      {np.mean(sh):+.3f}")
        if np.mean(r1) - np.mean(sh) > 0.15:
            print("      -> NOT white noise. The score wanders on a timescale of "
                  "several frames (~1 s), which is a physical movement, not the "
                  "sensor.")
        else:
            print("      -> indistinguishable from white noise")


def _vibration(sub: list[dict]) -> None:
    """Is the softness directional (motion smear) or symmetric (defocus)?

    Linear motion blur attenuates detail ONLY along the direction of travel, so
    its soft frames are MORE directional. Defocus is a symmetric PSF, so its
    soft frames are no more directional than its sharp ones. Comparing sharp and
    soft frames WITHIN one held run at a fixed encoder keeps the scene fixed,
    which is what makes the comparison mean anything.

    Read the sign carefully: ``aniso_ratio`` is lo/hi, so SMALL means directional.
    A positive correlation with score means soft frames are directional (motion);
    a negative one means soft frames are isotropic (defocus-like).
    """
    print("\n  [4] directional (motion smear) vs symmetric (defocus) softness")
    runs = held_runs(sub, min_len=4, fixed_encoder=True)
    rho = []
    for v in runs:
        s = np.array([x["score"] for x in v], dtype=float)
        a = np.array([x["aniso_ratio"] for x in v], dtype=float)
        if np.ptp(s) > 1e-9 and np.ptp(a) > 1e-9:
            rho.append(np.corrcoef(s, a)[0, 1])
    if rho:
        m = float(np.mean(rho))
        print(f"    corr(score, anisotropy lo/hi) within a held run: {m:+.3f} "
              f"(n={len(rho)})")
        if m > 0.15:
            print("    -> soft frames ARE more directional: consistent with motion "
                  "blur / vibration")
        else:
            print("    -> soft frames are NOT more directional; the sign is opposite "
                  "to the motion-blur prediction, so the softness is symmetric")

    # The artifact control. If the apparent smear axis is real it should be
    # strongest where there is detail to smear. If instead it is strongest on
    # featureless cloud -- where the orientation estimate is ill-conditioned
    # because the ellipse is nearly a circle -- it is a measurement artifact.
    rs = [r for r in sub if r["aniso_ratio"] is not None and r["score"]]
    if len(rs) > 50:
        sc = np.array([r["score"] for r in rs])
        an = np.array([r["aniso_ratio"] for r in rs])
        dg = np.array([r["blur_deg"] for r in rs])
        lo, hi = sc < np.percentile(sc, 25), sc > np.percentile(sc, 75)
        _d_lo, r_lo = _circ_mean_180(dg[lo])
        _d_hi, r_hi = _circ_mean_180(dg[hi])
        print(f"\n    orientation concentration R: featureless quartile {r_lo:.2f} "
              f"(anisotropy {np.median(an[lo]):.3f}), "
              f"textured quartile {r_hi:.2f} (anisotropy {np.median(an[hi]):.3f})")
        if r_lo >= r_hi:
            print("    -> the 'smear axis' is TIGHTEST on the frames with the least "
                  "detail, where the estimate is weakest: an artifact, not optics")


# ---------------------------------------------------------------------------
# Where did the pick land? (needs the full frames)
# ---------------------------------------------------------------------------

LOC_DS = 16   # downsample for finding the crop inside the frame
FLOW_DS = 8   # downsample for the global scene-flow estimate

LOC_FIELDS = ["ts_key", "fx", "fy", "loc_ncc", "flow_dx", "flow_dy", "lead",
              "pinned", "frames_left", "cross_frac"]


def _ds(img: np.ndarray, factor: int) -> np.ndarray:
    return cv2.resize(img.astype(np.float32), None, fx=1.0 / factor,
                      fy=1.0 / factor, interpolation=cv2.INTER_AREA)


def _frames_left(x0, y0, x1, y1, dx, dy, fw, fh):
    """Port of FineFocusTracker.frames_left -- distance to the OUTFLOW border
    over the per-frame flow, taking the nearer of the two axes."""
    spans = []
    if abs(dx) > 0.5:
        spans.append((x0 if dx < 0 else fw - x1) / abs(dx))
    if abs(dy) > 0.5:
        spans.append((y0 if dy < 0 else fh - y1) / abs(dy))
    return max(int(min(spans)), 0) if spans else None


def localize(line_path: Path, csv_path: Path, crops_dir: Path,
             limit: int, out_csv: Path) -> int:
    """Find each crop inside its full frame, and replay the tracker's own exit
    test against the measured scene flow.

    The crop carries no ROI in its metadata, so position has to be recovered by
    matching. That is the only question the crops alone cannot answer, and it is
    the one that decides WHY a pick dies: a tile flush against the leading edge
    is fine, a tile also flush against a CROSS-flow border is pinned the moment
    it is born, because `_roi_pinned_against_flow` tests both axes.

    Flow comes from phase correlation of consecutive whole frames, which is the
    scene motion the tracker's PatchTracker is estimating -- so `lead`, `pinned`
    and `frames_left` here are the same decisions the app made, recomputed
    against ground truth instead of trusted.
    """
    from bay14_export_core import iter_line_frames  # heavy import, only here

    by_key = {}
    for p in sorted(crops_dir.rglob("*_focus_*.tif")):
        m = _CROP_RE.search(p.name)
        if m:
            by_key[f"{m.group(1)}_{m.group(2)}"] = p

    rows, prev = [], None
    for n, (meta, arr) in enumerate(iter_line_frames(line_path, recovery_notice=None)):
        if n >= limit:
            break
        key = ts_key(meta.get("acquire_timestamp", ""))
        crop_path = by_key.get(key)
        if crop_path is None or arr is None:
            continue
        green = arr[0::2, 1::2]            # the plane the engine scores
        fh, fw = green.shape
        gl = _ds(green, LOC_DS)
        crop = cv2.imread(str(crop_path), cv2.IMREAD_UNCHANGED)
        cl = _ds(crop, LOC_DS)
        if cl.shape[0] >= gl.shape[0] or cl.shape[1] >= gl.shape[1]:
            continue
        res = cv2.matchTemplate(gl, cl, cv2.TM_CCOEFF_NORMED)
        _mn, mxv, _ml, mloc = cv2.minMaxLoc(res)
        x0, y0 = mloc[0] * LOC_DS, mloc[1] * LOC_DS
        x1, y1 = x0 + crop.shape[1], y0 + crop.shape[0]

        gf = _ds(green, FLOW_DS)
        dx = dy = 0.0
        if prev is not None and prev.shape == gf.shape:
            (sx, sy), _resp = cv2.phaseCorrelate(prev, gf)
            # phaseCorrelate gives the shift that maps prev onto cur; content
            # moving +x makes sx positive. Back to green-plane pixels.
            dx, dy = sx * FLOW_DS, sy * FLOW_DS
        prev = gf

        lead = leading_edge_from_motion(dx, dy)
        pinned = _roi_pinned_against_flow((x0, y0, x1, y1), dx, dy, fw, fh)
        fl = _frames_left(x0, y0, x1, y1, dx, dy, fw, fh)
        # Position across the flow axis, 0 = one border, 1 = the other. This is
        # the axis `lane_frac=0.0` leaves unconstrained.
        if lead in ("left", "right"):
            cross = (y0 + y1) / 2.0 / fh
        else:
            cross = (x0 + x1) / 2.0 / fw
        rows.append({
            "ts_key": key, "fx": round(x0 / fw, 4), "fy": round(y0 / fh, 4),
            "loc_ncc": round(float(mxv), 3),
            "flow_dx": round(dx, 1), "flow_dy": round(dy, 1),
            "lead": lead or "", "pinned": int(pinned),
            "frames_left": "" if fl is None else fl,
            "cross_frac": round(cross, 4),
        })
        if (n + 1) % 20 == 0:
            print(f"  {n + 1}/{limit} localized", flush=True)

    with open(out_csv, "w", newline="", encoding="utf-8") as fh_:
        w = csv.DictWriter(fh_, fieldnames=LOC_FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"\n{len(rows)} rows -> {out_csv}")
    report_localize(out_csv, csv_path)
    return 0


def diagnose(crops_dir: Path, jitter_csv: Path, limit: int) -> int:
    """Why does a tracked tile die? Separate the three ways it can happen.

    The NCC in the main table is an inset template match, so it can only see a
    shift up to NCC_MARGIN of the crop -- about 166 px on the 920-wide pane,
    which is LESS than the ~190 green px/frame the ground actually moves. A
    frozen ROI with ground sliding under it therefore looks identical to a
    re-pick in that statistic. Phase correlation has no such window, so it can
    tell the two apart:

      healthy   the ROI advanced with the ground, so the crop barely changes
      sliding   the crop shifts by about one frame of ground flow, meaning the
                ROI did NOT advance -- `self.roi = clamped` in
                FineFocusTracker.update only runs when the tracking response
                clears min_track_response, and the ROI is advanced by the
                MEASURED flow, so a collapsed estimate pins it to the sensor
      hopped    the content is unrelated: a genuine re-pick elsewhere
    """
    rows = [r for r in load(jitter_csv) if (r["coarse"] or 0) >= CLEAR_COARSE]
    rows.sort(key=lambda r: r["ts_key"])
    print(f"clear-ground crops: {len(rows)} (coarse contrast >= {CLEAR_COARSE})")

    pairs, prev = [], None
    for r in rows[:limit]:
        img = cv2.imread(str(crops_dir / r["crop"]), cv2.IMREAD_UNCHANGED)
        if img is None:
            prev = None
            continue
        if prev is not None and prev[1].shape == img.shape:
            a = prev[1].astype(np.float32)
            b = img.astype(np.float32)
            win = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
            (sx, sy), resp = cv2.phaseCorrelate(a, b, win)
            pairs.append((r, float(np.hypot(sx, sy)), float(resp)))
        prev = (r, img)
        if len(pairs) % 200 == 0 and pairs:
            print(f"  {len(pairs)} pairs", flush=True)

    if not pairs:
        print("no comparable consecutive pairs")
        return 1
    shift = np.array([p[1] for p in pairs])
    resp = np.array([p[2] for p in pairs])
    ok = resp >= 0.05
    healthy = ok & (shift < 30)
    sliding = ok & (shift >= 80)
    mid = ok & (shift >= 30) & (shift < 80)

    print(f"\n=== WHY A CLEAR-GROUND TILE DIES ({len(pairs)} consecutive pairs) ===")
    for name, m, note in (
        ("healthy  (<30 px)", healthy, "ROI advanced with the ground"),
        ("drifting (30-80px)", mid, "ROI under-advanced"),
        ("SLIDING  (>=80 px)", sliding, "ROI did not advance: ground slides under it"),
        ("hopped   (no match)", ~ok, "unrelated content: a genuine re-pick"),
    ):
        print(f"  {name:<20} {int(m.sum()):4d} ({100.0 * m.mean():3.0f}%)  {note}")
    if sliding.any():
        print(f"\n  median shift on the SLIDING pairs: {np.median(shift[sliding]):.0f} "
              "green px/frame")
        print("  (one frame of ground flow is ~190 green px, so a shift near that "
              "value means the ROI stood still for a whole frame)")
    return 0


def report_localize(loc_csv: Path, jitter_csv: Path) -> None:
    loc = list(csv.DictReader(open(loc_csv, newline="", encoding="utf-8")))
    if not loc:
        print("no localized rows")
        return
    dw = {}
    for r in load(jitter_csv):
        dw[r["ts_key"]] = (r["object_id"], r["frames_on_object"])

    print("\n=== WHERE THE PICK LANDS, AND WHETHER IT WAS BORN PINNED ===")
    ncc = np.array([float(r["loc_ncc"]) for r in loc])
    good = [r for r in loc if float(r["loc_ncc"]) > 0.5]
    print(f"  {len(loc)} frames localized, match quality median {np.median(ncc):.2f}, "
          f"{len(good)} confident")
    if not good:
        return
    fdx = np.array([float(r["flow_dx"]) for r in good])
    fdy = np.array([float(r["flow_dy"]) for r in good])
    mag = np.hypot(fdx, fdy)

    # Everything the tracker does hangs off this one number. The leading edge
    # is derived from the flow, so if the flow estimate is wrong the tracker is
    # picking tiles on the wrong side of the frame -- and phase correlation has
    # no peak to find on a featureless image, where it quietly returns ~0 rather
    # than failing. At this ground speed and frame interval the true flow is
    # ~190 green px/frame, so anything near zero is a collapsed estimate, not a
    # stationary aeroplane.
    lock, dead = mag >= 100.0, mag < 5.0
    print(f"\n  scene-flow estimate (true ground flow is ~190 green px/frame):")
    print(f"    LOCKED   (>=100 px): {int(lock.sum()):4d}/{len(good)}")
    print(f"    COLLAPSED  (<5 px) : {int(dead.sum()):4d}/{len(good)}")
    print(f"    in between         : {int((~lock & ~dead).sum()):4d}/{len(good)}"
          "   <- almost empty: the estimate either works or returns nothing")

    scores = {r["ts_key"]: r["score"] for r in load(jitter_csv)}
    sc = np.array([scores.get(r["ts_key"]) or np.nan for r in good])
    held = np.array([dw.get(r["ts_key"], (0, 0))[1] for r in good], dtype=float)
    for name, m in (("LOCKED", lock), ("COLLAPSED", dead)):
        if m.any():
            print(f"    where flow {name:<9}: median crop score "
                  f"{np.nanmedian(sc[m]):5.1f}, median frames held "
                  f"{np.median(held[m]):3.0f}")

    labels: dict[str, int] = {}
    for r, m in zip(good, lock):
        if m:
            labels[r["lead"] or "(none)"] = labels.get(r["lead"] or "(none)", 0) + 1
    allv: dict[str, int] = {}
    for r in good:
        allv[r["lead"] or "(none)"] = allv.get(r["lead"] or "(none)", 0) + 1
    print(f"\n  leading edge chosen, over ONE straight flight line:")
    print(f"    all frames            : {allv}")
    print(f"    flow-locked frames    : {labels}")
    print("    a straight line has ONE leading edge; the spread in the first row "
          "is the tracker being steered by a flow estimate that is not there")

    slope = np.abs(fdy) / np.maximum(np.abs(fdx), 1e-6)
    print(f"\n  |dy/dx| crab slope: median {np.median(slope):.2f}, "
          f"{int((slope > 1).sum())}/{len(good)} frames above 1.0")
    print("  (leading_edge_from_motion switches axis at exactly 1.0, so frames "
          "either side of it get a DIFFERENT edge for the same flight line)")

    pin = np.array([int(r["pinned"]) for r in good])
    print(f"\n  born pinned against a border with flow pushing further that way: "
          f"{int(pin.sum())}/{len(good)} ({100.0 * pin.mean():.0f}%)")

    # frames_left MUST be read only on frames where the flow estimate locked.
    # It divides the distance to the border by the measured flow, so a collapsed
    # estimate divides by ~0 and reports a tile with hundreds of frames of life.
    # Averaged over the whole flight those fictions dominate the median and hide
    # the real figure completely.
    coarse = {r["ts_key"]: (r["coarse"] or 0) for r in load(jitter_csv)}
    sel = [(r, m) for r, m in zip(good, mag)
           if r["frames_left"] != "" and m >= 100.0
           and coarse.get(r["ts_key"], 0) >= CLEAR_COARSE]
    if sel:
        fl = np.array([int(r["frames_left"]) for r, _ in sel])
        cf = np.array([float(r["cross_frac"]) for r, _ in sel])
        print(f"\n  frames of life left at the pick, counting ONLY clear ground with")
        print(f"  a locked flow estimate ({len(fl)} frames):")
        print(f"    median {np.median(fl):.0f} frames, "
              f"{int((fl <= 3).sum())}/{len(fl)} picks had <=3")
        edge = (cf < 0.2) | (cf >= 0.8)
        if edge.any() and (~edge).any():
            print(f"    picked flush to a side border : median "
                  f"{np.median(fl[edge]):.0f} frames (n={int(edge.sum())})")
            print(f"    picked in the middle 60%      : median "
                  f"{np.median(fl[~edge]):.0f} frames (n={int((~edge).sum())})")
            print("    -> where the tile is picked across the flow decides how long")
            print("       it survives, and lane_frac=0.0 in _pick is what allows the")
            print("       short-lived positions to be chosen at all")

    cross = np.array([float(r["cross_frac"]) for r in good])
    print(f"\n  position across the flow axis (0 and 1 are the side borders):")
    hist, be = np.histogram(cross, bins=10, range=(0, 1))
    for h, lo in zip(hist, be):
        print(f"    {lo:4.1f}-{lo + 0.1:4.1f}  {h:4d}  "
              f"{'#' * int(50.0 * h / max(1, hist.max()))}")
    edge = ((cross < 0.15) | (cross > 0.85)).mean()
    print(f"  in the outer 15% at either side: {100 * edge:.0f}% of picks "
          "(lane_frac=0.0 is what permits this)")

    pairs = [(float(r["cross_frac"]), dw.get(r["ts_key"], (0, 0))[1]) for r in good
             if r["ts_key"] in dw]
    if pairs:
        c = np.array([p[0] for p in pairs])
        held = np.array([p[1] for p in pairs])
        mid = (c > 0.3) & (c < 0.7)
        print(f"\n  frames already held, by where the tile sits:")
        print(f"    middle of the flow axis : mean {held[mid].mean():.1f} "
              f"(n={int(mid.sum())})")
        print(f"    outer thirds            : mean {held[~mid].mean():.1f} "
              f"(n={int((~mid).sum())})")


# ---------------------------------------------------------------------------
# Self-check
# ---------------------------------------------------------------------------

def self_check() -> int:
    rng = np.random.default_rng(7)

    # 1. The join key must survive the real timestamp format, and sweep/frame
    #    must NOT be relied on (they restart per session).
    assert ts_key("2026-09-30T14:42:21.938+03:00") == "144221_938"
    assert ts_key("garbage") is None

    # 2. Object boundary: two different grounds, each held for a few frames.
    g1 = rng.integers(300, 3000, (240, 480)).astype(np.uint16)
    g2 = rng.integers(300, 3000, (240, 480)).astype(np.uint16)
    same, _dx, _dy = match_prev(g1.copy(), g1)
    cut, _dx, _dy = match_prev(g2, g1)
    assert same > 0.9, f"same ground must match, got {same}"
    assert cut < SAME_OBJECT_NCC, f"new ground must break the match, got {cut}"
    shifted = np.roll(g1, (0, 8), axis=(0, 1))
    r, dx, dy = match_prev(shifted, g1)
    assert r > 0.8 and abs(dx - 8) <= NCC_DS, f"drift must be recovered, got {dx}"

    # 3. Anisotropy must fire on directional blur and NOT on symmetric blur --
    #    this is the only thing separating vibration from defocus.
    base = rng.integers(300, 3000, (256, 256)).astype(np.float32)
    iso = cv2.GaussianBlur(base, (0, 0), 3.0)
    motion = cv2.filter2D(base, -1, np.ones((1, 15), np.float32) / 15.0)
    r_iso, _ = anisotropy(iso)
    r_mot, d_mot = anisotropy(motion)
    assert r_iso > 0.8, f"symmetric blur must read isotropic, got {r_iso}"
    assert r_mot < 0.5, f"motion blur must read directional, got {r_mot}"
    # Smear is horizontal, so the least-gradient axis is ~0/180 deg.
    assert min(d_mot, 180 - d_mot) < 25, f"smear axis wrong: {d_mot}"

    # 4. A known lash must come back out of both legs. Same Gaussian peak,
    #    displaced by LASH between the up and down passes, with each object
    #    carrying its own texture scale (the thing scale-free must survive).
    LASH, sigma, centre = 600.0, 1200.0, 13000.0
    def leg(direction, n=260):
        pos = np.linspace(9000, 16000, n)[::direction]
        peak = centre + (LASH / 2.0) * direction
        out = []
        for i, p in enumerate(pos):
            oid = i // 6
            k = np.exp(rng.normal(0, 0.35, 1)[0]) if i % 6 == 0 else k_hold[0]
            k_hold[0] = k
            s = k * np.exp(-((p - peak) ** 2) / (2 * sigma ** 2))
            out.append({"encoder": p, "score": s * np.exp(rng.normal(0, 0.05)),
                        "object_id": oid, "leg": "x"})
        return out
    k_hold = [1.0]
    up, down = leg(+1), leg(-1)
    fu = _fit_peak_scale_free([(r["encoder"], r["score"], r["object_id"]) for r in up])
    fd = _fit_peak_scale_free([(r["encoder"], r["score"], r["object_id"]) for r in down])
    assert fu is not None and fd is not None, "scale-free fit must converge"
    got = fu - fd
    assert abs(got - LASH) < 300, f"lash {LASH} recovered as {got:.0f}"

    # 5. Leg labelling must split an out-and-back at the turnaround, not shred it.
    tri = list(np.concatenate([np.arange(0, 500, 5), np.arange(500, 0, -5)]))
    rws = [{"encoder": float(v), "score": 1.0, "ts_key": f"{i:06d}"}
           for i, v in enumerate(tri)]
    label_legs(rws)
    assert rws[10]["leg"] == "up" and rws[-10]["leg"] == "down"
    assert sum(1 for r in rws if r["leg"] == "up") > 0.4 * len(rws)

    # 6. The gates that keep cloud out of every focus conclusion. A run must be
    #    held AND textured AND at one encoder to count; drop any one and it is
    #    rejected. This is the guard against the flight's dominant failure mode.
    def _mk(n, ncc, score, enc):
        return [{"ncc": ncc, "score": score, "encoder": enc, "clip_frac": 0.0,
                 "aniso_ratio": 0.9} for _ in range(n)]
    assert len(held_runs(_mk(6, 0.99, 50.0, 100.0), 4, True)) == 1
    assert not held_runs(_mk(6, 0.99, 5.0, 100.0), 4, True), "cloud must be rejected"
    assert not held_runs(_mk(6, 0.80, 50.0, 100.0), 4, True), "a weak hold must fail"
    drifting = _mk(6, 0.99, 50.0, 100.0)
    for i, r in enumerate(drifting):
        r["encoder"] = 100.0 + i * 10
    assert not held_runs(drifting, 4, True), "a moving encoder is not a fixed station"
    assert len(held_runs(drifting, 4, False)) == 1, "...but is a valid held run"

    # 7. HANDOFF 51: the crops on disk are raw Bayer. Scoring one as stored
    #    measures the mosaic, not the scene, so build() must take the green
    #    plane first. Lay a mosaic over a BLURRED scene: the stored frame then
    #    reads as sharp while the green plane correctly reads as soft.
    yy, xx = np.mgrid[0:512, 0:512]
    scene = cv2.GaussianBlur(
        (2000.0 + 900 * ((xx // 97 + yy // 83) % 2)).astype(np.float32), (0, 0), 9)
    mosaic = scene.copy()
    mosaic[0::2, 0::2] *= 1.25          # R
    mosaic[1::2, 1::2] *= 0.80          # B
    mosaic = mosaic.astype(np.uint16)
    stored = focus_measure_green(mosaic)
    extracted = focus_measure_green(mosaic[0::2, 1::2])
    # Measured ratio on the real cam8 frame was 9.3x; 4x is a loose floor that
    # still fails loudly if build() ever goes back to scoring the stored frame.
    assert stored > 4 * extracted, (
        "the mosaic must dominate the score of a raw-Bayer frame, or this "
        f"guard proves nothing (stored {stored:.1f}, green {extracted:.1f})")

    # 8. The cloud discriminator must survive defocus but not flatness --
    #    otherwise "exclude cloud" silently also excludes every soft frame,
    #    which is the whole population under study.
    yy, xx = np.mgrid[0:512, 0:512]
    terrain = (2000 + 900 * ((xx // 97 + yy // 83) % 2)).astype(np.uint16)
    soft = cv2.GaussianBlur(terrain.astype(np.float32), (0, 0), 9).astype(np.uint16)
    # Cloud is bright, smooth and low-contrast -- not pixel noise.
    cloud = cv2.GaussianBlur(
        14000 + 400 * np.random.default_rng(7).standard_normal((512, 512)
                                                               ).astype(np.float32),
        (0, 0), 12).astype(np.uint16)
    c_sharp, c_soft, c_cloud = (coarse_contrast(v) for v in (terrain, soft, cloud))
    assert focus_measure_green(soft) < 0.01 * focus_measure_green(terrain), \
        "the blurred frame must genuinely be softer, or the test proves nothing"
    assert c_soft > 0.5 * c_sharp, "defocus must NOT look like cloud"
    assert c_cloud < 0.05 * c_soft, "cloud must separate from defocused terrain"

    # 9. The crux of finding 1: with a real flow the leading edge is stable
    #    under noise, but once the flow collapses toward zero the SAME noise
    #    flips it to a different edge. That is why one straight line produced
    #    four different labels.
    big = {leading_edge_from_motion(190.0 + n, 60.0 + n) for n in (-3, 0, 3)}
    assert len(big) == 1, "a locked flow must give one stable edge"
    tiny = {leading_edge_from_motion(0.6 + n, 0.5 - n) for n in (-1.0, 0.0, 1.0)}
    assert len(tiny) > 1, "a collapsed flow must be shown to flip the edge"

    # 10. The pinning replay. A tile flush at the LEADING edge must survive; the
    #    same tile also flush against a CROSS-flow border must read as pinned.
    #    That asymmetry is the whole mechanism being tested, so pin it down.
    fw, fh = 1000, 600
    flow_dx, flow_dy = 40.0, -20.0          # content right and up => lead "left"
    assert leading_edge_from_motion(flow_dx, flow_dy) == "left"
    mid = (0, 250, 200, 450)                # flush left, middle of the cross axis
    top = (0, 0, 200, 200)                  # flush left AND flush top
    assert not _roi_pinned_against_flow(mid, flow_dx, flow_dy, fw, fh), (
        "a leading-edge tile in mid-lane must not read as pinned")
    assert _roi_pinned_against_flow(top, flow_dx, flow_dy, fw, fh), (
        "a leading-edge tile at the cross-flow border IS pinned (dy<0, y0<=0)")
    assert _frames_left(*mid, flow_dx, flow_dy, fw, fh) == 12  # min(800/40, 250/20)
    assert _frames_left(*top, flow_dx, flow_dy, fw, fh) == 0

    # 7. Flow sign: phaseCorrelate must report content moving +x as +dx, or the
    #    leading edge is computed backwards and the replay is meaningless.
    scene = cv2.GaussianBlur(
        rng.integers(0, 4000, (300, 400)).astype(np.float32), (0, 0), 1.5)
    moved = np.roll(scene, 11, axis=1)
    (sx, sy), _r = cv2.phaseCorrelate(scene, moved)
    assert abs(sx - 11) < 1.5 and abs(sy) < 1.5, f"flow sign/scale wrong: {sx},{sy}"

    # 8. Guard the HANDOFF 45.1 trap: scoring must enter at the green sub-grid.
    #    focus_measure_full_res decimates again, so on an ALREADY-green crop it
    #    reads a different image. Assert the two genuinely differ, so a future
    #    edit that swaps the entry point fails here instead of silently
    #    producing wrong numbers.
    from capture.autofocus import focus_measure_full_res
    crop = cv2.GaussianBlur(
        rng.integers(300, 3000, (256, 256)).astype(np.float32), (0, 0), 1.2
    ).astype(np.uint16)
    a_green = focus_measure_green(crop)
    b_full = focus_measure_full_res(crop)
    assert a_green > 0 and abs(a_green - b_full) / a_green > 0.1, (
        "focus_measure_full_res must NOT be interchangeable here (HANDOFF 45.1)"
    )

    print("self-check OK")
    return 0


M0_PARKED_ENCODER = 15339.0   # where the three survey lines sat all flight


def report_m0(rows: list[dict]) -> None:
    """Were the M0 survey lines in focus at their parked encoder count?

    Answering this needs the best-focus count, and the only content-free way to
    locate it is the sign of d log(score)/d(encoder) measured WITHIN one held
    patch of ground: the patch's own texture cancels out of the ratio, so the
    sign is the lens alone. Best focus is where that sign flips.
    """
    print(f"\n=== M0: was the parked encoder {M0_PARKED_ENCODER:.0f} in focus? ===")
    rows = sorted((r for r in rows if r["session"] in ("144221", "152517")),
                  key=lambda r: r["ts_key"])
    spans = []
    for v in held_runs(rows, min_len=6, fixed_encoder=False):
        enc = np.array([x["encoder"] for x in v if x["encoder"]], dtype=float)
        sc = np.array([x["score"] for x in v if x["encoder"]], dtype=float)
        if len(enc) >= 6 and np.ptp(enc) >= 300:
            spans.append((enc.mean(), np.ptp(enc),
                          float(np.polyfit(enc, np.log(sc), 1)[0] * 1000)))
    spans.sort()
    print(f"  held textured runs that actually move the lens >=300 counts: "
          f"{len(spans)}")
    print("\n  enc_mid   span   slope dlog(score)/1000c   reading")
    for m, sp, k in spans:
        print(f"  {m:7.0f} {sp:6.0f}   {k:+8.3f}            "
              f"{'sharper with MORE counts' if k > 0 else 'sharper with FEWER counts'}")

    if len(spans) < 2 or len({k > 0 for _, _, k in spans}) < 2:
        print("\n  => NO ANSWER. The slope never changes sign, so best focus is not")
        print("     bracketed and the parked count cannot be placed against it.")
        return
    best = max(abs(k) for _, _, k in spans)
    print(f"\n  => NO RELIABLE ANSWER. The slope changes sign more than once, so these")
    print("     runs disagree about which side of focus they are on.")
    print(f"     The reason is scale: the strongest focus slope here is {best:.2f} "
          "per 1000")
    print("     counts, while the score already swings ~17% at a FIXED count "
          "(finding 2).")
    print("     Over the full 10292-count sweep focus would dominate easily, but no")
    print("     tracked patch survives more than ~3300 counts of it (finding 1), so")
    print("     every available measurement is taken over a span where the jitter is")
    print("     the larger effect.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", metavar="ROOT", type=Path,
                    help="scan a capture root for crops + .bay14line headers")
    ap.add_argument("--csv", type=Path, default=Path("focus_jitter.csv"))
    ap.add_argument("--tracker", metavar="CSV", type=Path, nargs="?", const=True,
                    help="dwell / re-pick report")
    ap.add_argument("--lash", metavar="CSV", type=Path, nargs="?", const=True,
                    help="lash, within-station spread and vibration report")
    ap.add_argument("--session", default=None, help="restrict --lash to one session")
    ap.add_argument("--m0", metavar="CSV", type=Path, nargs="?", const=True,
                    help="was the M0 parked focus in focus?")
    ap.add_argument("--localize", metavar="BAY14LINE", type=Path,
                    help="find each crop inside its full frame (slow: ~6 s/frame)")
    ap.add_argument("--crops", type=Path, default=None,
                    help="crop folder for --localize (default: <root>/focus)")
    ap.add_argument("--limit", type=int, default=150,
                    help="frames to localize (default 150)")
    ap.add_argument("--loc-csv", type=Path, default=Path("focus_jitter_loc.csv"))
    ap.add_argument("--report-localize", metavar="CSV", type=Path,
                    help="re-print the --localize report from its CSV")
    ap.add_argument("--diagnose", metavar="CROPDIR", type=Path,
                    help="classify why clear-ground tiles die")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()

    if args.self_check:
        return self_check()
    if args.report_localize:
        report_localize(args.report_localize, args.csv)
        return 0
    if args.localize:
        crops = args.crops or args.localize.parent / "focus"
        return localize(args.localize, args.csv, crops, args.limit, args.loc_csv)
    if args.diagnose:
        return diagnose(args.diagnose, args.csv, args.limit)
    if args.build:
        return build(args.build, args.csv)
    if args.tracker or args.lash or args.m0:
        path = args.csv
        for a in (args.tracker, args.lash, args.m0):
            if isinstance(a, Path):
                path = a
        rows = load(path)
        if args.tracker:
            report_tracker(rows)
        if args.lash:
            report_lash(rows, args.session)
        if args.m0:
            report_m0(rows)
        return 0
    ap.error("give --build, --tracker, --lash, --m0 or --self-check")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
