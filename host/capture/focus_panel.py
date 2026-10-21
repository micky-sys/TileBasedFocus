"""Tile-based focus panel for M4 mode.

Coarse view (default): decimated uint16 frames from the acquisition tap are
shown as a live Bayer grayscale preview (no tile analysis, no inset).

Fine view (toggle): full-resolution frames from the active camera's SHM pool;
best tile in the centre 1/5-area circle, scaled to the preview area (~150%
zoom tile geometry). Optional OpenCV debayer (checkbox); when off, the tile is
raw Bayer as duplicated grayscale channels. Optional CLAHE on the fine-tile
display (LAB luminance; display-only).
Small bottom-left overview inset (circle + tiles + winner) is always from the pipeline.

Arrow keys still drive focus steps and trigger period via serial (no on-screen
+/− or </> buttons — those group boxes are hidden; step count spinbox remains
for hotkey strength).

Camera selector, Strength radios, Save focus view, and tile info share one
compact bottom row for minimal M4 chrome.
"""

from __future__ import annotations

import datetime as _dt
import logging
import math
from math import ceil
from typing import Optional

import cv2
import numpy as np
from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .mode_controller import FocusSubMode
from .pipeline import (
    SENSOR_FULL_SCALE, CameraPipeline, _downsample_bayer_to_gray8, _u16_to_gray8,
    exposure_line, exposure_stats,
)
from .serial_client import SerialClient

log = logging.getLogger(__name__)


def _crop_pixmap_center(pm: QPixmap, tw: int, th: int) -> QPixmap:
    """Crop *pm* to *tw*×*th* about the centre (after KeepAspectRatioByExpanding fill)."""
    tw = max(1, int(tw))
    th = max(1, int(th))
    sw, sh = pm.width(), pm.height()
    if sw <= tw and sh <= th:
        return pm
    x = max(0, (sw - tw) // 2)
    y = max(0, (sh - th) // 2)
    return pm.copy(x, y, tw, th)


def _fine_view_crop(
    w: int, h: int, tw: int, th: int, zoom: float
) -> tuple[int, int, int, int]:
    """Centre crop ``(x0, y0, cw, ch)`` of a *w*x*h* tile to show in a *tw*x*th*
    pane at *zoom* screen px per sensor px.

    Rounds up, so the magnified crop covers the pane rather than leaving bars:
    ``cw * zoom < tw + zoom``. Any overshoot is under one magnified pixel and
    the label clips it (it never rescales -- ``setScaledContents(False)``). What
    it never does is return a crop needing a downscale; see
    :meth:`FocusPanel._paint_fine_tile_to_display` for why that matters.
    """
    zoom = max(1.0, float(zoom))
    cw = min(int(w), max(1, int(ceil(int(tw) / zoom))))  # ceil: fill the pane
    ch = min(int(h), max(1, int(ceil(int(th) / zoom))))
    return (int(w) - cw) // 2, (int(h) - ch) // 2, cw, ch


def _composite_fine_overview_inset(base: QPixmap, overview_rgb: np.ndarray) -> QPixmap:
    """Paint pipeline-produced overview RGB onto the bottom-left of *base*."""
    oh, ow = overview_rgb.shape[:2]
    if oh < 2 or ow < 2:
        return base
    inset_w = max(96, int(base.width() * 0.10))
    inset_h = max(2, round(inset_w * oh / ow))
    resized = cv2.resize(
        overview_rgb, (inset_w, inset_h), interpolation=cv2.INTER_AREA
    )
    resized = np.ascontiguousarray(resized)
    h2, w2 = resized.shape[:2]
    ov_qimg = QImage(
        resized.data, w2, h2, w2 * 3, QImage.Format.Format_RGB888
    ).copy()
    ov_pix = QPixmap.fromImage(ov_qimg)

    margin = 10
    out = QPixmap(base)
    painter = QPainter(out)
    ix = margin
    iy = base.height() - h2 - margin
    painter.drawPixmap(ix, iy, ov_pix)
    painter.setPen(QPen(QColor(200, 200, 200)))
    painter.drawRect(ix, iy, w2 - 1, h2 - 1)
    painter.end()
    return out


# Shared CLAHE instance for fine-tile display (reuse is fine; params fixed).
_CLAHE_DISPLAY = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))


def _apply_clahe_display_rgb_u8(rgb: np.ndarray) -> np.ndarray:
    """Display-only adaptive contrast on luminance (LAB L); keeps colour roughly stable."""
    if rgb.size == 0 or rgb.ndim != 3 or rgb.shape[2] != 3:
        return rgb
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)
    l_eq = _CLAHE_DISPLAY.apply(l_ch)
    merged = cv2.merge([l_eq, a_ch, b_ch])
    out = cv2.cvtColor(merged, cv2.COLOR_LAB2RGB)
    return np.ascontiguousarray(out)


