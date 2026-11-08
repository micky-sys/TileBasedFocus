"""Focus panel for M4 / M5.

Shows the decimated uint16 frames from the acquisition tap as a live Bayer
grayscale preview, with the w10 autofocus controls, the score-vs-encoder
scatter, the AF telemetry trace and the exposure histogram as overlays.

Fine view (toggle): the full-resolution crop of the patch
:class:`~capture.fine_tracker.FineFocusTracker` is following, drawn at an exact
integer number of screen pixels per sensor pixel so nothing is ever averaged
away, with a coarse overview inset showing where on the frame it sits. This is
the manual-focus view -- at 1:1 the operator can see the focus change that the
decimated preview hides.

Arrow keys drive focus steps and trigger period via serial (the on-screen
+/− and </> group boxes are hidden; the step-count spinbox remains for
hotkey strength).

The mode strip and the exposure / analog-gain / display-gain bar are
MainWindow-owned widgets re-homed into this panel while M4 is active.
"""

from __future__ import annotations

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
    QVBoxLayout,
    QWidget,
)

from .bayer_demosaic import opencv_bayer_rgb_demosaic_code
from .pipeline import (
    SENSOR_FULL_SCALE, _downsample_bayer_to_gray8, _u16_to_gray8, exposure_line,
    exposure_stats,
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


def _fine_view_crop(w: int, h: int, tw: int, th: int, zoom: float) -> tuple[int, int, int, int]:
    """Centre crop ``(x0, y0, cw, ch)`` of a *w* x *h* tile to show in a *tw* x *th*
    pane at *zoom* screen px per sensor px.

    Rounds up, so the magnified crop covers the pane rather than leaving bars:
    ``cw * zoom < tw + zoom``. Any overshoot is under one magnified pixel and the label
    clips it (it never rescales -- ``setScaledContents(False)``). What it never does is
    return a crop needing a DOWNSCALE; see :meth:`FocusPanel._paint_fine_tile` for why
    that matters.
    """
    zoom = max(1.0, float(zoom))
    cw = min(int(w), max(2, int(ceil(int(tw) / zoom))))
    ch = min(int(h), max(2, int(ceil(int(th) / zoom))))
    return (int(w) - cw) // 2, (int(h) - ch) // 2, cw, ch


class _FocusTileDisplayLabel(QLabel):
    """Avoid pixmap-driven minimumSizeHint feedback on stretched focus previews."""

    def minimumSizeHint(self):  # noqa: N802 -- Qt naming
        return QSize(0, 0)

    def sizeHint(self):  # noqa: N802 -- Qt naming
        return QSize(280, 200)


class _AFTelemetryGraph(QWidget):
    """Tiny live overlay plotting the autofocus measurement stream: X = sample
    index, Y = two separately auto-scaled traces sharing that x-axis.

    * pooled w10 (amber) -- the PSF width of each gated frame, px,
    * lens position in ENCODER COUNTS (cyan) -- the read-back motor-shaft count,
      not commanded pulses, so it reflects where the lens actually is."""

    _MAX = 200  # samples kept on screen

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._scores: list[float] = []
        self._counts: list[int] = []
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedSize(230, 130)
        self.hide()

    def clear(self) -> None:
        self._scores.clear()
        self._counts.clear()
        self.update()

    def add_sample(self, counts: int, score: float) -> None:
        self._counts.append(int(counts))
        self._scores.append(float(score))
        if len(self._scores) > self._MAX:
            del self._scores[:-self._MAX]
            del self._counts[:-self._MAX]
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
        amber = QColor(255, 170, 60)
        cyan = QColor(90, 200, 255)
        p.setPen(amber)
        p.drawText(5, 11, "w10 px")
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

        def _draw(vals, color):
            """Auto-scaled over its own values, on the shared sample-index x-axis."""
            lo, hi = min(vals), max(vals)
            rng = (hi - lo) or 1.0
            p.setPen(QPen(color, 1.5))
            px = py = None
            for idx, v in enumerate(vals):
                x = pad_l + plot_w * idx / (n - 1)
                y = pad_t + plot_h * (1.0 - (v - lo) / rng)
                if px is not None:
                    p.drawLine(int(px), int(py), int(x), int(y))
                px, py = x, y

        _draw(self._counts, cyan)
        _draw(self._scores, amber)
        p.setPen(amber)
        p.drawText(5, h - 3, f"{self._scores[-1]:.2f}")
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


class _OverlayHost(QWidget):
    """Holds the focus display and keeps the three graphs floating over it: exposure
    histogram top-left, score-vs-encoder scatter top-right, AF telemetry bottom-right."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.overlay: Optional[QWidget] = None          # bottom-right AF telemetry
        self.scatter_overlay: Optional[QWidget] = None  # top-right score-vs-enc scatter
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


class FocusPanel(QWidget):
    """M4 focus UI: coarse live preview, w10 autofocus controls, graph overlays."""

    # Emitted when the operator clicks one of the in-panel camera radios
    # (1, 2 or 3). The MainWindow listens for this and routes frames /
    # focus tap / ``$Cn*`` PCB selection to the chosen camera. Decoupled
    # via a signal so the panel itself stays ignorant of the pipelines
    # and rows it ultimately drives.
    camera_chosen = Signal(int)

    # Autofocus controls. The MainWindow owns the AutoFocusController; the panel
    # just emits intent and reflects run state/status.
    autofocus_requested = Signal()       # one w10 run on the active camera
    autofocus_all_requested = Signal()   # focus cam1..3 once, in sequence
    autofocus_stop_requested = Signal()
    m4_record_toggled = Signal(bool)     # arm/disarm .bay14line writing while in M4

    # Fine view: full-res tracked tile instead of the coarse whole-frame preview.
    fine_view_toggled = Signal(bool)
    fine_lock_changed = Signal(bool)     # freeze the tracked tile on the sensor

    # Emitted at low cadence whenever the panel receives a coarse frame. Used by
    # the MainWindow to surface the M4 focus-tap chain.
    diag_log = Signal(str)

    def __init__(self, serial: SerialClient, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._serial = serial
        self._receive_count = 0
        self._last_coarse_raw: Optional[np.ndarray] = None
        # The main window's "Display gain" (preview-only brightness), applied to the
        # coarse preview this panel renders itself.
        self._display_gain = 1.0
        self._cam_device_ids: dict[int, str] = {}
        self._cam_available: dict[int, bool] = {}
        self._controls_locked = False
        # Fine view state: the latest full-res crop, and where on the COARSE frame the
        # tracker says it came from (for the overview inset).
        self._last_fine_crop: Optional[np.ndarray] = None
        self._fine_roi_coarse: Optional[tuple] = None

        self._build_ui()
        self._install_m4_arrow_hotkeys()

    def sync_m4_record_toggle(self, on: bool) -> None:
        """Align the Record checkbox with :class:`ModeController` state.

        Silent: a mode change disarms recording in the controller, and the box
        has to follow without re-emitting and re-closing an already-closed line.
        """
        self._m4_record_toggle.blockSignals(True)
        self._m4_record_toggle.setChecked(bool(on))
        self._m4_record_toggle.blockSignals(False)

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
            QSpinBox, QLineEdit, QDoubleSpinBox {
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

        # Host for the MainWindow's exposure / analog gain / display gain bar while
        # M4 is active (same re-homing pattern as the mode strip above).
        self._sensor_bar_host = QWidget()
        self._sensor_bar_layout = QHBoxLayout(self._sensor_bar_host)
        self._sensor_bar_layout.setContentsMargins(0, 0, 0, 0)
        self._sensor_bar_layout.setSpacing(4)
        self._sensor_bar_host.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
        )
        top_row.addWidget(self._sensor_bar_host, 0)

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

        # The graphs are parented on the HOST (not the display) so they float over it.
        self._preview_host = _OverlayHost()
        self._preview_host.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        host_lay = QVBoxLayout(self._preview_host)
        host_lay.setContentsMargins(0, 0, 0, 0)
        host_lay.addWidget(self._display)

        self._af_graph = _AFTelemetryGraph(self._preview_host)
        self._preview_host.overlay = self._af_graph
        # Always-on encoder-vs-score scatter, top-right (bottom-right is the AF
        # telemetry graph, top-left the exposure histogram).
        self._scatter_graph = _FocusScatterGraph(self._preview_host)
        self._preview_host.scatter_overlay = self._scatter_graph
        self._scatter_graph.show()
        # Exposure histogram of the raw coarse frame, top-left; `Hist` checkbox.
        self._hist_graph = _ExposureHistGraph(self._preview_host)
        self._preview_host.hist_overlay = self._hist_graph
        self._hist_graph.show()
        self._last_exposure: Optional[dict] = None
        self._preview_host.reposition_overlays()
        outer.addWidget(self._preview_host, 1)

        outer.addLayout(self._build_bottom_control_row())
        outer.addLayout(self._build_autofocus_row())
        self._focus_step_box.hide()
        self._period_box.hide()

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

    def sensor_bar_layout(self) -> QHBoxLayout:
        """Host layout for the exposure / gain / display-gain bar while M4 is active
        (MainWindow-owned widget, re-homed exactly like the mode strip)."""
        return self._sensor_bar_layout

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
        """Single compact row: strength, record, active camera."""
        row = QHBoxLayout()
        row.setSpacing(6)
        row.setContentsMargins(0, 2, 0, 2)

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
                lambda on, lv=level: on and self._apply_strength(lv)
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

        row.addSpacing(12)
        self._fine_toggle = QPushButton("Fine view")
        self._fine_toggle.setCheckable(True)
        self._fine_toggle.setToolTip(
            "Show the full-resolution crop of one tracked patch instead of the coarse\n"
            "whole-frame preview. The tracker picks the most focusable tile entering the\n"
            "leading edge and follows it until it pans off, so a manual focus adjustment\n"
            "stays on the same subject. The decimated preview averages 4x4 sensor pixels\n"
            "together and cannot show the detail focus is judged on; this can."
        )
        self._fine_toggle.toggled.connect(self._on_fine_toggled)
        row.addWidget(self._fine_toggle)

        self._lock_tile_chk = QCheckBox("Lock tile")
        self._lock_tile_chk.setEnabled(False)
        self._lock_tile_chk.setToolTip(
            "Freeze the Fine view on the patch it is showing now, pinned in SENSOR\n"
            "coordinates. The scene then slides under it. Use it to creep focus on one\n"
            "fixed part of the frame (a bench target, a distant edge) without the\n"
            "tracker handing over to the next object mid-adjustment."
        )
        self._lock_tile_chk.toggled.connect(self.fine_lock_changed.emit)
        row.addWidget(self._lock_tile_chk)

        row.addWidget(QLabel("Zoom:"))
        self._zoom_box = QComboBox()
        self._zoom_box.addItems(["1x", "2x", "4x"])
        self._zoom_box.setEnabled(False)
        self._zoom_box.setToolTip(
            "Fine view magnification, in SCREEN pixels per SENSOR pixel. Integer only,\n"
            "and never below 1x: a view scaled below 1x averages sensor pixels together,\n"
            "which blurs away the high spatial frequencies focus is judged on and shows a\n"
            "sharp lens as soft. Raising the zoom shows less of the tracked tile, never\n"
            "more detail than the sensor captured."
        )
        self._zoom_box.currentIndexChanged.connect(self._repaint_fine_tile)
        row.addWidget(self._zoom_box)

        row.addStretch(1)

        self._dwell_label = QLabel("")
        self._dwell_label.setStyleSheet(
            "font-family:Consolas, monospace; color:#aaa; font-size:11px;"
        )
        self._dwell_label.setToolTip(
            "The tracked object in Fine view: which one (obj), which edge it entered from,\n"
            "how many frames it has been held, roughly how many are left before it exits,\n"
            "the measured scene flow in coarse px/frame, and the patch's texture score.\n"
            "WEAK means there is little detail here to judge focus by."
        )
        row.addWidget(self._dwell_label)

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
        """Autofocus controls: run / run-all / stop + graph toggles + M5 knobs + status."""
        row = QHBoxLayout()
        row.setSpacing(6)
        row.setContentsMargins(0, 2, 0, 2)

        self._af_button = QPushButton("Auto-focus")
        self._af_button.setToolTip(
            "One w10 run on the active camera (M4/M5): probe the motor polarity, sweep\n"
            "the lens scoring every frame's natural step edges (PSF width, content-\n"
            "independent), fit the V and park on its vertex. Stops by itself."
        )
        self._af_button.clicked.connect(self.autofocus_requested.emit)
        row.addWidget(self._af_button)

        self._af_all_button = QPushButton("Auto-focus all")
        self._af_all_button.setToolTip(
            "Run w10 on cam1, cam2 and cam3 once each, in sequence (relay-selected)."
        )
        self._af_all_button.clicked.connect(self.autofocus_all_requested.emit)
        row.addWidget(self._af_all_button)

        self._af_stop_button = QPushButton("Stop AF")
        self._af_stop_button.setEnabled(False)
        self._af_stop_button.clicked.connect(self.autofocus_stop_requested.emit)
        row.addWidget(self._af_stop_button)

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
            "overlap. Flight sweeps keep 60\u00b0. Sends $W<n>*."
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

    def set_autofocus_running(self, running: bool) -> None:
        """Lock start buttons / unlock Stop while an AF run is in progress."""
        self._af_button.setEnabled(not running)
        self._af_all_button.setEnabled(not running)
        self._af_stop_button.setEnabled(running)

    def set_autofocus_status(self, text: str) -> None:
        self._af_status_text = text
        # A fresh run (or idle) drops the stale live score from the prior run.
        if "acquiring" in text or "idle" in text or "probing" in text:
            self._af_score_txt = ""
        self._render_af_status()

    def _render_af_status(self) -> None:
        self._af_status_label.setText(self._af_status_text + self._af_score_txt)

    def set_encoder_text(self, text: str) -> None:
        """Update the live encoder-count readout (MainWindow polls this on a timer)."""
        self._enc_label.setText(text)

    # Longest manual '+'/'-' line per strength. One line is one burst, and a burst must stay
    # well under half a motor revolution (2048 counts) or the host's 12-bit encoder unwrap
    # loses a whole turn (2026-11-02 flight: cam3's count slipped by 4096 between runs after
    # multi-pulse S3 clicks; the stall points and vertices all agree modulo 4096). Sized for
    # <=~700 counts per line from the measured steps: str0 ~25 c, str3 ~850 c. One S3 pulse is
    # already past 700 and two (1700 c) only unwrap if no $ENC read is lost, so S3 is ONE pulse
    # per click: slower, never wrong. str2 is uncalibrated (somewhere between str1 and str3) and
    # gets the same. ponytail: str1 ~80 c is scaled from the duty ratio.
    _MANUAL_MAX_PULSES = {0: 25, 1: 5, 2: 1, 3: 1}

    def resend_strength(self) -> None:
        """Re-assert the radio's strength on the PCB. For callers that drove the motors at
        another ``$Sn*`` (preflight uses S3): the spinbox cap is sized for the radio's
        strength, so a mismatch would let one manual click send 25 S3 pulses."""
        self._apply_strength(self._strength_group.checkedId())

    def _apply_strength(self, level: int) -> None:
        self._serial.set_strength(level)
        count = getattr(self, "_focus_count", None)
        if count is not None:
            count.setMaximum(self._MANUAL_MAX_PULSES.get(int(level), 2))

    def _build_focus_step_box(self) -> QWidget:
        box = QGroupBox("Focus")
        layout = QHBoxLayout(box)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)
        self._focus_count = QSpinBox()
        self._focus_count.setRange(1, self._MANUAL_MAX_PULSES.get(self._strength_group.checkedId(), 2))
        self._focus_count.setValue(1)
        self._focus_count.setMaximumWidth(60)
        self._focus_count.setSuffix(" steps")
        self._focus_count.setToolTip(
            "Pulses per click. The maximum follows the strength: a longer line spins the\n"
            "motor past half a revolution between encoder reports and the position count\n"
            "silently loses a whole turn (4096 counts)."
        )
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

    def af_graph_add_sample(self, counts: int, score: float) -> None:
        """Append one AF measurement (encoder counts, pooled w10) to the telemetry
        overlay and show it live in the status line."""
        self._af_graph.add_sample(counts, score)
        self._af_score_txt = f"  w10={score:.2f}@{counts:+d}c"
        self._render_af_status()

    def set_live_w10(self, counts: int, w10: Optional[float], n_edges: int) -> None:
        """The w10 of the latest frame while NO AF run is active (manual focusing): same
        place on the status line as the AF's live score. ``None`` = too few edges to trust."""
        self._af_score_txt = (f"  live w10={w10:.2f}@{counts:+d}c ({n_edges} edges)" if w10 is not None
                              else f"  live w10=--@{counts:+d}c ({n_edges} edges, too few)")
        self._render_af_status()

    def af_graph_clear(self) -> None:
        """Reset the AF telemetry graph (called when a new run starts)."""
        self._af_graph.clear()
        self._af_score_txt = ""   # the last manual live w10 is not this run's
        self._render_af_status()

    def focus_scatter_add_point(self, encoder: float, score: float, roi: Optional[int] = None) -> None:
        """Append one (encoder, score) dot to the score-vs-encoder scatter (top-right,
        always on). ``roi`` 0..4 tags a w10 AF per-ROI width (drawn when 'ROIs' is ticked)."""
        self._scatter_graph.add_point(encoder, score, roi)

    def focus_scatter_clear(self) -> None:
        """Reset the scatter (a new AF run)."""
        self._scatter_graph.clear()

    # ── Fine view ───────────────────────────────────────────────────────

    def fine_view_enabled(self) -> bool:
        return self._fine_toggle.isChecked()

    def fine_view_sensor_wh(self) -> tuple[int, int]:
        """Sensor pixels this pane can display right now, as ``(width, height)``.

        The same ``ceil(pane / zoom)`` as :func:`_fine_view_crop`, so a tile cut to this
        size is displayed WHOLE: nothing tracked gets cropped away, and no pane pixel is
        left grey. It cannot be a constant -- the display label is Expanding/Expanding so
        it grows with the window, and the zoom is a combo box.
        """
        zoom = self._fine_zoom()
        return (max(2, ceil(self._display.width() / zoom)),
                max(2, ceil(self._display.height() / zoom)))

    def _fine_zoom(self) -> float:
        """Screen pixels per sensor pixel for the Fine view; >= 1 by construction."""
        try:
            return max(1.0, float(self._zoom_box.currentText().rstrip("xX")))
        except ValueError:
            return 1.0

    def _on_fine_toggled(self, on: bool) -> None:
        self._lock_tile_chk.setEnabled(bool(on))
        self._zoom_box.setEnabled(bool(on))
        if not on:
            if self._lock_tile_chk.isChecked():
                self._lock_tile_chk.setChecked(False)   # emits; the lock has no meaning in coarse
            self._last_fine_crop = None
            self._fine_roi_coarse = None
            self._dwell_label.setText("")
            self._refresh_coarse_preview()
        self.fine_view_toggled.emit(bool(on))

    def clear_fine_lock(self) -> None:
        """Untick 'Lock tile' without emitting (the camera or mode changed under it)."""
        self._lock_tile_chk.blockSignals(True)
        self._lock_tile_chk.setChecked(False)
        self._lock_tile_chk.blockSignals(False)

    def set_fine_track(self, roi_coarse: Optional[tuple], info: str) -> None:
        """Where the tracker has the patch on the COARSE frame, plus its dwell readout.
        The ROI drives the overview inset; it is not what gets cropped (the pipeline
        crops the full-res equivalent)."""
        self._fine_roi_coarse = tuple(roi_coarse) if roi_coarse else None
        self._dwell_label.setText(info)

    def receive_fine_crop(self, crop_u16: np.ndarray, ts_iso: str) -> None:
        """Slot for ``AcquisitionThread.raw_focus_fine_crop`` (full-res tracked patch)."""
        if crop_u16 is None or getattr(crop_u16, "ndim", 0) < 2:
            log.warning("receive_fine_crop: bad crop %s", getattr(crop_u16, "shape", None))
            return
        self._last_fine_crop = crop_u16
        self._paint_fine_tile(crop_u16)

    def _repaint_fine_tile(self) -> None:
        """Re-render the held crop (zoom changed, or the pane was resized)."""
        if self._fine_toggle.isChecked() and self._last_fine_crop is not None:
            self._paint_fine_tile(self._last_fine_crop)

    def _paint_fine_tile(self, crop_u16: np.ndarray) -> None:
        """Paint the crop at EXACTLY ``_fine_zoom()`` screen px per sensor px.

        This must never scale below 1:1. Filling the label with
        ``KeepAspectRatioByExpanding`` -- which is what the coarse preview does -- turns
        into a DOWNSCALE as soon as the tracked tile is larger than the pane, averaging
        sensor pixels together. That destroys precisely the high spatial frequencies
        focus is judged on, so a well-focused lens looks soft and the view lies about the
        one thing it exists to show. So: crop to the sensor pixels that fit and show
        those. Oversized tiles lose their margins, not their detail.
        """
        gray8 = _u16_to_gray8(crop_u16, self._display_gain)
        rgb = cv2.cvtColor(gray8, opencv_bayer_rgb_demosaic_code(
            {"genicam_pixel_format": "BayerRG14"}))
        zoom = self._fine_zoom()
        h, w = rgb.shape[:2]
        x0, y0, cw, ch = _fine_view_crop(
            w, h, max(1, self._display.width()), max(1, self._display.height()), zoom)
        view = np.ascontiguousarray(rgb[y0:y0 + ch, x0:x0 + cw])
        pm = QPixmap.fromImage(
            QImage(view.data, cw, ch, cw * 3, QImage.Format.Format_RGB888).copy())
        if zoom > 1.0:
            # An integer zoom replicates whole pixels, so nearest neighbour is exact and
            # invents nothing -- smooth would soften the very pixel edges being judged
            # and hand back a blur that reads as defocus.
            pm = pm.scaled(round(cw * zoom), round(ch * zoom),
                           Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.FastTransformation)
        self._display.setPixmap(self._with_overview_inset(pm))

    def _with_overview_inset(self, base: QPixmap) -> QPixmap:
        """Bottom-left thumbnail of the whole coarse frame with the tracked tile boxed.

        At 1:1 the Fine view shows well under 1% of the frame, so without this the
        operator cannot tell WHAT they are focusing on -- which matters because the
        tracker, not they, chose it.
        """
        coarse = self._last_coarse_raw
        if coarse is None or self._fine_roi_coarse is None:
            return base
        inset_w = max(96, int(base.width() * 0.18))
        gray = _downsample_bayer_to_gray8(coarse, inset_w, self._display_gain)
        gray = np.ascontiguousarray(cv2.resize(
            gray, (inset_w, max(2, round(inset_w * coarse.shape[0] / coarse.shape[1]))),
            interpolation=cv2.INTER_AREA))
        ih, iw = gray.shape[:2]
        pm = QPixmap.fromImage(
            QImage(gray.data, iw, ih, iw, QImage.Format.Format_Grayscale8).copy())
        sx, sy = iw / float(coarse.shape[1]), ih / float(coarse.shape[0])
        rx0, ry0, rx1, ry1 = self._fine_roi_coarse

        margin = 10
        out = QPixmap(base)
        p = QPainter(out)
        ix, iy = margin, base.height() - ih - margin
        p.drawPixmap(ix, iy, pm)
        p.setPen(QPen(QColor(220, 255, 0), 1))     # the tracked tile
        p.drawRect(int(ix + rx0 * sx), int(iy + ry0 * sy),
                   max(1, int((rx1 - rx0) * sx)), max(1, int((ry1 - ry0) * sy)))
        p.setPen(QPen(QColor(200, 200, 200), 1))
        p.drawRect(ix, iy, iw - 1, ih - 1)
        p.end()
        return out

    # ── coarse preview ──────────────────────────────────────────────────

    def _show_coarse_preview(self, raw_u16: np.ndarray) -> None:
        dw = max(64, self._display.width())
        dh = max(64, self._display.height())
        gray8 = np.ascontiguousarray(
            _downsample_bayer_to_gray8(raw_u16, max(dw, dh), self._display_gain))
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
        if raw is not None and not self._fine_toggle.isChecked():
            self._show_coarse_preview(raw)

    def exposure_summary(self) -> str:
        """Latest coarse-frame exposure numbers as one log line ('' before any frame)."""
        return exposure_line(self._last_exposure) if self._last_exposure else ""

    def set_display_gain(self, gain: float) -> None:
        """Preview-only brightness multiplier (shared with the main preview's spinbox);
        repaints the current view so the change shows before the next frame."""
        self._display_gain = float(gain)
        self._refresh_coarse_preview()
        self._repaint_fine_tile()

    def receive_raw_frame(self, raw_u16: np.ndarray, ts_iso: str) -> None:
        """Slot for ``AcquisitionThread.raw_focus_frame``.

        Held even in Fine view: it is what the tracker runs on (via the MainWindow),
        what the exposure histogram reads, and what the overview inset draws."""
        self._last_coarse_raw = raw_u16
        if not self._fine_toggle.isChecked():
            self._show_coarse_preview(raw_u16)
        # ~3 ms on the 4x tap (a further 2x inside); kept current even with the
        # overlay hidden so the AF-DONE / Record-arm log line is never stale.
        self._last_exposure = exposure_stats(raw_u16)
        if self._hist_graph.isVisible():
            self._hist_graph.set_stats(self._last_exposure)
        self._receive_count += 1
        if self._receive_count == 1 or self._receive_count % 16 == 0:
            shape = getattr(raw_u16, "shape", "?")
            self.diag_log.emit(
                f"focus panel: receive_raw_frame #{self._receive_count} "
                f"shape={shape} (coarse)"
            )

    # ── geometry ────────────────────────────────────────────────────────

    def resizeEvent(self, event) -> None:  # noqa: N802 -- Qt naming
        super().resizeEvent(event)
        self._refresh_coarse_preview()
        self._repaint_fine_tile()
