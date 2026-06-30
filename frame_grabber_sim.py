"""Simulated frame grabber for development without physical hardware.

Generates synthetic images with regions of varying sharpness so the
tile-based focus algorithm can be tested end-to-end.
"""

import logging
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
