"""Scratch: compare tile-selection metrics on a real dumped picker frame."""

import os

import sys



import cv2

import numpy as np



ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, ROOT)



from tile_geometry import TileGrid  # noqa: E402



IMG = os.path.join(ROOT, "af_select_frame.png")

img = cv2.imread(IMG, cv2.IMREAD_GRAYSCALE)

assert img is not None, f"no {IMG}"

fh, fw = img.shape

print(f"frame {fw}x{fh}")



grid = TileGrid(frame_width=fw, frame_height=fh, display_width=fw,

                display_height=fh, zoom=5.0, region="interior",

                interior_inset=0.12)

print(f"{len(grid)} tiles")



SS = 2





def highpass7(r):

    return float(np.std(r - cv2.GaussianBlur(r, (0, 0), 7)))





def dog(r, lo, hi):

    return float(np.std(cv2.GaussianBlur(r, (0, 0), lo)

                        - cv2.GaussianBlur(r, (0, 0), hi)))





rows = []

for i, t in enumerate(grid):

    reg = img[t.y0:t.y1:SS, t.x0:t.x1:SS].astype(np.float32)

    if reg.size == 0:

        continue

    cx, cy = (t.x0 + t.x1) // 2, (t.y0 + t.y1) // 2

    rows.append((i, cx, cy, highpass7(reg), dog(reg, 2, 16),

                 dog(reg, 2, 24), dog(reg, 3, 32)))



print(f"\n{'tile':>4} {'cx':>5} {'cy':>5} {'hp7':>7} {'dog2-16':>8} "

      f"{'dog2-24':>8} {'dog3-32':>8}")

for r in rows:

    print(f"{r[0]:>4} {r[1]:>5} {r[2]:>5} {r[3]:>7.2f} {r[4]:>8.2f} "

          f"{r[5]:>8.2f} {r[6]:>8.2f}")





def winner(rows, col):

    return max(rows, key=lambda r: r[col])





for name, col in [("hp7", 3), ("dog2-16", 4), ("dog2-24", 5), ("dog3-32", 6)]:

    w = winner(rows, col)

    print(f"WINNER {name:>8}: tile {w[0]} @({w[1]},{w[2]}) score={w[col]:.2f}")



vis = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

colors = {3: (0, 0, 255), 4: (0, 255, 0), 5: (255, 0, 0), 6: (0, 255, 255)}

for col, color in colors.items():

    w = winner(rows, col)

    t = grid[w[0]]

    cv2.rectangle(vis, (t.x0, t.y0), (t.x1, t.y1), color, 3)

out = os.path.join(ROOT, "af_tile_probe.png")

cv2.imwrite(out, vis)

print(f"\nwrote {out} (red=hp7 green=dog2-16 blue=dog2-24 yellow=dog3-32)")

