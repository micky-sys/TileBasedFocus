"""Simulated rig for running the app without hardware (``--demo``).

* :class:`DemoCamera` -- drop-in for :class:`KyfgCamera`: synthetic BayerRG14 frames at
  ``DEMO_FPS`` while the simulated PCB triggers (``DEMO_TRIGGER`` / ``DEMO_SWEEP_BUDGET``).
  The five w10 ROIs are box-blurred by how far the simulated lens is from best focus, so the
  live w10 / AF see a real V.
* :class:`DemoCameraManager` -- drop-in for :class:`CameraManager`.
* :class:`DemoClearCore` -- :class:`serial_comm.FocusController` with the UART replaced by an
  interpreter of the same command strings: ``ack:`` echoes, ``Mode n:`` banners, a 10 Hz
  ``$ENC`` stream for the relay-selected slot, one motor per slot (counts/pulse by strength,
  slot 2 wired backwards like cam 2439007 was), ``Swp#`` after ``$Click*``.

No GPS simulation: the preflight's GPS check fails in demo, by design.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

import cv2
import numpy as np

from serial_comm import PCB_FOCUS_CAMERA_SLOT_BY_SERIAL, FocusController

from .camera_manager import OpenedCamera
from .kyfg_backend import DEFAULT_CAMERA_CONFIG, FrameCallback
from .pipeline import CameraConfig
from .psf_w10 import roi_boxes

log = logging.getLogger(__name__)

# Match the rig's fixed PCB serial → slot mapping in serial_comm.py.
DEMO_CAMERA_SERIALS = ("2439006", "2439007", "2439008")

FULL_HEIGHT = 9528
FULL_WIDTH = 13400
SUBSAMPLE_FACTOR = 4
DEMO_FPS = 4.0

# Simulated lens per PCB slot: raw-count position (the DemoClearCore moves it) and the position
# of best focus. Blur radius grows DEMO_BLUR_PX_PER_COUNT per count away from best.
DEMO_LENS_POS: dict[int, float] = {1: 1500.0, 2: 1500.0, 3: 1500.0}
DEMO_LENS_BEST: dict[int, float] = {1: -1200.0, 2: 4300.0, 3: 900.0}
DEMO_BLUR_PX_PER_COUNT = 1.0 / 300.0

# The simulated PCB's trigger line, read by every DemoCamera: frames flow only while the PCB
# triggers, as on the rig -- continuously in M1/M4/M5/M6, and in M0/M3 only for the
# DEMO_SWEEP_FRAMES of a ``$Click*`` sweep (a per-slot budget, so every camera gets exactly
# the same count). Before the PCB is connected / in a mode, nothing is acquired.
DEMO_TRIGGER = {"continuous": False}
DEMO_SWEEP_BUDGET: dict[int, int] = {1: 0, 2: 0, 3: 0}
DEMO_SWEEP_FRAMES = 13


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
    def frames_delivered(self) -> int:
        return self._frame_index

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
            # Blocky random "ground": step edges everywhere, so every w10 ROI has a few
            # hundred edges to measure. Nearest-neighbour upsample of a coarse random field.
            rng = np.random.default_rng(self._camera_number)
            cell = 24
            coarse = rng.integers(600, 9000, size=(h // cell + 1, w // cell + 1), dtype=np.uint16)
            self._full_template = np.ascontiguousarray(
                np.repeat(np.repeat(coarse, cell, axis=0), cell, axis=1)[:h, :w]
            )
        return self._full_template

    @property
    def _slot(self) -> int:
        return PCB_FOCUS_CAMERA_SLOT_BY_SERIAL.get(self._serial, self._camera_number)

    def _blur_px(self) -> int:
        return int(abs(DEMO_LENS_POS[self._slot] - DEMO_LENS_BEST[self._slot]) * DEMO_BLUR_PX_PER_COUNT)

    def _triggered(self) -> bool:
        """One trigger from the simulated PCB, if it is triggering right now."""
        if DEMO_TRIGGER["continuous"]:
            return True
        if DEMO_SWEEP_BUDGET[self._slot] > 0:
            DEMO_SWEEP_BUDGET[self._slot] -= 1
            return True
        return False

    def _defocus_rois(self, full: np.ndarray) -> None:
        """Box-blur the five w10 ROIs of *full* in place by the lens's current defocus."""
        r = self._blur_px()
        if r < 1:
            return
        k = 2 * r + 1
        for x0, y0, x1, y1 in roi_boxes():
            roi = full[y0:y1, x0:x1]
            roi[...] = cv2.blur(roi, (k, k))

    def _make_small_frame(self) -> np.ndarray:
        step = self.SUBSAMPLE_FACTOR
        small = self._ensure_full_template()[::step, 1::step]   # green phase, as kyfg_backend
        offset = (self._frame_index * 3) % small.shape[1]
        return np.roll(small, offset, axis=1)

    def _emit_loop(self) -> None:
        interval = 1.0 / DEMO_FPS
        while self._running:
            t0 = time.perf_counter()
            cb = self._frame_cb
            if cb is not None and self._triggered():
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
                                self._defocus_rois(slot_view)
                                full = int(slot_idx)
                        else:
                            full = self._ensure_full_template().copy()
                            self._defocus_rois(full)
                    cb(small, full)
                    self._frame_index += 1
                except Exception:
                    log.exception("%s demo frame emit failed", self.label)
            elapsed = time.perf_counter() - t0
            remaining = interval - elapsed
            if remaining > 0:
                time.sleep(remaining)

    # Recovery hooks the CameraManager path calls on a KyfgCamera.
    def device_reset_issue(self) -> tuple[bool, str]:
        return True, "demo"

    def load_factory_userset(self) -> tuple[bool, str]:
        return True, "demo"


