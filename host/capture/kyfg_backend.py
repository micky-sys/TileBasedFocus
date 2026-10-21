"""KYFGLib-based camera backend for the unified capture + focus app.

Why KYFGLib instead of Harvesters / KYVPGenTL?
----------------------------------------------
The Harvesters path through KYVPGenTL exposes a *merged* GenICam tree where
all three Iron 661 cameras share one node-map; ``AcquisitionStart`` on the
camera-side branch of that tree is documented to fail with AccessException
(see ``trigger_preview_app.py``'s help text). Without ``AcquisitionStart``,
KAYA's ``DSStartAcquisition`` alone is not enough to arm the sensor and
external Line1 triggers are silently dropped.

KYFGLib is the C-level Python API that *does* arm the sensor reliably on
this hardware (the legacy single-camera focus tool, ``main.py`` /
``frame_grabber.py``, has been running on these exact cameras for months).
KYFGLib also supports opening multiple cameras on one grabber simultaneously
via ``KYFG_CameraOpen2``, with one stream + callback per camera, which is
what stage 2 of the unified app needs.

``KyfgGrabber`` enumerates every camera the grabber sees and lets a caller
build multiple independent :class:`KyfgCamera` instances against it; each
camera then has its own stream, its own buffer pool and its own per-frame
callback so they can be armed and disarmed independently.

Threading model
---------------
KYFGLib delivers buffers via a callback registered with
``KYFG_StreamBufferCallbackRegister``. The SDK calls the callback from one
of its internal worker threads, so the work done there must be short --
otherwise the SDK's queue backs up and frames are dropped.

Each :class:`KyfgCamera` owns a ``threading.Lock``-protected
"latest preview" buffer plus an optional ``frame_callback``. The SDK
callback inside this module:

1. Builds a zero-copy ``numpy`` view over the SDK buffer.
2. ALWAYS makes a small uint16 copy by ``SUBSAMPLE_FACTOR``-decimating
   the view (default 4x = ~8 MB at 13400/4 x 9528/4). This is the
   preview / focus-tap path; cheap enough that a ~10 Hz callback can't
   congest the GIL or saturate memory bandwidth.
3. CONDITIONALLY (only when ``set_full_res_emission(True)`` is in
   effect) makes a full-resolution uint16 copy of the view (~255 MB
   per frame). This is the TIFF-writer path; we want operators to be
   able to flip M0/M1 and have raw 14-bit Bayer frames hit disk
   without any decimation.
4. Hands both arrays (small + full-or-None) off to ``frame_callback``
   synchronously. Consumers are expected to drop them into a
   ``queue.Queue`` and return immediately.

The subsample factor for step 2 is configurable via
``SUBSAMPLE_FACTOR``. The full-res copy in step 3 is always exactly the
sensor's full output (currently 13400x9528 BayerRG14).

Code shared with ``frame_grabber.py``
-------------------------------------
The configure / status helpers in this module are deliberately copies of
the ones in the legacy ``frame_grabber.py`` (with the same default config
values, status names, etc.) rather than imports, to keep the legacy focus
tool fully self-contained -- so a backup restore of ``frame_grabber.py``
doesn't accidentally drag in unified-app changes.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

import numpy as np

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# KYFGLib bootstrap
# ---------------------------------------------------------------------------

_kyfg: Any = None  # set by _ensure_kyfg()


def _ensure_kyfg() -> Any:
    """Lazy-import KYFGLib from the Vision Point bin directory.

    The KAYA Python bindings live in ``%KAYA_VISION_POINT_BIN_PATH%`` next
    to the .cti producer, not on ``PYTHONPATH``. We splice that directory
    onto ``sys.path`` once, set ``WithAdapter=1`` so the legacy bindings
    talk to the Vision Point II backend, and import.

    Returns the module on success; raises ``RuntimeError`` if the env var
    is unset or the import fails.
    """
    global _kyfg
    if _kyfg is not None:
        return _kyfg
    bin_path = os.environ.get("KAYA_VISION_POINT_BIN_PATH", "")
    if not bin_path:
        raise RuntimeError(
            "KAYA_VISION_POINT_BIN_PATH environment variable is not set. "
            "Is the Kaya Vision Point SDK installed?"
        )
    os.environ["WithAdapter"] = "1"
    if bin_path not in sys.path:
        sys.path.insert(0, bin_path)
    import KYFGLib  # noqa: E402 -- path-dependent import
    _kyfg = KYFGLib
    return _kyfg


# ---------------------------------------------------------------------------
# Iron 661 default config (same as legacy frame_grabber.DEFAULT_CAMERA_CONFIG)
# ---------------------------------------------------------------------------

DEFAULT_CAMERA_CONFIG: dict[str, Any] = {
    # Must be written before TriggerMode -- some Iron 661 firmware
    # revisions otherwise inherit whatever AcquisitionMode the previous
    # session (typically Vision Point) left behind. If VP was last set
    # to SingleFrame or MultiFrame, KYFG_CameraStart returns OK but the
    # sensor produces 0 or 1 buffer per arm and stops, which manifests
    # in our app as "PCB triggers fire but no preview / no acquired".
    "AcquisitionMode": "Continuous",
    "TriggerMode": "On",
    "TriggerSource": "Line1",
    "TriggerActivation": "RisingEdge",
    # Allow next trigger to arrive while previous frame is still being read
    # out. Without this, when readout > trigger period the camera silently
    # drops every other trigger.
    "TriggerOverlap": "ReadOut",
    "Width": 13400,
    "Height": 9528,
    "PixelFormat": "BayerRG14",
    "ExposureAuto": "Off",
    "GainAuto": "Off",
    "ExposureMode": "Timed",
    "ExposureTime": 250.0,
    "Gain": 3.0,
    "BlackLevelAuto": "Off",
    "BlackLevel": 0.0,
    "BalanceWhiteAuto": "Continuous",
}


# SFNC ``GainSelector`` entries that select analog amplification before the ADC.
# Applied (in order) before GUI read/write of ``Gain`` when the camera exposes
# ``GainSelector``. If none stick — many devices map ``Gain`` to analog only —
# we still read/write ``Gain`` unchanged.
ANALOG_GAIN_SELECTOR_PRESETS: tuple[str, ...] = (
    "AnalogAll",
    "AnalogWhite",
    "Analog",
)


_FGSTATUS_OK = 0x3000
_FGSTATUS_NAMES = {
    0x3000: "OK",
    0x3001: "UNKNOWN_HANDLE",
    0x3002: "HW_NOT_FOUND",
    0x3003: "BUSY",
    0x3007: "INVALID_VALUE",
    0x300A: "WRONG_PARAMETER_NAME",
    0x300B: "WRONG_PARAMETER_TYPE",
    0x300C: "GENICAM_EXCEPTION",
    0x300D: "OUT_OF_RANGE_ADDRESS",
    0x301A: "PARAMETER_NOT_WRITABLE",
    0x3026: "PARAMETER_NOT_READABLE",
    0x4001: "INPUT_ARGUMENT_TYPE_ERROR",
}


def _status_name(status: int) -> str:
    return _FGSTATUS_NAMES.get(status, f"0x{status:04X}")


def _unpack_result(result: Any) -> tuple[int, Any]:
    """KYFGLib functions return either ``status`` or ``(status, value)``."""
    if isinstance(result, tuple):
        status = int(result[0])
        value = result[1] if len(result) >= 2 else None
        return status, value
    return int(result), None


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CameraInfo:
    """Identity record for one camera enumerated on a grabber."""

    index: int
    handle: Any  # KYFGLib camera handle (opaque int / pointer wrapper)
    serial: str
    model: str
    vendor: str
    firmware: str

    @property
    def label(self) -> str:
        return f"#{self.index} {self.serial!r} ({self.model})"


# ---------------------------------------------------------------------------
# Grabber
# ---------------------------------------------------------------------------


class KyfgGrabber:
    """Owns one KYFG grabber handle + the list of camera handles it sees.

    Designed so multiple :class:`KyfgCamera` instances can share a single
    grabber for stage 2 (multi-camera capture). Stage 1 only opens one
    camera at a time but the API doesn't change between stages.
    """

    def __init__(self, grabber_index: int = 0) -> None:
        self._kyfg = _ensure_kyfg()
        self._grabber_index = int(grabber_index)
        self._fg_handle: Any = None
        self._cameras: list[CameraInfo] = []

    @property
    def grabber_index(self) -> int:
        return self._grabber_index

    @property
    def fg_handle(self) -> Any:
        return self._fg_handle

    @property
    def cameras(self) -> list[CameraInfo]:
        """List of cameras enumerated on this grabber (after :meth:`open`)."""
        return list(self._cameras)

    def open(self) -> None:
        """Scan for devices, open grabber, enumerate cameras.

        ``KYFG_Open(0)`` raises ``KYPY_STATUS_INVALID_FGHANDLE`` until the
        SDK has scanned for devices at least once -- KYFGLib's internal
        device list is empty before the first ``KY_DeviceScan`` call,
        and ``Open(0)`` translates "no device at index 0" to that exact
        exception. The legacy ``frame_grabber.scan_grabbers`` ran
        ``KY_DeviceScan`` at module import time which masked the
        requirement; we make it explicit here.

        After the scan we mirror ``frame_grabber.FrameGrabber.open``'s
        cleanup pattern (open / list cameras / re-open each / stop /
        close) so a crashed previous session doesn't leave camera
        handles wedged with stale Python callbacks.
        """
        k = self._kyfg

        scan_result = k.KY_DeviceScan()
        scan_status, scan_count = _unpack_result(scan_result)
        log.info("KY_DeviceScan -> status=%s, devices=%s",
                 _status_name(scan_status), scan_count)
        if scan_status != _FGSTATUS_OK or not scan_count:
            raise RuntimeError(
                f"KY_DeviceScan found no frame grabbers "
                f"(status={_status_name(scan_status)}, "
                f"count={scan_count}). Is the grabber powered and "
                "are the KAYA drivers installed?"
            )
        if self._grabber_index >= int(scan_count):
            raise RuntimeError(
                f"Grabber index {self._grabber_index} requested but "
                f"KY_DeviceScan only found {scan_count} device(s)."
            )
        try:
            result = k.KYFG_Open(self._grabber_index)
            stale_fg = result[0] if isinstance(result, tuple) else result
            _, stale_cams = k.KYFG_UpdateCameraList(stale_fg)
            for ch in stale_cams:
                # The 3-step open/stop/close dance is what frame_grabber
                # does; without the open() the stop() can sometimes hit
                # an "invalid camera state" depending on what crashed.
                try:
                    k.KYFG_CameraOpen2(ch, None)
                except Exception:
                    pass
                try:
                    k.KYFG_CameraStop(ch)
                except Exception:
                    pass
                try:
                    k.KYFG_CameraClose(ch)
                except Exception:
                    pass
            k.KYFG_Close(stale_fg)
            time.sleep(1.0)
            log.info("Cleaned up stale grabber session (idx=%d)",
                     self._grabber_index)
        except Exception:
            log.debug("No stale session to clean up on grabber %d",
                      self._grabber_index)

        result = k.KYFG_Open(self._grabber_index)
        log.info("KYFG_Open(%d) returned %r (type=%s)",
                 self._grabber_index, result, type(result).__name__)
        self._fg_handle = result[0] if isinstance(result, tuple) else result
        log.info("Opened grabber %d -> handle %s",
                 self._grabber_index, self._fg_handle)

        self._enumerate_cameras()

    def _enumerate_cameras(self) -> None:
        k = self._kyfg
        _, cam_handles = k.KYFG_UpdateCameraList(self._fg_handle)
        self._cameras = []
        for idx, ch in enumerate(cam_handles):
            info = CameraInfo(
                index=idx, handle=ch,
                serial="", model="", vendor="", firmware="",
            )
            try:
                _, raw = k.KYFG_CameraInfo2(ch)
                info.model = (getattr(raw, "deviceModelName", "") or "").strip()
                info.vendor = (getattr(raw, "deviceVendorName", "") or "").strip()
                info.serial = (getattr(raw, "deviceID", "") or "").strip()
                info.firmware = (
                    getattr(raw, "deviceFirmwareVersion", "") or ""
                ).strip()
            except Exception as exc:
                log.debug("CameraInfo2 failed for idx %d: %s", idx, exc)
            self._cameras.append(info)
        log.info("Grabber %d sees %d camera(s):", self._grabber_index, len(self._cameras))
        for c in self._cameras:
            log.info("  %s", c.label)

    def find_by_serial(self, serial: str) -> Optional[CameraInfo]:
        target = (serial or "").strip()
        if not target:
            return None
        norm = lambda s: "".join(c for c in s.upper() if c.isalnum())
        ntarget = norm(target)
        for cam in self._cameras:
            if norm(cam.serial) == ntarget:
                return cam
        return None

    def close(self) -> None:
        """Close the grabber. Caller is responsible for closing cameras first."""
        if self._fg_handle is None:
            return
        try:
            self._kyfg.KYFG_Close(self._fg_handle)
        except Exception:
            log.warning("KYFG_Close failed", exc_info=True)
        self._fg_handle = None
        self._cameras = []

    def reopen(self, settle_s: float = 1.5) -> None:
        """Close + reopen the grabber and re-enumerate cameras.

        Used by the on-demand "Reset cameras" recovery path (see
        ``MainWindow._on_reset_cameras_clicked``) to reset the SDK
        session after issuing DeviceReset on every camera. KAYA's
        SDK won't let us reopen a closed camera handle without first
        re-enumerating via ``KYFG_UpdateCameraList`` -- and after a
        DeviceReset the cached camera handles point at GenICam node
        trees that no longer match the camera's current state, so
        the cleanest reset is to drop the entire grabber session
        and rebuild it.

        Caller is responsible for ``KyfgCamera.close``-ing every
        camera FIRST and adopting the fresh handles afterwards via
        ``KyfgCamera.adopt_handle`` matched by serial.

        ``settle_s`` defaults to 1.5 s -- enough for the kernel
        driver to release the device handle before we ask for it
        back. Without this small wait, the reopen sometimes races
        the driver and returns ``BUSY``.
        """
        log.info("KyfgGrabber.reopen: closing grabber...")
        self.close()
        time.sleep(float(settle_s))
        log.info("KyfgGrabber.reopen: re-opening grabber...")
        self.open()
        log.info(
            "KyfgGrabber.reopen: done; %d camera(s) re-enumerated",
            len(self._cameras),
        )


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------


# Type for the per-frame callback the GUI side gives us.
#
# Signature: ``(small, full_or_none)`` where
#   small : np.ndarray (uint16, height/SUBSAMPLE_FACTOR x width/SUBSAMPLE_FACTOR)
#       Always provided. Cheap (~8 MB at 4x decimation). Used by the GUI
#       for the live preview QImage and by the focus-tap for sharpness
#       analysis.
#   full_or_none : Optional[np.ndarray]
#       Full-resolution uint16 frame (~255 MB), or ``None``. Provided
#       only when ``KyfgCamera.set_full_res_emission(True)`` has been
#       called -- i.e., when the TIFF writer is enabled and we need the
#       raw 14-bit Bayer data on disk. Skipped during preview-only modes
#       (M4 focus, or M0/M1 with no drive/project) to keep memory
#       bandwidth and CPU copy cost down.
#
# The SDK's buffer is released as soon as this callable returns, so the
# caller MUST not hold onto either array beyond the call... which they
# won't, because both arrays are fresh copies out of the SDK buffer.
FrameCallback = Callable[[np.ndarray, Optional[np.ndarray]], None]


class KyfgCamera:
    """One opened camera on a :class:`KyfgGrabber`.

    Lifecycle is open -> configure -> arm(callback) -> ... -> disarm -> close.
    Reusable: you can disarm and re-arm with a different callback without
    closing.
    """

    # Per-camera ring of in-flight DMA buffers. With 3 cameras armed
    # simultaneously, every buffer here is a separately-pinned-and-mapped
    # DMA target the kernel has to track. We previously saw
    # ``DRIVER_VERIFIER_DMA_VIOLATION`` (bug check 0xE6) at 8, but
    # that was on a kernel where DRIVER_VERIFIER was applying extra
    # checks; the underlying driver streams fine in StreamPix 10 / VP
    # at higher counts. Today we run with 12 (Vision Point's default)
    # because at 4 the ring runs ~1 frame deep at our trigger cadence
    # and any scheduling jitter in the SDK's worker thread can cause
    # camera-side overruns -- in M1 we lose ~1 frame per sweep and the
    # losses are upstream of our callback (cb_count matches writes,
    # zero pipeline drops logged). VP captures 13/sweep cleanly with
    # 12 buffers, so let's match that.
    NUM_BUFFERS = 12

    # How aggressively to decimate frames before handing them to Python.
    # Must be a positive integer; the camera resolution does not need to
    # be divisible by it (numpy slicing handles odd remainders by simply
    # dropping the trailing pixels). 4 keeps each preview frame around
    # 8 MB, which is small enough that even three cameras streaming at
    # ~5 Hz won't saturate memory bandwidth.
    SUBSAMPLE_FACTOR = 4

    # "Kick" value (in microseconds) used to dislodge a stuck
    # ``ExposureTime`` setting before writing the real configured
    # value. See the long comment in ``configure()``. 10000 us is
    # well within the camera's range, far enough from any plausible
    # configured exposure that the firmware definitely sees a
    # change, and matches the value that the user empirically
    # confirmed kicks cam 2439006 out of the stuck state in Vision
    # Point.
    _EXPOSURE_KICK_US = 10000.0

    def __init__(self, grabber: KyfgGrabber, info: CameraInfo) -> None:
        self._kyfg = _ensure_kyfg()
        self._grabber = grabber
        self._info = info
        self._cam_handle: Any = info.handle
        self._stream_handle: Any = None
        self._opened = False
        self._configured = False
        self._armed = False
        # SDK callback context
        self._frame_cb: Optional[FrameCallback] = None
        # When True, the per-buffer callback also produces a full-res
        # uint16 copy alongside the small (subsampled) preview frame.
        # Default False so M4 / preview-only modes don't pay the ~255 MB
        # per frame copy cost. Toggled by the pipeline as recording mode
        # + writer-readiness change. Plain bool: only the GUI thread
        # writes to it, the KAYA worker thread only reads, and the GIL
        # makes the read atomic.
        self._emit_full_res: bool = False
        # IMAGEID gap diagnostics. Read per buffer in the SDK callback
        # so we can tell whether missing frames are camera-side (gap in
        # IMAGEID = sensor never produced that frame) vs SDK-side
        # (no gap, but our callback never saw it). Logged at low rate
        # (every gap, plus first buffer per arm). -1 means not yet seen.
        self._last_image_id: int = -1
        self._image_id_gap_count: int = 0
        self._image_id_total_missing: int = 0
        self._image_id_lookup_failed: bool = False
        # Some KYFG builds expose this as KY_STREAM_BUFFER_INFO_IMAGEID
        # (== 16); on builds where the enum is missing or the call
        # returns an error we fall back to the integer literal directly.
        # Resolved lazily on the first callback so we can log the
        # failure mode once.
        self._image_id_cmd: Optional[int] = None
        # Per-buffer timestamp diagnostics (always populated by KYFG
        # for our hardware, unlike IMAGEID). We log inter-frame
        # intervals and warn when they exceed roughly 1.5 trigger
        # periods so we can see WHERE losses happen (mid-sweep vs
        # at sweep boundaries). Timestamps are in nanoseconds.
        self._last_ts_ns: int = -1
        self._ts_lookup_failed: bool = False
        self._ts_gap_count: int = 0
        self._ts_total_missing: int = 0
        # Triggered logged when (current_ts - last_ts) exceeds this.
        # 320 ms covers a normal 211 ms inter-trigger interval plus
        # any small jitter; anything >320 ms means at least one
        # trigger was silently dropped between these two buffers.
        self._ts_gap_warn_ns: int = 320_000_000
        # Optional shared-memory pool the SDK callback can deposit
        # full-res frames into. When set + ``_emit_full_res`` is True,
        # we acquire a slot, copy the SDK buffer's bytes into it, and
        # hand the slot index downstream. The downstream consumer (or
        # the writer subprocess that consumes its work queue) is
        # responsible for returning the slot to ``pool.free_q`` once
        # the data has been written. When None we fall back to the
        # legacy "fresh ndarray per frame" path -- still works, just
        # incurs a ~20 ms Windows page-zero hit per allocation.
        self._full_pool: Any = None  # Optional[FrameBufferPool]
        self._full_pool_drops: int = 0
        # Frame format -- filled in by configure()
        self._width = 0
        self._height = 0
        self._pixel_format = ""
        self._bytes_per_pixel = 1
        self._row_stride_bytes = 0
        # Diagnostics
        self._cb_count = 0
        self._first_frame_logged = False
        self._cb_error_count = 0

    # ── identity ────────────────────────────────────────────────────────
    @property
    def info(self) -> CameraInfo:
        return self._info

    @property
    def serial(self) -> str:
        return self._info.serial

    @property
    def index(self) -> int:
        return self._info.index

    @property
    def label(self) -> str:
        return f"cam[{self._info.serial or self._info.index}]"

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def pixel_format(self) -> str:
        """GenICam PixelFormat string (e.g. ``BayerRG14``)."""
        return str(self._pixel_format or "")

    @property
    def is_armed(self) -> bool:
        return self._armed

    # ── full-res emission control ───────────────────────────────────────

    def set_full_res_emission(self, enabled: bool) -> None:
        """Enable/disable the per-buffer full-resolution copy.

        When True, ``_buffer_callback`` will deposit a copy of every
        frame's full-resolution pixels into the next free slot of the
        :class:`FrameBufferPool` registered via
        :meth:`set_full_res_pool` (or, if no pool is set, into a fresh
        ndarray) and hand a slot index (or ndarray) to the user
        callback alongside the small preview frame. When False, only
        the small copy is built and the second callback arg is
        ``None``.

        Safe to call from the GUI thread at any time; the KAYA worker
        thread reads the flag atomically.
        """
        self._emit_full_res = bool(enabled)

    @property
    def full_res_emission(self) -> bool:
        return self._emit_full_res

    def set_full_res_pool(self, pool: Any) -> None:
        """Register a :class:`FrameBufferPool` for the SDK callback to
        deposit full-res frames into.

        When set + ``_emit_full_res`` is True, every frame's pixels
        are copied straight into the next free shm slot and the slot
        index is passed downstream as the ``full`` callback argument.
        When set to None we fall back to allocating a fresh ndarray
        per frame (slower; only used during teardown or in the
        unit-test path that doesn't spin up a subprocess writer).
        """
        self._full_pool = pool
        self._full_pool_drops = 0

    # ── lifecycle ───────────────────────────────────────────────────────

    def open(self) -> None:
        """Open the camera handle. No-op if already open."""
        if self._opened:
            return
        self._kyfg.KYFG_CameraOpen2(self._cam_handle, None)
        self._opened = True
        log.info("%s opened", self.label)

    def adopt_handle(self, info: "CameraInfo") -> None:
        """Replace this camera's SDK handle with a freshly-enumerated one.

        Used by the "Reset cameras" recovery path: after a full
        grabber teardown + rebuild (``KyfgGrabber.reopen``), the
        original ``_cam_handle`` is invalid and the same camera now
        has a brand-new handle in the rebuilt grabber's enumeration.
        We adopt that fresh handle here while preserving everything
        else about this :class:`KyfgCamera` (the per-frame callback
        wiring, the GUI's ``backend`` reference inside the
        :class:`CameraPipeline`, etc.) so the recovery is invisible
        to the rest of the app.

        Caller MUST have already ``close()``-d this camera before
        calling. The camera is left in a non-open state; caller is
        expected to ``open()`` + ``configure()`` it next.
        """
        if self._opened:
            raise RuntimeError(
                f"{self.label}: adopt_handle called while still opened; "
                "close() first to avoid leaking the old handle"
            )
        self._info = info
        self._cam_handle = info.handle
        self._configured = False
        self._width = 0
        self._height = 0
        log.info(
            "%s adopted fresh handle (info.index=%d, handle=%s)",
            self.label, info.index, info.handle,
        )

    def device_reset_issue(self) -> tuple[bool, str]:
        """Send the GenICam ``DeviceReset`` command and return immediately.

        Companion to :meth:`device_reset_wait`: lets a caller fan out
        resets across multiple cameras and then pay a single shared
        settle window instead of summing 3-5 s per camera. Use
        :meth:`device_reset` when you want the monolithic
        issue+wait+verify sequence on a single camera.
        """
        if not self._opened:
            return False, "not open"
        try:
            status = self._kyfg.KYFG_CameraExecuteCommand(
                self._cam_handle, "DeviceReset"
            )
            status_int = int(status[0]) if isinstance(status, tuple) else int(status)
        except Exception as exc:
            msg = str(exc).splitlines()[0] or type(exc).__name__
            log.warning("%s DeviceReset raised: %s", self.label, msg)
            return False, f"exec-raise: {msg}"
        if status_int != _FGSTATUS_OK:
            log.warning(
                "%s DeviceReset status=%s",
                self.label, _status_name(status_int),
            )
            return False, f"status={_status_name(status_int)}"
        log.info("%s DeviceReset issued (no wait)", self.label)
        return True, "issued"

    def device_reset_verify(self) -> tuple[bool, str]:
        """Read one cheap feature and confirm the SDK can still talk to
        the camera after a recent :meth:`device_reset_issue`.

        Used by callers (e.g. ``CameraManager.open_all``) that batch
        DeviceResets across multiple cameras and then sleep ONCE for
        the shared settle window before asking each camera whether
        it's actually back. Separating the sleep from the verify
        avoids the trap where calling a monolithic "wait" in a loop
        adds ``settle_s`` per camera instead of one ``settle_s``
        total.

        Returns ``(True, "OK")`` on a successful read, ``(False,
        "...")`` otherwise. We deliberately do not raise on a bad
        readback -- the caller's follow-up ``configure()`` will
        surface a hard fault if the camera is really gone.
        """
        try:
            self._kyfg.KYFG_GetCameraValueInt(self._cam_handle, "Width")
            return True, "verified-readback-OK"
        except Exception as exc:
            log.warning(
                "%s DeviceReset: post-reset readback raised: %s",
                self.label, exc,
            )
            return False, f"verify-readback-failed:{type(exc).__name__}"

    def device_reset_wait(self, settle_s: float = 5.0) -> tuple[bool, str]:
        """Block ``settle_s`` and verify the camera is back via a small read.

        Use on a single camera when you don't need the issue / wait /
        verify split; for multi-camera bring-up, prefer the explicit
        :meth:`device_reset_verify` after one shared sleep.
        """
        time.sleep(float(settle_s))
        ok, detail = self.device_reset_verify()
        log.info("%s DeviceReset wait complete (%s)", self.label, detail)
        return ok, detail

    def device_reset(self, settle_s: float = 5.0) -> tuple[bool, str]:
        """Issue ``DeviceReset`` and wait for the camera to re-attach.

        The strongest software recovery we have for a stuck firmware
        state. Mirrors what KAYA Vision Point's "Device Reset" button
        does: the camera reboots its firmware, drops + re-establishes
        the CXP link, and comes back with all GenICam features at
        firmware defaults.

        Use this when ``load_factory_userset`` alone wasn't enough --
        observed in the wild on Iron 661 when an unrelated software
        crash (or a previous app run that exited mid-stream) leaves
        the camera in a state where it accepts feature writes but
        silently ignores Line1 hardware triggers. Vision Point's
        recovery sequence is "Device Reset" followed by re-applying
        Width / Height / TriggerMode / TriggerSource; we replicate
        that by following this call with :meth:`load_factory_userset`
        + :meth:`configure`.

        Caveats:

        * The KYFG handle stays valid across the reset (the SDK
          re-attaches transparently when the camera reappears on the
          CXP link), so we do NOT close + reopen the camera here.
        * ``settle_s`` is conservative -- Iron 661 tends to come back
          in 2-3 s; 5 s gives slack for worst-case CXP renegotiation.
        * Best-effort: a camera / firmware build that doesn't expose
          ``DeviceReset`` returns ``(False, "...")`` and the caller
          continues with whatever weaker recovery it has.

        Should be called while the camera is **not** streaming. If a
        stream is armed, disarm it first.

        For multi-camera bring-up where you want to fan out the
        resets and pay a single shared settle window, use
        :meth:`device_reset_issue` + :meth:`device_reset_wait`
        instead -- this method just composes those two and is
        kept for the single-camera convenience case.
        """
        ok, detail = self.device_reset_issue()
        if not ok:
            return False, detail
        return self.device_reset_wait(settle_s)

    def load_factory_userset(self) -> tuple[bool, str]:
        """Reset the camera's GenICam features to its factory user-set.

        Defensive recovery for cameras that have been left in a stuck
        firmware state where the SDK's readback returns a shadowed
        value but the actual sensor / acquisition state machine is
        configured differently. We've seen this happen after a mid-
        stream ``TriggerMode`` flip on Iron 661: the camera ends up
        running on its internal frame-rate generator with auto-
        extended exposure, while still claiming
        ``TriggerMode=On / TriggerSource=Line1`` to readback. The
        only reliable way to clear it (short of reflashing firmware)
        is to load the factory ``Default`` user-set, which is exactly
        what KAYA Vision Point's "UserSetLoad" button does.

        Best-effort: the camera may not expose ``UserSetSelector`` /
        ``UserSetLoad`` (depends on firmware build), in which case
        this is a quiet no-op. Any failure is logged and swallowed,
        not raised, so a misbehaving camera does not abort bring-up.

        Should be called once, after ``open()`` and before
        ``configure()``, while the camera is **not** streaming.
        """
        if not self._opened:
            return False, "not open"
        # Try the most common factory-default selector value names
        # first ("Default"), fall back to any synonyms the camera
        # might use ("Factory", "FactoryDefaults"). We stop on the
        # first one that the camera accepts.
        selector_ok = False
        selector_detail = ""
        for selector_value in ("Default", "Factory", "FactoryDefaults"):
            ok, detail = self._set_param("UserSetSelector", selector_value)
            if ok:
                selector_ok = True
                selector_detail = f"UserSetSelector={selector_value} ({detail})"
                break
            selector_detail = (
                f"UserSetSelector={selector_value} rejected ({detail})"
            )
        if not selector_ok:
            log.info(
                "%s factory user-set: skipped (%s)",
                self.label, selector_detail,
            )
            return False, selector_detail

        try:
            status = self._kyfg.KYFG_CameraExecuteCommand(
                self._cam_handle, "UserSetLoad"
            )
            status_int = int(status[0]) if isinstance(status, tuple) else int(status)
        except Exception as exc:
            msg = str(exc).splitlines()[0] or type(exc).__name__
            log.warning(
                "%s factory user-set: UserSetLoad raised: %s",
                self.label, msg,
            )
            return False, f"exec-raise: {msg}"
        if status_int != _FGSTATUS_OK:
            log.warning(
                "%s factory user-set: UserSetLoad status=%s",
                self.label, _status_name(status_int),
            )
            return False, f"status={_status_name(status_int)}"
        # Give the firmware a beat to propagate the new feature set
        # before we start writing on top of it. Empirically 100 ms is
        # plenty; we use 200 to be conservative.
        time.sleep(0.2)
        log.info(
            "%s factory user-set loaded (%s)", self.label, selector_detail,
        )
        return True, selector_detail

    def configure(self, config: Optional[dict] = None) -> list[str]:
        """Apply ``DEFAULT_CAMERA_CONFIG`` (or override). Each set is
        verified by readback. Returns a list of human-readable status
        lines for the caller to mirror into a GUI log.
        """
        if not self._opened:
            raise RuntimeError(f"{self.label}: configure before open")
        cfg = config or DEFAULT_CAMERA_CONFIG
        lines: list[str] = []
        for param, value in cfg.items():
            # Defensive double-write for ExposureTime. We've seen
            # cam 2439006 latch into a state where ``ExposureTime``
            # writes succeed at the GenICam protocol level (return
            # OK + readback matches) but the actual sensor exposure
            # timer keeps using a stale value. The user found in
            # KAYA Vision Point that writing a *different* value
            # first (e.g. 10000 us, which the camera quantizes to
            # ~9997 us) and then writing the intended value breaks
            # the staleness -- the camera firmware is forced to
            # re-evaluate the exposure timer.
            #
            # Reproducing that pattern in our bring-up makes the
            # sensor exposure actually match the configured value
            # on every session, even after the camera has been left
            # in a stuck state by a previous run. We do it ONLY for
            # ``ExposureTime`` -- broadening it to other params
            # without evidence would just add risk (and we
            # specifically do NOT want to flip ``TriggerMode``
            # mid-configure).
            if param == "ExposureTime":
                kick_value = self._EXPOSURE_KICK_US
                if (
                    isinstance(value, (int, float))
                    and abs(float(value) - kick_value) < 1.0
                ):
                    # If the configured value happens to coincide
                    # with our kick value, pick a different kick
                    # so the firmware actually sees a change.
                    kick_value = kick_value * 2.0
                kick_ok, kick_detail = self._set_param(
                    "ExposureTime", float(kick_value)
                )
                lines.append(
                    f"ExposureTime[kick]={kick_value:.0f}: "
                    f"{'OK' if kick_ok else 'FAIL'} ({kick_detail})"
                )
            ok, detail = self._set_param(param, value)
            lines.append(f"{param}={value}: {'OK' if ok else 'FAIL'} ({detail})")
        # Read back canonical Width/Height/PixelFormat for callback math
        self._read_camera_format()
        self._configured = True
        return lines

    def arm(self, on_frame: FrameCallback) -> None:
        """Create the stream, register the callback, and call
        ``KYFG_CameraStart`` so the sensor is armed and Line1 triggers
        from the PCB will produce buffers.

        ``on_frame`` is called from the SDK's internal callback thread for
        every completed buffer. It receives a 2x-subsampled uint16
        ``ndarray`` of the raw bayered frame. Keep the callback short.
        """
        if self._armed:
            log.debug("%s arm: already armed", self.label)
            return
        if not self._configured:
            raise RuntimeError(f"{self.label}: arm before configure")
        if self._width == 0:
            self._read_camera_format()

        k = self._kyfg
        self._frame_cb = on_frame
        self._cb_count = 0
        self._first_frame_logged = False
        self._cb_error_count = 0
        self._last_image_id = -1
        self._image_id_gap_count = 0
        self._image_id_total_missing = 0
        self._image_id_lookup_failed = False
        self._image_id_cmd = None
        self._last_ts_ns = -1
        self._ts_lookup_failed = False
        self._ts_gap_count = 0
        self._ts_total_missing = 0

        status, self._stream_handle = k.KYFG_StreamCreateAndAlloc(
            self._cam_handle, self.NUM_BUFFERS, 0
        )
        if int(status) != _FGSTATUS_OK:
            raise RuntimeError(
                f"{self.label}: KYFG_StreamCreateAndAlloc failed "
                f"(status={_status_name(int(status))})"
            )
        # Probe the actual row stride so the callback's ndarray view has
        # the right shape (the SDK may pad rows above width*bpp).
        try:
            _, payload = k.KYFG_StreamGetInfo(
                self._stream_handle,
                k.KY_STREAM_INFO_CMD.KY_STREAM_INFO_PAYLOAD_SIZE,
            )
            self._row_stride_bytes = max(
                int(payload) // max(self._height, 1),
                self._width * self._bytes_per_pixel,
            )
        except Exception:
            self._row_stride_bytes = self._width * self._bytes_per_pixel

        k.KYFG_StreamBufferCallbackRegister(
            self._stream_handle, self._buffer_callback, None
        )
        # 0 == continuous (the only value that makes sense for our
        # PCB-driven Line1 use case).
        start_status = k.KYFG_CameraStart(
            self._cam_handle, self._stream_handle, 0
        )
        log.info(
            "%s armed (stream=%s, %dx%d %s, stride=%d, start=%s)",
            self.label, self._stream_handle,
            self._width, self._height, self._pixel_format,
            self._row_stride_bytes, start_status,
        )
        self._armed = True

    def disarm(self) -> None:
        """Stop acquisition and tear down the stream. Idempotent."""
        if not self._armed:
            return
        k = self._kyfg
        try:
            k.KYFG_CameraStop(self._cam_handle)
        except Exception as exc:
            log.warning("%s KYFG_CameraStop: %s", self.label, exc)
        if self._stream_handle is not None:
            try:
                k.KYFG_StreamBufferCallbackUnregister(
                    self._stream_handle, self._buffer_callback
                )
            except Exception as exc:
                log.debug("%s callback unregister: %s", self.label, exc)
            try:
                k.KYFG_StreamDelete(self._stream_handle)
            except Exception as exc:
                log.debug("%s stream delete: %s", self.label, exc)
            self._stream_handle = None
        self._armed = False
        # Drop the user callback so a stray late-arriving SDK callback
        # doesn't reach into a destroyed Qt object.
        self._frame_cb = None
        log.info(
            "%s disarmed (cb_count=%d, IMAGEID last=%d, IMAGEID gaps=%d, "
            "IMAGEID missing=%d, TS gaps=%d, TS missed=%d)",
            self.label, self._cb_count, self._last_image_id,
            self._image_id_gap_count, self._image_id_total_missing,
            self._ts_gap_count, self._ts_total_missing,
        )

    def close(self) -> None:
        """Disarm if needed, then close the camera handle."""
        if self._armed:
            self.disarm()
        if not self._opened:
            return
        try:
            self._kyfg.KYFG_CameraClose(self._cam_handle)
        except Exception:
            log.warning("%s KYFG_CameraClose failed", self.label, exc_info=True)
        self._opened = False
        log.info("%s closed", self.label)

    # ── parameter set / readback ────────────────────────────────────────

    def _set_param(self, param: str, value: Any) -> tuple[bool, str]:
        """Set one camera parameter, verify by readback. Returns
        ``(ok, detail)`` where ``detail`` is a short status string the
        caller can include in a log line.
        """
        k = self._kyfg
        try:
            if isinstance(value, bool):
                result = k.KYFG_SetCameraValueBool(self._cam_handle, param, value)
            elif isinstance(value, int):
                result = k.KYFG_SetCameraValueInt(self._cam_handle, param, value)
            elif isinstance(value, float):
                result = k.KYFG_SetCameraValueFloat(self._cam_handle, param, value)
            elif isinstance(value, str):
                result = k.KYFG_SetCameraValueEnum_ByValueName(
                    self._cam_handle, param, value
                )
            else:
                return False, f"unsupported type {type(value).__name__}"
        except Exception as exc:
            msg = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            return False, msg

        status, _ = _unpack_result(result)
        if status != _FGSTATUS_OK:
            return False, f"status={_status_name(status)}"
        actual = self._read_param(param, value)
        if actual is None:
            return True, "set (readback unavailable)"
        if isinstance(value, float):
            ok = abs(float(actual) - value) <= max(1e-3, abs(value) * 1e-3)
        elif isinstance(value, str):
            ok = str(actual).strip() == value
        else:
            ok = actual == value
        if ok:
            return True, f"verified={actual}"
        return False, f"readback={actual}"

    def _prefer_analog_gain_selector(self) -> tuple[bool, str]:
        """Select analog gain stage via SFNC ``GainSelector`` when available."""
        if not self._opened:
            return False, "not open"
        for sel in ANALOG_GAIN_SELECTOR_PRESETS:
            ok, _detail = self._set_param("GainSelector", sel)
            if ok:
                return True, sel
        return False, "GainSelector analog preset not available"

    def read_exposure_time_us(self) -> Optional[float]:
        """Read ``ExposureTime`` (µs), or ``None`` if unavailable."""
        if not self._opened:
            return None
        v = self._read_param("ExposureTime", 0.0)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def read_gain(self) -> Optional[float]:
        """Read ``Gain`` after best-effort ``GainSelector`` → analog preset."""
        if not self._opened:
            return None
        self._prefer_analog_gain_selector()
        v = self._read_param("Gain", 0.0)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def set_exposure_time_us(self, us: float) -> tuple[bool, str]:
        """Runtime exposure write (µs). Uses the same kick-write pattern as :meth:`configure`."""
        if not self._opened:
            return False, "not open"
        value = float(us)
        kick_value = self._EXPOSURE_KICK_US
        if abs(value - kick_value) < 1.0:
            kick_value = kick_value * 2.0
        self._set_param("ExposureTime", float(kick_value))
        return self._set_param("ExposureTime", value)

    def set_gain(self, gain: float) -> tuple[bool, str]:
        """Write ``Gain`` after best-effort ``GainSelector`` → analog preset."""
        if not self._opened:
            return False, "not open"
        self._prefer_analog_gain_selector()
        return self._set_param("Gain", float(gain))

    def _read_param(self, param: str, type_hint: Any) -> Any:
        k = self._kyfg
        try:
            if isinstance(type_hint, bool):
                r = k.KYFG_GetCameraValueBool(self._cam_handle, param)
            elif isinstance(type_hint, int):
                r = k.KYFG_GetCameraValueInt(self._cam_handle, param)
            elif isinstance(type_hint, float):
                r = k.KYFG_GetCameraValueFloat(self._cam_handle, param)
            elif isinstance(type_hint, str):
                r = k.KYFG_GetCameraValueStringCopy(self._cam_handle, param)
            else:
                return None
        except Exception:
            return None
        status, value = _unpack_result(r)
        if status != _FGSTATUS_OK:
            return None
        return value

    def _read_camera_format(self) -> None:
        k = self._kyfg
        self._width = int(
            k.KYFG_GetCameraValueInt(self._cam_handle, "Width")[1]
        )
        self._height = int(
            k.KYFG_GetCameraValueInt(self._cam_handle, "Height")[1]
        )
        try:
            self._pixel_format = str(
                k.KYFG_GetCameraValueStringCopy(self._cam_handle, "PixelFormat")[1]
            ).strip()
        except Exception:
            self._pixel_format = "BayerRG14"
        # 14-bit Bayer is delivered as uint16 (2 bytes/pixel).
        if any(s in self._pixel_format for s in ("10", "12", "14", "16")):
            self._bytes_per_pixel = 2
        else:
            self._bytes_per_pixel = 1

    # ── SDK callback ────────────────────────────────────────────────────

    def _buffer_callback(self, buff_handle: Any, _user_context: Any) -> None:
        """SDK calls us per completed buffer (KAYA worker thread).

        We:
          1. Build a zero-copy uint16 view over the SDK buffer.
          2. Make a fresh small uint16 ndarray by ``SUBSAMPLE_FACTOR`` x
             decimating the view (always; needed for preview).
          3. If ``_emit_full_res`` is True, copy the entire view into
             the next free pool slot (the fast path:
             ``set_full_res_pool`` was called; the copy targets a
             pre-allocated shared-memory buffer the writer subprocess
             will read directly) OR, as a fallback, into a freshly
             allocated ndarray.
          4. Hand the small array and the full payload (slot index,
             ndarray, or ``None``) to ``self._frame_cb``.

        Anything raised inside the callback is logged but never
        propagated -- letting an exception escape to KAYA's worker
        crashes the whole SDK on Windows.
        """
        self._cb_count += 1
        cb = self._frame_cb
        if cb is None:
            return
        try:
            k = self._kyfg
            # IMAGEID = camera-side CXP source-image index. Reading it
            # here (cheap) lets us distinguish two failure modes when
            # frames go missing:
            #   * gap in IMAGEID  -> the camera/CXP path lost frames;
            #                        the sensor never produced them or
            #                        the KAYA driver dropped them
            #                        before our callback was invoked.
            #   * no gap, but our pipeline shows a shortfall -> we
            #                        are dropping frames downstream of
            #                        the SDK callback.
            image_id: Optional[int] = None
            if not self._image_id_lookup_failed:
                if self._image_id_cmd is None:
                    try:
                        self._image_id_cmd = int(
                            k.KY_STREAM_BUFFER_INFO_CMD.KY_STREAM_BUFFER_INFO_IMAGEID
                        )
                    except Exception:
                        # Fallback to the documented literal value.
                        self._image_id_cmd = 16
                try:
                    image_id_result = k.KYFG_BufferGetInfo(
                        buff_handle, self._image_id_cmd
                    )
                    # KYFG_BufferGetInfo returns
                    # (status, value, size, type); we only need the value.
                    image_id = int(image_id_result[1])
                except Exception as exc:
                    self._image_id_lookup_failed = True
                    log.warning(
                        "%s IMAGEID lookup failed (cmd=%s): %r -- gap "
                        "detection disabled for this arm",
                        self.label, self._image_id_cmd, exc,
                    )
            if image_id is not None:
                if self._last_image_id >= 0 and image_id > self._last_image_id + 1:
                    missing = image_id - self._last_image_id - 1
                    self._image_id_gap_count += 1
                    self._image_id_total_missing += missing
                    log.warning(
                        "%s IMAGEID gap: jumped %d -> %d (missing %d, "
                        "gaps so far=%d, total missing=%d)",
                        self.label, self._last_image_id, image_id,
                        missing, self._image_id_gap_count,
                        self._image_id_total_missing,
                    )
                self._last_image_id = image_id

            # TIMESTAMP-based gap detection. Iron 661 doesn't fill in
            # IMAGEID, but KYFG does timestamp every buffer. Inter-
            # frame interval at 211 ms/trigger -> normal gap ~211 ms;
            # >320 ms means at least one trigger was lost.
            ts_ns: Optional[int] = None
            if not self._ts_lookup_failed:
                try:
                    ts_result = k.KYFG_BufferGetInfo(
                        buff_handle,
                        k.KY_STREAM_BUFFER_INFO_CMD.KY_STREAM_BUFFER_INFO_TIMESTAMP,
                    )
                    ts_ns = int(ts_result[1])
                except Exception as exc:
                    self._ts_lookup_failed = True
                    log.warning(
                        "%s TIMESTAMP lookup failed: %r -- gap detection "
                        "disabled for this arm", self.label, exc,
                    )
            if ts_ns is not None:
                if self._last_ts_ns >= 0:
                    delta = ts_ns - self._last_ts_ns
                    if delta > self._ts_gap_warn_ns:
                        # Estimate how many triggers were skipped using
                        # the actual measured trigger period (211 ms).
                        # Round to the nearest int and clamp to >=1.
                        est_missing = max(1, int(round(delta / 211_000_000)) - 1)
                        self._ts_gap_count += 1
                        self._ts_total_missing += est_missing
                        log.warning(
                            "%s TS gap: %.1f ms since prior buffer "
                            "(~%d trigger(s) missed; gaps so far=%d, "
                            "total missed=%d)",
                            self.label, delta / 1e6, est_missing,
                            self._ts_gap_count, self._ts_total_missing,
                        )
                self._last_ts_ns = ts_ns

            base_result = k.KYFG_BufferGetInfo(
                buff_handle,
                k.KY_STREAM_BUFFER_INFO_CMD.KY_STREAM_BUFFER_INFO_BASE,
            )
            size_result = k.KYFG_BufferGetInfo(
                buff_handle,
                k.KY_STREAM_BUFFER_INFO_CMD.KY_STREAM_BUFFER_INFO_SIZE,
            )
            ptr = base_result[1]
            buf_size = size_result[1]
            if not ptr or not buf_size:
                return

            row_bytes = self._width * self._bytes_per_pixel
            stride = self._row_stride_bytes or row_bytes
            c_type = ctypes.c_uint16 if self._bytes_per_pixel == 2 else ctypes.c_uint8

            if stride == row_bytes:
                pixel_count = self._width * self._height
                arr_type = ctypes.POINTER(c_type * pixel_count)
                data = ctypes.cast(ptr, arr_type).contents
                view = np.ctypeslib.as_array(data).reshape(
                    self._height, self._width
                )
            else:
                stride_pixels = stride // self._bytes_per_pixel
                total_pixels = stride_pixels * self._height
                arr_type = ctypes.POINTER(c_type * total_pixels)
                data = ctypes.cast(ptr, arr_type).contents
                view = np.ctypeslib.as_array(data).reshape(
                    self._height, stride_pixels
                )[:, :self._width]

            # 1) Small (preview) copy. ALWAYS produced. The copy() is
            # required: ``view`` references the SDK buffer which the
            # SDK reuses immediately after the callback returns.
            # Column offset 1: BayerRG puts G at (0,1), so a step-4 grid from
            # there is ALL green -- the channel that clips first, which is what
            # the M4 exposure histogram must see (from (0,0) it would be all red
            # and under-report saturation ~1.5-2x). Same 1-px shift for every
            # consumer; ROI/tile maths is in multiples of the step so nothing cares.
            step = self.SUBSAMPLE_FACTOR
            if view.dtype == np.uint16:
                small = view[::step, 1::step].copy()
            else:
                small = view[::step, 1::step].astype(np.uint16, copy=True)

            # 2) Full-res copy. ONLY produced when the pipeline has
            # explicitly opted in via set_full_res_emission(True). The
            # SDK reuses the underlying buffer the moment we return,
            # so a copy (not a view) is mandatory if downstream code
            # is going to hand the array off to a writer.
            #
            # Two paths:
            #   * ``self._full_pool`` is set: pop a free slot from the
            #     pool, copy the SDK buffer's bytes into the slot, and
            #     emit the slot index. The downstream writer
            #     subprocess will read the bytes via the SharedMemory
            #     mapping with no extra IPC. This is the fast path --
            #     no per-frame Python allocation, no Windows page-zero
            #     hit, no GIL held during the copy (numpy releases it
            #     for the bulk memcpy).
            #   * ``self._full_pool`` is None: legacy path. Allocate a
            #     fresh ndarray; the writer will read it from the
            #     in-process queue. ~17-25 ms slower per frame on
            #     Windows due to page-zeroing.
            #
            # If the pool is set but has no free slots (writer is
            # behind), drop full-res for this frame and bump a
            # counter; the small (preview) frame is still emitted so
            # the operator's preview keeps working.
            # Sentinel discipline (read by AcquisitionThread):
            #   * ``None``   -- emission was OFF at the time of this
            #                  callback (focus / preview-only modes, or
            #                  a stale frame still in the queue from
            #                  before the GUI flipped emission to True).
            #                  NEVER counted as a "writer drop" --
            #                  there's no full-res payload to lose.
            #   * ``-1``     -- emission was ON but the pool had no free
            #                  slot. This IS a real writer-behind drop.
            #   * int >= 0   -- slot index; SDK already copied the frame
            #                  into the pool at this slot. Submit to
            #                  writer subprocess via the work queue.
            #   * ndarray    -- legacy in-process path (no pool). Same
            #                  semantics as a slot but ferried as bytes.
            full: Any = None
            if self._emit_full_res:
                pool = self._full_pool
                if pool is not None:
                    slot_idx = pool.acquire_slot_nowait()
                    if slot_idx is None:
                        self._full_pool_drops += 1
                        full = -1  # emission ON, pool empty -> real drop
                        # Don't log every drop; the pipeline already
                        # emits a "writer queue full" line at sane
                        # cadence based on its own counters.
                    else:
                        slot_view = pool.slot_view(slot_idx)
                        if view.dtype == np.uint16:
                            slot_view[...] = view
                        else:
                            slot_view[...] = view.astype(np.uint16, copy=False)
                        full = slot_idx
                else:
                    if view.dtype == np.uint16:
                        full = view.copy()
                    else:
                        full = view.astype(np.uint16, copy=True)

            if not self._first_frame_logged:
                self._first_frame_logged = True
                if isinstance(full, (int, np.integer)):
                    full_desc = f"slot={int(full)} (pool path)"
                elif full is None:
                    full_desc = "off"
                else:
                    full_desc = "ON (legacy ndarray path)"
                log.info(
                    "%s first buffer: %dx%d %s -> small %dx%d uint16 "
                    "(%dx subsampled), full=%s, IMAGEID=%s",
                    self.label, self._width, self._height, self._pixel_format,
                    small.shape[1], small.shape[0], step,
                    full_desc,
                    image_id if image_id is not None else "n/a",
                )

            cb(small, full)

        except Exception as exc:
            self._cb_error_count += 1
            if self._cb_error_count <= 5:
                log.exception("%s buffer callback failed: %s", self.label, exc)
            elif self._cb_error_count == 6:
                log.warning(
                    "%s buffer callback failed >5 times; suppressing further tracebacks",
                    self.label,
                )
