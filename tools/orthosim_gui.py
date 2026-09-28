r"""OrthoSim -- orthophoto screen bench rig display app.

Full-screen PySide6 app that advances a high-resolution orthophoto past a
stationary Iron661 camera at a calibrated, keyboard-controllable speed (and
zoom = simulated height) to simulate flight. It renders:

* the scrolling orthophoto (GPU-accelerated via a QOpenGLWidget viewport),
* four ArUco corner fiducials so the capture side can homography-crop the
  screen quad and recover the screen->camera magnification,
* a high-contrast optical ground-truth bar (absolute distance + tick +
  velocity, see ``orthosim_codec``) that the Iron661 capture app decodes,
* an on-screen HUD (toggleable) with speed / GSD / distance.

A CSV of the same ground-truth is written alongside as a cross-check.

The scene is STEPPED, not scrolled: it holds one position until the capture app
reports a captured frame (loopback UDP, see ``orthosim_codec.STEP_PORT``), then
advances by the interval that frame took. Every exposure therefore lands on a
settled panel, which is what removes the one-refresh overdrive ghost that a
continuously scrolling panel showed on moving scenes and never on static ones.
With no capture app running the scene stands still and the HUD says so.

Run from source::

    py -3 tools/orthosim_gui.py PATH\TO\ortho.tif --gsd 5.0
    py -3 tools/orthosim_gui.py ortho.png --gsd 4.0 --panel-pitch 0.181 --M 1.0
    py -3 tools/orthosim_gui.py ortho.png --gsd 5 --windowed   # for bring-up

Controls: Up/Down = +/- speed, +/- or PageUp/PageDown = zoom (height),
Space = pause, H = HUD, A = alignment pattern + tilt readout, C = calibration
overlay, R = reset distance, S = save config, Esc/Q = quit.
"""

from __future__ import annotations

import argparse
import math
import os
import queue
import socket
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
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

# Before cv2, because it reads this once at import and never again. OpenCV refuses any
# image over 2**30 pixels as a decompression-bomb guard, and this tool exists to display
# gigapixel orthophotos: Bnei Brak is 1.17 G, Pardes Hana 2.02 G, so the guard rejects the
# only inputs the program has. It is NOT a memory limit -- `--reduce` is what bounds RAM,
# and the guard fires on the file's header size regardless of how little we decode. Left
# unset it works only from a shell that happens to export the variable, which is a trap
# that reads as a corrupt file ("Assertion failed: pixels <= CV_IO_MAX_IMAGE_PIXELS").
os.environ.setdefault("OPENCV_IO_MAX_IMAGE_PIXELS", str(1 << 33))  # 8.6 G px

import cv2

