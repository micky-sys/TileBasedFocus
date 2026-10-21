"""The sec.54 speed-up of the w10 metric must not move a single number (HANDOFF sec.52 calibration stays valid).

Two layers:
  1. Always: host/capture/psf_w10.edge_widths vs a verbatim copy of the ORIGINAL per-edge loop (below) on synthetic
     scenes -- identical widths and angles, bit for bit.
  2. If D:\\_flight_quality\\af_study\\ref_old_w10.csv exists (written by the pre-speed-up tools/flight_edges.py on the
     frames listed in ref_frames.txt): re-score those frames and assert every column is identical; print old vs new
     in-RAM timing on one frame.
Run: py -3 tools/test_psf_w10_identical.py   (~15 s synthetic; +~40 s with the flight frames)"""
import csv, math, os, sys, time, numpy as np, cv2
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flight_edges import frame_edges, array_edges, S, TT
from capture.psf_w10 import edge_widths, _POOL  # noqa: F401

REF_DIR = r"D:\_flight_quality\af_study"


def edge_widths_ref(g, max_edges=1500, rng=None):
    """Verbatim copy of tools/flight_edges.py edge_widths as of 2026-10-15, before the speed-up."""
    rng = rng or np.random.default_rng(0)
    gs = cv2.GaussianBlur(g, (0, 0), 1.0)
    gx = cv2.Sobel(gs, cv2.CV_32F, 1, 0, ksize=3); gy = cv2.Sobel(gs, cv2.CV_32F, 0, 1, ksize=3)
    lo, hi = np.percentile(gs, (1, 99)); g8 = np.clip((gs - lo) * 255 / (hi - lo + 1e-9), 0, 255).astype(np.uint8)
    ed = cv2.Canny(g8, 40, 100)
    ys, xs = np.nonzero(ed[16:-16, 16:-16]); ys += 16; xs += 16
    if len(xs) == 0: return np.zeros(0), np.zeros(0)
    jxx = cv2.boxFilter(gx * gx, -1, (15, 15)); jyy = cv2.boxFilter(gy * gy, -1, (15, 15)); jxy = cv2.boxFilter(gx * gy, -1, (15, 15))
    coh = np.sqrt((jxx - jyy) ** 2 + 4 * jxy ** 2) / (jxx + jyy + 1e-6)
    keep = coh[ys, xs] > 0.8
    ys, xs = ys[keep], xs[keep]
    if len(xs) > max_edges:
        sel = rng.choice(len(xs), max_edges, replace=False); ys, xs = ys[sel], xs[sel]
    if len(xs) == 0: return np.zeros(0), np.zeros(0)
    ang = np.arctan2(gy[ys, xs], gx[ys, xs])
    nx, ny = np.cos(ang), np.sin(ang); tx, ty = -ny, nx
    mx = xs[:, None, None] + S[None, None, :] * nx[:, None, None] + TT[None, :, None] * tx[:, None, None]
    my = ys[:, None, None] + S[None, None, :] * ny[:, None, None] + TT[None, :, None] * ty[:, None, None]
    samp = cv2.remap(g, mx.reshape(-1, len(S)).astype(np.float32), my.reshape(-1, len(S)).astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    esf = samp.reshape(len(xs), len(TT), len(S)).mean(axis=1)
    a = esf[:, :6].mean(axis=1); b = esf[:, -6:].mean(axis=1)
    contrast = b - a
    widths = np.full(len(xs), np.nan)
    for i in range(len(xs)):
        c = contrast[i]
        if abs(c) < max(300.0, 0.05 * (abs(a[i]) + abs(b[i]))): continue
        e = (esf[i] - a[i]) / c
        if e.min() < -0.15 or e.max() > 1.15: continue
        try:
            i10 = np.where(e >= 0.1)[0][0]; i90 = np.where(e >= 0.9)[0][0]
        except IndexError:
            continue
        if i10 == 0 or i90 >= len(S) - 1 or i90 <= i10: continue
        s10 = S[i10 - 1] + (0.1 - e[i10 - 1]) / (e[i10] - e[i10 - 1] + 1e-9) * 0.5
        s90 = S[i90 - 1] + (0.9 - e[i90 - 1]) / (e[i90] - e[i90 - 1] + 1e-9) * 0.5
        widths[i] = s90 - s10
    ok = np.isfinite(widths)
    return widths[ok], (np.degrees(ang[ok]) % 180)


def synthetic(rng, sig, n=1024):
    """Steps, a thin line (must be rejected as not-a-step), ringing, blur, flight-like noise; mosaicked + demosaiced."""
    yy, xx = np.mgrid[:n, :n].astype(np.float32)
    img = np.full((n, n), 6000.0, np.float32)
    for _ in range(30):
        th = rng.uniform(0, np.pi); c = rng.uniform(0, n)
        d = (xx - c) * np.cos(th) + (yy - c * rng.uniform(0.3, 1.7)) * np.sin(th)
        img += np.where(d > 0, rng.uniform(1000, 9000), 0.0) * rng.choice([-1, 1])
    img[:, 400:403] += 7000                                  # a line
    img = np.clip(img, 1500, 30000)
    if sig: img = cv2.GaussianBlur(img, (0, 0), sig)
    img += rng.normal(0, 1, img.shape) * np.sqrt(img * 4 + 100 ** 2)
    raw = np.clip(img, 0, 65535).astype(np.uint16)
    return cv2.cvtColor(raw, cv2.COLOR_BayerBG2RGB)[..., 1].astype(np.float32)


def main():
    rng = np.random.default_rng(5)
    for k, sig in enumerate((0.0, 0.8, 1.6, 2.5, 4.0)):
        g = synthetic(rng, sig)
        w_ref, a_ref = edge_widths_ref(g)
        w_new, a_new = edge_widths(g)
        assert w_ref.shape == w_new.shape and np.array_equal(w_ref, w_new), f"widths differ at sigma {sig}"
        assert np.array_equal(a_ref, a_new), f"angles differ at sigma {sig}"
        print(f"synthetic sigma {sig:.1f}: {len(w_new)} edges, w10 {np.percentile(w_new, 10):.3f} -- identical")
    # a saturated / empty / tiny ROI must not crash either path
    assert edge_widths(np.full((300, 300), 5000, np.float32))[0].size == 0

    ref_csv = os.path.join(REF_DIR, "ref_old_w10.csv")
    if not os.path.exists(ref_csv):
        print("no flight reference CSV -- synthetic identity only"); print("ok"); return 0
    ref = {r["file"]: r for r in csv.DictReader(open(ref_csv, newline=""))}
    frames = [ln.strip() for ln in open(os.path.join(REF_DIR, "ref_frames.txt")) if ln.strip()]
    cols = ["n_edges", "w10", "w20", "w50"] + [f"r{k}_w10" for k in range(5)] + [f"w20_ang{b}" for b in range(0, 180, 30)]
    checked = 0
    for p in frames:
        if not os.path.exists(p): continue
        new = frame_edges(p); old = ref[os.path.basename(p)]
        for c in cols:
            o, n = old[c], new[c]
            o_f = float(o) if o not in ("", "nan") else math.nan
            n_f = float(n) if isinstance(n, (int, float, np.floating, np.integer)) and n == n else math.nan
            same = (math.isnan(o_f) and math.isnan(n_f)) or o_f == n_f
            assert same, f"{os.path.basename(p)} {c}: old {o} new {n}"
        checked += 1
    print(f"{checked} recorded frames: every column identical to the pre-speed-up CSV")

    # timing, in RAM (the live app has the frame in RAM already)
    import tifffile
    p = next(f for f in frames if os.path.exists(f) and "2439008_2026093" in f)
    with tifffile.TiffFile(p) as t:
        pg = t.pages[0]; full = np.array(np.memmap(p, dtype=np.uint16, mode="r", offset=int(pg.dataoffsets[0]), shape=pg.shape))
    array_edges(full)
    t0 = time.perf_counter(); n_rep = 5
    for _ in range(n_rep): array_edges(full)
    new_ms = (time.perf_counter() - t0) / n_rep * 1e3
    # old path = reference loop per ROI, serial (same ROIs, same demosaic)
    from capture.psf_w10 import BANDS, ROI
    t0 = time.perf_counter()
    for cy, xs in BANDS.items():
        band = np.array(full[cy - 514:cy + 514])
        for cx in xs:
            g = cv2.cvtColor(band[:, cx - 514:cx + 514], cv2.COLOR_BayerBG2RGB)[2:-2, 2:-2, 1].astype(np.float32)
            edge_widths_ref(g)
    old_ms = (time.perf_counter() - t0) * 1e3
    print(f"in-RAM per frame (5 ROIs): old {old_ms:.0f} ms -> new {new_ms:.0f} ms  ({old_ms / new_ms:.1f}x)")
    print("ok"); return 0


if __name__ == "__main__":
    sys.exit(main())
