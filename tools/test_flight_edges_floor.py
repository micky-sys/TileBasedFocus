"""Self-check for tools/flight_edges.py (the w10 edge-width PSF metric, HANDOFF §52).

Synthetic scenes of random straight step edges, pixel-integrated (4x supersampling), optional Gaussian optical PSF,
Bayer-mosaic -> green demosaic exactly as the metric does, flight-like noise, then edge_widths().
Asserts the pipeline floor (perfect optics -> w10 ~1.75 px) and the calibration w10 ~= sqrt(1.75^2 + (2.56*sigma)^2).
Run: py -3 tools/test_flight_edges_floor.py   (~40 s)"""
import os, sys, numpy as np, cv2
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flight_edges import edge_widths

rng = np.random.default_rng(1); N = 512; SS = 4


def scene(sig_px):
    yy, xx = np.mgrid[:N * SS, :N * SS].astype(np.float32) / SS
    img = np.full_like(xx, 6000.0)
    for _ in range(25):   # 25 random half-plane steps at random angles -> many isolated straight edges
        th = rng.uniform(0, np.pi); c = rng.uniform(0, N)
        d = (xx - c) * np.cos(th) + (yy - c * rng.uniform(0.3, 1.7)) * np.sin(th)
        img += np.where(d > 0, rng.uniform(2000, 8000), 0.0) * rng.choice([-1, 1])
    img = np.clip(img, 1500, 30000)
    if sig_px > 0: img = cv2.GaussianBlur(img, (0, 0), sig_px * SS)
    px = img.reshape(N, SS, N, SS).mean(axis=(1, 3))                       # pixel integration
    px = px + rng.normal(0, 1, px.shape) * np.sqrt(px * 4 + 100 ** 2)     # shot (~4 ADU/e-) + read noise, flight-like
    raw = np.clip(px, 0, 65535).astype(np.uint16)                          # grey scene: every Bayer site carries the same signal
    return cv2.cvtColor(raw, cv2.COLOR_BayerBG2RGB)[..., 1].astype(np.float32)


FLOOR, K = 1.75, 2.4   # w10 ~= hypot(FLOOR, K*sigma); K < 2.563 (exact Gaussian 10-90 rise) because the 10th percentile picks the noise-favoured edges
print("optical PSF sigma (px) | PSF FWHM (px) | w10 | w20 | w50 | n_edges | predicted w10")
for sig in (0.0, 1.0, 2.0):
    W = np.concatenate([edge_widths(scene(sig))[0] for _ in range(2)])
    w10 = np.percentile(W, 10); pred = np.hypot(FLOOR, K * sig)
    print(f"{sig:4.1f} | {2.355 * sig:4.1f} | {w10:4.2f} | {np.percentile(W, 20):4.2f} | {np.median(W):4.2f} | {len(W)} | {pred:4.2f}", flush=True)
    assert len(W) > 500, "edge detector found too few edges on a synthetic scene"
    assert abs(w10 - pred) < 0.35, f"w10 {w10:.2f} vs predicted {pred:.2f} at sigma {sig}"
print("ok")
