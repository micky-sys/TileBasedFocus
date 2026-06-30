"""PySide6 GUI for the SBF -> TIFF geotagger.

Workflow:

1. Pick an SBF ExtEvent CSV (``*.txt`` / ``*.csv``) and a destination
   folder of exported TIFFs.
2. Hit **Correlate**. The tool parses the SBF, clusters its events,
   walks the destination folder, and matches each captured line to an
   event cluster (using ``sbf_geotag_exports.correlate``).
3. The map panel plots every GPS event as a black point and overlays
   each matched line's events in a unique colour. The right-hand
   legend names each colour.
4. (Optional) Hit **Add Attitude** to compute a per-frame camera
   Yaw/Pitch/Roll for every matched point and draw, from each point, a
   short line in the camera's look-azimuth (yaw) direction as seen from
   above. Once computed, the attitude angles are appended to the saved
   CSV (``heading_deg``/``yaw_deg``/``pitch_deg``/``roll_deg``/
   ``attitude_status``).
5. Hit **Start writing** to actually write EXIF GPS into every matched
   TIFF (via ExifTool if available, otherwise tifffile fallback).

The plot is a 2-D scatter of (longitude, latitude); it's a quick
"sanity-check map", not a real basemap (we deliberately avoid pulling
in tile-server / network dependencies).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
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

_HERE = Path(__file__).resolve().parent
_HOST = _HERE.parent / "host"
for _p in (_HOST, _HERE):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

from sbf_geotag_exports import (  # noqa: E402
    CorrelationResult,
    CSV_FIELDS,
    DEFAULT_LEAP_SECONDS,
    DEFAULT_MAX_FLIGHT_OFFSET_S,
    DEFAULT_MAX_MISSING_EVENTS,
    DEFAULT_MAX_RMSE_S,
    GpsFix,
    Match,
    SOURCE_BAY14LINE,
    SOURCE_TIFF,
    build_correlation_rows,
    correlate,
    detect_exiftool,
    event_to_fix,
    exiftool_geotag,
    write_gps_into_bay14line_matches,
    write_correlation_csv,
    write_gps_into_tiff,
)
from geotag_attitude_exports import (  # noqa: E402
    _ATTITUDE_OUT_COLS,
    HEADING_BEARING_WINDOW,
    HEADING_MIN_DISPLACEMENT_M,
    _HeadingTracker,
)
from capture.attitude import yaw_pitch_roll  # noqa: E402

log = logging.getLogger("sbf_geotag_gui")


# ─── Workers ─────────────────────────────────────────────────────────────────


class CorrelateWorker(QThread):
    """Runs :func:`correlate` off the UI thread."""

    finished_ok = Signal(object)  # CorrelationResult
    failed = Signal(str)

    def __init__(
        self,
        sbf_csv: Path,
        root: Path,
        *,
        source: str,
        frames_per_sweep: int,
        cluster_gap_s: float,
        cluster_min_size: int,
        leap_seconds: int,
        allow_reuse: bool,
        max_flight_offset_s: float,
        max_rmse_s: float,
        max_missing_events: int,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._sbf = sbf_csv
        self._root = root
        self._source = source
        self._fps = frames_per_sweep
        self._gap = cluster_gap_s
        self._min = cluster_min_size
        self._leap = leap_seconds
        self._reuse = allow_reuse
        self._max_offset = max_flight_offset_s
        self._max_rmse = max_rmse_s
        self._max_missing = max_missing_events

    def run(self) -> None:
        try:
            result = correlate(
                self._sbf,
                self._root,
                source=self._source,
                frames_per_sweep=self._fps,
                cluster_gap_s=self._gap,
                cluster_min_size=self._min,
                leap_seconds=self._leap,
                allow_reuse=self._reuse,
                max_flight_offset_s=self._max_offset,
                max_rmse_s=self._max_rmse,
                max_missing_events=self._max_missing,
            )
            self.finished_ok.emit(result)
        except Exception as exc:
            log.exception("correlate failed")
            self.failed.emit(str(exc))


class WriteWorker(QThread):
    """Walks the correlation result and writes GPS into every match.

    In ``source='tiff'`` mode, that means EXIF GPS tags on each TIFF
    (via ExifTool or tifffile). In ``source='bay14line'`` mode, the
    same coordinates get embedded as a ``"gps"`` block inside each
    matched chunk header IN PLACE -- byte-length preserving so the
    file's chunk offsets and line index stay valid. Bay14Reader's
    TIFF encoder already reads that block on export and writes the
    standard EXIF GPS tag set, so the bay14line write completes the
    round-trip from SBF to exported TIFFs.
    """

    progress = Signal(int, int, str)        # done, total, message
    log_line = Signal(str)
    finished_ok = Signal(int, int, int)     # written, skipped, failed

    def __init__(
        self,
        result: CorrelationResult,
        *,
        leap_seconds: int,
        source: str,
        exiftool_exe: Optional[Path],
        delete_incomplete: bool,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._result = result
        self._leap = leap_seconds
        self._source = source
        self._exiftool = exiftool_exe
        self._delete_incomplete = delete_incomplete
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        if self._source == SOURCE_BAY14LINE:
            self._run_bay14line()
            return
        self._run_tiff()

    def _run_bay14line(self) -> None:
        total_files = len({
            frame.path
            for m in self._result.matches
            if m.cluster is not None
            for frame in m.line.frames
        })
        if total_files == 0:
            self.finished_ok.emit(0, 0, 0)
            return

        def _on_file_done(done: int, total: int, name: str) -> None:
            self.progress.emit(done, total, name)

        def _is_cancelled() -> bool:
            return self._cancel

        written, skipped, failed, errors = write_gps_into_bay14line_matches(
            self._result.matches,
            leap_seconds=self._leap,
            progress_cb=_on_file_done,
            cancel_cb=_is_cancelled,
        )
        for e in errors:
            self.log_line.emit(e)
        self.finished_ok.emit(written, skipped, failed)

    def _run_tiff(self) -> None:
        if self._delete_incomplete and self._result.incomplete_tiffs:
            self.log_line.emit(
                f"Deleting {len(self._result.incomplete_tiffs)} "
                "incomplete TIFF(s)..."
            )
            for p in self._result.incomplete_tiffs:
                try:
                    p.unlink()
                    self.log_line.emit(f"  deleted {p.name}")
                except OSError as exc:
                    self.log_line.emit(f"  delete failed {p.name}: {exc}")

        total = sum(len(m.line.frames) for m in self._result.matches if m.cluster is not None)
        if total == 0:
            self.finished_ok.emit(0, 0, 0)
            return

        written = 0
        skipped = 0
        failed = 0
        done = 0
        for m in self._result.matches:
            if m.cluster is None:
                continue
            for frame, slot in zip(m.line.frames, m.slots):
                if self._cancel:
                    self.finished_ok.emit(written, skipped, failed)
                    return
                ev = m.event_for_slot(slot)
                if ev is None:
                    skipped += 1
                    done += 1
                    self.progress.emit(done, total, f"slot OOB: {frame.path.name}")
                    continue
                if not frame.path.is_file():
                    skipped += 1
                    done += 1
                    self.progress.emit(done, total, f"missing: {frame.path.name}")
                    continue
                fix = event_to_fix(ev, leap_seconds=self._leap)
                if not fix.has_position:
                    skipped += 1
                    done += 1
                    self.progress.emit(done, total, f"no fix: {frame.path.name}")
                    continue
                try:
                    if self._exiftool is not None:
                        exiftool_geotag(frame.path, fix, exiftool_exe=self._exiftool)
                    else:
                        write_gps_into_tiff(frame.path, fix)
                    written += 1
                except subprocess.CalledProcessError as exc:
                    failed += 1
                    self.log_line.emit(
                        f"FAILED {frame.path.name}: exiftool exit "
                        f"{exc.returncode}\n{(exc.stderr or '').strip()}"
                    )
                except Exception as exc:
                    failed += 1
                    self.log_line.emit(f"FAILED {frame.path.name}: {exc}")
                done += 1
                self.progress.emit(done, total, frame.path.name)
        self.finished_ok.emit(written, skipped, failed)


# ─── Window ──────────────────────────────────────────────────────────────────


class SbfGeotagWindow(QMainWindow):
    """Single-window GUI: file pickers + parameters + map + write controls."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SBF \u2194 TIFF geotagger")
        self.resize(1280, 800)

        central = QWidget(self)
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)

        # --- Inputs row -----------------------------------------------------
        inputs = QGroupBox("Inputs", central)
        in_form = QFormLayout(inputs)

        sbf_row = QHBoxLayout()
        self._sbf_edit = QLineEdit(inputs)
        self._sbf_edit.setPlaceholderText("Pick the SBF ExtEvent CSV (*.txt)...")
        sbf_browse = QPushButton("Browse...", inputs)
        sbf_browse.clicked.connect(self._on_browse_sbf)
        sbf_row.addWidget(self._sbf_edit, 1)
        sbf_row.addWidget(sbf_browse)
        in_form.addRow("SBF CSV:", sbf_row)

        # Frames-source selector. The matcher is identical for both
        # sources; only the discovery step changes. We default to TIFFs
        # because that's the canonical post-export workflow; the raw
        # ``.bay14line`` mode is a shortcut for testing correlation
        # before the (slow) export step has run on an offline rig.
        # The currentIndexChanged signal is wired AFTER the rest of the
        # UI is built (see end of __init__) -- ``addItem`` fires it
        # while ``_folder_edit`` / ``_write_btn`` don't exist yet.
        self._source_combo = QComboBox(inputs)
        self._source_combo.addItem(
            "Exported TIFFs (*.tif)", userData=SOURCE_TIFF,
        )
        self._source_combo.addItem(
            "Raw line files (*.bay14line)  [CSV only -- no EXIF writeback]",
            userData=SOURCE_BAY14LINE,
        )
        in_form.addRow("Frames source:", self._source_combo)

        folder_row = QHBoxLayout()
        self._folder_edit = QLineEdit(inputs)
        folder_browse = QPushButton("Browse...", inputs)
        folder_browse.clicked.connect(self._on_browse_folder)
        folder_row.addWidget(self._folder_edit, 1)
        folder_row.addWidget(folder_browse)
        in_form.addRow("Destination folder:", folder_row)

        root_layout.addWidget(inputs)

        # --- Parameters row -------------------------------------------------
        params = QGroupBox("Parameters", central)
        p_form = QFormLayout(params)

        self._fps_spin = QSpinBox(params)
        self._fps_spin.setRange(1, 1000)
        self._fps_spin.setValue(13)
        p_form.addRow("Frames per sweep:", self._fps_spin)

        self._gap_spin = QDoubleSpinBox(params)
        self._gap_spin.setRange(1.0, 600.0)
        self._gap_spin.setSingleStep(1.0)
        # 60 s default: long enough to absorb inter-sweep pauses on the
        # May 2026 flight rig (which hit ~43 s on the L10 line) plus any
        # pre-line "trigger test" bursts; well below the multi-minute
        # inter-line pauses, so lines stay in separate clusters. The
        # ``match_line`` algorithm scans starting offsets inside each
        # cluster, so merging the trigger burst into the line cluster
        # is correct.
        self._gap_spin.setValue(60.0)
        self._gap_spin.setSuffix(" s")
        p_form.addRow("Cluster gap:", self._gap_spin)

        self._minsize_spin = QSpinBox(params)
        self._minsize_spin.setRange(1, 1_000_000)
        self._minsize_spin.setValue(50)
        p_form.addRow("Min cluster size:", self._minsize_spin)

        self._leap_spin = QSpinBox(params)
        self._leap_spin.setRange(0, 60)
        self._leap_spin.setValue(DEFAULT_LEAP_SECONDS)
        p_form.addRow("Leap seconds (GPS\u2212UTC):", self._leap_spin)

        # Absolute-time prior. Reject cluster candidates whose implied
        # flight-clock offset is further than this from zero, before
        # picking by RMSE. Without this, pure-RMSE matching can latch
        # onto the wrong 897-event cluster when several full-line
        # clusters share the same intra-line firing pattern. 15 min
        # is generous for the documented "up to several minutes off"
        # flight-computer-vs-GPS clock drift.
        self._max_offset_spin = QDoubleSpinBox(params)
        self._max_offset_spin.setRange(1.0, 86_400.0)
        self._max_offset_spin.setSingleStep(30.0)
        self._max_offset_spin.setDecimals(0)
        self._max_offset_spin.setValue(DEFAULT_MAX_FLIGHT_OFFSET_S)
        self._max_offset_spin.setSuffix(" s")
        p_form.addRow("Max flight-clock offset:", self._max_offset_spin)

        # RMSE cap on the intra-line firing-pattern fit. Real matches
        # are ~7-100 ms; clearly-wrong matches that the abs-time prior
        # couldn't reject (because the wrong cluster happens to fall
        # inside the window) come back at multiple SECONDS. 1 s is
        # well in the gap.
        self._max_rmse_spin = QDoubleSpinBox(params)
        self._max_rmse_spin.setRange(0.001, 3600.0)
        self._max_rmse_spin.setSingleStep(0.1)
        self._max_rmse_spin.setDecimals(3)
        self._max_rmse_spin.setValue(DEFAULT_MAX_RMSE_S)
        self._max_rmse_spin.setSuffix(" s")
        p_form.addRow("Max RMSE per match:", self._max_rmse_spin)

        # How many GPS events a line is allowed to be MISSING and still
        # claim a cluster. Real-world rigs occasionally drop the last
        # few triggers; the frames stay in the CSV with no coords.
        self._max_missing_spin = QSpinBox(params)
        self._max_missing_spin.setRange(0, 10_000)
        self._max_missing_spin.setValue(DEFAULT_MAX_MISSING_EVENTS)
        p_form.addRow("Max missing events:", self._max_missing_spin)

        self._reuse_cb = QCheckBox(
            "Allow re-using one cluster for multiple lines (diagnostic)",
            params,
        )
        p_form.addRow("", self._reuse_cb)

        self._delete_cb = QCheckBox(
            "Delete cancelled-mid-write TIFFs during writing",
            params,
        )
        p_form.addRow("", self._delete_cb)

        root_layout.addWidget(params)

        # --- Controls row ---------------------------------------------------
        controls = QHBoxLayout()
        self._correlate_btn = QPushButton("Correlate", central)
        self._correlate_btn.clicked.connect(self._on_correlate)
        # Per-frame camera attitude (Yaw/Pitch/Roll). Greyed out until a
        # correlation with at least one matched line exists.
        self._attitude_btn = QPushButton("Add Attitude", central)
        self._attitude_btn.setEnabled(False)
        self._attitude_btn.setToolTip(
            "Compute per-frame Yaw/Pitch/Roll for every matched point, draw "
            "the look-azimuth on the map, and append the angles to the saved CSV."
        )
        self._attitude_btn.clicked.connect(self._on_add_attitude)
        self._write_btn = QPushButton("Start writing", central)
        self._write_btn.setEnabled(False)
        self._write_btn.clicked.connect(self._on_start_writing)
        self._cancel_btn = QPushButton("Cancel write", central)
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._on_cancel)
        # CSV sidecar export -- useful when full EXIF writes are
        # impractical (e.g. BigTIFF on Google Drive: every per-file
        # rewrite is full-content, ~minutes/file).
        self._csv_btn = QPushButton("Save CSV...", central)
        self._csv_btn.setEnabled(False)
        self._csv_btn.clicked.connect(self._on_save_csv)
        controls.addWidget(self._correlate_btn)
        controls.addWidget(self._attitude_btn)
        controls.addWidget(self._write_btn)
        controls.addWidget(self._cancel_btn)
        controls.addWidget(self._csv_btn)
        controls.addStretch(1)
        backend = "exiftool" if detect_exiftool() is not None else "tifffile (slow)"
        self._backend_lbl = QLabel(f"EXIF backend: {backend}", central)
        controls.addWidget(self._backend_lbl)
        root_layout.addLayout(controls)

        # --- Map + log -----------------------------------------------------
        self._figure = Figure(figsize=(8, 5))
        # Leave room on the right for the legend axes.
        self._figure.subplots_adjust(left=0.08, right=0.78, top=0.94, bottom=0.10)
        self._ax = self._figure.add_subplot(111)
        self._ax.set_xlabel("Longitude (\u00b0E)")
        self._ax.set_ylabel("Latitude (\u00b0N)")
        self._ax.set_title("Run correlation to populate the map.")
        self._ax.grid(True, alpha=0.3)
        self._canvas = FigureCanvas(self._figure)
        self._toolbar = NavigationToolbar(self._canvas, central)
        root_layout.addWidget(self._toolbar)
        root_layout.addWidget(self._canvas, 1)

        self._log = QTextEdit(central)
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(150)
        root_layout.addWidget(self._log)

        self._progress = QProgressBar(central)
        self._progress.setMaximum(1)
        self._progress.setValue(0)
        root_layout.addWidget(self._progress)

        self.statusBar().showMessage("Ready.")

        self._correlation: Optional[CorrelationResult] = None
        self._correlate_worker: Optional[CorrelateWorker] = None
        self._write_worker: Optional[WriteWorker] = None

        # Attitude overlay state, populated by "Add Attitude":
        #   _attitude_by_match: id(Match) -> list[(lon, lat, yaw_deg)] for the
        #     map overlay (one entry per positioned, matched frame).
        #   _attitude_by_name: export filename -> attitude column dict, used to
        #     append Yaw/Pitch/Roll to the saved CSV.
        self._attitude_by_match: Optional[dict[int, list[tuple[float, float, float]]]] = None
        self._attitude_by_name: Optional[dict[str, dict[str, str]]] = None

        # Now that ``_folder_edit`` and ``_write_btn`` exist, it's safe
        # to wire the source-combo signal and apply the initial state.
        self._source_combo.currentIndexChanged.connect(
            self._on_source_changed,
        )
        self._on_source_changed()

    # ── Slots ─────────────────────────────────────────────────────────────

    @Slot()
    def _on_browse_sbf(self) -> None:
        start = self._sbf_edit.text().strip() or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "SBF ExtEvent CSV", start,
            "SBF logs (*.txt *.csv);;All files (*)",
        )
        if path:
            self._sbf_edit.setText(path)

    @Slot()
    def _on_browse_folder(self) -> None:
        start = self._folder_edit.text().strip() or str(Path.home())
        d = QFileDialog.getExistingDirectory(self, "Destination folder", start)
        if d:
            self._folder_edit.setText(d)

    def _current_source(self) -> str:
        data = self._source_combo.currentData()
        return data if isinstance(data, str) else SOURCE_TIFF

    @Slot()
    def _on_source_changed(self, *_args) -> None:
        """Keep the folder placeholder + write-button hint in sync with
        the selected source kind. Bay14Line writes patch each chunk
        header IN PLACE to embed a ``gps`` JSON block -- Bay14Reader
        then reads it on TIFF export and emits standard EXIF GPS tags,
        so the round-trip completes through the existing exporter.
        """
        src = self._current_source()
        if src == SOURCE_BAY14LINE:
            self._folder_edit.setPlaceholderText(
                "Pick the folder of raw .bay14line containers..."
            )
            self._write_btn.setToolTip(
                "Embed GPS coords into each matched .bay14line chunk "
                "header in place. Bay14Reader's TIFF export will then "
                "emit EXIF GPS tags from that block (no rewrite of the "
                "raw container's chunk offsets)."
            )
        else:
            self._folder_edit.setPlaceholderText(
                "Pick the destination folder of exported TIFFs..."
            )
            self._write_btn.setToolTip("")
            # Re-enable only if we already have a result with matches;
            # _on_correlate_done is the authoritative gate for first
            # enablement.
            if self._correlation is not None:
                any_match = any(
                    m.cluster is not None
                    for m in self._correlation.matches
                )
                self._write_btn.setEnabled(any_match)

    @Slot()
    def _on_correlate(self) -> None:
        sbf = Path(self._sbf_edit.text().strip())
        root = Path(self._folder_edit.text().strip())
        if not sbf.is_file():
            QMessageBox.warning(self, "Bad SBF file",
                                f"Not a file:\n  {sbf}")
            return
        if not root.is_dir():
            QMessageBox.warning(self, "Bad destination",
                                f"Not a folder:\n  {root}")
            return

        self._log.clear()
        self._append_log("Correlating...")
        self._correlate_btn.setEnabled(False)
        self._write_btn.setEnabled(False)
        self._attitude_btn.setEnabled(False)
        self._correlation = None
        self._attitude_by_match = None
        self._attitude_by_name = None
        self.statusBar().showMessage("Correlating...")

        self._correlate_worker = CorrelateWorker(
            sbf,
            root,
            source=self._current_source(),
            frames_per_sweep=self._fps_spin.value(),
            cluster_gap_s=self._gap_spin.value(),
            cluster_min_size=self._minsize_spin.value(),
            leap_seconds=self._leap_spin.value(),
            allow_reuse=self._reuse_cb.isChecked(),
            max_flight_offset_s=self._max_offset_spin.value(),
            max_rmse_s=self._max_rmse_spin.value(),
            max_missing_events=self._max_missing_spin.value(),
            parent=self,
        )
        self._correlate_worker.finished_ok.connect(self._on_correlate_done)
        self._correlate_worker.failed.connect(self._on_correlate_failed)
        self._correlate_worker.start()

    @Slot(object)
    def _on_correlate_done(self, result: CorrelationResult) -> None:
        self._correlate_btn.setEnabled(True)
        self._correlation = result
        matched = sum(1 for m in result.matches if m.cluster is not None)
        total_frames = sum(len(m.line.frames) for m in result.matches)
        self._append_log(
            f"Done. {len(result.events)} events; {len(result.clusters)} clusters "
            f"(after min-size filter); {len(result.lines)} line(s); "
            f"{matched}/{len(result.matches)} matched; "
            f"{total_frames} frame(s) total; "
            f"{len(result.incomplete_tiffs)} incomplete TIFF(s)."
        )
        for m in result.matches:
            self._append_log(self._fmt_match(m))
        for p in result.incomplete_tiffs:
            self._append_log(f"INCOMPLETE TIFF: {p}")
        self._update_plot(result)
        any_match = any(m.cluster is not None for m in result.matches)
        # In bay14line mode the writeback path isn't implemented yet --
        # Both sources support writing now: TIFFs get EXIF GPS via
        # ExifTool/tifffile; .bay14line containers get a per-chunk
        # ``gps`` JSON block patched in place.
        self._write_btn.setEnabled(any_match)
        # Attitude needs at least one matched line to derive headings from.
        self._attitude_btn.setEnabled(any_match)
        # CSV export is useful even with zero matches (unmatched rows
        # are still emitted with a diagnostic note).
        self._csv_btn.setEnabled(True)
        self.statusBar().showMessage(
            "Correlation complete. Inspect the map, then 'Start writing'."
        )

    @staticmethod
    def _fmt_match(m: Match) -> str:
        label = m.line.label()
        if m.cluster is None:
            return (f"{label}: NO match (need cluster size > "
                    f"{max(m.slots) if m.slots else 0})")
        import math
        rmse_txt = (f"{m.rmse_s*1000:.0f}ms"
                    if m.rmse_s == m.rmse_s and m.rmse_s != math.inf
                    else "n/a")
        off_txt = (f"{m.flight_offset_s:+.2f}s"
                   if m.flight_offset_s is not None else "n/a")
        unmatched = sum(1 for s in m.slots if m.event_for_slot(s) is None)
        partial_txt = f" ({unmatched} no-coords)" if unmatched else ""
        return (
            f"{label}: cluster #{m.cluster.index} size={m.cluster.size} "
            f"slot_offset={m.slot_offset} frames={len(m.line.frames)} "
            f"rmse={rmse_txt} offset={off_txt}{partial_txt}"
        )

    @Slot(str)
    def _on_correlate_failed(self, msg: str) -> None:
        self._correlate_btn.setEnabled(True)
        self._append_log(f"ERROR: {msg}")
        QMessageBox.critical(self, "Correlation failed", msg)
        self.statusBar().showMessage("Correlation failed.")

    # ── Attitude ──────────────────────────────────────────────────────────

    @Slot()
    def _on_add_attitude(self) -> None:
        if self._correlation is None:
            return
        self.statusBar().showMessage("Computing attitude...")
        try:
            by_match, by_name, stats = self._compute_attitude(self._correlation)
        except Exception as exc:
            log.exception("attitude computation failed")
            self._append_log(f"Attitude FAILED: {exc}")
            QMessageBox.critical(self, "Attitude failed", str(exc))
            self.statusBar().showMessage("Attitude failed.")
            return
        self._attitude_by_match = by_match
        self._attitude_by_name = by_name
        self._append_log(
            f"Attitude: {stats['points']} matched frame(s) "
            f"({stats['with_heading']} with heading, "
            f"{stats['without_heading']} without); "
            f"{stats['no_gps']} no-fix, "
            f"{stats['unknown_serial']} unknown-serial. "
            "Direction lines drawn on map; CSV export will include the angles."
        )
        self._update_plot(self._correlation)
        self.statusBar().showMessage(
            "Attitude added. Direction lines show the camera look-azimuth (yaw)."
        )

    def _compute_attitude(
        self, result: CorrelationResult,
    ) -> tuple[
        dict[int, list[tuple[float, float, float]]],
        dict[str, dict[str, str]],
        dict[str, int],
    ]:
        """Compute per-frame Yaw/Pitch/Roll for every matched line.

        Each :class:`Match` is exactly one ``(camera, line, hhmmss)`` track,
        so a fresh causal heading tracker per match mirrors the standalone
        ``geotag_attitude_exports`` logic (and the live ``gps_correlator``).
        Returns ``(by_match, by_name, stats)`` where *by_match* maps
        ``id(match)`` to the map overlay points and *by_name* maps each
        frame's export filename to its CSV attitude columns.
        """
        by_match: dict[int, list[tuple[float, float, float]]] = {}
        by_name: dict[str, dict[str, str]] = {}
        stats = {
            "points": 0, "with_heading": 0, "without_heading": 0,
            "no_gps": 0, "unknown_serial": 0,
        }
        for m in result.matches:
            if m.cluster is None:
                continue
            serial = m.line.cam_serial or (
                m.line.frames[0].cam if m.line.frames else ""
            )
            if serial.lower().startswith("cam"):
                serial = serial[3:]
            tracker = _HeadingTracker(
                min_displacement_m=HEADING_MIN_DISPLACEMENT_M,
                window=HEADING_BEARING_WINDOW,
            )
            overlay: list[tuple[float, float, float]] = []
            for frame, slot in zip(m.line.frames, m.slots):
                ev = m.event_for_slot(slot)
                lat = ev.lat_deg if ev is not None else None
                lon = ev.lon_deg if ev is not None else None
                heading = tracker.update(lat, lon)
                if lat is None or lon is None:
                    stats["no_gps"] += 1
                export_name = (
                    frame.tiff_export_name()
                    if frame.is_bay14line_source
                    else frame.path.name
                )
                try:
                    att = yaw_pitch_roll(
                        serial, frame.frame_in_sweep, heading_deg=heading,
                    )
                except ValueError:
                    stats["unknown_serial"] += 1
                    by_name[export_name] = {
                        "heading_deg": "" if heading is None else f"{heading:.4f}",
                        "yaw_deg": "", "pitch_deg": "", "roll_deg": "",
                        "attitude_status": "unknown_serial",
                    }
                    continue
                stats["points"] += 1
                if att.heading_deg is None:
                    stats["without_heading"] += 1
                    status = "ok_no_heading"
                else:
                    stats["with_heading"] += 1
                    status = "ok"
                by_name[export_name] = {
                    "heading_deg": (
                        "" if att.heading_deg is None else f"{att.heading_deg:.4f}"
                    ),
                    "yaw_deg": f"{att.yaw_deg:.4f}",
                    "pitch_deg": f"{att.pitch_deg:.4f}",
                    "roll_deg": f"{att.roll_deg:.4f}",
                    "attitude_status": status,
                }
                if lat is not None and lon is not None:
                    overlay.append((lon, lat, att.yaw_deg))
            if overlay:
                by_match[id(m)] = overlay
        return by_match, by_name, stats

    @Slot()
    def _on_start_writing(self) -> None:
        if self._correlation is None:
            return
        src = self._current_source()
        if src == SOURCE_TIFF:
            backend = detect_exiftool()
            if backend is None:
                choice = QMessageBox.question(
                    self, "Slow EXIF backend",
                    "ExifTool was not found. The fallback tifffile path will "
                    "rewrite every TIFF (slow on Google Drive, and the GPS tags "
                    "won't be readable by standard EXIF viewers). Proceed anyway?",
                )
                if choice != QMessageBox.Yes:
                    return
            start_msg = "Writing EXIF GPS..."
        else:
            backend = None
            start_msg = ("Embedding GPS into .bay14line chunk headers "
                         "(in-place, byte-length preserving)...")
        self._write_btn.setEnabled(False)
        self._correlate_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._progress.setMaximum(1)
        self._progress.setValue(0)
        self._append_log(start_msg)
        self._write_worker = WriteWorker(
            self._correlation,
            leap_seconds=self._leap_spin.value(),
            source=src,
            exiftool_exe=backend,
            delete_incomplete=self._delete_cb.isChecked(),
            parent=self,
        )
        self._write_worker.progress.connect(self._on_write_progress)
        self._write_worker.log_line.connect(self._append_log)
        self._write_worker.finished_ok.connect(self._on_write_done)
        self._write_worker.start()

    @Slot()
    def _on_save_csv(self) -> None:
        if self._correlation is None:
            return
        folder = self._folder_edit.text().strip()
        # Default to the destination folder with a friendly name, so the
        # operator just hits Save.
        if folder:
            suggested = str(Path(folder) / "gps_correlation.csv")
        else:
            suggested = str(Path.home() / "gps_correlation.csv")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save correlation CSV", suggested, "CSV (*.csv);;All files (*)",
        )
        if not path:
            return
        leap = self._leap_spin.value()
        root = Path(folder) if folder else None
        try:
            if self._attitude_by_name:
                n = self._write_csv_with_attitude(Path(path), leap_seconds=leap, root=root)
            else:
                n = write_correlation_csv(
                    self._correlation,
                    Path(path),
                    leap_seconds=leap,
                    root=root,
                )
        except Exception as exc:
            log.exception("CSV write failed")
            self._append_log(f"CSV write FAILED: {exc}")
            QMessageBox.critical(self, "CSV write failed", str(exc))
            return
        suffix = " (+attitude)" if self._attitude_by_name else ""
        self._append_log(f"Wrote {n}-row CSV{suffix}: {path}")
        self.statusBar().showMessage(f"CSV saved ({n} rows){suffix}: {path}")

    def _write_csv_with_attitude(
        self, out_path: Path, *, leap_seconds: int, root: Optional[Path],
    ) -> int:
        """Write the correlation CSV with the computed attitude columns added.

        Builds the standard correlation rows, then joins each row's
        ``filename`` against the per-frame attitude computed by
        "Add Attitude" and appends
        ``heading_deg``/``yaw_deg``/``pitch_deg``/``roll_deg``/
        ``attitude_status``. Rows with no attitude (e.g. unmatched frames)
        get empty attitude cells.
        """
        import csv as _csv

        assert self._correlation is not None
        assert self._attitude_by_name is not None
        rows = build_correlation_rows(
            self._correlation, leap_seconds=leap_seconds, root=root,
        )
        out_fields = list(CSV_FIELDS) + [
            c for c in _ATTITUDE_OUT_COLS if c not in CSV_FIELDS
        ]
        for row in rows:
            att = self._attitude_by_name.get(row.get("filename", ""))
            for col in _ATTITUDE_OUT_COLS:
                row[col] = att.get(col, "") if att else ""
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8-sig", newline="") as fp:
            writer = _csv.DictWriter(
                fp, fieldnames=out_fields, extrasaction="ignore",
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        return len(rows)

    @Slot()
    def _on_cancel(self) -> None:
        if self._write_worker is not None:
            self._write_worker.cancel()
            self._cancel_btn.setEnabled(False)
            self.statusBar().showMessage("Cancelling...")

    @Slot(int, int, str)
    def _on_write_progress(self, done: int, total: int, msg: str) -> None:
        self._progress.setMaximum(max(total, 1))
        self._progress.setValue(min(done, total))
        self.statusBar().showMessage(f"{done}/{total} {msg}")

    @Slot(int, int, int)
    def _on_write_done(self, written: int, skipped: int, failed: int) -> None:
        self._write_btn.setEnabled(True)
        self._correlate_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        if self._current_source() == SOURCE_BAY14LINE:
            verb = "Patched"
            unit = "chunk(s) in .bay14line files"
        else:
            verb = "Wrote EXIF GPS into"
            unit = "TIFF(s)"
        self._append_log(
            f"{verb} {written} {unit}; skipped {skipped}; "
            f"{failed} failure(s)."
        )
        self.statusBar().showMessage(
            f"Done. Wrote {written}, skipped {skipped}, failed {failed}."
        )

    # ── Plot helpers ──────────────────────────────────────────────────────

    def _update_plot(self, result: CorrelationResult) -> None:
        import math

        self._ax.clear()
        self._ax.set_xlabel("Longitude (\u00b0E)")
        self._ax.set_ylabel("Latitude (\u00b0N)")
        self._ax.grid(True, alpha=0.3)

        # All events as a black baseline so the operator sees the full
        # flight path even where no exported line lives.
        all_lon = [e.lon_deg for e in result.events if e.lon_deg is not None]
        all_lat = [e.lat_deg for e in result.events if e.lat_deg is not None]
        if all_lon:
            self._ax.scatter(
                all_lon, all_lat,
                s=3, c="black", alpha=0.45,
                label="all events (no line)",
                zorder=1,
            )

        # Latitude-cosine correction (also used to draw geographically
        # correct attitude direction lines), and a segment length scaled
        # to the plotted extent so the look-azimuth lines stay legible.
        cos_lat = 1.0
        seg_len = 0.0
        if all_lat and all_lon:
            mean_lat = sum(all_lat) / len(all_lat)
            cos_lat = max(math.cos(math.radians(mean_lat)), 1e-6)
            span_lat = max(all_lat) - min(all_lat)
            span_lon = (max(all_lon) - min(all_lon)) * cos_lat
            extent = max(span_lat, span_lon)
            seg_len = 0.02 * extent if extent > 0 else 1e-4

        # Per-matched-line overlays in a distinct colour.
        # `tab20` cycles 20 colours; survey runs with more than 20 lines are
        # rare, and the colours wrap gracefully if we exceed it.
        cmap = matplotlib.colormaps.get_cmap("tab20")
        matched_count = 0
        drew_attitude = False
        for i, m in enumerate(result.matches):
            if m.cluster is None:
                continue
            evs = [ev for ev in (m.event_for_slot(s) for s in m.slots)
                   if ev is not None]
            xs = [e.lon_deg for e in evs if e.lon_deg is not None]
            ys = [e.lat_deg for e in evs if e.lat_deg is not None]
            if not xs:
                continue
            colour = cmap(matched_count % cmap.N)
            self._ax.scatter(
                xs, ys,
                s=24, c=[colour],
                edgecolors="white", linewidths=0.5,
                label=f"{m.line.label()}  ({len(xs)} pts)",
                zorder=3,
            )
            # Attitude direction lines (look-azimuth = yaw), as seen from
            # above. Drawn in the same colour as the line's points.
            if (
                self._attitude_by_match is not None
                and seg_len > 0
                and id(m) in self._attitude_by_match
            ):
                segments = []
                for lon, lat, yaw in self._attitude_by_match[id(m)]:
                    yr = math.radians(yaw)
                    dlat = seg_len * math.cos(yr)
                    dlon = seg_len * math.sin(yr) / cos_lat
                    segments.append([(lon, lat), (lon + dlon, lat + dlat)])
                if segments:
                    self._ax.add_collection(LineCollection(
                        segments, colors=[colour],
                        linewidths=1.1, alpha=0.9, zorder=4,
                    ))
                    drew_attitude = True
            matched_count += 1

        # Anchor the legend OUTSIDE the axes (right side), per the spec.
        self._ax.legend(
            loc="upper left",
            bbox_to_anchor=(1.02, 1.0),
            borderaxespad=0.0,
            fontsize=8,
            framealpha=0.9,
        )

        # Equal scaling for sane aspect at mid-latitudes -- adjust for
        # the latitude cosine so longitude degrees look right next to
        # latitude degrees.
        if all_lat:
            self._ax.set_aspect(1.0 / cos_lat, adjustable="datalim")

        title_bits = [
            f"{len(result.events)} events",
            f"{len(result.clusters)} clusters",
            f"{matched_count}/{len(result.matches)} lines matched",
        ]
        if drew_attitude:
            title_bits.append("attitude \u2192 yaw")
        self._ax.set_title("  |  ".join(title_bits))
        self._canvas.draw_idle()

    # ── Misc ──────────────────────────────────────────────────────────────

    def _append_log(self, text: str) -> None:
        self._log.append(text)

    def set_sbf(self, value: str) -> None:
        self._sbf_edit.setText(value)

    def set_folder(self, value: str) -> None:
        self._folder_edit.setText(value)


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO)
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    win = SbfGeotagWindow()
    # Friendly CLI pre-fill: ``... sbf_geotag_gui.py SBF DEST``.
    if len(argv) >= 2 and argv[1]:
        win.set_sbf(argv[1])
    if len(argv) >= 3 and argv[2]:
        win.set_folder(argv[2])
    win.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
