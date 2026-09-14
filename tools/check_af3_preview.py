"""Render check for AF3's live field-profile overlay.

The operator's two display rules are easy to break silently in a paintEvent:

1. every completed traverse is normalised to ITS OWN peak, so two traverses of the
   same SHAPE but wildly different content scale must draw the same picture -- that
   is what makes "do they line up?" a fair question rather than a brightness contest,
2. normalising must not flatten a real difference in shape, or the plot would say
   "they line up" no matter what.

Tested by pixel-identity of the rendered widget rather than by hunting for curve
colours: a first version of this check searched for greenish pixels and kept finding
the cyan station label and the grey "waiting for traverses..." placeholder instead.

(That only the CURRENT station is shown is an engine property -- ``_open_station``
clearing ``_done_trails`` -- and is covered in ``tools/test_autofocus_v3.py``.)

Run: ``py -3 tools/check_af3_preview.py`` (writes af3_profile_preview.png)
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "host"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

FW, FH = 3350, 2382


def traverse(amp: float, peak_u: float, scale: float) -> list:
    """A vertical traverse (travel in y): dome of depth ``amp`` peaking at ``peak_u``,
    multiplied by ``scale`` to stand in for how bright/textured the patch is."""
    return [(FW * 0.42, FH * (i + 0.5) / 19,
             scale * (1.0 + amp * math.exp(-(((i + 0.5) / 19 - peak_u) / 0.28) ** 2)))
            for i in range(19)]


def main() -> int:
    app = QApplication([])  # noqa: F841 -- must outlive the widget
    from capture.focus_panel import _FieldProfileGraph

    g = _FieldProfileGraph()

    def render(completed):
        # Everything except `completed` held fixed, so any pixel difference is the
        # curves and not a label that happened to change with it.
        g.update_row({"frame_w": FW, "frame_h": FH, "need": 4, "pos": -1234,
                      "station": 7, "banked": [0.31, 0.28],
                      "stations": [(-800, 0.11), (-1234, 0.31)],
                      "completed": completed})
        pm = QPixmap(g.size())
        g.render(pm)
        return pm, pm.toImage()

    faint = traverse(1.4, 0.5, 1.0)         # dim patch
    bright = traverse(1.4, 0.5, 300.0)      # same shape, 300x the content scale
    shifted = traverse(1.4, 0.30, 1.0)      # peak a third of the way across

    pm_a, a = render([(11, faint)])
    _, b = render([(11, bright)])
    assert a == b, "peak-normalised curves must render identically at any content scale"
    print("  x1 and x300 content -> pixel-identical curves")

    _, c = render([(11, shifted)])
    assert c != a, "normalisation must not erase a real difference in profile shape"
    print("  a shape whose peak moved -> visibly different curve")

    # Both together: the picture the operator will actually read, plus a third at a
    # middling scale so the colour cycling is exercised.
    pm_all, allimg = render([(11, faint), (12, bright), (13, traverse(1.1, 0.5, 40.0))])
    assert allimg != a, "three traverses must not draw as one"
    pm_all.save(str(REPO / "af3_profile_preview.png"))

    _, empty = render([])
    assert empty != a, "a station with no completed traverse must not draw curves"
    print("  a station with nothing completed -> placeholder, no curves")

    print("\nAF3 preview check OK -> af3_profile_preview.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
