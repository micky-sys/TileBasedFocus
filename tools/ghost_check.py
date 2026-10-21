"""Is a focus crop defocused, or was it imaged twice?

Both look "soft", and the focus score cannot tell them apart, so a vibrating
airframe is easily mistaken for a lens that will not hold focus.

The test assumes no model. Take a sharp frame of the SAME ground, solve

    suspect = sharp (*) k

for the point spread function k by regularised division in the Fourier
domain, and look at k:

    one compact blob   ordinary defocus
    two blobs          the scene was imaged twice during one exposure
    a streak           steady motion smear

A sinusoidal vibration dwells longest at the two turning points of its
travel, so it lands in the middle case and the lobe separation is the
peak-to-peak amplitude of the shake, in pixels.

    py -3 tools/ghost_check.py --self-check
    py -3 tools/ghost_check.py --figure docs/images/51_double_image.png
"""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
import cv2
import numpy as np

DEFAULT_DIR = Path(
    r"G:\My Drive\Micky\WORK\Cursor\TileBasedFocus"
    r"\2026_07_29 data collection flight\Focus images\cam8\focus")
# Sharpest frame over this ground. NOTE the timestamp: S##_F## repeats every
# pass, so three different files are called S36_F5 and they are different
# scenes. Always identify a frame by its timestamp.
DEFAULT_REF = "Cam2439008_20260930_Project_focus_151048_080_S36_F5.tif"
DEFAULT_GLOB = "*15104[3-9]*"

S = 256                 # analysis window, green px
PS = 33                 # recovered PSF size, green px
CENTRE = (536, 178)     # centre of a well-textured patch (the greenhouse)
SNR_FRAC = 0.004        # Wiener regularisation, as a fraction of mean power
LOBE_FLOOR = 0.30       # a second lobe must reach this much of the first
MAX_SHIFT = (120, 60)   # beyond this the reference ground has left the frame
MIN_OVERLAP = 0.35      # correlation with the reference window; below it the
                        # recovered PSF is noise and its "lobes" are meaningless


def green(path: Path) -> np.ndarray:
    """HANDOFF 51: focus-view crops are raw Bayer, so take the green sub-grid."""
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise SystemExit(f"cannot read {path}")
    return img[0::2, 1::2].astype(np.float32)


def timestamp(name: str) -> float:
    m = re.search(r"_(\d{2})(\d{2})(\d{2})_(\d{3})_S", name)
    if not m:
        return float("nan")
    h, mi, s, ms = (int(g) for g in m.groups())
    return h * 3600 + mi * 60 + s + ms / 1000.0


