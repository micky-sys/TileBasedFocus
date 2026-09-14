"""Find doubled (ghosted) focus crops by fitting each against its own neighbour.

A doubled crop is ``a*scene + b*shift(scene, s)``. Blind autocorrelation cannot see that:
the ortho's own periodicity (field rows) peaks at r~0.37 on EVERY crop of an object,
flagged or not. So use a clean neighbour of the same object as the reference -- valid only
because the ROI odometer registers consecutive crops to within a few rows -- fit the
suspect against it, and cross-correlate the residual back against the reference. A ghost
leaves a shifted copy of the reference behind; a clean crop leaves noise.

Separation measured on the 15:04 run (lights on, 122 crops, 7 hand-flagged):
flagged median r=0.380, unflagged median r=0.107, 6 of 7 in the top 9.

    py -3 tools/_ghost_fit.py E:\\Project\\focus [name-substring-filter]
"""
import collections
import glob
import os
import re
import statistics as st
import sys

import cv2
import numpy as np

RADIUS = 380      # rows/cols of ghost offset to search; one scene step is ~600
DEADZONE = 20     # ignore the self-peak and sub-pixel registration slop


def _load(path):
    a = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if a is None:                      # still being written, or a name cv2 can't open
        return None
    if a.ndim == 3:
        a = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    a = a.astype(np.float32)
    # Band-pass: the 1.8 kills the LCD pixel grid (2.5 px period, ~20% of the focus
    # metric's energy), the 12 kills illumination roll-off. Both would otherwise
    # dominate the correlation.
    return cv2.GaussianBlur(a, (0, 0), 1.8) - cv2.GaussianBlur(a, (0, 0), 12)


def ghost(suspect, ref):
    """Return ``(dy, dx, strength, reg_dy, reg_dx)`` for the best ghost of ref in suspect."""
    h = min(suspect.shape[0], ref.shape[0])     # a crop clamped at a frame edge is smaller
    w = min(suspect.shape[1], ref.shape[1])
    s, r = suspect[:h, :w].copy(), ref[:h, :w].copy()
    s -= s.mean()
    r -= r.mean()
    # Register first, or residual tracking error reads as a short-offset ghost.
    off = cv2.phaseCorrelate(r.astype(np.float64), s.astype(np.float64))[0]
    reg_dx, reg_dy = int(round(off[0])), int(round(off[1]))
    r = cv2.warpAffine(r, np.float32([[1, 0, reg_dx], [0, 1, reg_dy]]), (w, h),
                       flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)

    amp = float((s * r).sum() / ((r * r).sum() + 1e-9))
    resid = s - amp * r
    win = np.outer(np.hanning(h), np.hanning(w)).astype(np.float32)
    f1, f2 = np.fft.rfft2(resid * win), np.fft.rfft2(r * win)
    cc = np.fft.fftshift(np.fft.irfft2(f1 * np.conj(f2), s=(h, w)))
    cc /= (np.linalg.norm(resid * win) * np.linalg.norm(r * win) + 1e-9)

    cy, cx = h // 2, w // 2
    m = cc[max(0, cy - RADIUS):cy + RADIUS, max(0, cx - RADIUS):cx + RADIUS].copy()
    my, mx = m.shape[0] // 2, m.shape[1] // 2
    yy, xx = np.mgrid[:m.shape[0], :m.shape[1]]
    m[np.hypot(yy - my, xx - mx) < DEADZONE] = -9
    k = np.unravel_index(np.argmax(m), m.shape)
    return int(k[0] - my), int(k[1] - mx), float(m[k]), abs(reg_dy), abs(reg_dx)


def scan(folder, keep=""):
    files = [f for f in glob.glob(os.path.join(folder, "OID*.tif")) if keep in os.path.basename(f)]
    by_oid = collections.defaultdict(list)
    for f in files:
        b = os.path.basename(f)
        stamp = re.search(r"_(\d{6})_(\d{3})\.tif$", b)
        if stamp:
            by_oid[int(b[3:6])].append((stamp.group(1) + stamp.group(2), b[:b.index("_Cam")], f))

    out = []
    for group in by_oid.values():
        group.sort()
        for i, (_t, name, f) in enumerate(group):
            nb = group[i + 1] if i + 1 < len(group) else (group[i - 1] if i else None)
            if nb is None:
                continue
            a, b = _load(f), _load(nb[2])
            if a is not None and b is not None:
                out.append((name,) + ghost(a, b))
    return out


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else r"E:\Project\focus"
    keep = sys.argv[2] if len(sys.argv) > 2 else ""
    rows = scan(folder, keep)
    if not rows:
        print("no crops matched in " + folder)
        return
    strengths = sorted(r[3] for r in rows)
    print("%d crops in %s\n" % (len(rows), folder))
    print("  %-20s %-20s %s" % ("crop", "ghost offset", "strength   registration"))
    for name, dy, dx, r, ry, rx in sorted(rows, key=lambda x: -x[3])[:20]:
        print("  %-20s dy=%+4d dx=%+4d     r=%.3f      dy%d dx%d" % (name, dy, dx, r, ry, rx))
    print("\n  all crops: median %.3f  90th %.3f  max %.3f"
          % (st.median(strengths), strengths[int(.9 * len(strengths))], strengths[-1]))
    big = [r for r in rows if abs(r[1]) > 120 or abs(r[2]) > 120]
    print("  large-offset ghosts (|offset| > 120 rows, the family you flagged): %d of %d (%.0f%%)"
          % (len(big), len(rows), 100.0 * len(big) / len(rows)))


if __name__ == "__main__":
    main()
