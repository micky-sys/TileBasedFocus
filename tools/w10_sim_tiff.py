"""Closed-loop w10 autofocus over a REAL orthophoto with a synthetic lens (HANDOFF sec.54.14).

The engine (host/capture/af_w10.py) is driven exactly as in ``af_w10_replay.py``, but the frames are
RENDERED instead of replayed: a 13400 x 9528 window rolls along a large orthophoto at the aircraft's
ground speed (one picture pixel = 5 cm), and the five sec.52 ROIs are cut from it, blurred with an
isotropic Gaussian whose sigma follows the lens's distance from true focus, scaled to the camera's
16-bit level, given shot-like noise and scored with the real ``score_rois``. The truth is therefore
known exactly (the encoder count the blur was computed from), the ground changes every frame like a
flight, and the content is a real city -- not the recorded V-curves' content, which is what the
replay already tests.

Lens model (from the bench V-curves, 54.13): sigma(d) = hypot(sigma0, d / counts_per_sigma_px) with
sigma0 = 1.26 px (w0 ~= 3.5 px through the metric's hypot(1.75, 2.4 sigma)) and 600 c per px of
defocus sigma (depth of focus +-380 c for +0.3 px). Motor-side encoder with 1000 c of lash, +-2 c
of reading noise, same as ``SyntheticLens``.

    py -3 tools/w10_sim_tiff.py "Running TIF/Bnei_brak.tif" "Running TIF/Pardes_hana.tif" [--starts 3]
                                [--speed 40] [--gsd-cm 5] [--seed 0] [--trace]
    py -3 tools/w10_sim_tiff.py --self-check     renderer: w10 of a blurred step edge follows the lens model
"""
from __future__ import annotations

import math, os, sys, time
import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "host"))
from capture.af_w10 import FRAME_S, W10Config  # noqa: E402
from capture.psf_w10 import ROI, roi_boxes, score_rois  # noqa: E402
from af_w10_replay import SyntheticLens, run_engine  # noqa: E402

FRAME_W, FRAME_H = 13400, 9528
SIGMA0_PX = 1.26             # in-focus lens PSF sigma: hypot(1.75, 2.4 * 1.26) = 3.5 px = the bench w0
DEPTH_C = 380.0              # bench depth of focus (54.13): counts either side of focus for +0.3 px of w10
COUNTS_PER_SIGMA_PX = 600.0  # defocus sigma grows 1 px per 600 c, which puts +0.3 px at DEPTH_C
LEVEL = 64.0                 # 8-bit ortho -> 16-bit scale: mean ~120 -> ~7700, the camera's bench level
NOISE_DN = 60.0              # shot-like noise at that level, 16-bit scale
MARGIN = 32                  # blur apron around each ROI crop (3 sigma at the far end of the back-off)


def sigma_for(defocus_counts: float) -> float:
    return math.hypot(SIGMA0_PX, abs(defocus_counts) / COUNTS_PER_SIGMA_PX)


def render(green8: np.ndarray, sigma: float, rng) -> np.ndarray:
    """One ROI (with its 2-px demosaic apron) as the scorer's RGB-at-16-bit view, from an 8-bit green
    crop that carries MARGIN extra pixels on every side."""
    g = cv2.GaussianBlur(green8.astype(np.float32), (0, 0), sigma)[MARGIN:-MARGIN, MARGIN:-MARGIN]
    g = g * LEVEL + rng.normal(0.0, NOISE_DN, g.shape).astype(np.float32)
    g16 = np.clip(g, 0, 65535).astype(np.uint16)
    # _score_roi reads channel 1 of a 3-D ROI; a broadcast view keeps this a single plane in memory
    return np.broadcast_to(g16[:, :, None], g16.shape + (3,))


