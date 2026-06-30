r"""Parallax Export Studio -- combined GPS correlation + Bay14 export.

A single PySide6 desktop app that merges the old **GPS Correlator** and
**Bay14Reader** workflows into one pass:

1. Point it at up to three camera folders of ``.bay14line`` containers
   (one folder per camera).
2. Optionally point it at an external Septentrio SBF ExtEvent CSV. The
   line files normally already carry the GPS event coordinates the Iron
   661 embeds at capture time, so the external file is only needed when
   you want to (re)correlate a camera's coordinates from it. Tick
   "External GPS" per camera to use the file instead of the embedded
   coordinates.
3. Hit **Load / Correlate** to gather every frame's position (embedded or
   correlated) and plot them on the map, coloured per (camera, line) --
   just like the GPS Correlator.
4. Optionally hit **Add Attitude** to compute each frame's camera
   Yaw/Pitch/Roll and draw the look-azimuth on the map.
5. Pick an output folder + format (JPEG / raw-Bayer TIFF / demosaiced-RGB
   TIFF, like Bay14Reader) and hit **Export**. Every exported image gets
   GPS coordinates and (if computed) Yaw/Pitch/Roll written into its EXIF.

The source ``.bay14line`` files are never modified.

Run from source::

    py -3 tools/gps_export_studio_gui.py
    py -3 tools/gps_export_studio_gui.py E:\camA E:\camB E:\camC --out F:\export
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSlider,
    QSpinBox,
    QTabBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import (  # noqa: E402
    FigureCanvasQTAgg as FigureCanvas,
    NavigationToolbar2QT as NavigationToolbar,
)
from matplotlib.collections import LineCollection  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from bay14_export_core import (  # noqa: E402
    encode_jpeg,
    encode_tiff_rgb16,
    iter_line_frames,
    write_tiff_bayer_u16,
)
from bay14_reader_gui import (  # noqa: E402
    build_export_path,
    normalize_fs_path,
    resolve_cam_label,
)
from gps_export_studio_core import (  # noqa: E402
    CAMERA_KIND_LABELS,
    KIND_OBLIQUE_NOSE,
    KIND_OBLIQUE_TAIL,
    KIND_VERTICAL,
    AttitudeStats,
    CameraInput,
    CorrelationParams,
    FrameStore,
    compute_attitude,
    count_camera_frames,
    kind_is_oblique,
    load_embedded_camera,
    load_external_camera,
)
from capture.gps_fix import GpsFix  # noqa: E402
from capture.tiff_writer import (  # noqa: E402
    detect_exiftool,
    exiftool_geotag,
    exiftool_write_camera_attitude,
)

log = logging.getLogger("gps_export_studio")

# Low-res ("_S") export width, in pixels. The full frame is ~13400 px wide;
# the low-res copy keeps the aspect ratio and is written to a sibling
# ``{serial}_S`` folder in the same format with the same EXIF.
LOW_RES_WIDTH = 675


def build_date() -> str:
    """Human-readable build/last-modified date (YYYY-MM-DD)."""
    try:
        target = Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__).resolve()
        return datetime.fromtimestamp(target.stat().st_mtime).strftime("%Y-%m-%d")
    except OSError:
        return "unknown"


# ─── Workers ─────────────────────────────────────────────────────────────────


class LoadWorker(QThread):
    """Gather per-frame GPS for every camera (embedded or correlated)."""

    log_line = Signal(str)
    failed = Signal(str)
    finished_ok = Signal(object)  # FrameStore

    def __init__(
        self,
        cameras: list[CameraInput],
        sbf_csv: Optional[Path],
        params: CorrelationParams,
        *,
        use_external: bool = False,
        pattern: str = "*.bay14line",
        recursive: bool = False,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._cameras = cameras
        self._sbf_csv = sbf_csv
        self._params = params
        self._use_external = bool(use_external)
        self._pattern = pattern
        self._recursive = recursive
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            store = FrameStore()
            for cam in self._cameras:
                if self._cancel.is_set():
                    break
                if self._use_external:
                    if self._sbf_csv is None:
                        self.log_line.emit(
                            f"Cam {cam.index + 1}: external requested but no SBF "
                            "file -- skipping."
                        )
                        continue
                    recs = load_external_camera(
                        cam, self._sbf_csv, self._params,
                        log_cb=self.log_line.emit,
                    )
                else:
                    recs = load_embedded_camera(
                        cam,
                        pattern=self._pattern,
                        recursive=self._recursive,
                        log_cb=self.log_line.emit,
                        cancel_cb=self._cancel.is_set,
                    )
                for rec in recs:
                    store.add(rec)
            self.finished_ok.emit(store)
        except Exception as exc:  # pragma: no cover (defensive)
            log.exception("load worker failed")
            self.failed.emit(str(exc))


class ExportWorker(QThread):
    """Encode every frame, injecting each record's GPS + attitude into EXIF.

    Mirrors Bay14Reader's exporter but stamps the studio's per-frame GPS
    (embedded or correlated) and Yaw/Pitch/Roll into the output. TIFF GPS
    + attitude flow through the existing encoders via the injected
    metadata; JPEG GPS + attitude are written with ExifTool post-encode.
    """

    progress = Signal(int, int, str)  # done, total, message
    log_line = Signal(str)
    failed = Signal(str)
    finished_ok = Signal(int, int, bool)  # done, total, cancelled

    def __init__(
        self,
        cameras: list[CameraInput],
        store: FrameStore,
        out_dir: Path,
        export_kind: str,
        jpeg_quality: int,
        *,
        export_low_res: bool = False,
        low_res_width: int = LOW_RES_WIDTH,
        pattern: str = "*.bay14line",
        recursive: bool = False,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._cameras = cameras
        self._store = store
        self._out_dir = Path(out_dir)
        self._export_kind = export_kind
        self._jpeg_quality = int(jpeg_quality)
        self._export_low_res = bool(export_low_res)
        self._low_res_width = int(low_res_width)
        self._pattern = pattern
        self._recursive = recursive
        self._cancel = threading.Event()
        self._exiftool = detect_exiftool()

    def cancel(self) -> None:
        self._cancel.set()

    def _inject(self, merged_meta: dict, line_path: Path) -> tuple[Optional[GpsFix], Optional[dict]]:
        """Overlay the store's GPS + attitude onto *merged_meta* in place.

        Returns the ``(fix, attitude_dict)`` for the JPEG EXIF post-pass.
        """
        rec = self._store.lookup(
            line_path, merged_meta.get("sweep", 0), merged_meta.get("frame_in_sweep", 0),
        )
        fix: Optional[GpsFix] = None
        attitude: Optional[dict] = None
        if rec is not None:
            if rec.has_position:
                fix = rec.to_gps_fix()
                merged_meta["gps"] = fix.to_dict()
            attitude = rec.attitude_dict()
            if attitude is not None:
                merged_meta["attitude"] = attitude
        return fix, attitude

    def _encode_one(
        self,
        out_path: Path,
        arr,
        merged_meta: dict,
        cam: CameraInput,
        fix: Optional[GpsFix],
        attitude: Optional[dict],
        *,
        resize_width: Optional[int],
    ) -> None:
        """Encode one frame to *out_path* in the selected format.

        TIFF GPS + attitude flow through the encoders via *merged_meta*;
        JPEG GPS + attitude are stamped with ExifTool afterwards. The same
        *merged_meta* / *fix* / *attitude* are used for the full-res and the
        low-res copy, so both carry identical EXIF.
        """
        if self._export_kind == "jpeg":
            encode_jpeg(
                out_path, arr, merged_meta,
                quality=self._jpeg_quality, flip_lr=cam.flip_lr,
                resize_width=resize_width,
            )
            self._write_jpeg_exif(out_path, fix, attitude)
        elif self._export_kind == "tiff_rgb":
            encode_tiff_rgb16(
                out_path, arr, merged_meta, flip_lr=cam.flip_lr,
                resize_width=resize_width,
            )
        else:
            write_tiff_bayer_u16(
                out_path, arr, merged_meta,
                scale_14_to_16=True, flip_lr=cam.flip_lr,
                resize_width=resize_width,
            )

    def _write_jpeg_exif(self, out_path: Path, fix: Optional[GpsFix], attitude: Optional[dict]) -> None:
        if self._exiftool is None:
            return
        try:
            if fix is not None and fix.has_position:
                exiftool_geotag(out_path, fix, exiftool_exe=self._exiftool)
            if attitude is not None:
                exiftool_write_camera_attitude(
                    out_path, attitude, exiftool_exe=self._exiftool,
                )
        except Exception as exc:
            self.log_line.emit(f"  JPEG EXIF failed for {out_path.name}: {exc}")

    def run(self) -> None:
        cancelled = False
        done = 0
        total = 0
        if self._export_kind == "jpeg" and self._exiftool is None:
            self.log_line.emit(
                "WARNING: ExifTool not found -- JPEG exports will have NO "
                "GPS/attitude EXIF. (TIFF GPS still works; TIFF attitude needs "
                "ExifTool too.)"
            )
        try:
            from bay14_export_core import discover_lines

            jobs: list[tuple[CameraInput, Path]] = []
            for cam in self._cameras:
                for line_path in discover_lines(
                    cam.directory, self._pattern, recursive=self._recursive,
                ):
                    jobs.append((cam, line_path))
                total += count_camera_frames(
                    cam, pattern=self._pattern, recursive=self._recursive,
                )

            if total <= 0:
                self.failed.emit("No frames to export from the selected camera folders.")
                self.finished_ok.emit(0, 0, False)
                return

            for cam, line_path in jobs:
                if self._cancel.is_set():
                    cancelled = True
                    break
                cam_label = resolve_cam_label(line_path, cam.directory)
                for merged_meta, arr in iter_line_frames(
                    line_path, recovery_notice=self.log_line.emit,
                ):
                    if self._cancel.is_set():
                        cancelled = True
                        break
                    fix, attitude = self._inject(merged_meta, line_path)
                    ext = "jpg" if self._export_kind == "jpeg" else "tif"
                    out_path = build_export_path(
                        self._out_dir, cam_label, line_path, merged_meta, ext,
                    )
                    self._encode_one(
                        out_path, arr, merged_meta, cam, fix, attitude,
                        resize_width=None,
                    )
                    if self._export_low_res:
                        low_path = build_export_path(
                            self._out_dir, f"{cam_label}_S", line_path,
                            merged_meta, ext,
                        )
                        self._encode_one(
                            low_path, arr, merged_meta, cam, fix, attitude,
                            resize_width=self._low_res_width,
                        )
                    done += 1
                    self.progress.emit(done, total, out_path.name)
                if cancelled:
                    break

            self.progress.emit(done, total, "")
            self.finished_ok.emit(done, total, cancelled)
        except Exception as exc:
            log.exception("export worker failed")
            self.failed.emit(str(exc))
            self.finished_ok.emit(done, total, cancelled)


# ─── Window ──────────────────────────────────────────────────────────────────


class StudioWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._build_date = build_date()
        self.setWindowTitle(f"Parallax Export Studio  (build {self._build_date})")
        self.resize(1180, 900)

        self._store: Optional[FrameStore] = None
        self._attitude_done = False
        self._map_cam_index: Optional[int] = None
        self._load_worker: Optional[LoadWorker] = None
        self._export_worker: Optional[ExportWorker] = None

        self._cam_edits: list[QLineEdit] = []
        self._cam_kinds: list[QComboBox] = []

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        root.addWidget(self._build_sources_group())
        self._sbf_group = self._build_sbf_group()
        # Only relevant when correlating from the external file -- hidden
        # until "External GPS" is ticked, so the map gets the space.
        self._sbf_group.setVisible(False)
        self._external_cb.toggled.connect(self._sbf_group.setVisible)
        root.addWidget(self._sbf_group)
        self._params_group = self._build_params_group()
        # Hidden by default -- correlation params are rarely touched, so we
        # keep them tucked away behind the "Correlation parameters" toggle
        # and give the freed vertical space to the map.
        self._params_group.setVisible(False)
        root.addLayout(self._build_controls_row())
        root.addWidget(self._params_group)
        root.addWidget(self._build_map(), 1)
        root.addWidget(self._build_output_group())
        root.addLayout(self._build_export_row())

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        root.addWidget(self._progress)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(150)
        root.addWidget(self._log)

        backend = "exiftool" if detect_exiftool() is not None else "tifffile / none (JPEG EXIF disabled)"
        self.statusBar().showMessage(f"Ready. EXIF backend: {backend}")
        build_lab = QLabel(f"Build: {self._build_date}")
        build_lab.setStyleSheet("color: gray;")
        self.statusBar().addPermanentWidget(build_lab)

    # ── UI builders ───────────────────────────────────────────────────────

    def _build_sources_group(self) -> QGroupBox:
        group = QGroupBox("Camera source folders (each scanned for .bay14line)")
        layout = QVBoxLayout(group)
        # Default kinds: a typical 3-camera rig is nose-oblique / tail-oblique
        # / vertical. The operator can change any of them.
        default_kinds = [KIND_OBLIQUE_NOSE, KIND_OBLIQUE_TAIL, KIND_VERTICAL]
        for i in range(3):
            row = QHBoxLayout()
            lab = QLabel(f"Cam {i + 1}:")
            lab.setMinimumWidth(52)
            edit = QLineEdit()
            edit.setPlaceholderText(f"Optional path to camera {i + 1} folder...")
            browse = QPushButton("Browse...")
            kind_combo = QComboBox()
            for kind, label in CAMERA_KIND_LABELS.items():
                kind_combo.addItem(label, userData=kind)
            kind_combo.setCurrentIndex(
                kind_combo.findData(default_kinds[i])
            )
            kind_combo.setToolTip(
                "Optical path of this camera. Sets the attitude model and "
                "(for oblique cameras) the default horizontal flip on export."
            )
            idx = i

            def _browse_slot(_checked: bool = False, j: int = idx) -> None:
                d = QFileDialog.getExistingDirectory(self, f"Cam {j + 1} folder")
                if d:
                    self._cam_edits[j].setText(d)

            browse.clicked.connect(_browse_slot)
            row.addWidget(lab)
            row.addWidget(edit, stretch=1)
            row.addWidget(browse)
            row.addWidget(QLabel("Type:"))
            row.addWidget(kind_combo)
            layout.addLayout(row)
            self._cam_edits.append(edit)
            self._cam_kinds.append(kind_combo)

        opt_row = QHBoxLayout()
        opt_row.addWidget(QLabel("Glob pattern:"))
        self._pattern_edit = QLineEdit("*.bay14line")
        opt_row.addWidget(self._pattern_edit, stretch=1)
        self._recursive_cb = QCheckBox("Include subfolders")
        opt_row.addWidget(self._recursive_cb)
        layout.addLayout(opt_row)

        global_row = QHBoxLayout()
        self._external_cb = QCheckBox("External GPS (correlate all cameras from the SBF file)")
        self._external_cb.setToolTip(
            "When ticked, every camera's coordinates are correlated from the "
            "external SBF file below instead of using the GPS already embedded "
            "in the line files."
        )
        self._dont_flip_oblique_cb = QCheckBox("Don't flip oblique")
        self._dont_flip_oblique_cb.setToolTip(
            "Oblique cameras are mirrored horizontally on export by default "
            "(their optical path reflects the image once). Tick this to leave "
            "them un-flipped. Vertical cameras are never flipped."
        )
        global_row.addWidget(self._external_cb)
        global_row.addStretch(1)
        global_row.addWidget(self._dont_flip_oblique_cb)
        layout.addLayout(global_row)
        return group

    def _build_sbf_group(self) -> QGroupBox:
        group = QGroupBox("External SBF event coordinates file (optional)")
        row = QHBoxLayout(group)
        self._sbf_edit = QLineEdit()
        self._sbf_edit.setPlaceholderText(
            "Pick the Septentrio SBF ExtEvent CSV (*.txt) -- only needed for "
            "cameras with 'External GPS' ticked"
        )
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse_sbf)
        row.addWidget(self._sbf_edit, stretch=1)
        row.addWidget(browse)
        return group

    def _build_params_group(self) -> QGroupBox:
        group = QGroupBox("External correlation parameters (used only with 'External GPS')")
        form = QFormLayout(group)

        self._fps_spin = QSpinBox()
        self._fps_spin.setRange(1, 1000)
        self._fps_spin.setValue(13)
        form.addRow("Frames per sweep:", self._fps_spin)

        self._gap_spin = QDoubleSpinBox()
        self._gap_spin.setRange(1.0, 600.0)
        self._gap_spin.setSingleStep(1.0)
        self._gap_spin.setValue(80.0)
        self._gap_spin.setSuffix(" s")
        form.addRow("Cluster gap:", self._gap_spin)

        self._minsize_spin = QSpinBox()
        self._minsize_spin.setRange(1, 1_000_000)
        self._minsize_spin.setValue(50)
        form.addRow("Min cluster size:", self._minsize_spin)

        self._leap_spin = QSpinBox()
        self._leap_spin.setRange(0, 60)
        self._leap_spin.setValue(18)
        form.addRow("Leap seconds (GPS-UTC):", self._leap_spin)

        self._max_offset_spin = QDoubleSpinBox()
        self._max_offset_spin.setRange(1.0, 86_400.0)
        self._max_offset_spin.setSingleStep(30.0)
        self._max_offset_spin.setDecimals(0)
        self._max_offset_spin.setValue(900.0)
        self._max_offset_spin.setSuffix(" s")
        form.addRow("Max flight-clock offset:", self._max_offset_spin)

        self._max_rmse_spin = QDoubleSpinBox()
        self._max_rmse_spin.setRange(0.001, 3600.0)
        self._max_rmse_spin.setSingleStep(0.1)
        self._max_rmse_spin.setDecimals(3)
        self._max_rmse_spin.setValue(1.0)
        self._max_rmse_spin.setSuffix(" s")
        form.addRow("Max RMSE per match:", self._max_rmse_spin)

        self._max_missing_spin = QSpinBox()
        self._max_missing_spin.setRange(0, 10_000)
        self._max_missing_spin.setValue(10)
        form.addRow("Max missing events:", self._max_missing_spin)
        return group

    def _build_controls_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._load_btn = QPushButton("Load / Correlate")
        self._load_btn.clicked.connect(self._on_load)
        self._attitude_btn = QPushButton("Add Attitude")
        self._attitude_btn.setEnabled(False)
        self._attitude_btn.setToolTip(
            "Compute per-frame camera Yaw/Pitch/Roll and draw the look-azimuth "
            "on the map. The angles are written into EXIF on export."
        )
        self._attitude_btn.clicked.connect(self._on_add_attitude)
        self._params_toggle_btn = QPushButton("Correlation parameters \u25be")
        self._params_toggle_btn.setCheckable(True)
        self._params_toggle_btn.setToolTip(
            "Show/hide the external-SBF correlation parameters (rarely changed)."
        )
        self._params_toggle_btn.toggled.connect(self._on_toggle_params)
        row.addWidget(self._load_btn)
        row.addWidget(self._attitude_btn)
        row.addWidget(self._params_toggle_btn)
        row.addStretch(1)
        return row

    @Slot(bool)
    def _on_toggle_params(self, shown: bool) -> None:
        self._params_group.setVisible(shown)
        self._params_toggle_btn.setText(
            "Correlation parameters \u25b4" if shown
            else "Correlation parameters \u25be"
        )

    def _build_map(self) -> QWidget:
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        self._map_cam_tabs = QTabBar()
        self._map_cam_tabs.setVisible(False)
        self._map_cam_tabs.setExpanding(False)
        self._map_cam_tabs.currentChanged.connect(self._on_map_cam_tab_changed)
        v.addWidget(self._map_cam_tabs)
        self._figure = Figure(figsize=(8, 5))
        self._figure.subplots_adjust(left=0.08, right=0.78, top=0.94, bottom=0.10)
        self._ax = self._figure.add_subplot(111)
        self._ax.set_xlabel("Longitude (\u00b0E)")
        self._ax.set_ylabel("Latitude (\u00b0N)")
        self._ax.set_title("Load / Correlate to populate the map.")
        self._ax.grid(True, alpha=0.3)
        self._canvas = FigureCanvas(self._figure)
        self._toolbar = NavigationToolbar(self._canvas, container)
        v.addWidget(self._toolbar)
        v.addWidget(self._canvas, 1)
        return container

    def _build_output_group(self) -> QGroupBox:
        group = QGroupBox("Output")
        form = QFormLayout(group)

        out_row = QHBoxLayout()
        self._out_edit = QLineEdit()
        self._out_edit.setPlaceholderText("Destination folder...")
        out_btn = QPushButton("Browse...")
        out_btn.clicked.connect(self._browse_out)
        out_row.addWidget(self._out_edit, stretch=1)
        out_row.addWidget(out_btn)
        form.addRow("Folder:", out_row)

        fmt_row = QHBoxLayout()
        self._fmt_tiff = QRadioButton("TIFF")
        self._fmt_jpeg = QRadioButton("JPEG")
        self._fmt_tiff.setChecked(True)
        for rb in (self._fmt_tiff, self._fmt_jpeg):
            rb.setAutoExclusive(False)
        fmt_row.addWidget(self._fmt_tiff)
        fmt_row.addWidget(self._fmt_jpeg)
        fmt_row.addStretch()
        form.addRow("Format:", fmt_row)
        self._format_group = QButtonGroup(self)
        self._format_group.setExclusive(True)
        self._format_group.addButton(self._fmt_tiff)
        self._format_group.addButton(self._fmt_jpeg)

        self._tiff_raw = QRadioButton("Grayscale (~243 MB/frame)")
        self._tiff_rgb = QRadioButton("RGB (~731 MB/frame)")
        self._tiff_rgb.setChecked(True)
        for rb in (self._tiff_raw, self._tiff_rgb):
            rb.setAutoExclusive(False)
        tiff_box = QVBoxLayout()
        tiff_box.addWidget(self._tiff_raw)
        tiff_box.addWidget(self._tiff_rgb)
        form.addRow("TIFF mode:", tiff_box)
        self._tiff_mode_group = QButtonGroup(self)
        self._tiff_mode_group.setExclusive(True)
        self._tiff_mode_group.addButton(self._tiff_raw)
        self._tiff_mode_group.addButton(self._tiff_rgb)

        jq_row = QHBoxLayout()
        self._quality_slider = QSlider(Qt.Orientation.Horizontal)
        self._quality_slider.setRange(1, 100)
        self._quality_slider.setValue(85)
        self._quality_lab = QLabel("85")
        self._quality_slider.valueChanged.connect(
            lambda v: self._quality_lab.setText(str(v)),
        )
        jq_row.addWidget(QLabel("JPEG quality:"))
        jq_row.addWidget(self._quality_slider, stretch=1)
        jq_row.addWidget(self._quality_lab)
        form.addRow("", jq_row)

        self._low_res_cb = QCheckBox(
            f"Export low res images ({LOW_RES_WIDTH}px wide, to '<serial>_S' folders)"
        )
        self._low_res_cb.setToolTip(
            "Also export a downscaled copy of every frame (same aspect ratio, "
            f"{LOW_RES_WIDTH}px wide) into a sibling folder named '<serial>_S'. "
            "Same format and same EXIF (GPS + attitude) as the full-size image."
        )
        form.addRow("", self._low_res_cb)

        self._format_group.buttonClicked.connect(self._update_format_controls)
        self._update_format_controls()
        return group

    def _build_export_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._export_btn = QPushButton("Export")
        self._export_btn.clicked.connect(self._on_export)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._on_cancel)
        row.addWidget(self._export_btn)
        row.addWidget(self._cancel_btn)
        row.addStretch(1)
        return row

    # ── Input collection ──────────────────────────────────────────────────

    def _cameras(self) -> list[CameraInput]:
        cams: list[CameraInput] = []
        dont_flip = self._dont_flip_oblique_cb.isChecked()
        for i, edit in enumerate(self._cam_edits):
            raw = edit.text().strip()
            if not raw:
                continue
            kind = self._cam_kinds[i].currentData() or KIND_VERTICAL
            flip = kind_is_oblique(kind) and not dont_flip
            cams.append(CameraInput(
                index=i,
                directory=normalize_fs_path(raw),
                kind=kind,
                flip_lr=flip,
            ))
        return cams

    def _params(self) -> CorrelationParams:
        return CorrelationParams(
            frames_per_sweep=self._fps_spin.value(),
            cluster_gap_s=self._gap_spin.value(),
            cluster_min_size=self._minsize_spin.value(),
            leap_seconds=self._leap_spin.value(),
            max_flight_offset_s=self._max_offset_spin.value(),
            max_rmse_s=self._max_rmse_spin.value(),
            max_missing_events=self._max_missing_spin.value(),
            recursive=self._recursive_cb.isChecked(),
            allow_reuse=True,
        )

    def _pattern(self) -> str:
        p = self._pattern_edit.text().strip()
        return p if p else "*.bay14line"

    def _export_kind(self) -> str:
        if self._fmt_jpeg.isChecked():
            return "jpeg"
        if self._tiff_rgb.isChecked():
            return "tiff_rgb"
        return "tiff_raw"

    # ── Browse slots ──────────────────────────────────────────────────────

    @Slot()
    def _browse_sbf(self) -> None:
        start = self._sbf_edit.text().strip() or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "SBF ExtEvent CSV", start,
            "SBF logs (*.txt *.csv);;All files (*)",
        )
        if path:
            self._sbf_edit.setText(path)

    @Slot()
    def _browse_out(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Output folder")
        if d:
            self._out_edit.setText(d)

    @Slot()
    def _update_format_controls(self) -> None:
        tiff = self._fmt_tiff.isChecked()
        self._tiff_raw.setEnabled(tiff)
        self._tiff_rgb.setEnabled(tiff)
        self._quality_slider.setEnabled(not tiff)
        self._quality_lab.setEnabled(not tiff)

    # ── Load / Correlate ──────────────────────────────────────────────────

    @Slot()
    def _on_load(self) -> None:
        cams = self._cameras()
        if not cams:
            QMessageBox.warning(self, "No cameras", "Enter at least one camera folder.")
            return
        for cam in cams:
            if not cam.directory.is_dir():
                QMessageBox.warning(
                    self, "Bad folder",
                    f"Cam {cam.index + 1} is not a folder:\n  {cam.directory}",
                )
                return
        sbf_raw = self._sbf_edit.text().strip()
        sbf_csv: Optional[Path] = Path(sbf_raw) if sbf_raw else None
        use_external = self._external_cb.isChecked()
        if use_external:
            if sbf_csv is None or not sbf_csv.is_file():
                QMessageBox.warning(
                    self, "No SBF file",
                    "'External GPS' is ticked, but the external SBF file is "
                    "missing or invalid.",
                )
                return

        self._log.clear()
        self._append_log("Loading...")
        self._store = None
        self._attitude_done = False
        self._refresh_map_camera_tabs()
        self._update_plot()
        self._set_busy(True)

        self._load_worker = LoadWorker(
            cams, sbf_csv, self._params(),
            use_external=use_external,
            pattern=self._pattern(),
            recursive=self._recursive_cb.isChecked(),
            parent=self,
        )
        self._load_worker.log_line.connect(self._append_log)
        self._load_worker.failed.connect(self._on_load_failed)
        self._load_worker.finished_ok.connect(self._on_load_done)
        self._load_worker.start()

    @Slot(object)
    def _on_load_done(self, store: FrameStore) -> None:
        self._store = store
        self._set_busy(False)
        positioned = store.positioned()
        self._append_log(
            f"Loaded {len(store.records)} frame(s); {len(positioned)} with a "
            f"GPS position across {len(store.tracks())} track(s)."
        )
        self._attitude_btn.setEnabled(len(positioned) > 0)
        self._refresh_map_camera_tabs()
        self._update_plot()
        self.statusBar().showMessage(
            "Loaded. Inspect the map, optionally add attitude, then export."
        )

    @Slot(str)
    def _on_load_failed(self, msg: str) -> None:
        self._set_busy(False)
        self._append_log(f"ERROR: {msg}")
        QMessageBox.critical(self, "Load failed", msg)

    # ── Attitude ──────────────────────────────────────────────────────────

    @Slot()
    def _on_add_attitude(self) -> None:
        if self._store is None:
            return
        self.statusBar().showMessage("Computing attitude...")
        try:
            stats: AttitudeStats = compute_attitude(self._store)
        except Exception as exc:
            log.exception("attitude failed")
            self._append_log(f"Attitude FAILED: {exc}")
            QMessageBox.critical(self, "Attitude failed", str(exc))
            return
        self._attitude_done = True
        self._append_log(
            f"Attitude: {stats.points} frame(s) "
            f"({stats.with_heading} with heading, {stats.without_heading} "
            f"without); {stats.no_gps} no-fix, {stats.unknown_serial} "
            "unknown-serial. Yaw/Pitch/Roll will be written to EXIF on export."
        )
        self._update_plot()
        self.statusBar().showMessage(
            "Attitude added. Direction lines show the camera look-azimuth (yaw)."
        )

    # ── Export ────────────────────────────────────────────────────────────

    @Slot()
    def _on_export(self) -> None:
        cams = self._cameras()
        if not cams:
            QMessageBox.warning(self, "No cameras", "Enter at least one camera folder.")
            return
        out_raw = self._out_edit.text().strip()
        if not out_raw:
            QMessageBox.warning(self, "No output folder", "Choose a destination directory.")
            return
        out_dir = Path(out_raw)
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "Output folder", str(exc))
            return
        if self._store is None:
            choice = QMessageBox.question(
                self, "No coordinates loaded",
                "You haven't run 'Load / Correlate' yet, so exports will carry "
                "only whatever GPS is already embedded in the line files and no "
                "attitude. Export anyway?",
            )
            if choice != QMessageBox.StandardButton.Yes:
                return
            store = FrameStore()
        else:
            store = self._store

        self._append_log(f"Starting export: kind={self._export_kind()}")
        self._set_busy(True, export=True)
        self._export_worker = ExportWorker(
            cams, store, out_dir,
            export_kind=self._export_kind(),
            jpeg_quality=self._quality_slider.value(),
            export_low_res=self._low_res_cb.isChecked(),
            low_res_width=LOW_RES_WIDTH,
            pattern=self._pattern(),
            recursive=self._recursive_cb.isChecked(),
            parent=self,
        )
        self._export_worker.progress.connect(self._on_progress)
        self._export_worker.log_line.connect(self._append_log)
        self._export_worker.failed.connect(self._on_export_failed)
        self._export_worker.finished_ok.connect(self._on_export_done)
        self._export_worker.start()

    @Slot()
    def _on_cancel(self) -> None:
        if self._export_worker is not None:
            self._export_worker.cancel()
        if self._load_worker is not None:
            self._load_worker.cancel()

    @Slot(int, int, str)
    def _on_progress(self, done: int, total: int, msg: str) -> None:
        self._progress.setMaximum(max(total, 1))
        self._progress.setValue(min(done, total))
        if msg:
            self.statusBar().showMessage(msg)

    @Slot(str)
    def _on_export_failed(self, msg: str) -> None:
        self._append_log(f"ERROR: {msg}")
        QMessageBox.critical(self, "Export failed", msg)

    @Slot(int, int, bool)
    def _on_export_done(self, done: int, total: int, cancelled: bool) -> None:
        self._export_worker = None
        self._set_busy(False)
        suffix = " (cancelled)" if cancelled else ""
        self._append_log(f"Finished{suffix}: {done}/{total} frame(s).")
        self.statusBar().showMessage(f"Done{suffix}: {done}/{total}")

    # ── Plot ──────────────────────────────────────────────────────────────

    def _loaded_camera_indices(self) -> list[int]:
        if self._store is None:
            return []
        return sorted({r.cam_index for r in self._store.positioned()})

    def _camera_tab_label(self, cam_index: int) -> str:
        serial = ""
        if self._store is not None:
            for rec in self._store.records:
                if rec.cam_index == cam_index and rec.cam_serial:
                    serial = rec.cam_serial
                    break
        label = f"Cam {cam_index + 1}"
        if serial:
            label += f" ({serial})"
        return label

    def _refresh_map_camera_tabs(self) -> None:
        self._map_cam_tabs.blockSignals(True)
        while self._map_cam_tabs.count():
            self._map_cam_tabs.removeTab(0)

        cam_indices = self._loaded_camera_indices()
        if len(cam_indices) <= 1:
            self._map_cam_tabs.setVisible(False)
            self._map_cam_index = cam_indices[0] if cam_indices else None
            self._map_cam_tabs.blockSignals(False)
            return

        for cam_index in cam_indices:
            tab_i = self._map_cam_tabs.addTab(self._camera_tab_label(cam_index))
            self._map_cam_tabs.setTabData(tab_i, cam_index)
        self._map_cam_tabs.setVisible(True)
        self._map_cam_index = cam_indices[0]
        self._map_cam_tabs.setCurrentIndex(0)
        self._map_cam_tabs.blockSignals(False)

    @Slot(int)
    def _on_map_cam_tab_changed(self, tab_index: int) -> None:
        if tab_index < 0 or not self._map_cam_tabs.isVisible():
            return
        cam_index = self._map_cam_tabs.tabData(tab_index)
        if cam_index is None:
            return
        self._map_cam_index = int(cam_index)
        self._update_plot()

    def _update_plot(self) -> None:
        self._ax.clear()
        self._ax.set_xlabel("Longitude (\u00b0E)")
        self._ax.set_ylabel("Latitude (\u00b0N)")
        self._ax.grid(True, alpha=0.3)
        if self._store is None:
            self._canvas.draw_idle()
            return

        cam_filter = self._map_cam_index
        positioned = [
            r for r in self._store.positioned()
            if cam_filter is None or r.cam_index == cam_filter
        ]
        all_lon = [r.lon_deg for r in positioned]
        all_lat = [r.lat_deg for r in positioned]
        if all_lon:
            self._ax.scatter(
                all_lon, all_lat, s=3, c="black", alpha=0.35,
                label="all frames", zorder=1,
            )

        cos_lat = 1.0
        seg_len = 0.0
        if all_lat and all_lon:
            mean_lat = sum(all_lat) / len(all_lat)
            cos_lat = max(math.cos(math.radians(mean_lat)), 1e-6)
            span_lat = max(all_lat) - min(all_lat)
            span_lon = (max(all_lon) - min(all_lon)) * cos_lat
            extent = max(span_lat, span_lon)
            seg_len = 0.02 * extent if extent > 0 else 1e-4

        cmap = matplotlib.colormaps.get_cmap("tab20")
        tracks = self._store.tracks()
        visible_tracks = sorted(
            (key, recs) for key, recs in tracks.items()
            if cam_filter is None or key[0] == cam_filter
        )
        drew_attitude = False
        for i, ((cam_index, line, hhmmss), recs) in enumerate(visible_tracks):
            pts = [(r.lon_deg, r.lat_deg, r) for r in recs if r.has_position]
            if not pts:
                continue
            colour = cmap(i % cmap.N)
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            serial = recs[0].cam_serial or f"Cam{cam_index + 1}"
            self._ax.scatter(
                xs, ys, s=20, c=[colour], edgecolors="white", linewidths=0.4,
                label=f"{serial} L{line}@{hhmmss} ({len(pts)} pts)", zorder=3,
            )
            if self._attitude_done and seg_len > 0:
                segments = []
                for lon, lat, r in pts:
                    if r.yaw_deg is None:
                        continue
                    yr = math.radians(r.yaw_deg)
                    dlat = seg_len * math.cos(yr)
                    dlon = seg_len * math.sin(yr) / cos_lat
                    segments.append([(lon, lat), (lon + dlon, lat + dlat)])
                if segments:
                    self._ax.add_collection(LineCollection(
                        segments, colors=[colour], linewidths=1.0,
                        alpha=0.9, zorder=4,
                    ))
                    drew_attitude = True

        self._ax.legend(
            loc="upper left", bbox_to_anchor=(1.02, 1.0),
            borderaxespad=0.0, fontsize=8, framealpha=0.9,
        )
        if all_lat:
            self._ax.set_aspect(1.0 / cos_lat, adjustable="datalim")
        cam_label = (
            self._camera_tab_label(cam_filter)
            if cam_filter is not None else "all cameras"
        )
        title = (
            f"{cam_label}: {len(positioned)} positioned frame(s), "
            f"{len(visible_tracks)} track(s)"
        )
        if drew_attitude:
            title += "  |  attitude -> yaw"
        self._ax.set_title(title)
        self._canvas.draw_idle()

    # ── Misc ──────────────────────────────────────────────────────────────

    def _set_busy(self, busy: bool, *, export: bool = False) -> None:
        self._load_btn.setEnabled(not busy)
        self._export_btn.setEnabled(not busy)
        self._attitude_btn.setEnabled(
            not busy and self._store is not None and bool(self._store.positioned())
        )
        self._cancel_btn.setEnabled(busy and export)

    def _append_log(self, text: str) -> None:
        self._log.append(text)

    def set_cam_dir(self, index: int, path: str | Path) -> None:
        if 0 <= index < len(self._cam_edits):
            self._cam_edits[index].setText(str(path))

    def set_out_dir(self, path: str | Path) -> None:
        self._out_edit.setText(str(path))

    def set_sbf(self, path: str | Path) -> None:
        self._sbf_edit.setText(str(path))


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cam_dirs", nargs="*", help="Up to three camera folders.")
    parser.add_argument("--sbf", type=Path, default=None, help="External SBF ExtEvent CSV.")
    parser.add_argument("--out", type=Path, default=None, help="Output folder.")
    args = parser.parse_args(argv)

    app = QApplication(sys.argv)
    win = StudioWindow()
    for i, d in enumerate(args.cam_dirs[:3]):
        win.set_cam_dir(i, d)
    if args.sbf is not None:
        win.set_sbf(args.sbf)
    if args.out is not None:
        win.set_out_dir(args.out)
    win.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