_TOOLS = Path(__file__).resolve().parent
_ROOT = _TOOLS.parent
for _p in (str(_TOOLS), str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orthosim_codec as codec  # noqa: E402
from orthosim_core import (  # noqa: E402
    free_run_dt,
    GroundTruthLogger,
    OrthoSimConfig,
    scale_ticks,
    scroll_speed_screen_px_s,
    screen_gsd_cm,
    simulated_gsd_cm,
    step_dt,
)

_TILE_MAX = 4096  # max pixmap tile edge (safe below GPU texture limits)

# Bubble-level scaling. Full scale is 2 deg because the tilt actually measured on the
# rig was ~1.9 deg, so the dot starts on the dial rather than pinned to the rim. The
# inner ring is the acceptance threshold: under it the residual focus ramp across a
# frame is ~0.1 depth of field, i.e. no longer the thing limiting the field map.
_ALIGN_SCALE_DEG = 2.0
_ALIGN_OK_DEG = 0.36


# ── Frame-step sync ──────────────────────────────────────────────────────────
class StepListener(threading.Thread):
    """Receives per-frame step nudges from the capture app on loopback UDP.

    A plain socket on a daemon thread rather than ``QUdpSocket``: the frozen
    build (``OrthoSim.spec``) would otherwise need QtNetwork pulled in as a
    hidden import, and this costs the same eight lines without the packaging
    risk. A ``SimpleQueue`` rather than an integer counter because the GUI
    thread reads-and-zeroes while this thread increments, and ``+=`` is not
    atomic -- the queue makes a dropped step impossible instead of merely
    unlikely, for the same amount of code.
    """

    def __init__(self, host: str = codec.STEP_HOST, port: int = codec.STEP_PORT) -> None:
        super().__init__(daemon=True, name="orthosim-step")
        self.nudges: "queue.SimpleQueue[float]" = queue.SimpleQueue()
        # Lockstep requests are a QUEUE, not a latest-wins attribute, because a repeat
        # of a seq is a meaningful EVENT: it means the app's READY went missing and it
        # is asking again. Collapsing it would leave the app waiting for an answer that
        # the display already considers delivered, i.e. a deadlock.
        self.lockstep: "queue.SimpleQueue[tuple]" = queue.SimpleQueue()
        self.received = 0
        self.error: Optional[str] = None
        # Latest screen-tilt reading, plus when it landed. A plain attribute rather
        # than a queue: unlike a step, a stale angle is worthless, so the GUI only
        # ever wants the newest one and dropping the rest is the correct behaviour.
        self.align: Optional[codec.AlignReading] = None
        self.align_t = 0.0
        self._sock: Optional[socket.socket] = None
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Deliberately NO SO_REUSEADDR. On Windows it lets a second socket bind
            # this same UDP port, and the two then split the datagrams arbitrarily --
            # a zombie OrthoSim would quietly steal half the steps and the live one
            # would crawl. UDP has no TIME_WAIT, so an honest bind always succeeds
            # after a clean or unclean exit; a failure here means something really is
            # still holding the port, which is worth being told about.
            sock.bind((host, port))
        except OSError as exc:
            self.error = f"step port {host}:{port} unavailable ({exc}) -- already running?"
            print(f"[OrthoSim] {self.error}", flush=True)
            sock.close()
            return
        self._sock = sock

    def run(self) -> None:
        if self._sock is None:
            return
        while True:
            try:
                data, _ = self._sock.recvfrom(128)
            except OSError:      # socket closed on shutdown
                return
            # Two message types share the port. Check the prefixed one first, since
            # the bare float is the fallback that swallows anything numeric.
            reading = codec.decode_align(data)
            if reading is not None:
                self.align = reading
                self.align_t = time.monotonic()
                continue
            request = codec.decode_step(data)
            if request is not None:
                self.received += 1
                self.lockstep.put(request)
                continue
            try:
                dt = float(data)
            except ValueError:   # someone else on the port; not ours, ignore
                continue
            self.received += 1
            self.nudges.put(dt)

    def drain(self) -> List[float]:
        """Every open-loop nudge that arrived since the last call."""
        return _drain(self.nudges)

    def drain_lockstep(self) -> List[tuple]:
        """Every lockstep ``(seq, dt)`` request that arrived since the last call."""
        return _drain(self.lockstep)


def _drain(q: "queue.SimpleQueue") -> list:
    out: list = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


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
        # Normally on -- they are how the capture side recovers M and reads ground
        # truth. Off only for the blank-panel control; see paintGL.
        self._overlays = not bool(getattr(cfg, "no_overlays", False))
        self._seam_visible = False
        self._align_mode = bool(getattr(cfg, "align_mode", False))
        self._align_pixmap: Optional[QPixmap] = None

        self._aruco = make_aruco_pixmaps(cfg.aruco_dict, cfg.fiducial_px)

        # The scene advances on camera frames, not on wall-clock time, so the
        # simulation runs on a virtual clock fed by this listener. Started before
        # the repaint timer so a nudge arriving during bring-up is queued, not lost.
        self._steps = StepListener()
        self._steps.start()
        self._steps_taken = 0

        # Lockstep state. _ls_seq is the request being served; it is kept AFTER the
        # answer goes out so a re-sent STEP is recognised as a duplicate rather than
        # advancing the scene a second time.
        self._ls_seq: Optional[int] = None
        self._ls_ready_at: Optional[float] = None    # monotonic deadline, None = nothing due
        self._ls_awaiting_paint = False
        self._ls_answered = 0
        self._settle_s = max(0.0, float(getattr(cfg, "settle_ms", codec.SETTLE_MS_DEFAULT))) / 1000.0

        # Standalone scrolling, for a display with no capture app to step it (the
        # step channel is loopback-only, so a capture app on another machine cannot
        # reach it either). Off by default: on the bench the camera owns the motion.
        self._free_run = bool(getattr(cfg, "free_run", False))
        self._free_run_t = time.monotonic()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        # Still ~120 Hz, but now only to repaint (HUD, key presses, pause state).
        # Motion happens on nudges, so tick jitter cannot affect the geometry.
        self._timer.start(8)

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
        self._ensure_zoom()
        # Step-and-hold: the scene is motionless between camera frames, so every
        # 250 us exposure lands on a settled panel. Advancing by the interval the
        # capture app measured (rather than a configured rate) keeps the simulated
        # aircraft honest when a frame is late or conflated -- it covers that ground
        # instead of quietly falling behind its own ground-truth log.
        # Closed loop first. It is a no-op unless the app is actually sending STEP, so
        # the two channels coexist and the display needs no mode of its own -- whichever
        # the app speaks drives the scene, and there is no flag to fall out of sync.
        self._lockstep_tick()
        dt = step_dt(self._steps.drain(), self._paused)
        if self._free_run:
            # The drain above still ran, so a stray nudge is discarded rather than
            # left to bank a backlog; in free run the wall clock is the motion.
            dt = self._free_run_tick()
        if dt > 0:
            self._advance_scene(dt)
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

    def _free_run_tick(self) -> float:
        """Simulated seconds from the repaint clock, for standalone scrolling.

        The timestamp advances even while paused, so that un-pausing resumes rather
        than replaying the paused interval as one jump.
        """
        now = time.monotonic()
        dt = free_run_dt(now - self._free_run_t, self._paused)
        self._free_run_t = now
        return dt

    def _advance_scene(self, dt: float) -> None:
        """Move the scene by ``dt`` simulated seconds. The one place motion happens."""
        self._speed = max(0.0, min(self.cfg.max_speed_mps, self._speed))
        self._distance_m += self._speed * dt
        v_px = scroll_speed_screen_px_s(self._speed, self.cfg.ortho_gsd_cm, self._zoom)
        self._scroll += v_px * dt
        # One tick per camera frame, so the tick in the optical bar IS the frame index:
        # the capture side can tie a frame to a scene position by reading it out of the
        # image, with no clock to correlate.
        self._tick = (self._tick + 1) % (1 << 20)
        self._steps_taken += 1

    # ── lockstep handshake ───────────────────────────────────────────────
    def _lockstep_tick(self) -> None:
        """Advance on STEP, answer READY once the panel has settled.

        The settle window is started by ``paintGL``, not here. Starting it at the
        advance would have it overlap the wait for Qt to actually paint, and the whole
        point of the margin is to cover what the PANEL does after it receives the new
        frame -- charging the wrong side of the swap would quietly shorten it.
        """
        for seq, dt in self._steps.drain_lockstep():
            if seq == self._ls_seq:
                # A re-send: our READY went missing, not their STEP. Answer again
                # without advancing -- a duplicate must never double-step the scene.
                if self._ls_ready_at is None:
                    self._ls_ready_at = 0.0     # already settled; answer on this tick
                continue
            # Paused forfeits the ground rather than banking it, but must still be
            # ANSWERED: a display that goes quiet while paused deadlocks the app.
            if not self._paused:
                self._advance_scene(dt)
            self._ls_seq = seq
            self._ls_awaiting_paint = True
            self._ls_ready_at = None
        if self._ls_ready_at is not None and time.monotonic() >= self._ls_ready_at:
            self._send_ready(self._ls_seq)
            self._ls_ready_at = None
            self._ls_answered += 1

    def _send_ready(self, seq: int) -> None:
        """Tell the app the picture for ``seq`` is up and settled. Never blocks/raises."""
        sock = getattr(self, "_ls_sock", None)
        if sock is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setblocking(False)
            self._ls_sock = sock
        try:
            sock.sendto(codec.encode_ready(seq), (codec.STEP_HOST, codec.READY_PORT))
        except OSError:
            pass    # app down or buffer full; its timeout is the recovery path

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
        if self._align_mode:
            # The pattern replaces the scene rather than overlaying it: the capture
            # side localises corners sub-pixel, and imagery showing through would
            # only add texture for the detector to reject.
            self._draw_alignment_pattern(p)
        else:
            self._draw_ortho(p)
            if self._calibration:
                self._draw_calibration(p)
            if self._scale and self._overlays:
                self._draw_scale(p)
        # Every overlay here is high-contrast BY DESIGN -- fiducials so detection is
        # reliable, the bar so the capture side can decode it, the ruler explicitly
        # "so the camera can resolve it". On the blank-panel AF control they would be
        # the only content in an otherwise uniform frame and the patch picker would go
        # straight for them, so this suppresses the set rather than one at a time.
        if self._overlays:
            self._draw_fiducials(p)
            self._draw_bar(p)
        if self._align_mode:
            self._draw_align_hud(p)
        if self._hud and self._overlays:
            self._draw_hud(p)
        p.end()
        # The new scene position has now been handed to the compositor, so this is
        # where the panel's own transition starts and where the settle margin has to
        # be measured from. Timing it from the advance instead would spend part of the
        # margin waiting for Qt to paint and hand the camera a panel still in motion.
        if self._ls_awaiting_paint:
            self._ls_awaiting_paint = False
            self._ls_ready_at = time.monotonic() + self._settle_s

    def _draw_alignment_pattern(self, p: QPainter) -> None:
        """Static ChArUco board, blitted 1:1, for the capture app to measure tilt off.

        Two details carry the whole thing:

        * **1:1, smoothing off.** Any resample rounds the square edges, and the edge
          IS the measurement -- corner localisation is what buys the 0.02 deg. Drawn
          at its native size and centred; if it does not fit the window it is clipped,
          which costs coverage but never accuracy.
        * **Centred, not inset below the bar.** ``paintGL`` draws the fiducials and the
          bar after this, and both lay down their own opaque backing, so they occlude a
          handful of the board's markers instead of being occluded by it -- the bar
          stays decodable without giving up any board. Reserving their band at both
          ends would clip ~14% of the rows, and it is the outermost rows that carry
          most of the keystone signal, since tilt sensitivity grows with field angle.
          ChArUco identifies every corner by marker ID, so losing the covered ones
          costs a little precision and nothing else. On a panel smaller than the board
          the edges clip, which costs coverage the same way and is reported back as a
          lower corner count.
        """
        if self._align_pixmap is None:
            b = codec.charuco_board()
            nx, ny = codec.ALIGN_SQUARES
            img = np.ascontiguousarray(
                b.generateImage((nx * codec.ALIGN_SQUARE_PX,
                                 ny * codec.ALIGN_SQUARE_PX)))
            h, w = img.shape[:2]
            self._align_pixmap = QPixmap.fromImage(
                QImage(img.data, w, h, w, QImage.Format.Format_Grayscale8).copy())
        pm = self._align_pixmap
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        x = (self.width() - pm.width()) / 2.0
        y = (self.height() - pm.height()) / 2.0
        p.drawPixmap(QRectF(x, y, pm.width(), pm.height()), pm, QRectF(pm.rect()))
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

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
        # Simulated camera GSD (cm/cam-px) is the optics-independent "altitude"
        # proxy; encode it in 0.01 cm units. Returns 0 when M is uncalibrated, which
        # the decoder surfaces as "GSD unset".
        gsd_cm = simulated_gsd_cm(
            self.cfg.ortho_gsd_cm, self._zoom, self.cfg.magnification
        )
        gt = codec.GroundTruth(
            distance_mm=int(round(self._distance_m * 1000.0)),
            tick=self._tick,
            velocity_mmps=int(round(self._speed * 1000.0)),
            gsd_cm_e2=int(round(max(0.0, gsd_cm) * 100.0)),
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
            # Spelled out because a stepped rig with no camera attached looks
            # broken rather than idle: nothing moves and there is no other clue.
            (
                f"STEP CHANNEL DOWN -- {self._steps.error}"
                if self._steps.error
                # Lockstep is worth calling out rather than folding into the step
                # count: "stepped by camera" is true in both, but only here is the
                # panel guaranteed static through the exposure, and if the settle
                # margin is what someone is tuning they need to see it on the screen.
                else f"LOCKSTEP: {self._steps_taken} frames, "
                     f"{self._ls_answered} answered, settle {self._settle_s*1000:.0f} ms"
                if self._ls_seq is not None
                else f"stepped by camera (open loop): {self._steps_taken} frames"
                if self._steps.received
                else "FREE RUN -- scrolling on the repaint clock, not stepped by "
                     "the camera. NOT for a measurement run"
                if self._free_run
                else "WAITING FOR CAMERA -- scene steps on captured frames, "
                     "not on a timer"
            ),
            f"zoom: {self._zoom:.4f} screen-px/ortho-px   ortho GSD: {self.cfg.ortho_gsd_cm:.3f} cm/px",
            f"screen GSD: {s_gsd:.4f} cm/screen-px",
            (
                f"sim camera GSD: {c_gsd:.4f} cm/cam-px (M={self.cfg.magnification:g})"
                if c_gsd > 0 else "sim camera GSD: set --M from fiducial calibration"
            ),
            "Up/Down speed  +/- zoom  Space pause  H hud  A align  C calib  G scale  "
            "R reset  M screen  S save  Esc quit",
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

    def _draw_align_hud(self, p: QPainter) -> None:
        """Bubble level plus signed degrees, for an operator with both hands on the rig.

        A bubble is the primary readout because it has no axis label to get backwards.
        Which angle is "pitch" depends on a camera roll that is ~90 deg on this rig and
        could change the next time it is remounted; "drive the dot to the middle" does
        not. The numbers are there for the record and for judging how close is close.

        Drawn over the CENTRE of the pattern on purpose. It has to be read from across
        the room while the platform is being adjusted, and the centre is the cheapest
        place to occlude: tilt sensitivity grows with field angle, so the corners it
        covers are the ones contributing least.
        """
        vw, vh = self.width(), self.height()
        r = self._steps.align
        age = time.monotonic() - self._steps.align_t if r is not None else 0.0
        stale = r is None or age > 2.0
        if stale:
            ink = QColor(105, 105, 105)
        elif r.magnitude_deg <= _ALIGN_OK_DEG:
            ink = QColor(0, 235, 110)
        elif r.magnitude_deg <= 1.0:
            ink = QColor(240, 190, 40)
        else:
            ink = QColor(240, 80, 60)

        big, small = QFont("Consolas", 64), QFont("Consolas", 22)
        headline = ("waiting for the capture app -- press Align" if r is None else
                    f"v {r.tilt_v_deg:+6.2f}\u00b0  h {r.tilt_h_deg:+6.2f}\u00b0")
        if r is None:
            big = QFont("Consolas", 34)
        # Corner count rides along so a degraded reading can never pass for a good one:
        # the same angle off 40 corners is worth roughly 6x less than off 312.
        note = ("" if r is None else
                f"STALE {age:.0f} s -- capture app stopped sending" if stale else
                f"{r.n_corners} corners   {r.distance_mm / 1000.0:.2f} m standoff")

        # Size the panel from the text rather than from guessed fractions of R: at 64 pt
        # the headline is ~1600 px wide on the 4K panel, so a box scaled to the dial
        # alone would spill the numbers across the pattern.
        R = min(vw, vh) * 0.13
        p.setFont(big)
        fm_big = p.fontMetrics()
        p.setFont(small)
        fm_small = p.fontMetrics()
        pad = 28
        text_h = fm_big.height() + (fm_small.height() if note else 0)
        box_w = max(2 * R + 4 * pad, fm_big.horizontalAdvance(headline) + 2 * pad,
                    (fm_small.horizontalAdvance(note) + 2 * pad) if note else 0)
        box_h = 2 * R + text_h + 3 * pad
        cx = vw / 2.0
        box_x, box_y = cx - box_w / 2.0, vh / 2.0 - box_h / 2.0
        # Opaque, not translucent: pattern showing through halves the contrast of the
        # one thing being read from across the room, and the squares under the panel
        # are lost to the detector either way.
        p.fillRect(QRectF(box_x, box_y, box_w, box_h), QColor(0, 0, 0))
        p.setPen(ink)
        p.drawRect(QRectF(box_x, box_y, box_w, box_h))

        # The dial. Inner ring is the acceptance threshold, outer is full scale.
        cy = box_y + pad + R
        for frac, col, label in ((_ALIGN_OK_DEG / _ALIGN_SCALE_DEG,
                                  QColor(0, 200, 90), f"{_ALIGN_OK_DEG:g}\u00b0"),
                                 (1.0, QColor(120, 120, 120),
                                  f"{_ALIGN_SCALE_DEG:g}\u00b0")):
            p.setPen(ink if stale else col)
            p.drawEllipse(QRectF(cx - R * frac, cy - R * frac,
                                 2 * R * frac, 2 * R * frac))
            p.setFont(QFont("Consolas", 14))
            p.drawText(int(cx + R * frac + 6), int(cy - 6), label)
        p.setPen(QColor(105, 105, 105) if stale else QColor(150, 150, 150))
        p.drawLine(int(cx - R), int(cy), int(cx + R), int(cy))
        p.drawLine(int(cx), int(cy - R), int(cx), int(cy + R))

        if r is not None:
            # The dot moves the way the panel is tipping: the screen's vertical axis
            # tipping in depth drives it up/down, the horizontal axis left/right. The
            # SIGNS are deliberately unverified against the rig -- nulling a bubble
            # does not need them, and asserting one would be asserting how the camera
            # happens to be mounted today.
            k = R / _ALIGN_SCALE_DEG
            dx = max(-R, min(R, r.tilt_h_deg * k))
            dy = max(-R, min(R, r.tilt_v_deg * k))
            p.setBrush(ink)
            p.setPen(ink)
            p.drawEllipse(QRectF(cx + dx - 15, cy + dy - 15, 30, 30))
            p.setBrush(Qt.BrushStyle.NoBrush)

        y = cy + R + pad + fm_big.ascent()
        p.setFont(big)
        p.setPen(QColor(200, 200, 60) if r is None else ink)
        p.drawText(int(cx - fm_big.horizontalAdvance(headline) / 2), int(y), headline)
        if note:
            p.setFont(small)
            p.setPen(QColor(150, 150, 150) if stale else QColor(185, 185, 185))
            p.drawText(int(cx - fm_small.horizontalAdvance(note) / 2),
                       int(y + fm_small.height()), note)

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
        elif k == Qt.Key.Key_A:
            self._align_mode = not self._align_mode
        elif k == Qt.Key.Key_G:
            self._scale = not self._scale
        elif k == Qt.Key.Key_F:
            self._overlays = not self._overlays
        elif k == Qt.Key.Key_R:
            self._scroll = 0.0
            self._distance_m = 0.0
            self._tick = 0
            self._steps_taken = 0
        elif k == Qt.Key.Key_S:
            self._save_config()
        elif k == Qt.Key.Key_M:
            self._cycle_screen()
        else:
            super().keyPressEvent(e)

    def _cycle_screen(self) -> None:
        """Hop the window to the next monitor.

        ``--screen`` is decided once at startup and silently falls through to the
        primary if the target panel was asleep at that moment, which lands the rig on
        a 1920x1080 Dell instead of the 4K screen and quietly breaks every
        layout-dependent decode. This is the recovery that doesn't cost a
        1.17-gigapixel reload."""
        win = self.window()
        screens = QGuiApplication.screens()
        if len(screens) < 2:
            print("[OrthoSim] only one screen attached", flush=True)
            return
        i, scr = next_screen(screens, win.screen())
        show_on_screen(win, scr)
        print(f"[OrthoSim] moved to screen {i} {scr.name()!r}, "
              f"geometry={win.geometry().getRect()}", flush=True)

    def _save_config(self) -> None:
        self.cfg.zoom = self._zoom
        self.cfg.ground_speed_mps = self._speed
        path = _TOOLS / "orthosim_config.json"
        try:
            self.cfg.to_json(path)
            print(f"[OrthoSim] saved config -> {path}")
        except OSError as exc:
            print(f"[OrthoSim] save config failed: {exc}")


def next_screen(screens: list, current) -> Tuple[int, object]:
    """The screen after ``current``, wrapping. Falls back to the first when the
    window's screen isn't in the list -- Qt drops a monitor from ``screens()`` when
    it sleeps, which is exactly when you want the key that gets you off it."""
    i = screens.index(current) if current in screens else -1
    j = (i + 1) % len(screens)
    return j, screens[j]


def show_on_screen(win, scr) -> None:
    """Put ``win`` full-screen on ``scr``.

    Move BEFORE going full-screen so Qt picks the target monitor, and drop out of
    full-screen first because Windows ignores move() on a window that is already in
    it. setScreen then pins the choice on platforms that would otherwise re-enter
    full-screen on the window's original monitor."""
    if win.isFullScreen():
        win.showNormal()
    win.move(scr.geometry().topLeft())
    win.showFullScreen()
    wh = win.windowHandle()
    if wh is not None:
        wh.setScreen(scr)


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
    ap.add_argument("--settle-ms", type=int, default=codec.SETTLE_MS_DEFAULT,
                    help="lockstep: ms to wait after painting before reporting READY, "
                         "covering the PANEL's own transition (the artefact being "
                         "removed). Only used when the capture app drives the closed "
                         "loop; ignored on the open-loop nudge channel")
    ap.add_argument("--free-run", action="store_true",
                    help="scroll on the repaint clock instead of waiting for the "
                         "capture app to step the scene (Space pauses/resumes). For "
                         "standalone viewing and bring-up -- the panel then moves "
                         "during the exposure, so do NOT use it for a measurement run")
    ap.add_argument("--config", type=str, default=None, help="config JSON to load")
    ap.add_argument("--log", type=str, default=None,
                    help="ground-truth CSV output path (default: next to ortho)")
    ap.add_argument("--no-log", action="store_true", help="disable CSV ground-truth log")
    ap.add_argument("--scale", action="store_true",
                    help="start with the ground-distance ruler on (toggle: G)")
    ap.add_argument("--no-overlays", action="store_true",
                    help="draw the scene ONLY -- no fiducials, ground-truth bar, "
                         "ruler or HUD (toggle: F). For the blank-panel AF control, "
                         "where those would be the only content in frame")
    ap.add_argument("--reduce", type=int, choices=[1, 2, 4, 8], default=1,
                    help="decode the ortho at 1/N resolution (much faster / less "
                         "RAM for gigapixel files); GSD is scaled by N so "
                         "calibration is unchanged")
    ap.add_argument("--windowed", action="store_true", help="windowed (not full-screen)")
    ap.add_argument("--screen", type=int, default=None,
                    help="monitor index for full-screen (QApplication.screens() order)")
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
    # Not a declared OrthoSimConfig field on purpose: it is a rig-tuning knob set per
    # launch, and adding it would put it in the saved config JSON as if it were a
    # property of the scene.
    cfg.settle_ms = args.settle_ms
    cfg.free_run = args.free_run          # same reasoning: a per-launch rig knob
    if args.speed is not None:
        cfg.ground_speed_mps = args.speed
    if args.zoom is not None:
        cfg.zoom = args.zoom
    if args.scroll_dir is not None:
        cfg.scroll_dir = args.scroll_dir
    if args.scale:
        cfg.scale_enabled = True
    if args.no_overlays:
        cfg.no_overlays = True
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

    if args.free_run:
        print("[OrthoSim] free run: scene scrolls on the repaint clock; Space "
              "pauses. Not stepped by the camera.", flush=True)

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
        screens = app.screens()
        if args.screen is not None and 0 <= args.screen < len(screens):
            show_on_screen(win, screens[args.screen])
        else:
            if args.screen is not None:
                # Almost always a sleeping monitor: Qt hasn't enumerated it yet, so
                # the index is out of range and we'd land on the primary without
                # saying so. Press M once the panel wakes.
                print(f"[OrthoSim] --screen {args.screen} out of range "
                      f"({len(screens)} attached); using primary -- press M to move",
                      flush=True)
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
