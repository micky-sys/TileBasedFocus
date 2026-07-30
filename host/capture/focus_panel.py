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
from typing import Optional

import cv2
import numpy as np
from PySide6.QtCore import QSize, Qt, Signal
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
from .pipeline import CameraPipeline, _downsample_bayer_to_gray8
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

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._enc: list[float] = []
        self._score: list[float] = []
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedSize(230, 130)
        self.hide()

    def clear(self) -> None:
        self._enc.clear()
        self._score.clear()
        self.update()

    def add_point(self, encoder: float, score: float) -> None:
        self._enc.append(float(encoder))
        self._score.append(float(score))
        if len(self._enc) > self._MAX:
            del self._enc[:-self._MAX]
            del self._score[:-self._MAX]
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
        n = len(self._enc)
        if n < 1:
            p.end()
            return
        pad_l = pad_r = 6
        pad_t, pad_b = 15, 14
        plot_w = w - pad_l - pad_r
        plot_h = h - pad_t - pad_b
        emin, emax = min(self._enc), max(self._enc)
        smin, smax = min(self._score), max(self._score)
        erng = (emax - emin) or 1.0
        srng = (smax - smin) or 1.0
        dot = QColor(120, 230, 160)
        newest = QColor(255, 170, 60)
        for i in range(n):
            x = pad_l + plot_w * (self._enc[i] - emin) / erng
            y = pad_t + plot_h * (1.0 - (self._score[i] - smin) / srng)
            p.setPen(QPen(newest if i == n - 1 else dot, 1))
            r = 2.5 if i == n - 1 else 1.5
            p.drawEllipse(int(x - r), int(y - r), int(2 * r), int(2 * r))
        # Corner labels: encoder span (X) and score at the newest dot.
        p.setPen(QColor(170, 170, 170))
        p.drawText(pad_l, h - 3, f"{int(emin)}")
        p.drawText(w - 46, h - 3, f"{int(emax)}")
        p.setPen(newest)
        p.drawText(5, h - 15, f"{self._score[-1]:.1f}")
        p.end()