class Ortho:
    """Regional reads of a strip-LZW orthophoto (tifffile's zarr store decodes only the strips touched)."""

    def __init__(self, path: str):
        import tifffile, zarr
        self.path = path
        self._tif = tifffile.TiffFile(path)
        self.a = zarr.open(self._tif.pages[0].aszarr(), mode="r")
        self.rows, self.cols = self.a.shape[:2]

    def green(self, y0: int, x0: int, h: int, w: int) -> np.ndarray:
        return np.asarray(self.a[y0:y0 + h, x0:x0 + w, 1])

    def valid(self, y0: int, x0: int, h: int, w: int) -> bool:
        """Alpha fully opaque (orthophotos carry no-data margins)."""
        return self.a.shape[2] < 4 or bool((np.asarray(self.a[y0:y0 + h, x0:x0 + w, 3]) == 255).all())


class OrthoLens(SyntheticLens):
    """``SyntheticLens`` motor/lash model; frames are rendered from the rolling ortho window."""

    def __init__(self, ortho: Ortho, e0_lens: float, start_pos: float, col0: int, row0: int,
                 px_per_frame: float, seed: int, lash: float = 1000.0):
        super().__init__(e0_lens=e0_lens, lash=lash, seed=seed)
        self.pos = float(start_pos); self.lens = self.pos + lash / 2
        self.o, self.col0, self.row0, self.px_per_frame = ortho, col0, row0, px_per_frame
        self.boxes = roi_boxes()
        self.score_s = 0.0; self.read_s = 0.0

    def crops8(self, frame_index: int) -> list:
        top = self.row0 + int(round(frame_index * self.px_per_frame))
        out = []
        for x0, y0, x1, y1 in self.boxes:
            t = time.perf_counter()
            out.append(self.o.green(top + y0 - MARGIN, self.col0 + x0 - MARGIN,
                                    (y1 - y0) + 2 * MARGIN, (x1 - x0) + 2 * MARGIN))
            self.read_s += time.perf_counter() - t
        return out

    def stats_at(self, crops8: list, defocus_counts: float) -> dict:
        s = sigma_for(defocus_counts)
        rois = [render(c, s, self.rng) for c in crops8]
        t = time.perf_counter()
        st = score_rois(rois)
        self.score_s += time.perf_counter() - t
        return st

    def frame(self):
        crops = self.crops8(self.n)
        self.n += 1
        self.last_crops = crops
        return self.pos + self.rng.normal(0, 2), self.stats_at(crops, self.lens_pos() - self.e0)


