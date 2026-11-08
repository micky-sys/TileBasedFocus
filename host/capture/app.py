"""Unified PySide6 main window for the 3-camera Iron 661 rig.

Combines:

* live preview (any one camera at a time, rate-capped),
* M0 / M1 sweep capture to per-camera U.2 drives,
* M4 focus mode (:mod:`capture.focus_panel` + the w10 autofocus in
  :mod:`capture.autofocus_w10`),
* ClearCore PCB serial control (mode, camera select, focus strength,
  focus step, trigger period).

GNSS is wired through to the writer via a ``gps_provider`` callback that is
currently always ``None`` -- the receiver and correlator will be added once
the GPS antenna is on the rig.

Run as::

    py -3 host/main_capture.py            # auto-find CTI + ClearCore
    py -3 host/main_capture.py --cti ...  # explicit CTI
    py -3 host/main_capture.py --port COMx
"""

from __future__ import annotations

import argparse
import datetime as _dt
import logging
import os
import shutil
import sys
import time
from typing import Optional

from PySide6.QtCore import QSize, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QButtonGroup,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .autofocus_w10 import AutoFocusController, ROI_NAMES
from .psf_w10 import frame_ok, roi_boxes
from .camera_manager import CameraManager, OpenedCamera, find_cti_from_env
from .drive_picker import DriveInfo, list_drives
from .file_naming import sanitize_project_name
from .focus_panel import FocusPanel, _crop_pixmap_center
from .gps_correlator import GpsCorrelator
from .gps_receiver import GpsReceiver
from .fine_tracker import FineFocusTracker
from .kyfg_backend import DEFAULT_CAMERA_CONFIG, KyfgCamera
from .mode_controller import (
    Mode,
    ModeController,
    MODE_BUTTON_LABELS,
    MODE_BUTTON_WIDTH_COMPACT,
    MODE_BUTTON_WIDTH_FULL,
    mode_display_label,
)
from .preflight import PreflightHooks, PreflightRunner, format_report, gps_stats_by_camera
from .pipeline import (
    AcquisitionThread,
    CameraPipeline,
    CaptureContext,
    PREVIEW_DISPLAY_GAIN_MAX,
    PREVIEW_DISPLAY_GAIN_MIN,
    PREVIEW_MAX_EDGE,
)
from .serial_client import SerialClient
from .session_log import SessionLogger, build_date
from .w10_live import W10LiveScorer, format_score

log = logging.getLogger(__name__)

PREVIEW_PLACEHOLDER_LABEL = "Pick a camera's preview radio to display its feed"

# Calibrated on-rig (HANDOFF 23.6): str0 moves ~24 encoder counts per 3 ms pulse. The
# SIGN is only a starting guess -- the w10 engine probes the real polarity (and
# magnitude) at the start of every run, since each lens motor may be wired either way.
AF_COUNTS_PER_PULSE = -24.0

_COM_PANEL_STYLE_CONNECTED = (
    "QGroupBox { border: 1px solid #4a4a4a; border-radius: 4px; margin-top: 8px; "
    "padding-top: 2px; }"
    "QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; "
    "color: #ccc; }"
)
_COM_PANEL_STYLE_DISCONNECTED = (
    "QGroupBox { border: 2px solid #c44; border-radius: 4px; margin-top: 8px; "
    "padding-top: 2px; background-color: rgba(180, 40, 40, 0.18); }"
    "QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; "
    "color: #f88; font-weight: bold; }"
)


def _com_panel_style(connected: bool) -> str:
    return _COM_PANEL_STYLE_CONNECTED if connected else _COM_PANEL_STYLE_DISCONNECTED


# GPS link probe: how many ``$E*`` pulses to fire before declaring the bridge
# dead, and how long to wait for each one's sentence. Measured round-trip on
# the bench is ~40 ms (pulse out, GGA back), so 800 ms is generous; the retries
# cover a receiver that is still acquiring when the app starts.
_GPS_PROBE_ATTEMPTS = 3
_GPS_PROBE_INTERVAL_MS = 800


class _PreviewDisplayLabel(QLabel):
    """Live pixmap preview without pixmap-driven layout minimums.

    Default ``QLabel`` uses the pixmap size for ``minimumSizeHint()``; with a
    horizontally stretched preview that scales pixmaps to the label size, that
    creates positive feedback (each frame widens the layout). This subclass
    keeps hints stable so stretch allocates geometry and the pixmap is clipped
    inside it.

    Also hosts the GPS status overlay: a child :class:`QLabel` parented
    to this label and positioned in the top-right corner via
    :meth:`resizeEvent`. The overlay is created lazily by
    :meth:`set_gps_overlay`; until then ``resizeEvent`` is a no-op.
    """

    OVERLAY_MARGIN = 6  # px from the top-right corner

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._gps_overlay: Optional[QLabel] = None
        self._w10_overlay: Optional[QLabel] = None
        self._corner_button: Optional[QWidget] = None

    def minimumSizeHint(self):  # noqa: N802 -- Qt naming
        return QSize(0, 0)

    def sizeHint(self):  # noqa: N802 -- Qt naming
        return QSize(280, 200)

    def set_gps_overlay(self, overlay: QLabel) -> None:
        """Attach a child label that should always sit in the top-right.

        The caller is responsible for setting the overlay's text +
        stylesheet; this method only handles parenting and geometry.
        """
        self._gps_overlay = overlay
        overlay.setParent(self)
        overlay.show()
        self._reposition_gps_overlay()

    def set_corner_button(self, button: QWidget) -> None:
        """Attach a widget pinned to the top-left corner (e.g. zoom toggle)."""
        self._corner_button = button
        button.setParent(self)
        button.show()
        self._reposition_corner_button()

    def set_w10_overlay(self, overlay: QLabel) -> None:
        """Attach a child label pinned to the bottom-right (the live w10 readout, sec.54.12)."""
        self._w10_overlay = overlay
        overlay.setParent(self)
        overlay.show()
        self._reposition_w10_overlay()

    def resizeEvent(self, event):  # noqa: N802 -- Qt naming
        super().resizeEvent(event)
        self._reposition_gps_overlay()
        self._reposition_w10_overlay()
        self._reposition_corner_button()

    def _reposition_w10_overlay(self) -> None:
        ov = self._w10_overlay
        if ov is None:
            return
        ov.adjustSize()
        ov.move(max(0, self.width() - ov.width() - self.OVERLAY_MARGIN),
                max(0, self.height() - ov.height() - self.OVERLAY_MARGIN))
        ov.raise_()

    def _reposition_gps_overlay(self) -> None:
        ov = self._gps_overlay
        if ov is None:
            return
        # Use the overlay's preferred size; the label resizes itself
        # via setText().
        ov.adjustSize()
        x = max(0, self.width() - ov.width() - self.OVERLAY_MARGIN)
        y = self.OVERLAY_MARGIN
        ov.move(x, y)
        ov.raise_()

    def _reposition_corner_button(self) -> None:
        btn = self._corner_button
        if btn is None:
            return
        btn.move(self.OVERLAY_MARGIN, self.OVERLAY_MARGIN)
        btn.raise_()


# ---------------------------------------------------------------------------


class CameraRow(QWidget):
    """One row of per-camera UI: serial, drive picker, counters, preview radio."""

    drive_changed = Signal(int, object)  # camera_number, DriveInfo | None
    preview_selected = Signal(int)  # camera_number

    def __init__(self, camera_number: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._camera_number = camera_number
        self._drive: Optional[DriveInfo] = None

        # Latest numeric counters mirrored from the per-camera labels so
        # the session-log can report acquired/written/dropped totals at
        # line-close time without re-parsing the label text.
        self._acq_count = 0
        self._written_count = 0
        self._dropped_count = 0

        self._serial_label = QLabel("(not opened)")
        self._serial_label.setMinimumWidth(96)
        self._serial_label.setMaximumWidth(200)
        self._serial_label.setStyleSheet("font-family: Consolas, monospace;")

        self._drive_combo = QComboBox()
        self._drive_combo.setMinimumWidth(140)
        self._drive_combo.setMaximumWidth(280)
        self._drive_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self._drive_combo.currentIndexChanged.connect(self._on_drive_combo_changed)

        # How full the selected drive is (% used). Refreshed by MainWindow's
        # 2 s tick so the operator sees it filling during a recording.
        self._fill_bar = QProgressBar()
        self._fill_bar.setRange(0, 100)
        self._fill_bar.setFixedWidth(64)
        self._fill_bar.setFormat("%p%")
        self._fill_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._set_fill_colour("#1aaa1a")
        self.refresh_fill()

        self._frames_acq_label = QLabel("acq:0  hz:0.0")
        self._frames_acq_label.setFixedWidth(150)
        self._frames_written_label = QLabel("written:0")
        self._frames_written_label.setFixedWidth(104)
        self._frames_dropped_label = QLabel("dropped:0")
        self._frames_dropped_label.setFixedWidth(100)
        self._sweep_idx_label = QLabel("S- F-")
        self._sweep_idx_label.setFixedWidth(76)

        for lbl in (
            self._frames_acq_label, self._frames_written_label,
            self._frames_dropped_label, self._sweep_idx_label,
        ):
            lbl.setStyleSheet("font-family: Consolas, monospace;")

        self._preview_radio = QRadioButton("preview")
        self._preview_radio.toggled.connect(self._on_preview_toggled)

        # Cam title label is a member so we can repaint it green once
        # the pipeline reports a successful bring-up. We start in the
        # "not yet up" muted-grey state.
        self._cam_title_label = QLabel(f"<b>cam{camera_number}</b>")
        self._cam_title_label.setStyleSheet("color: #888;")
        self._cam_title_label.setMinimumWidth(48)

        self._drive_combo.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 1, 3, 1)
        layout.setSpacing(4)
        layout.addWidget(self._cam_title_label)
        layout.addWidget(self._serial_label)
        layout.addWidget(QLabel("Drive:"))
        layout.addWidget(self._drive_combo, 1)
        layout.addWidget(self._fill_bar)
        layout.addWidget(self._frames_acq_label)
        layout.addWidget(self._frames_written_label)
        layout.addWidget(self._frames_dropped_label)
        layout.addWidget(self._sweep_idx_label)
        layout.addWidget(self._preview_radio)

    # -- public API ---------------------------------------------------------

    @property
    def camera_number(self) -> int:
        return self._camera_number

    @property
    def preview_radio(self) -> QRadioButton:
        return self._preview_radio

    def set_serial(self, serial: str) -> None:
        text = serial or "(unknown)"
        self._serial_label.setText(text)
        self._serial_label.setToolTip(text)

    def set_brought_up(self, ok: bool) -> None:
        """Recolour the ``cam<N>`` title label.

        Bright green = bring-up succeeded and the pipeline is producing
        frames. Muted grey = the row is still empty (e.g. cam2/cam3 in
        stage 1, or any camera that failed to open). Used purely for
        operator feedback at a glance.
        """
        if ok:
            self._cam_title_label.setStyleSheet(
                "color: #1aaa1a; font-weight: bold;"
            )
        else:
            self._cam_title_label.setStyleSheet("color: #888;")

    def set_drives(self, drives: list[DriveInfo]) -> None:
        """Refresh the drive dropdown; preserve current selection if still present."""
        prev_root = self._drive.root if self._drive else None
        self._drive_combo.blockSignals(True)
        self._drive_combo.clear()
        self._drive_combo.addItem("(none)", userData=None)
        target_index = 0
        for i, d in enumerate(drives, start=1):
            self._drive_combo.addItem(d.human(), userData=d)
            if prev_root and d.root == prev_root:
                target_index = i
        self._drive_combo.setCurrentIndex(target_index)
        self._drive_combo.blockSignals(False)
        self._drive = self._drive_combo.itemData(target_index)
        self.refresh_fill()
        self.drive_changed.emit(self._camera_number, self._drive)

    def current_drive_root(self) -> Optional[str]:
        return self._drive.root if self._drive else None

    def refresh_fill(self) -> None:
        """Re-read the selected drive's usage into the fill bar.

        Green while there is room; red once free space drops under 10 % so a
        drive filling mid-line is visible at a glance. ``disk_usage`` here is one
        cheap Win32 call: the dropdown only ever offers local NTFS/exFAT/ReFS
        volumes (``drive_picker`` filters out the cloud mounts that stall).
        """
        root = self.current_drive_root()
        if root is None:
            self._fill_bar.setValue(0)
            self._fill_bar.setFormat("--")
            self._fill_bar.setToolTip("no drive selected")
            return
        try:
            usage = shutil.disk_usage(root)
        except OSError as exc:
            self._fill_bar.setValue(100)
            self._fill_bar.setFormat("?")
            self._fill_bar.setToolTip(f"{root}: {exc}")
            self._set_fill_colour("#c62828")
            return
        pct_used = round(100.0 * (usage.total - usage.free) / max(usage.total, 1))
        self._fill_bar.setValue(int(pct_used))
        self._fill_bar.setFormat("%p%")
        gib = 1_073_741_824
        self._fill_bar.setToolTip(
            f"{root}  {usage.free / gib:.1f} GB free of {usage.total / gib:.1f} GB"
        )
        self._set_fill_colour("#c62828" if pct_used >= 90 else "#1aaa1a")

    def _set_fill_colour(self, colour: str) -> None:
        self._fill_bar.setStyleSheet(
            "QProgressBar { border: 1px solid #888; background: #eee; "
            "font-family: Consolas, monospace; font-size: 10px; }"
            f"QProgressBar::chunk {{ background: {colour}; }}"
        )

    @Slot(int, float)
    def update_acq(self, count: int, hz: float) -> None:
        self._acq_count = int(count)
        self._frames_acq_label.setText(f"acq:{count}  hz:{hz:.1f}")

    @Slot(int, str)
    def update_written(self, count: int, _basename: str) -> None:
        self._written_count = int(count)
        self._frames_written_label.setText(f"written:{count}")

    @Slot(int)
    def update_dropped(self, count: int) -> None:
        self._dropped_count = int(count)
        self._frames_dropped_label.setText(f"dropped:{count}")

    def frame_counts(self) -> tuple[int, int, int]:
        """Latest ``(acquired, written, dropped)`` counters for this camera."""
        return (self._acq_count, self._written_count, self._dropped_count)

    def update_sweep(self, position: tuple[int, int]) -> None:
        sweep, frame = position
        self._sweep_idx_label.setText(f"S{sweep} F{frame}")

    # -- internals ----------------------------------------------------------

    def _on_drive_combo_changed(self, idx: int) -> None:
        data = self._drive_combo.itemData(idx)
        self._drive = data if isinstance(data, DriveInfo) else None
        self.refresh_fill()
        self.drive_changed.emit(self._camera_number, self._drive)

    def _on_preview_toggled(self, on: bool) -> None:
        if on:
            self.preview_selected.emit(self._camera_number)


# ---------------------------------------------------------------------------


