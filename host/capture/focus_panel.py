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
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
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

    def minimumSizeHint(self):  # noqa: N802 -- Qt naming
        return QSize(0, 0)

    def sizeHint(self):  # noqa: N802 -- Qt naming
        return QSize(280, 200)


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
        self._cam_device_ids: dict[int, str] = {}

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
                "Currently Coarse view. Click for full-res best tile "
                "(~150% zoom; uses more bandwidth). Use Debayer for colour."
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
        outer.addWidget(self._display, 1)

        outer.addLayout(self._build_bottom_control_row())
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
        rb = self._cam_radios.get(int(camera_number))
        if rb is not None:
            rb.setEnabled(bool(available))

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
        for level, k in ((1, "7k"), (2, "10k"), (3, "14k")):
            rb = QRadioButton(str(level))
            rb.setToolTip(f"Strength {level} ({k})")
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

    def _show_coarse_preview(self, raw_u16: np.ndarray) -> None:
        dw = max(64, self._display.width())
        dh = max(64, self._display.height())
        gray8 = _downsample_bayer_to_gray8(raw_u16, max(dw, dh))
        gray8 = np.ascontiguousarray(gray8)
        gh, gw = gray8.shape
        qimg = QImage(
            gray8.data, gw, gh, gw, QImage.Format.Format_Grayscale8
        ).copy()
        pix = QPixmap.fromImage(qimg)
        self._display.setPixmap(
            pix.scaled(
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
        self._tile_info_label.setText("coarse: live preview")
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
        self._tile_info_label.setText(f"tile #{bi}  score={bs:.1f}  {tag}")
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

    # ── geometry ────────────────────────────────────────────────────────

    def resizeEvent(self, event) -> None:  # noqa: N802 -- Qt naming
        super().resizeEvent(event)
        if not self._fine_toggle.isChecked():
            if self._last_coarse_raw is not None:
                self._refresh_coarse_preview()
        elif self._last_fine_rgb is not None and self._last_fine_meta is not None:
            self._paint_fine_tile_to_display(self._last_fine_rgb, self._last_fine_meta)
