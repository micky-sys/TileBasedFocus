"""Frame acquisition from Kaya Instruments grabbers via the KYFGLib Python API.

Requires:
  - Vision Point SDK installed with KAYA_VISION_POINT_BIN_PATH env var set
  - KYFGLib.py located in that bin path
"""

import atexit
import sys
import os
import ctypes
import logging
import time
import threading
from typing import Callable, Optional

import cv2
import numpy as np

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy-import KYFGLib — it lives in the Kaya bin directory, not on PYTHONPATH
# ---------------------------------------------------------------------------
_kyfg = None  # module reference, set by _ensure_kyfg()


def _ensure_kyfg():
    global _kyfg
    if _kyfg is not None:
        return
    bin_path = os.environ.get("KAYA_VISION_POINT_BIN_PATH", "")
    if not bin_path:
        raise RuntimeError(
            "KAYA_VISION_POINT_BIN_PATH environment variable is not set. "
            "Is the Kaya Vision Point SDK installed?"
        )
    # Use the VP2 adapter (KYFGLibA) so the legacy Python API talks to the
    # Vision Point II backend.  This must be set before importing KYFGLib.
    os.environ["WithAdapter"] = "1"
    if bin_path not in sys.path:
        sys.path.insert(0, bin_path)
    import KYFGLib  # noqa: E402 — path-dependent import
    _kyfg = KYFGLib


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def scan_grabbers() -> list[dict]:
    """Return a list of dicts describing each detected frame grabber."""
    _ensure_kyfg()
    status, dev_count = _kyfg.KY_DeviceScan()
    grabbers = []
    for i in range(dev_count):
        status, info = _kyfg.KY_DeviceInfo(i)
        grabbers.append({
            "index": i,
            "name": info.szDeviceDisplayName,
            "pid": info.DevicePID,
            "is_virtual": info.isVirtual,
        })
    return grabbers


# ---------------------------------------------------------------------------
# Default camera configuration for Iron661
# ---------------------------------------------------------------------------

# Order matters: auto-modes must be turned OFF before we try to set their
# associated manual values, otherwise the camera silently rejects the manual
# write (e.g. setting ExposureMode=Timed while ExposureAuto=Continuous fails).
# We saw this in the field on cam 2439006: it had been left in
# ExposureAuto=Continuous by Vision Point, every "Set" looked OK in the log
# but exposure stayed at the auto-computed (huge) value.
DEFAULT_CAMERA_CONFIG = {
    "TriggerMode": "On",
    "TriggerSource": "Line1",
    "TriggerActivation": "RisingEdge",
    # Allow a new trigger to arrive while the previous frame is still being
    # read out. Some firmware names this differently; a failure to set is
    # just logged and ignored. Without this, when readout > trigger period
    # the camera silently drops every other trigger.
    "TriggerOverlap": "ReadOut",
    "Width": 13400,
    "Height": 9528,
    "PixelFormat": "BayerRG14",
    # disable auto first so the manual writes below are accepted
    "ExposureAuto": "Off",
    "GainAuto": "Off",          # best-effort; Iron 661 firmware may not expose it
    # manual values
    "ExposureMode": "Timed",
    "ExposureTime": 250.0,
    "Gain": 3.0,
    # Pin the analog black level instead of letting AnalogContinuous drift
    # per-camera. We saw this enabled with different state across the three
    # cameras and it can cause the perceived "exposure too high" behavior.
    "BlackLevelAuto": "Off",
    "BlackLevel": 0.0,
    # white-balance loop for color cameras (does not touch Gain on Iron 661)
    "BalanceWhiteAuto": "Continuous",
}

# Note: AcquisitionFrameRate is intentionally NOT set here. On the Iron 661
# this is only meaningful in free-run (TriggerMode=Off), the camera rejects
# writes above its computed max, and the stored value persists across
# sessions — so reading it back tells us nothing useful about link health.

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