class DemoCameraManager:
    """Drop-in for :class:`CameraManager`: three :class:`DemoCamera`, no grabber."""

    cti_path = "<demo: no KAYA>"

    def __init__(self) -> None:
        self._opened: list[OpenedCamera] = []

    @property
    def opened(self) -> list:
        return list(self._opened)

    def open_all(self, expected_count: int = 3) -> list:
        time.sleep(1.0)   # a visible bring-up, so the GUI's "opening cameras" path is exercised
        self._opened = []
        for idx, serial in enumerate(DEMO_CAMERA_SERIALS[:expected_count]):
            cam = DemoCamera(idx + 1, serial)
            cam.open()
            cfg = CameraConfig(camera_number=idx + 1, camera_serial=serial, device_index=idx)
            self._opened.append(OpenedCamera(backend=cam, config=cfg, setup_log=cam.configure()))
        return list(self._opened)

    def recover_cameras(self, do_device_reset: bool = False) -> list[tuple[int, bool, str]]:
        time.sleep(5.0 if do_device_reset else 2.0)
        return [(c.config.camera_number, True, "demo recovery") for c in self._opened]

    def close_all(self) -> None:
        for c in self._opened:
            c.backend.close()
        self._opened = []


class DemoClearCore(FocusController):
    """The PCB as a text interpreter behind the unchanged :class:`FocusController` command
    methods. Replies arrive on timer threads exactly as pyserial's reader thread delivers them."""

    CPP = {0: 30.0, 1: 80.0, 2: 300.0, 3: 850.0}     # counts per pulse by strength
    POLARITY = {1: 1.0, 2: -1.0, 3: 1.0}             # sign of a '+' pulse per slot
    ENC_HZ = 10.0
    LATENCY_S = 0.25                                  # command -> motion / ack

    def __init__(self) -> None:
        super().__init__()
        self._alive = False
        self._slot = 1
        self._strength = 3
        self._mode = 0
        self._enc_thread: Optional[threading.Thread] = None

    @staticmethod
    def find_clearcore() -> Optional[str]:
        return "DEMO"

    @property
    def connected(self) -> bool:
        return self._alive

    def connect(self, port: str, on_response: Optional[Callable[[str], None]] = None) -> None:
        self._on_response = on_response
        self._alive = True
        self._enc_thread = threading.Thread(target=self._enc_loop, name="demo-clearcore-enc", daemon=True)
        self._enc_thread.start()
        log.info("DemoClearCore connected (%s)", port)

    def disconnect(self) -> None:
        self._alive = False
        DEMO_TRIGGER["continuous"] = False
        if self._enc_thread is not None:
            self._enc_thread.join(timeout=1.0)
            self._enc_thread = None

    # -- the "PCB" ---------------------------------------------------------
    def _reply(self, line: str) -> None:
        cb = self._on_response
        if self._alive and cb is not None:
            cb(line)

    def _later(self, dt: float, fn: Callable[[], None]) -> None:
        threading.Timer(dt, lambda: self._alive and fn()).start()

    def _enc_loop(self) -> None:
        while self._alive:
            raw = int(DEMO_LENS_POS[self._slot]) % 4096
            self._reply(f"$ENC={self._slot},{raw}*")
            time.sleep(1.0 / self.ENC_HZ)

    def _send(self, data: str) -> None:
        if not self._alive:
            log.warning("DemoClearCore not connected -- command ignored: %r", data)
            return
        cmd = data.strip()
        if cmd and cmd[0] in "+-" and set(cmd) <= {"+", "-"}:
            # One burst: the motor moves after the command latency, in one step (the host's
            # unwrap sees exactly what a fast real move between two $ENC reports looks like).
            slot, sym, n = self._slot, cmd[0], len(cmd)
            step = self.POLARITY[slot] * self.CPP[self._strength] * n * (1 if sym == "+" else -1)
            self._later(self.LATENCY_S, lambda: DEMO_LENS_POS.__setitem__(slot, DEMO_LENS_POS[slot] + step))
            return
        self._later(0.05, lambda: self._reply(f"ack: {cmd};  A# 1"))
        m = re.fullmatch(r"\$([MCS])(\d)\*", cmd)
        if cmd == "$Click*":
            if self._mode in (0, 3):
                for slot in DEMO_SWEEP_BUDGET:
                    DEMO_SWEEP_BUDGET[slot] = DEMO_SWEEP_FRAMES
            self._later(4.0, lambda: self._reply("Swp# 1;  Total sweep time 4000;  Net sweep time 3800"))
        elif m and m.group(1) == "M":
            n = int(m.group(2))
            self._mode = n
            DEMO_TRIGGER["continuous"] = n in (1, 4, 5, 6)
            self._later(2.0, lambda: self._reply(f"Mode {n}: demo"))
        elif m and m.group(1) == "C":
            self._slot = int(m.group(2))
        elif m and m.group(1) == "S":
            self._strength = int(m.group(2))
            self._later(0.05, lambda: self._reply(f"Focus strength {self._strength}"))