class _FocusTileDisplayLabel(QLabel):
    """Avoid pixmap-driven minimumSizeHint feedback on stretched focus previews."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.overlay: Optional[QWidget] = None  # bottom-right AF telemetry graph
        self.scatter_overlay: Optional[QWidget] = None  # top-right score-vs-enc scatter

    def minimumSizeHint(self):  # noqa: N802 -- Qt naming
        return QSize(0, 0)

    def sizeHint(self):  # noqa: N802 -- Qt naming
        return QSize(280, 200)

    def resizeEvent(self, ev):  # noqa: N802 -- Qt naming
        super().resizeEvent(ev)
        self.reposition_overlay()
        self.reposition_scatter_overlay()

    def reposition_overlay(self) -> None:
        ov = self.overlay
        if ov is not None:
            m = 6
            ov.move(max(0, self.width() - ov.width() - m),
                    max(0, self.height() - ov.height() - m))

    def reposition_scatter_overlay(self) -> None:
        # Top-right: the bottom-left is taken by the Fine-mode overview inset
        # (which would hide the scatter), and bottom-right by the AF graph.
        ov = self.scatter_overlay
        if ov is not None:
            m = 6
            ov.move(max(0, self.width() - ov.width() - m), m)


class _AFTelemetryGraph(QWidget):
    """Tiny live overlay plotting the autofocus measurement stream: X = sample
    index, Y = separately auto-scaled traces sharing that x-axis.

    * coarse score (green) -- the focus_measure sweep metric,
    * fine score (amber)   -- the full-res refine metric (a different numeric
      scale, so it gets its OWN vertical auto-scale; otherwise it looks like a
      flatline squashed under the taller coarse spikes),
    * lens position in ENCODER COUNTS (cyan) -- the read-back motor-shaft count,
      not commanded pulses, so it reflects where the lens actually is.

    Each point is one settled AF measurement. The legacy servo runs a single
    metric, so it only ever draws the coarse (green) trace."""

    _MAX = 200  # samples kept on screen

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._scores: list[float] = []
        self._counts: list[int] = []
        self._fine: list[bool] = []
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedSize(230, 130)
        self.hide()

    def clear(self) -> None:
        self._scores.clear()
        self._counts.clear()
        self._fine.clear()
        self.update()

    def add_sample(self, counts: int, score: float, fine: bool = False) -> None:
        self._counts.append(int(counts))
        self._scores.append(float(score))
        self._fine.append(bool(fine))
        if len(self._scores) > self._MAX:
            del self._scores[:-self._MAX]
            del self._counts[:-self._MAX]
            del self._fine[:-self._MAX]
        if not self.isVisible():
            self.show()
        self.update()

    def paintEvent(self, ev):  # noqa: N802 -- Qt naming
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(0, 0, 0, 150))
        p.setPen(QPen(QColor(90, 90, 90), 1))
        p.drawRect(0, 0, w - 1, h - 1)
        green = QColor(80, 230, 120)
        amber = QColor(255, 170, 60)
        cyan = QColor(90, 200, 255)
        p.setPen(green)
        p.drawText(5, 11, "coarse")
        p.setPen(amber)
        p.drawText(45, 11, "fine")
        p.setPen(cyan)
        p.drawText(w - 46, 11, "counts")
        n = len(self._scores)
        if n < 2:
            p.end()
            return
        pad_l = pad_r = 4
        pad_t, pad_b = 15, 14
        plot_w = w - pad_l - pad_r
        plot_h = h - pad_t - pad_b

        def _draw(points, color):
            """points: list of (sample_index, value). Auto-scaled over its own
            values, placed on the shared sample-index x-axis."""
            if len(points) < 2:
                return
            vals = [v for _, v in points]
            lo, hi = min(vals), max(vals)
            rng = (hi - lo) or 1.0
            p.setPen(QPen(color, 1.5))
            px = py = None
            for idx, v in points:
                x = pad_l + plot_w * idx / (n - 1)
                y = pad_t + plot_h * (1.0 - (v - lo) / rng)
                if px is not None:
                    p.drawLine(int(px), int(py), int(x), int(y))
                px, py = x, y

        coarse = [(i, s) for i, s in enumerate(self._scores) if not self._fine[i]]
        fine = [(i, s) for i, s in enumerate(self._scores) if self._fine[i]]
        _draw(list(enumerate(self._counts)), cyan)
        _draw(coarse, green)
        _draw(fine, amber)
        if coarse:
            p.setPen(green)
            p.drawText(5, h - 3, f"{coarse[-1][1]:.0f}")
        if fine:
            p.setPen(amber)
            p.drawText(45, h - 3, f"{fine[-1][1]:.2f}")
        p.setPen(cyan)
        p.drawText(w - 46, h - 3, f"{self._counts[-1]:+d}")
        p.end()


class _FocusScatterGraph(QWidget):
    """Always-on scatter of focus SCORE (Y) vs ENCODER count (X) for the manual
    backlash workflow. Every locked-tile focus frame adds one dot; creeping in
    then backing out draws two arms whose horizontal gap at a given score IS the
    gear backlash (the encoder has to travel that far before the image reacts).

    Both axes auto-scale over the ring buffer. Multiple scores at one encoder
    value are simply multiple dots stacked in Y."""

    _MAX = 600  # dots kept (ring buffer)
    # w10 AF per-ROI dots: roi_boxes order TL, TR, C, BL, BR (sec.52.2)
    ROI_NAMES = ("TL", "TR", "C", "BL", "BR")
    ROI_COLORS = (QColor(255, 110, 110), QColor(255, 200, 80), QColor(120, 230, 160),
                  QColor(110, 170, 255), QColor(220, 130, 255))

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._enc: list[float] = []
        self._score: list[float] = []
        self._roi: list[Optional[int]] = []   # None = pooled / manual-creep score, 0..4 = one ROI's w10
        self._per_roi = False                 # view: pooled dots (False) or the five ROI traces (True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedSize(230, 130)
        self.hide()

    def clear(self) -> None:
        self._enc.clear()
        self._score.clear()
        self._roi.clear()
        self.update()

    def set_per_roi(self, on: bool) -> None:
        """Both point kinds are kept; this only picks which are drawn, so the switch works mid-run."""
        self._per_roi = bool(on)
        self.update()

    def add_point(self, encoder: float, score: float, roi: Optional[int] = None) -> None:
        self._enc.append(float(encoder))
        self._score.append(float(score))
        self._roi.append(roi)
        if len(self._enc) > self._MAX:
            del self._enc[:-self._MAX]
            del self._score[:-self._MAX]
            del self._roi[:-self._MAX]
        if not self.isVisible():
            self.show()
        self.update()

    def paintEvent(self, ev):  # noqa: N802 -- Qt naming
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(0, 0, 0, 150))
        p.setPen(QPen(QColor(90, 90, 90), 1))
        p.drawRect(0, 0, w - 1, h - 1)
        cyan = QColor(90, 200, 255)
        p.setPen(cyan)
        p.drawText(5, 11, "score vs enc")
        if self._per_roi:
            x = 80
            for name, col in zip(self.ROI_NAMES, self.ROI_COLORS):
                p.setPen(col)
                p.drawText(x, 11, name)
                x += 28
        # which dots this view draws: one ROI's w10 each, or the pooled / manual score
        idx = [i for i, r in enumerate(self._roi) if (r is not None) == self._per_roi]
        n = len(idx)
        if n < 1:
            p.end()
            return
        pad_l = pad_r = 6
        pad_t, pad_b = 15, 14
        plot_w = w - pad_l - pad_r
        plot_h = h - pad_t - pad_b
        enc = [self._enc[i] for i in idx]
        sc = [self._score[i] for i in idx]
        emin, emax = min(enc), max(enc)
        smin, smax = min(sc), max(sc)
        erng = (emax - emin) or 1.0
        srng = (smax - smin) or 1.0
        dot = QColor(120, 230, 160)
        newest = QColor(255, 170, 60)
        last = idx[-1]
        for k, i in enumerate(idx):
            x = pad_l + plot_w * (self._enc[i] - emin) / erng
            y = pad_t + plot_h * (1.0 - (self._score[i] - smin) / srng)
            col = self.ROI_COLORS[self._roi[i]] if self._per_roi else dot
            is_newest = i == last if not self._per_roi else i >= last - 4   # the newest frame's five
            p.setPen(QPen(newest if (is_newest and not self._per_roi) else col, 1))
            r = 2.5 if is_newest else 1.5
            p.drawEllipse(int(x - r), int(y - r), int(2 * r), int(2 * r))
        # Corner labels: encoder span (X) and score at the newest dot.
        p.setPen(QColor(170, 170, 170))
        p.drawText(pad_l, h - 3, f"{int(emin)}")
        p.drawText(w - 46, h - 3, f"{int(emax)}")
        p.setPen(newest if not self._per_roi else self.ROI_COLORS[self._roi[last]])
        p.drawText(5, h - 15, f"{self._score[last]:.1f}")
        p.end()


class _ExposureHistGraph(QWidget):
    """Top-left overlay: histogram of the coarse frame's RAW 14-bit counts (green
    phase), log-y, x ticked in stops below full scale. Markers at p0.1 / p50 / p99.9,
    the clipped bin drawn red, and a readout line. Display gain does not move it:
    it answers "how much of the sensor am I using" for what is written to disk."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._st: Optional[dict] = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedSize(230, 130)
        self.hide()

    def set_stats(self, st: Optional[dict]) -> None:
        self._st = st
        self.update()

    def paintEvent(self, ev):  # noqa: N802 -- Qt naming
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(0, 0, 0, 150))
        p.setPen(QPen(QColor(90, 90, 90), 1))
        p.drawRect(0, 0, w - 1, h - 1)
        p.setPen(QColor(90, 200, 255))
        p.drawText(5, 11, "exposure (raw G, 14-bit)")
        st = self._st
        if st is None or st.get("n", 0) == 0:
            p.end()
            return
        pad_l, pad_r, pad_t, pad_b = 6, 6, 15, 28
        plot_w, plot_h = w - pad_l - pad_r, h - pad_t - pad_b
        hist = st["hist"]
        top = math.log1p(float(hist.max()))
        bw = plot_w / len(hist)
        for i, c in enumerate(hist):
            if c == 0:
                continue
            bh = plot_h * math.log1p(float(c)) / top
            if i == len(hist) - 1:  # clipped bin: red and 3 px wide so a marker can't hide it
                p.fillRect(QRectF(pad_l + plot_w - 3, pad_t + plot_h - bh, 3, bh), QColor(255, 80, 80))
            else:
                p.fillRect(QRectF(pad_l + i * bw, pad_t + plot_h - bh, max(bw, 1.0), bh),
                           QColor(120, 230, 160))
        # stop ticks: full scale, 1/2, 1/4, ... down to 1/128
        p.setPen(QColor(120, 120, 120))
        for s in range(0, 8):
            x = pad_l + plot_w * (SENSOR_FULL_SCALE / 2 ** s) / SENSOR_FULL_SCALE
            p.drawLine(int(x), pad_t + plot_h, int(x), pad_t + plot_h + 3)
        # percentile markers
        for key, col in (("p01", QColor(170, 170, 170)), ("p50", QColor(255, 200, 80)),
                         ("p999", QColor(255, 170, 60))):
            x = pad_l + plot_w * min(st[key], SENSOR_FULL_SCALE) / SENSOR_FULL_SCALE
            p.setPen(QPen(col, 1, Qt.PenStyle.DashLine))
            p.drawLine(int(x), pad_t, int(x), pad_t + plot_h)
        p.setPen(QColor(255, 80, 80) if st["clip"] > 0.001 else QColor(200, 200, 200))
        p.drawText(5, h - 15, f"p50 {st['p50']} ({100.0 * st['p50'] / SENSOR_FULL_SCALE:.1f}%)"
                              f"  p99.9 {st['p999']}")
        p.drawText(5, h - 3, f"clip {100.0 * st['clip']:.2f}%  head {st['headroom']:.1f}st"
                             f"  used {st['used']:.1f}st")
        p.end()


