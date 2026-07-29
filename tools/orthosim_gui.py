r"""OrthoSim -- orthophoto screen bench rig display app.

Full-screen PySide6 app that scrolls a high-resolution orthophoto at a
calibrated, keyboard-controllable speed (and zoom = simulated height) to
simulate flight in front of a stationary Iron661 camera. It renders:

* the scrolling orthophoto (GPU-accelerated via a QOpenGLWidget viewport),
* four ArUco corner fiducials so the capture side can homography-crop the
  screen quad and recover the screen->camera magnification,
* a high-contrast optical ground-truth bar (absolute distance + tick +
  velocity, see ``orthosim_codec``) that the Iron661 capture app decodes,
* an on-screen HUD (toggleable) with speed / GSD / distance.

A CSV of the same ground-truth is written alongside as a cross-check.

Run from source::

    py -3 tools/orthosim_gui.py PATH\TO\ortho.tif --gsd 5.0
    py -3 tools/orthosim_gui.py ortho.png --gsd 4.0 --panel-pitch 0.181 --M 1.0
    py -3 tools/orthosim_gui.py ortho.png --gsd 5 --windowed   # for bring-up

Controls: Up/Down = +/- speed, +/- or PageUp/PageDown = zoom (height),
Space = pause, H = HUD, C = calibration overlay, R = reset distance,
S = save config, Esc/Q = quit.
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from PySide6.QtCore import QElapsedTimer, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QKeyEvent,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QMainWindow

try:
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
    _HAVE_GL = True
except Exception:  # pragma: no cover - platform without GL widget
    from PySide6.QtWidgets import QWidget as QOpenGLWidget  # type: ignore
    _HAVE_GL = False

import cv2

_TOOLS = Path(__file__).resolve().parent
_ROOT = _TOOLS.parent
for _p in (str(_TOOLS), str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orthosim_codec as codec  # noqa: E402
from orthosim_core import (  # noqa: E402
    GroundTruthLogger,
    OrthoSimConfig,
    scale_ticks,
    scroll_speed_screen_px_s,
    screen_gsd_cm,
    simulated_gsd_cm,
)

_TILE_MAX = 4096  # max pixmap tile edge (safe below GPU texture limits)


def build_date() -> str:
    """Human-readable build/last-modified date (YYYY-MM-DD)."""
    try:
        target = (
            Path(sys.executable)
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve()
        )
        return datetime.fromtimestamp(target.stat().st_mtime).strftime("%Y-%m-%d")
    except OSError:
        return "unknown"


def _fmt_dist(m: float) -> str:
    """Compact ground-distance label: metres below 1 km, else km."""
    return f"{m/1000.0:g} km" if m >= 1000.0 else f"{m:g} m"


# ── Orthophoto tiling ────────────────────────────────────────────────────────
class OrthoTile:
    __slots__ = ("x0", "y0", "w", "h", "pix")

    def __init__(self, x0: int, y0: int, w: int, h: int, pix: QPixmap) -> None:
        self.x0 = x0
        self.y0 = y0
        self.w = w
        self.h = h
        self.pix = pix


class OrthoImage:
    """Load a (possibly very large) orthophoto and split into GPU-safe tiles."""

    def __init__(self, path: Path, reduce: int = 1) -> None:
        img = self._load_rgb(path, reduce)
        self.height, self.width = img.shape[:2]
        self.tiles: List[OrthoTile] = []
        for y0 in range(0, self.height, _TILE_MAX):
            for x0 in range(0, self.width, _TILE_MAX):
                y1 = min(self.height, y0 + _TILE_MAX)
                x1 = min(self.width, x0 + _TILE_MAX)
                sub = np.ascontiguousarray(img[y0:y1, x0:x1])
                h, w = sub.shape[:2]
                qimg = QImage(sub.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
                self.tiles.append(OrthoTile(x0, y0, w, h, QPixmap.fromImage(qimg)))

    # 1/N-resolution decode flags: gigapixel orthos are far bigger than any
    # screen, so decoding at 1/2..1/8 loads in seconds and uses a fraction of
    # the RAM. Calibration stays exact as long as the caller scales the GSD by
    # the same N (see main()).
    _REDUCE_FLAG = {
        1: cv2.IMREAD_COLOR,
        2: cv2.IMREAD_REDUCED_COLOR_2,
        4: cv2.IMREAD_REDUCED_COLOR_4,
        8: cv2.IMREAD_REDUCED_COLOR_8,
    }

    @classmethod
    def _load_rgb(cls, path: Path, reduce: int = 1) -> np.ndarray:
        # Prefer OpenCV (handles big TIFF/PNG/JPG); fall back to QImage.
        flag = cls._REDUCE_FLAG.get(reduce, cv2.IMREAD_COLOR)
        arr = cv2.imread(str(path), flag)
        if arr is not None:
            return cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
        qimg = QImage(str(path))
        if qimg.isNull():
            raise FileNotFoundError(f"Could not load orthophoto: {path}")
        qimg = qimg.convertToFormat(QImage.Format.Format_RGB888)
        w, h = qimg.width(), qimg.height()
        ptr = qimg.constBits()
        buf = np.frombuffer(ptr, np.uint8, count=h * qimg.bytesPerLine())
        buf = buf.reshape(h, qimg.bytesPerLine())[:, : 3 * w].reshape(h, w, 3)
        return np.ascontiguousarray(buf)


# ── ArUco fiducials ──────────────────────────────────────────────────────────
def make_aruco_pixmaps(dict_name: str, side_px: int) -> List[Optional[QPixmap]]:
    """Return 4 corner-marker pixmaps (ids 0..3), or [None]*4 if unavailable."""
    if not hasattr(cv2, "aruco"):
        return [None, None, None, None]
    try:
        const = getattr(cv2.aruco, dict_name)
        dic = cv2.aruco.getPredefinedDictionary(const)
        out: List[Optional[QPixmap]] = []
        for mid in range(4):
            img = cv2.aruco.generateImageMarker(dic, mid, side_px)
            h, w = img.shape[:2]
            qimg = QImage(
                np.ascontiguousarray(img).data, w, h, w,
                QImage.Format.Format_Grayscale8,
            ).copy()
            out.append(QPixmap.fromImage(qimg))
        return out
    except Exception:
        return [None, None, None, None]


# ── Scrolling view ───────────────────────────────────────────────────────────
class OrthoView(QOpenGLWidget):
    """Renders the scrolling orthophoto + fiducials + optical bar + HUD."""

    def __init__(
        self,
        ortho: OrthoImage,
        cfg: OrthoSimConfig,
        logger: Optional[GroundTruthLogger],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.ortho = ortho
        self.cfg = cfg
        self._logger = logger
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # dynamic state
        self._zoom = cfg.zoom if cfg.zoom > 0 else 0.0  # 0 -> fit width lazily
        self._speed = float(cfg.ground_speed_mps)
        self._scroll = 0.0
        self._distance_m = 0.0
        self._tick = 0
        self._paused = False
        self._hud = bool(cfg.hud_enabled)
        self._calibration = False
        self._scale = bool(getattr(cfg, "scale_enabled", False))
        self._seam_visible = False

        self._aruco = make_aruco_pixmaps(cfg.aruco_dict, cfg.fiducial_px)

        self._clock = QElapsedTimer()
        self._clock.start()
        self._last_ns = self._clock.nsecsElapsed()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start(8)  # ~120 Hz; motion is dt-based so jitter is benign

    # ── helpers ──────────────────────────────────────────────────────────
    def _ensure_zoom(self) -> None:
        if self._zoom <= 0:
            vw = max(1, self.width())
            self._zoom = vw / max(1, self.ortho.width)

    @property
    def scroll_axis_len_screen(self) -> float:
        z = self._zoom
        return (self.ortho.height if self._is_vertical() else self.ortho.width) * z

    def _is_vertical(self) -> bool:
        return self.cfg.scroll_dir in ("up", "down")

    # ── per-frame update ─────────────────────────────────────────────────
    def _on_tick(self) -> None:
        now = self._clock.nsecsElapsed()
        dt = (now - self._last_ns) / 1e9
        self._last_ns = now
        if dt <= 0:
            return
        self._ensure_zoom()
        if not self._paused:
            self._speed = max(0.0, min(self.cfg.max_speed_mps, self._speed))
            self._distance_m += self._speed * dt
            v_px = scroll_speed_screen_px_s(self._speed, self.cfg.ortho_gsd_cm, self._zoom)
            self._scroll += v_px * dt
            self._tick = (self._tick + 1) % (1 << 20)
        if self._logger is not None:
            self._logger.maybe_log(
                tick=self._tick,
                distance_m=self._distance_m,
                velocity_mps=self._speed,
                screen_gsd_cm_value=screen_gsd_cm(self.cfg.ortho_gsd_cm, self._zoom),
                simulated_gsd_cm_value=simulated_gsd_cm(
                    self.cfg.ortho_gsd_cm, self._zoom, self.cfg.magnification
                ),
                zoom=self._zoom,
                flags=self._current_flags(),
            )
        self.update()

    def _current_flags(self) -> int:
        flags = 0
        if self._seam_visible:
            flags |= codec.FLAG_SEAM
        if self._paused:
            flags |= codec.FLAG_PAUSED
        if self._calibration:
            flags |= codec.FLAG_CALIBRATION
        return flags

    # ── painting ─────────────────────────────────────────────────────────
    def paintGL(self) -> None:  # QOpenGLWidget entry; QPainter uses GL engine
        self._ensure_zoom()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0))
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self._draw_ortho(p)
        if self._calibration:
            self._draw_calibration(p)
        if self._scale:
            self._draw_scale(p)
        self._draw_fiducials(p)
        self._draw_bar(p)
        if self._hud:
            self._draw_hud(p)
        p.end()

    # Reuse paintGL for the non-GL fallback widget.
    def paintEvent(self, event) -> None:  # type: ignore[override]
        if _HAVE_GL:
            return super().paintEvent(event)
        self.paintGL()

    def _draw_ortho(self, p: QPainter) -> None:
        z = self._zoom
        vw, vh = self.width(), self.height()
        self._seam_visible = False
        if z <= 0:
            return
        if self._is_vertical():
            W = self.ortho.width * z
            H = self.ortho.height * z
            if H <= 0:
                return
            x_base = (vw - W) / 2.0
            off = self._scroll % H
            start = (-off) if self.cfg.scroll_dir == "up" else (off - H)
            oy = (start % H) - H  # first copy origin at or above the top edge
            while oy < vh:
                self._blit_ortho_copy(p, x_base, oy, z)
                if 0.1 * vh < oy < 0.9 * vh:
                    self._seam_visible = True
                    p.fillRect(QRectF(0, oy - 1, vw, 2), QColor(255, 0, 255))
                oy += H
        else:
            W = self.ortho.width * z
            H = self.ortho.height * z
            if W <= 0:
                return
            y_base = (vh - H) / 2.0
            off = self._scroll % W
            start = (-off) if self.cfg.scroll_dir == "left" else (off - W)
            ox = (start % W) - W
            while ox < vw:
                self._blit_ortho_copy_h(p, ox, y_base, z)
                if 0.1 * vw < ox < 0.9 * vw:
                    self._seam_visible = True
                    p.fillRect(QRectF(ox - 1, 0, 2, vh), QColor(255, 0, 255))
                ox += W

    def _blit_ortho_copy(self, p: QPainter, x_base: float, oy: float, z: float) -> None:
        vw, vh = self.width(), self.height()
        for t in self.ortho.tiles:
            tx = x_base + t.x0 * z
            ty = oy + t.y0 * z
            tw = t.w * z
            th = t.h * z
            if ty + th < 0 or ty > vh or tx + tw < 0 or tx > vw:
                continue
            p.drawPixmap(QRectF(tx, ty, tw, th), t.pix, QRectF(t.pix.rect()))

    def _blit_ortho_copy_h(self, p: QPainter, ox: float, y_base: float, z: float) -> None:
        vw, vh = self.width(), self.height()
        for t in self.ortho.tiles:
            tx = ox + t.x0 * z
            ty = y_base + t.y0 * z
            tw = t.w * z
            th = t.h * z
            if ty + th < 0 or ty > vh or tx + tw < 0 or tx > vw:
                continue
            p.drawPixmap(QRectF(tx, ty, tw, th), t.pix, QRectF(t.pix.rect()))

    def _draw_fiducials(self, p: QPainter) -> None:
        s = self.cfg.fiducial_px
        m = self.cfg.fiducial_margin_px
        vw, vh = self.width(), self.height()
        # corners: TL=0, TR=1, BR=2, BL=3
        positions = [
            (m, m),
            (vw - m - s, m),
            (vw - m - s, vh - m - s),
            (m, vh - m - s),
        ]
        for mid, (x, y) in enumerate(positions):
            pm = self._aruco[mid] if mid < len(self._aruco) else None
            # white quiet-zone backing for reliable detection
            p.fillRect(QRectF(x - 6, y - 6, s + 12, s + 12), QColor(255, 255, 255))
            if pm is not None:
                p.drawPixmap(QRectF(x, y, s, s), pm, QRectF(pm.rect()))
            else:
                # fallback: distinct filled/hollow squares per corner
                p.fillRect(QRectF(x, y, s, s), QColor(0, 0, 0))
                inner = QColor(255, 255, 255)
                p.fillRect(QRectF(x + s * 0.2, y + s * 0.2, s * 0.6, s * 0.6), inner)
                p.fillRect(
                    QRectF(x + s * 0.35, y + s * 0.35, s * 0.3 * (1 + mid) / 4, s * 0.3),
                    QColor(0, 0, 0),
                )

    def _draw_bar(self, p: QPainter) -> None:
        gt = codec.GroundTruth(
            distance_mm=int(round(self._distance_m * 1000.0)),
            tick=self._tick,
            velocity_mmps=int(round(self._speed * 1000.0)),
            flags=self._current_flags(),
        )
        cells = codec.encode(gt)
        n = len(cells)
        vw = self.width()
        bh = self.cfg.bar_height_px
        margin = self.cfg.bar_margin_px
        s = self.cfg.fiducial_px
        fm = self.cfg.fiducial_margin_px
        # Share the fiducial row: sit the bar on the SAME line as the corner
        # markers, in the gap between them, vertically centred on the marker
        # band. Horizontally it clears each corner marker plus its 6 px quiet
        # zone and a side margin, so neither occludes the other. This reclaims
        # the ~fiducial-height stack the bar used to add below the markers.
        band_top = fm if self.cfg.bar_edge == "top" else self.height() - fm - s
        top = band_top + max(0, (s - bh) // 2)
        clear = fm + s + 6 + margin
        x0 = clear
        x1 = vw - clear
        band_w = x1 - x0
        if band_w <= 0:
            return
        # background + quiet zones (mid gray band, black frame)
        p.fillRect(QRectF(x0 - 12, top - 6, band_w + 24, bh + 12), QColor(128, 128, 128))
        cell_w = band_w / n
        for i, bit in enumerate(cells):
            col = QColor(255, 255, 255) if bit else QColor(0, 0, 0)
            p.fillRect(QRectF(x0 + i * cell_w, top, math.ceil(cell_w) + 1, bh), col)

    def _draw_hud(self, p: QPainter) -> None:
        s_gsd = screen_gsd_cm(self.cfg.ortho_gsd_cm, self._zoom)
        c_gsd = simulated_gsd_cm(self.cfg.ortho_gsd_cm, self._zoom, self.cfg.magnification)
        lines = [
            f"OrthoSim  (build {build_date()})",
            f"speed: {self._speed:6.1f} m/s{'  [PAUSED]' if self._paused else ''}",
            f"distance: {self._distance_m/1000.0:8.3f} km   tick: {self._tick}",
            f"zoom: {self._zoom:.4f} screen-px/ortho-px   ortho GSD: {self.cfg.ortho_gsd_cm:.3f} cm/px",
            f"screen GSD: {s_gsd:.4f} cm/screen-px",
            (
                f"sim camera GSD: {c_gsd:.4f} cm/cam-px (M={self.cfg.magnification:g})"
                if c_gsd > 0 else "sim camera GSD: set --M from fiducial calibration"
            ),
            "Up/Down speed  +/- zoom  Space pause  H hud  C calib  G scale  R reset  S save  Esc quit",
        ]
        p.setFont(QFont("Consolas", 12))
        fm = p.fontMetrics()
        w = max(fm.horizontalAdvance(s) for s in lines) + 24
        h = fm.height() * len(lines) + 20
        y0 = self.height() - h - 20
        p.fillRect(QRectF(16, y0, w, h), QColor(0, 0, 0, 170))
        p.setPen(QColor(0, 255, 128))
        y = y0 + 10 + fm.ascent()
        for s in lines:
            p.drawText(28, int(y), s)
            y += fm.height()

    def _draw_calibration(self, p: QPainter) -> None:
        vw, vh = self.width(), self.height()
        cx, cy = vw / 2, vh / 2
        # a 20x20 checkerboard of known screen size
        n = 20
        cell = min(vw, vh) / (n + 4)
        ox = cx - n * cell / 2
        oy = cy - n * cell / 2
        for r in range(n):
            for c in range(n):
                if (r + c) % 2 == 0:
                    p.fillRect(
                        QRectF(ox + c * cell, oy + r * cell, cell, cell),
                        QColor(255, 255, 255),
                    )
        # scale bar: 500 screen px, labelled with real length if pitch known
        bar_px = 500.0
        by = oy + n * cell + 30
        p.fillRect(QRectF(ox, by, bar_px, 10), QColor(255, 220, 0))
        p.setFont(QFont("Consolas", 14))
        p.setPen(QColor(255, 220, 0))
        if self.cfg.panel_pitch_mm > 0:
            label = f"{bar_px:.0f} screen px = {bar_px * self.cfg.panel_pitch_mm:.1f} mm"
        else:
            label = f"{bar_px:.0f} screen px (set --panel-pitch to label in mm)"
        p.drawText(int(ox), int(by + 34), label)

    def _draw_scale(self, p: QPainter) -> None:
        """Human-readable ground-distance ruler along the scroll axis.

        Ticks land on round ground distances and scroll locked to the imagery,
        so from the Iron661 image you can read cumulative distance / speed and
        measure camera-px per known ground distance (i.e. the *effective* GSD,
        which already includes screen distance + crop). High-contrast so the
        camera can resolve it even from a cropped view. Toggle with 'G'.
        """
        z = self._zoom
        gsd = self.cfg.ortho_gsd_cm
        if z <= 0 or gsd <= 0:
            return
        px_per_m = (100.0 / gsd) * z
        vertical = self._is_vertical()
        axis_len = self.height() if vertical else self.width()
        content_from_far = self.cfg.scroll_dir in ("up", "left")
        ticks = scale_ticks(
            self._distance_m, px_per_m, axis_len,
            float(self.cfg.scale_target_px), content_from_far,
        )
        if not ticks:
            return
        thick = 48
        p.setFont(QFont("Consolas", 11))
        ink = QColor(255, 255, 0)
        if vertical:
            p.fillRect(QRectF(0, 0, thick, self.height()), QColor(0, 0, 0, 170))
            for dist_m, c in ticks:
                if not (-2 <= c <= axis_len + 2):
                    continue
                p.fillRect(QRectF(0, c - 1, 30, 2), ink)
                p.setPen(ink)
                p.drawText(6, int(c) - 4, _fmt_dist(dist_m))
        else:
            by = self.height() - thick
            p.fillRect(QRectF(0, by, self.width(), thick), QColor(0, 0, 0, 170))
            for dist_m, c in ticks:
                if not (-2 <= c <= axis_len + 2):
                    continue
                p.fillRect(QRectF(c - 1, by, 2, 30), ink)
                p.setPen(ink)
                p.drawText(int(c) + 4, by + 44, _fmt_dist(dist_m))

    # ── input ────────────────────────────────────────────────────────────
    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        k = e.key()
        if k in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.window().close()
        elif k == Qt.Key.Key_Up:
            self._speed = min(self.cfg.max_speed_mps, self._speed + self.cfg.speed_step_mps)
        elif k == Qt.Key.Key_Down:
            self._speed = max(0.0, self._speed - self.cfg.speed_step_mps)
        elif k in (Qt.Key.Key_Plus, Qt.Key.Key_Equal, Qt.Key.Key_PageUp):
            self._zoom *= self.cfg.zoom_step
        elif k in (Qt.Key.Key_Minus, Qt.Key.Key_PageDown):
            self._zoom /= self.cfg.zoom_step
        elif k == Qt.Key.Key_Space:
            self._paused = not self._paused
        elif k == Qt.Key.Key_H:
            self._hud = not self._hud
        elif k == Qt.Key.Key_C:
            self._calibration = not self._calibration
        elif k == Qt.Key.Key_G:
            self._scale = not self._scale
        elif k == Qt.Key.Key_R:
            self._scroll = 0.0
            self._distance_m = 0.0
            self._tick = 0
        elif k == Qt.Key.Key_S:
            self._save_config()
        else:
            super().keyPressEvent(e)

    def _save_config(self) -> None:
        self.cfg.zoom = self._zoom
        self.cfg.ground_speed_mps = self._speed
        path = _TOOLS / "orthosim_config.json"
        try:
            self.cfg.to_json(path)
            print(f"[OrthoSim] saved config -> {path}")
        except OSError as exc:
            print(f"[OrthoSim] save config failed: {exc}")


class OrthoSimWindow(QMainWindow):
    def __init__(
        self,
        ortho: OrthoImage,
        cfg: OrthoSimConfig,
        logger: Optional[GroundTruthLogger],
    ) -> None:
        super().__init__()
        self._build_date = build_date()
        self.setWindowTitle(f"OrthoSim  (build {self._build_date})")
        self._view = OrthoView(ortho, cfg, logger, self)
        self.setCentralWidget(self._view)
        self._view.setFocus()


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="OrthoSim orthophoto screen bench rig")
    ap.add_argument("ortho", type=str, help="path to the orthophoto image")
    ap.add_argument("--gsd", type=float, default=None,
                    help="orthophoto ground sample distance (cm per ortho pixel)")
    ap.add_argument("--panel-pitch", type=float, default=None,
                    help="screen pixel pitch in mm (for calibration labels)")
    ap.add_argument("--M", "--magnification", dest="M", type=float, default=None,
                    help="camera-px per screen-px (from fiducial calibration)")
    ap.add_argument("--speed", type=float, default=None, help="initial ground speed m/s")
    ap.add_argument("--zoom", type=float, default=None,
                    help="screen-px per ortho-px (default: fit width)")
    ap.add_argument("--dir", dest="scroll_dir", choices=["up", "down", "left", "right"],
                    default=None, help="scroll direction (content flow)")
    ap.add_argument("--config", type=str, default=None, help="config JSON to load")
    ap.add_argument("--log", type=str, default=None,
                    help="ground-truth CSV output path (default: next to ortho)")
    ap.add_argument("--no-log", action="store_true", help="disable CSV ground-truth log")
    ap.add_argument("--scale", action="store_true",
                    help="start with the ground-distance ruler on (toggle: G)")
    ap.add_argument("--reduce", type=int, choices=[1, 2, 4, 8], default=1,
                    help="decode the ortho at 1/N resolution (much faster / less "
                         "RAM for gigapixel files); GSD is scaled by N so "
                         "calibration is unchanged")
    ap.add_argument("--windowed", action="store_true", help="windowed (not full-screen)")
    return ap.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)

    if args.config:
        cfg = OrthoSimConfig.from_json(Path(args.config))
    else:
        cfg = OrthoSimConfig()
    if args.gsd is not None:
        cfg.ortho_gsd_cm = args.gsd
    if args.panel_pitch is not None:
        cfg.panel_pitch_mm = args.panel_pitch
    if args.M is not None:
        cfg.magnification = args.M
    if args.speed is not None:
        cfg.ground_speed_mps = args.speed
    if args.zoom is not None:
        cfg.zoom = args.zoom
    if args.scroll_dir is not None:
        cfg.scroll_dir = args.scroll_dir
    if args.scale:
        cfg.scale_enabled = True
    if args.reduce > 1:
        # 1/N decode -> each ortho pixel now covers N x the ground; scale GSD to
        # match so distance/scale/HUD stay correct.
        cfg.ortho_gsd_cm *= args.reduce

    app = QApplication(sys.argv)

    ortho_path = Path(args.ortho)
    print(f"[OrthoSim] loading {ortho_path} (reduce=1/{args.reduce}) ...", flush=True)
    ortho = OrthoImage(ortho_path, reduce=args.reduce)
    print(f"[OrthoSim] loaded {ortho.width}x{ortho.height} px "
          f"({len(ortho.tiles)} tiles)", flush=True)

    logger: Optional[GroundTruthLogger] = None
    if not args.no_log and cfg.csv_log_hz > 0:
        log_path = Path(args.log) if args.log else ortho_path.with_suffix(".groundtruth.csv")
        logger = GroundTruthLogger(log_path, cfg.csv_log_hz)
        logger.open()
        print(f"[OrthoSim] ground-truth CSV -> {log_path}")

    win = OrthoSimWindow(ortho, cfg, logger)
    if args.windowed:
        # Fit the window inside the available screen and pin it to the top-left.
        # A fixed 1280x800 on a smaller/virtual display (e.g. an 800x800 remote
        # session) overflows and lands mostly off-screen, so it looks like
        # nothing launched even though paintGL is happy.
        scr = app.primaryScreen().availableGeometry()
        win.resize(min(1280, scr.width()), min(800, scr.height()))
        win.move(scr.x(), scr.y())
        win.show()
        win.raise_()
        win.activateWindow()
    else:
        win.showFullScreen()
        win.setCursor(Qt.CursorShape.BlankCursor)
    print(f"[OrthoSim] window shown, geometry={win.geometry().getRect()}, "
          f"visible={win.isVisible()}", flush=True)

    try:
        rc = app.exec()
    finally:
        if logger is not None:
            logger.close()
    return int(rc)


if __name__ == "__main__":
    raise SystemExit(main())
