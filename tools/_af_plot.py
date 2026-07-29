"""One-off: plot the AF run's per-crop sharpness score over time (cv2, no matplotlib)."""
import sys, datetime as dt
from pathlib import Path
import cv2, numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "host"))
from capture.autofocus import focus_measure_full_res  # noqa: E402

folder = Path(r"G:\Project\focus")
t0 = dt.datetime(2026, 7, 19, 16, 54, 52)
t1 = dt.datetime(2026, 7, 19, 16, 56, 50)
files = [f for f in folder.glob("*.tif")
         if t0 <= dt.datetime.fromtimestamp(f.stat().st_mtime) <= t1]
files.sort(key=lambda f: f.stat().st_mtime)

t_start = files[0].stat().st_mtime
ts, scores = [], []
for f in files:
    b = cv2.imread(str(f), cv2.IMREAD_UNCHANGED)
    if b is None:
        continue
    ts.append(f.stat().st_mtime - t_start)
    scores.append(focus_measure_full_res(b))
ts = np.array(ts); scores = np.array(scores)
pk = int(np.argmax(scores))

# ── canvas ──
W, H = 1200, 620
ML, MR, MT, MB = 90, 40, 70, 70
pw, ph = W - ML - MR, H - MT - MB
img = np.full((H, W, 3), 255, np.uint8)
xmax = ts.max() if ts.max() > 0 else 1.0
ymax = float(scores.max()) * 1.08

def X(t): return int(ML + t / xmax * pw)
def Y(s): return int(MT + ph - s / ymax * ph)

# grid + y ticks
for k in range(6):
    yv = ymax * k / 5
    y = Y(yv)
    cv2.line(img, (ML, y), (ML + pw, y), (230, 230, 230), 1)
    cv2.putText(img, f"{yv:.0f}", (12, y + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (80, 80, 80), 1, cv2.LINE_AA)
# x ticks (every 30 s)
tt = 0
while tt <= xmax:
    x = X(tt)
    cv2.line(img, (x, MT), (x, MT + ph), (238, 238, 238), 1)
    cv2.putText(img, f"{tt:.0f}s", (x - 10, MT + ph + 26), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (80, 80, 80), 1, cv2.LINE_AA)
    tt += 30
# axes
cv2.rectangle(img, (ML, MT), (ML + pw, MT + ph), (120, 120, 120), 1)
# series
pts = np.array([[X(t), Y(s)] for t, s in zip(ts, scores)], np.int32)
cv2.polylines(img, [pts], False, (204, 102, 34), 2, cv2.LINE_AA)
# peak + end markers
cv2.circle(img, (X(ts[pk]), Y(scores[pk])), 7, (34, 34, 204), -1, cv2.LINE_AA)
cv2.circle(img, (X(ts[-1]), Y(scores[-1])), 7, (34, 34, 34), -1, cv2.LINE_AA)
# labels
cv2.putText(img, "M4 autofocus run - tile sharpness vs time (servo walked good focus into blur)",
            (ML, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (30, 30, 30), 1, cv2.LINE_AA)
cv2.putText(img, f"peak {scores[pk]:.0f} @ {ts[pk]:.0f}s", (X(ts[pk]) + 10, Y(scores[pk]) - 6),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (34, 34, 204), 1, cv2.LINE_AA)
cv2.putText(img, f"end {scores[-1]:.0f}", (X(ts[-1]) - 90, Y(scores[-1]) - 10),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (34, 34, 34), 1, cv2.LINE_AA)
cv2.putText(img, "focus_measure (higher = sharper)", (12, MT - 24),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (80, 80, 80), 1, cv2.LINE_AA)

out = ROOT / "af_score_plot2.png"
cv2.imwrite(str(out), img)
# dump first/mid/last green PNGs for eyeballing
def green_png(b):
    g = b[0::2, 1::2].astype(np.float32)
    lo, hi = np.percentile(g, (1, 99)); hi = max(hi, lo + 1)
    return np.clip((g - lo) / (hi - lo) * 255, 0, 255).astype(np.uint8)
outdir = ROOT / "af_review_out"; outdir.mkdir(exist_ok=True)
for tag, i in [("r2_first", 0), ("r2_last", len(files) - 1)]:
    b = cv2.imread(str(files[i]), cv2.IMREAD_UNCHANGED)
    cv2.imwrite(str(outdir / f"{tag}.png"), green_png(b))
print(f"saved {out} n={len(scores)} first={scores[0]:.1f} peak={scores[pk]:.1f}@{ts[pk]:.0f}s "
      f"end={scores[-1]:.1f}")
print("series(every 10th):", [f"{s:.0f}" for s in scores[::10]])
