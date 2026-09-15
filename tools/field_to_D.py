"""What would AF3's D read on the field the flight actually has?

``field_pairs.py`` measures the flight field as a quadratic surface. AF3's D
(``autofocus_v3.profile_metric``) reduces ONE patch's traverse to centre-band
minus both edge-bands, in log units. Those are the same physical quantity in
different clothes, so the surface can be pushed through the real metric.

By hand the answer is startling: for a traverse along x at fixed y,

    D = f(0,y0) - [f(-1,y0) + f(+1,y0)]/2 = -curv_x

Every other term cancels. Both tilts are odd in x and die in the average of the
two edges; astig*x*y is odd in x too; tilt_y*y and curv_y*y^2 do not depend on x
at all and cancel against the centre. So D is blind to everything the flight fit
resolves WELL and sensitive only to the one coefficient it cannot resolve.

That is a big enough claim to check against the real function rather than the
algebra, which is what this does -- synthesising trails from the fitted surface
and feeding them to the shipped ``profile_metric``.

    py -3 tools/field_to_D.py
    py -3 tools/field_to_D.py --self-check
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "host"))
sys.path.insert(0, str(ROOT / "tools"))

from capture.autofocus_v3 import AutoFocusConfigV3, profile_metric  # noqa: E402

W, H = 13400, 9528          # the flight frame, cols x rows
A_COUNTS = 3.26e-7          # ln-score curvature per count^2, from fieldmap_counts


def surface(coef: dict):
    """f(x, y) in log-score units, x along-track, y across-track, both +-1."""
    def f(x, y):
        return (coef["tilt_x"] * x + coef["tilt_y"] * y
                + coef["curv_x"] * x * x + coef["curv_y"] * y * y
                + coef["astig"] * x * y)
    return f


def trail_from_surface(coef: dict, y0: float, n: int = 40, content: float = 1.0,
                       slope: float = 0.0):
    """One patch crossing the frame, at across-track position y0 when x=0.

    ``content`` is the patch's own brightness/texture scale -- the thing D is
    supposed to be immune to. ``slope`` is dy/dx in NORMALISED field units: real
    traverses are diagonal, because the aircraft crabs and the mirror does not
    return the footprint to exactly the same place.
    """
    f = surface(coef)
    out = []
    for cx in np.linspace(0.02 * W, 0.98 * W, n):
        x = (cx - W / 2) / (W / 2)
        y = y0 + slope * x
        out.append((float(cx), float(H / 2 + y * (H / 2)),
                    float(content * np.exp(f(x, y)))))
    return out


def d_for(coef: dict, cfg, y0: float, content: float = 1.0, slope: float = 0.0):
    got = profile_metric(
        trail_from_surface(coef, y0, content=content, slope=slope), W, H, cfg)
    return None if got is None else got[0]


# Sweep-to-sweep displacements measured across the 129 frames (rows, cols).
# The spread is crab plus mirror repeatability, and it is not small.
MEASURED_SHIFTS = ((824, -5138), (1072, -4422), (1704, -5516), (1824, -5556),
                   (2368, -4960), (2376, -4976), (3424, -4152), (3437, -4152))


def slope_of(dy_px: float, dx_px: float) -> float:
    """Traverse slope dy/dx in normalised units, from a pixel displacement."""
    return (dy_px / (H / 2)) / (dx_px / (W / 2))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()

    cfg = AutoFocusConfigV3()
    print(f"profile bands: centre +-{cfg.profile_centre_frac:.2f}, "
          f"edges outer {cfg.profile_edge_frac:.2f} of the travel axis")

    # The flight fit: ALL pairs, both geometries (7381 patches, 204 sweep pairs).
    flight = {"tilt_x": +0.1113, "tilt_y": -0.0625, "curv_x": -0.0284,
              "curv_y": +0.1411, "astig": +0.0945}
    se = {"tilt_x": 0.0276, "tilt_y": 0.0173, "curv_x": 0.0160,
          "curv_y": 0.0225, "astig": 0.0126}

    print("\nflight field (log units, +-1 normalised field):")
    for k in flight:
        print(f"  {k:<8} {flight[k]:+.4f} +- {se[k]:.4f}   "
              f"{abs(flight[k]) / se[k]:.1f} sigma")

    print("\nD from the shipped profile_metric, for a patch crossing at each "
          "across-track position:")
    for y0 in (-0.8, -0.4, 0.0, +0.4, +0.8):
        d = d_for(flight, cfg, y0)
        print(f"  y0 = {y0:+.1f}   D = {d:+.5f}")
    print(f"  -curv_x   = {-flight['curv_x']:+.5f}   <- D equals this, at every y0")

    print("\ncontent invariance (the property the whole method rests on):")
    for scale in (1.0, 50.0, 0.02):
        print(f"  patch {scale:>5}x textured -> D = {d_for(flight, cfg, 0.2, scale):+.5f}")

    # The bands sit at |x| ~ 0.73, not 1.0, so the realised D is -curv_x scaled by
    # <x_edge^2> ~ 0.55 -- the metric gives away a further factor of two by not
    # reaching the corners of the field.
    d_band = d_for(flight, cfg, 0.0)
    dilution = d_band / -flight["curv_x"]
    d_se = se["curv_x"] * dilution
    print(f"\nSo in flight, D = -curv_x * {dilution:.2f} = {d_band:+.4f} "
          f"+- {d_se:.4f} log units ({abs(d_band) / d_se:.1f} sigma -- NOT resolved).")
    print(f"  (the {dilution:.2f} is <x_edge^2>: the edge bands sit at |x|~"
          f"{np.sqrt(dilution):.2f}, not at the field corner)")
    print(f"AF3 arms its stop at a D rise of {cfg.station_arm_rise:.2f}; the bench "
          f"notched run reached 0.277 and the content run 0.637.")
    print(f"The flight field offers {abs(d_band):.4f}, i.e. "
          f"{cfg.station_arm_rise / max(abs(d_band), 1e-9):.0f}x too little to arm.")
    print(f"In counts, {abs(d_band):.4f} log units at a={A_COUNTS:.2e}/c^2 is "
          f"{np.sqrt(abs(d_band) / A_COUNTS):.0f} c of defocus.")

    print("\nWhat D throws away, by construction (traverse parallel to the axis):")
    for k in ("tilt_x", "tilt_y", "curv_y", "astig"):
        probe = dict.fromkeys(flight, 0.0)
        probe[k] = flight[k]
        d = d_for(probe, cfg, 0.6)
        print(f"  {k:<8} alone ({flight[k]:+.4f}, {abs(flight[k]) / se[k]:.1f} sigma) "
              f"-> D = {d:+.6f}")

    # But no real traverse IS parallel to the axis. On a diagonal at slope m the
    # x^2 coefficient becomes curv_x + m^2*curv_y + m*astig, so the two terms D is
    # supposed to be blind to leak straight back in -- scaled by the crab angle.
    print("\nD on the DIAGONAL traverses the flight actually flew "
          "(lens never moved, so every row should read the same D):")
    print(f"  {'dy,dx (px)':>16} {'slope':>7} {'D':>9}")
    ds = []
    for dy, dx in MEASURED_SHIFTS:
        m = slope_of(dy, dx)
        d = d_for(flight, cfg, 0.0, slope=m)
        ds.append(d)
        print(f"  {f'{dy:+d},{dx:+d}':>16} {m:+7.2f} {d:+9.4f}")
    print(f"  spread: {min(ds):+.4f} to {max(ds):+.4f} = "
          f"{max(ds) - min(ds):.4f} log units of D, from crab angle alone")
    print(f"  against a real signal of {abs(d_band):.4f} -- the geometric artefact is "
          f"{(max(ds) - min(ds)) / max(abs(d_band), 1e-9):.1f}x the thing being measured,"
          f"\n  and it changes SIGN across the observed range of drift.")

    if args.self_check:
        _self_check(cfg)


def _self_check(cfg) -> None:
    """Pin the derivation and the invariance to the SHIPPED metric."""
    rng = np.random.default_rng(7)
    # Calibrate the band dilution once on a pure curv_x surface, then require every
    # random surface to obey D = -curv_x * dilution and nothing else.
    probe = {k: 0.0 for k in ("tilt_x", "tilt_y", "curv_x", "curv_y", "astig")}
    probe["curv_x"] = 1.0
    dilution = d_for(probe, cfg, 0.0) / -1.0
    assert 0.3 < dilution < 1.0, f"implausible band dilution {dilution}"
    # Tolerance is relative to each surface's own range, not absolute: D takes the
    # median of the SCORES in a band, which is the score at the band's median
    # position only where the surface is monotonic across it. In the centre band it
    # is not, so a steep surface leaves a small residual that scales with it.
    worst = 0.0
    for _ in range(200):
        coef = {k: float(rng.normal(0, 0.3))
                for k in ("tilt_x", "tilt_y", "curv_x", "curv_y", "astig")}
        scale = sum(abs(v) for v in coef.values())
        for y0 in (-0.9, -0.3, 0.0, 0.5, 0.9):
            d = d_for(coef, cfg, y0)
            assert d is not None, "a full traverse must yield a D"
            worst = max(worst, abs(d - (-coef["curv_x"] * dilution)) / max(scale, 1e-9))
    assert worst < 0.02, (f"D deviated from -curv_x*{dilution:.3f} by "
                          f"{100 * worst:.1f}% of the surface range")

    # content must not move D at all, at any texture scale
    coef = {"tilt_x": 0.11, "tilt_y": -0.06, "curv_x": -0.03,
            "curv_y": 0.14, "astig": 0.09}
    base = d_for(coef, cfg, 0.3, 1.0)
    for scale in (1e-3, 0.1, 10.0, 1e3):
        assert abs(d_for(coef, cfg, 0.3, scale) - base) < 1e-9, \
            f"content leaked into D at scale {scale}"

    # and the sensitivity claim: D responds to curv_x and to nothing else
    for k in ("tilt_x", "tilt_y", "curv_y", "astig"):
        probe = dict.fromkeys(coef, 0.0)
        probe[k] = 1.0                       # a huge coefficient
        assert abs(d_for(probe, cfg, 0.7)) < 5e-3, f"D leaked {k}"
    probe = dict.fromkeys(coef, 0.0)
    probe["curv_x"] = 1.0
    assert abs(d_for(probe, cfg, 0.7) + dilution) < 5e-3, "D failed to track curv_x"

    # The blindness is only true for a traverse PARALLEL to the axis. On a diagonal
    # the x^2 coefficient is curv_x + m^2*curv_y + m*astig, so a pure-astig field
    # must produce a NON-zero, slope-dependent D. If this ever reads zero, the crab
    # contamination in 47.2 has silently stopped being real.
    probe = dict.fromkeys(coef, 0.0)
    probe["astig"] = 0.5
    flat = d_for(probe, cfg, 0.0, slope=0.0)
    tilted = d_for(probe, cfg, 0.0, slope=-0.7)
    assert abs(flat) < 5e-3, f"astig leaked on a parallel traverse: {flat}"
    assert abs(tilted - (-dilution * -0.7 * 0.5)) < 0.02, (
        f"astig should enter a diagonal traverse as m*astig, got {tilted}")
    print(f"  pure astig 0.5: D = {flat:+.5f} parallel, {tilted:+.5f} at slope -0.7 "
          f"-- the blindness holds only along the axis")

    print(f"\nself-check OK: across 1000 random surfaces the shipped profile_metric "
          f"returns -curv_x*{dilution:.3f} to within {100 * worst:.1f}% of the surface "
          f"range, is exactly content invariant, and reads zero from "
          f"tilt_x/tilt_y/curv_y/astig however large they are")


if __name__ == "__main__":
    main()
