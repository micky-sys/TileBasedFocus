"""w10: content-independent PSF width of a frame from its natural step edges (HANDOFF sec.52).

For each of 5 fixed 1024-px ROIs (demosaiced green plane, full raw resolution): Canny edge
pixels -> keep straight, high-contrast, monotonic step edges -> edge spread function sampled
perpendicular to the edge (averaged +-6 px along it) -> 10-90% rise width in px. Per frame:
w10 (10th percentile of the pooled widths = the sharpest edges ~ the PSF), w20, w50, n_edges,
per-ROI w10 and w20 per edge-normal orientation bin.

This module is the recipe of sec.52.2-52.4 bit-for-bit; it lives in ``host/capture`` so the
live autofocus can import it, and ``tools/flight_edges.py`` (file readers + CLI) re-exports
it. The speed work for the autofocus (sec.54) changed only the IMPLEMENTATION: the per-edge
Python loop became array ops on the same float32 values, the structure-tensor coherence is
evaluated only at the Canny candidates, and the five ROIs run on a thread pool. Identity with
the original loop is asserted by ``tools/test_psf_w10_identical.py`` on recorded flight
frames; the calibration (floor 1.75 px, w10 ~= hypot(1.75, 2.4 sigma)) is asserted by
``tools/test_flight_edges_floor.py``.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

ROI = 1024
# ROI centres: {row: [cols]} -- top-L/top-R, centre, bottom-L/bottom-R of a 13400 x 9528 frame.
BANDS = {2382: [3350, 10050], 4764: [6700], 7146: [3350, 10050]}
S = np.arange(-10, 10.01, 0.5)   # profile samples along the edge normal (px)
TT = np.arange(-6, 6.01, 1.0)    # offsets along the edge that are averaged
NB = 6                           # orientation bins (30 deg)
MIN_W, MIN_H = max(max(v) for v in BANDS.values()) + ROI // 2, max(BANDS) + ROI // 2
MIN_EDGES_FRAME = 300            # sec.52.4: a frame-level w10 is trusted only above this
CLOUD_MEAN = 22000.0             # sec.52.4: ROI mean above this (16-bit scale) is cloud

# ponytail: one pool for the five ROIs. cv2 releases the GIL, the numpy parts mostly do; the
# ceiling is the GIL on the remaining Python glue, not the pool. Measured on the dev box: ~455
# -> ~140 ms per frame (sec.54). Upgrade path if the live loop needs more: a process pool.
_POOL = ThreadPoolExecutor(max_workers=len(sum(BANDS.values(), [])))


def _widths(esf: np.ndarray) -> np.ndarray:
    """10-90% rise widths of each ESF row, NaN where the row is not an acceptable step.

    Vectorised form of the sec.52.3 step-6/7 loop. Every intermediate keeps the float32 dtype
    the scalar loop had under NEP 50 (python floats are weak), so the results are the same
    bits; the only reordering is that the per-row gates are evaluated as masks.
    """
    a = esf[:, :6].mean(axis=1)
    b = esf[:, -6:].mean(axis=1)
    contrast = b - a
    ok = np.abs(contrast) >= np.maximum(300.0, 0.05 * (np.abs(a) + np.abs(b)))
    with np.errstate(divide="ignore", invalid="ignore"):
        e = (esf - a[:, None]) / contrast[:, None]                  # 0 -> 1 rising
    e_ok = np.where(ok[:, None], e, 0.0)                            # rows already rejected: inert
    ok &= (e_ok.min(axis=1) >= -0.15) & (e_ok.max(axis=1) <= 1.15)  # overshoot / line, not a step
    ge10, ge90 = e >= 0.1, e >= 0.9
    i10, i90 = ge10.argmax(axis=1), ge90.argmax(axis=1)
    ok &= ge10.any(axis=1) & ge90.any(axis=1) & (i10 > 0) & (i90 < len(S) - 1) & (i90 > i10)
    widths = np.full(len(esf), np.nan)
    if not ok.any():
        return widths
    r = np.flatnonzero(ok)
    i10, i90 = i10[r], i90[r]
    e0, e1 = e[r, i10 - 1], e[r, i10]
    s10 = S[i10 - 1] + (0.1 - e0) / (e1 - e0 + 1e-9) * 0.5
    e0, e1 = e[r, i90 - 1], e[r, i90]
    s90 = S[i90 - 1] + (0.9 - e0) / (e1 - e0 + 1e-9) * 0.5
    widths[r] = s90 - s10
    return widths


def edge_widths(g: np.ndarray, max_edges: int = 1500, rng=None):
    """``(widths, angles_deg)`` of the acceptable step edges in one green-plane ROI (float32).

    A fresh generator per call: a default-argument rng carried state across calls, so a ROI's
    1500-edge subsample (and hence w10 by ~0.02 px) depended on how many frames had been
    scored before it.
    """
    rng = rng or np.random.default_rng(0)
    gs = cv2.GaussianBlur(g, (0, 0), 1.0)
    gx = cv2.Sobel(gs, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gs, cv2.CV_32F, 0, 1, ksize=3)
    lo, hi = np.percentile(gs, (1, 99))
    # (gs - lo) * 255 / (hi - lo + 1e-9), clipped to 0..255, in float64 (lo/hi are float64
    # scalars, which NEP 50 does not demote) -- same ops in the same order, one buffer
    g8 = np.subtract(gs, lo, dtype=np.float64)
    np.multiply(g8, 255, out=g8)
    np.divide(g8, hi - lo + 1e-9, out=g8)
    np.clip(g8, 0, 255, out=g8)
    g8 = g8.astype(np.uint8)
    ed = cv2.Canny(g8, 40, 100)
    ys, xs = np.nonzero(ed[16:-16, 16:-16])
    ys += 16
    xs += 16
    if len(xs) == 0:
        return np.zeros(0), np.zeros(0)
    # straightness: coherence of the structure tensor in a 15x15 window. The box filters have
    # to run on the whole ROI, but the sqrt/divide only matters at the candidate pixels.
    jxx = cv2.boxFilter(gx * gx, -1, (15, 15))[ys, xs]
    jyy = cv2.boxFilter(gy * gy, -1, (15, 15))[ys, xs]
    jxy = cv2.boxFilter(gx * gy, -1, (15, 15))[ys, xs]
    coh = np.sqrt((jxx - jyy) ** 2 + 4 * jxy ** 2) / (jxx + jyy + 1e-6)
    keep = coh > 0.8
    ys, xs = ys[keep], xs[keep]
    if len(xs) > max_edges:
        sel = rng.choice(len(xs), max_edges, replace=False)
        ys, xs = ys[sel], xs[sel]
    if len(xs) == 0:
        return np.zeros(0), np.zeros(0)
    ang = np.arctan2(gy[ys, xs], gx[ys, xs])                      # edge normal
    nx, ny = np.cos(ang), np.sin(ang)
    tx, ty = -ny, nx
    # sample maps: for each edge, a (len(TT), len(S)) grid
    mx = xs[:, None, None] + S[None, None, :] * nx[:, None, None] + TT[None, :, None] * tx[:, None, None]
    my = ys[:, None, None] + S[None, None, :] * ny[:, None, None] + TT[None, :, None] * ty[:, None, None]
    samp = cv2.remap(g, mx.reshape(-1, len(S)).astype(np.float32),
                     my.reshape(-1, len(S)).astype(np.float32),
                     cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    esf = samp.reshape(len(xs), len(TT), len(S)).mean(axis=1)    # average along the edge
    widths = _widths(esf)
    ok = np.isfinite(widths)
    return widths[ok], (np.degrees(ang[ok]) % 180)


def _score_roi(roi: np.ndarray):
    """``(per_roi_w10, widths, angles, roi_mean)`` or ``None`` when the ROI is saturated.

    *roi* is the ROI plus its 2-px demosaic apron: raw Bayer (2-D) or an RGB export (3-D).
    """
    M = 2
    if roi.ndim == 3:
        g = roi[M:-M, M:-M, 1]
    else:
        g = cv2.cvtColor(np.ascontiguousarray(roi), cv2.COLOR_BayerBG2RGB)[M:-M, M:-M, 1]
    if (g >= 65000).mean() > 0.005:        # skip saturated (cloud) ROIs; tolerate hot pixels
        return None
    wd, an = edge_widths(g.astype(np.float32))
    return (np.percentile(wd, 10) if len(wd) >= 40 else np.nan), wd, an, float(g.mean())


def array_edges(mm, enc=None, flip_lr=False, scale=1, pct=(), ksharp=()) -> dict:
    """Per-frame stats from a uint16 frame at 16-bit scale (14-bit << 2); memmap or ndarray.

    (H, W) = raw Bayer, demosaiced per ROI with the bilinear COLOR_BayerBG2RGB. (H, W, 3) = an
    RGB export made with that SAME call (Export Studio / Bay14Reader), whose green channel is
    therefore the identical plane -- so raw and debayered data score apples-to-apples.
    *flip_lr* undoes the export's mirror so ROI k is the same sensor region in both; *scale*
    multiplies 14-bit-range data up to the 16-bit scale the thresholds assume. *enc* is the
    capture app's ``focus`` block (HANDOFF sec.45/49.2) for the encoder columns, or None.

    Besides the sec.52.4 columns this returns ``med_mean`` (median of the scored ROIs' means,
    the cloud gate's input, same name as the flight CSVs) and ``n_roi`` (ROIs that produced a
    w10), which the autofocus gates read. ``pct`` adds ``w{p}`` (p-th percentile of the pooled
    widths) and ``ksharp`` adds ``k{n}`` (mean of the n sharpest edges) for percentile studies
    (sec.54.11); both default to off and change nothing else.
    """
    return score_rois(crop_rois(mm, flip_lr, scale), enc, pct, ksharp)


def roi_boxes() -> list:
    """The five ROIs as sensor-pixel ``(x0, y0, x1, y1)`` boxes, apron included, in ``crop_rois``
    order -- so a pipeline crop of a full frame at these boxes is exactly ``crop_rois(frame)``."""
    M, h = 2, ROI // 2
    return [(cx - h - M, cy - h - M, cx + h + M, cy + h + M) for cy, xs in BANDS.items() for cx in xs]


def crop_rois(mm, flip_lr=False, scale=1, copy=False) -> list:
    """The five ROIs (each with its 2-px demosaic apron) of a full frame, as views unless ``copy``
    (the live scorer copies them out of a shared-memory slot that the writer recycles)."""
    M = 2   # demosaic margin: bilinear output depends on 1-px neighbours, so a 2-px apron makes
            # the ROI's green plane identical to a full-frame demosaic (what an RGB export
            # holds) instead of carrying OpenCV's 1-px border ring
    rois = []
    for cy, xs in BANDS.items():
        y0 = cy - ROI // 2
        band = mm[y0 - M:y0 + ROI + M]
        if isinstance(band, np.memmap):
            band = np.array(band)   # one contiguous row-band read; strided ROI reads were 20 s on the USB disk
        if flip_lr:
            band = band[:, ::-1]
        if scale != 1:
            band = (band.astype(np.uint32) * scale).clip(0, 65535).astype(np.uint16)
        for cx in xs:
            x0 = cx - ROI // 2
            roi = band[:, x0 - M:x0 + ROI + M]
            rois.append(np.array(roi, order="C") if copy else roi)
    return rois


def score_rois(rois, enc=None, pct=(), ksharp=()) -> dict:
    """``array_edges`` from the five ROIs of ``crop_rois``."""
    enc = enc or {}
    results = list(_POOL.map(_score_roi, rois))

    W, A, per_roi, means = [], [], [], []
    for res in results:
        if res is None:
            per_roi.append(np.nan)
            continue
        r10, wd, an, mean = res
        per_roi.append(r10)
        W.append(wd)
        A.append(an)
        means.append(mean)
    W = np.concatenate(W) if W else np.zeros(0)
    A = np.concatenate(A) if A else np.zeros(0)
    ok = len(W) >= 40
    out = dict(n_edges=len(W),
               w10=np.percentile(W, 10) if ok else np.nan,
               w20=np.percentile(W, 20) if ok else np.nan,
               w50=np.median(W) if ok else np.nan,
               encoder=enc.get("encoder_count", np.nan),
               enc_stale=int(bool(enc.get("encoder_stale", False))) if enc else "",
               med_mean=float(np.median(means)) if means else np.nan,
               n_roi=int(sum(np.isfinite(v) for v in per_roi)))
    for k, v in enumerate(per_roi):
        out[f"r{k}_w10"] = v
    for p in pct:
        out[f"w{p}"] = np.percentile(W, p) if ok else np.nan
    for n in ksharp:
        out[f"k{n}"] = float(np.partition(W, n - 1)[:n].mean()) if len(W) >= n else np.nan
    for b in range(NB):
        m = (A >= b * 180 / NB) & (A < (b + 1) * 180 / NB)
        out[f"w20_ang{b * 180 // NB}"] = np.percentile(W[m], 20) if m.sum() >= 20 else np.nan
    return out


def frame_ok(stats: dict, *, min_edges: int = MIN_EDGES_FRAME, min_roi: int = 1,
             cloud_mean: float = CLOUD_MEAN) -> bool:
    """The sec.52.4 frame-level reliability gate on an ``array_edges`` result."""
    w = stats.get("w10", np.nan)
    if not (isinstance(w, (int, float)) and np.isfinite(w)):
        return False
    if stats.get("n_edges", 0) < min_edges or stats.get("n_roi", 0) < min_roi:
        return False
    if stats.get("enc_stale") == 1:
        return False
    rm = stats.get("med_mean", np.nan)
    return not (isinstance(rm, float) and np.isfinite(rm) and rm > cloud_mean)
