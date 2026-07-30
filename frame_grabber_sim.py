"""Simulated frame grabber for development without physical hardware.

Generates synthetic images with regions of varying sharpness so the
tile-based focus algorithm can be tested end-to-end.
"""

import logging
import math
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np

log = logging.getLogger(__name__)

IRON661_WIDTH = 13400
IRON661_HEIGHT = 9528
DEFAULT_FPS = 2.0


def _generate_test_frame(
    width: int = IRON661_WIDTH,
    height: int = IRON661_HEIGHT,
    sharp_seed: Optional[int] = None,
) -> np.ndarray:
    """Create a grayscale frame with a mix of sharp and blurry regions.

    A random rectangular patch contains high-frequency detail (sharp edges),
    while the rest is smoothed to simulate out-of-focus background.
    """
    rng = np.random.default_rng(sharp_seed)

    base = rng.integers(60, 180, size=(height // 8, width // 8), dtype=np.uint8)
    frame = cv2.resize(base, (width, height), interpolation=cv2.INTER_LINEAR)

    # Blur everything to simulate poor focus
    frame = cv2.GaussianBlur(frame, (31, 31), 10)

    # Place 2-4 sharp patches at random locations
    n_patches = rng.integers(2, 5)
    for _ in range(n_patches):
        pw = rng.integers(width // 10, width // 4)
        ph = rng.integers(height // 10, height // 4)
        px = rng.integers(0, width - pw)
        py = rng.integers(0, height - ph)

        patch = rng.integers(0, 255, size=(ph, pw), dtype=np.uint8)
        # Add strong edges: random rectangles and lines
        for __ in range(20):
            x1, y1 = rng.integers(0, pw), rng.integers(0, ph)
            x2, y2 = rng.integers(0, pw), rng.integers(0, ph)
            color = int(rng.integers(0, 255))
            thickness = int(rng.integers(1, 4))
            cv2.line(patch, (x1, y1), (x2, y2), color, thickness)
        for __ in range(10):
            x1, y1 = rng.integers(0, pw), rng.integers(0, ph)
            x2, y2 = rng.integers(0, pw), rng.integers(0, ph)
            color = int(rng.integers(0, 255))
            cv2.rectangle(patch, (x1, y1), (x2, y2), color, -1)

        frame[py : py + ph, px : px + pw] = patch

    return frame


class FocusSceneSimulator:
    """Closed-loop software focus rig for autofocus testing (no hardware).

    Renders a textured scene whose **blur is a function of a simulated focus
    position**, pans a window across it according to a **motion profile**, and
    accepts focus pulses through :meth:`apply_pulse` (with a backlash model). Feed
    the rendered frames to :class:`capture.autofocus.AutoFocusEngine` and connect
    its pulse callback to :meth:`apply_pulse` to close the loop entirely in
    software.

    Motion profiles (mirroring the ground-test mirror rig):

    * ``"translate"`` -- steady pan (flight-like leading/trailing edge flow).
    * ``"oscillate"`` -- left<->right sweep (the stationary nadir camera + mirror).
    * ``"circle"``    -- the window orbits; near the top of the circle the view is
      sky-only (low texture), to exercise the pause/resume behaviour.

    The optical sharpness is maximised when the simulated focus position equals
    ``best_focus_pulses``; ``blur_per_pulse`` sets how fast it defocuses away from
    that optimum.
    """

    def __init__(
        self,
        width: int = 1600,
        height: int = 1200,
        *,
        profile: str = "translate",
        start_focus_pulses: float = 20.0,
        best_focus_pulses: float = 0.0,
        blur_per_pulse: float = 0.16,
        base_blur: float = 0.5,
        backlash_pulses: int = 2,
        step_px: float = 16.0,
        period: int = 40,
        seed: int = 0,
        sky_value: int = 70,
    ) -> None:
        self.fw = int(width)
        self.fh = int(height)
        self.profile = profile
        self.best_focus = float(best_focus_pulses)
        self.blur_per_pulse = float(blur_per_pulse)
        self.base_blur = float(base_blur)
        self.backlash = int(backlash_pulses)
        self.step_px = float(step_px)
        self.period = int(period)
        self.sky_value = int(sky_value)

        # Open-loop focus bookkeeping with a backlash band.
        self._carrier = float(start_focus_pulses)  # commanded position
        self._optical = float(start_focus_pulses)  # actual optical position
        self._t = 0

        rng = np.random.default_rng(seed)
        # A textured canvas larger than the frame so the window can pan within it.
        margin = int(self.step_px * (period + 4)) + max(self.fw, self.fh)
        cw = self.fw + 2 * margin
        ch = self.fh + 2 * margin
        base = rng.integers(20, 235, size=(ch // 2, cw // 2), dtype=np.uint8)
        canvas = cv2.resize(base, (cw, ch), interpolation=cv2.INTER_LINEAR)
        for _ in range(400):
            x1, y1 = int(rng.integers(0, cw)), int(rng.integers(0, ch))
            x2, y2 = int(rng.integers(0, cw)), int(rng.integers(0, ch))
            cv2.line(canvas, (x1, y1), (x2, y2), int(rng.integers(0, 255)),
                     int(rng.integers(1, 3)))
        self._canvas = canvas
        self._cw, self._ch = cw, ch
        self._cx0 = margin  # base window origin (centre of travel)
        self._cy0 = margin

    # ── focus actuation (pulse callback for the AF engine) ───────────────

    def apply_pulse(self, sym: str, count: int) -> None:
        """Apply ``count`` focus pulses (``'+'``/``'-'``). Backlash band of
        ``backlash_pulses`` must be traversed before the optics move on a
        reversal."""
        d = 1.0 if sym == "+" else -1.0
        self._carrier += d * float(int(count))
        lo = self._carrier - self.backlash
        hi = self._carrier + self.backlash
        # Optics drag along only when the carrier pushes past the lash band.
        self._optical = min(max(self._optical, lo), hi)

    @property
    def optical_pulses(self) -> float:
        return self._optical

    @property
    def carrier_pulses(self) -> float:
        """Commanded (pre-lash) carrier position -- the motor shaft. The AMT203S
        encoder sits here (upstream of the gear lash), so this is what an
        EncoderAxis reads back; the optics (:attr:`optical_pulses`) lag it by the
        backlash band."""
        return self._carrier

    def current_blur_sigma(self) -> float:
        return self.base_blur + self.blur_per_pulse * abs(self._optical - self.best_focus)

    # ── motion + rendering ───────────────────────────────────────────────

    def _window_origin(self) -> tuple[int, int, bool]:
        """Return (x0, y0, is_sky) for the current frame window."""
        t = self._t
        if self.profile == "translate":
            x0 = self._cx0 + int(round(self.step_px * t))
            y0 = self._cy0
            return x0, y0, False
        if self.profile == "oscillate":
            amp = self.step_px * self.period / 4.0
            x0 = self._cx0 + int(round(amp * math.sin(2 * math.pi * t / self.period)))
            y0 = self._cy0
            return x0, y0, False
        if self.profile == "circle":
            radius = self.step_px * self.period / (2 * math.pi)
            theta = 2 * math.pi * t / self.period
            x0 = self._cx0 + int(round(radius * math.sin(theta)))
            y0 = self._cy0 + int(round(-radius * math.cos(theta)))
            # Top of the circle (theta ~ 0) points at sky.
            is_sky = math.cos(theta) > 0.8
            return x0, y0, is_sky
        return self._cx0, self._cy0, False

    def render(self) -> np.ndarray:
        """Render the current frame as uint16 (Bayer14-style, value << 6)."""
        x0, y0, is_sky = self._window_origin()
        x0 = max(0, min(x0, self._cw - self.fw))
        y0 = max(0, min(y0, self._ch - self.fh))
        if is_sky:
            rng = np.random.default_rng(self._t)
            crop = (self.sky_value + rng.integers(0, 3, size=(self.fh, self.fw))).astype(
                np.uint8
            )
        else:
            crop = self._canvas[y0:y0 + self.fh, x0:x0 + self.fw].copy()
            sigma = self.current_blur_sigma()
            if sigma > 0.3:
                crop = cv2.GaussianBlur(crop, (0, 0), sigma)
        return (crop.astype(np.uint16) << 6)

    def step(self) -> None:
        """Advance the motion clock by one frame."""
        self._t += 1


class SimulatedGrabber:
    """Drop-in replacement for FrameGrabber that generates synthetic frames."""

    def __init__(
        self,
        width: int = IRON661_WIDTH,
        height: int = IRON661_HEIGHT,
        fps: float = DEFAULT_FPS,
        image_dir: Optional[str] = None,
    ) -> None:
        self._width = width
        self._height = height
        self._fps = fps
        self._image_dir = image_dir
        self._on_frame: Optional[Callable[[np.ndarray], None]] = None
        self._raw_processor: Optional[Callable[[np.ndarray], np.ndarray]] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._frame_index = 0
        self._images: list[np.ndarray] = []

    # ── Mimic FrameGrabber interface ──────────────────────────────────

    def set_raw_processor(self, processor):
        """Set (or clear) a raw-frame processor (same API as FrameGrabber)."""
        self._raw_processor = processor

    def open(self, grabber_index: int = 0, camera_index: int = 0) -> None:
        if self._image_dir:
            self._load_images()
        log.info(
            "SimulatedGrabber open (grabber=%d, camera=%d, %dx%d @ %.1f fps, %d preloaded images)",
            grabber_index, camera_index,
            self._width, self._height, self._fps, len(self._images),
        )

    def close(self) -> None:
        self.stop()
        log.info("SimulatedGrabber closed")

    def start(self, on_frame: Callable[[np.ndarray], None]) -> None:
        self._on_frame = on_frame
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        log.info("SimulatedGrabber started")

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        log.info("SimulatedGrabber stopped")

    # ── Internal ──────────────────────────────────────────────────────

    def _load_images(self) -> None:
        img_dir = Path(self._image_dir)
        for p in sorted(img_dir.glob("*")):
            if p.suffix.lower() in (".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp"):
                img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
                if img is not None:
                    if img.shape[:2] != (self._height, self._width):
                        img = cv2.resize(img, (self._width, self._height))
                    self._images.append(img)
        log.info("Loaded %d images from %s", len(self._images), self._image_dir)

    def _run(self) -> None:
        interval = 1.0 / self._fps
        while self._running:
            t0 = time.perf_counter()
            if self._images:
                frame = self._images[self._frame_index % len(self._images)].copy()
            else:
                frame = _generate_test_frame(self._width, self._height, sharp_seed=self._frame_index)
            self._frame_index += 1
            if self._raw_processor is not None:
                try:
                    frame = self._raw_processor(frame)
                except Exception:
                    log.exception("Error in raw processor")
            if self._on_frame:
                try:
                    self._on_frame(frame)
                except Exception:
                    log.exception("Error in frame callback")
            elapsed = time.perf_counter() - t0
            remaining = interval - elapsed
            if remaining > 0:
                time.sleep(remaining)
