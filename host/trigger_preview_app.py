"""
Live low-resolution full-frame preview on each trigger (default: external Line1).

Connects via KAYA GenTL (.cti) to the device with the given serial number,
sets TriggerMode=On and either Line1+RisingEdge or Software (see --software-trigger),
and updates the window when a buffer arrives.

Usage:
  py -3 trigger_preview_app.py --cti "C:\\Program Files\\KAYA Instruments\\Common\\bin\\KYVPGenTL_vc141.cti"
  py -3 trigger_preview_app.py --list-devices --cti "...\\KYVPGenTL_vc141.cti"

If the camera is already configured in Vision Point (trigger, acquisition mode, etc.):

  py -3 trigger_preview_app.py --cti "..." --vision-point

Software triggers (app pulses GenICam software-trigger commands each frame):

  py -3 trigger_preview_app.py --cti "..." --software-trigger

On some KAYA + Harvesters setups these commands are not executable from Python (AccessException
on TriggerSoftware / ExecuteSoftwareTrigger — same as AcquisitionStart). If pulse failures
climb and fetch stays 0, software trigger from this app is blocked; use VP for acquisition
or ask KAYA for a supported API. Use --debug-harvesters to log each command attempt.

With --vision-point + --software-trigger, set Software in VP first; this app only pulses.

Komodo III + merged GenICam: the app still selects the camera branch (CameraSelector /
link) to match --serial and sets TriggerSelector=FrameStart so acquisition commands
apply to the right device; with --vision-point it does not rewrite AcquisitionMode /
Line1 / Software unless you omit --vision-point.

After Stop / closing the window, wait a few seconds before opening Vision Point. If VP
claims the camera is still busy (even with no other VP open), wait 10–20 s or restart
Vision Point once — the grabber stack sometimes lags releasing the session.

Only one GUI instance should run at a time: two copies fight over the same GenTL device.
The app blocks a second window unless you pass --allow-second-instance (debug only).

If Vision Point acquires on Line1 but this app never gets frames, the hardware path is fine;
the gap is KYVPGenTL + Harvesters vs VP's own stack. Use --debug-harvesters and/or set
environment variable HARVESTERS_LOG_BUFFER=1 for stderr detail, then contact KAYA support
with that log. Optional: try KYFGLibGenTL .cti instead of KYVPGenTL if installed.

Requires: pip install -r requirements.txt
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
import time
from typing import Any

from PySide6.QtCore import QDir, QLockFile, Qt, QThread, Signal
from PySide6.QtGui import QCloseEvent, QImage, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


def find_cti_from_env() -> str | None:
    base = os.environ.get("GENICAM_GENTL64_PATH", "").strip()
    if not base or not os.path.isdir(base):
        return None
    matches = sorted(glob.glob(os.path.join(base, "*.cti")))
    return matches[0] if matches else None


def raw_bayer_rg14_to_preview_bgr(raw_u16: Any, max_long_edge: int) -> Any:
    """Downscale Bayer RG (14-bit in uint16), then debayer to BGR8 for display."""
    import cv2
    import numpy as np

    if raw_u16.ndim != 2:
        raise ValueError(f"Expected 2D raw image, got {raw_u16.shape}")

    h, w = raw_u16.shape
    long_edge = max(h, w)
    scale = max_long_edge / long_edge
    if scale >= 1.0:
        small = raw_u16
    else:
        new_w = max(2, int(w * scale) & ~1)
        new_h = max(2, int(h * scale) & ~1)
        small = cv2.resize(raw_u16, (new_w, new_h), interpolation=cv2.INTER_AREA)

    u8 = np.clip(np.right_shift(small, 6), 0, 255).astype(np.uint8)
    return cv2.cvtColor(u8, cv2.COLOR_BAYER_RG2BGR)


def try_set_trigger_line1(nm: Any) -> list[str]:
    lines: list[str] = []
    try:
        nm.TriggerMode.value = "On"
        lines.append("TriggerMode = On")
    except Exception as e:
        lines.append(f"TriggerMode: skipped ({e})")
    try:
        nm.TriggerSource.value = "Line1"
        lines.append("TriggerSource = Line1")
    except Exception as e:
        lines.append(f"TriggerSource: skipped ({e})")
    try:
        nm.TriggerActivation.value = "RisingEdge"
        lines.append("TriggerActivation = RisingEdge")
    except Exception as e:
        lines.append(f"TriggerActivation: skipped ({e})")
    return lines


class AcquisitionThread(QThread):
    frame_ready = Signal(QImage, int, float)
    log_line = Signal(str)
    failed = Signal(str)
    fetch_timeout_count = Signal(int)
    software_pulse_fail_count = Signal(int)

    def __init__(
        self,
        cti_path: str,
        serial: str,
        max_long_edge: int,
        fetch_timeout_s: float,
        set_trigger: bool,
        prepare_camera: bool = True,
        device_index: int | None = None,
        software_trigger_once: bool = False,
        data_stream_index: int | None = None,
        debug_harvesters: bool = False,
        software_trigger: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._cti_path = cti_path
        self._serial = serial
        self._max_long_edge = max_long_edge
        self._fetch_timeout_s = fetch_timeout_s
        self._set_trigger = set_trigger
        self._software_trigger = software_trigger
        self._prepare_camera = prepare_camera
        self._device_index = device_index
        self._software_trigger_once = software_trigger_once
        self._data_stream_index = data_stream_index
        self._debug_harvesters = debug_harvesters
        self._running = True

    def stop_gracefully(self) -> None:
        self._running = False

    def run(self) -> None:
        import cv2
        import numpy as np
        from genicam.gentl import TimeoutException
        from harvesters.core import Callback as HarvestersCallback
        from harvesters.core import Harvester

        from gentl_acq_helpers import (
            align_genicam_branch_by_device_index,
            align_genicam_branch_by_serial,
            create_image_acquirer,
            ensure_trigger_selector_frame_start,
            fetch_first_buffer_any_stream,
            format_create_failure,
            gentle_genicam_before_harvesters_stop,
            prepare_camera_for_triggered_frames,
            pulse_software_trigger,
            snapshot_acquisition_and_trigger,
            trigger_chain_readback,
            try_acquisition_start_execute,
            try_pulse_software_trigger,
            try_set_software_trigger,
        )
        from kaya_harvesters_patch import apply_kaya_gentl_patch

        apply_kaya_gentl_patch()

        h: Harvester | None = None
        ia = None
        try:
            self.log_line.emit("GenTL: loading producer and updating device list…")
            h = Harvester()
            h.add_file(self._cti_path)
            h.update()

            if not h.device_info_list:
                self.failed.emit("No GenTL devices found (cables / power / driver?).")
                return

            if self._device_index is not None:
                search_key: int | dict[str, str] = self._device_index
                sk_repr = f"index {self._device_index}"
            else:
                search_key = {"serial_number": self._serial}
                sk_repr = f"serial {self._serial}"

            try:
                ia, priv = create_image_acquirer(h, search_key)
                self.log_line.emit(f"Opened camera ({sk_repr}, access={priv}).")
            except Exception as e:
                lines = "\n".join(str(info) for info in h.device_info_list)
                self.failed.emit(
                    format_create_failure(search_key, e) + f"\n\nDevices seen:\n{lines}"
                )
                return

            nm = ia.remote_device.node_map
            try:
                model = nm.DeviceModelName.value
            except Exception:
                model = "?"
            self.log_line.emit(f"Model: {model}")

            if self._device_index is not None:
                for line in align_genicam_branch_by_device_index(nm, self._device_index):
                    self.log_line.emit(line)
            else:
                for line in align_genicam_branch_by_serial(nm, self._serial):
                    self.log_line.emit(line)

            for line in ensure_trigger_selector_frame_start(nm):
                self.log_line.emit(line)

            if self._prepare_camera:
                for line in prepare_camera_for_triggered_frames(nm):
                    self.log_line.emit(line)
            else:
                self.log_line.emit(
                    "Skipping AcquisitionMode / TriggerSelector writes "
                    "(use when Vision Point already configured the camera)."
                )

            if self._set_trigger:
                if self._software_trigger:
                    for line in try_set_software_trigger(nm):
                        self.log_line.emit(line)
                    self.log_line.emit("Trigger readback (after software-trigger setup):")
                    for line in trigger_chain_readback(nm):
                        self.log_line.emit(f"  {line}")
                else:
                    for line in try_set_trigger_line1(nm):
                        self.log_line.emit(line)
            elif self._software_trigger:
                self.log_line.emit(
                    "Software-trigger mode but GenICam trigger writes skipped "
                    "(--vision-point / --no-set-trigger): ensure TriggerSource=Software "
                    "in Vision Point; app will only pulse TriggerSoftware."
                )

            if self._software_trigger:
                self.log_line.emit(
                    "Starting Harvesters stream (ia.start) — pulsing software trigger each frame."
                )
            else:
                self.log_line.emit(
                    "Starting Harvesters stream (ia.start) — fire Line1 when ready."
                )
            try:
                ia.start()
            except Exception as se:
                self.failed.emit(
                    "Harvesters could not start acquisition (inside ia.start()):\n\n"
                    f"{se}\n\n"
                    "This often comes from GenICam nodes (e.g. PayloadSize / AcquisitionStart) "
                    "not matching the camera state. If you already set up the camera in "
                    "Vision Point, run this app with:\n"
                    "  --vision-point\n"
                    "which skips writing AcquisitionMode, TriggerSelector, and trigger lines.\n"
                    "Or use: --no-set-trigger --no-prepare-camera"
                )
                return

            if self._software_trigger:
                self.log_line.emit("Trigger readback (after ia.start):")
                for line in trigger_chain_readback(nm):
                    self.log_line.emit(f"  {line}")

            if self._debug_harvesters:

                class _HarvestersEventCb(HarvestersCallback):
                    def __init__(self, sig: Signal, name: str) -> None:
                        super().__init__()
                        self._sig = sig
                        self._name = name
                        self._n = 0

                    def emit(self, context: object | None = None) -> None:
                        self._n += 1
                        if self._n <= 25 or self._n % 100 == 0:
                            self._sig.emit(
                                f"[debug] Harvesters {self._name} callback (#{self._n})"
                            )

                ia.add_callback(
                    ia.Events.INCOMPLETE_BUFFER,
                    _HarvestersEventCb(self.log_line, "INCOMPLETE_BUFFER"),
                )
                ia.add_callback(
                    ia.Events.NEW_BUFFER_AVAILABLE,
                    _HarvestersEventCb(self.log_line, "NEW_BUFFER_AVAILABLE"),
                )
                self.log_line.emit(
                    "[debug] Registered INCOMPLETE_BUFFER + NEW_BUFFER_AVAILABLE callbacks."
                )

            snap_lines = snapshot_acquisition_and_trigger(nm)
            self.log_line.emit(
                "GenICam snapshot (after ia.start):\n"
                + "\n".join(f"  {s}" for s in snap_lines)
            )
            ok_as, as_msg = try_acquisition_start_execute(
                nm, "explicit retry after ia.start()"
            )
            self.log_line.emit(as_msg)
            if not ok_as:
                if self._software_trigger:
                    self.log_line.emit(
                        "If software triggers never produce frames, check TriggerSource=Software "
                        "and that TriggerSoftware / ExecuteSoftwareTrigger exists in the nodemap."
                    )
                else:
                    self.log_line.emit(
                        "If Line1 triggers never produce frames, the sensor may not be "
                        "armed: fix AcquisitionStart / acquisition mode in Vision Point "
                        "or try without --vision-point so this app can set Continuous + "
                        "FrameStart trigger."
                    )

            try:
                n_streams = len(ia._new_buffer_event_monitor_dict)
                ds_ids = list(ia._device_proxy.data_stream_ids)
            except Exception as e:
                n_streams = -1
                ds_ids = [repr(e)]
            fetch_mode = (
                f"only stream index {self._data_stream_index}"
                if self._data_stream_index is not None
                else "round-robin across all streams"
            )
            self.log_line.emit(
                f"GenTL: {n_streams} data stream(s) with NEW_BUFFER, "
                f"data_stream_ids={ds_ids!r} — fetch: {fetch_mode}. "
                f"(VPStrm0/VPStrm1 is normal for KAYA VP; if frames stay 0, neither "
                f"stream is getting image buffers yet — usually trigger/acquisition, "
                f"not the stream count. Try --data-stream-index 1 or 0 to isolate.)"
            )

            if self._software_trigger_once:
                self.log_line.emit("Software trigger test (needs TriggerSource=Software):")
                for line in try_pulse_software_trigger(nm):
                    self.log_line.emit(f"  {line}")

            frame_count = 0
            fetch_timeouts = 0
            pulse_fails = 0
            last_t = time.perf_counter()

            while self._running:
                buffer = None
                try:
                    try:
                        if self._software_trigger:
                            if not pulse_software_trigger(nm):
                                pulse_fails += 1
                                self.software_pulse_fail_count.emit(pulse_fails)
                                if pulse_fails == 1:
                                    self.log_line.emit(
                                        "Software trigger: GenICam pulse commands are not "
                                        "executable here (AccessException / reference not valid — "
                                        "same issue as AcquisitionStart on KAYA merged trees). "
                                        "VP can still trigger; this Python+Harvester path cannot. "
                                        "Retrying every 1 s. Add --debug-harvesters for per-command "
                                        "errors; otherwise use VP for live frames or contact KAYA."
                                    )
                                    if self._debug_harvesters:
                                        for line in try_pulse_software_trigger(nm):
                                            self.log_line.emit(f"  {line}")
                                time.sleep(1.0)
                                continue
                            time.sleep(0.02)
                        buffer = fetch_first_buffer_any_stream(
                            ia,
                            self._fetch_timeout_s,
                            data_stream_index=self._data_stream_index,
                        )
                    except TimeoutException:
                        fetch_timeouts += 1
                        self.fetch_timeout_count.emit(fetch_timeouts)
                        continue

                    if not self._running:
                        break

                    comp = buffer.payload.components[0]
                    raw = np.asarray(
                        comp.data.reshape(comp.height, comp.width), dtype=np.uint16
                    ).copy()
                    bgr = raw_bayer_rg14_to_preview_bgr(raw, self._max_long_edge)
                    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                    rgb = np.ascontiguousarray(rgb)
                    hh, ww, ch = rgb.shape
                    qimg = QImage(
                        rgb.data,
                        ww,
                        hh,
                        ch * ww,
                        QImage.Format.Format_RGB888,
                    ).copy()
                except Exception as e:
                    self.failed.emit(f"Process frame failed: {e}")
                    break
                finally:
                    if buffer is not None:
                        try:
                            buffer.queue()
                        except Exception:
                            pass

                frame_count += 1
                now = time.perf_counter()
                period = now - last_t
                last_t = now
                self.frame_ready.emit(qimg, frame_count, period)

        except Exception as e:
            self.failed.emit(f"Acquisition thread error: {e}")
        finally:
            if ia is not None:
                try:
                    gentle_genicam_before_harvesters_stop(ia.remote_device.node_map)
                except Exception:
                    pass
                try:
                    ia.stop()
                except Exception:
                    pass
                try:
                    ia.destroy()
                except Exception:
                    pass
            if h is not None:
                try:
                    h.reset()
                except Exception:
                    pass
            import gc

            gc.collect()
            # KAYA VP GenTL often needs time after reset before another app can open
            # the camera; without this, Vision Point may report a phantom "busy" session.
            time.sleep(2.0)
            try:
                self.log_line.emit(
                    "GenTL session released (wait ~10–20 s before Vision Point if it "
                    "still says the camera is busy; restarting VP once usually clears it)."
                )
            except Exception:
                pass


class TriggerPreviewWindow(QMainWindow):
    def __init__(
        self,
        cti_path: str,
        serial: str,
        max_long_edge: int,
        fetch_timeout_s: float,
        set_trigger: bool,
        prepare_camera: bool = True,
        device_index: int | None = None,
        software_trigger_once: bool = False,
        data_stream_index: int | None = None,
        debug_harvesters: bool = False,
        software_trigger: bool = False,
    ) -> None:
        super().__init__()
        self.setWindowTitle(
            "Trigger preview — software trigger → low-res full frame"
            if software_trigger
            else "Trigger preview — Line1 → low-res full frame"
        )
        self._cti_path = cti_path
        self._serial = serial
        self._max_long_edge = max_long_edge
        self._fetch_timeout_s = fetch_timeout_s
        self._set_trigger = set_trigger
        self._prepare_camera = prepare_camera
        self._device_index = device_index
        self._software_trigger_once = software_trigger_once
        self._data_stream_index = data_stream_index
        self._debug_harvesters = debug_harvesters
        self._software_trigger = software_trigger

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setMinimumSize(800, 600)
        self._image_label.setStyleSheet("background-color: #222;")

        self._status = QLabel(
            "Idle. Press Start, then fire Line1 triggers."
            if not software_trigger
            else "Idle. Press Start — app will pulse software trigger each frame."
        )
        self._status.setWordWrap(True)
        self._frame_counter = QLabel("Frames acquired: 0")
        self._frame_counter.setStyleSheet(
            "font-weight: bold; font-size: 13pt; color: #e0e0e0;"
        )
        self._fetch_waits = QLabel("Fetch timeouts (no buffer yet): 0")
        self._fetch_waits.setStyleSheet("color: #aaa;")
        self._fps = QLabel("")

        start_btn = QPushButton("Start")
        start_btn.clicked.connect(self._on_start)
        stop_btn = QPushButton("Stop")
        stop_btn.clicked.connect(self._on_stop)

        row = QHBoxLayout()
        row.addWidget(start_btn)
        row.addWidget(stop_btn)
        row.addStretch(1)
        row.addWidget(self._fps)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(self._image_label, stretch=1)
        layout.addWidget(self._status)
        layout.addWidget(self._frame_counter)
        layout.addWidget(self._fetch_waits)
        layout.addLayout(row)
        self.setCentralWidget(central)

        self._thread: AcquisitionThread | None = None
        self._last_qimage: QImage | None = None
        self._wait_fetch_timeouts = 0
        self._wait_pulse_fails = 0

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._refresh_scaled_pixmap()

    def _refresh_scaled_pixmap(self) -> None:
        if self._last_qimage is None or self._last_qimage.isNull():
            return
        pix = QPixmap.fromImage(self._last_qimage)
        scaled = pix.scaled(
            self._image_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image_label.setPixmap(scaled)

    def _on_start(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        self._thread = AcquisitionThread(
            self._cti_path,
            self._serial,
            self._max_long_edge,
            self._fetch_timeout_s,
            self._set_trigger,
            prepare_camera=self._prepare_camera,
            device_index=self._device_index,
            software_trigger_once=self._software_trigger_once,
            data_stream_index=self._data_stream_index,
            debug_harvesters=self._debug_harvesters,
            software_trigger=self._software_trigger,
            parent=self,
        )
        self._thread.frame_ready.connect(self._on_frame)
        self._thread.log_line.connect(self._on_log)
        self._thread.failed.connect(self._on_failed)
        self._thread.fetch_timeout_count.connect(self._on_fetch_timeout)
        self._thread.software_pulse_fail_count.connect(self._on_software_pulse_fail)
        self._wait_fetch_timeouts = 0
        self._wait_pulse_fails = 0
        self._thread.start()
        self._status.setText("Connecting to camera (GenTL)…")
        self._frame_counter.setText("Frames acquired: 0")
        self._refresh_wait_stats_label()
        self._fps.setText("")

    def _on_stop(self) -> None:
        if self._thread is None:
            return
        self._thread.stop_gracefully()
        self._thread.wait(15_000)
        self._thread = None
        self._status.setText("Stopped.")
        self._frame_counter.setText("Frames acquired: 0")
        self._wait_fetch_timeouts = 0
        self._wait_pulse_fails = 0
        self._refresh_wait_stats_label()
        self._fps.setText("")

    def _refresh_wait_stats_label(self) -> None:
        if self._software_trigger:
            self._fetch_waits.setText(
                f"Software pulse failures: {self._wait_pulse_fails} "
                f"(1 s backoff — not fetch timeouts)  |  "
                f"Fetch timeouts: {self._wait_fetch_timeouts} "
                f"(~{self._wait_fetch_timeouts * self._fetch_timeout_s:.0f}s)"
            )
        else:
            self._fetch_waits.setText(
                f"Fetch timeouts (no buffer yet): {self._wait_fetch_timeouts} "
                f"(~{self._wait_fetch_timeouts * self._fetch_timeout_s:.0f}s waiting)"
            )

    def _on_fetch_timeout(self, n: int) -> None:
        self._wait_fetch_timeouts = n
        self._refresh_wait_stats_label()

    def _on_software_pulse_fail(self, n: int) -> None:
        self._wait_pulse_fails = n
        self._refresh_wait_stats_label()

    def _on_frame(self, qimg: QImage, count: int, period_s: float) -> None:
        self._last_qimage = qimg
        self._refresh_scaled_pixmap()
        self._frame_counter.setText(f"Frames acquired: {count}")
        hz = 1.0 / period_s if period_s > 1e-6 else 0.0
        self._fps.setText(f"{hz:.2f} Hz (instant, last interval)")

    def _on_log(self, line: str) -> None:
        self._status.setText(line)

    def _on_failed(self, msg: str) -> None:
        self._on_stop()
        QMessageBox.critical(self, "Acquisition error", msg)
        self._status.setText("Error — see dialog.")

    def closeEvent(self, event: QCloseEvent) -> None:
        self._on_stop()
        super().closeEvent(event)


def list_devices(cti_path: str) -> int:
    from kaya_harvesters_patch import apply_kaya_gentl_patch

    apply_kaya_gentl_patch()

    try:
        from harvesters.core import Harvester
    except ImportError:
        print("pip install -r requirements.txt", file=sys.stderr)
        return 1

    h = Harvester()
    h.add_file(cti_path)
    h.update()
    infos = list(h.device_info_list)
    for i, info in enumerate(infos):
        print(f"[{i}] {info}")
    h.reset()
    return 0 if infos else 2


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Low-res full-frame preview on Line1 trigger.")
    p.add_argument("--cti", default=None, help="Path to KYVPGenTL_*.cti")
    p.add_argument("--serial", default="2439007", help="Camera serial_number (GenTL)")
    p.add_argument(
        "--device-index",
        type=int,
        default=None,
        metavar="N",
        help="Open device by index N instead of --serial (from --list-devices)",
    )
    p.add_argument("--max-edge", type=int, default=1920, help="Preview long edge (pixels)")
    p.add_argument("--fetch-timeout", type=float, default=5.0, help="Per-frame fetch timeout (s)")
    p.add_argument(
        "--no-set-trigger",
        action="store_true",
        help="Do not write TriggerMode/Source/Activation",
    )
    p.add_argument(
        "--no-prepare-camera",
        action="store_true",
        help="Do not write AcquisitionMode / TriggerSelector (avoid clashing with Vision Point)",
    )
    p.add_argument(
        "--vision-point",
        action="store_true",
        help="Same as --no-set-trigger --no-prepare-camera (camera already tuned in Vision Point)",
    )
    p.add_argument(
        "--list-devices",
        action="store_true",
        help="Print GenTL device list and exit",
    )
    p.add_argument(
        "--software-trigger",
        action="store_true",
        help="Use TriggerSource=Software (when not using --vision-point) and pulse software "
        "trigger before each fetch — no Line1. With --vision-point, set Software in VP first; "
        "this app only pulses each frame.",
    )
    p.add_argument(
        "--software-trigger-once",
        action="store_true",
        help="After start, pulse TriggerSoftware once (only works if TriggerSource is Software; "
        "use to test whether any buffers arrive without Line1)",
    )
    p.add_argument(
        "--data-stream-index",
        type=int,
        default=None,
        metavar="N",
        help="Listen only to GenTL data stream index N (0=first id, e.g. VPStrm0; 1=VPStrm1). "
        "Omit to round-robin all streams. Use to see which stream carries the image.",
    )
    p.add_argument(
        "--allow-second-instance",
        action="store_true",
        help="Do not enforce single-GUI lock (debug only; two instances can break GenTL access).",
    )
    p.add_argument(
        "--debug-harvesters",
        action="store_true",
        help="Log Harvesters INCOMPLETE_BUFFER / NEW_BUFFER_AVAILABLE callbacks to the status line "
        "(see if data arrives but is rejected as incomplete). Also run with HARVESTERS_LOG_BUFFER=1 in "
        "the environment for verbose stderr from Harvesters.",
    )
    return p


def main() -> int:
    args = build_arg_parser().parse_args()

    cti = args.cti or find_cti_from_env()
    if not cti or not os.path.isfile(cti):
        print(
            "Provide --cti or set GENICAM_GENTL64_PATH.\n"
            r'Example: py -3 trigger_preview_app.py --cti "C:\Program Files\KAYA Instruments\Common\bin\KYVPGenTL_vc141.cti"',
            file=sys.stderr,
        )
        return 1

    from kaya_harvesters_patch import apply_kaya_gentl_patch

    apply_kaya_gentl_patch()

    if args.list_devices:
        return list_devices(cti)

    vp = args.vision_point
    app = QApplication(sys.argv)

    if not args.allow_second_instance:
        lock_path = QDir.temp().absoluteFilePath("TileBasedFocus_trigger_preview.lock")
        instance_lock = QLockFile(lock_path)
        instance_lock.setStaleLockTime(60_000)
        if not instance_lock.tryLock(200):
            QMessageBox.warning(
                None,
                "Trigger preview already running",
                "Another Trigger preview window is already open, or the last run ended "
                "abruptly and the lock has not expired yet.\n\n"
                "Only one instance should use the camera at a time. Close the other "
                "window, or wait up to about a minute, then try again.\n\n"
                "If you truly need two GUIs (not recommended), start with "
                "--allow-second-instance.",
            )
            return 1
        app.setProperty("_tilebasedfocus_instance_lock", instance_lock)

    win = TriggerPreviewWindow(
        cti_path=cti,
        serial=args.serial,
        max_long_edge=args.max_edge,
        fetch_timeout_s=args.fetch_timeout,
        set_trigger=not (args.no_set_trigger or vp),
        prepare_camera=not (args.no_prepare_camera or vp),
        device_index=args.device_index,
        software_trigger_once=args.software_trigger_once,
        data_stream_index=args.data_stream_index,
        debug_harvesters=args.debug_harvesters,
        software_trigger=args.software_trigger,
    )
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