def simulate(paths, starts, speed_mps, gsd_cm, seed0, trace=False):
    px_per_frame = speed_mps * FRAME_S / (gsd_cm / 100.0)
    cfg = W10Config()   # the live configuration, direction -1, 3500 c back-off
    half_depth = 0.5 * DEPTH_C
    print(f"ground speed {speed_mps} m/s, {gsd_cm} cm/px -> {px_per_frame:.0f} px per frame; lens: w0 ~3.5 px, "
          f"depth of focus +-{DEPTH_C:.0f} c (half = {half_depth:.0f} c), lash 1000 c")
    errs, parks, frames = [], [], []
    fails = 0
    for path in paths:
        o = Ortho(path)
        print(f"\n{os.path.basename(path)}: {o.rows} x {o.cols} px")
        for s in range(starts):
            rng = np.random.default_rng(seed0 * 1000 + s)
            # a run can take ~150 frames; keep the whole roll inside the picture and on valid ground
            for _ in range(50):
                col0 = int(rng.integers(0, o.cols - FRAME_W + 1))
                row0 = int(rng.integers(0, max(1, o.rows - FRAME_H - int(200 * px_per_frame))))
                if all(o.valid(row0 + y0, col0 + x0, y1 - y0, x1 - x0) for x0, y0, x1, y1 in roi_boxes()):
                    break
            e0 = float(rng.uniform(-2000, 2000))                       # true focus, motor counts
            start = e0 + float(rng.uniform(-1500, 1500))               # operator hand-focused near it
            lens = OrthoLens(o, e0, start, col0, row0, px_per_frame, seed=seed0 * 1000 + s)
            t0 = time.perf_counter()
            af = run_engine(lens, cfg, trace=trace)
            rep = af.report()
            if rep["state"] != "done":
                fails += 1
                print(f"  start #{s + 1} at {start - e0:+.0f} c from focus: {rep['state']} after {rep['frames']} frames"
                      f" -- {af.log[-1]}")
                continue
            err = lens.lens_pos() - e0                                  # residual defocus of the LENS
            w_park = lens.stats_at(lens.last_crops, err)["w10"]
            w_true = lens.stats_at(lens.last_crops, 0.0)["w10"]
            rise = 2.4 * (sigma_for(err) - SIGMA0_PX)                   # lens-model w10 cost of the residual
            flag = "" if abs(err) <= half_depth else "   <-- outside half a depth"
            fails += abs(err) > half_depth
            errs.append(err); parks.append(rep["vertex"] - e0); frames.append(rep["frames"])
            print(f"  start #{s + 1} at {start - e0:+.0f} c: lens parked {err:+.0f} c from focus (motor vertex "
                  f"{rep['vertex'] - e0:+.0f}, CI +-{rep['ci_half']:.0f}); w10 at park {w_park:.2f} px vs {w_true:.2f} "
                  f"in focus (model +{max(rise, 0):.2f}); {rep['frames']} frames = {rep['seconds']:.0f} s, "
                  f"{rep['samples']} gated, {af.backoffs} back-off(s); wall {time.perf_counter() - t0:.0f} s "
                  f"(read {lens.read_s:.0f}, score {lens.score_s:.0f}){flag}")
    if errs:
        e = np.array(errs)
        print(f"\n{len(e)} parks: lens error rms {np.sqrt(np.mean(e ** 2)):.0f} c, max {np.abs(e).max():.0f} c, "
              f"median {np.median(e):+.0f} c; frames median {np.median(frames):.0f} ({np.median(frames) * FRAME_S:.0f} s)")
    print(f"{fails} failure(s) (not done, or lens parked outside half a depth of focus)")
    return 1 if fails else 0


def self_check():
    """The renderer + scorer reproduce the lens model: a sharp step pattern blurred for defocus d scores
    w10 ~= hypot(1.75, 2.4 sigma(d)), monotone in |d| and symmetric."""
    rng = np.random.default_rng(0)
    n = ROI + 4 + 2 * MARGIN
    g8 = np.full((n, n), 80, np.uint8)
    for k in range(60, n - 60, 90):                 # a lattice of bright blocks: edges at all four orientations
        g8[k:k + 40, :] = 200
        g8[:, k:k + 40] = np.where(g8[:, k:k + 40] == 200, 80, 200)
    got = {}
    for d in (0.0, 380.0, -380.0, 1500.0):
        st = score_rois([render(g8, sigma_for(d), rng)] * 5)
        got[d] = st["w10"]
        want = math.hypot(1.75, 2.4 * sigma_for(d))
        assert st["n_edges"] >= 300 and abs(st["w10"] - want) < 0.35, (d, st["w10"], want, st["n_edges"])
    assert got[0.0] < got[380.0] < got[1500.0] and abs(got[380.0] - got[-380.0]) < 0.1, got
    # the depth-of-focus definition (+0.3 px at +-380 c) within the floor model's slack: a binary step
    # lattice reads ~0.3 px sharper than the flight-frame floor the hypot(1.75, ...) calibration came from
    assert 0.15 < got[380.0] - got[0.0] < 0.6, got
    print("ok", {k: round(v, 2) for k, v in got.items()})
    return 0


def main(argv):
    if "--self-check" in argv:
        return self_check()
    args = list(argv)
    opt = {"--starts": 3, "--speed": 40.0, "--gsd-cm": 5.0, "--seed": 0}
    for k in list(opt):
        if k in args:
            i = args.index(k); opt[k] = type(opt[k])(args[i + 1]); del args[i:i + 2]
    trace = "--trace" in args
    paths = [a for a in args if a != "--trace"]
    if not paths:
        print(__doc__); return 2
    return simulate(paths, opt["--starts"], opt["--speed"], opt["--gsd-cm"], opt["--seed"], trace)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