class Recover:
    """Recovers the PSF that maps one fixed sharp frame to any other frame."""

    def __init__(self, ref: np.ndarray):
        self.ref = ref
        self.h, self.w = ref.shape
        self.win = cv2.createHanningWindow((self.w, self.h), cv2.CV_32F)
        self.hann = cv2.createHanningWindow((S, S), cv2.CV_32F)
        self.R = np.fft.fft2(self._tile(ref))
        self.snr = SNR_FRAC * (np.abs(self.R) ** 2).mean()

    def _tile(self, img: np.ndarray) -> np.ndarray:
        x, y = CENTRE
        t = img[y - S // 2:y + S // 2, x - S // 2:x + S // 2]
        return (t - t.mean()) * self.hann

    def psf(self, img: np.ndarray) -> np.ndarray:
        G = np.fft.fft2(self._tile(img))
        K = np.fft.fftshift(np.real(np.fft.ifft2(
            G * np.conj(self.R) / (np.abs(self.R) ** 2 + self.snr))))
        c = S // 2
        return K[c - PS // 2:c + PS // 2 + 1, c - PS // 2:c + PS // 2 + 1]

    def register(self, img: np.ndarray):
        """-> (aligned image, overlap) or (None, overlap).

        Deconvolution against ground that is not the same ground returns
        noise that happens to have peaks in it, so the overlap is checked
        on the analysis window itself, not just on the shift."""
        (sx, sy), _ = cv2.phaseCorrelate(self.ref, img, self.win)
        if abs(sx) > MAX_SHIFT[0] or abs(sy) > MAX_SHIFT[1]:
            return None, 0.0
        m = np.float32([[1, 0, -sx], [0, 1, -sy]])
        reg = cv2.warpAffine(img, m, (self.w, self.h), flags=cv2.INTER_LANCZOS4)
        a, b = self._tile(self.ref).ravel(), self._tile(reg).ravel()
        overlap = float(np.dot(a, b) / max(np.linalg.norm(a) * np.linalg.norm(b),
                                           1e-9))
        return (reg if overlap >= MIN_OVERLAP else None), overlap


def lobes(k: np.ndarray) -> tuple[float, float, float]:
    """-> (separation px, axis deg, brightness of 2nd lobe relative to 1st).
    Zero separation means a single blob, i.e. plain defocus."""
    sm = cv2.GaussianBlur(np.maximum(k, 0), (0, 0), 0.8)
    pk = [(sm[y, x], x, y) for y in range(2, PS - 2) for x in range(2, PS - 2)
          if sm[y, x] == sm[y - 2:y + 3, x - 2:x + 3].max()
          and sm[y, x] > LOBE_FLOOR * sm.max()]
    pk.sort(reverse=True)
    if len(pk) < 2:
        return 0.0, float("nan"), 0.0
    (a1, x1, y1), (a2, x2, y2) = pk[0], pk[1]
    return (float(np.hypot(x2 - x1, y2 - y1)),
            float(np.degrees(np.arctan2(y2 - y1, x2 - x1)) % 180),
            float(a2 / a1))


def run(d: Path, ref_name: str, pattern: str, figure: Path | None) -> None:
    rec = Recover(green(d / ref_name))
    t0 = timestamp(ref_name)
    print(f"reference {ref_name[-26:]}\n")
    print(f"{'frame':<12}{'t rel ref':>11}{'overlap':>9}{'sep grn':>9}"
          f"{'sep sens':>10}{'axis':>7}{'2nd/1st':>9}")
    rows, skipped = [], 0
    for path in sorted(d.glob(pattern)):
        reg, overlap = rec.register(green(path))
        dt = timestamp(path.name) - t0
        if reg is None:
            skipped += 1
            print(f"{path.name[-10:-4]:<12}{dt:+11.3f}{overlap:9.2f}"
                  f"{'  -- different ground, skipped':>35}")
            continue
        sep, ang, amp = lobes(rec.psf(reg))
        rows.append((dt, sep, ang, amp, path.name, reg))
        print(f"{path.name[-10:-4]:<12}{dt:+11.3f}{overlap:9.2f}{sep:9.1f}"
              f"{2 * sep:10.1f}"
              + (f"{ang:7.0f}" if sep else f"{'-':>7}") + f"{amp:9.2f}"
              + ("   <== imaged twice" if sep else ""))
    if skipped:
        print(f"\n{skipped} frames skipped: the reference ground had moved out "
              f"of the window.")

    hit = [r for r in rows if r[1] > 0]
    print(f"\n{len(hit)}/{len(rows)} frames over this ground were imaged twice.")
    if hit:
        axes = np.array([r[2] for r in hit])
        print(f"separation {min(r[1] for r in hit):.1f}-"
              f"{max(r[1] for r in hit):.1f} green px, axis "
              f"{np.median(axes):.0f} deg (spread {np.std(axes):.0f} deg).")
        print("A consistent axis with a decaying separation is a damped "
              "airframe\nvibration, not a focus problem: the lens cannot "
              "produce two copies.")
    if figure:
        _figure(rows, rec, figure)


def _figure(rows, rec: Recover, out: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    hit = [r for r in rows if r[1] > 0]
    clean = [r for r in rows if r[1] == 0]
    if not hit or not clean:
        print("need both a doubled and a clean frame for the figure")
        return
    worst = max(hit, key=lambda r: r[1])
    best = min(clean, key=lambda r: abs(r[0]))

    fig = plt.figure(figsize=(13, 7.2))
    gs = fig.add_gridspec(2, 3)
    for col, r, tag in ((0, worst, "imaged twice"), (1, best, "clean")):
        t = r[5][CENTRE[1] - 70:CENTRE[1] + 70, CENTRE[0] - 70:CENTRE[0] + 70]
        a = fig.add_subplot(gs[0, col])
        a.imshow(t, cmap="gray", vmin=np.percentile(t, 1),
                 vmax=np.percentile(t, 99))
        a.set_title(f"{r[4][-10:-4]}  ({tag})", fontsize=10)
        a.axis("off")
        k = rec.psf(r[5])
        a = fig.add_subplot(gs[1, col])
        a.imshow(cv2.resize(k, (PS * 12, PS * 12),
                            interpolation=cv2.INTER_NEAREST), cmap="inferno")
        a.set_title(f"recovered PSF: "
                    + (f"two lobes {r[1]:.1f} px apart" if r[1]
                       else "one blob"), fontsize=9)
        a.axis("off")
    a = fig.add_subplot(gs[:, 2])
    dt = [r[0] for r in rows]
    a.stem(dt, [r[1] for r in rows], basefmt=" ")
    a.set_xlabel("time relative to the sharp reference (s)")
    a.set_ylabel("separation of the two copies (green px)")
    a.set_title("the shake decays away", fontsize=10)
    a.grid(alpha=.3)
    fig.suptitle("Same ground, same lens: a double image is vibration, "
                 "not defocus", fontsize=12)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=115)
    print(f"wrote {out}")


def self_check(d: Path, ref_name: str) -> None:
    """Blur a sharp frame by known kernels and insist the method reads them back."""
    ref = green(d / ref_name)
    rec = Recover(ref)

    def two_delta(sep, deg):
        k = np.zeros((PS, PS), np.float32)
        c = PS // 2
        k[c, c] = 1
        k[int(round(c + sep * np.sin(np.radians(deg)))),
          int(round(c + sep * np.cos(np.radians(deg))))] = 1
        return k / k.sum()

    def disc(diam):
        n = int(np.ceil(diam)) | 1
        y, x = np.mgrid[0:n, 0:n] - n // 2
        k = (np.hypot(x, y) <= diam / 2).astype(np.float32)
        return k / k.sum()

    for sep, deg in ((5, 30), (7, 120), (4, 75)):
        got, ang, amp = lobes(rec.psf(cv2.filter2D(ref, -1, two_delta(sep, deg))))
        assert abs(got - sep) <= 1.0, f"sep {got:.1f} should be {sep}"
        assert min(abs(ang - deg), 180 - abs(ang - deg)) <= 10, (
            f"axis {ang:.0f} should be {deg}")
        assert amp > 0.8, f"two equal copies should come back equal, got {amp:.2f}"

    # the controls that matter: blur alone must never look like a double image
    for diam in (5, 9, 13):
        got, _, _ = lobes(rec.psf(cv2.filter2D(ref, -1, disc(diam))))
        assert got == 0.0, f"disc defocus {diam} px read as a double image"
    for sigma in (1.0, 2.5):
        got, _, _ = lobes(rec.psf(cv2.GaussianBlur(ref, (0, 0), sigma)))
        assert got == 0.0, f"gaussian blur {sigma} read as a double image"
    got, _, _ = lobes(rec.psf(ref))
    assert got == 0.0, "the reference against itself must be a single blob"

    print("self-check: 9/9 OK (3 injected double images recovered to <1 px "
          "and <10 deg;\n            5 pure blurs and the identity all read "
          "as a single blob)")


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    p.add_argument("--ref", default=DEFAULT_REF)
    p.add_argument("--glob", default=DEFAULT_GLOB)
    p.add_argument("--figure", type=Path)
    p.add_argument("--centre", nargs=2, type=int, metavar=("X", "Y"),
                   help="centre of the analysis window, green px")
    p.add_argument("--self-check", action="store_true")
    a = p.parse_args()
    if a.centre:
        global CENTRE
        CENTRE = tuple(a.centre)
    if a.self_check:
        self_check(a.dir, a.ref)
        return
    run(a.dir, a.ref, a.glob, a.figure)


if __name__ == "__main__":
    main()

