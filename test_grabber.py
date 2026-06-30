"""Standalone test: display frames from either the real or simulated grabber.

Usage:
    python test_grabber.py                      # simulated, small (1920x1080)
    python test_grabber.py --real               # real camera (grabber 0, camera 0)
    python test_grabber.py --real --grabber 0 --camera 1
    python test_grabber.py --dir PATH           # simulated, loads real images
    python test_grabber.py --fullsize           # simulated at full 13400x9528
"""

import argparse
import sys
import logging
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal, QObject
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow,
    QPushButton, QVBoxLayout, QWidget,
)

import cv2
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger(__name__)


class SignalBridge(QObject):
    frame_ready = pyqtSignal(np.ndarray)


class TestWindow(QMainWindow):
    def __init__(self, grabber, is_real: bool = False) -> None:
        super().__init__()
        self.setWindowTitle("Grabber Test")
        self.resize(960, 540)

        self._grabber = grabber
        self._is_real = is_real
        self._bridge = SignalBridge()
        self._bridge.frame_ready.connect(self._show_frame)
        self._frame_count = 0
        self._pending_frame: Optional[np.ndarray] = None
        self._display_busy = False
        self._cam_buttons: list[QPushButton] = []

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # Camera selector row (only for real grabbers with multiple cameras)
        if is_real and hasattr(grabber, "cameras") and len(grabber.cameras) > 1:
            cam_row = QHBoxLayout()
            cam_row.addWidget(QLabel("Camera:"))
            for cam in grabber.cameras:
                label = cam.get("device_id") or cam.get("model") or f"Cam {cam['index']}"
                btn = QPushButton(label)
                btn.setCheckable(True)
                idx = cam["index"]
                btn.clicked.connect(lambda checked, i=idx: self._switch_camera(i))
                cam_row.addWidget(btn)
                self._cam_buttons.append(btn)
            cam_row.addStretch()
            layout.addLayout(cam_row)
            self._highlight_active_camera()

        self._info = QLabel("Waiting for frame...")
        layout.addWidget(self._info)

        self._display = QLabel()
        self._display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._display, stretch=1)

        self._grabber.start(on_frame=self._on_frame)

    def _highlight_active_camera(self) -> None:
        active = getattr(self._grabber, "active_camera_index", -1)
        for i, btn in enumerate(self._cam_buttons):
            btn.setChecked(i == active)

    def _switch_camera(self, camera_index: int) -> None:
        self._info.setText(f"Switching to camera {camera_index}...")
        self._frame_count = 0
        QApplication.processEvents()
        try:
            self._grabber.switch_camera(camera_index)
            log.info("Switched to camera %d", camera_index)
        except Exception:
            log.exception("Failed to switch to camera %d", camera_index)
            self._info.setText(f"Error switching to camera {camera_index}")
        self._highlight_active_camera()

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
            scale = min(label_w / w, label_h / h, 1.0)
            dw, dh = int(w * scale), int(h * scale)
            small = cv2.resize(latest, (dw, dh), interpolation=cv2.INTER_AREA)

            smin, smax = int(small.min()), int(small.max())
            cam_label = ""
            if self._is_real and hasattr(self._grabber, "cameras"):
                idx = self._grabber.active_camera_index
                cams = self._grabber.cameras
                if 0 <= idx < len(cams):
                    cam_label = f"  cam={cams[idx].get('device_id', idx)}"

            self._info.setText(
                f"Frame #{self._frame_count}: {w}x{h}  "
                f"range=[{smin}..{smax}]{cam_label}"
            )

            if small.ndim == 2:
                small = np.ascontiguousarray(small)
                qimg = QImage(small.data, dw, dh, dw, QImage.Format.Format_Grayscale8)
            else:
                small = np.ascontiguousarray(small)
                qimg = QImage(small.data, dw, dh, dw * 3, QImage.Format.Format_RGB888)
            self._display.setPixmap(QPixmap.fromImage(qimg))
        finally:
            self._display_busy = False

    def closeEvent(self, event) -> None:
        self._grabber.stop()
        if hasattr(self._grabber, "close"):
            self._grabber.close()
        super().closeEvent(event)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true", help="Use real Kaya camera")
    parser.add_argument("--grabber", type=int, default=0, help="Grabber index (default 0)")
    parser.add_argument("--camera", type=int, default=0, help="Camera index (default 0)")
    parser.add_argument("--fullsize", action="store_true", help="Simulated at full 13400x9528")
    parser.add_argument("--dir", type=str, default=None, help="Load images from directory (sim mode)")
    args = parser.parse_args()

    if args.real:
        from frame_grabber import FrameGrabber, scan_grabbers
        grabbers = scan_grabbers()
        for g in grabbers:
            print(f"  Grabber {g['index']}: {g['name']}")
        grabber = FrameGrabber()
        grabber.open(args.grabber, args.camera)
        grabber.configure()
        is_real = True
    else:
        from frame_grabber_sim import SimulatedGrabber
        w, h = (13400, 9528) if args.fullsize else (1920, 1080)
        grabber = SimulatedGrabber(width=w, height=h, fps=2.0, image_dir=args.dir)
        grabber.open()
        is_real = False

    app = QApplication(sys.argv)
    window = TestWindow(grabber, is_real=is_real)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
