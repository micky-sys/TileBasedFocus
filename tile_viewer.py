"""Offline tile-selection viewer.

Load aerial test images, run the same tile-choosing algorithm used by
the live app, and inspect the results.

Usage:
    python tile_viewer.py <image_or_directory> [--width 13400] [--height 9528]

Controls:
    Left / Right   — previous / next image
    Escape         — quit
"""

import argparse
import sys
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QImage, QPixmap, QPainter, QColor, QPen, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from tile_geometry import TileGrid
from sharpness import find_best_tile, SharpnessResult

IMAGE_EXTS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp"}


def load_image(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Load an image. Returns (gray, display) where *gray* is single-channel
    for sharpness scoring and *display* is BGR colour (or gray if source is gray)."""
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"Cannot read {path}")
    if img.ndim == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        display = img  # keep original colour
    else:
        gray = img
        display = img
    return gray, display


class TileViewer(QMainWindow):
    def __init__(self, images: list[Path], frame_w: int, frame_h: int) -> None:
        super().__init__()
        self.setWindowTitle("Tile Viewer")
        self._images = images
        self._index = 0
        self._frame_w = frame_w
        self._frame_h = frame_h
        self._grid: Optional[TileGrid] = None

        root = QWidget()
        self.setCentralWidget(root)
        main_lay = QVBoxLayout(root)
        main_lay.setContentsMargins(4, 4, 4, 4)

        top_row = QHBoxLayout()
        top_row.setSpacing(6)

        self._goto = QLineEdit()
        self._goto.setFixedWidth(60)
        self._goto.setPlaceholderText("#")
        self._goto.setStyleSheet(
            "background:#333; color:#fff; padding:4px; font-family:monospace; font-size:12px; border:1px solid #555;"
        )
        self._goto.returnPressed.connect(self._jump_to_image)
        top_row.addWidget(self._goto)

        self._info = QLabel()
        self._info.setStyleSheet(
            "background:#222; color:#ccc; padding:4px; font-family:monospace; font-size:12px;"
        )
        top_row.addWidget(self._info, stretch=1)
        main_lay.addLayout(top_row)

        img_row = QHBoxLayout()
        img_row.setSpacing(6)

        self._overview_label = QLabel()
        self._overview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._overview_label.setStyleSheet("background:#111;")
        self._overview_label.setMinimumWidth(400)
        img_row.addWidget(self._overview_label, stretch=2)

        self._tile_label = QLabel()
        self._tile_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._tile_label.setStyleSheet("background:#111;")
        self._tile_label.setMinimumWidth(400)
        img_row.addWidget(self._tile_label, stretch=3)

        main_lay.addLayout(img_row, stretch=1)

        QApplication.instance().installEventFilter(self)
        self._process_current()

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Right:
                self._navigate(1)
                return True
            if key == Qt.Key.Key_Left:
                self._navigate(-1)
                return True
            if key == Qt.Key.Key_Escape:
                self.close()
                return True
        return super().eventFilter(obj, event)

    def _navigate(self, delta: int) -> None:
        new = self._index + delta
        if 0 <= new < len(self._images):
            self._index = new
            self._process_current()

    def _jump_to_image(self) -> None:
        text = self._goto.text().strip()
        try:
            num = int(text) - 1  # user enters 1-based
            if 0 <= num < len(self._images):
                self._index = num
                self._process_current()
            else:
                self._info.setText(f"Out of range (1–{len(self._images)})")
        except ValueError:
            self._info.setText("Enter a number")
        self._goto.clear()

    def _process_current(self) -> None:
        path = self._images[self._index]
        gray, display = load_image(path)
        h, w = gray.shape[:2]

        fw = self._frame_w or w
        fh = self._frame_h or h
        if (w, h) != (fw, fh):
            gray = cv2.resize(gray, (fw, fh), interpolation=cv2.INTER_AREA)
            display = cv2.resize(display, (fw, fh), interpolation=cv2.INTER_AREA)
            h, w = fh, fw

        if self._grid is None or self._grid.frame_w != w or self._grid.frame_h != h:
            self._grid = TileGrid(w, h)

        result = find_best_tile(gray, self._grid)
        self._show(path, display, result)

    def _show(self, path: Path, display: np.ndarray, result: SharpnessResult) -> None:
        grid = self._grid
        h, w = display.shape[:2]

        # --- Info bar ---
        best = grid[result.best_index]
        self._info.setText(
            f"[{self._index + 1}/{len(self._images)}]  {path.name}  "
            f"{w}×{h}  tile #{result.best_index}/{len(grid)}  "
            f"score={result.best_score:.1f}  "
            f"region=({best.x0},{best.y0})–({best.x1},{best.y1})"
        )

        # --- Overview (full frame with annotations) ---
        ov_h = self._overview_label.height() or 600
        ov_scale = ov_h / h
        ov_w = int(w * ov_scale)
        overview = cv2.resize(display, (ov_w, ov_h), interpolation=cv2.INTER_AREA)
        if overview.dtype == np.uint16:
            overview = (overview >> 6).astype(np.uint8)

        if overview.ndim == 2:
            overview_rgb = cv2.cvtColor(overview, cv2.COLOR_GRAY2RGB)
        else:
            overview_rgb = cv2.cvtColor(overview, cv2.COLOR_BGR2RGB)
        overview_rgb = np.ascontiguousarray(overview_rgb)
        qimg_ov = QImage(overview_rgb.data, ov_w, ov_h, ov_w * 3, QImage.Format.Format_RGB888)
        pix_ov = QPixmap.fromImage(qimg_ov)

        painter = QPainter(pix_ov)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Draw circle
        pen_c = QPen(QColor(80, 80, 255, 140))
        pen_c.setWidth(1)
        painter.setPen(pen_c)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        cx = int(grid.circle_cx * ov_scale)
        cy = int(grid.circle_cy * ov_scale)
        r = int(grid.circle_r * ov_scale)
        painter.drawEllipse(cx - r, cy - r, r * 2, r * 2)

        # Draw all tiles (dim)
        pen_tile = QPen(QColor(255, 255, 255, 60))
        pen_tile.setWidth(1)
        painter.setPen(pen_tile)
        for i, tile in enumerate(grid):
            rx = int(tile.x0 * ov_scale)
            ry = int(tile.y0 * ov_scale)
            rw = int(tile.width * ov_scale)
            rh = int(tile.height * ov_scale)
            painter.drawRect(rx, ry, rw, rh)

        # Draw score labels on each tile
        painter.setFont(QFont("monospace", 7))
        for i, tile in enumerate(grid):
            tx = int(tile.cx * ov_scale)
            ty = int(tile.cy * ov_scale)
            score_text = f"{result.scores[i]:.1f}"
            if i == result.best_index:
                painter.setPen(QColor(0, 255, 0, 220))
            else:
                painter.setPen(QColor(200, 200, 200, 160))
            painter.drawText(tx - 15, ty + 4, score_text)

        # Draw winning tile (green, thick)
        pen_best = QPen(QColor(0, 255, 0, 220))
        pen_best.setWidth(2)
        painter.setPen(pen_best)
        rx = int(best.x0 * ov_scale)
        ry = int(best.y0 * ov_scale)
        rw = int(best.width * ov_scale)
        rh = int(best.height * ov_scale)
        painter.drawRect(rx, ry, rw, rh)

        painter.end()
        self._overview_label.setPixmap(pix_ov)

        # --- Winning tile (zoomed, from colour display image) ---
        tile_crop = display[best.y0:best.y1, best.x0:best.x1]
        if tile_crop.dtype == np.uint16:
            tile_crop = (tile_crop >> 6).astype(np.uint8)

        th = self._tile_label.height() or 600
        t_scale = th / tile_crop.shape[0]
        tw = int(tile_crop.shape[1] * t_scale)
        tile_disp = cv2.resize(tile_crop, (tw, th), interpolation=cv2.INTER_AREA)

        if tile_disp.ndim == 2:
            tile_disp = np.ascontiguousarray(tile_disp)
            qimg_t = QImage(tile_disp.data, tw, th, tw, QImage.Format.Format_Grayscale8)
        else:
            tile_disp = cv2.cvtColor(tile_disp, cv2.COLOR_BGR2RGB)
            tile_disp = np.ascontiguousarray(tile_disp)
            qimg_t = QImage(tile_disp.data, tw, th, tw * 3, QImage.Format.Format_RGB888)
        self._tile_label.setPixmap(QPixmap.fromImage(qimg_t))


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline tile-selection viewer")
    parser.add_argument("path", help="Image file or directory of images")
    parser.add_argument("--width", type=int, default=0, help="Resize to this width (0 = use native)")
    parser.add_argument("--height", type=int, default=0, help="Resize to this height (0 = use native)")
    args = parser.parse_args()

    p = Path(args.path)
    if p.is_dir():
        images = sorted(f for f in p.iterdir() if f.suffix.lower() in IMAGE_EXTS)
    elif p.is_file():
        images = [p]
    else:
        print(f"Error: {p} not found")
        sys.exit(1)

    if not images:
        print(f"No images found in {p}")
        sys.exit(1)

    print(f"Found {len(images)} image(s)")

    app = QApplication(sys.argv)
    viewer = TileViewer(images, args.width, args.height)
    viewer.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
