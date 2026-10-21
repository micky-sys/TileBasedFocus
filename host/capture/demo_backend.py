"""Simulated camera backend for UI demonstration without KAYA hardware.

Provides :class:`DemoCamera`, a drop-in substitute for :class:`KyfgCamera`
used when the app is launched with ``--demo``. Generates animated synthetic
Bayer uint16 frames at a modest rate so preview, split-screen, and M4 focus
taps all have something to display.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

import numpy as np

from .kyfg_backend import DEFAULT_CAMERA_CONFIG, FrameCallback

log = logging.getLogger(__name__)

# Match the rig's fixed PCB serial → slot mapping in serial_comm.py.
DEMO_CAMERA_SERIALS = ("2439006", "2439007", "2439008")

FULL_HEIGHT = 9528
FULL_WIDTH = 13400
SUBSAMPLE_FACTOR = 4
DEMO_FPS = 4.0


@dataclass
class _DemoCameraInfo:
    """Minimal stand-in for :class:`CameraInfo`."""

    index: int
    serial: str
    handle: int = 0

    @property
    def label(self) -> str:
        return f"demo[{self.serial}]"


class DemoCamera:
    """One simulated Iron 661 camera for ``--demo`` mode."""

    NUM_BUFFERS = 12
    SUBSAMPLE_FACTOR = SUBSAMPLE_FACTOR

    def __init__(self, camera_number: int, serial: str) -> None:
        self._camera_number = int(camera_number)
        self._serial = str(serial)
        self._info = _DemoCameraInfo(index=camera_number - 1, serial=self._serial)
        self._width = FULL_WIDTH
        self._height = FULL_HEIGHT
        self._pixel_format = "BayerRG14"
        self._configured = True
        self._armed = False
        self._emit_full_res = False
        self._full_pool: Any = None
        self._frame_cb: Optional[FrameCallback] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._frame_index = 0
        self._exposure_us = float(DEFAULT_CAMERA_CONFIG["ExposureTime"])
        self._gain = float(DEFAULT_CAMERA_CONFIG["Gain"])
        self._full_template: Optional[np.ndarray] = None

    @property
    def info(self) -> _DemoCameraInfo:
        return self._info

    @property
    def serial(self) -> str:
        return self._serial

    @property
    def index(self) -> int:
        return self._info.index

    @property
    def label(self) -> str:
        return f"cam{self._camera_number}[{self._serial}]"

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def pixel_format(self) -> str:
        return self._pixel_format

    @property
    def is_armed(self) -> bool:
        return self._armed

    @property
    def full_res_emission(self) -> bool:
        return self._emit_full_res

    def set_full_res_emission(self, enabled: bool) -> None:
        self._emit_full_res = bool(enabled)

    def set_full_res_pool(self, pool: Any) -> None:
        self._full_pool = pool

    def open(self) -> None:
        log.info("%s opened (demo)", self.label)

    def close(self) -> None:
        self.disarm()
        log.info("%s closed (demo)", self.label)

    def configure(self) -> list[str]:
        return [
            "demo mode: synthetic BayerRG14 frames",
            f"ExposureTime={self._exposure_us:g} (simulated)",
            f"Gain={self._gain:g} (simulated)",
        ]

    def arm(self, on_frame: FrameCallback) -> None:
        if self._armed:
            return
        self._frame_cb = on_frame
        self._running = True
        self._frame_index = 0
        self._thread = threading.Thread(
            target=self._emit_loop,
            name=f"demo-cam{self._camera_number}",
            daemon=True,
        )
        self._thread.start()
        self._armed = True
        log.info("%s armed (demo @ %.1f fps)", self.label, DEMO_FPS)

    def disarm(self) -> None:
        if not self._armed:
            return
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        self._armed = False
        self._frame_cb = None
        log.info("%s disarmed (demo)", self.label)

    def read_exposure_time_us(self) -> Optional[float]:
        return self._exposure_us

    def read_gain(self) -> Optional[float]:
        return self._gain

    def set_exposure_time_us(self, us: float) -> tuple[bool, str]:
        self._exposure_us = float(us)
        return True, f"verified={self._exposure_us:g} (demo)"

    def set_gain(self, gain: float) -> tuple[bool, str]:
        self._gain = float(gain)
        return True, f"verified={self._gain:g} (demo)"

    def _ensure_full_template(self) -> np.ndarray:
        if self._full_template is None:
            h, w = self._height, self._width
            cam = self._camera_number
            yy = np.linspace(0, 12000, h, dtype=np.uint16)[:, None]
            xx = np.linspace(0, 16000, w, dtype=np.uint16)[None, :]
            base = ((xx + yy + cam * 2500) % 14000).astype(np.uint16)
            # A few sharp vertical stripes so focus/sharpness views have structure.
            stripe = np.zeros((h, w), dtype=np.uint16)
            for k in range(3):
                x0 = (w // 6) * (k + 1) + cam * 120
                x0 = min(w - 8, max(0, x0 & ~1))
                stripe[:, x0:x0 + 6] = 14000
            self._full_template = np.clip(base + stripe, 0, 16383).astype(np.uint16)
        return self._full_template

    def _make_small_frame(self) -> np.ndarray:
        step = self.SUBSAMPLE_FACTOR
        full = self._ensure_full_template()
        offset = (self._frame_index * step * 3) % full.shape[1]
        rolled = np.roll(full, offset, axis=1)
        return rolled[::step, 1::step].copy()  # green phase, as kyfg_backend

    def _emit_loop(self) -> None:
        interval = 1.0 / DEMO_FPS
        while self._running:
            t0 = time.perf_counter()
            cb = self._frame_cb
            if cb is not None:
                try:
                    small = self._make_small_frame()
                    full: Any = None
                    if self._emit_full_res:
                        pool = self._full_pool
                        if pool is not None:
                            slot_idx = pool.acquire_slot_nowait()
                            if slot_idx is None:
                                full = -1
                            else:
                                slot_view = pool.slot_view(int(slot_idx))
                                slot_view[...] = self._ensure_full_template()
                                full = int(slot_idx)
                        else:
                            full = self._ensure_full_template().copy()
                    cb(small, full)
                except Exception:
                    log.exception("%s demo frame emit failed", self.label)
            self._frame_index += 1
            elapsed = time.perf_counter() - t0
            remaining = interval - elapsed
            if remaining > 0:
                time.sleep(remaining)
