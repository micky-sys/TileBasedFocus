"""Measure what an AF frame actually costs, to explain the 4.7 -> 3.4 fps drop.

Pressing AF adds, per frame: full-res crops out of the held pool slot, and a
Laplacian-variance score on each. The drop is ~28% of a 213 ms frame budget, i.e.
~60 ms, and the point of this bench is to find out which part of it that is before
optimising the wrong one.

Sizes come from a live run: coarse AF ROI 670x476 at SUBSAMPLE_FACTOR 4.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "host"))

from capture.autofocus import focus_measure_full_res  # noqa: E402

SUBSAMPLE = 4
ROI_W, ROI_H = 670, 476           # coarse AF ROI, live
FULL_W, FULL_H = 13400, 9528      # sensor
CROP_W, CROP_H = ROI_W * SUBSAMPLE, ROI_H * SUBSAMPLE


def timeit(fn, n=20):
    fn()  # warm
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - t0) / n * 1e3  # ms


def main() -> int:
    rng = np.random.default_rng(0)
    # A full sensor frame of uint16, like the pool slot the crops are taken from.
    frame = rng.integers(0, 4096, size=(FULL_H, FULL_W), dtype=np.uint16)
    y0, x0 = 4000, 6000
    view = frame[y0:y0 + CROP_H, x0:x0 + CROP_W]

    print(f"crop {CROP_W}x{CROP_H} uint16 = {view.nbytes / 1e6:.1f} MB "
          f"from a {FULL_W}x{FULL_H} frame\n")

    # Step breakdown: the score is already OpenCV, so if it costs 57 ms something
    # other than the filters is paying for it. The prime suspect is pulling the green
    # sub-grid out of a strided uint16 view -- that gather touches all 10.2 MB to
    # produce 2.5 MB, and numpy does not vectorise it the way cv2 does its filters.
    import cv2
    crop = view.copy()
    green_u16 = crop[0::2, 1::2]
    green_f = (green_u16 >> 6).astype(np.float32)
    print("step breakdown (green plane is "
          f"{green_u16.shape[1]}x{green_u16.shape[0]} = "
          f"{green_u16.size / 1e6:.2f} Mpx):")
    for name, fn in (
        ("green = crop[0::2, 1::2] >> 6 -> f32", lambda: (crop[0::2, 1::2] >> 6).astype(np.float32)),
        ("GaussianBlur sigma=1", lambda: cv2.GaussianBlur(green_f, (0, 0), 1.0)),
        ("Laplacian ksize=3", lambda: cv2.Laplacian(green_f, cv2.CV_32F, ksize=3)),
        ("lap.var()", lambda: float(green_f.var())),
        ("green.mean()", lambda: float(green_f.mean())),
    ):
        print(f"  {name:<40} {timeit(fn, n=10):7.2f} ms")
    print(f"  cv2 threads={cv2.getNumThreads()}\n")

    # WHERE to take the green sub-grid. The pixels are identical in all three; only the
    # slice that cuts them out of the 255 MB slot differs, and that slice runs on the
    # frame-delivery thread. Two effects pull against each other:
    #   - fewer ROWS is a real saving: rows are 26800 B apart, so a skipped row is
    #     cache lines never touched.
    #   - fewer COLUMNS is not: neighbouring uint16 share a cache line, so a column
    #     stride reads the same bytes and only throws away half of them -- while
    #     costing the vectorised per-row memcpy that a contiguous run gets.
    # Shipping the column stride into the pipeline (2026-09-10) measured SLOWER on the
    # rig than the full cut it replaced, which is what this section exists to explain.
    cut_full = lambda: np.array(view, order="C")                       # noqa: E731
    cut_green = lambda: np.array(                                      # noqa: E731
        frame[y0:y0 + CROP_H:2, x0 + 1:x0 + CROP_W:2], order="C")
    cut_rows = lambda: np.array(                                       # noqa: E731
        frame[y0:y0 + CROP_H:2, x0:x0 + CROP_W], order="C")
    c_full, c_green, c_rows = cut_full(), cut_green(), cut_rows()
    assert np.array_equal(c_green, c_full[0::2, 1::2]), "green cut moved the pixels"
    assert np.array_equal(c_rows[:, 1::2], c_full[0::2, 1::2]), "row cut moved the pixels"

    # Only the CUT is timed. Whichever slice is used, the filters afterwards run on the
    # same 1340x952 green plane, so the score is a constant and the cut is the entire
    # difference between the three -- which is the point: it is the smaller half.
    print("-- where to take the green sub-grid (same pixels, different slice) --")
    for name, cut, c in (
        ("full Bayer cut, metric decimates", cut_full, c_full),
        ("green cut (rows AND cols)", cut_green, c_green),
        ("rows-only cut", cut_rows, c_rows),
    ):
        print(f"  {name:<38} cut {timeit(cut, n=10):6.2f} ms   "
              f"({c.nbytes / 1e6:5.1f} MB shipped)")
    print(f"  {'...then the same score on all three':<38} "
          f"score {timeit(lambda: focus_measure_full_res(c_full), n=10):6.2f} ms\n")

    # Can the N patches of one frame be scored CONCURRENTLY? They are independent, and
    # every step releases the GIL (cv2 filters, numpy astype/reductions), so a thread
    # pool is allowed to overlap them. Whether it pays is a different question: cv2 is
    # already running its filters on 16 threads, so per-patch threading may only add
    # contention. Measured rather than assumed -- guessing about this cost model is
    # exactly what produced two no-op "optimisations" on 2026-09-10.
    from concurrent.futures import ThreadPoolExecutor
    n_patch = 5                                   # AF3 station_objects
    patches = [view.copy() for _ in range(n_patch)]

    def serial():
        return [focus_measure_full_res(p) for p in patches]

    pool = ThreadPoolExecutor(max_workers=n_patch)

    def parallel():
        return list(pool.map(focus_measure_full_res, patches))

    print(f"-- scoring {n_patch} patches (one AF frame) --")
    t_ser = timeit(serial, n=5)
    t_par = timeit(parallel, n=5)
    print(f"  {'serial, cv2 threads=16 (today)':<38} {t_ser:7.2f} ms")
    print(f"  {'pool of 5, cv2 threads=16':<38} {t_par:7.2f} ms")
    cv2.setNumThreads(1)                          # give each patch one core, not all 16
    t_ser1 = timeit(serial, n=5)
    t_par1 = timeit(parallel, n=5)
    print(f"  {'serial, cv2 threads=1':<38} {t_ser1:7.2f} ms")
    print(f"  {'pool of 5, cv2 threads=1':<38} {t_par1:7.2f} ms")
    cv2.setNumThreads(0)                          # restore the default
    best = min(t_ser, t_par, t_ser1, t_par1)
    print(f"  -> best is {best:.0f} ms vs {t_ser:.0f} ms today "
          f"({t_ser - best:.0f} ms off a ~208 ms frame period)\n")
    pool.shutdown()

    double = timeit(lambda: np.ascontiguousarray(view).copy())
    single = timeit(lambda: view.copy())
    score = timeit(lambda: focus_measure_full_res(view.copy()), n=10)
    score_only = timeit(lambda: focus_measure_full_res(np.ascontiguousarray(view)), n=10)

    print(f"{'crop, ascontiguousarray().copy()  (today)':<44} {double:7.2f} ms")
    print(f"{'crop, .copy()                     (one copy)':<44} {single:7.2f} ms")
    print(f"{'focus_measure_full_res (score only)':<44} {score_only:7.2f} ms")
    print(f"{'crop + score, as shipped':<44} {score:7.2f} ms")

    print("\n-- per AF frame --")
    # Live: the primary is cropped+scored TWICE -- once via _focus_roi for the climb
    # curve, and again as crops[0] of the cross-cal pair, which pins the primary and
    # the on-deck. Three crops, three scores, for two distinct patches.
    today = 3 * score
    deduped = 2 * score
    one_copy = 2 * (single + score_only)
    budget = 1000.0 / 4.7
    print(f"  frame budget at 4.7 fps                    {budget:7.2f} ms")
    print(f"  today: 3 crops + 3 scores                  {today:7.2f} ms  "
          f"({today / budget:5.1%} of budget)")
    print(f"  drop the duplicate primary: 2 + 2          {deduped:7.2f} ms  "
          f"({deduped / budget:5.1%})")
    print(f"  ...and one copy instead of two             {one_copy:7.2f} ms  "
          f"({one_copy / budget:5.1%})")
    print(f"\n  predicted fps today                        "
          f"{1000.0 / (budget + today):7.2f}  (measured 3.4)")
    print(f"  predicted fps deduped + single copy        "
          f"{1000.0 / (budget + one_copy):7.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