def _unpack_result(result):
    """KYFGLib functions return either ``status`` or ``(status, ...)``.

    Returns ``(status, value)`` where ``value`` is the second element if any.
    """
    if isinstance(result, tuple):
        status = result[0]
        value = result[1] if len(result) >= 2 else None
        return int(status), value
    return int(result), None



# Map Bayer pattern names to OpenCV demosaic codes
_BAYER_DEMOSAIC = {
    "BayerRG": cv2.COLOR_BayerRG2GRAY,
    "BayerGR": cv2.COLOR_BayerGR2GRAY,
    "BayerGB": cv2.COLOR_BayerGB2GRAY,
    "BayerBG": cv2.COLOR_BayerBG2GRAY,
}


# ---------------------------------------------------------------------------
# FrameGrabber
# ---------------------------------------------------------------------------

class FrameGrabber:
    """Acquire frames from cameras on a Kaya frame grabber.

    Typical usage::

        fg = FrameGrabber()
        fg.open(grabber_index=0, camera_index=0)
        fg.configure()                    # applies DEFAULT_CAMERA_CONFIG
        fg.start(on_frame=my_callback)    # callback receives np.ndarray (grayscale)
        ...
        fg.switch_camera(1)               # switch to another camera
        ...
        fg.stop()
        fg.close()
    """

    NUM_BUFFERS = 8

    def __init__(self) -> None:
        _ensure_kyfg()
        self._fg_handle = None
        self._cam_handles: list = []
        self._cam_handle = None
        self._active_camera_index: int = -1
        self._cameras: list[dict] = []
        self._stream_handle = None
        self._on_frame: Optional[Callable[[np.ndarray], None]] = None
        self._raw_processor: Optional[Callable[[np.ndarray], np.ndarray]] = None
        self._width = 0
        self._height = 0
        self._pixel_format = ""
        self._configured_pixel_format = "BayerRG14"
        self._bytes_per_pixel = 1
        self._demosaic_code: Optional[int] = None
        self._running = False
        self._buffer_handles: list = []

    def set_raw_processor(
        self, processor: Optional[Callable[[np.ndarray], np.ndarray]]
    ) -> None:
        """Set (or clear) a function that receives the full-res raw view.

        When set, the callback calls ``processor(view)`` instead of the
        default 2× subsample and passes the return value to *on_frame*.
        The view is only valid for the duration of the call (zero-copy).
        """
        self._raw_processor = processor

    @property
    def cameras(self) -> list[dict]:
        """List of detected cameras with index, model, serial, etc."""
        return list(self._cameras)

    @property
    def active_camera_index(self) -> int:
        return self._active_camera_index

    # ── Enumeration ───────────────────────────────────────────────────

    def _enumerate_cameras(self) -> None:
        """Populate self._cameras from the currently open grabber."""
        status, cam_handles = _kyfg.KYFG_UpdateCameraList(self._fg_handle)
        self._cam_handles = cam_handles
        self._cameras = []
        for idx, ch in enumerate(cam_handles):
            info_dict: dict = {"index": idx, "handle": ch}
            try:
                _, info = _kyfg.KYFG_CameraInfo2(ch)
                info_dict["model"] = info.deviceModelName.strip()
                info_dict["vendor"] = info.deviceVendorName.strip()
                info_dict["device_id"] = info.deviceID.strip()
                info_dict["firmware"] = info.deviceFirmwareVersion.strip()
            except Exception:
                info_dict["model"] = "unknown"
            self._cameras.append(info_dict)
        log.info(
            "Found %d camera(s): %s",
            len(self._cameras),
            [f"#{c['index']} {c.get('device_id', '?')}" for c in self._cameras],
        )

    def scan_cameras(self, grabber_index: int) -> list[dict]:
        """Open the grabber (temporarily) and list connected cameras."""
        _ensure_kyfg()
        fg = _kyfg.KYFG_Open(grabber_index)
        if isinstance(fg, tuple):
            fg = fg[0]
        status, cam_handles = _kyfg.KYFG_UpdateCameraList(fg)
        cameras = []
        for idx, ch in enumerate(cam_handles):
            try:
                _, info = _kyfg.KYFG_CameraInfo2(ch)
                cameras.append({
                    "index": idx,
                    "handle": ch,
                    "model": info.deviceModelName.strip(),
                    "vendor": info.deviceVendorName.strip(),
                    "device_id": info.deviceID.strip(),
                })
            except Exception:
                cameras.append({"index": idx, "handle": ch, "model": "unknown"})
        _kyfg.KYFG_Close(fg)
        return cameras

    # ── Open / Close ──────────────────────────────────────────────────

    def open(self, grabber_index: int, camera_index: int) -> None:
        _ensure_kyfg()

        # Release any stale resources from a previously crashed session
        try:
            result = _kyfg.KYFG_Open(grabber_index)
            stale_fg = result[0] if isinstance(result, tuple) else result
            _, stale_cams = _kyfg.KYFG_UpdateCameraList(stale_fg)
            for ch in stale_cams:
                try:
                    _kyfg.KYFG_CameraOpen2(ch, None)
                except Exception:
                    pass
                try:
                    _kyfg.KYFG_CameraStop(ch)
                except Exception:
                    pass
                try:
                    _kyfg.KYFG_CameraClose(ch)
                except Exception:
                    pass
            _kyfg.KYFG_Close(stale_fg)
            time.sleep(1)
            log.info("Cleaned up stale grabber session")
        except Exception:
            log.debug("No stale session to clean up")

        result = _kyfg.KYFG_Open(grabber_index)
        self._fg_handle = result[0] if isinstance(result, tuple) else result
        log.info("Opened grabber %d → handle %s", grabber_index, self._fg_handle)

        self._enumerate_cameras()

        if camera_index >= len(self._cam_handles):
            raise IndexError(
                f"Camera index {camera_index} out of range "
                f"(found {len(self._cam_handles)} cameras)"
            )
        self._open_camera(camera_index)

        atexit.register(self.close)

    def _open_camera(self, camera_index: int) -> None:
        """Open a specific camera (by index) on the already-open grabber."""
        self._cam_handle = self._cam_handles[camera_index]
        self._active_camera_index = camera_index
        log.info(
            "Opening camera %d (handle %s, id=%s)...",
            camera_index, self._cam_handle,
            self._cameras[camera_index].get("device_id", "?"),
        )
        _kyfg.KYFG_CameraOpen2(self._cam_handle, None)
        log.info("Camera %d opened successfully", camera_index)

    def _close_camera(self) -> None:
        """Close the currently open camera (stop acquisition first)."""
        if self._running:
            self.stop()
        if self._cam_handle is not None:
            try:
                _kyfg.KYFG_CameraClose(self._cam_handle)
            except Exception:
                log.warning("Error closing camera", exc_info=True)
            self._cam_handle = None
            self._active_camera_index = -1
            self._width = 0
            self._height = 0

    def switch_camera(self, camera_index: int) -> None:
        """Switch to a different camera. Stops acquisition, closes current,
        opens and configures the new camera, then restarts acquisition."""
        if camera_index == self._active_camera_index:
            return
        if camera_index >= len(self._cam_handles):
            raise IndexError(
                f"Camera index {camera_index} out of range "
                f"(found {len(self._cam_handles)} cameras)"
            )
        was_running = self._running
        saved_callback = self._on_frame

        self._close_camera()
        self._open_camera(camera_index)
        self.configure()

        if was_running and saved_callback:
            self.start(saved_callback)

    # ── Parameter set / readback (with status checking) ───────────────

    def _set_param(self, handle, param: str, value) -> bool:
        """Set one camera parameter and verify with a readback.

        Returns True on success, False on any SDK failure or readback mismatch.
        Logs informative messages either way.
        """
        try:
            if isinstance(value, bool):
                result = _kyfg.KYFG_SetCameraValueBool(handle, param, value)
            elif isinstance(value, int):
                result = _kyfg.KYFG_SetCameraValueInt(handle, param, value)
            elif isinstance(value, float):
                result = _kyfg.KYFG_SetCameraValueFloat(handle, param, value)
            elif isinstance(value, str):
                result = _kyfg.KYFG_SetCameraValueEnum_ByValueName(handle, param, value)
            else:
                log.error("Unsupported value type for %s: %r", param, type(value))
                return False
        except Exception as exc:
            # KYFGLib raises KYException for non-OK SDK statuses (e.g. when the
            # camera rejects an out-of-range value). The full Python traceback
            # adds noise; report the FGSTATUS as a single-line warning instead.
            msg = str(exc)
            log.warning("Set %s = %s FAILED (%s)", param, value, msg.splitlines()[0] if msg else type(exc).__name__)
            return False

        status, _ = _unpack_result(result)
        if status != _FGSTATUS_OK:
            log.warning(
                "Set %s = %s FAILED (status=%s)", param, value, _status_name(status)
            )
            return False

        actual = self._read_param(handle, param, value)
        if actual is None:
            log.info("Set %s = %s (readback unavailable)", param, value)
            return True

        # numeric tolerance for Float parameters (camera may quantize)
        if isinstance(value, float):
            ok = abs(float(actual) - value) <= max(1e-3, abs(value) * 1e-3)
        elif isinstance(value, str):
            ok = str(actual).strip() == value
        else:
            ok = actual == value

        if ok:
            log.info("Set %s = %s (verified=%s)", param, value, actual)
        else:
            log.warning(
                "Set %s = %s applied but readback returned %s", param, value, actual
            )
        return ok

    def _read_param(self, handle, param: str, type_hint):
        """Best-effort readback of a parameter, dispatched on the type of the
        last value we tried to write. Returns the value or None on failure."""
        try:
            if isinstance(type_hint, bool):
                r = _kyfg.KYFG_GetCameraValueBool(handle, param)
            elif isinstance(type_hint, int):
                r = _kyfg.KYFG_GetCameraValueInt(handle, param)
            elif isinstance(type_hint, float):
                r = _kyfg.KYFG_GetCameraValueFloat(handle, param)
            elif isinstance(type_hint, str):
                r = _kyfg.KYFG_GetCameraValueStringCopy(handle, param)
            else:
                return None
        except Exception:
            return None
        status, value = _unpack_result(r)
        if status != _FGSTATUS_OK:
            return None
        return value

    def _try_read_any(self, handle, param: str, *, grabber: bool = False):
        """Type-aware best-effort read of a parameter.

        Uses ``KYFG_(Get)?CameraValueType`` (or grabber equivalent) to learn
        the property type, then dispatches to the matching typed getter. This
        avoids the trap that ``KYFG_GetCameraValueFloat`` always returns
        ``FGSTATUS_OK`` with whatever was in the c_double buffer, even when
        the underlying SDK call failed — which made our previous best-effort
        chain print uninitialized memory for integer params.
        """
        type_fn = _kyfg.KYFG_GetGrabberValueType if grabber else _kyfg.KYFG_GetCameraValueType
        try:
            tr = type_fn(handle, param)
        except Exception:
            return None
        tstatus, ptype = _unpack_result(tr)
        if tstatus != _FGSTATUS_OK or ptype is None:
            return None

        try:
            from KYFGLib import KY_CAM_PROPERTY_TYPE  # type: ignore
        except Exception:
            try:
                KY_CAM_PROPERTY_TYPE = _kyfg.KY_CAM_PROPERTY_TYPE  # type: ignore[attr-defined]
            except Exception:
                return None

        if grabber:
            int_fn = _kyfg.KYFG_GetGrabberValueInt
            bool_fn = _kyfg.KYFG_GetGrabberValueBool
            float_fn = _kyfg.KYFG_GetGrabberValueFloat
            str_fn = _kyfg.KYFG_GetGrabberValueStringCopy
            enum_fn = _kyfg.KYFG_GetGrabberValueEnum
        else:
            int_fn = _kyfg.KYFG_GetCameraValueInt
            bool_fn = _kyfg.KYFG_GetCameraValueBool
            float_fn = _kyfg.KYFG_GetCameraValueFloat
            str_fn = _kyfg.KYFG_GetCameraValueStringCopy
            enum_fn = _kyfg.KYFG_GetCameraValueEnum

        try:
            if ptype == KY_CAM_PROPERTY_TYPE.PROPERTY_TYPE_INT:
                r = int_fn(handle, param)
            elif ptype == KY_CAM_PROPERTY_TYPE.PROPERTY_TYPE_BOOL:
                r = bool_fn(handle, param)
            elif ptype == KY_CAM_PROPERTY_TYPE.PROPERTY_TYPE_FLOAT:
                r = float_fn(handle, param)
            elif ptype == KY_CAM_PROPERTY_TYPE.PROPERTY_TYPE_STRING:
                r = str_fn(handle, param)
            elif ptype == KY_CAM_PROPERTY_TYPE.PROPERTY_TYPE_ENUM:
                # Prefer the human-readable name for enums.
                rs = str_fn(handle, param)
                sstatus, svalue = _unpack_result(rs)
                if sstatus == _FGSTATUS_OK and svalue is not None:
                    return svalue.strip() if isinstance(svalue, str) else svalue
                r = enum_fn(handle, param)
            else:
                return None
        except Exception:
            return None
        status, value = _unpack_result(r)
        if status != _FGSTATUS_OK or value is None:
            return None
        return value.strip() if isinstance(value, str) else value

    # Read-only features that help diagnose why one camera behaves
    # differently from the others (sensor mode, ROI, achievable frame rate,
    # temperature, etc.). Comparing across cameras quickly reveals stale
    # state from previous app sessions.
    _DIAG_PARAMS: tuple = (
        "DeviceModelName",
        "DeviceSerialNumber",
        "DeviceFirmwareVersion",
        "DeviceTemperature",
        "AcquisitionMode",
        "AcquisitionFrameRate",
        "AcquisitionFrameRateMax",
        "TriggerOverlap",
        "TriggerDelay",
        "OffsetX",
        "OffsetY",
        "Width",
        "Height",
        "BinningHorizontal",
        "BinningVertical",
        "DecimationHorizontal",
        "DecimationVertical",
        "ReverseX",
        "ReverseY",
        # Sanity-readback of what we configured
        "ExposureMode",
        "ExposureAuto",
        "ExposureTime",
        "Gain",
        "GainAuto",
        "BlackLevel",
        "BlackLevelAuto",
        "PixelFormat",
    )

    def _dump_camera_state(self, handle, label: str) -> None:
        """Best-effort log of useful camera features for one camera.

        Many of these will be unavailable on a given firmware — we just
        print "<unavailable>" so we can see which ones DID respond.
        Comparing the output across cameras quickly reveals state that
        explains why one camera is slower than the others.
        """
        log.info("--- camera state dump: %s ---", label)
        for p in self._DIAG_PARAMS:
            v = self._try_read_any(handle, p)
            if v is None:
                log.info("    %s: <unavailable>", p)
            else:
                log.info("    %s: %s", p, v)
        log.info("--- end camera state dump: %s ---", label)

    def configure(self, config: Optional[dict] = None) -> None:
        """Apply camera parameters to the currently active camera.

        Uses DEFAULT_CAMERA_CONFIG if none given. Each parameter is checked
        for SDK status and verified by readback; mismatches are logged.
        """
        if self._cam_handle is None:
            raise RuntimeError("No camera open — call open() first")
        cfg = config or DEFAULT_CAMERA_CONFIG
        if "PixelFormat" in cfg:
            self._configured_pixel_format = str(cfg["PixelFormat"])

        for param, value in cfg.items():
            self._set_param(self._cam_handle, param, value)

        self._read_camera_format()

        cam_label = (
            self._cameras[self._active_camera_index].get("device_id", str(self._active_camera_index))
            if 0 <= self._active_camera_index < len(self._cameras)
            else str(self._active_camera_index)
        )
        self._dump_camera_state(self._cam_handle, cam_label)

    def configure_all_cameras(self, config: Optional[dict] = None) -> None:
        """Apply DEFAULT_CAMERA_CONFIG to every camera on the open grabber.

        Used at startup so cameras the user hasn't selected yet still have
        consistent features (otherwise they keep whatever Vision Point or a
        previous app left behind — we caught cam 2439006 stuck in
        ExposureAuto=Continuous with a multi-second exposure that way).

        The originally-active camera remains the active one after this call.
        """
        if self._fg_handle is None:
            raise RuntimeError("No grabber open — call open() first")
        if self._running:
            raise RuntimeError(
                "Cannot reconfigure all cameras while acquisition is running"
            )

        original_active = self._active_camera_index
        for idx in range(len(self._cam_handles)):
            cam_id = self._cameras[idx].get("device_id", f"#{idx}") if idx < len(self._cameras) else f"#{idx}"
            log.info("Configuring camera %d (id=%s) with default features...", idx, cam_id)
            if idx != self._active_camera_index:
                if self._cam_handle is not None:
                    try:
                        _kyfg.KYFG_CameraClose(self._cam_handle)
                    except Exception:
                        log.exception("Error closing camera %d before reconfigure", self._active_camera_index)
                    self._cam_handle = None
                    self._active_camera_index = -1
                self._open_camera(idx)
            self.configure(config)

        if original_active >= 0 and self._active_camera_index != original_active:
            if self._cam_handle is not None:
                try:
                    _kyfg.KYFG_CameraClose(self._cam_handle)
                except Exception:
                    log.exception("Error closing camera before restoring active")
                self._cam_handle = None
                self._active_camera_index = -1
            self._open_camera(original_active)
            self._read_camera_format()
        log.info("All %d camera(s) configured.", len(self._cam_handles))

    def _read_camera_format(self) -> None:
        """Read back Width, Height, PixelFormat from the camera after configuration."""
        self._width = int(_kyfg.KYFG_GetCameraValueInt(self._cam_handle, "Width")[1])
        self._height = int(_kyfg.KYFG_GetCameraValueInt(self._cam_handle, "Height")[1])
        try:
            self._pixel_format = str(
                _kyfg.KYFG_GetCameraValueStringCopy(self._cam_handle, "PixelFormat")[1]
            )
        except Exception:
            try:
                self._pixel_format = str(
                    _kyfg.KYFG_GetCameraValueString(self._cam_handle, "PixelFormat")[1]
                )
            except Exception:
                self._pixel_format = self._configured_pixel_format
                log.warning("Could not read PixelFormat from camera, using configured value: %s", self._pixel_format)

        # Determine bytes per pixel from format string
        if any(s in self._pixel_format for s in ("10", "12", "14", "16")):
            self._bytes_per_pixel = 2
        else:
            self._bytes_per_pixel = 1

        # Determine Bayer demosaic code
        self._demosaic_code = None
        for pattern, code in _BAYER_DEMOSAIC.items():
            if self._pixel_format.startswith(pattern):
                self._demosaic_code = code
                break

        log.info(
            "Camera format: %dx%d, pixel_format=%s, bpp=%d, demosaic=%s",
            self._width, self._height, self._pixel_format,
            self._bytes_per_pixel, self._demosaic_code is not None,
        )

    def close(self) -> None:
        self._close_camera()
        if self._fg_handle is not None:
            try:
                _kyfg.KYFG_Close(self._fg_handle)
            except Exception:
                log.warning("Error closing grabber", exc_info=True)
            self._fg_handle = None
        log.info("Closed grabber and camera")

    # ── Acquisition ───────────────────────────────────────────────────

    def start(self, on_frame: Callable[[np.ndarray], None]) -> None:
        if self._cam_handle is None:
            raise RuntimeError("No camera open — call open() first")
        if self._width == 0:
            self._read_camera_format()
        self._on_frame = on_frame
        self._cb_count = 0

        # Create stream and allocate buffers in one call
        status, self._stream_handle = _kyfg.KYFG_StreamCreateAndAlloc(
            self._cam_handle, self.NUM_BUFFERS, 0
        )
        log.info("Stream created: %s (status=%s)", self._stream_handle, status)

        # Query the stream's payload size for stride diagnostics
        try:
            _, payload = _kyfg.KYFG_StreamGetInfo(
                self._stream_handle,
                _kyfg.KY_STREAM_INFO_CMD.KY_STREAM_INFO_PAYLOAD_SIZE,
            )
            expected = self._width * self._height * self._bytes_per_pixel
            self._row_stride_bytes = int(payload) // self._height
            log.info(
                "Stream payload=%s, expected=%d, row_stride=%d bytes (width*bpp=%d)",
                payload, expected, self._row_stride_bytes,
                self._width * self._bytes_per_pixel,
            )
        except Exception:
            self._row_stride_bytes = self._width * self._bytes_per_pixel
            log.warning("Could not query stream payload; assuming no padding")

        # Register stream buffer callback
        _kyfg.KYFG_StreamBufferCallbackRegister(
            self._stream_handle, self._buffer_callback, None
        )
        log.info("Buffer callback registered")

        # Start acquisition (0 = continuous)
        start_status = _kyfg.KYFG_CameraStart(self._cam_handle, self._stream_handle, 0)
        log.info("CameraStart status: %s", start_status)
        self._running = True
        log.info("Acquisition started (%d buffers)", self.NUM_BUFFERS)

    def stop(self) -> None:
        if not self._running:
            return
        _kyfg.KYFG_CameraStop(self._cam_handle)
        if self._stream_handle is not None:
            _kyfg.KYFG_StreamBufferCallbackUnregister(
                self._stream_handle, self._buffer_callback
            )
            _kyfg.KYFG_StreamDelete(self._stream_handle)
            self._stream_handle = None
        self._running = False
        log.info("Acquisition stopped")

    # ── Internal callback ─────────────────────────────────────────────

    _cb_count: int = 0

    def _buffer_callback(self, buff_handle, user_context) -> None:
        """Called by the SDK from its internal thread for each completed buffer."""
        self._cb_count += 1
        try:
            base_result = _kyfg.KYFG_BufferGetInfo(
                buff_handle,
                _kyfg.KY_STREAM_BUFFER_INFO_CMD.KY_STREAM_BUFFER_INFO_BASE,
            )
            size_result = _kyfg.KYFG_BufferGetInfo(
                buff_handle,
                _kyfg.KY_STREAM_BUFFER_INFO_CMD.KY_STREAM_BUFFER_INFO_SIZE,
            )

            ptr = base_result[1]
            buf_size = size_result[1]

            if self._cb_count <= 3:
                expected = self._width * self._height * self._bytes_per_pixel
                log.info(
                    "CB #%d: ptr=%s buf_size=%s expected=%d stride=%d",
                    self._cb_count, ptr, buf_size, expected,
                    self._row_stride_bytes,
                )

            if not ptr or not buf_size:
                if self._cb_count <= 3:
                    log.warning("CB #%d: null ptr or size — skipping", self._cb_count)
                return

            # Zero-copy view of the SDK buffer
            row_bytes = self._width * self._bytes_per_pixel
            stride = getattr(self, "_row_stride_bytes", row_bytes)
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

            if self._raw_processor is not None:
                # Fine mode: let the processor work on the full-res view
                frame = self._raw_processor(view)
            else:
                # Coarse mode: 2× subsample, 14→8 bit
                if view.dtype == np.uint16:
                    frame = (view[::2, ::2] >> 6).astype(np.uint8)
                else:
                    frame = view[::2, ::2].copy()

            if self._cb_count <= 3:
                log.info(
                    "CB #%d: frame shape=%s dtype=%s",
                    self._cb_count, frame.shape, frame.dtype,
                )

            if self._on_frame:
                self._on_frame(frame)
        except Exception:
            log.exception("Error in buffer callback #%d", self._cb_count)
