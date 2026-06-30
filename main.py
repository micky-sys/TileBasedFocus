"""Tile-Based Focus Tool — main application.

Run with:
    python main.py --real               # real camera (grabber 0, camera 0)
    python main.py                      # simulated (no hardware needed)
    python main.py --real --grabber 0
"""

import argparse
import sys
import logging
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal, QObject, QEvent
from PyQt6.QtGui import QImage, QPixmap, QPainter, QColor, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

import cv2
import numpy as np

from serial_comm import FocusController
from tile_geometry import TileGrid
from sharpness import find_best_tile, SharpnessResult

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


class FrameBridge(QObject):
    frame_ready = pyqtSignal(np.ndarray)
    serial_rx = pyqtSignal(str)


class MainWindow(QMainWindow):
    # Serial-number → PCB camera channel mapping
    SERIAL_TO_PCB_CAM = {
        "2439006": 1,
        "2439007": 2,
        "2439008": 3,
    }

    def __init__(self, grabber, is_real: bool) -> None:
        super().__init__()
        self.setWindowTitle("Tile-Based Focus Tool")
        self.resize(1280, 800)

        self._grabber = grabber
        self._is_real = is_real
        self._ctrl = FocusController()
        self._bridge = FrameBridge()
        self._bridge.frame_ready.connect(self._show_frame)
        self._bridge.serial_rx.connect(self._on_serial_rx)

        self._frame_count = 0
        self._pending_frame: Optional[np.ndarray] = None
        self._display_busy = False
        self._cam_buttons: list[QPushButton] = []
        self._grabber_to_pcb: dict[int, int] = {}

        # Fine-focus mode state
        self._fine_mode = False
        self._tile_grid: Optional[TileGrid] = None
        self._last_sharpness: Optional[SharpnessResult] = None
        self._last_overview: Optional[np.ndarray] = None

        btn_style = (
            "QPushButton { font-size:10px; padding:2px 4px; }"
            "QComboBox   { font-size:10px; padding:1px 2px; }"
            "QLabel       { font-size:10px; }"
        )

        # ── Layout: left panel + right display ────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)

        left_panel = QWidget()
        left_panel.setStyleSheet(btn_style)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(2, 2, 2, 2)
        left_layout.setSpacing(2)
        left_panel.setMaximumWidth(155)

        # ── Serial connection ─────────────────────────────────────────
        port_row = QHBoxLayout()
        port_row.setSpacing(1)
        self._port_combo = QComboBox()
        self._port_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        port_row.addWidget(self._port_combo, stretch=1)
        refresh_btn = QPushButton("↻")
        refresh_btn.setFixedWidth(18)
        refresh_btn.setToolTip("Refresh ports")
        refresh_btn.clicked.connect(self._refresh_ports)
        port_row.addWidget(refresh_btn)
        self._connect_btn = QPushButton("Conn")
        self._connect_btn.setFixedWidth(34)
        self._connect_btn.clicked.connect(self._toggle_connection)
        port_row.addWidget(self._connect_btn)
        left_layout.addLayout(port_row)

        # ── Mode row ──────────────────────────────────────────────────
        mode_row = QHBoxLayout()
        mode_row.setSpacing(1)
        for mode_num, mode_label in ((0, "Std"), (1, "Cont"), (4, "Focus")):
            btn = QPushButton(f"M{mode_num}")
            btn.setToolTip(mode_label)
            btn.clicked.connect(lambda _, m=mode_num: self._ctrl.set_mode(m))
            mode_row.addWidget(btn)
        left_layout.addLayout(mode_row)

        # ── Unified camera buttons (serial number labels) ─────────────
        cam_row = QHBoxLayout()
        cam_row.setSpacing(1)
        real_cams = (
            grabber.cameras
            if is_real and hasattr(grabber, "cameras") and grabber.cameras
            else []
        )
        real_ids = {c.get("device_id", "") for c in real_cams}
        for serial, pcb_num in self.SERIAL_TO_PCB_CAM.items():
            btn = QPushButton(serial)
            btn.setCheckable(True)
            # Find matching grabber index (if camera is connected)
            grabber_idx = next(
                (c["index"] for c in real_cams if c.get("device_id") == serial),
                None,
            )
            if grabber_idx is not None:
                self._grabber_to_pcb[grabber_idx] = pcb_num
                btn.clicked.connect(
                    lambda _, gi=grabber_idx: self._select_camera(gi)
                )
            else:
                btn.setEnabled(False)
            cam_row.addWidget(btn)
            self._cam_buttons.append(btn)
        left_layout.addLayout(cam_row)
        if real_cams:
            self._highlight_active_camera()

        # ── Strength row ──────────────────────────────────────────────
        str_row = QHBoxLayout()
        str_row.setSpacing(1)
        for s in (1, 2, 3):
            btn = QPushButton(f"S{s}")
            btn.clicked.connect(lambda _, v=s: self._ctrl.set_strength(v))
            str_row.addWidget(btn)
        left_layout.addLayout(str_row)

        # ── Coarse / Fine toggle ──────────────────────────────────────
        self._mode_btn = QPushButton("→ Fine")
        self._mode_btn.setCheckable(True)
        self._mode_btn.setToolTip("Toggle coarse ↔ fine focus mode")
        self._mode_btn.clicked.connect(self._toggle_fine_mode)
        left_layout.addWidget(self._mode_btn)

        # ── PCB log ───────────────────────────────────────────────────
        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(200)
        self._log_view.setStyleSheet("font-size: 9px; font-family: monospace;")
        left_layout.addWidget(self._log_view, stretch=1)

        # ── Keyboard hint ─────────────────────────────────────────────
        hint = QLabel("↑↓ Focus ←→ Trig Shift=×3")
        hint.setStyleSheet("color: #888; font-size: 8px;")
        left_layout.addWidget(hint)

        splitter.addWidget(left_panel)

        # ── Right: camera display ─────────────────────────────────────
        right_panel = QWidget()
        right_lay = QVBoxLayout(right_panel)
        right_lay.setContentsMargins(0, 0, 0, 0)

        self._frame_info = QLabel("Waiting for frames...")
        self._frame_info.setStyleSheet(
            "background: #222; color: #ccc; padding: 4px; font-family: monospace;"
        )
        right_lay.addWidget(self._frame_info)

        self._display = QLabel()
        self._display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._display.setStyleSheet("background: #111;")
        right_lay.addWidget(self._display, stretch=1)

        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([155, 1125])

        self._refresh_ports()
        QApplication.instance().installEventFilter(self)

        # Auto-connect to ClearCore if present
        cc_port = FocusController.find_clearcore()
        if cc_port:
            try:
                self._ctrl.connect(cc_port, on_response=self._bridge.serial_rx.emit)
                self._connect_btn.setText("Kill")
                self._log_view.appendPlainText(f"— Auto {cc_port} OK —")
                idx = self._port_combo.findText(cc_port)
                if idx >= 0:
                    self._port_combo.setCurrentIndex(idx)
            except Exception as exc:
                self._log_view.appendPlainText(f"Auto-connect ERR: {exc}")

        # Start frame acquisition
        self._grabber.start(on_frame=self._on_frame)

    # ── Global key intercept ──────────────────────────────────────────

    _FOCUS_KEYS = {Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right}

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.KeyPress and event.key() in self._FOCUS_KEYS:
            if self._ctrl.connected:
                shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                count = 3 if shift else 1
                key = event.key()
                if key == Qt.Key.Key_Up:
                    self._ctrl.focus_step("+", count)
                elif key == Qt.Key.Key_Down:
                    self._ctrl.focus_step("-", count)
                elif key == Qt.Key.Key_Right:
                    self._ctrl.adjust_trigger_period(">")
                elif key == Qt.Key.Key_Left:
                    self._ctrl.adjust_trigger_period("<")
            return True
        return super().eventFilter(obj, event)

    # ── Serial slots ──────────────────────────────────────────────────

    def _refresh_ports(self) -> None:
        self._port_combo.clear()
        for port in FocusController.list_ports():
            self._port_combo.addItem(port)

    def _toggle_connection(self) -> None:
        if self._ctrl.connected:
            self._ctrl.disconnect()
            self._connect_btn.setText("Conn")
            self._log_view.appendPlainText("— Disconnected —")
        else:
            port = self._port_combo.currentText()
            if not port:
                return
            try:
                self._ctrl.connect(port, on_response=self._bridge.serial_rx.emit)
            except Exception as exc:
                self._log_view.appendPlainText(f"ERR: {exc}")
                return
            self._connect_btn.setText("Kill")
            self._log_view.appendPlainText(f"— {port} OK —")

    def _on_serial_rx(self, text: str) -> None:
        self._log_view.appendPlainText(text)

    # ── Camera switching ──────────────────────────────────────────────

    def _highlight_active_camera(self) -> None:
        active = getattr(self._grabber, "active_camera_index", -1)
        for i, btn in enumerate(self._cam_buttons):
            btn.setChecked(i == active)

    def _select_camera(self, grabber_index: int) -> None:
        """Switch the grabber stream AND send $Cn* to the PCB."""
        self._frame_info.setText(f"Switching to camera {grabber_index}...")
        self._frame_count = 0
        QApplication.processEvents()
        try:
            self._grabber.switch_camera(grabber_index)
        except Exception:
            log.exception("Failed to switch camera")
            self._frame_info.setText(f"Error switching to camera {grabber_index}")
        self._highlight_active_camera()

        pcb_cam = self._grabber_to_pcb.get(grabber_index)
        if pcb_cam is not None and self._ctrl.connected:
            self._ctrl.select_camera(pcb_cam)

    # ── Fine-focus mode ──────────────────────────────────────────────

    def _toggle_fine_mode(self) -> None:
        self._fine_mode = self._mode_btn.isChecked()
        if self._fine_mode:
            self._mode_btn.setText("→ Coarse")
            self._init_tile_grid()
            self._grabber.set_raw_processor(self._fine_processor)
            log.info("Switched to FINE mode — %s", self._tile_grid.summary())
        else:
            self._mode_btn.setText("→ Fine")
            self._grabber.set_raw_processor(None)
            self._last_sharpness = None
            self._last_overview = None
            log.info("Switched to COARSE mode")

    def _init_tile_grid(self) -> None:
        if self._is_real:
            fw, fh = 13400, 9528
        else:
            fw, fh = self._grabber._width, self._grabber._height
        dw = self._display.width() or 1920
        dh = self._display.height() or 1080
        self._tile_grid = TileGrid(fw, fh, dw, dh, zoom=1.5)
        log.info("Tile grid: %s", self._tile_grid.summary())

    def _fine_processor(self, raw_view: np.ndarray) -> np.ndarray:
        """Called from the SDK/sim thread.  Must be fast (~12 ms)."""
        result = find_best_tile(raw_view, self._tile_grid)
        # Build a small overview for the inset (2× subsample → uint8)
        if raw_view.dtype == np.uint16:
            overview = (raw_view[::2, ::2] >> 6).astype(np.uint8)
        else:
            overview = raw_view[::2, ::2].copy()
        self._last_sharpness = result
        self._last_overview = overview

        tile_crop = result.best_tile_full
        # Debayer the winning tile for colour display (real cameras only)
        if self._is_real and tile_crop.ndim == 2:
            tile_crop = cv2.cvtColor(tile_crop, cv2.COLOR_BayerBG2RGB)
        return tile_crop

    # ── Frame display ─────────────────────────────────────────────────

    def _on_frame(self, frame: np.ndarray) -> None:
        self._pending_frame = frame
        self._bridge.frame_ready.emit(frame)

    def _show_frame(self, frame: np.ndarray) -> None:
        if self._display_busy:
            return
        latest = self._pending_frame
        if latest is None:
            return
        self._pending_frame = None
        self._display_busy = True

        try:
            self._frame_count += 1
            h, w = latest.shape[:2]

            label_w = self._display.width() or 960
            label_h = self._display.height() or 540

            if self._fine_mode and self._last_sharpness is not None:
                self._show_fine_frame(latest, label_w, label_h)
            else:
                self._show_coarse_frame(latest, label_w, label_h)
        finally:
            self._display_busy = False

    def _show_coarse_frame(
        self, frame: np.ndarray, label_w: int, label_h: int
    ) -> None:
        h, w = frame.shape[:2]
        scale = min(label_w / w, label_h / h, 1.0)
        dw, dh = int(w * scale), int(h * scale)
        small = cv2.resize(frame, (dw, dh), interpolation=cv2.INTER_AREA)

        smin, smax = int(small.min()), int(small.max())
        cam_label = self._cam_label()
        self._frame_info.setText(
            f"Frame #{self._frame_count}  {w}×{h}  "
            f"range=[{smin}..{smax}]{cam_label}"
        )

        self._display.setPixmap(self._gray_to_pixmap(small, dw, dh))

    def _show_fine_frame(
        self, tile_crop: np.ndarray, label_w: int, label_h: int
    ) -> None:
        result = self._last_sharpness
        overview = self._last_overview

        h, w = tile_crop.shape[:2]
        # Scale tile to fill display (this *is* the 150 % zoom path)
        scale = min(label_w / w, label_h / h)
        dw, dh = int(w * scale), int(h * scale)
        display = cv2.resize(tile_crop, (dw, dh), interpolation=cv2.INTER_AREA)

        pix = self._gray_to_pixmap(display, dw, dh)

        # Draw overview inset in bottom-left corner
        if overview is not None and self._tile_grid is not None:
            pix = self._draw_overview_inset(pix, overview, result)

        cam_label = self._cam_label()
        self._frame_info.setText(
            f"Frame #{self._frame_count}  FINE  "
            f"tile #{result.best_index}/{len(self._tile_grid)}  "
            f"score={result.best_score:.1f}{cam_label}"
        )
        self._display.setPixmap(pix)

    def _draw_overview_inset(
        self,
        pix: QPixmap,
        overview: np.ndarray,
        result: SharpnessResult,
    ) -> QPixmap:
        """Composite a small overview image with highlighted tile onto *pix*."""
        grid = self._tile_grid
        oh, ow = overview.shape[:2]

        # Scale overview to fit in an inset ~220 px wide
        inset_w = 220
        inset_scale = inset_w / ow
        inset_h = int(oh * inset_scale)
        inset_img = cv2.resize(overview, (inset_w, inset_h), interpolation=cv2.INTER_AREA)
        inset_img = np.ascontiguousarray(inset_img)
        inset_qimg = QImage(
            inset_img.data, inset_w, inset_h, inset_w,
            QImage.Format.Format_Grayscale8,
        )
        inset_pix = QPixmap.fromImage(inset_qimg)

        # Draw the winning tile rectangle and circle on the inset
        # The overview is 2× subsampled from the full frame, and then
        # further scaled by inset_scale.
        combined_scale = inset_scale / 2.0  # full-frame → inset pixels

        painter = QPainter(inset_pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Circle boundary
        pen_circle = QPen(QColor(80, 80, 255, 120))
        pen_circle.setWidth(1)
        painter.setPen(pen_circle)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        cx = int(grid.circle_cx * combined_scale)
        cy = int(grid.circle_cy * combined_scale)
        r = int(grid.circle_r * combined_scale)
        painter.drawEllipse(cx - r, cy - r, r * 2, r * 2)

        # Best tile rectangle (green)
        best = grid[result.best_index]
        pen_tile = QPen(QColor(0, 255, 0, 200))
        pen_tile.setWidth(2)
        painter.setPen(pen_tile)
        rx = int(best.x0 * combined_scale)
        ry = int(best.y0 * combined_scale)
        rw = int(best.width * combined_scale)
        rh = int(best.height * combined_scale)
        painter.drawRect(rx, ry, rw, rh)

        painter.end()

        # Composite inset onto the main pixmap (bottom-left, with margin)
        margin = 8
        out = QPixmap(pix)
        p2 = QPainter(out)
        ix = margin
        iy = pix.height() - inset_h - margin
        p2.drawPixmap(ix, iy, inset_pix)
        # Border around inset
        p2.setPen(QPen(QColor(200, 200, 200, 180)))
        p2.drawRect(ix, iy, inset_w - 1, inset_h - 1)
        p2.end()
        return out

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _gray_to_pixmap(img: np.ndarray, w: int, h: int) -> QPixmap:
        img = np.ascontiguousarray(img)
        if img.ndim == 2:
            qimg = QImage(img.data, w, h, w, QImage.Format.Format_Grayscale8)
        else:
            qimg = QImage(img.data, w, h, w * 3, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(qimg)

    def _cam_label(self) -> str:
        if self._is_real and hasattr(self._grabber, "cameras"):
            idx = self._grabber.active_camera_index
            cams = self._grabber.cameras
            if 0 <= idx < len(cams):
                return f"  cam={cams[idx].get('device_id', idx)}"
        return ""

    # ── Cleanup ───────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        self._ctrl.disconnect()
        self._grabber.stop()
        if hasattr(self._grabber, "close"):
            self._grabber.close()
        super().closeEvent(event)


def main() -> None:
    parser = argparse.ArgumentParser(description="Tile-Based Focus Tool")
    parser.add_argument("--real", action="store_true", help="Use real Kaya cameras")
    parser.add_argument("--grabber", type=int, default=0, help="Grabber index (default 0)")
    parser.add_argument("--camera", type=int, default=0, help="Initial camera index (default 0)")
    parser.add_argument(
        "--fullsize", action="store_true",
        help="Sim generates 13400×9528 frames (needed for fine-mode testing)",
    )
    args = parser.parse_args()

    if args.real:
        from frame_grabber import FrameGrabber, scan_grabbers
        grabbers = scan_grabbers()
        for g in grabbers:
            print(f"  Grabber {g['index']}: {g['name']}")
        grabber = FrameGrabber()
        grabber.open(args.grabber, args.camera)
        # Push DEFAULT_CAMERA_CONFIG to every camera on the grabber, not just
        # the one we're about to stream from. Otherwise cameras the user
        # hasn't selected yet keep whatever Vision Point / previous app left
        # behind (we hit this with cam 2439006 silently stuck in
        # ExposureAuto=Continuous).
        grabber.configure_all_cameras()
        is_real = True
    else:
        from frame_grabber_sim import SimulatedGrabber
        if args.fullsize:
            grabber = SimulatedGrabber(width=13400, height=9528, fps=1.0)
        else:
            grabber = SimulatedGrabber(width=1920, height=1080, fps=2.0)
        grabber.open()
        is_real = False

    app = QApplication(sys.argv)
    window = MainWindow(grabber, is_real=is_real)
    window.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