class _BringupWorker(QThread):
    """Run the slow KAYA bring-up off the GUI thread.

    KAYA's ``ia.start()`` blocks for 10-30 seconds per camera. Doing this on
    the GUI thread either freezes the window before it even paints or
    crashes the process when ``processEvents()`` re-enters the SDK during a
    blocking call. So we run it all here and emit signals back to the
    main window for each step.

    Pipelines and ImageAcquirer handles are passed back via signals; Qt's
    queued connections marshal them across the thread boundary cleanly.
    """

    log_line = Signal(str)
    devices_listed = Signal(int)  # number of GenTL devices visible
    camera_opened = Signal(int, str, list)  # camera_number, serial, align_log
    pipeline_starting = Signal(int, str)  # camera_number, serial
    pipeline_ready = Signal(int, object)  # camera_number, CameraPipeline
    failed = Signal(str)
    completed = Signal(int)  # number of pipelines successfully started

    def __init__(
        self,
        manager: CameraManager,
        mode_controller: ModeController,
        context: CaptureContext,
        get_drive_root,
        gps_provider=None,
        heading_provider=None,
        focus_provider=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._modes = mode_controller
        self._context = context
        self._get_drive_root = get_drive_root
        # Callable[[serial, perf_ns, sweep, frame], Optional[GpsFix]].
        # Constructed in :class:`MainWindow.__init__` and passed in
        # here so the per-camera CameraPipeline can be wired with it
        # at construction. The pipeline forwards it to AcquisitionThread.
        self._gps_provider = gps_provider
        self._heading_provider = heading_provider
        self._focus_provider = focus_provider

    def run(self) -> None:
        try:
            self.log_line.emit(f"KAYA bin: {self._manager.cti_path}")
            # Stage 2: open all three cameras. CameraManager downgrades
            # gracefully if a camera fails to open -- so we ask for 3 but
            # accept whatever comes back. The GUI shows the rows that did
            # come up in green and leaves the rest grey.
            opened: list[OpenedCamera] = self._manager.open_all(expected_count=3)
        except Exception as exc:
            self.failed.emit(f"Camera open failed: {exc}")
            return
        self.devices_listed.emit(len(opened))

        for cam in opened:
            self.camera_opened.emit(
                cam.config.camera_number,
                cam.config.camera_serial,
                list(cam.setup_log),
            )

        started = 0
        for cam in opened:
            cam_no = cam.config.camera_number
            self.pipeline_starting.emit(cam_no, cam.config.camera_serial)
            try:
                pipeline = CameraPipeline(
                    backend=cam.backend,
                    config=cam.config,
                    mode_controller=self._modes,
                    context=self._context,
                    get_drive_root=self._get_drive_root(cam_no),
                    gps_provider=self._gps_provider,
                    heading_provider=self._heading_provider,
                    # Bind THIS row's camera so the focus block carries its own
                    # encoder rather than the selected camera's -- only one of
                    # the three encoders is live at a time (shared SPI bus,
                    # relay-selected), so they genuinely differ.
                    focus_provider=(
                        None if self._focus_provider is None
                        else (lambda c=cam_no: self._focus_provider(c))
                    ),
                )
                pipeline.start(log_sink=lambda msg: self.log_line.emit(msg))
                # Move the writer QObject from this worker thread to
                # the main (GUI) thread BEFORE emitting pipeline_ready.
                # moveToThread can only be called from the object's
                # current thread, which is this one right now -- once
                # the object lands in the GUI thread the main window
                # can attach a QTimer to it for telemetry polling.
                if pipeline.writer is not None:
                    pipeline.writer.moveToThread(QApplication.instance().thread())
                started += 1
                self.pipeline_ready.emit(cam_no, pipeline)
            except Exception as exc:
                self.log_line.emit(f"cam{cam_no}: pipeline start FAILED: {exc}")

        self.completed.emit(started)


class _DriveListWorker(QThread):
    """Run drive enumeration off the GUI thread (defensive: even after we
    filter out cloud-stream mounts, ``disk_usage`` on a freshly-plugged
    U.2 drive can stall briefly while Windows finishes mounting it)."""

    result_ready = Signal(list)
    failed = Signal(str)

    def run(self) -> None:
        try:
            drives = list_drives()
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.result_ready.emit(drives)


class _CameraResetWorker(QThread):
    """Drive ``CameraManager.recover_cameras`` off the GUI thread.

    Triggered from the two "Reset cameras" buttons in the project
    header. The whole grabber-rebuild dance is in
    :meth:`CameraManager.recover_cameras`; this thread just invokes
    it and pumps results back to the GUI thread via signals so the
    operator sees per-camera progress in the log instead of a frozen
    UI for the ~10-25 s the recovery takes.

    ``do_device_reset`` toggles between SOFT (close+reopen only) and
    DEEP (DeviceReset + close+reopen) recovery. See
    :meth:`CameraManager.recover_cameras` for the details.

    Caller (``MainWindow._on_reset_cameras_*_clicked``) is responsible
    for disarming pipelines BEFORE calling ``start()`` and re-arming
    them on the GUI thread when this worker emits ``completed``.
    """

    log_line = Signal(str)
    completed = Signal(int)  # number of cameras successfully recovered

    def __init__(self, manager, do_device_reset: bool, parent=None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._do_device_reset = bool(do_device_reset)

    def run(self) -> None:
        try:
            results = self._manager.recover_cameras(
                do_device_reset=self._do_device_reset,
            )
        except Exception as exc:
            self.log_line.emit(f"Reset cameras: recover_cameras raised: {exc}")
            self.completed.emit(0)
            return
        succeeded = 0
        for cam_no, ok, detail in results:
            self.log_line.emit(
                f"cam{cam_no}: recovery {'OK' if ok else 'FAILED'} ({detail})"
            )
            if ok:
                succeeded += 1
        self.completed.emit(succeeded)


class _PipelineWarmupWorker(QThread):
    """Run :meth:`FrameBufferPool.warmup_writers` for every pipeline in
    parallel, off the GUI thread.

    Goal: eliminate the ~4-5 frame drops we see in the first ~5 s of M1
    capture by paying their first-touch costs (parent-side + writer-side
    SHM page faults, full-size numba JIT cache load) up front, before the
    operator can click any mode button. See ``FrameBufferPool.warmup_writers``
    for the per-pool mechanics.

    Three cameras run their warmups concurrently in a small Python
    ``ThreadPoolExecutor`` here: each pool is independent (separate SHM
    block, separate work_q / free_q, separate writer subprocess set), so
    overlapping them collapses the wall-clock from ~3 x per-pool time
    down to ~1 x per-pool time. The throttle inside ``warmup_writers``
    still applies per-pool, so memory commit pressure on the OS stays
    bounded even with all three running at once.
    """

    log_line = Signal(str)
    completed = Signal(int, float)  # (cameras_warmed, total_wall_s)

    def __init__(self, pipelines, parent=None) -> None:
        super().__init__(parent)
        # Snapshot the pipeline list so a late "pipeline_ready" can't
        # mutate it under us mid-run. The pipelines themselves are
        # owned by the GUI thread, but we only touch their public
        # ``pool`` attribute (the FrameBufferPool's queues + counters
        # are already process-shared and thread-safe).
        self._pipelines = list(pipelines)

    def run(self) -> None:
        import concurrent.futures as _cf
        import time as _time
        if not self._pipelines:
            self.completed.emit(0, 0.0)
            return

        targets = [
            (p.camera_number, p.pool)
            for p in self._pipelines
            if getattr(p, "pool", None) is not None
        ]
        if not targets:
            self.log_line.emit(
                "warmup: no pipelines have a writer pool; skipping"
            )
            self.completed.emit(0, 0.0)
            return

        self.log_line.emit(
            f"warmup: pre-faulting SHM slots + warming pack JIT for "
            f"{len(targets)} camera(s)..."
        )
        t0 = _time.perf_counter()

        def _warm_one(cam_no: int, pool) -> tuple[int, dict]:
            try:
                stats = pool.warmup_writers()
                return cam_no, stats
            except Exception as exc:
                return cam_no, {"error": repr(exc)}

        warmed = 0
        with _cf.ThreadPoolExecutor(max_workers=len(targets)) as ex:
            futs = [ex.submit(_warm_one, cn, pl) for cn, pl in targets]
            for fut in _cf.as_completed(futs):
                cam_no, stats = fut.result()
                if "error" in stats:
                    self.log_line.emit(
                        f"warmup: cam{cam_no} FAILED: {stats['error']}"
                    )
                    continue
                self.log_line.emit(
                    f"warmup: cam{cam_no} done -- "
                    f"submitted={stats['submitted']} "
                    f"completed={stats['completed']} "
                    f"touch={stats['touch_s']*1000.0:.0f} ms "
                    f"drain={stats['drain_s']*1000.0:.0f} ms "
                    f"total={stats['total_s']*1000.0:.0f} ms"
                )
                warmed += 1
        wall_s = _time.perf_counter() - t0
        self.completed.emit(warmed, wall_s)


# ---------------------------------------------------------------------------


def _is_pcb_pulse_noise(line: str) -> bool:
    """True for the PCB's routine motor-pulse chatter (``ack: +++``/``ack: ---``
    and the bare mode-echo digit it prints after each pulse).

    Autofocus dithers the lens continuously, so the PCB echoes an ack per pulse
    command -- tens of lines/second during an M5 run. Logging each one appends to
    the GUI widget AND does a synchronous write to the session-log file (which lives
    on the Google-Drive-backed workspace), on the GUI thread, so the flood stalled
    the UI to a freeze. These lines carry no diagnostic value the ``af-servo`` /
    ``af-raw`` logs don't already have, so they are dropped from the human log.
    Control lines (``Swp#``, ``Mode 5:``, ``ack: $M5*``, homing banners, etc.) are
    NOT matched and still log + drive mode sync."""
    s = line.strip()
    if s.startswith("$ENC="):  # encoder position stream: the tracker consumes it on
        return True            # the reader thread; logging every reading just spams
    if s.isdigit():  # bare mode-echo (e.g. "5") printed after servicing a pulse
        return True
    if s.startswith("ack:"):
        cmd = s[4:].split(";", 1)[0].strip()  # acked command chars before "; A#..."
        return cmd != "" and all(c in "+-" for c in cmd)  # only +/- pulse acks
    return False


class MainWindow(QMainWindow):
    """Top-level window: mode bar, project header, camera rows, preview, log."""

    camera_recovery_finished = Signal(int)   # cameras recovered; after re-arm + counter zero

    def __init__(
        self,
        cti_path: str,
        clearcore_port: Optional[str] = None,
        gps_port: Optional[str] = None,
        demo: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._demo = bool(demo)
        if self._demo:
            # Simulated cameras + PCB (no GPS); see demo_backend. Imported here so a
            # flight launch never loads it.
            from .demo_backend import DemoCameraManager, DemoClearCore
            demo_ctrl = DemoClearCore()
            clearcore_port = "DEMO"
        else:
            demo_ctrl = None
        # Build date shown in the title bar + status bar, exactly like
        # the Bay14Reader GUI does (date-only).
        self._build_date = build_date("%Y-%m-%d") or "unknown"
        self.setWindowTitle(
            f"Iron 661 Capture + Focus  (build {self._build_date})"
        )
        # Tuned for common rig HDMI panels (e.g. 1280×800): prioritize preview area.
        self.resize(1280, 800)

        # ── Human-readable session log file ─────────────────────────────
        # Opened first so the session-start header lands before any other
        # activity. Everything that scrolls past the on-screen log window
        # is mirrored here via :meth:`_log`; button presses, dialogs,
        # periodic GPS fixes, and per-line summaries are appended too.
        self._session_log = SessionLogger()
        if self._session_log.path:
            log.info("session log: %s", self._session_log.path)
        # Per-line acquired/written/dropped baselines, captured when a
        # line container opens so the close-time summary reports the
        # frames belonging to *that* line (see :meth:`_log_ended_line_summary`).
        self._line_count_baseline: dict[int, tuple[int, int, int]] = {}

        # ── Core state ──────────────────────────────────────────────────
        self._modes = ModeController()
        self._context = CaptureContext(project="Project", line=1)
        self._serial = SerialClient(self._modes, controller=demo_ctrl)
        self._serial.line_received.connect(self._on_serial_line)
        self._serial.connection_changed.connect(self._on_serial_connection_changed)

        # GPS: a shared correlator + receiver. The receiver pushes
        # every decoded ExtEventPVTGeodetic block straight into the
        # correlator from its reader thread (synchronous callback)
        # so the per-camera AcquisitionThreads can read fixes back
        # via :meth:`GpsCorrelator.pop_fix_for` on the next frame
        # without a Qt signal round-trip. The Qt signal is also
        # wired below so the GUI log can show counts.
        self._gps_correlator = GpsCorrelator()
        self._gps_receiver = GpsReceiver(
            on_event=self._gps_correlator.add_event,
        )
        self._gps_receiver.connection_changed.connect(
            self._on_gps_connection_changed
        )
        self._gps_receiver.error_logged.connect(self._log)
        self._gps_receiver.parser_stats.connect(self._on_gps_parser_stats)
        self._gps_receiver.event_received.connect(self._on_gps_event_received)
        self._gps_port_arg = gps_port
        # An open port is not a proven link -- see :meth:`_refresh_gps_panel`.
        self._gps_link_verified = False
        self._gps_probe_attempts = 0

        self._manager = DemoCameraManager() if self._demo else CameraManager(cti_path)
        self._pipelines: list[CameraPipeline] = []
        self._rows: list[CameraRow] = []
        self._drive_roots: dict[int, Optional[str]] = {1: None, 2: None, 3: None}
        # Sweep containers the last preflight wrote, deleted once the writers close them.
        self._preflight_line_paths: list[str] = []
        self._clearcore_port_arg = clearcore_port

        # Diagnostic counters for the M4 focus-tap chain. Logged at low
        # cadence inside :meth:`_on_raw_focus_frame` so a smoke run
        # immediately tells us where frames are getting lost (e.g.
        # mode-mismatch vs camera-mismatch vs nothing arriving at all).
        self._raw_focus_drops_not_in_m4 = 0
        self._raw_focus_drops_wrong_cam = 0
        self._raw_focus_forwarded = 0

        self._pending_mode_apply: Optional[Mode] = None
        self._pending_mode_timer: Optional[QTimer] = None
        # M3 End-line is deferred: after sending ``!`` we keep recording
        # (gate on, lines open) until the PCB finishes the in-progress
        # sweep, so EVERY camera captures the final sweep instead of only
        # the previewed one.
        self._pending_end_line = False
        self._pending_end_line_timer: Optional[QTimer] = None
        # Ignore PCB ``Mode 4: …`` banners until warmup completes so boot /
        # connect serial chatter cannot drive :meth:`_apply_mode_state_after_uart`.
        self._mode_ui_ready = False
        self._last_preview_qimage: Optional[QImage] = None
        # 100% full-resolution preview toggle (top-left of preview window).
        self._preview_zoom_on = False
        # Software-only brightness multiplier on the preview image (lets the
        # operator fly at minimum analog gain and still see the ground).
        # Never applied to recorded data.
        self._preview_display_gain = 1.0
        # Split-screen preview: when on, every connected camera streams a
        # decimated preview composited side by side. Resolution is fixed at
        # a low subsample so three feeds stay cheap without user tuning.
        self._split_preview_on = False
        self._split_preview_images: dict[int, QImage] = {}
        self._split_preview_subsample = 4  # 1/4× — good enough to see image
        # Log panel beside preview (toggleable to give preview more width).
        self._log_visible = False
        self._below_cam_split_saved: Optional[list[int]] = None

        # ── Build UI shell first so log + status bar exist before camera open
        self._build_ui()
        self._install_button_press_logging()
        self._sync_preview_sensor_fields()
        self._preflight = self._build_preflight_runner()

        # Permanent build-date readout in the status bar (matches Bay14Reader).
        _build_lab = QLabel(f"Build: {self._build_date}")
        _build_lab.setStyleSheet("color: gray;")
        self.statusBar().addPermanentWidget(_build_lab)

        self.statusBar().showMessage(f"GenTL CTI: {cti_path}")

        # System-resource pre-flight: surfaces problems that would
        # otherwise only show up as a kernel BSOD partway through a
        # capture. Runs synchronously here because it's just a few
        # WMI / disk_usage queries -- fast (<100 ms) and safe to do on
        # the GUI thread before bring-up.
        self._preflight_resource_check()

        # Sweep counters update tick (rate-capped GUI refresh)
        self._sweep_tick = QTimer(self)
        self._sweep_tick.setInterval(200)
        self._sweep_tick.timeout.connect(self._refresh_sweep_labels)

        # Per-camera drive fill bars: 2 s is plenty to watch a line eat a drive
        # (~2.5 GB/s worst case is ~0.1 %/s on a 4 TB U.2) without hammering Win32.
        self._drive_fill_tick = QTimer(self)
        self._drive_fill_tick.setInterval(2000)
        self._drive_fill_tick.timeout.connect(
            lambda: [row.refresh_fill() for row in self._rows]
        )
        self._drive_fill_tick.start()

        # ── Hardware bring-up runs on a worker QThread (KAYA's ia.start()
        # blocks for 10-30 s per camera and re-entering the GUI event loop
        # during those blocking calls crashes the SDK). The window paints
        # immediately; cameras come online progressively via signals.
        self._bringup_worker: Optional[_BringupWorker] = None
        self._warmup_worker: Optional[_PipelineWarmupWorker] = None
        self._reset_worker: Optional[_CameraResetWorker] = None
        QTimer.singleShot(50, self._start_bringup_worker)

    def _preflight_resource_check(self) -> None:
        """Warn the user about system-resource conditions known to
        produce kernel BSODs or lose dumps during capture.

        Currently checks:

        * **C: free space** below 25 GB. Windows refuses to keep
          ``C:\\Windows\\MEMORY.DMP`` if free space is below this
          threshold, so any future bugcheck will reboot without
          leaving a dump for analysis. We saw a 0x1A
          MEMORY_MANAGEMENT BSOD with the dump auto-deleted for
          exactly this reason.
        * **Page file size** much smaller than the per-camera
          SharedMemory pool footprint. Windows backs SharedMemory
          via the page file; allocating 6+ GB of named shm at
          startup against a small/system-managed page file pushes
          the system commit limit and stresses the kernel memory
          manager. Best practice on this host is a fixed page file
          >= 16 GB.

        We only WARN -- the user may have legitimate reasons not to
        fix these (e.g. they're running short on time before a
        flight). Capture will still proceed.
        """
        import shutil
        try:
            usage = shutil.disk_usage("C:\\")
            free_gb = usage.free / (1024**3)
            if free_gb < 25.0:
                self._log(
                    f"WARN: C: free = {free_gb:.1f} GB (<25 GB). Windows "
                    "will auto-delete kernel memory dumps below this "
                    "threshold; if a BSOD happens during capture we "
                    "won't have a dump to diagnose it."
                )
        except Exception as exc:
            log = logging.getLogger("app.gui_log")
            log.debug("disk_usage(C:) failed: %s", exc)

        # Per-camera shm pool footprint (computed from the same
        # constants the pipeline uses, so this stays accurate if we
        # bump the pool size). Three cameras' worth assumed.
        try:
            from .pipeline import (
                CameraPipeline,
                EXPECTED_FRAME_HEIGHT, EXPECTED_FRAME_WIDTH, SHM_POOL_SLOTS,
            )
            per_cam_gb = (
                SHM_POOL_SLOTS * EXPECTED_FRAME_HEIGHT
                * EXPECTED_FRAME_WIDTH * 2
            ) / (1024**3)
            total_gb = per_cam_gb * 3
            self._log(
                f"shm pool: {SHM_POOL_SLOTS} slots/cam ({per_cam_gb:.1f} GB), "
                f"{total_gb:.1f} GB committed across 3 cameras."
            )
            # Codec + writer layout: visible at every launch for diagnosing drops.
            from .pipeline import WriterProcessClient
            n_packers = WriterProcessClient.DEFAULT_N_PACKERS
            n_io_writers = WriterProcessClient.DEFAULT_N_IO_WRITERS
            codec_env = os.environ.get("BAY14_CAPTURE_CODEC", "").strip().lower()
            eff_codec = codec_env if codec_env else "none"
            if eff_codec == "none":
                self._log(
                    f"capture codec: none (no compression). Writers: "
                    f"{n_packers} packer + {n_io_writers} io_writer "
                    "subprocess(es) per camera."
                )
            else:
                blosc2_nthreads = int(
                    os.environ.get("BAY14_BLOSC2_NTHREADS", "2")
                )
                self._log(
                    f"capture codec: {eff_codec}; encoder budget: "
                    f"{n_packers} packer subproc/cam x "
                    f"{blosc2_nthreads} blosc2 nthread "
                    f"+ {n_io_writers} io_writer subproc/cam "
                    f"= {(n_packers * blosc2_nthreads + n_io_writers) * 3} "
                    "threads worst-case across 3 cams; each line writes "
                    f"{n_io_writers} part-file(s) per camera."
                )
        except Exception:
            pass

    # ── Bring-up signal plumbing ────────────────────────────────────────

    def _start_bringup_worker(self) -> None:
        self._log("Opening cameras on background thread (≈30 s on KAYA)…")
        self.statusBar().showMessage("Opening cameras…")

        worker = _BringupWorker(
            manager=self._manager,
            mode_controller=self._modes,
            context=self._context,
            # Capture the camera number per pipeline so the lambda the
            # worker holds resolves to *this* row's drive at write time.
            get_drive_root=lambda cam_no: (lambda: self._drive_roots.get(cam_no)),
            # The correlator's pop_fix_for happens to match the
            # GpsProvider signature (camera_serial, perf_ns, sweep,
            # frame_in_sweep), so we can pass it straight through.
            gps_provider=self._gps_correlator.pop_fix_for,
            heading_provider=self._gps_correlator.heading_deg,
            focus_provider=self._focus_metadata,
        )
        worker.log_line.connect(self._log)
        worker.failed.connect(self._on_bringup_failed)
        worker.devices_listed.connect(
            lambda n: self.statusBar().showMessage(f"GenTL: {n} devices visible; starting…")
        )
        worker.camera_opened.connect(self._on_camera_opened)
        worker.pipeline_starting.connect(self._on_pipeline_starting)
        worker.pipeline_ready.connect(self._on_pipeline_ready)
        worker.completed.connect(self._on_bringup_completed)
        self._bringup_worker = worker
        worker.start()

        # Kick off side-bring-up that doesn't depend on cameras: drive
        # enumeration + ClearCore auto-connect run on the GUI thread but
        # they're milliseconds-fast so they don't block anything visible.
        QTimer.singleShot(100, self._refresh_drive_lists)
        QTimer.singleShot(120, self._auto_connect_clearcore)
        QTimer.singleShot(140, self._auto_connect_gps)

    @Slot(str)
    def _on_bringup_failed(self, msg: str) -> None:
        self._log(f"ERROR: {msg}")
        try:
            self._session_log.error("Camera open failed", msg)
        except Exception:
            pass
        QMessageBox.critical(
            self, "Camera open failed",
            f"{msg}\n\n"
            "Check that Vision Point is closed, no leftover Python\n"
            "process is holding the cameras, and the rig is powered.",
        )
        self.statusBar().showMessage("Camera open failed.")

    @Slot(int, str, list)
    def _on_camera_opened(self, cam_no: int, serial: str, align_log: list) -> None:
        # Tell the serial client which PCB slot this camera is wired to, so
        # ``$ENC`` tags land on the app camera number the rest of the app uses.
        # Earliest point where both numbers are known.
        self._serial.set_camera_serial(cam_no, serial)
        if 1 <= cam_no <= len(self._rows):
            self._rows[cam_no - 1].set_serial(serial)
        try:
            self._focus_panel.set_camera_device_id(cam_no, serial)
        except Exception:
            pass
        for line in align_log:
            self._log(f"cam{cam_no}: {line}")

    @Slot(int, str)
    def _on_pipeline_starting(self, cam_no: int, serial: str) -> None:
        self.statusBar().showMessage(f"Starting cam{cam_no} ({serial})…")

    @Slot(int, object)
    def _on_pipeline_ready(self, cam_no: int, pipeline: object) -> None:
        if not isinstance(pipeline, CameraPipeline):
            self._log(f"cam{cam_no}: bad pipeline object received")
            return
        self._pipelines.append(pipeline)
        if 1 <= cam_no <= len(self._rows):
            row = self._rows[cam_no - 1]
            self._wire_pipeline_signals(pipeline, row)
            # Flip the row title to green so the operator can see at a
            # glance which cameras came up. (Rows for cameras that
            # weren't opened stay grey.)
            row.set_brought_up(True)
        # Light up the matching radio in the focus-panel's in-view
        # camera selector so the operator can switch focus targets
        # without leaving M4.
        try:
            self._focus_panel.set_camera_available(cam_no, True)
        except Exception:
            pass
        self._sync_preview_sensor_fields()
        self._log(f"cam{cam_no}: pipeline started")

    @Slot(int)
    def _on_bringup_completed(self, started: int) -> None:
        self.statusBar().showMessage(
            f"Cameras ready ({started}/{len(self._rows)})  |  KAYA: "
            f"{self._manager.cti_path}"
        )
        # Stage 3: arm ALL pipelines that came up. The preview radio is
        # purely a "which feed do I want to look at right now" toggle --
        # all cameras stream into their AcquisitionThreads in lockstep
        # so M0/M1 sweep capture (when the writer is enabled) records
        # all three simultaneously. The KAYA kernel driver tolerated
        # this fine in StreamPix at the same trigger rates; the BSOD we
        # hit at NUM_BUFFERS=8 was a per-camera ring-size issue, not a
        # multi-camera arming issue. (If we ever see the BSOD again,
        # halve NUM_BUFFERS in kyfg_backend.py before further reducing
        # the number of concurrently armed cameras.)
        for p in self._pipelines:
            try:
                p.arm(log_sink=self._log)
            except Exception as exc:
                self._log(f"cam{p.camera_number}: arm FAILED: {exc}")

        # Auto-select preview on the first pipeline that actually came up
        # (which row that is depends on KYFGLib's enumeration order, not
        # the GUI row order). All cameras are now armed, but only the
        # selected one's preview pixmap gets pushed to the QLabel.
        if self._pipelines and not any(
            r.preview_radio.isChecked() for r in self._rows
        ):
            first_cam_no = self._pipelines[0].camera_number
            for row in self._rows:
                if row.camera_number == first_cam_no:
                    row.preview_radio.setChecked(True)
                    break
        self._sweep_tick.start()
        self._log(
            f"Bring-up: {started}/{len(self._rows)} cameras ready, all armed; "
            "starting writer-pipeline warmup..."
        )
        # Mode buttons stay DISABLED until warmup finishes -- otherwise
        # the operator can race a $M0/$M1 in before the writer
        # subprocesses have committed their slot pages and we'd see
        # the same 4-5-frame cold-start drop the warmup is meant to
        # eliminate. The gate is reopened in
        # :meth:`_on_warmup_completed`.
        self._start_pipeline_warmup()

    def _start_pipeline_warmup(self) -> None:
        """Kick off the per-pool warmup that pre-faults SHM slot pages
        and warms numba's JIT for the production frame size.

        Runs on a worker thread (``_PipelineWarmupWorker``) so the GUI
        stays responsive while the writer subprocesses chew through
        ~24 synthetic pack jobs per camera. See ``_PipelineWarmupWorker``
        and ``FrameBufferPool.warmup_writers`` for the rationale.
        """
        self.statusBar().showMessage("Warming writer pipelines (~1-2 s)...")
        self._warmup_worker = _PipelineWarmupWorker(
            list(self._pipelines), parent=self,
        )
        self._warmup_worker.log_line.connect(self._log)
        self._warmup_worker.completed.connect(self._on_warmup_completed)
        self._warmup_worker.start()

    @Slot(int, float)
    def _on_warmup_completed(self, cameras_warmed: int, wall_s: float) -> None:
        """Called on the GUI thread when the warmup worker is done.

        Re-enables the M-mode buttons (the gate we held during warmup)
        and surfaces a one-line summary so we can tell from the log
        whether warmup ran and how long it took.
        """
        self._log(
            f"Bring-up complete: warmup finished for {cameras_warmed} "
            f"camera(s) in {wall_s * 1000.0:.0f} ms; mode buttons enabled."
        )
        self.statusBar().showMessage(
            f"Ready  |  warmup {wall_s * 1000.0:.0f} ms"
        )
        for btn in self._mode_buttons.values():
            btn.setEnabled(True)
        self._reset_cameras_btn.setEnabled(True)
        self._reset_cameras_deep_btn.setEnabled(True)
        self._preflight_btn.setEnabled(True)
        self._mode_ui_ready = True
        self._refresh_line_buttons_enabled()

    @Slot()
    def _on_reset_cameras_soft(self) -> None:
        """Soft-recovery button: close+reopen, no DeviceReset."""
        self._start_camera_recovery(
            do_device_reset=False,
            label="soft",
            window_s=15,
        )

    @Slot()
    def _on_reset_cameras_deep(self) -> None:
        """Deep-recovery button: soft + DeviceReset on top."""
        self._start_camera_recovery(
            do_device_reset=True,
            label="deep",
            window_s=25,
        )

    def _start_camera_recovery(
        self, *, do_device_reset: bool, label: str, window_s: int,
    ) -> None:
        """Common click-path for both "Reset cameras" buttons.

        Recovery for the stuck-firmware state where a camera is armed
        but silently ignores PCB triggers. Runs on already-running
        pipelines so writer subprocesses + SHM pools (which are warm
        and committed) survive untouched.

        ``do_device_reset`` selects SOFT (close+reopen only, ~10-15 s)
        vs DEEP (close+reopen + DeviceReset, ~20-25 s). Use SOFT first;
        escalate to DEEP only if SOFT didn't unstick the camera. See
        :meth:`CameraManager.recover_cameras` for the per-step
        rationale.

        Sequence:
          1. Confirm with the operator.
          2. Drop into M0 on the PCB so triggers stop while we recover.
             Without this, cameras would re-arm into a live trigger
             stream and might catch the first sweep mid-cycle.
          3. Disable mode buttons + both reset buttons.
          4. Disarm every pipeline.
          5. Spawn :class:`_CameraResetWorker` with the chosen
             severity.
          6. On completion, re-arm pipelines and re-enable buttons.
        """
        if self._reset_worker is not None and self._reset_worker.isRunning():
            return
        if not self._pipelines:
            return
        from PySide6.QtWidgets import QMessageBox  # local import; rare use
        if do_device_reset:
            body = (
                "Run DEEP camera recovery?\n\n"
                "  1. DeviceReset every camera (firmware reboot).\n"
                "  2. Tear down + rebuild the SDK grabber session.\n"
                "  3. Re-enumerate cameras and re-apply configure().\n\n"
                f"Cameras will be disarmed for ~{window_s} seconds. Any "
                "sweep currently in flight will lose its tail frames.\n\n"
                "Use only when SOFT recovery wasn't enough -- a camera "
                "that still shows zero frames after a soft reset + re-arm."
            )
        else:
            body = (
                "Run SOFT camera recovery?\n\n"
                "  1. Tear down + rebuild the SDK grabber session.\n"
                "  2. Re-enumerate cameras and re-apply configure().\n\n"
                f"Cameras will be disarmed for ~{window_s} seconds. Any "
                "sweep currently in flight will lose its tail frames.\n\n"
                "Equivalent to closing + reopening the app but in-process. "
                "Try this first when a camera is armed but not streaming."
            )
        try:
            self._session_log.write(
                f"dialog: Reset cameras ({label})? -- awaiting operator confirmation",
                category="DIALOG",
            )
        except Exception:
            pass
        reply = QMessageBox.question(
            self,
            f"Reset cameras ({label})",
            body,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        confirmed = reply == QMessageBox.StandardButton.Yes
        try:
            self._session_log.write(
                f"dialog: Reset cameras ({label}) -> "
                f"{'Yes' if confirmed else 'No'}",
                category="DIALOG",
            )
        except Exception:
            pass
        if not confirmed:
            return
        self._run_camera_recovery(do_device_reset=do_device_reset, label=label, window_s=window_s)

    def _run_camera_recovery(
        self, *, do_device_reset: bool, label: str, window_s: int,
    ) -> None:
        """Steps 2-5 of :meth:`_start_camera_recovery`, no dialog. Also the preflight
        check's non-interactive entry point; completion is announced on
        :attr:`camera_recovery_finished`."""
        if self._reset_worker is not None and self._reset_worker.isRunning():
            return
        if not self._pipelines:
            return
        # Make sure the PCB isn't pumping triggers into cameras that
        # are about to vanish off the CXP link. M3 (idle) would be the
        # cleaner target if the firmware has it, but $M0* (which sends
        # nav-point-only triggers via the operator's interrupt button)
        # is universally safe.
        try:
            if self._serial.connected:
                self._serial.send_mode(0)
                self._log(f"sent $M0* (cameras-reset {label} preamble)")
        except Exception as exc:
            self._log(f"reset-cameras: send $M0* failed: {exc}")

        for btn in self._mode_buttons.values():
            btn.setEnabled(False)
        self._reset_cameras_btn.setEnabled(False)
        self._reset_cameras_deep_btn.setEnabled(False)
        # Mid-reset: the host has just disarmed every pipeline and is
        # about to re-arm them. Lock manual-line buttons too so a stray
        # click can't fire ?/! against a not-yet-streaming PCB state.
        self._mode_ui_ready = False
        self._refresh_line_buttons_enabled()
        self._log(f"Reset cameras ({label}): disarming pipelines...")
        self.statusBar().showMessage(
            f"Resetting cameras ({label}, ~{window_s} s)..."
        )

        for p in self._pipelines:
            try:
                p.disarm(log_sink=self._log)
            except Exception as exc:
                self._log(
                    f"cam{p.camera_number}: disarm before reset FAILED: {exc}"
                )

        self._reset_worker = _CameraResetWorker(
            self._manager,
            do_device_reset=do_device_reset,
            parent=self,
        )
        self._reset_worker.log_line.connect(self._log)
        self._reset_worker.completed.connect(self._on_reset_cameras_completed)
        self._reset_worker.start()

    @Slot(int)
    def _on_reset_cameras_completed(self, succeeded: int) -> None:
        """GUI-thread follow-up to :meth:`_on_reset_cameras_clicked`.

        Re-arms every pipeline (including those whose reset failed --
        re-arming a not-actually-reset camera is no worse than the
        state we were in before the user clicked, and a working camera
        shouldn't be stranded because a sibling failed), zeroes the
        per-camera ``acq`` / ``written`` / ``dropped`` GUI labels (so
        the user sees a clean baseline against which to read post-reset
        triggers), and re-opens the mode-button gate.
        """
        for p in self._pipelines:
            try:
                p.arm(log_sink=self._log)
            except Exception as exc:
                self._log(f"cam{p.camera_number}: arm-after-reset FAILED: {exc}")

        # Zero counters AFTER re-arm so any stray emit-while-disarmed
        # signal doesn't sneak past the reset (the AcquisitionThread
        # only emits while ``_armed`` + a consumer wants frames, but
        # re-arm is the safe cut-line either way).
        for p in self._pipelines:
            try:
                p.reset_counters()
            except Exception as exc:
                self._log(
                    f"cam{p.camera_number}: counter reset FAILED: {exc}"
                )

        for btn in self._mode_buttons.values():
            btn.setEnabled(True)
        self._reset_cameras_btn.setEnabled(True)
        self._reset_cameras_deep_btn.setEnabled(True)
        self._mode_ui_ready = True
        self._refresh_line_buttons_enabled()
        self._log(
            f"Reset cameras: complete -- {succeeded}/{len(self._pipelines)} "
            "camera(s) reset, all re-armed; counters zeroed."
        )
        self.statusBar().showMessage(
            f"Cameras reset ({succeeded}/{len(self._pipelines)}); ready."
        )
        self.camera_recovery_finished.emit(int(succeeded))

    def _auto_connect_clearcore(self) -> None:
        port = self._clearcore_port_arg or SerialClient.auto_detect()
        if not port:
            return
        self._port_combo.setCurrentText(port)
        self._connect_clearcore()

    def _auto_connect_gps(self) -> None:
        """Connect the GPS receiver at startup if a port is known.

        Order of preference: CLI ``--gps-port`` arg, then
        :meth:`GpsReceiver.auto_detect` -- which tries COM3 (the RS232 NMEA
        bridge) before any Septentrio USB descriptor. Silently no-ops when
        nothing's found so a rig running without GPS attached still boots
        cleanly. Connecting only opens the port; ``$E*`` proves the link.
        """
        port = self._gps_port_arg or GpsReceiver.auto_detect()
        if not port:
            return
        if hasattr(self, "_gps_port_combo"):
            self._gps_port_combo.setCurrentText(port)
        self._connect_gps()

    def _connect_gps(self) -> None:
        port = (
            self._gps_port_combo.currentText().strip()
            if hasattr(self, "_gps_port_combo")
            else (self._gps_port_arg or "")
        )
        if not port:
            self._log("GPS: no COM port selected")
            return
        try:
            self._gps_receiver.connect_port(port)
            self._log(f"GPS port {port} open")
        except Exception as exc:
            self._log(f"GPS connect failed: {exc}")

    def _toggle_gps(self) -> None:
        if self._gps_receiver.connected:
            self._gps_receiver.disconnect_port()
            return
        self._connect_gps()

    def _refresh_gps_ports(self) -> None:
        cur = self._gps_port_combo.currentText()
        self._gps_port_combo.clear()
        for port in GpsReceiver.list_ports():
            self._gps_port_combo.addItem(port)
        if cur:
            self._gps_port_combo.setCurrentText(cur)

    @Slot(bool)
    def _on_gps_connection_changed(self, connected: bool) -> None:
        self._gps_correlator.set_connected(connected)
        # Reset the parser-health watcher so the next session re-baselines its
        # error counters and re-emits a single "receiving events" line.
        self._gps_parser_alive_logged = False
        self._gps_parser_err_prev = None
        self._gps_link_verified = False
        if not connected:
            # Final diagnostic dump on disconnect (covers shutdown after the
            # common M1 -> M0 -> quit flow, which never re-enters a
            # non-recording mode and so misses the leave-recording dump).
            self._log_gps_correlator_stats()
        self._refresh_gps_panel()
        if connected:
            self._start_gps_link_probe()

    def _refresh_gps_panel(self) -> None:
        """Paint the GPS bar from (port open, link proven) -- in that order.

        An open port is NOT a working link here: the bridge is silent until an
        EXT_EVENT pulse reaches the receiver, so a disconnected antenna, a dead
        bridge and a healthy idle rig all look identical on the wire. Only a
        decoded sentence turns the panel green; until one arrives the bar stays
        red and says so, rather than claiming a link nobody has tested.
        """
        open_ = self._gps_receiver.connected
        live = open_ and self._gps_link_verified
        if hasattr(self, "_gps_status_label"):
            if live:
                text = "connected"
            elif open_:
                text = "port open, no data"
            else:
                text = "disconnected"
            self._gps_status_label.setText(text)
            self._gps_status_label.setStyleSheet(
                "color:#3a7;" if live else "color:#888;"
            )
        if hasattr(self, "_gps_connect_btn"):
            self._gps_connect_btn.setText("Disconnect" if open_ else "Connect")
        if hasattr(self, "_gps_bar_box"):
            self._gps_bar_box.setStyleSheet(_com_panel_style(live))

    def _start_gps_link_probe(self) -> None:
        """Fire ``$E*`` at the PCB and wait for the bridge to answer.

        ``$E*`` pulses the receiver's EXT_EVENT input without moving a motor or
        exposing a frame, so this is safe to run on every connect. A sentence
        coming back proves the whole chain at once -- PCB output, EXT_EVENT
        input, the bridge's SBF-to-NMEA translation, the RS232 wiring and this
        port -- which is the only honest test available for a port whose
        Windows descriptor says nothing.

        Retries because one pulse can be lost to a receiver still warming up.
        On timeout the port is closed so the operator sees a red bar and a
        reason, not a green one hiding a dead link.

        Verification itself is driven by ``_on_gps_event_received``, not by
        this timer: any sentence at all flips the bar green, so a rig whose
        ClearCore connects late still goes green on its first real trigger.
        """
        if not self._serial.connected:
            # No PCB, so no way to provoke a sentence. Leave the port open and
            # the bar red; the first real trigger will verify it.
            self._log(
                "GPS: link unverified -- ClearCore not connected, cannot fire "
                "$E*. Will verify on the first sentence received."
            )
            return

        self._gps_probe_attempts = 0

        def _attempt() -> None:
            if self._gps_link_verified or not self._gps_receiver.connected:
                return
            self._gps_probe_attempts += 1
            if self._gps_probe_attempts > _GPS_PROBE_ATTEMPTS:
                self._log(
                    f"GPS: no sentence after {_GPS_PROBE_ATTEMPTS} $E* probes "
                    f"on {self._gps_receiver.port} -- closing the port. Check "
                    f"the bridge, the RS232 lead, and the receiver's EXT_EVENT "
                    f"input."
                )
                self._gps_receiver.disconnect_port()
                return
            try:
                self._serial.fire_event()
            except Exception as exc:
                self._log(f"GPS: $E* probe failed to send: {exc}")
                return
            QTimer.singleShot(_GPS_PROBE_INTERVAL_MS, _attempt)

        self._log(f"GPS: probing {self._gps_receiver.port} with $E* ...")
        _attempt()

    @Slot(object, "qint64")
    def _on_gps_event_received(self, event: object, host_perf_ns: int) -> None:
        """First sentence of a session proves the link; later ones are free.

        Wired to every decoded sentence (~5 Hz while flying), so this stays a
        bool check after the first one.
        """
        if self._gps_link_verified:
            return
        self._gps_link_verified = True
        fix = getattr(event, "fix", None)
        where = ""
        if fix is not None and fix.has_position:
            where = f" at {fix.latitude_deg:+.6f}, {fix.longitude_deg:+.6f}"
        self._log(f"GPS: link verified on {self._gps_receiver.port}{where}")
        self._refresh_gps_panel()

    @Slot(dict)
    def _on_gps_parser_stats(self, stats: dict) -> None:
        """Parser-health watcher.

        The receiver emits cumulative parser stats every few seconds. Logging
        each one floods the GUI log, so we stay quiet while things are healthy
        and only speak up when something is actually off -- i.e. when an error
        counter (bad CRC / bad length / skipped garbage bytes) grows since the
        last sample. A single one-time line confirms the parser is alive once
        events start flowing. Full cumulative totals are still dumped on
        disconnect via the correlator/parser summary.
        """
        good = stats.get("good_blocks", 0)
        events = stats.get("events_decoded", 0)
        bad_crc = stats.get("bad_crc", 0)
        bad_len = stats.get("bad_length", 0)
        skipped = stats.get("skipped_bytes", 0)

        prev = getattr(self, "_gps_parser_err_prev", None)
        if prev is None:
            prev = {"bad_crc": 0, "bad_length": 0, "skipped_bytes": 0}

        def _delta(cur: int, key: str) -> int:
            # Negative => parser counters were reset (reconnect); count from 0.
            d = cur - prev.get(key, 0)
            return cur if d < 0 else d

        d_crc = _delta(bad_crc, "bad_crc")
        d_len = _delta(bad_len, "bad_length")
        d_skip = _delta(skipped, "skipped_bytes")

        self._gps_parser_err_prev = {
            "bad_crc": bad_crc,
            "bad_length": bad_len,
            "skipped_bytes": skipped,
        }

        if d_crc or d_len or d_skip:
            self._log(
                f"GPS parser: PROBLEM -- bad_crc +{d_crc} bad_len +{d_len} "
                f"skipped +{d_skip} B (cumulative: bad_crc={bad_crc} "
                f"bad_len={bad_len} skipped={skipped} B, good={good})"
            )
            return

        if not getattr(self, "_gps_parser_alive_logged", False) and events > 0:
            self._gps_parser_alive_logged = True
            self._log(f"GPS parser: receiving events (good={good})")

    def _log_gps_correlator_stats(self) -> None:
        """Dump the correlator's per-camera decision counters to the log.

        Called when leaving a recording mode so each capture's GPS-match
        outcome is visible without rummaging through the .bay14line files:

        * ``matched``       -- frame got a fix from its slot.
        * ``stale``         -- a slot entry existed but was too old (the
                               next sweep's event hadn't landed yet).
        * ``out_of_future`` -- only candidate was too far ahead in time.
        * ``no_event``      -- no slot entry for that frame_in_sweep at all.
        * ``fallback_latest`` -- no slot match, but a fresh fix of another
                               slot was used instead of writing no GPS.

        ``matched + fallback_latest + stale + out_of_future + no_event``
        should equal the number of frames each camera submitted while a
        line was open.
        """
        try:
            stats = self._gps_correlator.stats()
        except Exception as exc:
            self._log(f"GPS correlator stats unavailable: {exc}")
            return
        cams = stats.get("cameras", {})
        self._log(
            "GPS correlator summary: "
            f"total_events={stats.get('total_events', 0)} "
            f"gps_sweep_gen={stats.get('gps_sweep_gen', 0)} "
            f"slot_entries={stats.get('slot_table_entries', 0)}"
        )
        if not cams:
            self._log(
                "GPS correlator: no per-camera lookups recorded this run "
                "(no drive/project set, or no frames captured)."
            )
            return
        for cam, c in sorted(cams.items()):
            matched = c.get("matched", 0)
            stale = c.get("stale", 0)
            future = c.get("out_of_future", 0)
            no_evt = c.get("no_event", 0)
            fallback = c.get("fallback_latest", 0)
            total = matched + fallback + stale + future + no_evt
            # Percentage of frames that got *a* fix (direct slot match or
            # latest-fix fallback) -- the number that matters downstream.
            with_fix = matched + fallback
            pct = (100.0 * with_fix / total) if total else 0.0
            self._log(
                f"  cam {cam}: with_fix={with_fix} ({pct:.0f}%) "
                f"[matched={matched} fallback_latest={fallback}] "
                f"stale={stale} out_of_future={future} no_event={no_evt} "
                f"(total lookups={total})"
            )
            # On a stale storm, report how far off the nearest event was:
            # +age means the event arrived before the frame (window too
            # tight); -age means the frame ran ahead of its event.
            if stale:
                amin = c.get("stale_age_min_ms")
                amax = c.get("stale_age_max_ms")
                aavg = c.get("stale_age_avg_ms")
                if amin is not None:
                    self._log(
                        f"    cam {cam} stale-age (frame-event): "
                        f"min={amin:+.0f} ms avg={aavg:+.0f} ms "
                        f"max={amax:+.0f} ms"
                    )

    def _focus_metadata(self, camera_number: int) -> Optional[dict]:
        """The ``focus`` block stamped on every written frame, or None.

        *camera_number* is the camera whose frame this is, bound per pipeline at
        construction, so each camera's imagery carries ITS OWN lens position
        rather than whichever one happened to be selected.

        Called from the acquisition thread once per frame, so it only reads the
        lock-guarded encoder accessors and does no work.

        The point of recording this is that field curvature is a difference in
        best-focus *position*, so any later attempt to measure it from imagery
        needs to know where the lens was. Imagery written before this existed
        cannot answer that question at all -- see the 2026-06-23 flight, whose
        frames carry GPS and attitude but no lens position, leaving a focus
        bracket unanalysable after the fact.

        ``encoder_epoch`` is not decoration: counts are only comparable within
        one epoch, so a consumer must compare epochs before differencing two
        frames' counts.

        ``encoder_stale`` is the same kind of warning for the multi-camera rig.
        The three encoders share one SPI bus and the motor-selection relays pick
        which one answers, so only the SELECTED camera has a live count; the
        other two carry their last known value. Stepping camera 1 through focus
        leaves camera 2 and 3 reporting a fixed count that is real but no longer
        current, and a consumer that cannot tell the difference would read a
        through-focus series where none was flown.
        """
        try:
            if not self._serial.connected:
                return None
            cam = int(camera_number)
            if not self._serial.encoder_present_for(cam):
                return None
            selected = self._serial.selected_camera
            age = self._serial.encoder_age_for(cam)
            meta = {
                "encoder_count": float(self._serial.encoder_position_for(cam)),
                "encoder_epoch": self._serial.encoder_epoch,
                "encoder_camera": cam,
                "selected_camera": int(selected),
                "encoder_stale": cam != int(selected),
            }
            if age is not None:
                meta["encoder_age_s"] = round(float(age), 3)
            return meta
        except Exception:
            log.debug("focus metadata unavailable", exc_info=True)
            return None

    # Cadence for the periodic GPS position line written to the
    # session-log file. Coarse on purpose: the log is a wall-clock
    # trail of where the rig was, not a navigation feed.
    GPS_LOG_INTERVAL_S = 10.0

    def _log_gps_position(self) -> None:
        """Write one GPS-position line to the session log (periodic)."""
        try:
            if not self._gps_receiver.connected:
                self._session_log.gps("receiver not connected")
                return
            fix = self._gps_correlator.latest_fix()
            age_ns = self._gps_correlator.age_of_latest_ns()
            if fix is None or not fix.has_position:
                self._session_log.gps("no position fix yet")
                return
            age_s = (age_ns / 1e9) if age_ns is not None else None
            parts = [
                f"lat={fix.latitude_deg:.7f}",
                f"lon={fix.longitude_deg:.7f}",
            ]
            if fix.altitude_m is not None:
                parts.append(f"alt={fix.altitude_m:.1f}m")
            if fix.satellites is not None:
                parts.append(f"sats={fix.satellites}")
            if fix.fix_quality is not None:
                parts.append(f"quality={fix.fix_quality}")
            if fix.timestamp_utc is not None:
                parts.append(
                    f"utc={fix.timestamp_utc.strftime('%H:%M:%S')}"
                )
            if age_s is not None:
                parts.append(f"age={age_s:.1f}s")
            self._session_log.gps("  ".join(parts))
        except Exception:
            pass

    # How old can the freshest fix get before the overlay flips to
    # "NO GPS"? 2 s is comfortably more than one full sweep cycle.
    _GPS_OVERLAY_STALE_S = 2.0

    def _on_w10_toggle_toggled(self, checked: bool) -> None:
        """Operator switch for the live w10: attach / detach the scorer on every pipeline."""
        self._w10_live_enabled = bool(checked)
        self._w10_toggle_btn.setText("Live w10: On" if checked else "Live w10: Off")
        for p in self._pipelines:
            p.acq.set_w10_live(self._w10_live if checked else None)
        if checked:
            self._w10_live.disabled_line = None     # a fresh start also clears the fail-safe
        else:
            self._w10_last.clear()
            self._w10_off_note = ""
            self._w10_overlay_label.hide()
        self._log(f"w10 live scoring {'on' if checked else 'off'} (operator)")

    @Slot(dict)
    def _on_w10_live_scored(self, s: dict) -> None:
        """One live w10 score (W10LiveScorer.scored): log it, show the latest line per camera."""
        if not self._w10_live_enabled:
            return                                  # a score that was in flight when switched off
        line = format_score(s)
        self._log("w10 " + line)
        cam = int(s.get("camera", 0))
        w = s.get("w10")
        short = f"cam{cam} S{s.get('sweep', '?')} w10 " + (f"{w:.2f} px" if isinstance(w, float) and w == w else "--")
        if not s.get("ok"):
            short += " (gated)"
        self._w10_last[cam] = short
        self._w10_refresh_overlay()

    @Slot(str)
    def _on_w10_live_notice(self, msg: str) -> None:
        """Fail-safe on/off (W10LiveScorer.notice): log it and flag the overlay while scoring is off."""
        self._log("w10 " + msg)
        self._w10_off_note = "" if self._w10_live.disabled_line is None else "w10 OFF (recording drop)"
        self._w10_refresh_overlay()

    def _w10_refresh_overlay(self) -> None:
        lines = [self._w10_last[k] for k in sorted(self._w10_last)]
        if self._w10_off_note:
            lines.append(self._w10_off_note)
        if not lines:
            return
        self._w10_overlay_label.setText("\n".join(lines))
        self._w10_overlay_label.show()
        self._preview_label._reposition_w10_overlay()

    def _refresh_gps_overlay(self) -> None:
        """Update the preview's top-right GPS readout from the correlator.

        Called every 250 ms by ``_gps_overlay_timer``. We don't need
        sub-second freshness in the UI, and the polling pattern
        means we don't have to plumb a signal-per-event into the GUI.
        """
        overlay = self._gps_overlay_label
        # If we're not even connected, we render NO GPS in red.
        connected = self._gps_receiver.connected
        age_ns = self._gps_correlator.age_of_latest_ns()
        fresh = (
            connected
            and age_ns is not None
            and age_ns < int(self._GPS_OVERLAY_STALE_S * 1_000_000_000)
        )
        if not fresh:
            overlay.setText("NO GPS")
            overlay.setStyleSheet(
                "QLabel {"
                "  background: rgba(0, 0, 0, 160);"
                "  color: #ff5050;"
                "  font-family: Consolas, monospace;"
                "  font-size: 10pt;"
                "  padding: 3px 6px;"
                "  border-radius: 3px;"
                "}"
            )
            self._preview_label._reposition_gps_overlay()
            return
        fix = self._gps_correlator.latest_fix()
        if fix is None or not fix.has_position:
            overlay.setText("NO GPS")
            overlay.setStyleSheet(
                "QLabel {"
                "  background: rgba(0, 0, 0, 160);"
                "  color: #ff5050;"
                "  font-family: Consolas, monospace;"
                "  font-size: 10pt;"
                "  padding: 3px 6px;"
                "  border-radius: 3px;"
                "}"
            )
            self._preview_label._reposition_gps_overlay()
            return
        lat = fix.latitude_deg
        lon = fix.longitude_deg
        alt = fix.altitude_m
        ts_part = ""
        if fix.timestamp_utc is not None:
            ts_part = fix.timestamp_utc.strftime("%H:%M:%S.") + (
                f"{fix.timestamp_utc.microsecond // 1000:03d} UTC"
            )
        sat_part = (
            f"  {fix.satellites} sats" if fix.satellites is not None else ""
        )
        alt_part = f"  alt {alt:7.1f} m" if alt is not None else ""
        line1 = f"lat {lat:11.6f}   lon {lon:11.6f}"
        line2 = (ts_part + sat_part + alt_part).strip()
        overlay.setText(f"{line1}\n{line2}" if line2 else line1)
        overlay.setStyleSheet(
            "QLabel {"
            "  background: rgba(0, 0, 0, 160);"
            "  color: #c8ffc8;"
            "  font-family: Consolas, monospace;"
            "  font-size: 10pt;"
            "  padding: 3px 6px;"
            "  border-radius: 3px;"
            "}"
        )
        self._preview_label._reposition_gps_overlay()

    # ── UI construction ─────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(5)
        self._central_outer_layout = outer

        # Hold references to every chrome groupbox so :meth:`_apply_m4_layout`
        # can hide them when entering M4 (giving the focus panel maximum
        # screen real estate) and restore them on the way out.
        self._header_box = self._build_header()
        self._mode_bar_box = self._build_mode_bar()
        self._camera_rows_box = self._build_camera_rows()
        self._preview_area_box = self._build_preview_area()
        self._serial_bar_box = self._build_serial_bar()
        self._gps_bar_box = self._build_gps_bar()
        self._log_box = self._build_log()

        self._com_stack_box = QWidget()
        com_layout = QVBoxLayout(self._com_stack_box)
        com_layout.setContentsMargins(0, 0, 0, 0)
        com_layout.setSpacing(5)
        com_layout.addWidget(self._serial_bar_box)
        com_layout.addWidget(self._gps_bar_box)
        com_layout.addStretch(1)
        self._com_stack_box.setMinimumWidth(300)

        self._top_chrome_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._top_chrome_splitter.setChildrenCollapsible(False)
        self._top_chrome_splitter.addWidget(self._camera_rows_box)
        self._top_chrome_splitter.addWidget(self._com_stack_box)
        self._top_chrome_splitter.setStretchFactor(0, 3)
        self._top_chrome_splitter.setStretchFactor(1, 1)
        self._top_chrome_splitter.setSizes([900, 380])

        self._below_cam_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._below_cam_splitter.setChildrenCollapsible(False)
        self._below_cam_splitter.addWidget(self._log_box)
        self._below_cam_splitter.addWidget(self._preview_area_box)
        self._below_cam_splitter.setStretchFactor(0, 0)
        self._below_cam_splitter.setStretchFactor(1, 1)
        self._below_cam_splitter.setSizes([400, 880])
        self._below_cam_splitter.splitterMoved.connect(self._on_below_cam_split_moved)
        self._below_cam_split_saved = [400, 880]
        self._log_box.setVisible(False)
        total = max(sum(self._below_cam_splitter.sizes()), 1)
        self._below_cam_splitter.setSizes([0, total])

        outer.addWidget(self._header_box)
        outer.addWidget(self._mode_bar_box)
        outer.addWidget(self._top_chrome_splitter)
        outer.addWidget(self._below_cam_splitter, 1)

        self.setStatusBar(QStatusBar(self))

    def _apply_m4_layout(self, in_m4: bool) -> None:
        """Hide non-critical chrome in M4 so the focus panel fills the screen.

        We keep visible:
          * compact M0/M1/M4 controls (re-homed into the focus panel top row),
          * the exposure / analog gain / display gain bar (re-homed likewise, so
            the operator can expose for the focus scene without leaving M4),
          * the preview / focus-panel stack (which IS the focus view).

        We hide:
          * the project header (project / line / GNSS), which the
            operator already set before entering M4,
          * the camera rows (drive / counters / preview radios) -- the
            focus panel has its own compact camera selector now,
          * the whole left column (ClearCore COM bar + log) below the
            camera rows.
        The hidden widgets keep their state -- bringing them back on
        exit is just a single ``setVisible(True)`` per box, no
        re-creation cost.

        Idempotent: callers (e.g. every ``$M*`` apply) may invoke this
        with the *current* state. The reparent helpers only run on a
        true M4-vs-non-M4 transition so we don't keep re-adding
        ``addStretch(1)`` to the mode bar (which used to push the M0/M1/M4
        buttons rightward by one stretch slot per click).
        """
        non_critical_boxes = [
            self._header_box,
            self._top_chrome_splitter,
        ]
        for box in non_critical_boxes:
            box.setVisible(not in_m4)

        show_log = not in_m4 and self._log_visible
        self._log_box.setVisible(show_log)
        if show_log and self._below_cam_splitter.sizes()[0] <= 0:
            # Leaving M4 with the log toggled on: the splitter is still collapsed to
            # [0, total] from when the log was hidden, so just un-hiding the box leaves
            # it a thin sliver. Restore its saved width.
            self._below_cam_splitter.setSizes(
                self._below_cam_split_saved or [400, 880]
            )
        if hasattr(self, "_log_toggle_btn"):
            self._log_toggle_btn.setVisible(not in_m4)
            self._log_toggle_btn.blockSignals(True)
            self._log_toggle_btn.setChecked(self._log_visible)
            self._log_toggle_btn.setText(
                "Hide log" if self._log_visible else "Show log"
            )
            self._log_toggle_btn.blockSignals(False)

        marg = 4 if in_m4 else 6
        sp = 4 if in_m4 else 5
        self._central_outer_layout.setContentsMargins(marg, marg, marg, marg)
        self._central_outer_layout.setSpacing(sp)

        prior = getattr(self, "_mode_strip_in_focus", False)
        if in_m4 == prior:
            self._mode_bar_box.setVisible(not in_m4)
            return

        if in_m4:
            self._set_mode_buttons_compact(True)
            self._move_mode_strip_into_focus_panel()
            self._mode_bar_box.setVisible(False)
        else:
            self._restore_mode_strip_to_mode_bar()
            self._mode_bar_box.setVisible(True)
            self._set_mode_buttons_compact(False)
        self._mode_strip_in_focus = in_m4

    def _drain_layout(self, lay) -> None:
        """Remove every item (widgets + spacers/stretches) from *lay* in place."""
        if lay is None:
            return
        lay.removeWidget(self._mode_buttons_strip)
        lay.removeWidget(self._mode_status_label)
        while lay.count():
            lay.takeAt(0)

    def _move_mode_strip_into_focus_panel(self) -> None:
        self._drain_layout(self._mode_bar_layout)
        self._focus_panel.clear_mode_strip_layout()

        host = self._focus_panel.mode_strip_layout()
        self._drain_layout(host)
        host.addWidget(self._mode_buttons_strip, 0)
        host.addStretch(1)
        host.addWidget(self._mode_status_label)
        # Same trick for the sensor bar: one widget, one home at a time.
        self._preview_hdr_layout.removeWidget(self._preview_sensor_bar)
        self._focus_panel.sensor_bar_layout().addWidget(self._preview_sensor_bar, 0)

    def _restore_mode_strip_to_mode_bar(self) -> None:
        self._drain_layout(self._focus_panel.mode_strip_layout())
        self._drain_layout(self._mode_bar_layout)

        lay = self._mode_bar_layout
        lay.addWidget(self._mode_buttons_strip, 0)
        lay.addStretch(1)
        lay.addWidget(self._mode_status_label)
        self._focus_panel.sensor_bar_layout().removeWidget(self._preview_sensor_bar)
        self._preview_hdr_layout.addWidget(self._preview_sensor_bar, 0)

    def _set_mode_buttons_compact(self, compact: bool) -> None:
        """Small fixed-size mode buttons in M4; stable fixed-size row in M0/M1.

        Mode buttons use ``QSizePolicy.Fixed`` and explicit pixel sizes so the
        row does not reflow or stretch when check state / layout changes.
        Start/End line buttons follow the same compact/full sizing so the
        whole strip stays visually consistent in both layouts.
        """
        if compact:
            for mode, btn in self._mode_buttons.items():
                w = MODE_BUTTON_WIDTH_COMPACT.get(mode, 42)
                btn.setFixedSize(w, 24)
                btn.setStyleSheet(
                    "font-size: 10px; padding: 1px 4px; font-weight: bold;"
                )
            for btn in (self._start_line_btn, self._end_line_btn):
                btn.setFixedSize(64, 24)
                btn.setStyleSheet(
                    "font-size: 10px; padding: 1px 4px; font-weight: bold;"
                )
            self._mode_status_label.setStyleSheet(
                "font-weight: bold; font-size: 10px; padding: 0 6px;"
            )
        else:
            for mode, btn in self._mode_buttons.items():
                w = MODE_BUTTON_WIDTH_FULL.get(mode, 50)
                btn.setFixedSize(w, 32)
                btn.setStyleSheet("")
            for btn in (self._start_line_btn, self._end_line_btn):
                btn.setFixedSize(80, 32)
                btn.setStyleSheet("")
            self._mode_status_label.setStyleSheet(
                "font-weight: bold; padding: 0 12px;"
            )

    def _build_header(self) -> QWidget:
        box = QGroupBox("Project")
        layout = QHBoxLayout(box)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(6)

        self._project_edit = QLineEdit()
        self._project_edit.setText("Project")
        self._project_edit.setPlaceholderText("Project name (folder + filename component)")
        self._project_edit.setMinimumWidth(220)
        self._project_edit.editingFinished.connect(self._on_project_edited)
        layout.addWidget(QLabel("Name:"))
        layout.addWidget(self._project_edit, 1)

        # Line indicator + up/down
        layout.addSpacing(20)
        layout.addWidget(QLabel("Line:"))
        self._line_spin = QSpinBox()
        self._line_spin.setRange(-9999, 9999)
        self._line_spin.setValue(1)
        self._line_spin.setMaximumWidth(80)
        self._line_spin.valueChanged.connect(self._on_line_changed)
        layout.addWidget(self._line_spin)

        line_down_btn = QPushButton("Line ↓")
        line_down_btn.clicked.connect(lambda: self._line_spin.setValue(self._line_spin.value() - 1))
        layout.addWidget(line_down_btn)
        line_up_btn = QPushButton("Line ↑")
        line_up_btn.clicked.connect(lambda: self._line_spin.setValue(self._line_spin.value() + 1))
        layout.addWidget(line_up_btn)

        # Drive refresh
        layout.addSpacing(20)
        refresh_btn = QPushButton("Refresh drives")
        refresh_btn.clicked.connect(self._refresh_drive_lists)
        layout.addWidget(refresh_btn)

        # Camera recovery -- two buttons in escalating severity. Use
        # when a camera silently stops responding to PCB triggers
        # (the stuck-firmware state); cheaper than a full app restart
        # and avoids the kayak of opening Vision Point as a workaround.
        # Both buttons stay disabled until bring-up + warmup finish so
        # we don't race the bring-up worker.
        #
        # SOFT first, DEEP only if SOFT didn't unstick the camera.
        # SOFT covers the most common stuck state (matches what "VP
        # just opening" did to recover cam1 in the wild); DEEP adds
        # a per-camera DeviceReset on top for the rare case where
        # SOFT alone wasn't enough.
        self._reset_cameras_btn = QPushButton("Reset cameras")
        self._reset_cameras_btn.setToolTip(
            "SOFT recovery: close every camera, tear down + rebuild\n"
            "the SDK grabber session, re-open + re-configure each\n"
            "camera. Equivalent to 'close the app and reopen it' but\n"
            "in-process so writers + buffers survive.\n"
            "Use first when a camera is armed but not streaming.\n"
            "Disarms cameras for ~10-15 s while the recovery runs.\n"
            "Disabled until bring-up + warmup are complete."
        )
        self._reset_cameras_btn.setEnabled(False)
        self._reset_cameras_btn.clicked.connect(self._on_reset_cameras_soft)
        layout.addWidget(self._reset_cameras_btn)

        self._reset_cameras_deep_btn = QPushButton("Reset cameras (deep)")
        self._reset_cameras_deep_btn.setToolTip(
            "DEEP recovery: same as 'Reset cameras', plus a hardware-\n"
            "level DeviceReset on every camera (firmware reboot).\n"
            "Use only if SOFT recovery wasn't enough -- e.g. a camera\n"
            "still shows zero frames after a soft reset + re-arm.\n"
            "Disarms cameras for ~20-25 s while the recovery runs.\n"
            "Disabled until bring-up + warmup are complete."
        )
        self._reset_cameras_deep_btn.setEnabled(False)
        self._reset_cameras_deep_btn.clicked.connect(
            self._on_reset_cameras_deep
        )
        layout.addWidget(self._reset_cameras_deep_btn)

        # ── Single-trigger probe (PCB ``$T*`` / testClick) ───────────
        # Fires one camera-trigger pulse without commanding the focus
        # or nav-point motors. Each click should bump every healthy
        # camera's ``acq:N`` label by exactly 1 -- a camera that
        # doesn't tick after a few clicks is still stuck and needs a
        # deeper reset. Stays disabled until the ClearCore serial port
        # is connected; ``_on_serial_connection_changed`` flips it.
        # Independent of bring-up: useful even pre-bring-up to verify
        # the UART / PCB combo is alive (the cameras just won't fire).
        self._test_trigger_btn = QPushButton("Test trigger ($T)")
        self._test_trigger_btn.setToolTip(
            "Send one '$T*' pulse to the PCB (testClick handler).\n"
            "Fires a single trigger to every camera without moving\n"
            "the focus or nav-point motors. Use after a camera reset\n"
            "to confirm streaming -- each click should advance every\n"
            "healthy camera's 'acq:N' label by 1. A camera that\n"
            "doesn't tick is still stuck.\n"
            "Disabled until the ClearCore serial port is connected."
        )
        self._test_trigger_btn.setEnabled(False)
        self._test_trigger_btn.clicked.connect(self._on_test_trigger_clicked)
        layout.addWidget(self._test_trigger_btn)

        # ── Reset frame counters ─────────────────────────────────────
        # Zeroes every camera's acquired / written / dropped counters
        # back to a clean baseline without touching the cameras, the
        # serial link, or any open line container. Handy mid-session to
        # read a fresh count against the next set of triggers.
        self._reset_counters_btn = QPushButton("Reset counters")
        self._reset_counters_btn.setToolTip(
            "Zero the acquired / written / dropped counters on every\n"
            "camera back to 0. Does not touch the cameras, serial\n"
            "link, or any open line file -- only the on-screen\n"
            "counters restart from a clean baseline."
        )
        self._reset_counters_btn.clicked.connect(self._on_reset_counters_clicked)
        layout.addWidget(self._reset_counters_btn)

        # ── Preflight check ──────────────────────────────────────────
        # Focus motors (M4, +/-10k counts each, encoder watched), then one
        # real $Click* sweep in M0 (frames acquired, written, GPS-tagged),
        # with a deep camera recovery + retry if a camera acquires nothing.
        # See capture/preflight.py. Enabled with the reset buttons.
        layout.addSpacing(12)
        self._preflight_btn = QPushButton("Preflight check")
        self._preflight_btn.setToolTip(
            "Run the pre-flight sequence (~1.5-3 min):\n"
            "  1. M4: drive each focus motor +10k then -10k encoder counts\n"
            "     (motion on every pulse, consistent encoder, lens back at start).\n"
            "  2. M0: one $Click* sweep -- every camera gets the same frame\n"
            "     count, nothing dropped, all written, every frame GPS-tagged.\n"
            "  3. Watches the ClearCore for motor alerts / homing failures.\n"
            "A camera that acquires nothing gets a DEEP reset and one retry.\n"
            "Ends in M0 with fresh line containers. Click again to abort."
        )
        self._preflight_btn.setEnabled(False)
        self._preflight_btn.clicked.connect(self._on_preflight_clicked)
        layout.addWidget(self._preflight_btn)

        # GNSS placeholder (deferred until antenna)
        layout.addSpacing(20)
        gnss_label = QLabel("GNSS: not connected (deferred)")
        gnss_label.setStyleSheet("color: #888;")
        layout.addWidget(gnss_label)

        return box

    def _build_mode_bar(self) -> QWidget:
        box = QGroupBox("Mode")
        layout = QHBoxLayout(box)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._mode_bar_layout = layout

        self._mode_buttons: dict[Mode, QPushButton] = {}
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)

        strip = QWidget()
        strip_lay = QHBoxLayout(strip)
        strip_lay.setContentsMargins(0, 0, 0, 0)
        strip_lay.setSpacing(6)
        strip_lay.setAlignment(Qt.AlignmentFlag.AlignLeft)
        strip.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        for mode, tooltip in (
            (
                Mode.M0,
                "Normal — nav-point recording; frames are written to disk.",
            ),
            (
                Mode.M1,
                "Continuous — continuous trigger recording; frames are written to disk.",
            ),
            (
                Mode.M3,
                "CollectionON — manual line mode; host sends ?/! to bracket each line.",
            ),
            (
                Mode.M4,
                "Focus — tile sharpness analysis; no sweep writes.",
            ),
            (
                Mode.M5,
                "Ground Test — stepped mirror sweep with autofocus running "
                "in the background; preview only (no writes).",
            ),
            (
                Mode.M6,
                "Timelapse — mirrors in mid position firing once per second; "
                "frames are written to disk.",
            ),
        ):
            label = MODE_BUTTON_LABELS[mode]
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            btn.setFixedSize(MODE_BUTTON_WIDTH_FULL.get(mode, 50), 32)
            btn.setToolTip(tooltip)
            # Start-boundary fix: keep mode buttons disabled until
            # bring-up has armed every camera. Otherwise an eager
            # click could send ``$Mn*`` while the SDK is still
            # completing its arm sequence, and the first 1-2 trigger
            # pulses of the first sweep would land on a not-yet-
            # streaming sensor and be flushed.
            btn.setEnabled(False)
            # Exclusive checkable buttons: ``toggled(True)`` is reliable; ``clicked``
            # can fire on both check and uncheck depending on platform/theme.
            btn.toggled.connect(
                lambda checked, m=mode: checked and self._on_mode_clicked(m)
            )
            self._mode_buttons[mode] = btn
            self._mode_group.addButton(btn)
            strip_lay.addWidget(btn)

        # ── Manual line controls (M3 only) ───────────────────────────────
        # Live in the same strip as the mode buttons so they reparent with
        # them when the strip is moved into the M4 focus panel. They stay
        # greyed in every mode except M3 (and only when ClearCore is
        # connected) -- ``_refresh_line_buttons_enabled`` owns the gate.
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        strip_lay.addWidget(sep)

        self._start_line_btn = QPushButton("Start line")
        self._start_line_btn.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self._start_line_btn.setFixedSize(80, 32)
        self._start_line_btn.setToolTip(
            "M3 only: send '?' to the PCB to mark the START of a manual line."
        )
        self._start_line_btn.setEnabled(False)
        self._start_line_btn.clicked.connect(self._on_start_line_clicked)
        strip_lay.addWidget(self._start_line_btn)

        self._end_line_btn = QPushButton("End line")
        self._end_line_btn.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self._end_line_btn.setFixedSize(80, 32)
        self._end_line_btn.setToolTip(
            "M3 only: send '!' to the PCB to mark the END of a manual line."
        )
        self._end_line_btn.setEnabled(False)
        self._end_line_btn.clicked.connect(self._on_end_line_clicked)
        strip_lay.addWidget(self._end_line_btn)

        self._mode_buttons_strip = strip

        layout.addWidget(strip, 0)
        layout.addStretch(1)
        self._mode_status_label = QLabel("Current: UNKNOWN")
        self._mode_status_label.setStyleSheet("font-weight: bold; padding: 0 12px;")
        self._mode_status_label.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred
        )
        layout.addWidget(self._mode_status_label)

        return box

    def _build_camera_rows(self) -> QWidget:
        box = QGroupBox("Cameras")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(6, 5, 6, 5)
        layout.setSpacing(2)
        self._preview_group = QButtonGroup(self)
        self._preview_group.setExclusive(True)
        for n in (1, 2, 3):
            row = CameraRow(camera_number=n)
            row.drive_changed.connect(self._on_drive_changed)
            row.preview_selected.connect(self._on_preview_selected)
            self._rows.append(row)
            self._preview_group.addButton(row.preview_radio)
            layout.addWidget(row)
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setFrameShadow(QFrame.Shadow.Sunken)
            layout.addWidget(sep)
        # Split-screen preview row (exclusive with per-camera preview radios).
        split_row = QWidget()
        split_layout = QHBoxLayout(split_row)
        split_layout.setContentsMargins(3, 1, 3, 1)
        split_layout.setSpacing(6)

        self._split_preview_radio = QRadioButton("Split screen preview")
        self._split_preview_radio.setToolTip(
            "Preview all connected cameras (up to 3) side by side instead of\n"
            "one at a time. M0/M1 only; preview quality is kept low automatically."
        )
        self._preview_group.addButton(self._split_preview_radio)
        self._split_preview_radio.toggled.connect(self._on_split_preview_toggled)

        split_layout.addWidget(self._split_preview_radio)
        split_layout.addStretch(1)
        layout.addWidget(split_row)
        return box

    def _build_preview_area(self) -> QWidget:
        # Stack the live preview (M0/M1) and the focus panel (M4) so we can
        # swap at zero cost when the mode changes; both keep getting frames
        # via their own taps so there's no warm-up time on switch.
        self._preview_label = _PreviewDisplayLabel(PREVIEW_PLACEHOLDER_LABEL)
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setStyleSheet(
            "background:#101010; color:#888; font-family:Consolas, monospace;"
            "padding:2px;"
        )
        self._preview_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding,
        )
        self._preview_label.setMinimumHeight(200)

        # GPS overlay -- top-right corner of the preview area. Reads
        # the freshest correlator fix on a 4 Hz timer (slow enough
        # that the AlignRight repositioning isn't expensive and fast
        # enough that the operator sees the position update with
        # every sweep). Initial style is the "NO GPS" red.
        self._gps_overlay_label = QLabel("NO GPS", self._preview_label)
        self._gps_overlay_label.setTextFormat(Qt.TextFormat.PlainText)
        self._gps_overlay_label.setStyleSheet(
            "QLabel {"
            "  background: rgba(0, 0, 0, 160);"
            "  color: #ff5050;"
            "  font-family: Consolas, monospace;"
            "  font-size: 10pt;"
            "  padding: 3px 6px;"
            "  border-radius: 3px;"
            "}"
        )
        self._preview_label.set_gps_overlay(self._gps_overlay_label)

        # Live w10 readout -- bottom-right. One frame per sweep (F7) of every recording camera
        # is scored off the recording path (w10_live.W10LiveScorer, sec.54.12); the latest line
        # per camera is shown here and every score goes to the log. Hidden until the first score.
        self._w10_overlay_label = QLabel("", self._preview_label)
        self._w10_overlay_label.setTextFormat(Qt.TextFormat.PlainText)
        self._w10_overlay_label.setStyleSheet(
            "QLabel {"
            "  background: rgba(0, 0, 0, 160);"
            "  color: #80ff80;"
            "  font-family: Consolas, monospace;"
            "  font-size: 10pt;"
            "  padding: 3px 6px;"
            "  border-radius: 3px;"
            "}"
        )
        self._preview_label.set_w10_overlay(self._w10_overlay_label)
        self._w10_overlay_label.hide()
        self._w10_last: dict[int, str] = {}   # camera number -> latest overlay line
        self._w10_off_note = ""               # overlay line while the fail-safe has scoring off
        self._w10_live = W10LiveScorer(parent=self)
        self._w10_live.scored.connect(self._on_w10_live_scored)
        self._w10_live.notice.connect(self._on_w10_live_notice)

        # Zoom toggle -- top-left corner of the preview window. Switches
        # the M0/M1 preview between the whole-frame low-res view and a
        # 100% (1:1) full-resolution centre crop. Disabled in M4 (the
        # focus panel owns the preview there). Checkable so its label
        # reflects the current state.
        self._preview_zoom_btn = QPushButton("Zoom: Fit", self._preview_label)
        self._preview_zoom_btn.setCheckable(True)
        self._preview_zoom_btn.setToolTip(
            "Toggle the preview between a fit-to-window low-resolution\n"
            "view of the whole frame and a 100% (1:1) full-resolution\n"
            "centre crop of the previewed camera. M0/M1 only."
        )
        self._preview_zoom_btn.setStyleSheet(
            "QPushButton {"
            "  background: rgba(0, 0, 0, 160);"
            "  color: #e0e0e0;"
            "  font-family: Consolas, monospace;"
            "  font-size: 10pt;"
            "  padding: 3px 8px;"
            "  border: 1px solid rgba(255,255,255,40);"
            "  border-radius: 3px;"
            "}"
            "QPushButton:checked {"
            "  background: rgba(40, 90, 150, 200);"
            "  color: #ffffff;"
            "}"
        )
        self._preview_zoom_btn.toggled.connect(self._on_preview_zoom_toggled)
        self._preview_label.set_corner_button(self._preview_zoom_btn)

        self._gps_overlay_timer = QTimer(self)
        self._gps_overlay_timer.setInterval(250)
        self._gps_overlay_timer.timeout.connect(self._refresh_gps_overlay)
        self._gps_overlay_timer.start()

        # Periodic GPS position snapshot into the session-log file. The
        # on-screen overlay refreshes 4x/s, but the log only needs a
        # coarse trail of where the rig was, so this ticks every
        # GPS_LOG_INTERVAL_S and writes one line from the latest decoded
        # ExtEvent block (or a "no fix" note while the receiver is dark).
        self._gps_log_timer = QTimer(self)
        self._gps_log_timer.setInterval(int(self.GPS_LOG_INTERVAL_S * 1000))
        self._gps_log_timer.timeout.connect(self._log_gps_position)
        self._gps_log_timer.start()

        preview_box = QFrame()
        preview_box.setFrameShape(QFrame.Shape.StyledPanel)
        preview_box.setMidLineWidth(0)
        prev_layout = QVBoxLayout(preview_box)
        prev_layout.setContentsMargins(5, 5, 5, 5)
        prev_layout.setSpacing(3)

        preview_hdr = QHBoxLayout()
        preview_hdr.setSpacing(8)
        preview_hdr.setContentsMargins(0, 0, 0, 0)
        # Kept: the sensor bar below moves into the focus panel for M4 and back here.
        self._preview_hdr_layout = preview_hdr

        self._preview_title_lbl = QLabel(
            f"Preview ({MODE_BUTTON_LABELS[Mode.M0]} / {MODE_BUTTON_LABELS[Mode.M1]})"
        )
        self._preview_title_lbl.setStyleSheet("font-weight: bold; font-size: 11px;")

        self._preview_sensor_bar = QWidget()
        sensor_layout = QHBoxLayout(self._preview_sensor_bar)
        sensor_layout.setContentsMargins(0, 0, 0, 0)
        sensor_layout.setSpacing(6)

        exp_def = float(DEFAULT_CAMERA_CONFIG["ExposureTime"])
        gain_def = float(DEFAULT_CAMERA_CONFIG["Gain"])

        sensor_layout.addWidget(QLabel("Exposure (µs):"))
        self._preview_exposure_edit = QLineEdit()
        self._preview_exposure_edit.setPlaceholderText(f"{exp_def:g}")
        self._preview_exposure_edit.setMaximumWidth(110)
        self._preview_exposure_edit.setToolTip(
            "Exposure time in microseconds. Apply writes this value to "
            "the currently previewed (M0/M1) or focused (M4/M5) camera only."
        )
        sensor_layout.addWidget(self._preview_exposure_edit)

        sensor_layout.addWidget(QLabel("Analog gain:"))
        self._preview_gain_edit = QLineEdit()
        self._preview_gain_edit.setPlaceholderText(f"{gain_def:g}")
        self._preview_gain_edit.setMaximumWidth(80)
        self._preview_gain_edit.setToolTip(
            "GenICam Gain after selecting an analog GainSelector when the "
            "camera supports it (AnalogAll / AnalogWhite / Analog). Applies "
            "to the currently previewed (M0/M1) or focused (M4/M5) camera only."
        )
        sensor_layout.addWidget(self._preview_gain_edit)

        sensor_layout.addWidget(QLabel("Display gain:"))
        self._preview_display_gain_spin = QDoubleSpinBox()
        self._preview_display_gain_spin.setRange(
            PREVIEW_DISPLAY_GAIN_MIN, PREVIEW_DISPLAY_GAIN_MAX
        )
        self._preview_display_gain_spin.setDecimals(1)
        self._preview_display_gain_spin.setSingleStep(1.0)
        self._preview_display_gain_spin.setValue(self._preview_display_gain)
        self._preview_display_gain_spin.setSuffix("×")
        self._preview_display_gain_spin.setMaximumWidth(80)
        self._preview_display_gain_spin.setToolTip(
            "Software brightness multiplier on the PREVIEW image only. "
            "Lets you run the sensor at minimum analog gain and still see "
            "the scene. Takes effect immediately; recorded frames are "
            "never changed."
        )
        self._preview_display_gain_spin.valueChanged.connect(
            self._on_display_gain_changed
        )
        sensor_layout.addWidget(self._preview_display_gain_spin)

        self._preview_sensor_apply = QPushButton("Apply")
        self._preview_sensor_apply.setToolTip(
            "Write exposure and/or gain to the currently previewed / focused "
            "camera. Leave a field empty to skip changing that feature."
        )
        self._preview_sensor_apply.clicked.connect(self._on_preview_sensor_apply)
        sensor_layout.addWidget(self._preview_sensor_apply)
        sensor_layout.addStretch(1)

        self._preview_exposure_edit.returnPressed.connect(
            self._on_preview_sensor_apply
        )
        self._preview_gain_edit.returnPressed.connect(self._on_preview_sensor_apply)

        preview_hdr.addWidget(self._preview_title_lbl, 0, Qt.AlignmentFlag.AlignLeft)

        self._log_toggle_btn = QPushButton("Show log")
        self._log_toggle_btn.setCheckable(True)
        self._log_toggle_btn.setChecked(False)
        self._log_toggle_btn.setToolTip(
            "Show or hide the log panel beside the preview."
        )
        self._log_toggle_btn.toggled.connect(self._on_log_toggle_toggled)
        preview_hdr.addWidget(self._log_toggle_btn, 0)

        # Live w10 on/off (sec.54.12). Default on; off detaches the scorer from every pipeline
        # so the recording path does not even see the offer -- the operator's switch for a
        # flight where the live score is not worth any risk to the recording.
        self._w10_live_enabled = True
        self._w10_toggle_btn = QPushButton("Live w10: On")
        self._w10_toggle_btn.setCheckable(True)
        self._w10_toggle_btn.setChecked(True)
        self._w10_toggle_btn.setToolTip(
            "Score one frame per sweep (F7) of every recording camera with the w10\n"
            "sharpness metric and show it in the preview and the log. Off detaches the\n"
            "scorer completely; the recording path then does nothing extra."
        )
        self._w10_toggle_btn.toggled.connect(self._on_w10_toggle_toggled)
        preview_hdr.addWidget(self._w10_toggle_btn, 0)

        preview_hdr.addStretch(1)
        preview_hdr.addWidget(self._preview_sensor_bar, 0)
        prev_layout.addLayout(preview_hdr)
        prev_layout.addWidget(self._preview_label, 1)

        self._focus_panel = FocusPanel(self._serial)
        # Route the in-panel camera radio through the same handler the
        # legacy preview radios use, so pipeline focus-tap / preview /
        # PCB ``$Cn*`` all stay in sync no matter which control flipped
        # which.
        self._focus_panel.camera_chosen.connect(self._on_focus_panel_camera_chosen)
        self._focus_panel.m4_record_toggled.connect(self._on_m4_record_toggled)
        # Fine view (manual focus): the GUI-side leading-edge tracker picks a patch off
        # each coarse frame and pins its full-res equivalent for the pipeline to crop.
        self._fine_tracker = FineFocusTracker()
        self._locked_fine_roi: Optional[tuple] = None
        self._focus_panel.fine_view_toggled.connect(self._on_fine_view_toggled)
        self._focus_panel.fine_lock_changed.connect(self._on_fine_lock_changed)
        # Diagnostic chatter from the focus panel feeds the main log
        # so we can see at a glance whether frames are reaching it
        # and whether the worker is producing results.
        self._focus_panel.diag_log.connect(self._log)
        # w10 autofocus controller (M4/M5). Drives the relay-selected focus motor via
        # the serial client; gated to M4/M5 + connected PCB. The cameras whose full-res
        # emission it switched on are tracked so "Auto-focus all" cleans up after itself.
        self._af_full_res_cams: set[int] = set()
        self._autofocus = AutoFocusController(
            self._serial,
            self._modes,
            counts_per_pulse=AF_COUNTS_PER_PULSE,
            pin_rois_cb=self._af_pin_rois,
            select_camera_cb=self._on_focus_panel_camera_chosen,
            parent=self,
        )
        self._focus_panel.autofocus_requested.connect(self._on_autofocus_requested)
        self._focus_panel.autofocus_all_requested.connect(self._on_autofocus_all_requested)
        self._focus_panel.autofocus_stop_requested.connect(self._on_af_stop_requested)
        self._autofocus.status.connect(self._on_autofocus_status)
        self._autofocus.finished.connect(self._on_autofocus_finished)
        self._autofocus.run_state_changed.connect(self._on_autofocus_run_state)
        self._autofocus.sample.connect(self._on_af_sample)
        self._autofocus.roi_sample.connect(self._on_af_roi_sample)
        self._autofocus.live_scored.connect(self._on_af_live_scored)

        focus_host = QWidget()
        focus_layout = QVBoxLayout(focus_host)
        focus_layout.setContentsMargins(0, 0, 0, 0)
        focus_layout.setSpacing(0)
        focus_layout.addWidget(self._focus_panel)

        self._view_stack = QStackedWidget()
        self._view_stack.addWidget(preview_box)  # index 0
        self._view_stack.addWidget(focus_host)  # index 1

        return self._view_stack

    @Slot(int)
    def _on_focus_panel_camera_chosen(self, camera_number: int) -> None:
        """Operator clicked one of the in-panel ``Camera: cam1/2/3`` radios.

        We forward to the regular preview-selected handler so
        per-pipeline focus tap, PCB ``$Cn*``, and the on-screen preview
        target all align with what the focus panel is showing.
        """
        # Mirror the choice into the legacy CameraRow preview radios.
        # Block their toggled signals so we don't bounce back into the
        # focus panel.
        for row in self._rows:
            target = (row.camera_number == camera_number)
            row.preview_radio.blockSignals(True)
            row.preview_radio.setChecked(target)
            row.preview_radio.blockSignals(False)
        # New camera = a discontinuous frame stream, so the scene-motion reference and
        # any sensor-pinned lock belong to the camera we just left.
        self._fine_tracker.reset()
        self._locked_fine_roi = None
        self._focus_panel.clear_fine_lock()
        self._on_preview_selected(camera_number)

    def _build_serial_bar(self) -> QWidget:
        box = QGroupBox("Controller PCB")
        box.setStyleSheet(_com_panel_style(False))
        layout = QHBoxLayout(box)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.addWidget(QLabel("COM:"))
        self._port_combo = QComboBox()
        self._port_combo.setEditable(True)
        for port in SerialClient.list_ports():
            self._port_combo.addItem(port)
        layout.addWidget(self._port_combo)
        self._port_refresh_btn = QPushButton("⟳")
        self._port_refresh_btn.setToolTip("Re-scan COM ports")
        self._port_refresh_btn.setMaximumWidth(30)
        self._port_refresh_btn.clicked.connect(self._refresh_com_ports)
        layout.addWidget(self._port_refresh_btn)

        self._connect_btn = QPushButton("Connect")
        self._connect_btn.clicked.connect(self._toggle_clearcore)
        layout.addWidget(self._connect_btn)

        layout.addStretch()
        self._serial_status_label = QLabel("disconnected")
        self._serial_status_label.setStyleSheet("color:#888;")
        layout.addWidget(self._serial_status_label)
        return box

    def _build_gps_bar(self) -> QWidget:
        """COM bar for the GNSS receiver's NMEA feed.

        Same shape as :meth:`_build_serial_bar`: port combo + refresh,
        Connect/Disconnect button, status label. The receiver thread
        and parser live in :class:`GpsReceiver` (constructed in
        :meth:`__init__`); we only build the UI here. Auto-detect
        favours COM3, the RS232 bridge. The bar only turns green once a
        sentence has actually arrived -- see :meth:`_refresh_gps_panel`.
        """
        box = QGroupBox("GPS receiver")
        box.setStyleSheet(_com_panel_style(False))
        layout = QHBoxLayout(box)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.addWidget(QLabel("COM:"))
        self._gps_port_combo = QComboBox()
        self._gps_port_combo.setEditable(True)
        for port in GpsReceiver.list_ports():
            self._gps_port_combo.addItem(port)
        layout.addWidget(self._gps_port_combo)

        self._gps_port_refresh_btn = QPushButton("⟳")
        self._gps_port_refresh_btn.setToolTip("Re-scan COM ports")
        self._gps_port_refresh_btn.setMaximumWidth(30)
        self._gps_port_refresh_btn.clicked.connect(self._refresh_gps_ports)
        layout.addWidget(self._gps_port_refresh_btn)

        self._gps_connect_btn = QPushButton("Connect")
        self._gps_connect_btn.clicked.connect(self._toggle_gps)
        layout.addWidget(self._gps_connect_btn)

        layout.addStretch()
        self._gps_status_label = QLabel("disconnected")
        self._gps_status_label.setStyleSheet("color:#888;")
        layout.addWidget(self._gps_status_label)
        return box

    def _build_log(self) -> QWidget:
        box = QGroupBox("Log")
        box.setMinimumWidth(300)  # never let the splitter squeeze it to a sliver
        layout = QVBoxLayout(box)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(2)
        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(2000)
        self._log_view.setMinimumHeight(60)
        self._log_view.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        self._log_view.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._log_view.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 9pt;"
        )
        layout.addWidget(self._log_view)
        return box

    # ── Hardware bring-up ───────────────────────────────────────────────

    def _wire_pipeline_signals(self, pipeline: CameraPipeline, row: CameraRow) -> None:
        pipeline.acq.frame_acquired.connect(row.update_acq)
        pipeline.acq.preview_ready.connect(self._on_preview_ready)
        # IMPORTANT: connect to a bound @Slot of MainWindow (a QObject), not
        # to a Python lambda. With a lambda, Qt has no receiver-QObject and
        # therefore no thread context, so AutoConnection falls back to
        # DirectConnection -- the slot runs on the AcquisitionThread (a
        # QThread worker), then silently aborts the moment it touches any
        # GUI widget. The result is that no frame ever reaches the M4
        # focus panel even though pipeline-side ``focus-tap emit`` keeps
        # firing. Routing through ``_on_raw_focus_frame_slot`` (which uses
        # ``self.sender()`` to recover the originating pipeline) lets Qt
        # see ``self`` as the receiver and use QueuedConnection
        # automatically, the same way ``preview_ready`` already works.
        pipeline.acq.raw_focus_frame.connect(self._on_raw_focus_frame_slot)
        pipeline.acq.set_w10_live(self._w10_live if self._w10_live_enabled else None)
        pipeline.acq.raw_focus_preview_crops.connect(self._on_raw_focus_preview_crops)
        pipeline.acq.raw_focus_fine_crop.connect(self._on_raw_focus_fine_crop)
        pipeline.acq.failed.connect(self._on_pipeline_failed_slot)
        pipeline.acq.log_line.connect(self._log)
        # AcquisitionThread now owns the drop counter (the writer is a
        # subprocess and only sees writes the producer chose to
        # submit). ``frame_dropped`` replaces the old
        # WriterThread.frame_dropped_not_recording signal one-for-one.
        pipeline.acq.frame_dropped.connect(row.update_dropped)
        # NB: ``fetch_timeout_count`` still drives the per-row counter via
        # other code paths (none today). We deliberately don't log it
        # per-event -- the rate-limited summary inside AcquisitionThread
        # already emits via ``log_line``.

        if pipeline.writer is not None:
            # The writer QObject was constructed on the bring-up worker
            # thread (which is about to die). Move it to the GUI
            # thread so its telemetry QTimer fires on the same loop
            # the slots run on, and the deletion semantics behave.
            pipeline.writer.moveToThread(self.thread())
            pipeline.writer.frame_written.connect(row.update_written)
            pipeline.writer.write_error.connect(lambda msg: self._log(msg))
            pipeline.writer.log_line.connect(self._log)
            # Now that we're on the GUI thread and the QObject lives
            # here too, kick off the telemetry timer.
            pipeline.writer.start_telemetry()

    # ── Drive list ──────────────────────────────────────────────────────

    def _refresh_drive_lists(self) -> None:
        """Kick off drive enumeration on a worker thread.

        ``psutil.disk_usage`` can stall briefly on some Windows mounts even
        after we filter the obvious cloud-stream offenders, so we never
        run it on the GUI thread.
        """
        worker = _DriveListWorker(self)
        worker.result_ready.connect(self._on_drives_listed)
        worker.failed.connect(lambda msg: self._log(f"drive enumeration failed: {msg}"))
        # Auto-delete on finish so we don't accumulate worker objects
        # across the lifetime of the app (refresh button can be clicked
        # many times during a session).
        worker.finished.connect(worker.deleteLater)
        worker.start()

    @Slot(list)
    def _on_drives_listed(self, drives: list) -> None:
        non_system = [d for d in drives if not d.is_system]
        target_list = non_system if non_system else drives
        for row in self._rows:
            row.set_drives(target_list)
        self._log(
            f"drives: {len(drives)} recordable; offering {len(target_list)} as targets"
        )

    def _on_drive_changed(self, camera_number: int, drive: object) -> None:
        info = drive if isinstance(drive, DriveInfo) else None
        self._drive_roots[camera_number] = info.root if info else None
        if info:
            self._log(
                f"cam{camera_number}: drive set to {info.root} "
                f"({info.free_bytes / 1_073_741_824:.1f} GB free)"
            )
        else:
            self._log(f"cam{camera_number}: drive cleared (frames will be dropped)")

        # The .bay14line container for this camera is tied to the
        # drive path. While we're in a recording mode, close + reopen
        # so future frames land under the new drive (or stay closed
        # until a drive is set again). Other cameras are untouched.
        if not self._modes.is_recording_mode():
            return
        target = next(
            (p for p in self._pipelines if p.camera_number == camera_number),
            None,
        )
        if target is None:
            return
        if target.line_open:
            prior_path = target.writer.current_line_path if target.writer else None
            self._log_ended_line_summary(target, path=prior_path)
            target.close_line()
            if prior_path:
                self._log(
                    f"cam{camera_number}: line CLOSE due to drive change "
                    f"({os.path.basename(prior_path)})"
                )
        if info is not None:
            opened = target.open_line()
            if opened and target.writer is not None:
                self._capture_line_baseline(camera_number)
                new_path = target.writer.current_line_path or ""
                self._log(
                    f"cam{camera_number}: line OPEN on new drive "
                    f"({os.path.basename(new_path)})"
                )

    # ── Mode + serial ───────────────────────────────────────────────────

    def _on_mode_clicked(self, mode: Mode) -> None:
        # Map Mode.Mn -> int for the protocol command.
        n = {Mode.M0: 0, Mode.M1: 1, Mode.M3: 3, Mode.M4: 4, Mode.M5: 5, Mode.M6: 6}[mode]

        if self._pending_mode_apply is not None or self._pending_mode_timer is not None:
            self._log("cancelled pending deferred mode apply (new mode choice)")
            self._clear_deferred_mode_schedule()

        # Pre-flight for recording modes: tell the user up front what's
        # missing, so they don't hit M1 expecting to see TIFFs land on
        # disk and stare at an empty drive. We still send the mode to the
        # PCB (the user may want to enter M0/M1 just to drive the lights);
        # the writers self-gate.
        if mode in (Mode.M0, Mode.M1, Mode.M6):
            missing: list[str] = []
            if not (self._context.project or "").strip():
                missing.append("project name")
            cams_without_drive = [
                p.camera_number
                for p in self._pipelines
                if self._drive_roots.get(p.camera_number) is None
            ]
            if cams_without_drive:
                cams_str = ", ".join(f"cam{c}" for c in cams_without_drive)
                missing.append(f"target drive ({cams_str})")
            if missing:
                self._log(
                    "WARN entering "
                    f"M{n}: nothing will be saved until you set: "
                    + "; ".join(missing) + "."
                )

        # Start-boundary fix: ensure every pipeline's SDK is fully
        # streaming BEFORE we tell the PCB to start triggering.
        # Bring-up arms each camera, but if anything tripped a
        # disarm in between (a panic, a future "pause" feature, an
        # eager mode click during bring-up that slipped past the
        # button gate) we'd otherwise send ``$Mn*`` against a sensor
        # that's still in arm-completing state -- the first 1-2
        # trigger pulses of the first sweep would land on it and
        # the SDK would flush them.
        re_armed: list[int] = []
        arm_failed: list[int] = []
        for p in self._pipelines:
            if p.is_armed:
                continue
            try:
                p.arm(log_sink=self._log)
                re_armed.append(p.camera_number)
            except Exception as exc:
                arm_failed.append(p.camera_number)
                self._log(
                    f"cam{p.camera_number}: arm failed before "
                    f"$M{n}*: {exc}"
                )
        if arm_failed:
            if mode in (Mode.M0, Mode.M1, Mode.M6):
                self._log(
                    f"refusing to send $M{n}*: cams {arm_failed} not armed"
                )
                self.statusBar().showMessage(
                    f"Cannot enter M{n}: cam(s) {arm_failed} not armed — see log",
                    12000,
                )
                return
            # M4 is host-preview-focused: still swap the focus UI + command $M4*
            # so PCB matches; broken cameras simply won't deliver frames.
            self._log(
                "WARN entering M4 with cam(s) "
                f"{arm_failed} not armed — those feeds unavailable until recovery."
            )
        if re_armed:
            self._log(f"re-armed cams {re_armed} before $M{n}*")

        prev_mode = self._modes.mode
        defer_apply = (
            self._serial.connected
            and mode in (Mode.M0, Mode.M1)
            and prev_mode in (Mode.M0, Mode.M1)
            and mode != prev_mode
        )

        if not self._serial.connected:
            self._log(f"WARN: ClearCore not connected; would send $M{n}*")
            self._apply_mode_state_after_uart(mode)
            return

        try:
            self._serial.send_mode(n, update_local=False)
            self._log(f"sent $M{n}*")
        except Exception as exc:
            self._log(f"send_mode failed: {exc}")
            return

        if defer_apply:
            self._log(
                "deferring software mode / sweep reset for "
                f"{self.MODE_APPLY_AFTER_UART_S:.0f}s "
                "(let PCB finish current sweep + homing before flipping host)"
            )
            self._schedule_deferred_mode_apply(mode)
        else:
            self._apply_mode_state_after_uart(mode)

    def _sync_mode_buttons_ui(self, mode: Mode) -> None:
        """Update the exclusive mode bar to match *mode* without UART traffic."""
        btn = self._mode_buttons.get(mode)
        if btn is None or btn.isChecked():
            return
        for b in self._mode_buttons.values():
            b.blockSignals(True)
        btn.setChecked(True)
        for b in self._mode_buttons.values():
            b.blockSignals(False)

    @Slot(str)
    def _on_serial_line(self, line: str) -> None:
        # Drop the per-pulse motor ack flood (see _is_pcb_pulse_noise): logging it
        # per line synchronously writes the Google-Drive session log on the GUI
        # thread and froze the UI during M5 autofocus. Control lines still log below.
        if not _is_pcb_pulse_noise(line):
            self._log(f"PCB< {line}")
        if self._pending_end_line and line.lstrip().startswith("Swp#"):
            self._finalize_end_line()
            return
        # ``Parallax_CLRCR_V2_4.ino`` prints ``Mode 4: Focus adjustment`` after
        # ``setMode(4)``. If the PCB enters focus mode without the Qt mode bar
        # having run :meth:`_on_mode_clicked`, mirror that here so the stacked
        # focus view appears. (We deliberately do not parse Mode 0/1 banners:
        # those fire during PCB boot and would fight UNKNOWN / deferred applies.)
        if not self._mode_ui_ready:
            return
        banner_mode = None
        if line.startswith("Mode 4:"):
            banner_mode = Mode.M4
        elif line.startswith("Mode 5:"):
            banner_mode = Mode.M5
        if banner_mode is None:
            return
        if self._modes.mode == banner_mode:
            return
        self._log(
            f"PCB entered {banner_mode.name} (USB banner); syncing host UI"
        )
        self._clear_deferred_mode_schedule()
        self._apply_mode_state_after_uart(banner_mode)
        self._sync_mode_buttons_ui(banner_mode)

    @Slot(bool)
    def _on_serial_connection_changed(self, connected: bool) -> None:
        self._serial_status_label.setText("connected" if connected else "disconnected")
        self._serial_status_label.setStyleSheet(
            "color:#3a7;" if connected else "color:#888;"
        )
        self._connect_btn.setText("Disconnect" if connected else "Connect")
        if hasattr(self, "_serial_bar_box"):
            self._serial_bar_box.setStyleSheet(_com_panel_style(connected))
        # The $T probe button is a pure serial command (no camera
        # state required), so enable/disable in lockstep with the
        # ClearCore connection.
        self._test_trigger_btn.setEnabled(connected)
        self._refresh_line_buttons_enabled()

    def _refresh_line_buttons_enabled(self) -> None:
        """Start/End line buttons are M3-only and require ClearCore connected.

        Called from every place that can flip the relevant inputs:
        mode-state apply, serial connection changes, bring-up gate
        opening / closing.
        """
        in_m3 = self._modes.mode == Mode.M3
        ok = bool(in_m3 and self._serial.connected and self._mode_ui_ready)
        collecting = self._modes.m3_collecting()
        self._start_line_btn.setEnabled(ok and not collecting)
        self._end_line_btn.setEnabled(
            ok and collecting and not self._pending_end_line
        )

    def _recording_prerequisites_missing(
        self, only: Optional[int] = None
    ) -> list[str]:
        """What is still unset that would make an armed line write nothing.

        ``_open_lines_for_active_pipelines`` skips pipelines without a drive or
        project silently by design, so every manual arm (M3 Start line, M4
        Record) warns up front instead of letting the operator discover it from
        an empty drive afterwards.

        *only* restricts the drive check to one camera. M4 Record writes the
        selected camera alone, so without this it would nag about drives for two
        cameras it is never going to write to.
        """
        missing: list[str] = []
        if not (self._context.project or "").strip():
            missing.append("project name")
        cams_without_drive = [
            p.camera_number
            for p in self._pipelines
            if (only is None or p.camera_number == only)
            and self._drive_roots.get(p.camera_number) is None
        ]
        if cams_without_drive:
            cams_str = ", ".join(f"cam{c}" for c in cams_without_drive)
            missing.append(f"target drive ({cams_str})")
        return missing

    @Slot()
    def _on_start_line_clicked(self) -> None:
        """M3-only: send ``?`` and begin host-side collection for the line."""
        if self._modes.mode != Mode.M3:
            self._log("Start line: ignored (only valid in M3).")
            return
        if not self._serial.connected:
            self._log("Start line: ClearCore not connected -- ignored.")
            return
        if self._modes.m3_collecting():
            self._log("Start line: a line is already open -- ignored.")
            return

        missing = self._recording_prerequisites_missing()
        if missing:
            self._log(
                "WARN Start line: nothing will be saved until you set: "
                + "; ".join(missing) + "."
            )

        try:
            self._serial.start_line()
        except Exception as exc:
            self._log(f"Start line: send FAILED: {exc}")
            return
        self._log("sent ? (M3 line START).")

        for p in self._pipelines:
            try:
                p.set_full_res_emission(True)
            except Exception as exc:
                self._log(
                    f"cam{p.camera_number}: set_full_res_emission(True) "
                    f"failed: {exc}"
                )
        self._gps_correlator.reset_stats()
        self._modes.set_m3_collecting(True)
        self._open_lines_for_active_pipelines()
        self._refresh_line_buttons_enabled()

    @Slot()
    def _on_end_line_clicked(self) -> None:
        """M3-only: send ``!``; defer host close until sweep completes."""
        if self._modes.mode != Mode.M3:
            self._log("End line: ignored (only valid in M3).")
            return
        if not self._serial.connected:
            self._log("End line: ClearCore not connected -- ignored.")
            return
        if self._pending_end_line:
            self._log("End line: already finishing the current sweep -- ignored.")
            return
        try:
            self._serial.end_line()
        except Exception as exc:
            self._log(f"End line: send FAILED: {exc}")
            return
        self._log(
            "sent ! (M3 line END); keeping all cameras recording until the "
            "current sweep finishes."
        )

        self._pending_end_line = True
        if self._pending_end_line_timer is not None:
            self._pending_end_line_timer.stop()
            self._pending_end_line_timer.deleteLater()
        self._pending_end_line_timer = QTimer(self)
        self._pending_end_line_timer.setSingleShot(True)
        self._pending_end_line_timer.timeout.connect(
            self._on_end_line_grace_timeout
        )
        self._pending_end_line_timer.start(int(self.END_LINE_SWEEP_GRACE_S * 1000))
        self._refresh_line_buttons_enabled()

    @Slot()
    def _on_end_line_grace_timeout(self) -> None:
        if not self._pending_end_line:
            return
        self._log(
            "End line: no sweep-complete banner within "
            f"{self.END_LINE_SWEEP_GRACE_S:.0f}s -- closing the line now."
        )
        self._finalize_end_line()

    def _finalize_end_line(self) -> None:
        if not self._pending_end_line:
            return
        self._pending_end_line = False
        if self._pending_end_line_timer is not None:
            self._pending_end_line_timer.stop()
            self._pending_end_line_timer.deleteLater()
            self._pending_end_line_timer = None

        self._modes.set_m3_collecting(False)
        self._close_all_lines()
        for p in self._pipelines:
            try:
                p.set_full_res_emission(False)
            except Exception as exc:
                self._log(
                    f"cam{p.camera_number}: set_full_res_emission(False) "
                    f"failed: {exc}"
                )
        self._log("End line: sweep finished -- line closed, cameras idle.")
        self._refresh_line_buttons_enabled()

    @Slot(bool)
    def _on_m4_record_toggled(self, on: bool) -> None:
        """M4 'Record': write .bay14line containers while focusing.

        The M4 analogue of M3's Start/End line, and deliberately the same
        sequence: full-res emission first (so a slot exists to submit), then the
        mode gate, then open the containers. Unlike M3 there is no PCB bracket
        to send -- M4 already self-triggers, so arming is purely host-side.

        Every frame written here carries the lens encoder count in its chunk
        JSON, which is the point: a focus bracket flown through focus and out
        the other side can be sorted by lens position on the ground.

        Writes the SELECTED camera only. A bracket steps one lens, and only the
        selected camera's focus motor is on the relay, so the other two would
        contribute frames at a fixed (and separately drifting) lens position --
        three times the disk for data that can't be sorted by the thing the
        bracket varies. Switching preview while armed disarms; see
        :meth:`_on_preview_selected`.
        """
        if self._modes.mode != Mode.M4:
            self._log("Record: ignored (only valid in M4).")
            self._focus_panel.sync_m4_record_toggle(False)
            return

        active = self._active_pipeline()
        if on and active is None:
            self._log("Record: no camera selected for preview -- ignored.")
            self._focus_panel.sync_m4_record_toggle(False)
            return

        cam = active.camera_number if active is not None else None
        if on:
            missing = self._recording_prerequisites_missing(only=cam)
            if missing:
                self._log(
                    "WARN Record: nothing will be saved until you set: "
                    + "; ".join(missing) + "."
                )
        for p in self._pipelines:
            # Only the selected camera emits full-res while bracketing. In M4
            # the focus tap is already gated on the selected camera, so the
            # other two have no use for full-res frames.
            want = bool(on) and p.camera_number == cam
            try:
                p.set_full_res_emission(want)
            except Exception as exc:
                self._log(
                    f"cam{p.camera_number}: set_full_res_emission({want}) "
                    f"failed: {exc}"
                )
        self._modes.set_m4_recording(on)
        if on:
            self._gps_correlator.reset_stats()
            self._open_lines_for_active_pipelines(only=cam)
            self._log(
                f"Record ARMED in M4 on cam{cam} only: writing .bay14line "
                "with encoder stamps."
            )
            if exp := self._focus_panel.exposure_summary():
                self._log(f"exposure at arm: {exp}")
        else:
            self._close_all_lines()
            self._log("Record disarmed in M4: lines closed.")

    @Slot()
    def _on_test_trigger_clicked(self) -> None:
        """Fire one ``$T*`` (single trigger, no motors) at the PCB.

        Useful as a non-invasive liveness probe -- click after a
        recovery and watch each camera's ``acq:N`` label tick up
        once per click. A stuck camera will stay at zero, signalling
        the user to escalate to ``Reset cameras (deep)``.
        """
        if not self._serial.connected:
            self._log("Test trigger: ClearCore not connected -- ignored.")
            return
        try:
            self._serial.send_test_trigger()
        except Exception as exc:
            self._log(f"Test trigger: send FAILED: {exc}")
            return
        self._log("Test trigger: sent $T* (single pulse, motors idle).")

    @Slot()
    def _on_reset_counters_clicked(self) -> None:
        """Zero acquired / written / dropped counters on every camera.

        Pure counter reset: it does not arm/disarm cameras, touch the
        serial link, or roll any open line container. The per-camera
        labels flip to 0 immediately (``CameraPipeline.reset_counters``
        emits zeroed signals), and any open line's summary baseline is
        re-snapshotted so the next line-close summary stays consistent
        with the freshly reset counters.
        """
        if not self._pipelines:
            self._log("Reset counters: no cameras -- nothing to reset.")
            return
        n = 0
        for p in self._pipelines:
            try:
                p.reset_counters()
                if p.line_open:
                    # Counters genuinely restart at 0 after the reset
                    # (the acq/dropped zero arrives asynchronously from
                    # the camera thread), so pin the per-line baseline
                    # to zero rather than the stale row values -- that
                    # keeps the eventual line-close delta correct.
                    self._line_count_baseline[p.camera_number] = (0, 0, 0)
                n += 1
            except Exception as exc:
                self._log(
                    f"cam{p.camera_number}: counter reset FAILED: {exc}"
                )
        self._log(f"Reset counters: zeroed acq/written/dropped on {n} camera(s).")

    # ── Preflight check ─────────────────────────────────────────────────

    def _build_preflight_runner(self) -> PreflightRunner:
        """Wire :class:`PreflightRunner` to this window. Every hook speaks app camera
        numbers and reuses the exact code paths the operator's buttons use (mode click,
        deep recovery, line rotation), so the check exercises the real flight path."""
        mode_by_n = {0: Mode.M0, 1: Mode.M1, 3: Mode.M3, 4: Mode.M4, 5: Mode.M5, 6: Mode.M6}

        def send_mode(n: int) -> bool:
            mode = mode_by_n[n]
            self._on_mode_clicked(mode)
            self._sync_mode_buttons_ui(mode)
            return self._modes.mode == mode

        def frame_counts(cam: int) -> tuple[int, int, int]:
            row = next((r for r in self._rows if r.camera_number == cam), None)
            return row.frame_counts() if row is not None else (0, 0, 0)

        def armed(cam: int) -> bool:
            return any(p.camera_number == cam and p.is_armed for p in self._pipelines)

        def frames_delivered(cam: int) -> int:
            p = next((p for p in self._pipelines if p.camera_number == cam), None)
            return p.backend.frames_delivered if p is not None else 0

        def camera_serial(cam: int) -> str:
            p = next((p for p in self._pipelines if p.camera_number == cam), None)
            return p.camera_serial if p is not None else ""

        def gps_stats() -> dict:
            # The correlator counts per camera serial; the runner speaks camera numbers.
            return gps_stats_by_camera(
                self._gps_correlator.stats(),
                {p.camera_number: p.camera_serial for p in self._pipelines},
            )

        def rotate_lines() -> None:
            # The preflight sweep gets its own closed container; the flight starts on a
            # fresh one. Host-only, no PCB traffic. The sweep images have done their job
            # once the counters were read, so remember them for deletion at the end
            # (``_delete_preflight_lines``) -- ~2.8 GB/camera that would otherwise
            # accumulate on the U.2 drives, one line number ahead of every flight.
            self._preflight_line_paths = [
                path for p in self._pipelines if p.line_open
                for path in p.writer.current_line_paths
            ]
            self._close_all_lines()
            self._open_lines_for_active_pipelines()

        hooks = PreflightHooks(
            serial_connected=lambda: self._serial.connected,
            send_mode=send_mode,
            start_sweep=self._serial.start_sweep,
            select_camera=self._serial.select_camera,
            set_strength=self._serial.set_strength,
            pulse=self._serial.focus_step,
            encoder_position=self._serial.encoder_position_for,
            encoder_present=self._serial.encoder_present_for,
            encoder_age=self._serial.encoder_age_for,
            cameras=lambda: [p.camera_number for p in self._pipelines],
            armed=armed,
            frame_counts=frame_counts,
            frames_delivered=frames_delivered,
            gps_connected=lambda: self._gps_receiver.connected,
            gps_stats=gps_stats,
            recording_prerequisites_missing=self._recording_prerequisites_missing,
            recover_cameras=lambda: self._run_camera_recovery(
                do_device_reset=True, label="deep/preflight", window_s=25),
            rotate_lines=rotate_lines,
            camera_serial=camera_serial,
            log=self._log,
        )
        runner = PreflightRunner(hooks, parent=self)
        self._serial.line_received.connect(runner.on_serial_line)
        self.camera_recovery_finished.connect(runner.on_recovery_done)
        runner.status.connect(lambda m: self.statusBar().showMessage(f"Preflight: {m}"))
        runner.finished.connect(self._on_preflight_finished)
        return runner

    @Slot()
    def _on_preflight_clicked(self) -> None:
        if self._preflight.is_running():
            self._preflight.abort()
            self._log("Preflight: abort requested")
            return
        busy = []
        if self._reset_worker is not None and self._reset_worker.isRunning():
            busy.append("camera recovery running")
        if self._autofocus is not None and self._autofocus.is_running():
            busy.append("autofocus running")
        if self._pending_mode_apply is not None or self._pending_end_line:
            busy.append("mode change / line end still pending")
        if self._modes.m3_collecting():
            busy.append("M3 line open")
        if busy:
            self._log("Preflight: not started -- " + "; ".join(busy))
            self.statusBar().showMessage("Preflight: not started -- " + "; ".join(busy), 8000)
            return
        self._session_log.write("preflight: START", category="PREFLIGHT")
        # Lock first: a prerequisite failure finishes (and unlocks) inside start().
        self._set_preflight_lockout(True)
        self._preflight.start()

    def _set_preflight_lockout(self, on: bool) -> None:
        """While the runner owns the PCB, the operator's own mode / reset / AF clicks
        would race it. The preflight button itself stays live as the abort."""
        for btn in self._mode_buttons.values():
            btn.setEnabled(not on)
        self._reset_cameras_btn.setEnabled(not on)
        self._reset_cameras_deep_btn.setEnabled(not on)
        self._focus_panel.set_controls_locked(on)
        self._preflight_btn.setText("Abort preflight" if on else "Preflight check")

    @Slot(bool, object)
    def _on_preflight_finished(self, ok: bool, results) -> None:
        self._set_preflight_lockout(False)
        # Motors that passed had their '+' direction measured over 10k counts and their c/pulse
        # at every strength: the AF skips its polarity probe for them from now on (also on a
        # FAIL further down the sequence).
        self._autofocus.motor_polarity.update(self._preflight.motor_polarity)
        self._autofocus.motor_cpp.update(self._preflight.motor_cpp)
        if self._serial.connected:
            try:
                self._focus_panel.resend_strength()   # the motor test ran at S3
            except Exception as exc:
                self._log(f"Preflight: strength restore failed: {exc}")
        # Every measurement goes to the log; the dialog gets the operator's instructions only.
        measured = "\n".join(r.line() for r in results)
        for line in measured.splitlines():
            self._session_log.write(f"preflight: {line}", category="PREFLIGHT")
        self._log("Preflight " + ("PASSED" if ok else "FAILED") + ":\n" + measured)
        self.statusBar().showMessage("Preflight " + ("PASSED" if ok else "FAILED -- see dialog"), 15000)
        if self._preflight_line_paths:
            self._delete_preflight_lines()
        report = format_report(list(results))
        if ok:
            QMessageBox.information(self, "Preflight check: PASSED", report)
        else:
            QMessageBox.critical(self, "Preflight check: FAILED", report)

    _PREFLIGHT_DELETE_RETRIES = 30

    def _delete_preflight_lines(self, attempt: int = 0) -> None:
        """Remove the preflight sweep containers recorded by ``rotate_lines``.

        Only containers the check actually rotated out are touched (a FAIL before
        the sweep passed leaves its files on disk as evidence). ``close_line`` is
        asynchronous -- the io_writer still has to flush the index, fsync and close --
        and Windows refuses to unlink a file another process holds open, so a
        ``PermissionError`` is retried once a second for up to 30 s.
        """
        left: list[str] = []
        for path in self._preflight_line_paths:
            if not path.endswith(".bay14line"):   # only ever our own containers
                self._log(f"preflight: refusing to delete non-container {path}")
                continue
            try:
                os.remove(path)
                self._log(f"preflight: deleted sweep container {os.path.basename(path)}")
            except FileNotFoundError:
                pass
            except PermissionError:
                left.append(path)
            except OSError as exc:
                self._log(f"preflight: could not delete {path}: {exc}")
        self._preflight_line_paths = left
        if not left:
            return
        if attempt < self._PREFLIGHT_DELETE_RETRIES:
            QTimer.singleShot(1000, lambda: self._delete_preflight_lines(attempt + 1))
        else:
            self._preflight_line_paths = []
            self._log(
                f"preflight: {len(left)} sweep container(s) still held open by the "
                "writers after 30 s; left on disk: "
                + ", ".join(os.path.basename(p) for p in left)
            )

    def _toggle_clearcore(self) -> None:
        if self._serial.connected:
            self._serial.disconnect_port()
            return
        self._connect_clearcore()

    def _connect_clearcore(self) -> None:
        port = self._port_combo.currentText().strip()
        if not port:
            self._log("ClearCore: no COM port selected")
            return
        try:
            self._serial.connect_port(port)
            self._log(f"ClearCore connected on {port}")
        except Exception as exc:
            self._log(f"ClearCore connect failed: {exc}")

    def _refresh_com_ports(self) -> None:
        cur = self._port_combo.currentText()
        self._port_combo.clear()
        for port in SerialClient.list_ports():
            self._port_combo.addItem(port)
        if cur:
            self._port_combo.setCurrentText(cur)

    # ── Project + line ──────────────────────────────────────────────────

    def _on_project_edited(self) -> None:
        raw = self._project_edit.text()
        try:
            cleaned = sanitize_project_name(raw)
        except ValueError as exc:
            self._log(f"project name rejected: {exc}")
            self._context.project = ""
            return
        previous = self._context.project
        self._context.project = cleaned
        self._log(f"project = {cleaned!r}")
        # Project changes the on-disk path. If a recording mode is
        # currently active, roll the line containers so each camera's
        # next frames land in a file that reflects the new project.
        if cleaned != previous and self._modes.is_recording_mode():
            self._roll_line_containers(reason=f"project -> {cleaned!r}")

    def _on_line_changed(self, value: int) -> None:
        # Sweep counter reset happens inside CameraPipeline.open_line
        # for the recording-mode case (so sweep 1 frame 1 lines up
        # with the start of the new container). For the not-recording
        # case (mode != M0/M1) we still reset locally so the next
        # mode change starts cleanly.
        if not self._modes.is_recording_mode():
            self._context.line = value
            for p in self._pipelines:
                p.sweep.reset()
            self._log(f"line set to {value}; sweep counters reset")
            return
        # Race-tight order: close the old containers FIRST (so any
        # in-flight pack work lands with the old line number, which
        # is what the old container's file header already records),
        # THEN bump _context.line so subsequent WriteJobs carry the
        # new line number, THEN open new containers labelled with the
        # new line number. The close drain inside
        # WriterProcessClient.close_line guarantees no PackedResult
        # races past the LineCloseJob, so the boundary is clean.
        self._log(f"line set to {value}; rolling .bay14line containers")
        self._close_all_lines()
        self._context.line = value
        self._open_lines_for_active_pipelines()

    # ── Line-container lifecycle ───────────────────────────────────────

    def _open_lines_for_active_pipelines(
        self, only: Optional[int] = None
    ) -> None:
        """Open a fresh ``.bay14line`` for every pipeline that has the
        prerequisites (drive set, project name set).

        Pipelines without a drive or project skip silently -- the
        AcquisitionThread will drop frames at submit time and emit
        the standard ``no_line_open`` block message via its log_line
        signal, which is already wired into the GUI log.

        *only* opens a line for that one camera. M0/M1/M3 record all three
        cameras (they are mapping the ground and want every look angle), but an
        M4 focus bracket is about ONE lens, so it writes one camera.
        """
        for p in self._pipelines:
            if only is not None and p.camera_number != only:
                continue
            if p.line_open:
                # Defensive: if a stray open snuck through (e.g. a
                # mid-bring-up race), close it first so we always end
                # up with at most one open file per camera per call.
                self._log_ended_line_summary(p, path=p.writer.current_line_path)
                p.close_line()
            opened = p.open_line()
            if opened:
                self._capture_line_baseline(p.camera_number)
                self._log(
                    f"cam{p.camera_number}: line OPEN "
                    f"({os.path.basename(p.writer.current_line_path or '')})"
                )

    def _close_all_lines(self) -> None:
        for p in self._pipelines:
            if p.line_open:
                path = p.writer.current_line_path
                self._log_ended_line_summary(p, path=path)
                p.close_line()
                if path:
                    self._log(
                        f"cam{p.camera_number}: line CLOSE "
                        f"({os.path.basename(path)})"
                    )

    def _capture_line_baseline(self, camera_number: int) -> None:
        """Snapshot a camera's frame counters at the moment a line opens.

        The acquired/written/dropped counters are cumulative across a
        recording session (they only zero on a mode (re)start), so the
        per-line summary is computed as a delta against this baseline.
        """
        row = next(
            (r for r in self._rows if r.camera_number == camera_number), None
        )
        if row is not None:
            self._line_count_baseline[camera_number] = row.frame_counts()

    def _log_ended_line_summary(
        self, pipeline: CameraPipeline, *, path: Optional[str]
    ) -> None:
        """Write a per-line acquired/written/dropped summary to the session log.

        Called just before a camera's ``.bay14line`` container closes.
        Reports the frames belonging to the line that is ending, derived
        from the delta since :meth:`_capture_line_baseline` ran at open.
        """
        cam_no = pipeline.camera_number
        row = next((r for r in self._rows if r.camera_number == cam_no), None)
        if row is None:
            return
        acq, written, dropped = row.frame_counts()
        base = self._line_count_baseline.get(cam_no)
        if base is not None:
            b_acq, b_written, b_dropped = base
            # Clamp: a mode restart can zero the counters mid-line, which
            # would otherwise yield a negative delta. Fall back to the
            # raw cumulative value in that (rare) case.
            d_acq = acq - b_acq if acq >= b_acq else acq
            d_written = written - b_written if written >= b_written else written
            d_dropped = dropped - b_dropped if dropped >= b_dropped else dropped
        else:
            d_acq, d_written, d_dropped = acq, written, dropped
        try:
            line_no = self._context.line
        except Exception:
            line_no = "?"
        basename = os.path.basename(path) if path else "(no file)"
        self._session_log.line_summary(
            f"line {line_no} ended  cam{cam_no} ({basename})  "
            f"acquired={d_acq}  written={d_written}  dropped={d_dropped}"
        )
        # Drop the consumed baseline so a stray re-close can't report a
        # second (zero-delta) summary for the same line.
        self._line_count_baseline.pop(cam_no, None)

    def _roll_line_containers(self, *, reason: str) -> None:
        """Close current containers + open fresh ones for every pipeline.

        Used on line-spinbox bumps, project renames, drive changes
        while in M0/M1 -- anything that changes where future frames
        should land. The drain inside ``WriterProcessClient.close_line``
        guarantees in-flight pack work for the closing line is
        appended before the close runs, so we never split a sweep
        across two files.
        """
        self._log(f"rolling line containers: {reason}")
        self._close_all_lines()
        self._open_lines_for_active_pipelines()

    # ── Preview routing ─────────────────────────────────────────────────

    def _on_preview_selected(self, camera_number: int) -> None:
        """User clicked the preview radio for one camera.

        Stage 3 semantics: all cameras are armed all the time. This
        callback only:
          * picks which camera's preview pixmap goes to the on-screen
            QLabel (one ``set_preview(True)``, others False),
          * routes the M4 focus-tap to the same camera so tile sharpness
            analysis follows the on-screen image,
          * tells the PCB which camera the focus +/- buttons act on.

        It NEVER calls ``arm()`` / ``disarm()``. Hot-swapping the armed
        camera caused the operator to stare at "waiting for first frame"
        for ~1 s on every preview switch and made M0/M1 capture
        impossible (only the selected camera was streaming).
        """
        target_pipeline = None
        for p in self._pipelines:
            if p.camera_number == camera_number:
                target_pipeline = p
                break

        if target_pipeline is None:
            # User clicked a radio for a camera that failed to come up.
            # There's no pipeline to flip flags on, but we still want a
            # sensible GUI -- bounce the selection back to whichever
            # camera IS active so the user isn't left with no preview.
            available = ", ".join(
                f"cam{p.camera_number}" for p in self._pipelines
            ) or "(none)"
            self._log(
                f"cam{camera_number}: no pipeline (available: {available}); "
                "ignoring preview-radio click"
            )
            active = self._active_pipeline()
            if active is not None:
                for row in self._rows:
                    if row.camera_number == active.camera_number:
                        row.preview_radio.blockSignals(True)
                        row.preview_radio.setChecked(True)
                        row.preview_radio.blockSignals(False)
                        break
            self._sync_preview_sensor_fields()
            return

        # A focus-bracket container belongs to ONE camera. If Record is armed
        # and the operator promotes a different camera, the old camera's line
        # would stay open and keep writing while they focus another lens, which
        # silently mixes two lenses into one file. Disarm instead and make them
        # re-arm -- the same rule that makes any mode change disarm Record.
        if self._modes.m4_recording() and not any(
            p.line_open and p.camera_number == camera_number
            for p in self._pipelines
        ):
            self._log(
                f"Record disarmed: preview moved to cam{camera_number}; "
                "re-arm to bracket this camera."
            )
            self._on_m4_record_toggled(False)
            self._focus_panel.sync_m4_record_toggle(False)

        # Flip the preview flag exclusively to the chosen camera. The
        # focus tap follows the preview and is additionally gated on
        # M4. AcquisitionThreads for the non-selected cameras keep
        # running and processing frames; they just don't emit preview
        # QImages or raw_focus_frame signals so the GUI thread stays
        # quiet.
        in_focus = self._modes.is_af_mode()
        for p in self._pipelines:
            is_active = (p.camera_number == camera_number)
            p.set_preview(is_active)
            self._set_focus_tap(p, in_focus and is_active)
            # Keep the zoom flag + crop size + display gain current on
            # whichever camera is now emitting preview frames.
            try:
                p.set_preview_zoom(self._preview_zoom_on)
                p.set_preview_gain(self._preview_display_gain)
            except Exception:
                pass
        if self._preview_zoom_on:
            self._push_preview_zoom_size()

        self._log(f"preview = cam{camera_number}")
        self._last_preview_qimage = None
        self._preview_label.setText(
            f"cam{camera_number}: waiting for next frame..."
        )
        self._focus_panel.set_active_camera(camera_number)
        # Re-route ``$Cn*`` so the focus +/- buttons act on the camera
        # the user just promoted to active preview.
        if in_focus and self._serial.connected:
            try:
                self._serial.select_camera(camera_number)
                self._log(
                    f"sent $C{self._serial.pcb_slot_for(camera_number)}* "
                    f"(UI cam{camera_number}, serial {target_pipeline.camera_serial})"
                )
            except Exception as exc:
                self._log(f"select_camera failed: {exc}")
        self._sync_preview_sensor_fields()

    def _sync_preview_sensor_fields(self) -> None:
        """Refresh exposure/gain text from the currently previewed camera.

        Falls back to the lowest-numbered online camera when no preview
        radio is selected, so the fields still show a sensible value.
        The Exposure/Gain controls act on this same (previewed) camera --
        in M4/M5 that is the camera the focus panel's radio selected.
        """
        in_m4 = self._modes.is_focus_mode()
        self._preview_sensor_bar.setEnabled(bool(self._pipelines))
        if hasattr(self, "_preview_zoom_btn"):
            zoom_ok = (
                not in_m4 and not self._split_preview_on and bool(self._pipelines)
            )
            self._preview_zoom_btn.setEnabled(zoom_ok)
            self._preview_zoom_btn.setVisible(zoom_ok)
        if hasattr(self, "_split_preview_radio"):
            self._split_preview_radio.setEnabled(not in_m4 and bool(self._pipelines))
        if not self._pipelines:
            self._preview_exposure_edit.setText("")
            self._preview_gain_edit.setText("")
            return
        p = self._active_pipeline()
        if p is None:
            p = min(self._pipelines, key=lambda x: x.camera_number)
        exp = p.read_exposure_time_us()
        gn = p.read_gain()
        self._preview_exposure_edit.setText("" if exp is None else f"{exp:g}")
        self._preview_gain_edit.setText("" if gn is None else f"{gn:g}")

    @Slot()
    def _on_preview_sensor_apply(self) -> None:
        """Write exposure and/or gain to the currently previewed / focused camera only."""
        if not self._pipelines:
            self._log("Exposure/Gain: no cameras online.")
            return
        target = self._active_pipeline()
        if target is None:
            self._log(
                "Exposure/Gain: no camera is being previewed — pick a "
                "camera's preview radio first."
            )
            return
        exp_t = self._preview_exposure_edit.text().strip()
        gain_t = self._preview_gain_edit.text().strip()
        if not exp_t and not gain_t:
            self._log("Exposure/Gain: type a value in Exposure and/or Gain, then Apply.")
            return
        exp_v: Optional[float] = None
        gain_v: Optional[float] = None
        if exp_t:
            try:
                exp_v = float(exp_t.replace(",", "."))
            except ValueError:
                self._log(f"Exposure/Gain: invalid exposure {exp_t!r}")
                return
        if gain_t:
            try:
                gain_v = float(gain_t.replace(",", "."))
            except ValueError:
                self._log(f"Exposure/Gain: invalid gain {gain_t!r}")
                return

        cam = target.camera_number
        if exp_v is not None:
            ok, detail = target.set_exposure_time_us(exp_v)
            self._log(
                f"cam{cam} ExposureTime={exp_v:g} → "
                f"{'OK' if ok else 'FAIL'} ({detail})"
            )
        if gain_v is not None:
            ok, detail = target.set_gain(gain_v)
            self._log(
                f"cam{cam} Gain={gain_v:g} → "
                f"{'OK' if ok else 'FAIL'} ({detail})"
            )
        self._sync_preview_sensor_fields()


    # ── autofocus (M4 / M5, w10) ─────────────────────────────────────────

    def _on_raw_focus_preview_crops(self, crops, ts_iso: str) -> None:
        """A batch of the five pinned sensor-native ROI crops -> the w10 engine.
        Runs on the GUI thread; releases the pipeline's crop conflation latch first so
        the next batch can be staged while the engine scores this one."""
        p = self._active_pipeline()
        if p is not None:
            try:
                p.note_preview_crops_consumed()
            except Exception:
                pass
        self._autofocus.feed_crops(crops)

    # ── Fine view (M4 / M5 manual focus) ─────────────────────────────────

    @Slot(object, str)
    def _on_raw_focus_fine_crop(self, crop, ts_iso: str) -> None:
        """The tracked patch at full resolution. Latch released first so the acq thread
        can stage the next crop while this one is painted (same pattern as the coarse
        tap and the AF crop batch)."""
        acq = self.sender()
        if acq is not None and hasattr(acq, "note_fine_crop_consumed"):
            acq.note_fine_crop_consumed()
        if not self._focus_panel.fine_view_enabled():
            return
        active = self._active_pipeline()
        if active is None or active.acq is not acq:
            return
        self._focus_panel.receive_fine_crop(crop, ts_iso)

    def _update_fine_tracker(self, pipeline: CameraPipeline, coarse_raw) -> None:
        """Advance the tracker on one coarse frame and re-pin the pipeline's Fine ROI."""
        # Size the candidates to what the pane can show AT THIS ZOOM. Set every frame
        # because both inputs are the operator's: the pane expands with the window and
        # the zoom is a combo box, so any constant fits one layout and letterboxes the
        # rest. It takes effect at the next hand-over rather than mid-object, since the
        # pick only runs on re-acquire -- resizing the live tile would look like the
        # focus jumped.
        vw, vh = self._focus_panel.fine_view_sensor_wh()
        sub = KyfgCamera.SUBSAMPLE_FACTOR
        fh_c, fw_c = coarse_raw.shape[:2]
        # Even (Bayer), and never larger than the frame: an oversized tile yields an
        # EMPTY grid and the pick silently falls back to a centred ROI, dropping the
        # leading-edge behaviour this whole path exists for.
        self._fine_tracker.tile_wh = (min(fw_c, max(2, -(-vw // sub))) & ~1,
                                      min(fh_c, max(2, -(-vh // sub))) & ~1)
        self._fine_tracker.update(coarse_raw)
        tr = self._fine_tracker
        if self._locked_fine_roi is not None:
            # A lock pins a FIXED sensor ROI and the tracker wants a MOVING one. Keep
            # tracking (the motion estimate and dwell readout stay live) but let the
            # lock own the pipeline ROI -- otherwise the tracker overwrites it on the
            # very next coarse frame and the lock never holds.
            self._focus_panel.set_fine_track(None, "LOCKED to sensor patch")
            return
        roi_coarse = tr.predicted_roi()
        try:
            pipeline.set_fine_roi(
                None if roi_coarse is None else tuple(int(v * sub) for v in roi_coarse))
        except Exception as exc:
            self._log(f"Fine view: set_fine_roi failed: {exc}")
            return
        left = tr.frames_left()
        # Texture does not block the pick -- an out-of-focus lens produces none, and
        # that is exactly when the operator needs something to look at -- so show how
        # strong the target is instead of refusing to offer one.
        weak = "  WEAK" if tr.pick_score < tr.cfg.min_texture_score else ""
        self._focus_panel.set_fine_track(
            roi_coarse,
            f"obj #{tr.object_id} from {tr.lead_edge or 'static'}"
            f"  held {tr.frames_on_object}f  "
            + (f"~{left}f left" if left is not None else "no flow")
            + f"  dx{tr.motion_dx:+.1f} dy{tr.motion_dy:+.1f}"
            + f"  tex {tr.pick_score:.1f}{weak}")

    @Slot(bool)
    def _on_fine_view_toggled(self, on: bool) -> None:
        """Start the tracker fresh each time Fine view is entered, and release the
        pipeline ROI on the way out so no camera keeps cropping for a hidden view."""
        self._fine_tracker.reset()
        self._locked_fine_roi = None
        if not on:
            self._clear_fine_roi()

    def _clear_fine_roi(self) -> None:
        for p in self._pipelines:
            try:
                p.set_fine_roi(None)
            except Exception as exc:
                self._log(f"cam{p.camera_number}: set_fine_roi(clear) failed: {exc}")

    @Slot(bool)
    def _on_fine_lock_changed(self, locked: bool) -> None:
        """Freeze the Fine view on the patch it is showing, pinned in sensor coordinates
        (the scene then slides under it), or return to tracking."""
        if not locked:
            self._locked_fine_roi = None
            self._log("Lock tile: OFF (tracking).")
            return
        roi = self._fine_tracker.roi
        if roi is None:
            self._log("Lock tile: no tracked tile yet.")
            self._focus_panel.clear_fine_lock()
            return
        sub = KyfgCamera.SUBSAMPLE_FACTOR
        self._locked_fine_roi = tuple(int(v * sub) for v in roi)
        p = self._active_pipeline()
        if p is not None:
            try:
                p.set_fine_roi(self._locked_fine_roi)
            except Exception as exc:
                self._log(f"Lock tile: set_fine_roi failed: {exc}")
                return
        self._log(f"Lock tile: ON (pinned {self._locked_fine_roi}).")

    def _set_focus_tap(self, p: CameraPipeline, on: bool) -> None:
        """The focus view's feed from one pipeline: the coarse frame for the preview AND
        the five full-res w10 ROI crops, so the w10 number is live whenever the operator is
        looking at a camera in M4/M5 -- focusing by hand or under AF (sec.54). The crops
        need full-res emission, which M4 otherwise leaves off; off never takes it away
        from a camera that is recording (M4 Record owns the feed just as much)."""
        p.set_focus_tap(on)
        try:
            if on:
                p.set_full_res_emission(True)
                p.set_preview_rois(roi_boxes())
            else:
                p.set_preview_rois([])
                p.set_fine_roi(None)
                if not self._modes.is_recording_mode():
                    p.set_full_res_emission(False)
        except Exception as exc:
            self._log(f"cam{p.camera_number}: focus tap {'on' if on else 'off'} failed: {exc}")

    def _af_pin_rois(self, rois) -> None:
        """w10 AF (sec.54): (re)assert its five full-res ROIs on the active pipeline. The
        live focus tap already pins the same boxes; this covers a run started on a camera
        whose tap was flipped on before the pipeline existed."""
        if not rois:
            return
        p = self._active_pipeline()
        if p is None:
            return
        self._af_full_res_cams.add(p.camera_number)
        try:
            p.set_full_res_emission(True)
            p.set_preview_rois(list(rois))
        except Exception as exc:
            self._log(f"w10 AF: set_preview_rois failed: {exc}")

    def _af_enable_full_res(self, enable: bool) -> None:
        """Off (end of a run): release the crops on every camera the run touched EXCEPT the
        one the focus view is still showing -- its live w10 keeps the feed. "Auto-focus all"
        leaves the last camera selected, so this is what frees the other two."""
        if enable:
            self._af_pin_rois(roi_boxes())
            return
        active = self._active_pipeline()
        keep = active.camera_number if active is not None and self._modes.is_af_mode() else None
        for p in self._pipelines:
            if p.camera_number in self._af_full_res_cams and p.camera_number != keep:
                self._set_focus_tap(p, False)
        self._af_full_res_cams.clear()

    @Slot()
    def _on_af_stop_requested(self) -> None:
        self._autofocus.stop()

    def _encoder_now(self) -> Optional[int]:
        """Current motor-shaft encoder count, or None if no encoder is streaming."""
        if self._serial is None or not getattr(self._serial, "encoder_present", False):
            return None
        try:
            return int(self._serial.encoder_position)
        except Exception:
            return None

    def _on_af_sample(self, enc: int, score: float, _fine: bool = False) -> None:
        """One gated w10 frame -> both overlay graphs (the engine reports the raw
        encoder it read at the frame)."""
        self._focus_panel.af_graph_add_sample(int(enc), float(score))
        self._focus_panel.focus_scatter_add_point(float(enc), float(score))

    def _on_af_roi_sample(self, enc: int, widths) -> None:
        """w10 AF: the five per-ROI widths of one gated frame -> the scatter's per-ROI
        view ('ROIs' checkbox). A ROI without a w10 (NaN) draws nothing."""
        for k, wv in enumerate(widths or ()):
            if wv == wv:
                self._focus_panel.focus_scatter_add_point(float(enc), float(wv), roi=k)

    @Slot(float, object)
    def _on_af_live_scored(self, enc: float, stats) -> None:
        """A frame scored with no AF run active (manual focusing): the same w10 the AF
        would see, on the status line and the scatter, so the operator can creep onto
        the minimum by hand."""
        cfg = self._autofocus.config
        ok = stats is not None and frame_ok(stats, min_edges=cfg.min_edges, min_roi=cfg.min_roi)
        self._focus_panel.set_live_w10(
            int(round(enc)),
            float(stats["w10"]) if ok else None,
            int(stats.get("n_edges", 0)) if stats else 0,
        )
        if ok:
            self._focus_panel.focus_scatter_add_point(float(enc), float(stats["w10"]))
            for k in range(len(ROI_NAMES)):
                wv = stats.get(f"r{k}_w10")
                if wv is not None and wv == wv:
                    self._focus_panel.focus_scatter_add_point(float(enc), float(wv), roi=k)

    def _af_preconditions_ok(self) -> bool:
        if not self._modes.is_af_mode():
            self._log("Autofocus: switch to M4 (Focus) or M5 (Ground Test) mode first.")
            return False
        if not self._pipelines:
            self._log("Autofocus: no cameras online.")
            return False
        return True

    @Slot()
    def _on_autofocus_requested(self) -> None:
        if not self._af_preconditions_ok():
            return
        pipe = self._active_pipeline()
        if pipe is None:
            self._log("Autofocus: select a camera to preview first.")
            return
        self._autofocus.start_single(pipe.camera_number)

    @Slot()
    def _on_autofocus_all_requested(self) -> None:
        if not self._af_preconditions_ok():
            return
        self._autofocus.start_all(sorted(p.camera_number for p in self._pipelines))

    @Slot(str)
    def _on_autofocus_status(self, text: str) -> None:
        self._focus_panel.set_autofocus_status(text)
        self._log(text)

    @Slot(bool, dict)
    def _on_autofocus_finished(self, ok: bool, report: dict) -> None:
        self._log(f"autofocus finished: ok={ok} {report}")
        if exp := self._focus_panel.exposure_summary():
            self._log(f"exposure at AF end: {exp}")

    @Slot(bool)
    def _on_autofocus_run_state(self, running: bool) -> None:
        self._focus_panel.set_autofocus_running(running)
        self._focus_panel.set_controls_locked(running)
        if running:
            self._focus_panel.af_graph_clear()       # fresh trace per run
            self._focus_panel.focus_scatter_clear()  # score-vs-encoder starts anew per run
        else:
            # Run finished (or aborted): drop back to preview-only emission and
            # release the pinned ROIs.
            self._af_enable_full_res(False)

    def _active_pipeline(self) -> Optional[CameraPipeline]:
        """Return the pipeline whose preview radio is currently on, if any.

        Resolves by ``camera_number`` rather than by row index so the
        lookup still works in stage 1, where ``self._pipelines`` may have
        fewer entries than ``self._rows``.
        """
        for row in self._rows:
            if not row.preview_radio.isChecked():
                continue
            for p in self._pipelines:
                if p.camera_number == row.camera_number:
                    return p
        return None

    @Slot(object, str)
    def _on_raw_focus_frame_slot(self, raw, ts_iso: str) -> None:
        """Bound-method receiver for ``AcquisitionThread.raw_focus_frame``.

        ``raw_focus_frame`` is emitted from the per-camera AcquisitionThread
        worker. Connecting it to a bare lambda (the previous wiring) gave
        Qt no QObject context for the receiver, so AutoConnection silently
        used DirectConnection on the worker thread, which then aborted the
        moment the slot touched any GUI widget -- the M4 focus panel never
        saw a frame even though the per-camera tap was firing on cadence.

        Routing through this @Slot of MainWindow gives Qt a proper
        QObject receiver living on the GUI thread, so AutoConnection
        picks QueuedConnection and we get the frame back on the main
        thread before forwarding it on. ``self.sender()`` recovers the
        originating ``AcquisitionThread`` so we can map it back to the
        ``CameraPipeline`` the tap came from.
        """
        acq = self.sender()
        # Release the conflation latch immediately so the acq thread can stage
        # the NEXT frame while we process this one (bounds the 8 MB frame backlog
        # to ~1 in flight -- the fix for the M4 memory-blowup crash).
        if acq is not None and hasattr(acq, "note_focus_frame_consumed"):
            acq.note_focus_frame_consumed()
        pipeline = next(
            (p for p in self._pipelines if p.acq is acq),
            None,
        )
        if pipeline is None:
            # Connection survived past pipeline tear-down. Nothing to do
            # except quietly drop the frame.
            return
        self._on_raw_focus_frame(pipeline, raw, ts_iso)


    @Slot(str)
    def _on_pipeline_failed_slot(self, msg: str) -> None:
        """Bound-method receiver for ``AcquisitionThread.failed`` so the
        delivery is queued onto the GUI thread (same reason as
        :meth:`_on_raw_focus_frame_slot` -- a lambda receiver would have
        been a DirectConnection on the worker thread)."""
        self._log(f"FAILED: {msg}")

    def _on_raw_focus_frame(self, pipeline: CameraPipeline, raw, ts_iso: str) -> None:
        """Forward a raw uint16 frame from one pipeline into the focus panel,
        but only when that pipeline is the one currently selected for preview
        AND we're in M4. The other pipelines' tap stays disabled while
        their preview radios are off, but this guard makes it safe even if
        they were ever toggled on simultaneously."""
        # Diagnostic: surface why frames may or may not be reaching the
        # focus panel from M4. Cheap (1-line bumps + occasional log);
        # gated on focus mode so it doesn't spam in M0/M1.
        if not self._modes.is_af_mode():
            self._raw_focus_drops_not_in_m4 += 1
            if self._raw_focus_drops_not_in_m4 % 32 == 1:
                self._log(
                    "raw_focus_frame received while not in M4/M5 "
                    f"(count={self._raw_focus_drops_not_in_m4}); ignored"
                )
            return
        active = self._active_pipeline()
        if active is None or active.camera_number != pipeline.camera_number:
            self._raw_focus_drops_wrong_cam += 1
            if self._raw_focus_drops_wrong_cam % 32 == 1:
                act = active.camera_number if active is not None else None
                self._log(
                    f"raw_focus_frame: src=cam{pipeline.camera_number} "
                    f"active={act} (mismatch, count="
                    f"{self._raw_focus_drops_wrong_cam}); ignored"
                )
            return
        self._raw_focus_forwarded += 1
        if self._raw_focus_forwarded == 1 or (
            self._raw_focus_forwarded % 16 == 0
        ):
            # Measured arrival fps over the logged window -- tells us whether the
            # real limiter is the PCB trigger (~4.8 fps @ 209 ms) or host-side frame
            # conflation (GUI thread too busy, dropping stale frames before us).
            now = time.monotonic()
            last = getattr(self, "_raw_focus_last_log_t", None)
            fps = ""
            if last is not None:
                dt = now - last
                if dt > 0:
                    fps = f" ~{16 / dt:.1f} fps"
            self._raw_focus_last_log_t = now
            self._log(
                f"raw_focus_frame -> focus panel #{self._raw_focus_forwarded} "
                f"(cam{pipeline.camera_number}){fps}"
            )
        self._focus_panel.receive_raw_frame(raw, ts_iso)
        if self._focus_panel.fine_view_enabled():
            try:
                self._update_fine_tracker(pipeline, raw)
            except Exception as exc:
                self._log(f"Fine view: tracker failed: {exc}")

    @Slot(QImage)
    def _on_preview_ready(self, qimg: QImage) -> None:
        if qimg.isNull():
            return
        if self._split_preview_on:
            acq = self.sender()
            if isinstance(acq, AcquisitionThread):
                self._split_preview_images[acq.camera_number] = qimg.copy()
                self._render_split_preview()
            return
        self._last_preview_qimage = qimg.copy()
        self._scale_preview_to_label()

    def _split_preview_max_edge(self) -> int:
        return max(160, PREVIEW_MAX_EDGE // max(1, self._split_preview_subsample))

    def _render_split_preview(self) -> None:
        if not self._split_preview_on:
            return
        panes: list[tuple[int, QImage]] = []
        for p in sorted(self._pipelines, key=lambda x: x.camera_number):
            q = self._split_preview_images.get(p.camera_number)
            if q is not None and not q.isNull():
                panes.append((p.camera_number, q))
        if not panes:
            self._preview_label.setText("Split preview: waiting for frames…")
            return

        tw = max(1, self._preview_label.width())
        th = max(1, self._preview_label.height())
        n = len(panes)
        pane_w = max(1, tw // n)
        combined = QImage(tw, th, QImage.Format.Format_RGB32)
        combined.fill(0x101010)
        painter = QPainter(combined)
        try:
            for i, (cam_no, qimg) in enumerate(panes):
                pix = QPixmap.fromImage(qimg)
                scaled = pix.scaled(
                    pane_w,
                    th,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                cropped = _crop_pixmap_center(scaled, pane_w, th)
                x = i * pane_w
                painter.drawPixmap(x, 0, cropped)
                painter.setPen(Qt.GlobalColor.white)
                painter.drawText(x + 6, 18, f"cam{cam_no}")
        finally:
            painter.end()
        self._last_preview_qimage = combined
        self._preview_label.setPixmap(QPixmap.fromImage(combined))

    @Slot(bool)
    def _on_split_preview_toggled(self, checked: bool) -> None:
        self._split_preview_on = bool(checked)
        if checked:
            if self._preview_zoom_on and hasattr(self, "_preview_zoom_btn"):
                self._preview_zoom_btn.setChecked(False)
            edge = self._split_preview_max_edge()
            in_focus = self._modes.is_focus_mode()
            for p in self._pipelines:
                p.set_preview(True)
                self._set_focus_tap(p, False)
                try:
                    p.set_preview_max_edge(edge)
                except Exception:
                    pass
            self._preview_title_lbl.setText("Preview — Split screen (all cameras)")
            self._log("preview = split screen (all cameras)")
            self._split_preview_images.clear()
            self._render_split_preview()
        else:
            for p in self._pipelines:
                try:
                    p.set_preview_max_edge(PREVIEW_MAX_EDGE)
                except Exception:
                    pass
            self._split_preview_images.clear()
            self._last_preview_qimage = None
            self._preview_title_lbl.setText(
                f"Preview ({MODE_BUTTON_LABELS[Mode.M0]} / {MODE_BUTTON_LABELS[Mode.M1]})"
            )
            for row in self._rows:
                if row.preview_radio.isChecked():
                    self._on_preview_selected(row.camera_number)
                    break
        self._sync_preview_sensor_fields()

    @Slot(bool)
    def _on_log_toggle_toggled(self, checked: bool) -> None:
        self._log_visible = bool(checked)
        self._log_toggle_btn.setText("Hide log" if checked else "Show log")
        if self._modes.is_focus_mode():
            return
        if checked:
            self._log_box.setVisible(True)
            if self._below_cam_split_saved:
                self._below_cam_splitter.setSizes(self._below_cam_split_saved)
        else:
            sizes = self._below_cam_splitter.sizes()
            if self._log_box.isVisible() and sizes and sizes[0] > 0:
                self._below_cam_split_saved = list(sizes)
            self._log_box.setVisible(False)
            total = max(sum(sizes), 1)
            self._below_cam_splitter.setSizes([0, total])
        if self._preview_zoom_on:
            self._push_preview_zoom_size()
        if self._split_preview_on:
            self._render_split_preview()
        else:
            self._scale_preview_to_label()

    def _scale_preview_to_label(self) -> None:
        """Fill the preview label (crop edges) so M0/M1 video isn't letterboxed."""
        if self._split_preview_on:
            self._render_split_preview()
            return
        qimg = self._last_preview_qimage
        if qimg is None or qimg.isNull():
            return
        tw = max(1, self._preview_label.width())
        th = max(1, self._preview_label.height())
        pix = QPixmap.fromImage(qimg)
        if self._preview_zoom_on:
            # 100% zoom: the camera already cropped a full-res region to
            # ~the window size, so show it 1:1 (no upscaling). The label
            # is centre-aligned and clips any overflow. If the crop is
            # slightly larger than the window (resize lag), centre-crop
            # to the window so we never upscale.
            scaled = _crop_pixmap_center(pix, min(tw, pix.width()), min(th, pix.height()))
            self._preview_label.setPixmap(scaled)
            return
        scaled = pix.scaled(
            tw,
            th,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled = _crop_pixmap_center(scaled, tw, th)
        self._preview_label.setPixmap(scaled)

    def _push_preview_zoom_size(self) -> None:
        """Tell every pipeline the current preview-window pixel size so the
        100% crop maps 1:1 to the display."""
        w = max(2, self._preview_label.width())
        h = max(2, self._preview_label.height())
        for p in self._pipelines:
            try:
                p.set_preview_zoom_size(w, h)
            except Exception:
                pass

    @Slot(bool)
    def _on_preview_zoom_toggled(self, checked: bool) -> None:
        """Switch the preview between fit-to-window and 100% full-res crop."""
        self._preview_zoom_on = bool(checked)
        self._preview_zoom_btn.setText("Zoom: 100%" if checked else "Zoom: Fit")
        if checked:
            self._push_preview_zoom_size()
        for p in self._pipelines:
            try:
                p.set_preview_zoom(checked)
            except Exception:
                pass
        # Clear the stale frame so we don't briefly show the old
        # (wrong-scale) image before the next preview frame arrives.
        self._last_preview_qimage = None

    @Slot(float)
    def _on_display_gain_changed(self, gain: float) -> None:
        """Push the preview-only brightness multiplier to every pipeline (M0/M1 preview
        and the M4 Fine tile, built on the acquisition thread) and to the focus panel
        (M4 Coarse preview and AF tile zoom, built on the GUI thread)."""
        self._preview_display_gain = float(gain)
        for p in self._pipelines:
            try:
                p.set_preview_gain(self._preview_display_gain)
            except Exception:
                pass
        self._focus_panel.set_display_gain(self._preview_display_gain)
        self._log(f"display gain {self._preview_display_gain:g}x (preview only)")

    @Slot(int, int)
    def _on_below_cam_split_moved(self, _pos: int, _index: int) -> None:
        if self._log_visible and self._log_box.isVisible():
            self._below_cam_split_saved = list(self._below_cam_splitter.sizes())
        if self._preview_zoom_on:
            self._push_preview_zoom_size()
        if self._split_preview_on:
            self._render_split_preview()
        else:
            self._scale_preview_to_label()

    def resizeEvent(self, event) -> None:  # noqa: N802 -- Qt naming
        super().resizeEvent(event)
        if self._preview_zoom_on:
            self._push_preview_zoom_size()
        self._scale_preview_to_label()

    # ── Sweep counter labels ────────────────────────────────────────────

    def _refresh_sweep_labels(self) -> None:
        # Same camera_number lookup pattern as _on_mode_clicked: rows and
        # pipelines aren't guaranteed to align by index when one camera
        # failed to come up.
        for p in self._pipelines:
            row = next(
                (r for r in self._rows if r.camera_number == p.camera_number),
                None,
            )
            if row is not None:
                row.update_sweep(p.sweep.peek_position())
        self._refresh_encoder_readout()

    def _refresh_encoder_readout(self) -> None:
        """Push the live encoder count to the M4/M5 focus panel (200 ms tick)."""
        if self._serial is None or not getattr(self._serial, "connected", False):
            text = "enc: --"
        elif not self._serial.encoder_present:
            text = f"enc: absent (cam{self._serial.selected_camera})"
        else:
            # Name the camera: three encoders share the bus and only the selected
            # one is live, so an unlabelled count is ambiguous on this rig.
            text = (
                f"enc: {int(self._serial.encoder_position)} "
                f"(cam{self._serial.selected_camera})"
            )
        self._focus_panel.set_encoder_text(text)

    # ── Log + close ─────────────────────────────────────────────────────

    def _install_button_press_logging(self) -> None:
        """Record every operator button press in the session-log file.

        Walks every :class:`QAbstractButton` in the window (push buttons,
        radio buttons, checkboxes) and connects its ``clicked`` signal to
        a logger. ``clicked`` only fires on genuine user interaction (not
        programmatic ``setChecked``/``setValue``), which is exactly the
        "button press" semantics we want. The button's visible text is
        used as the label, falling back to its object name or class.
        """
        seen: set[int] = set()
        for btn in self.findChildren(QAbstractButton):
            key = id(btn)
            if key in seen:
                continue
            seen.add(key)
            label = (btn.text() or "").replace("&", "").strip()
            if not label:
                label = btn.objectName() or btn.__class__.__name__
            # ``checkable`` buttons report their resulting state so the
            # log distinguishes "turned X on" from "turned X off".
            if btn.isCheckable():
                btn.clicked.connect(
                    lambda checked, lbl=label: self._log_button_press(
                        f"{lbl} ({'on' if checked else 'off'})"
                    )
                )
            else:
                btn.clicked.connect(
                    lambda _checked=False, lbl=label: self._log_button_press(lbl)
                )

    def _log_button_press(self, label: str) -> None:
        try:
            self._session_log.button(label)
        except Exception:
            pass

    def _log(self, msg: str) -> None:
        ts = _dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._log_view.appendPlainText(f"{ts}  {msg}")
        # Mirror to Python logging so the message also lands in stderr.
        # Useful when the GUI itself is sluggish or hung -- we still get
        # a wall-clock trail in the terminal of what the user saw.
        try:
            logging.getLogger("app.gui_log").info(msg)
        except Exception:
            pass
        # Mirror to the human-readable session-log file so it captures
        # everything that scrolled past the on-screen log window.
        try:
            self._session_log.write(msg)
        except Exception:
            pass

    def _clear_deferred_mode_schedule(self) -> None:
        if self._pending_mode_timer is not None:
            self._pending_mode_timer.stop()
            self._pending_mode_timer.deleteLater()
            self._pending_mode_timer = None
        self._pending_mode_apply = None

    def _schedule_deferred_mode_apply(self, mode: Mode) -> None:
        self._clear_deferred_mode_schedule()
        self._pending_mode_apply = mode
        self._pending_mode_timer = QTimer(self)
        self._pending_mode_timer.setSingleShot(True)
        self._pending_mode_timer.timeout.connect(self._on_deferred_mode_apply_timeout)
        self._pending_mode_timer.start(int(self.MODE_APPLY_AFTER_UART_S * 1000))
        # Keep this label short: the previous multi-word countdown blew up its
        # minimum width and shifted the whole mode row horizontally during M0↔M1
        # deferrals. Details go to the status bar + log instead.
        self._mode_status_label.setText("Pending…")
        self.statusBar().showMessage(
            f"{mode.name}: host mode applies after ~{self.MODE_APPLY_AFTER_UART_S:.0f}s "
            "(PCB finishing sweep + homing).",
            int(self.MODE_APPLY_AFTER_UART_S * 1000) + 2500,
        )

    def _on_deferred_mode_apply_timeout(self) -> None:
        mode = self._pending_mode_apply
        if mode is None:
            return
        self._pending_mode_timer = None
        self._pending_mode_apply = None
        self._log(
            f"deferred mode apply → {mode.name} "
            f"({self.MODE_APPLY_AFTER_UART_S:.0f}s elapsed)"
        )
        self._apply_mode_state_after_uart(mode)

    def _apply_mode_state_after_uart(self, mode: Mode) -> None:
        """Camera + GUI follow-through for a mode that was already commanded on UART.

        Keeps ``set_full_res_emission`` before ``ModeController.set_mode`` so
        AcquisitionThread doesn't classify in-flight frames as spurious drops.
        """
        wants_full_res = mode in (Mode.M0, Mode.M1, Mode.M6)
        for p in self._pipelines:
            try:
                p.set_full_res_emission(wants_full_res)
            except Exception as exc:
                self._log(
                    f"cam{p.camera_number}: set_full_res_emission "
                    f"({wants_full_res}) failed: {exc}"
                )

        # A mode change supersedes any in-flight deferred End-line.
        if self._pending_end_line:
            self._pending_end_line = False
            if self._pending_end_line_timer is not None:
                self._pending_end_line_timer.stop()
                self._pending_end_line_timer.deleteLater()
                self._pending_end_line_timer = None

        # Capture the mode we're leaving BEFORE set_mode overwrites it, so
        # the GPS diagnostic can tell a fresh capture start (non-recording
        # -> M0/M1) from an intra-capture flip (M1 <-> M0, both recording).
        prev_mode = self._modes.mode
        self._modes.set_mode(mode)
        self._mode_status_label.setText(f"Current: {mode_display_label(mode)}")
        # The mirrors re-home on every setMode, so the scene jumps: the tracker's
        # previous frame is no longer a valid reference for the next one.
        self._fine_tracker.reset()
        self._locked_fine_roi = None
        self._focus_panel.clear_fine_lock()
        # set_mode just cleared m4_recording (any mode change disarms it, including
        # re-entering M4), so the checkbox has to follow or it would lie.
        self._focus_panel.sync_m4_record_toggle(False)

        # Any mode transition (the PCB re-homes on setMode) aborts an
        # in-progress autofocus run.
        af = getattr(self, "_autofocus", None)
        if af is not None and af.is_running():
            af.stop("mode changed")

        for p in self._pipelines:
            p.sweep.reset()

        # ── line container lifecycle ──────────────────────────────
        # Whenever the local mode tracker transitions, every camera's
        # .bay14line container needs to follow:
        # * Entering M0/M1: close any prior line (defensive; should
        #   already be closed) and open a fresh one per pipeline so
        #   the next frames have somewhere to land.
        # * Leaving M0/M1 (M4 / UNKNOWN): close the open line so its
        #   index lands on disk before any further mode changes.
        # The CameraPipeline.open_line call resets the per-camera
        # sweep counter as part of its work, so the "sweep 1 frame 1"
        # invariant lines up with the start of each container.
        if mode in (Mode.M0, Mode.M1, Mode.M6):
            # Zero correlator decision counters only when STARTING a capture
            # (coming from a non-recording mode). A M1<->M0 flip keeps the
            # tally running so the whole capture is measured as one window.
            if prev_mode not in (Mode.M0, Mode.M1, Mode.M6):
                self._gps_correlator.reset_stats()
            self._open_lines_for_active_pipelines()
        else:
            self._close_all_lines()
            # Leaving recording entirely (to M4 / idle): dump the counters.
            # The shutdown / GPS-disconnect path also dumps, covering the
            # common "M1 -> M0 -> quit" workflow that never hits this branch.
            self._log_gps_correlator_stats()

        in_focus = mode in (Mode.M4, Mode.M5)
        if in_focus and self._active_pipeline() is None and self._pipelines:
            target = self._pipelines[0].camera_number
            for row in self._rows:
                if row.camera_number == target:
                    row.preview_radio.setChecked(True)
                    break

        for p in self._pipelines:
            row = next(
                (r for r in self._rows if r.camera_number == p.camera_number),
                None,
            )
            preview_on = bool(row and row.preview_radio.isChecked())
            self._set_focus_tap(p, in_focus and preview_on)

        self._view_stack.setCurrentIndex(1 if in_focus else 0)
        self._apply_m4_layout(in_focus)
        self._sync_preview_sensor_fields()
        active = self._active_pipeline()
        self._focus_panel.set_active_camera(
            active.camera_number if active is not None else None
        )

        if in_focus and self._serial.connected:
            if active is not None:
                try:
                    self._serial.select_camera(active.camera_number)
                    self._log(
                        f"sent $C{self._serial.pcb_slot_for(active.camera_number)}* "
                        f"(focus UI cam{active.camera_number}, serial {active.camera_serial})"
                    )
                except Exception as exc:
                    self._log(f"select_camera failed: {exc}")

        self._refresh_line_buttons_enabled()

    # Grace period between sending the "stop triggering" PCB command
    # and disarming the cameras at shutdown. Lets any sweep that was
    # in-flight when the user clicked Close run to completion and
    # gives the SDK callback time to deliver its trailing 1-3 frames
    # before we tear the data stream down. Without it the SDK
    # flushes mid-exposure / mid-transfer frames on disarm and they
    # are silently lost, which the operator perceives as "the last
    # sweep didn't fully save" -- and across multiple cameras it
    # also produces the small (1 frame) inter-camera count
    # discrepancies we used to see at end-of-session.
    SHUTDOWN_DRAIN_S = 5.0

    # After ``$M0*``/``$M1*`` when switching *between* M0 and M1, the PCB
    # finishes the current sweep and then runs its homing sequence
    # (~4 s) before the new mode actually starts emitting triggers.
    # We hold off applying the local ModeController + sweep reset
    # for a flat 10 s, which comfortably covers worst-case sweep
    # tail + homing time so the host doesn't reset sweep numbering
    # while the PCB is still finishing the previous mode.
    MODE_APPLY_AFTER_UART_S = 10.0

    # M3 End-line grace: after ``!`` wait for ``Swp#`` before dropping gate.
    END_LINE_SWEEP_GRACE_S = 8.0

    def closeEvent(self, event) -> None:  # noqa: N802 -- Qt naming
        self._log("Shutting down…")
        self._clear_deferred_mode_schedule()
        self._preflight.cancel()   # before the drain loop below can tick it into a dialog
        # Before anything touches the hardware: the live w10 thread and the AF engine's
        # scorer thread are non-daemon; leaving them to the interpreter's atexit hook
        # would put an unjoined thread behind the grabber teardown that must not be
        # interrupted. Detach the live scorer from the pipelines first so no new offer
        # lands while it drains (at most one ~150 ms score).
        for p in self._pipelines:
            p.acq.set_w10_live(None)
        self._w10_live.shutdown()
        if self._autofocus is not None:
            self._autofocus.shutdown()
        if self._pending_end_line_timer is not None:
            self._pending_end_line_timer.stop()
            self._pending_end_line_timer.deleteLater()
            self._pending_end_line_timer = None
        self._pending_end_line = False

        # Stop-boundary fix: tell the PCB to stop emitting triggers
        # BEFORE we touch the cameras, then wait SHUTDOWN_DRAIN_S so
        # any in-flight sweep can complete cleanly. We send $M0*
        # even when our local mode tracker says we're already in M0
        # -- it's idempotent on the PCB side and protects us if the
        # local state ever drifted from the hardware.
        if self._serial.connected:
            try:
                self._serial.send_mode(0)
                self._log("sent $M0* (shutdown stop)")
            except Exception as exc:
                self._log(f"shutdown $M0* failed: {exc}")
        else:
            self._log("ClearCore not connected; skipping $M0* on shutdown")

        # Local mode follows so any AcquisitionThread / writer-gate
        # logic that watches ``self._modes`` sees the change
        # immediately and stops submitting new write jobs.
        try:
            self._modes.set_mode(Mode.M0)
        except Exception:
            pass

        # Drain. Stay responsive (process Qt events) so the log
        # line actually paints and any final SDK callbacks deliver
        # into the writer pool. Sleep in small slices so the user
        # doesn't see a frozen "Shutting down…" window.
        self._log(
            f"draining {self.SHUTDOWN_DRAIN_S:.0f}s for in-flight frames "
            f"before disarming…"
        )
        deadline = time.monotonic() + self.SHUTDOWN_DRAIN_S
        while time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.05)

        # If the user closes the window while bring-up is still running,
        # wait for the worker to finish so we don't double-destroy IAs.
        if self._bringup_worker is not None and self._bringup_worker.isRunning():
            self._bringup_worker.wait(60_000)
        try:
            for p in self._pipelines:
                p.stop()
            self._manager.close_all()
        except Exception as exc:
            self._log(f"shutdown error: {exc}")
        try:
            if self._serial.connected:
                self._serial.disconnect_port()
        except Exception:
            pass
        # Tear down the SBF reader thread + port before Qt exits so a
        # dangling pyserial handle doesn't keep the COM busy across
        # app restarts.
        try:
            if self._gps_receiver.connected:
                self._gps_receiver.disconnect_port()
        except Exception:
            pass
        # Flush + footer the human-readable session log last so it
        # captures the full shutdown sequence above.
        try:
            self._session_log.close()
        except Exception:
            pass
        super().closeEvent(event)


# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Unified Iron 661 capture + focus app",
    )
    parser.add_argument(
        "--cti",
        default=None,
        help="Path to KAYA GenTL producer (.cti). Auto-detected from "
        "GENICAM_GENTL64_PATH if omitted.",
    )
    parser.add_argument(
        "--port",
        default=None,
        help="ClearCore COM port. Auto-detected by VID/PID if omitted.",
    )
    parser.add_argument(
        "--gps-port",
        default=None,
        help=(
            "GNSS NMEA COM port (115200 8N1). Defaults to COM3, the RS232 "
            "SBF-to-NMEA bridge, falling back to a Septentrio USB port."
        ),
    )
    parser.add_argument(
        "--log-level", default="INFO", help="Python logging level (DEBUG/INFO/WARNING)",
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="No hardware: simulated cameras (focus-dependent ROI blur) and a simulated "
             "ClearCore (acks, banners, encoders, motors). No GPS.",
    )
    parser.add_argument(
        "--screen", type=int, default=None,
        help="Monitor index to place the window on (QApplication.screens() order).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    cti = args.cti or find_cti_from_env() or ("<demo>" if args.demo else None)
    if not cti:
        print(
            "ERROR: no GenTL producer (.cti) found.\n"
            "Pass --cti or set GENICAM_GENTL64_PATH.",
            file=sys.stderr,
        )
        return 2

    app = QApplication(sys.argv)
    win = MainWindow(
        cti_path=cti,
        clearcore_port=args.port,
        gps_port=args.gps_port,
        demo=args.demo,
    )
    win.show()
    if args.screen is not None:
        # Recenter the window onto the requested monitor after show() (geometry
        # isn't valid until then). Keeps it a normal resizable window -- the
        # operator can maximize; we just pick the screen.
        screens = app.screens()
        if 0 <= args.screen < len(screens):
            geo = win.frameGeometry()
            geo.moveCenter(screens[args.screen].availableGeometry().center())
            win.move(geo.topLeft())
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    # Required on Windows for multiprocessing.spawn-style child
    # processes (the per-camera writer subprocesses we use for full-res
    # TIFF writes). Without this, a frozen / py-installer build would
    # re-enter main() on every spawned child.
    import multiprocessing as _mp
    _mp.freeze_support()
    sys.exit(main())