class _FieldProfileGraph(QWidget):
    """AF3's live station view: focus SCORE (Y) against position ACROSS THE FRAME (X),
    one polyline per tracked object.

    This is a different question from the score-vs-encoder scatter above it. That one
    asks "what does focus do to this patch"; this one asks "what does the FIELD do at
    this one lens position", which is what AF3 actually steers on. The lens is dead
    still for the whole of a station, so every curve here is the field, not focus.

    What to look for: a hump peaking mid-frame means the lens is near best focus (the
    centre only out-scores the edges when it is near its own vertex). A bowl -- edges
    high, middle low -- means it is well off, either side. D is that shape reduced to
    one number, and its sign is the dome/bowl verdict.

    Two operator-chosen display rules, both of which change what the picture answers:

    *Only COMPLETED traverses are drawn.* A partial arc has not visited both edge bands
    yet, so its shape is a fragment of a profile and reads as a slope that is not there.
    The engine only publishes trails that actually yielded a D, so the curves on screen
    are exactly the ones the station's median is built from -- nothing shown that did
    not count, nothing counted that is not shown.

    *Each curve is normalised to ITS OWN peak*, on a fixed 0..1 axis. Content then
    cancels in the picture the same way it cancels in D, so the curves overlay and the
    only thing separating them is shape. The fixed axis is what makes depth readable
    across stations: as the lens walks, a bowl flattening into a dome and back is a
    real change in these curves' depth, which an auto-scaled axis would hide by
    re-stretching every frame. The cost is that a patch scored near the noise floor no
    longer looks low -- it looks like any other curve -- so judge acquisition quality
    from the scatter above, not from here."""

    _COLOURS = ((120, 230, 160), (255, 170, 60), (140, 190, 255),
                (240, 130, 200), (220, 220, 120))

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._row: dict = {}
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedSize(260, 150)
        self.hide()

    def clear(self) -> None:
        self._row = {}
        self.hide()

    def update_row(self, row: dict) -> None:
        self._row = dict(row or {})
        if not self.isVisible():
            self.show()
        self.update()

    def paintEvent(self, ev):  # noqa: N802 -- Qt naming
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(0, 0, 0, 160))
        p.setPen(QPen(QColor(90, 90, 90), 1))
        p.drawRect(0, 0, w - 1, h - 1)

        row = self._row
        cyan = QColor(90, 200, 255)
        p.setPen(cyan)
        pos = row.get("pos")
        p.drawText(5, 11, "field profile  st%s%s  norm" % (
            row.get("station", "-"),
            "" if pos is None else " @ %+dc" % int(pos)))

        completed = list(row.get("completed") or [])
        pts = [t for _oid, tr in completed for t in tr]
        pad_l, pad_r, pad_t, pad_b = 6, 6, 16, 26
        plot_w, plot_h = w - pad_l - pad_r, h - pad_t - pad_b
        if plot_w <= 1 or plot_h <= 1:
            p.end()
            return

        # Travel axis from the data, exactly as profile_metric decides it, so the
        # picture and the number can never disagree about which way the scene moves.
        fw = float(row.get("frame_w") or 1)
        fh = float(row.get("frame_h") or 1)
        use_x = True
        if pts:
            use_x = (max(t[0] for t in pts) - min(t[0] for t in pts)) >= \
                    (max(t[1] for t in pts) - min(t[1] for t in pts))
        extent = fw if use_x else fh

        # Edge bands (outer quarter each end) and the frame centre, so the operator can
        # see WHICH samples D is actually differencing.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 16))
        p.drawRect(pad_l, pad_t, int(0.25 * plot_w), plot_h)
        p.drawRect(pad_l + int(0.75 * plot_w), pad_t, int(0.25 * plot_w), plot_h)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(120, 120, 120), 1, Qt.PenStyle.DashLine))
        xc = pad_l + plot_w // 2
        p.drawLine(xc, pad_t, xc, pad_t + plot_h)

        if not pts:
            p.setPen(QColor(160, 160, 160))
            p.drawText(pad_l, pad_t + plot_h // 2, "waiting for traverses...")
            p.end()
            return

        # Half-scale marker: with every curve pinned to 1.0 at its own peak, this is
        # the "lost half its contrast" line, which is the depth worth eyeballing.
        p.setPen(QPen(QColor(70, 70, 70), 1, Qt.PenStyle.DotLine))
        p.drawLine(pad_l, pad_t + plot_h // 2, pad_l + plot_w, pad_t + plot_h // 2)

        for i, (_oid, tr) in enumerate(sorted(completed, key=lambda c: c[0])):
            peak = max(t[2] for t in tr) or 1.0
            p.setPen(QPen(QColor(*self._COLOURS[i % len(self._COLOURS)]), 1))
            prev = None
            for cx, cy, sc in tr:
                u = (cx if use_x else cy) / max(extent, 1.0)
                x = pad_l + int(min(max(u, 0.0), 1.0) * plot_w)
                frac = min(max(sc / peak, 0.0), 1.0)
                y = pad_t + plot_h - int(frac * plot_h)
                if prev is not None:
                    p.drawLine(prev[0], prev[1], x, y)
                prev = (x, y)

        banked = row.get("banked") or []
        need = row.get("need")
        p.setPen(cyan)
        if banked:
            med = sorted(banked)[len(banked) // 2]
            verdict = "DOME" if med > 0 else "bowl"
            p.drawText(5, h - 14, "D=%+.2f %s  (%d%s traverses)" % (
                med, verdict, len(banked), "" if need is None else "/%d" % need))
        else:
            p.drawText(5, h - 14, "no completed traverse yet")
        done = row.get("stations") or []
        if done:
            best = max(done, key=lambda s: s[1])
            p.setPen(QColor(180, 180, 180))
            p.drawText(5, h - 3, "best so far D=%+.2f @ %+dc  (%d stations)"
                       % (best[1], int(best[0]), len(done)))
        p.end()


class _PreviewStack(QStackedWidget):
    """Host that swaps the single focus display (page 0) for the two-object split
    preview (page 1). The score graphs are parented HERE -- not on either page --
    so they stay visible over both views (the standing "scatter always on" rule)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.overlay: Optional[QWidget] = None          # bottom-right AF telemetry
        self.scatter_overlay: Optional[QWidget] = None  # top-right score-vs-enc scatter
        self.profile_overlay: Optional[QWidget] = None  # AF3 field profile, under it
        self.hist_overlay: Optional[QWidget] = None     # top-left exposure histogram

    def resizeEvent(self, ev):  # noqa: N802 -- Qt naming
        super().resizeEvent(ev)
        self.reposition_overlays()

    def reposition_overlays(self) -> None:
        m = 6
        hs = self.hist_overlay
        if hs is not None:
            hs.move(m, m)
            hs.raise_()
        ov = self.overlay
        if ov is not None:
            ov.move(max(0, self.width() - ov.width() - m),
                    max(0, self.height() - ov.height() - m))
            ov.raise_()
        sc = self.scatter_overlay
        if sc is not None:
            sc.move(max(0, self.width() - sc.width() - m), m)
            sc.raise_()
        # Directly beneath the scatter: the two read together (score vs focus above,
        # score vs field position below) and AF3 needs both to be judged at a glance.
        pr = self.profile_overlay
        if pr is not None:
            top = m + (sc.height() + m if sc is not None else 0)
            pr.move(max(0, self.width() - pr.width() - m), top)
            pr.raise_()


class _DualObjectPreview(QWidget):
    """Split-screen: two panes, each following ONE tracked object from the moment
    it enters the FOV until it pans off, so the operator can watch each object for
    as long as possible. Fed coarse-frame grayscale crops via :meth:`set_pane_gray8`
    (the tracker relays a fresh object onto a pane as soon as the old one exits)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background:#101010;")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self._panes: list[QLabel] = []
        for i in range(2):
            lab = QLabel(f"object {i + 1}: waiting…")
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lab.setStyleSheet(
                "background:#0a0a0a; color:#888; font-family:Consolas, monospace;"
                "font-size:11px; border:1px solid #222;"
            )
            # Ignored policy + a tiny fixed minimum size decouples the label's size
            # from its pixmap. A QLabel reports its pixmap's size as minimumSizeHint,
            # so scaling each crop to the label's CURRENT size (rounding up a pixel)
            # would creep the layout -> stacked-widget sizeHint -> the whole window
            # bigger every frame. Ignored means the layout drives the label, never
            # the other way round.
            lab.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
            )
            lab.setMinimumSize(1, 1)
            lab.setScaledContents(False)
            row.addWidget(lab, 1)
            self._panes.append(lab)

    def set_pane_gray8(self, idx: int, gray8: Optional[np.ndarray]) -> None:
        """Show a grayscale crop (H×W uint8) in pane *idx*; None -> idle placeholder."""
        if idx < 0 or idx >= len(self._panes):
            return
        lab = self._panes[idx]
        if gray8 is None or gray8.size == 0:
            lab.setPixmap(QPixmap())
            lab.setText(f"object {idx + 1}: waiting…")
            return
        g = np.ascontiguousarray(gray8)
        h, w = g.shape[:2]
        qimg = QImage(g.data, w, h, w, QImage.Format.Format_Grayscale8).copy()
        pix = QPixmap.fromImage(qimg)
        tw, th = max(1, lab.width()), max(1, lab.height())
        scaled = pix.scaled(
            tw, th,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        lab.setPixmap(_crop_pixmap_center(scaled, tw, th))


class FocusPanel(QWidget):
    """M4 focus UI: coarse live preview or fine tile + inset; compact controls."""

    save_toggled = Signal(bool)
    # Emitted when the operator clicks one of the in-panel camera radios
    # (1, 2 or 3). The MainWindow listens for this and routes frames /
    # focus tap / ``$Cn*`` PCB selection to the chosen camera. Decoupled
    # via a signal so the panel itself stays ignorant of the pipelines
    # and rows it ultimately drives.
    camera_chosen = Signal(int)

    focus_submode_changed = Signal(object)  # FocusSubMode

    fine_demosaic_changed = Signal(bool)

    # Autofocus controls. The MainWindow owns the AutoFocusController; the panel
    # just emits intent (the leading edge is read via af_lead_hint) and reflects
    # run state/status.
    autofocus_requested = Signal()       # continuous servo on the active camera
    autofocus_all_requested = Signal()   # focus cam1..3 once, in sequence
    autofocus_stop_requested = Signal()
    field_map_requested = Signal()       # one sweep, fixed tiles, a vertex per tile
    align_toggled = Signal(bool)         # live camera-vs-screen tilt readout, shown ON the screen
    observe_requested = Signal()         # fixed focus, N tracked patches, CSV per frame
    autofocus_algorithm_changed = Signal(str)  # "w10" | "sweep_and_hold" | "legacy"
    fine_lock_changed = Signal(bool)     # freeze the tracked Fine tile (no re-pick)
    csv_log_toggled = Signal(bool)       # start/stop focus-telemetry CSV logging
    preview_toggled = Signal(bool)       # split-screen two-object preview on/off
    dual_track_toggled = Signal(bool)    # two-object tracking overlay (green boxes) on/off
    orthosim_toggled = Signal(bool)      # OrthoSim ground-truth decode + AF interior masking on/off
    lockstep_toggled = Signal(bool)      # closed-loop display/camera handshake on/off (bench only)
    m4_record_toggled = Signal(bool)     # arm/disarm .bay14line writing while in M4

    # Emitted at low cadence whenever the panel receives a coarse frame or a
    # fine tile. Used by the MainWindow to surface the M4 focus-tap chain.
    diag_log = Signal(str)

    def __init__(self, serial: SerialClient, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._serial = serial
        self._active_pipeline: Optional[CameraPipeline] = None
        self._receive_count = 0
        self._last_coarse_raw: Optional[np.ndarray] = None
        self._last_fine_rgb: Optional[np.ndarray] = None
        self._last_fine_meta: Optional[dict] = None
        # Autofocus winning-tile overlay (coarse-frame pixel coords) or None.
        self._af_roi: Optional[tuple[int, int, int, int]] = None
        # Two-object tracking overlay: list of (oid, coarse-frame roi) green boxes.
        self._track_rois: list = []
        # When True, the coarse preview shows just the AF tile, zoomed to fill,
        # instead of the whole frame with the tile outlined -- lets the operator
        # watch focus sharpen on the actual patch AF is optimising.
        self._af_zoom = False
        # Latest FULL-RES winning-tile crop (raw Bayer uint16) -- what AF actually
        # scores. The Zoom-tile view renders its green plane so the operator sees
        # the same resolution AF optimises, not the 4x preview decimated again.
        self._af_fullres_crop: Optional[np.ndarray] = None
        # The main window's "Display gain" (preview-only brightness), applied to the
        # views this panel renders itself: Coarse preview and the AF tile zoom.
        self._display_gain = 1.0
        self._cam_device_ids: dict[int, str] = {}
        self._cam_available: dict[int, bool] = {}
        self._controls_locked = False

        self._build_ui()
        self._install_m4_arrow_hotkeys()

    def fine_demosaic_enabled(self) -> bool:
        """Whether Fine mode uses OpenCV debayer on the tile (vs raw Bayer gray)."""
        return self._debayer_chk.isChecked()

    def sync_focus_submode_toggle(self, sub: FocusSubMode) -> None:
        """Align the Coarse/Fine button with :class:`ModeController` state."""
        self._fine_toggle.blockSignals(True)
        self._fine_toggle.setChecked(sub == FocusSubMode.FINE)
        self._fine_toggle.blockSignals(False)
        self._debayer_chk.setEnabled(self._fine_toggle.isChecked())
        self._clahe_chk.setEnabled(self._fine_toggle.isChecked())
        if sub == FocusSubMode.COARSE:
            self._last_fine_rgb = None
            self._last_fine_meta = None
        self._sync_fine_toggle_caption()

    def set_dwell_info(self, text: str) -> None:
        """Show which tracked object is in the Fine view and how long it lasts."""
        self._dwell_label.setText(text)

    def sync_m4_record_toggle(self, on: bool) -> None:
        """Align the Record checkbox with :class:`ModeController` state.

        Silent: a mode change disarms recording in the controller, and the box
        has to follow without re-emitting and re-closing an already-closed line.
        """
        self._m4_record_toggle.blockSignals(True)
        self._m4_record_toggle.setChecked(bool(on))
        self._m4_record_toggle.blockSignals(False)

    def _sync_fine_toggle_caption(self) -> None:
        """Button shows the mode you get when you click (unchecked=Fine, checked=Coarse)."""
        if self._fine_toggle.isChecked():
            self._fine_toggle.setText("Coarse mode")
            self._fine_toggle.setToolTip(
                "Currently Fine view. Click for coarse decimated preview "
                "(full frame, no tile zoom)."
            )
        else:
            self._fine_toggle.setText("Fine mode")
            self._fine_toggle.setToolTip(
                "Currently Coarse view. Click for full-res leading-edge tile "
                "(tracks the winning patch until it exits; M4/M5)."
            )

    # ── UI ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.setStyleSheet(
            """
            QLabel { font-size: 11px; }
            QGroupBox {
                font-size: 11px;
                margin-top: 2px;
                padding-top: 1px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 4px;
                padding: 0 2px;
            }
            QRadioButton, QCheckBox { font-size: 11px; }
            QPushButton {
                font-size: 11px;
                padding: 2px 8px;
                min-height: 22px;
                max-height: 28px;
            }
            QSpinBox {
                font-size: 11px;
                min-height: 22px;
                max-height: 26px;
            }
            """
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(2)

        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        top_row.setContentsMargins(0, 0, 0, 0)

        self._mode_strip_host = QWidget()
        self._mode_strip_layout = QHBoxLayout(self._mode_strip_host)
        self._mode_strip_layout.setContentsMargins(0, 0, 0, 0)
        self._mode_strip_layout.setSpacing(4)
        self._mode_strip_host.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
        )
        top_row.addWidget(self._mode_strip_host, 0)

        top_row.addSpacing(8)
        self._add_camera_selector_widgets(top_row)

        top_row.addStretch(1)

        top_row.addWidget(QLabel("View:"))
        self._fine_toggle = QPushButton("Fine mode")
        self._fine_toggle.setCheckable(True)
        self._fine_toggle.toggled.connect(self._on_fine_toggle_toggled)
        top_row.addWidget(self._fine_toggle)
        self._debayer_chk = QCheckBox("Debayer")
        self._debayer_chk.setChecked(True)
        self._debayer_chk.setToolTip(
            "When Fine mode is on: OpenCV BayerRG demosaic. "
            "Uncheck for raw Bayer mosaic as grayscale (no demosaic)."
        )
        self._debayer_chk.toggled.connect(self._on_debayer_toggled)
        top_row.addWidget(self._debayer_chk)
        self._clahe_chk = QCheckBox("CLAHE")
        self._clahe_chk.setToolTip(
            "Fine view only: contrast-limited adaptive histogram equalization "
            "on luminance (LAB). Display only; does not affect saved focus TIFFs."
        )
        self._clahe_chk.toggled.connect(self._on_clahe_display_toggled)
        top_row.addWidget(self._clahe_chk)
        top_row.addWidget(QLabel("Zoom:"))
        self._zoom_box = QComboBox()
        self._zoom_box.addItems(["1x", "1.5x", "1.8x", "2x", "4x"])
        self._zoom_box.setToolTip(
            "Fine view magnification, in SCREEN pixels per SENSOR pixel. Integer "
            "only, and never below 1x: a view scaled below 1x averages sensor "
            "pixels together, which blurs away the high spatial frequencies focus "
            "is judged on and shows a sharp lens as soft. Raising the zoom shows "
            "less of the tracked tile, never more detail than the sensor captured."
        )
        self._zoom_box.currentIndexChanged.connect(self._on_fine_zoom_changed)
        top_row.addWidget(self._zoom_box)

        self._preview_toggle = QPushButton("2-obj preview")
        self._preview_toggle.setCheckable(True)
        self._preview_toggle.setToolTip(
            "Split the view into two panes, each following one tracked object from "
            "when it enters until it leaves the FOV (independent of autofocus)."
        )
        self._preview_toggle.toggled.connect(self._on_preview_toggle)
        top_row.addWidget(self._preview_toggle)

        self._track_toggle = QPushButton("2-obj track")
        self._track_toggle.setCheckable(True)
        self._track_toggle.setToolTip(
            "Track two leading-edge objects (no autofocus) and outline each with a "
            "green box + its object id on the coarse preview, to check the flow of "
            "objects from the leading edge across the FOV."
        )
        self._track_toggle.toggled.connect(self._on_track_toggle)
        top_row.addWidget(self._track_toggle)

        # OrthoSim bench rig: decode the on-screen optical ground-truth bar
        # (distance / velocity / simulated GSD) and mask AF off the bezel + bar.
        # The W/H/edge must match the OrthoSim display run so the homography
        # samples the bar cells at their known positions.
        self._ortho_toggle = QPushButton("OrthoSim GT")
        self._ortho_toggle.setCheckable(True)
        self._ortho_toggle.setToolTip(
            "OrthoSim rig: decode the screen's optical ground-truth bar (distance / "
            "velocity / simulated GSD ~= altitude) and log + overlay it, and restrict "
            "AF tiles to the screen interior (off the bezel, fiducials and optical "
            "strip). Set screen W/H + bar edge to match the OrthoSim display run."
        )
        self._ortho_toggle.toggled.connect(self._on_orthosim_toggle)
        top_row.addWidget(self._ortho_toggle)

        self._ortho_w = QSpinBox()
        self._ortho_w.setRange(640, 15360)
        self._ortho_w.setValue(3840)
        self._ortho_w.setToolTip("OrthoSim display width (px)")
        top_row.addWidget(self._ortho_w)
        self._ortho_h = QSpinBox()
        self._ortho_h.setRange(480, 8640)
        self._ortho_h.setValue(2160)
        self._ortho_h.setToolTip("OrthoSim display height (px)")
        top_row.addWidget(self._ortho_h)
        self._ortho_bar = QComboBox()
        self._ortho_bar.addItems(["top", "bottom"])
        self._ortho_bar.setToolTip("Which screen edge the optical bar sits on")
        top_row.addWidget(self._ortho_bar)

        outer.addLayout(top_row)

        self._display = _FocusTileDisplayLabel("M4: waiting for first focus frame…")
        self._display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._display.setStyleSheet(
            "background:#101010; color:#888; font-family:Consolas, monospace;"
            "font-size:11px;"
        )
        self._display.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._display.setMinimumHeight(320)
        self._display.setScaledContents(False)

        # Stack: page 0 = the normal single focus display; page 1 = the two-object
        # split preview. The score graphs are parented on the STACK (not the display)
        # so they float over whichever page is showing (scatter stays always-on).
        self._preview_stack = _PreviewStack()
        self._preview_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._preview_stack.addWidget(self._display)          # page 0
        self._dual_preview = _DualObjectPreview()
        self._preview_stack.addWidget(self._dual_preview)     # page 1
        self._preview_stack.setCurrentIndex(0)

        self._af_graph = _AFTelemetryGraph(self._preview_stack)
        self._preview_stack.overlay = self._af_graph
        # Always-on encoder-vs-score scatter for the manual backlash workflow,
        # top-right (bottom-left is the Fine overview inset, bottom-right the AF
        # telemetry graph).
        self._scatter_graph = _FocusScatterGraph(self._preview_stack)
        self._preview_stack.scatter_overlay = self._scatter_graph
        self._scatter_graph.show()
        # AF3's station view. Hidden until a station publishes, so AF2/legacy runs
        # look exactly as they did.
        self._profile_graph = _FieldProfileGraph(self._preview_stack)
        self._preview_stack.profile_overlay = self._profile_graph
        # Exposure histogram of the raw coarse frame, top-left; `Hist` checkbox.
        self._hist_graph = _ExposureHistGraph(self._preview_stack)
        self._preview_stack.hist_overlay = self._hist_graph
        self._hist_graph.show()
        self._last_exposure: Optional[dict] = None
        self._preview_stack.reposition_overlays()
        outer.addWidget(self._preview_stack, 1)

        outer.addLayout(self._build_bottom_control_row())
        outer.addLayout(self._build_autofocus_row())
        self._focus_step_box.hide()
        self._period_box.hide()
        self._debayer_chk.setEnabled(self._fine_toggle.isChecked())
        self._clahe_chk.setEnabled(self._fine_toggle.isChecked())
        self._zoom_box.setEnabled(self._fine_toggle.isChecked())
        self._sync_fine_toggle_caption()

    def _on_debayer_toggled(self, checked: bool) -> None:
        self.fine_demosaic_changed.emit(bool(checked))

    def _repaint_last_fine_tile(self) -> None:
        """Re-render the tile already in hand, for display-only setting changes."""
        if (
            self._fine_toggle.isChecked()
            and self._last_fine_rgb is not None
            and self._last_fine_meta is not None
        ):
            self._paint_fine_tile_to_display(self._last_fine_rgb, self._last_fine_meta)

    def _on_clahe_display_toggled(self, _checked: bool) -> None:
        self._repaint_last_fine_tile()

    def _on_fine_zoom_changed(self, _index: int) -> None:
        self._repaint_last_fine_tile()

    def fine_view_sensor_wh(self) -> tuple[int, int]:
        """Sensor pixels this pane can display right now, as (width, height).

        The same ``ceil(pane / zoom)`` as :func:`_fine_view_crop`, so a tile cut to
        this size is displayed WHOLE: nothing scored gets cropped away, and no pane
        pixel is left grey. It cannot be a constant -- the display label is
        Expanding/Expanding so it grows with the window, and the zoom is a combo
        box -- which is why the nominal 1280x720 left most of a maximised pane
        empty at 1x.
        """
        zoom = self._fine_zoom()
        return (
            max(2, ceil(self._display.width() / zoom)),
            max(2, ceil(self._display.height() / zoom)),
        )

    def _fine_zoom(self) -> float:
        """Screen pixels per sensor pixel for the Fine view; >= 1 by construction."""
        try:
            return max(1.0, float(self._zoom_box.currentText().rstrip("xX")))
        except ValueError:
            return 1.0

    def _on_fine_toggle_toggled(self, checked: bool) -> None:
        self._debayer_chk.setEnabled(bool(checked))
        self._clahe_chk.setEnabled(bool(checked))
        self._zoom_box.setEnabled(bool(checked))
        if not checked:
            self._last_fine_rgb = None
            self._last_fine_meta = None
        self._sync_fine_toggle_caption()
        sub = FocusSubMode.FINE if checked else FocusSubMode.COARSE
        self.focus_submode_changed.emit(sub)

    def _on_preview_toggle(self, checked: bool) -> None:
        self._preview_stack.setCurrentIndex(1 if checked else 0)
        self._preview_stack.reposition_overlays()  # keep the graphs on top of the new page
        if not checked:
            self._dual_preview.set_pane_gray8(0, None)
            self._dual_preview.set_pane_gray8(1, None)
        self.preview_toggled.emit(bool(checked))

    def preview_active(self) -> bool:
        return self._preview_toggle.isChecked()

    def _on_track_toggle(self, checked: bool) -> None:
        if not checked:
            self._track_rois = []
            if not self._fine_toggle.isChecked() and self._last_coarse_raw is not None:
                self._refresh_coarse_preview()
        self.dual_track_toggled.emit(bool(checked))

    def track_active(self) -> bool:
        return self._track_toggle.isChecked()

    def _on_orthosim_toggle(self, checked: bool) -> None:
        # Lock the layout inputs while active so the decoder's ScreenLayout can't
        # drift mid-run; the app reads them when the toggle flips on.
        for w in (self._ortho_w, self._ortho_h, self._ortho_bar):
            w.setEnabled(not checked)
        self.orthosim_toggled.emit(bool(checked))

    def orthosim_active(self) -> bool:
        return self._ortho_toggle.isChecked()

    def ortho_screen_wh(self) -> tuple[int, int]:
        return int(self._ortho_w.value()), int(self._ortho_h.value())

    def ortho_bar_edge(self) -> str:
        return self._ortho_bar.currentText()

    def set_track_rois(self, rois: list) -> None:
        """Set the two-object tracking boxes to outline on the coarse preview.
        ``rois`` is a list of (oid, (x0,y0,x1,y1)) in coarse-frame pixel coords
        (entries may be None for an idle pane)."""
        self._track_rois = list(rois) if rois else []
        if not self._fine_toggle.isChecked() and self._last_coarse_raw is not None:
            self._refresh_coarse_preview()

    def receive_object_crops(self, crops) -> None:
        """Paint the two tracked-object crops (list of 2 grayscale uint8 arrays or
        None) into the split-preview panes. No-op unless preview mode is on."""
        if not self._preview_toggle.isChecked():
            return
        for i in range(2):
            crop = crops[i] if crops is not None and i < len(crops) else None
            self._dual_preview.set_pane_gray8(i, crop)

    def _install_m4_arrow_hotkeys(self) -> None:
        """Arrow keys mirror Focus ± and Trigger period < > while M4 is active.

        Shortcuts use :attr:`Qt.ShortcutContext.WidgetWithChildrenShortcut` so
        they apply when this panel or a child has focus (and are inactive when
        the panel is hidden in the stacked preview). When the step-count
        ``QSpinBox`` has focus, Qt delivers ↑/↓ to the spinbox first so the
        operator can still nudge the step value.
        """
        ctx = Qt.ShortcutContext.WidgetWithChildrenShortcut
        sc_up = QShortcut(QKeySequence(Qt.Key.Key_Up), self)
        sc_up.setContext(ctx)
        sc_up.activated.connect(self._hotkey_focus_plus)
        sc_down = QShortcut(QKeySequence(Qt.Key.Key_Down), self)
        sc_down.setContext(ctx)
        sc_down.activated.connect(self._hotkey_focus_minus)
        sc_right = QShortcut(QKeySequence(Qt.Key.Key_Right), self)
        sc_right.setContext(ctx)
        sc_right.activated.connect(self._hotkey_period_gt)
        sc_left = QShortcut(QKeySequence(Qt.Key.Key_Left), self)
        sc_left.setContext(ctx)
        sc_left.activated.connect(self._hotkey_period_lt)

    def _hotkey_focus_plus(self) -> None:
        self._serial.focus_step("+", self._focus_count.value())

    def _hotkey_focus_minus(self) -> None:
        self._serial.focus_step("-", self._focus_count.value())

    def _hotkey_period_gt(self) -> None:
        self._serial.adjust_trigger_period(">")

    def _hotkey_period_lt(self) -> None:
        self._serial.adjust_trigger_period("<")

    def mode_strip_layout(self) -> QHBoxLayout:
        """Host layout for M0/M1/M4 mode buttons while M4 is active (MainWindow-owned)."""
        return self._mode_strip_layout

    def clear_mode_strip_layout(self) -> None:
        """Remove all widgets and spacers (used when leaving M4)."""
        lay = self._mode_strip_layout
        while lay.count():
            item = lay.takeAt(0)
            if item is None:
                continue
            w = item.widget()
            if w is not None:
                w.setParent(None)

    def _cam_selector_label(self, camera_number: int) -> str:
        """Radio text: ``camN`` plus ``[device_id]`` when the serial is known."""
        device_id = self._cam_device_ids.get(int(camera_number), "").strip()
        if device_id:
            return f"cam{camera_number} [{device_id}]"
        return f"cam{camera_number}"

    def _refresh_cam_selector_label(self, camera_number: int) -> None:
        rb = self._cam_radios.get(int(camera_number))
        if rb is None:
            return
        text = self._cam_selector_label(camera_number)
        rb.setText(text)
        rb.setToolTip(text)

    def set_camera_device_id(self, camera_number: int, device_id: str) -> None:
        """Show the KAYA device ID in brackets on the M4 camera selector."""
        n = int(camera_number)
        text = (device_id or "").strip()
        if text:
            self._cam_device_ids[n] = text
        else:
            self._cam_device_ids.pop(n, None)
        self._refresh_cam_selector_label(n)

    def _add_camera_selector_widgets(self, row: QHBoxLayout) -> None:
        """``Camera: cam1 [id] cam2 [id] …`` radios into *row* (no trailing stretch)."""
        row.addWidget(QLabel("Camera:"))
        self._cam_group = QButtonGroup(self)
        self._cam_group.setExclusive(True)
        self._cam_radios: dict[int, QRadioButton] = {}
        for n in (1, 2, 3):
            rb = QRadioButton(self._cam_selector_label(n))
            rb.setEnabled(False)  # MainWindow re-enables once pipelines are up
            rb.toggled.connect(
                lambda on, num=n: on and self.camera_chosen.emit(num)
            )
            self._cam_group.addButton(rb)
            self._cam_radios[n] = rb
            row.addWidget(rb)

    def set_camera_available(self, camera_number: int, available: bool) -> None:
        """Enable / disable a camera's radio. Called from MainWindow as
        each pipeline comes up (or fails to)."""
        self._cam_available[int(camera_number)] = bool(available)
        rb = self._cam_radios.get(int(camera_number))
        if rb is not None and not self._controls_locked:
            rb.setEnabled(bool(available))

    def set_controls_locked(self, locked: bool) -> None:
        """Lock camera + strength controls while autofocus drives them."""
        self._controls_locked = bool(locked)
        for n, rb in self._cam_radios.items():
            rb.setEnabled(not self._controls_locked and self._cam_available.get(n, False))
        for btn in self._strength_group.buttons():
            btn.setEnabled(not self._controls_locked)

    def set_active_camera(self, camera_number: Optional[int]) -> None:
        """Reflect the externally-selected active camera in our radio
        strip + status label, without re-emitting :sig:`camera_chosen`.

        Called from MainWindow when the user selects a camera elsewhere
        (or when we auto-pick one on M4 entry) so the in-panel radios
        track the source of truth.
        """
        if camera_number is None:
            self._active_cam_label.setText("(no camera selected)")
            for rb in self._cam_radios.values():
                rb.blockSignals(True)
                rb.setChecked(False)
                rb.blockSignals(False)
            return
        rb = self._cam_radios.get(int(camera_number))
        if rb is None:
            return
        rb.blockSignals(True)
        rb.setChecked(True)
        rb.blockSignals(False)
        self._active_cam_label.setText(f"focusing cam{camera_number}")

    def _build_bottom_control_row(self) -> QHBoxLayout:
        """Single compact row: tile readout, strength, save, active camera."""
        row = QHBoxLayout()
        row.setSpacing(6)
        row.setContentsMargins(0, 2, 0, 2)

        self._tile_info_label = QLabel("tile #-/-  score=0.0")
        self._tile_info_label.setStyleSheet(
            "font-family:Consolas, monospace; font-size:11px;"
        )
        self._tile_info_label.setMinimumWidth(220)
        row.addWidget(self._tile_info_label, 0)

        self._dwell_label = QLabel("")
        self._dwell_label.setStyleSheet(
            "font-family:Consolas, monospace; font-size:11px; color:#8cf;"
        )
        self._dwell_label.setMinimumWidth(190)
        self._dwell_label.setToolTip(
            "The tracked object in Fine view: which one (obj), how many frames it\n"
            "has been held, and roughly how many are left before it reaches the\n"
            "trailing edge and a fresh leading-edge object is picked.\n\n"
            "Watch the object number: if it changes while you are adjusting, the\n"
            "sharpness you are judging belongs to different ground."
        )
        row.addWidget(self._dwell_label, 0)

        row.addSpacing(4)
        row.addWidget(QLabel("Strength"))
        self._strength_group = QButtonGroup(self)
        self._strength_group.setExclusive(True)
        for level, k in ((0, "3.5k"), (1, "7k"), (2, "10k"), (3, "14k")):
            rb = QRadioButton(str(level))
            rb.setToolTip(
                f"Strength {level} ({k} PWM duty). "
                + ("Extra-fine, for autofocus (may stall if the motor can't "
                   "overcome static friction)." if level == 0
                   else "Larger move per 10 ms pulse.")
            )
            if level == 2:
                rb.setChecked(True)
            rb.toggled.connect(
                lambda on, lv=level: on and self._serial.set_strength(lv)
            )
            self._strength_group.addButton(rb, id=level)
            row.addWidget(rb)

        row.addSpacing(8)
        self._m4_record_toggle = QCheckBox("Record")
        self._m4_record_toggle.setToolTip(
            "Write full .bay14line containers while in M4, exactly as M0/M1 do.\n\n"
            "For capturing a focus bracket to analyse on the ground: every frame's\n"
            "chunk JSON carries the lens encoder count, so a run from out-of-focus\n"
            "through focus and out again can be sorted by lens position afterwards.\n\n"
            "Manual arm, because M4 is where idle time is spent and always-on would\n"
            "fill the drive. Disarms itself on any mode change. Set drive, project\n"
            "and line first -- the same prerequisites as M0/M1."
        )
        self._m4_record_toggle.setStyleSheet("QCheckBox:checked { color:#f55; }")
        self._m4_record_toggle.toggled.connect(self.m4_record_toggled.emit)
        row.addWidget(self._m4_record_toggle)

        self._save_toggle = QCheckBox("Save focus view")
        self._save_toggle.setToolTip(
            "When checked in M4 Fine mode, each best-tile crop is written\n"
            "as a TIFF under <drive>:\\<project>\\focus\\."
        )
        self._save_toggle.toggled.connect(self._on_save_toggled)
        row.addWidget(self._save_toggle)

        self._lockstep_toggle = QCheckBox("Lockstep")
        self._lockstep_toggle.setToolTip(
            "OrthoSim bench rig only. Drives the display and the camera as a closed\n"
            "loop: the display reports when the picture is painted AND settled, the\n"
            "app then fires ONE trigger into a provably static panel, and the scene\n"
            "does not move again until the frame is in.\n\n"
            "Removes the doubled crops at the source instead of rejecting them, so\n"
            "every frame becomes a measurement rather than every other one.\n\n"
            "Needs the display app running and firmware with $X1* support. NEVER in\n"
            "flight: it stops the camera self-triggering, so with nothing answering\n"
            "there are no frames until it gives up and reverts."
        )
        self._lockstep_toggle.toggled.connect(self.lockstep_toggled.emit)
        row.addWidget(self._lockstep_toggle)

        self._csv_log_toggle = QCheckBox("Log CSV")
        self._csv_log_toggle.setToolTip(
            "Log manual-backlash focus telemetry to a CSV in the 'Session logs'\n"
            "folder: one row per locked-tile focus frame (timestamp, frame,\n"
            "encoder, focus score). Lock a tile and creep focus to record the\n"
            "score-vs-encoder curve for offline backlash analysis."
        )
        self._csv_log_toggle.toggled.connect(self.csv_log_toggled.emit)
        row.addWidget(self._csv_log_toggle)

        row.addStretch(1)

        self._active_cam_label = QLabel("(no camera selected)")
        self._active_cam_label.setStyleSheet(
            "font-family:Consolas, monospace; color:#aaa; font-size:11px;"
        )
        row.addWidget(self._active_cam_label)

        self._focus_step_box = self._build_focus_step_box()
        self._period_box = self._build_period_box()
        self._focus_step_box.setParent(self)
        self._period_box.setParent(self)
        self._focus_step_box.hide()
        self._period_box.hide()

        return row

    def _build_autofocus_row(self) -> QHBoxLayout:
        """Autofocus controls: run / run-all / stop + leading-edge + status."""
        row = QHBoxLayout()
        row.setSpacing(6)
        row.setContentsMargins(0, 2, 0, 2)

        self._af_button = QPushButton("Auto-focus")
        self._af_button.setToolTip(
            "Continuous autofocus servo on the active camera (M4/M5). Tracks a "
            "high-texture patch on the leading edge and dithers the focus motor "
            "to hold sharpness as the scene moves. Runs until 'Stop AF'."
        )
        self._af_button.clicked.connect(self._on_af_clicked)
        row.addWidget(self._af_button)

        self._af_all_button = QPushButton("Auto-focus all")
        self._af_all_button.setToolTip(
            "Focus cam1, cam2 and cam3 once each, in sequence (relay-selected). "
            "Each camera servos until it first locks, then advances."
        )
        self._af_all_button.clicked.connect(self._on_af_all_clicked)
        row.addWidget(self._af_all_button)

        self._af_stop_button = QPushButton("Stop AF")
        self._af_stop_button.setEnabled(False)
        self._af_stop_button.clicked.connect(self._on_af_stop_clicked)
        row.addWidget(self._af_stop_button)

        self._field_map_button = QPushButton("Field map")
        self._field_map_button.setToolTip(
            "Measure how best-focus varies ACROSS the frame. One motor sweep scores "
            "five fixed tiles (centre + the four mid-edges) on every frame, then "
            "reports each tile's focus vertex in encoder counts and their spread -- "
            "screen tilt / field curvature. FREEZE THE SCENE FIRST: the tiles are "
            "pinned to sensor pixels, so the content under them must hold still. "
            "Stop AF aborts it."
        )
        self._field_map_button.clicked.connect(self.field_map_requested.emit)
        row.addWidget(self._field_map_button)

        self._align_button = QPushButton("Align")
        self._align_button.setCheckable(True)
        self._align_button.setToolTip(
            "Null the mechanical tilt between the camera and the screen, which is "
            "what puts the top and bottom of one frame at different focus (the field "
            "map measured 641 counts of ramp against a ~1460c depth of field).\n\n"
            "Press A in OrthoSim to put up the alignment pattern first, then toggle "
            "this: twice a second the camera measures the panel's orientation and "
            "OrthoSim shows it as a bubble level plus degrees, so you can watch the "
            "screen you are aiming at while you adjust the platform. Park near focus "
            "first -- a blurred pattern loses corners, and the corner count beside the "
            "angles tells you if it has. Moves no motor; the sweeps stay locked out "
            "while it runs because the pattern replaces the ortho scene."
        )
        self._align_button.toggled.connect(self.align_toggled.emit)
        row.addWidget(self._align_button)

        self._observe_button = QPushButton("Observe")
        self._observe_button.setToolTip(
            "Diagnostic, NOT an autofocus. Holds the lens still and scores several "
            "tracked patches on every frame, logging each one to CSV. Answers why a "
            "newly acquired patch scores ~1.6x an older one at the same focus, which "
            "is what makes cross-object stitching bias the climb curve.\n\n"
            "LET THE SCENE SCROLL -- the patches must travel across the frame. Run it "
            "in both directions (OrthoSim --dir down) to separate a patch's age from "
            "its height in the frame; forward-only, the two cannot be told apart.\n\n"
            "Returns the lens to where it started. Stop AF ends it early."
        )
        self._observe_button.clicked.connect(self.observe_requested.emit)
        row.addWidget(self._observe_button)

        self._lock_tile_chk = QCheckBox("Lock tile")
        self._lock_tile_chk.setToolTip(
            "Freeze the tracked Fine tile so it stops re-picking. Sensor noise on a "
            "static scene makes the auto-pick hop between tiles; lock it once it's on "
            "good texture to score that fixed patch as you creep focus (manual "
            "backlash). Off = normal leading-edge tracking."
        )
        self._lock_tile_chk.toggled.connect(self.fine_lock_changed.emit)
        row.addWidget(self._lock_tile_chk)

        self._af_zoom_button = QPushButton("Zoom tile")
        self._af_zoom_button.setCheckable(True)
        self._af_zoom_button.setToolTip(
            "Show just the autofocus tile (green box), zoomed to fill the preview, "
            "so you can watch focus sharpen on the patch AF is optimising. Off = "
            "the whole frame with the tile outlined."
        )
        self._af_zoom_button.toggled.connect(self._on_af_zoom_toggled)
        row.addWidget(self._af_zoom_button)

        row.addWidget(QLabel("AF:"))
        self._af_algo_combo = QComboBox()
        # userData is what AutoFocusController.set_algorithm consumes.
        # First entry must match the controller's default (app.py: IRON661_AF default).
        for label, data in (
            ("w10 (PSF)", "w10"),
            ("Sweep-and-hold", "sweep_and_hold"),
            ("Legacy dither", "legacy"),
        ):
            self._af_algo_combo.addItem(label, data)
        self._af_algo_combo.setToolTip(
            "Autofocus algorithm (A/B test).\n"
            "w10 (PSF): score every frame's natural step edges (PSF width, content-"
            "independent), sweep through focus, fit the V, park on its vertex.\n"
            "Legacy dither: the continuous +-d wobble servo.\n"
            "Sweep-and-hold: sweep the lens while tracking objects, stitch a global "
            "focus curve, drive to the peak and hold (in-flight one-shot acquisition)."
        )
        self._af_algo_combo.currentIndexChanged.connect(self._on_af_algo_changed)
        row.addWidget(self._af_algo_combo)

        self._roi_graph_chk = QCheckBox("ROIs")
        self._roi_graph_chk.setToolTip(
            "Score-vs-encoder graph: plot the w10 AF's five ROIs separately (TL red, TR "
            "yellow, C green, BL blue, BR purple) instead of the pooled frame w10. Five "
            "bowls at one position = one target plane; a ROI with its own bottom is at "
            "another distance (jig structure, field tilt). Toggle any time, also mid-run."
        )
        self._roi_graph_chk.toggled.connect(self._scatter_graph.set_per_roi)
        row.addWidget(self._roi_graph_chk)

        self._hist_chk = QCheckBox("Hist")
        self._hist_chk.setChecked(True)
        self._hist_chk.setToolTip(
            "Top-left histogram of the coarse frame's RAW 14-bit counts (green phase): "
            "how much of the sensor range the recording uses. Display gain has no effect "
            "on it. Dashed lines p0.1 / p50 / p99.9; red bar = clipped pixels; 'head' = "
            "stops between p99.9 and full scale, 'used' = stops between p0.1 and p99.9."
        )
        self._hist_chk.toggled.connect(self._hist_graph.setVisible)
        row.addWidget(self._hist_chk)

        row.addWidget(QLabel("Lead:"))
        self._af_lead_combo = QComboBox()
        # userData is what the AF engine consumes: None = auto (motion), a
        # top/bottom/left/right edge = manual leading edge, "static" = no motion
        # so search the whole frame for the sharpest tile (bench / hover).
        for label, data in (
            ("Auto", None),
            ("Up", "top"),
            ("Down", "bottom"),
            ("Left", "left"),
            ("Right", "right"),
            ("Static", "static"),
        ):
            self._af_lead_combo.addItem(label, data)
        self._af_lead_combo.setToolTip(
            "Leading edge for target selection. Auto = pick it from image motion. "
            "Up/Down/Left/Right = the edge where fresh ground enters (target picked "
            "there dwells longest). Static = no motion, search the whole frame for "
            "the sharpest tile."
        )
        row.addWidget(self._af_lead_combo)

        row.addSpacing(12)
        row.addWidget(QLabel("M5 positions:"))
        self._m5_positions_spin = QSpinBox()
        self._m5_positions_spin.setRange(2, 64)
        self._m5_positions_spin.setValue(40)
        self._m5_positions_spin.setToolTip(
            "Ground Test (M5): number of stepped-sweep positions per cycle. More "
            "positions = the target crosses the FOV more gradually. Sends $P<n>*."
        )
        self._m5_positions_spin.editingFinished.connect(self._on_m5_positions_changed)
        row.addWidget(self._m5_positions_spin)

        row.addSpacing(8)
        row.addWidget(QLabel("M5 sweep\u00b0:"))
        self._m5_sweep_spin = QDoubleSpinBox()
        self._m5_sweep_spin.setRange(1.0, 60.0)
        self._m5_sweep_spin.setDecimals(1)
        self._m5_sweep_spin.setSingleStep(1.0)
        self._m5_sweep_spin.setValue(10.0)
        self._m5_sweep_spin.setToolTip(
            "Ground Test (M5): total mirror sweep angle in degrees. A NARROW angle "
            "plus many positions gives a small per-step angle so consecutive frames "
            "overlap and the patch-tracker can follow the panning scene (60\u00b0 jumps "
            "~1.5\u00b0/frame and tracking fails). Flight sweeps keep 60\u00b0. Sends $W<n>*."
        )
        self._m5_sweep_spin.editingFinished.connect(self._on_m5_sweep_angle_changed)
        row.addWidget(self._m5_sweep_spin)

        row.addStretch(1)

        self._enc_label = QLabel("enc: --")
        self._enc_label.setStyleSheet(
            "font-family:Consolas, monospace; color:#9cf; font-size:11px;"
        )
        self._enc_label.setToolTip(
            "Live AMT203S motor-shaft encoder count (accumulated across wraps). "
            "'--' = no reading yet; 'absent' = the encoder reported $ENC=-1."
        )
        self._enc_label.setMinimumWidth(120)
        row.addWidget(self._enc_label)

        self._af_status_label = QLabel("autofocus: idle")
        self._af_status_label.setStyleSheet(
            "font-family:Consolas, monospace; color:#9c9; font-size:11px;"
        )
        self._af_status_label.setMinimumWidth(260)
        self._af_status_text = "autofocus: idle"
        self._af_score_txt = ""
        row.addWidget(self._af_status_label)
        return row

    def _on_af_clicked(self) -> None:
        self.autofocus_requested.emit()

    def _on_af_all_clicked(self) -> None:
        self.autofocus_all_requested.emit()

    def _on_af_stop_clicked(self) -> None:
        self.autofocus_stop_requested.emit()

    def _on_af_algo_changed(self, _idx: int) -> None:
        data = self._af_algo_combo.currentData()
        if data:
            self.autofocus_algorithm_changed.emit(str(data))

    def _on_af_zoom_toggled(self, on: bool) -> None:
        self._af_zoom = bool(on)
        if not self._fine_toggle.isChecked() and self._last_coarse_raw is not None:
            self._refresh_coarse_preview()

    def _on_m5_positions_changed(self) -> None:
        n = self._m5_positions_spin.value()
        try:
            self._serial.set_m5_positions(n)
            self.diag_log.emit(f"M5 positions -> {n} ($P{n}*)")
        except Exception as exc:
            self.diag_log.emit(f"set M5 positions failed: {exc}")

    def _on_m5_sweep_angle_changed(self) -> None:
        deg = self._m5_sweep_spin.value()
        try:
            self._serial.set_m5_sweep_angle(deg)
            self.diag_log.emit(f"M5 sweep angle -> {deg:g}\u00b0 ($W{deg:g}*)")
        except Exception as exc:
            self.diag_log.emit(f"set M5 sweep angle failed: {exc}")

    def af_lead_hint(self) -> Optional[str]:
        """Selected leading edge: None (auto), 'top'/'bottom'/'left'/'right', or
        'static' (no motion -> full-frame sharpest-tile search)."""
        return self._af_lead_combo.currentData()

    def set_autofocus_running(self, running: bool) -> None:
        """Lock start buttons / unlock Stop while an AF run (or a field-map sweep --
        both drive the same motor, so neither may start on top of the other) is in
        progress."""
        self._af_button.setEnabled(not running)
        self._af_all_button.setEnabled(not running)
        self._field_map_button.setEnabled(not running)
        self._observe_button.setEnabled(not running)
        # Align moves no motor, but it needs its pattern on the screen, so the ortho
        # scene the other three score is gone while it is on -- pointless at best.
        self._align_button.setEnabled(not running)
        self._af_lead_combo.setEnabled(not running)
        self._af_algo_combo.setEnabled(not running)
        # Lock the Coarse/Fine toggle too: a run forces the Coarse full-res tap it
        # depends on, and flipping to Fine mid-run starves the AF engine's frame
        # feed (the tap stops shipping the coarse/full-res crop it advances on).
        self._fine_toggle.setEnabled(not running)
        self._af_stop_button.setEnabled(running)

    def set_autofocus_status(self, text: str) -> None:
        self._af_status_text = text
        # A fresh run (or idle) drops the stale live score from the prior run.
        if "acquiring" in text or "idle" in text:
            self._af_score_txt = ""
        self._render_af_status()

    def _render_af_status(self) -> None:
        self._af_status_label.setText(self._af_status_text + self._af_score_txt)

    def set_encoder_text(self, text: str) -> None:
        """Update the live encoder-count readout (MainWindow polls this on a timer)."""
        self._enc_label.setText(text)

    def reset_lock_tile(self) -> None:
        """Uncheck 'Lock tile' without emitting (e.g. when leaving Fine view)."""
        self._lock_tile_chk.blockSignals(True)
        self._lock_tile_chk.setChecked(False)
        self._lock_tile_chk.blockSignals(False)

    def reset_align(self) -> None:
        """Pop 'Align' back out without re-emitting, for when the MainWindow refuses
        it (no AF mode, a sweep already running) -- a button left down while nothing
        measures is how an operator ends up staring at a frozen bubble."""
        self._align_button.blockSignals(True)
        self._align_button.setChecked(False)
        self._align_button.blockSignals(False)

    def reset_lockstep(self) -> None:
        """Untick 'Lockstep' without re-emitting, for when the handshake gives up.

        Same lesson as :meth:`reset_align`, learned the hard way on 2026-09-07: the
        loop degraded to PCB triggering after one missing frame, the box stayed ticked,
        and two full observe runs were then recorded open loop by an operator who had
        every reason to believe the rig was in lockstep. A control that misreports the
        state of the hardware is worse than no control.
        """
        self._lockstep_toggle.blockSignals(True)
        self._lockstep_toggle.setChecked(False)
        self._lockstep_toggle.blockSignals(False)

    def _build_focus_step_box(self) -> QWidget:
        box = QGroupBox("Focus")
        layout = QHBoxLayout(box)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)
        self._focus_count = QSpinBox()
        self._focus_count.setRange(1, 50)
        self._focus_count.setValue(1)
        self._focus_count.setMaximumWidth(60)
        self._focus_count.setSuffix(" steps")
        layout.addWidget(self._focus_count)
        for sym, label in (("-", "  −  "), ("+", "  +  ")):
            btn = QPushButton(label)
            btn.clicked.connect(
                lambda _ck, s=sym: self._serial.focus_step(s, self._focus_count.value())
            )
            layout.addWidget(btn)
        return box

    def _build_period_box(self) -> QWidget:
        box = QGroupBox("Trigger period")
        layout = QHBoxLayout(box)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)
        for sym, label in (("<", "  <  "), (">", "  >  ")):
            btn = QPushButton(label)
            btn.clicked.connect(
                lambda _ck, s=sym: self._serial.adjust_trigger_period(s)
            )
            layout.addWidget(btn)
        return box

    # ── public hooks ────────────────────────────────────────────────────

    def set_active_pipeline(self, pipeline: Optional[CameraPipeline]) -> None:
        """Tell the panel which camera's focus crops to write.

        Pass ``None`` when leaving M4 or when no preview is selected; the
        panel will still display incoming frames for whichever pipeline is
        wired to ``receive_*``, but won't submit save jobs.
        """
        self._active_pipeline = pipeline

    def _paint_fine_tile_to_display(self, rgb: np.ndarray, meta: dict) -> None:
        """Paint the tile at EXACTLY ``_fine_zoom()`` screen px per sensor px.

        This must never scale below 1:1. It used to fill the label with
        ``KeepAspectRatioByExpanding``, which was an exact 1.5x upscale back when
        the tile was sized from the display constants (853x480 into a 1280x720
        pane). Once the leading-edge tracker began pinning the ROI, the tile
        arrived at 2680x1904 (a coarse grid tile at ``tiles_per_frame=5``, scaled
        by ``SUBSAMPLE_FACTOR``) and the same call silently became a ~0.5x
        DOWNSCALE, averaging sensor pixels together. That destroys precisely the
        high spatial frequencies focus is judged on, so a well-focused lens
        looked soft -- the view lied about the thing it exists to show.

        So: crop to the sensor pixels that fit and show those, rather than
        squeezing the whole tile in. Oversized tiles lose their margins, not
        their detail.
        """
        rgb = np.ascontiguousarray(rgb)
        if self._clahe_chk.isChecked():
            rgb = _apply_clahe_display_rgb_u8(rgb)
        zoom = self._fine_zoom()
        tw = max(1, self._display.width())
        th = max(1, self._display.height())
        h, w = rgb.shape[:2]
        # Sensor pixels that fit the pane at this zoom, taken about the tile
        # centre (which is what the tracker is holding on the object).
        x0, y0, cw, ch = _fine_view_crop(w, h, tw, th, zoom)
        crop = np.ascontiguousarray(rgb[y0 : y0 + ch, x0 : x0 + cw])
        qimg = QImage(
            crop.data, cw, ch, cw * 3, QImage.Format.Format_RGB888
        ).copy()
        scaled = QPixmap.fromImage(qimg)
        if zoom > 1.0:
            # An integer zoom replicates whole pixels, so nearest neighbour is
            # exact and invents nothing -- smooth would soften the very pixel
            # edges being judged and hand back a blur that reads as defocus.
            # A fractional zoom cannot replicate evenly (1.5x would double every
            # other pixel and leave visible beat patterns), so it interpolates;
            # that is still an upscale, so no captured detail is lost.
            exact = float(zoom).is_integer()
            scaled = scaled.scaled(
                round(cw * zoom),
                round(ch * zoom),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation
                if exact
                else Qt.TransformationMode.SmoothTransformation,
            )
        ov = meta.get("overview_rgb")
        if (
            ov is not None
            and isinstance(ov, np.ndarray)
            and ov.ndim == 3
            and ov.shape[2] == 3
            and ov.dtype == np.uint8
        ):
            scaled = _composite_fine_overview_inset(scaled, ov)
        self._display.setPixmap(scaled)

    def af_graph_add_sample(self, counts: int, score: float, fine: bool = False) -> None:
        """Append one AF measurement (encoder counts, score, fine_metric) to the
        preview overlay graph. ``fine`` selects the amber (full-res) trace over
        the green (coarse) one so the two metrics scale independently."""
        self._af_graph.add_sample(counts, score, fine)
        # Live score in the status line: coarse and fine live on different scales,
        # so label which one this is (and its encoder position).
        kind = "fine" if fine else "coarse"
        prec = 2 if fine else 0
        self._af_score_txt = f"  {kind}={score:.{prec}f}@{counts:+d}c"
        self._render_af_status()

    def af_graph_clear(self) -> None:
        """Reset the AF telemetry graph (called when a new run starts)."""
        self._af_graph.clear()

    def focus_scatter_add_point(self, encoder: float, score: float, roi: Optional[int] = None) -> None:
        """Append one (encoder, score) dot to the score-vs-encoder scatter
        (top-right, always on -- fed by both the manual creep and AF runs).
        ``roi`` 0..4 tags a w10 AF per-ROI width (drawn when 'ROIs' is ticked)."""
        self._scatter_graph.add_point(encoder, score, roi)

    def focus_scatter_clear(self) -> None:
        """Reset the manual-backlash scatter."""
        self._scatter_graph.clear()

    def field_profile_update(self, row: dict) -> None:
        """Show AF3's in-progress station: score vs position across the frame."""
        self._profile_graph.update_row(row)
        self._preview_stack.reposition_overlays()

    def field_profile_clear(self) -> None:
        """Hide the station view (end of an AF3 run, or a non-AF3 run starting)."""
        self._profile_graph.clear()

    def set_af_roi_overlay(self, roi: Optional[tuple[int, int, int, int]]) -> None:
        """Set (or clear) the autofocus winning-tile rectangle to overlay on the
        coarse preview. ``roi`` is in coarse-frame pixel coords; None clears it."""
        self._af_roi = tuple(roi) if roi is not None else None
        if self._af_roi is None:
            self._af_fullres_crop = None  # drop stale tile once AF releases the ROI
        if not self._fine_toggle.isChecked() and self._last_coarse_raw is not None:
            self._refresh_coarse_preview()

    def _show_coarse_preview(self, raw_u16: np.ndarray) -> None:
        dw = max(64, self._display.width())
        dh = max(64, self._display.height())
        if self._af_zoom and self._af_roi is not None:
            self._show_af_tile_zoom(raw_u16)
            return
        gray8 = _downsample_bayer_to_gray8(raw_u16, max(dw, dh), self._display_gain)
        gray8 = np.ascontiguousarray(gray8)
        gh, gw = gray8.shape
        qimg = QImage(
            gray8.data, gw, gh, gw, QImage.Format.Format_Grayscale8
        ).copy()
        pix = QPixmap.fromImage(qimg)
        if self._af_roi is not None or self._track_rois:
            rh, rw = raw_u16.shape[:2]
            sx, sy = gw / max(1, rw), gh / max(1, rh)
            painter = QPainter(pix)
            painter.setPen(QPen(QColor(0, 255, 0), 2))
            if self._af_roi is not None:
                x0, y0, x1, y1 = self._af_roi
                ax0, ay0 = int(x0 * sx), int(y0 * sy)
                painter.drawRect(
                    ax0, ay0,
                    max(1, int((x1 - x0) * sx)), max(1, int((y1 - y0) * sy)),
                )
                # Labelled "AF": it shares the tracking boxes' green, holds still (AF
                # pins a tile rather than following the scene) and survives an AF stop,
                # so unlabelled it reads as a frozen tracked object.
                painter.drawText(ax0 + 3, ay0 + 14, "AF")
            # Two-object tracking boxes: same green as the AF tile, labelled with the
            # object id so the leading-edge -> trailing-edge flow (and re-acquires,
            # where the id jumps) is visible frame to frame.
            for entry in self._track_rois:
                if entry is None:
                    continue
                oid, (x0, y0, x1, y1) = entry
                dx0, dy0 = int(x0 * sx), int(y0 * sy)
                painter.drawRect(
                    dx0, dy0,
                    max(1, int((x1 - x0) * sx)), max(1, int((y1 - y0) * sy)),
                )
                painter.drawText(dx0 + 3, dy0 + 14, f"#{oid}")
            painter.end()
        self._display.setPixmap(
            pix.scaled(
                self._display.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def set_af_fullres_crop(self, crop: Optional[np.ndarray]) -> None:
        """Cache the latest FULL-RES winning-tile crop (raw Bayer uint16) that AF
        scores, so :meth:`_show_af_tile_zoom` can display the real green plane
        instead of re-decimating the 4x preview."""
        self._af_fullres_crop = crop
        if self._af_zoom and self._af_roi is not None and crop is not None:
            self._show_af_tile_zoom(None)

    def _show_af_tile_zoom(self, raw_u16: Optional[np.ndarray]) -> None:
        """Render just the AF winning tile, scaled up to fill the display, so the
        operator can watch focus sharpen on the patch AF is optimising."""
        crop = self._af_fullres_crop
        if crop is not None and getattr(crop, "ndim", 0) == 2:
            # Show the exact green plane AF scores: crop[0::2, 1::2] is the
            # mosaic-free green sub-grid of the FULL-RES crop (half-res/axis).
            # The old path cropped the already-4x-decimated preview and then
            # decimated by a further 2x -- ~8x subsampled, hence "too pixelated".
            green = crop[0::2, 1::2]
            gray8 = np.ascontiguousarray(_u16_to_gray8(green, self._display_gain))
        else:
            # No full-res crop yet (first frames): fall back to the decimated
            # preview so the view isn't blank.
            if raw_u16 is None:
                return
            rh, rw = raw_u16.shape[:2]
            x0, y0, x1, y1 = self._af_roi
            x0 = max(0, min(int(x0), rw - 2))
            y0 = max(0, min(int(y0), rh - 2))
            x1 = max(x0 + 2, min(int(x1), rw))
            y1 = max(y0 + 2, min(int(y1), rh))
            gray8 = np.ascontiguousarray(
                _downsample_bayer_to_gray8(raw_u16[y0:y1, x0:x1], 4096, self._display_gain)
            )
        gh, gw = gray8.shape
        qimg = QImage(
            gray8.data, gw, gh, gw, QImage.Format.Format_Grayscale8
        ).copy()
        self._display.setPixmap(
            QPixmap.fromImage(qimg).scaled(
                self._display.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _refresh_coarse_preview(self) -> None:
        raw = self._last_coarse_raw
        if raw is None:
            return
        self._show_coarse_preview(raw)

    def exposure_summary(self) -> str:
        """Latest coarse-frame exposure numbers as one log line ('' before any frame)."""
        return exposure_line(self._last_exposure) if self._last_exposure else ""

    def set_display_gain(self, gain: float) -> None:
        """Preview-only brightness multiplier (shared with the main preview's spinbox);
        repaints the current Coarse view so the change shows before the next frame."""
        self._display_gain = float(gain)
        if not self._fine_toggle.isChecked():
            self._refresh_coarse_preview()

    def receive_raw_frame(self, raw_u16: np.ndarray, ts_iso: str) -> None:
        """Slot for ``AcquisitionThread.raw_focus_frame`` (M4 Coarse preview)."""
        self._last_coarse_raw = raw_u16
        self._show_coarse_preview(raw_u16)
        # ~3 ms on the 4x tap (a further 2x inside); kept current even with the
        # overlay hidden so the AF-DONE / Record-arm log line is never stale.
        self._last_exposure = exposure_stats(raw_u16)
        if self._hist_graph.isVisible():
            self._hist_graph.set_stats(self._last_exposure)
        self._tile_info_label.setText(
            "coarse: AF tile (zoomed)"
            if self._af_zoom and self._af_roi is not None
            else "coarse: live preview"
        )
        self._receive_count += 1
        if self._receive_count == 1 or self._receive_count % 16 == 0:
            shape = getattr(raw_u16, "shape", "?")
            self.diag_log.emit(
                f"focus panel: receive_raw_frame #{self._receive_count} "
                f"shape={shape} (coarse)"
            )

    def receive_fine_tile_rgb(self, rgb: np.ndarray, meta: dict) -> None:
        """Slot for ``AcquisitionThread.raw_focus_tile_rgb`` (M4 Fine)."""
        if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
            log.warning("receive_fine_tile_rgb: bad rgb %s", getattr(rgb, "shape", None))
            return
        rgb = np.ascontiguousarray(rgb)
        self._last_fine_rgb = rgb
        self._last_fine_meta = meta
        self._paint_fine_tile_to_display(rgb, meta)
        bi = int(meta.get("best_index", -1))
        bs = float(meta.get("best_score", 0.0))
        demosaic = bool(meta.get("fine_demosaic", True))
        tag = "(fine / debayered)" if demosaic else "(fine / raw Bayer)"
        # best_index == -1 means a fixed ROI is pinned (Lock tile), not an auto-pick.
        tile = "LOCKED" if bi < 0 else f"#{bi}"
        self._tile_info_label.setText(f"tile {tile}  score={bs:.1f}  {tag}")
        ts_iso = str(meta.get("ts_iso", ""))
        try:
            when = _dt.datetime.fromisoformat(ts_iso)
        except Exception:
            when = _dt.datetime.now()
        if self._save_toggle.isChecked() and self._active_pipeline is not None:
            crop = meta.get("uint16_crop")
            if crop is not None and isinstance(crop, np.ndarray):
                ok = self._active_pipeline.submit_focus_capture(
                    crop,
                    when=when,
                    sweep=meta.get("sweep"),
                    frame_in_sweep=meta.get("frame_in_sweep"),
                )
                if not ok:
                    log.debug("fine save: submit_focus_capture rejected")
        self._receive_count += 1
        if self._receive_count == 1 or self._receive_count % 16 == 0:
            self.diag_log.emit(
                f"focus panel: receive_fine_tile_rgb #{self._receive_count} "
                f"shape={rgb.shape}"
            )

    def shutdown(self) -> None:
        """No background threads; hook kept for MainWindow symmetry."""

    # ── slots ──────────────────────────────────────────────────────────

    def _on_save_toggled(self, on: bool) -> None:
        self.save_toggled.emit(on)

    def is_save_focus_enabled(self) -> bool:
        """Whether the 'Save focus view' toggle is checked (used to also dump every
        autofocus winning-tile crop while an AF run is in progress)."""
        return self._save_toggle.isChecked()

    def set_save_focus_enabled(self, on: bool) -> None:
        """Tick/untick 'Save focus view' as if the operator had clicked it.

        Signals are NOT blocked: arming M4 Record flips this on, and any
        listener on ``save_toggled`` should see that exactly as it would a
        manual click.
        """
        if self._save_toggle.isChecked() != bool(on):
            self._save_toggle.setChecked(bool(on))

    # ── geometry ────────────────────────────────────────────────────────

    def resizeEvent(self, event) -> None:  # noqa: N802 -- Qt naming
        super().resizeEvent(event)
        if not self._fine_toggle.isChecked():
            if self._last_coarse_raw is not None:
                self._refresh_coarse_preview()
        elif self._last_fine_rgb is not None and self._last_fine_meta is not None:
            self._paint_fine_tile_to_display(self._last_fine_rgb, self._last_fine_meta)