class _PreviewStack(QStackedWidget):
    """Host that swaps the single focus display (page 0) for the two-object split
    preview (page 1). The score graphs are parented HERE -- not on either page --
    so they stay visible over both views (the standing "scatter always on" rule)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.overlay: Optional[QWidget] = None          # bottom-right AF telemetry
        self.scatter_overlay: Optional[QWidget] = None  # top-right score-vs-enc scatter

    def resizeEvent(self, ev):  # noqa: N802 -- Qt naming
        super().resizeEvent(ev)
        self.reposition_overlays()

    def reposition_overlays(self) -> None:
        m = 6
        ov = self.overlay
        if ov is not None:
            ov.move(max(0, self.width() - ov.width() - m),
                    max(0, self.height() - ov.height() - m))
            ov.raise_()
        sc = self.scatter_overlay
        if sc is not None:
            sc.move(max(0, self.width() - sc.width() - m), m)
            sc.raise_()


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
    autofocus_algorithm_changed = Signal(str)  # "legacy" | "sweep_and_hold"
    fine_lock_changed = Signal(bool)     # freeze the tracked Fine tile (no re-pick)
    csv_log_toggled = Signal(bool)       # start/stop focus-telemetry CSV logging
    preview_toggled = Signal(bool)       # split-screen two-object preview on/off

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
        # When True, the coarse preview shows just the AF tile, zoomed to fill,
        # instead of the whole frame with the tile outlined -- lets the operator
        # watch focus sharpen on the actual patch AF is optimising.
        self._af_zoom = False
        # Latest FULL-RES winning-tile crop (raw Bayer uint16) -- what AF actually
        # scores. The Zoom-tile view renders its green plane so the operator sees
        # the same resolution AF optimises, not the 4x preview decimated again.
        self._af_fullres_crop: Optional[np.ndarray] = None
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

        self._preview_toggle = QPushButton("2-obj preview")
        self._preview_toggle.setCheckable(True)
        self._preview_toggle.setToolTip(
            "Split the view into two panes, each following one tracked object from "
            "when it enters until it leaves the FOV (independent of autofocus)."
        )
        self._preview_toggle.toggled.connect(self._on_preview_toggle)
        top_row.addWidget(self._preview_toggle)

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
        self._preview_stack.reposition_overlays()
        outer.addWidget(self._preview_stack, 1)

        outer.addLayout(self._build_bottom_control_row())
        outer.addLayout(self._build_autofocus_row())
        self._focus_step_box.hide()
        self._period_box.hide()
        self._debayer_chk.setEnabled(self._fine_toggle.isChecked())
        self._clahe_chk.setEnabled(self._fine_toggle.isChecked())
        self._sync_fine_toggle_caption()

    def _on_debayer_toggled(self, checked: bool) -> None:
        self.fine_demosaic_changed.emit(bool(checked))

    def _on_clahe_display_toggled(self, _checked: bool) -> None:
        if (
            self._fine_toggle.isChecked()
            and self._last_fine_rgb is not None
            and self._last_fine_meta is not None
        ):
            self._paint_fine_tile_to_display(self._last_fine_rgb, self._last_fine_meta)

    def _on_fine_toggle_toggled(self, checked: bool) -> None:
        self._debayer_chk.setEnabled(bool(checked))
        self._clahe_chk.setEnabled(bool(checked))
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
        self._save_toggle = QCheckBox("Save focus view")
        self._save_toggle.setToolTip(
            "When checked in M4 Fine mode, each best-tile crop is written\n"
            "as a TIFF under <drive>:\\<project>\\focus\\."
        )
        self._save_toggle.toggled.connect(self._on_save_toggled)
        row.addWidget(self._save_toggle)

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
        for label, data in (
            ("Sweep-and-hold", "sweep_and_hold"),
            ("Legacy dither", "legacy"),
        ):
            self._af_algo_combo.addItem(label, data)
        self._af_algo_combo.setToolTip(
            "Autofocus algorithm (A/B test).\n"
            "Legacy dither: the continuous +-d wobble servo.\n"
            "Sweep-and-hold: sweep the lens while tracking objects, stitch a global "
            "focus curve, drive to the peak and hold (in-flight one-shot acquisition)."
        )
        self._af_algo_combo.currentIndexChanged.connect(self._on_af_algo_changed)
        row.addWidget(self._af_algo_combo)

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
        """Lock start buttons / unlock Stop while an AF run is in progress."""
        self._af_button.setEnabled(not running)
        self._af_all_button.setEnabled(not running)
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
        """Scale tile to fill the label (crop top/bottom if needed) — no side bars."""
        rgb = np.ascontiguousarray(rgb)
        if self._clahe_chk.isChecked():
            rgb = _apply_clahe_display_rgb_u8(rgb)
        h, w = rgb.shape[:2]
        qimg = QImage(
            rgb.data, w, h, w * 3, QImage.Format.Format_RGB888
        ).copy()
        pix = QPixmap.fromImage(qimg)
        tw = max(1, self._display.width())
        th = max(1, self._display.height())
        scaled = pix.scaled(
            tw,
            th,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled = _crop_pixmap_center(scaled, tw, th)
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

    def focus_scatter_add_point(self, encoder: float, score: float) -> None:
        """Append one (encoder, score) dot to the score-vs-encoder scatter
        (top-right, always on -- fed by both the manual creep and AF runs)."""
        self._scatter_graph.add_point(encoder, score)

    def focus_scatter_clear(self) -> None:
        """Reset the manual-backlash scatter."""
        self._scatter_graph.clear()

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
        gray8 = _downsample_bayer_to_gray8(raw_u16, max(dw, dh))
        gray8 = np.ascontiguousarray(gray8)
        gh, gw = gray8.shape
        qimg = QImage(
            gray8.data, gw, gh, gw, QImage.Format.Format_Grayscale8
        ).copy()
        pix = QPixmap.fromImage(qimg)
        if self._af_roi is not None:
            rh, rw = raw_u16.shape[:2]
            sx, sy = gw / max(1, rw), gh / max(1, rh)
            x0, y0, x1, y1 = self._af_roi
            painter = QPainter(pix)
            painter.setPen(QPen(QColor(0, 255, 0), 2))
            painter.drawRect(
                int(x0 * sx), int(y0 * sy),
                max(1, int((x1 - x0) * sx)), max(1, int((y1 - y0) * sy)),
            )
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
            gray8 = np.ascontiguousarray(np.clip(green >> 6, 0, 255).astype(np.uint8))
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
                _downsample_bayer_to_gray8(raw_u16[y0:y1, x0:x1], 4096)
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

    def receive_raw_frame(self, raw_u16: np.ndarray, ts_iso: str) -> None:
        """Slot for ``AcquisitionThread.raw_focus_frame`` (M4 Coarse preview)."""
        self._last_coarse_raw = raw_u16
        self._show_coarse_preview(raw_u16)
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
                ok = self._active_pipeline.submit_focus_capture(crop, when=when)
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

    # ── geometry ────────────────────────────────────────────────────────

    def resizeEvent(self, event) -> None:  # noqa: N802 -- Qt naming
        super().resizeEvent(event)
        if not self._fine_toggle.isChecked():
            if self._last_coarse_raw is not None:
                self._refresh_coarse_preview()
        elif self._last_fine_rgb is not None and self._last_fine_meta is not None:
            self._paint_fine_tile_to_display(self._last_fine_rgb, self._last_fine_meta)
