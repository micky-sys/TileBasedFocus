"""Find the display raster in the spectrum AF sees, and notch it out.

AF3's score is ``var(Laplacian(green))`` where ``green = bayer[0::2, 1::2]`` --
so the only spectrum that matters is the one of that *decimated* plane, not of
the sensor. The screen's pixel grid is periodic, so it lands as a lattice of
narrow spikes; real terrain is broadband. This finds the spikes, writes them to
a calibration file, and checks what removing them does to the score.

Two numbers make the spike positions non-obvious, and are why they are measured
rather than derived:

* the green sub-grid is a 2x decimation with NO prefilter, so anything above
  0.25 cyc/sensor-px folds back and appears somewhere else entirely;
* the LCD's RGB stripe is 3x finer than its pixel pitch, so it is already
  aliased at the sensor before the sub-grid touches it.

Usage::

    # look: where are the spikes?
    python tools/raster_spectrum.py "G:/Project/focus" --n 12

    # calibrate: crops of a BLANK GREY panel, where every spike is raster
    python tools/raster_spectrum.py "G:/Project/focus" --calibrate

    # verify: does notching flatten the score across the run?
    python tools/raster_spectrum.py "G:/Project/focus" --verify

    python tools/raster_spectrum.py --self-check
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "host"))

# Where the app looks for the calibration by default.
CALIB_PATH = ROOT / "host" / "capture" / "raster_notch.json"


def load_green(path: Path) -> np.ndarray | None:
    """Load a 'Save focus view' TIF, which IS already the green sub-grid.

    Not raw Bayer -- ``_save_af_crops`` writes ``crop[0::2, 1::2]``. Slicing it
    again here would decimate a second time with no prefilter, which moves every
    frequency and folds the ones past Nyquist somewhere else entirely, so the
    notch would be calibrated against an image the engine never scores.
    """
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None or img.ndim != 2:
        return None
    return img.astype(np.float32)


def power_spectrum(green: np.ndarray) -> np.ndarray:
    """Mean-removed, Hann-windowed power spectrum, fftshifted.

    The window is not cosmetic: a raw FFT of a non-periodic crop leaks the DC
    step along the full fx=0 and fy=0 axes, which is exactly where the screen
    grid's own spikes live, so without it the thing being measured is buried in
    its own edge artefact.
    """
    g = green - float(green.mean())
    wy = np.hanning(g.shape[0]).astype(np.float32)
    wx = np.hanning(g.shape[1]).astype(np.float32)
    g = g * wy[:, None] * wx[None, :]
    f = np.fft.fftshift(np.fft.fft2(g))
    return (f.real**2 + f.imag**2).astype(np.float32)


def prominence_map(psd: np.ndarray, dc_guard: int = 8) -> np.ndarray:
    """Log power above the local median.

    This is the measurement that separates the raster from the picture: a grid
    spike is narrow, so it towers over its own neighbourhood, while a terrain
    ridge is broad and takes its neighbourhood up with it. Ranking by raw power
    instead just finds the brightest content.
    """
    lg = np.log1p(psd).astype(np.float32)
    # Median twice at 5 rather than once at 9: cv2 caps float32 medians at
    # ksize 5, and the wider support matters -- a spike is several bins across,
    # so a tight median rides up onto its own shoulder and hides it.
    prom = lg - cv2.medianBlur(cv2.medianBlur(lg, 5), 5)
    h, w = prom.shape
    cy, cx = h // 2, w // 2
    prom[cy - dc_guard:cy + dc_guard + 1, cx - dc_guard:cx + dc_guard + 1] = 0.0
    return prom


def detect_spikes(psd: np.ndarray, min_prominence: float = 1.2,
                  max_spikes: int = 64, min_sep: int = 4) -> list[tuple]:
    """Spike centres as ``(fx, fy, prominence)`` in cycles/px, strongest first."""
    prom = prominence_map(psd)
    h, w = prom.shape
    cy, cx = h // 2, w // 2
    out: list[tuple] = []
    for _ in range(max_spikes):
        idx = int(np.argmax(prom))
        py, px = divmod(idx, w)
        val = float(prom[py, px])
        if val < min_prominence:
            break
        out.append(((px - cx) / w, (py - cy) / h, val))
        prom[max(0, py - min_sep):py + min_sep + 1,
             max(0, px - min_sep):px + min_sep + 1] = -1e9
    return out


def axis_profiles(psd: np.ndarray, band: int = 3) -> tuple[np.ndarray, np.ndarray]:
    """Power collapsed onto fx and onto fy (a thin strip through DC each way)."""
    h, w = psd.shape
    cy, cx = h // 2, w // 2
    return (psd[cy - band:cy + band + 1, :].mean(axis=0),
            psd[:, cx - band:cx + band + 1].mean(axis=1))


def average_psd(files: list[Path], limit: int) -> tuple[np.ndarray, int]:
    acc = None
    shape = None
    used = 0
    for f in files[:limit]:
        g = load_green(f)
        if g is None:
            continue
        if shape is None:
            shape = g.shape
            acc = np.zeros(shape, dtype=np.float32)
        elif g.shape != shape:
            # Averaging demands a common shape; a mismatch means the folder is
            # mixed, and silently skipping is better than averaging a lie.
            print(f"  skip {f.name}: {g.shape} != {shape}")
            continue
        acc += power_spectrum(g)
        used += 1
    if acc is None or used == 0:
        return np.zeros((1, 1), dtype=np.float32), 0
    return acc / used, used


def _report_axis(name: str, prof: np.ndarray, dc_guard: int = 8) -> None:
    n = prof.size
    half = prof[n // 2:].copy()
    half[:dc_guard] = 0.0
    freqs = np.arange(half.size) / n
    med = float(np.median(half[dc_guard:]) + 1e-9)
    print(f"\n  {name}: top 5 by power (freq cyc/px, period px, x median)")
    for i in sorted(np.argsort(half)[::-1][:5]):
        f = float(freqs[i])
        if f > 0:
            print(f"    {f:6.4f}   {1.0 / f:7.3f}   {half[i] / med:9.1f}")


def cmd_report(psd: np.ndarray, out_png: Path) -> None:
    along_x, along_y = axis_profiles(psd)
    _report_axis("horizontal (fx)", along_x)
    _report_axis("vertical   (fy)", along_y)
    print("\n  spikes by prominence (fx, fy, period_x, period_y, prom)")
    for fx, fy, prom in detect_spikes(psd)[:16]:
        px = 1.0 / fx if abs(fx) > 1e-9 else float("inf")
        py = 1.0 / fy if abs(fy) > 1e-9 else float("inf")
        print(f"    {fx:+7.4f} {fy:+7.4f}   {px:+8.2f} {py:+8.2f}   {prom:6.2f}")
    img = np.log1p(psd)
    img = (img - img.min()) / max(img.max() - img.min(), 1e-9)
    cv2.imwrite(str(out_png), (img * 255).astype(np.uint8))
    print(f"\nlog spectrum -> {out_png}")


def cmd_calibrate(psd: np.ndarray, used: int, min_prominence: float,
                  radius: float, dest: Path, max_spikes: int = 256) -> None:
    # Suppress as wide as the notch is, or a single lattice node spends several
    # slots on bins one disk would have covered anyway.
    sep = max(3, int(round(radius * min(psd.shape))))
    spikes = detect_spikes(psd, min_prominence=min_prominence,
                           max_spikes=max_spikes, min_sep=sep)
    if not spikes:
        print(f"no spike cleared prominence {min_prominence}; nothing written")
        return
    payload = {
        "note": ("Display-raster notch for the OrthoSim bench. Frequencies are "
                 "cycles per GREEN-SUB-GRID pixel. Recalibrate whenever the "
                 "camera, lens or panel geometry moves."),
        "radius": radius,
        "min_prominence": min_prominence,
        "crops_averaged": used,
        "spikes": [[round(fx, 6), round(fy, 6)] for fx, fy, _ in spikes],
    }
    dest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\n{len(spikes)} spike(s), radius {radius} cyc/px -> {dest}")
    for fx, fy, prom in spikes[:12]:
        print(f"    {fx:+7.4f} {fy:+7.4f}   prom {prom:5.2f}")
    if len(spikes) > 12:
        print(f"    ... and {len(spikes) - 12} more")


def cmd_verify(files: list[Path], limit: int, calib: Path) -> int:
    """Score every crop with and without the notch and compare the spread.

    "Flatlines the grey target" means the score stops tracking the lens, so the
    number that matters is the run's max/min ratio: large without the notch,
    ~1 with it.
    """
    from capture.autofocus import focus_measure_green, set_raster_notch

    if not calib.exists():
        print(f"no calibration at {calib}; run --calibrate first")
        return 1
    cfg = json.loads(calib.read_text(encoding="utf-8"))
    rows = []
    for f in files[:limit]:
        g = load_green(f)
        if g is None:
            continue
        set_raster_notch(None)
        raw = focus_measure_green(g)
        set_raster_notch(cfg["spikes"], cfg.get("radius", 0.012))
        notched = focus_measure_green(g)
        rows.append((f.name, raw, notched))
    set_raster_notch(None)
    if not rows:
        print("nothing readable")
        return 1
    print(f"\n{'crop':<46} {'raw':>10} {'notched':>10} {'kept %':>8}")
    print("-" * 78)
    for name, raw, notched in rows:
        pct = 100.0 * notched / raw if raw > 0 else float("nan")
        print(f"{name[:46]:<46} {raw:>10.2f} {notched:>10.2f} {pct:>8.1f}")
    for label, i in (("raw", 1), ("notched", 2)):
        vals = np.array([r[i] for r in rows], dtype=np.float64)
        lo, hi = float(vals.min()), float(vals.max())
        print(f"\n  {label:<8} min {lo:9.2f}  max {hi:9.2f}  "
              f"max/min {hi / max(lo, 1e-9):7.2f}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", help="a *.tif crop, or a folder of them")
    ap.add_argument("--n", type=int, default=8, help="crops to use")
    ap.add_argument("--out", default=None, help="PNG of the log spectrum")
    ap.add_argument("--calibrate", action="store_true",
                    help="write the notch calibration (use BLANK GREY crops)")
    ap.add_argument("--verify", action="store_true",
                    help="score with and without the notch")
    ap.add_argument("--calib", default=str(CALIB_PATH), help="calibration file")
    ap.add_argument("--prominence", type=float, default=1.2,
                    help="how far above the local median counts as a spike")
    ap.add_argument("--radius", type=float, default=0.012,
                    help="notch half-width, cycles/px")
    args = ap.parse_args()

    p = Path(args.path)
    files = sorted(p.glob("*.tif")) if p.is_dir() else [p]
    if not files:
        print(f"no *.tif at {p}")
        return 1

    if args.verify:
        return cmd_verify(files, args.n, Path(args.calib))

    psd, used = average_psd(files, args.n)
    if used == 0:
        print("nothing readable")
        return 1
    print(f"\naveraged {used} crop(s), green plane {psd.shape[1]}x{psd.shape[0]}")
    print("frequencies are cycles per GREEN-SUBGRID pixel (Nyquist = 0.5)")

    if args.calibrate:
        cmd_calibrate(psd, used, args.prominence, args.radius, Path(args.calib))
    else:
        out = Path(args.out) if args.out else (
            (p if p.is_dir() else p.parent) / "raster_spectrum.png")
        cmd_report(psd, out)
    return 0


def _self_check() -> int:
    """Synthesise a known grid, recover it, notch it, and prove it is gone."""
    from capture.autofocus import focus_measure_green, set_raster_notch

    n = 512
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
    period = 6.4  # px in the green plane
    grid = 100.0 * np.cos(2 * np.pi * xx / period)

    # 1. the spectrum tool finds the pitch it was given, on the right axis.
    psd = power_spectrum(100.0 + grid)
    along_x, along_y = axis_profiles(psd)
    half = along_x[n // 2:].copy()
    half[:4] = 0.0
    got = float(np.argmax(half)) / n
    assert abs(1.0 / got - period) < 0.25, f"period {1.0 / got}, want {period}"
    hy = along_y[n // 2:].copy()
    hy[:4] = 0.0
    assert hy.max() < half.max() * 1e-3, "vertical stripes leaked onto fy"

    # 2. detection puts a spike at that frequency.
    spikes = detect_spikes(psd)
    assert spikes, "no spike detected on a pure sinusoid"
    assert any(abs(abs(fx) - 1.0 / period) < 0.01 and abs(fy) < 0.01
               for fx, fy, _ in spikes), f"spike not at 1/{period}: {spikes[:3]}"

    # 3. the notch removes the grid but keeps the texture underneath, measured
    #    through the engine's own scorer rather than a reimplementation.
    rng = np.random.default_rng(0)
    texture = cv2.GaussianBlur(rng.normal(0, 150, (n, n)).astype(np.float32), (0, 0), 1.5)
    contaminated_img = 2000.0 + texture + grid
    clean_img = 2000.0 + texture

    set_raster_notch(None)
    contaminated = focus_measure_green(contaminated_img)
    truth = focus_measure_green(clean_img)
    set_raster_notch([(s[0], s[1]) for s in spikes])
    recovered = focus_measure_green(contaminated_img)
    set_raster_notch(None)

    # Guard the guard: if the clean texture scored ~0 the comparison below would
    # pass on 0 == 0 and prove nothing about preserving content.
    assert truth > 0.5, f"clean texture scored {truth:.3f}; test is vacuous"
    assert contaminated > truth * 1.5, (
        f"grid should inflate the score, got {contaminated:.3f} vs {truth:.3f}")
    err = abs(recovered - truth) / truth
    assert err < 0.15, (
        f"notched {recovered:.3f} should match clean {truth:.3f}, off {err:.1%}")
    print(f"self-check OK: period {1.0 / got:.3f} px (truth {period}); "
          f"score {contaminated:.3f} contaminated -> {recovered:.3f} notched "
          f"vs {truth:.3f} clean ({err:.1%} off)")
    return 0


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        raise SystemExit(_self_check())
    raise SystemExit(main())
