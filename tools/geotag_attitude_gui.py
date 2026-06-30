"""Tiny PySide6 GUI for the per-frame attitude tagger.

Wraps :mod:`geotag_attitude_exports` with a folder picker, a few options
(recursive, dry-run, skip-no-heading, optional sidecar CSV), and a streaming
log pane. The work runs in a QThread so the UI stays responsive on slow
filesystems (e.g. Google Drive).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from geotag_attitude_exports import (  # noqa: E402
    RunStats,
    detect_exiftool,
    process,
)

log = logging.getLogger(__name__)


class AttitudeWorker(QThread):
    """Background thread that tags TIFFs with Camera:Yaw/Pitch/Roll."""

    progress = Signal(int, int, str)  # done, total, name
    log_line = Signal(str)
    failed = Signal(str)
    finished_ok = Signal(object, bool)  # RunStats, write_mode

    def __init__(
        self,
        root: Path,
        *,
        recursive: bool,
        write: bool,
        skip_no_heading: bool,
        csv_path: Optional[Path],
        exiftool_exe: Optional[Path],
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._root = Path(root)
        self._recursive = recursive
        self._write = write
        self._skip_no_heading = skip_no_heading
        self._csv_path = csv_path
        self._exiftool_exe = exiftool_exe
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            _recs, stats = process(
                [self._root],
                recursive=self._recursive,
                write=self._write,
                skip_no_heading=self._skip_no_heading,
                csv_path=self._csv_path,
                exiftool_exe=self._exiftool_exe,
                min_displacement_m=0.5,
                window=8,
                progress_cb=lambda d, t, n: self.progress.emit(d, t, n),
                log_cb=lambda m: self.log_line.emit(m),
                cancel_cb=self._cancel.is_set,
            )
            self.finished_ok.emit(stats, self._write)
        except Exception as exc:
            log.exception("attitude worker failed")
            self.failed.emit(str(exc))
            self.finished_ok.emit(RunStats(), self._write)


class AttitudeWindow(QMainWindow):
    """Single-window UI: folder picker + options + log pane."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Parallax per-frame attitude tagger")
        self.resize(860, 560)

        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        intro = QLabel(
            "Computes per-frame camera Yaw/Pitch/Roll for exported TIFFs and "
            "writes them as XMP Camera:Yaw/Pitch/Roll.\n"
            "Reads GPS from each TIFF's EXIF and the camera serial + frame "
            "number from its Bay14Reader filename\n"
            "    Cam{serial}_{date}_{project}_{hhmmss}_L{line}_S{sweep}_F{frame}.tif\n"
            "Heading is a causal running-average GPS bearing, so the first "
            "frame(s) of each line have no heading.",
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        folder_group = QGroupBox("Folder of exported TIFFs", self)
        folder_row = QHBoxLayout(folder_group)
        self._folder_edit = QLineEdit(folder_group)
        self._folder_edit.setPlaceholderText("Pick a folder...")
        self._browse_btn = QPushButton("Browse...", folder_group)
        self._browse_btn.clicked.connect(self._on_browse)
        folder_row.addWidget(self._folder_edit, 1)
        folder_row.addWidget(self._browse_btn)
        layout.addWidget(folder_group)

        opts_group = QGroupBox("Options", self)
        opts_row = QHBoxLayout(opts_group)
        self._recursive_cb = QCheckBox("Include subfolders", opts_group)
        self._recursive_cb.setChecked(True)
        self._dryrun_cb = QCheckBox("Dry-run (preview only)", opts_group)
        self._dryrun_cb.setChecked(True)
        self._skip_cb = QCheckBox("Skip frames with no heading", opts_group)
        opts_row.addWidget(self._recursive_cb)
        opts_row.addWidget(self._dryrun_cb)
        opts_row.addWidget(self._skip_cb)
        opts_row.addStretch(1)
        layout.addWidget(opts_group)

        csv_group = QGroupBox("Sidecar CSV (optional)", self)
        csv_row = QHBoxLayout(csv_group)
        self._csv_edit = QLineEdit(csv_group)
        self._csv_edit.setPlaceholderText("Leave empty to skip; e.g. D:\\Exports\\attitude.csv")
        self._csv_btn = QPushButton("Browse...", csv_group)
        self._csv_btn.clicked.connect(self._on_browse_csv)
        csv_row.addWidget(self._csv_edit, 1)
        csv_row.addWidget(self._csv_btn)
        layout.addWidget(csv_group)

        controls = QHBoxLayout()
        self._start_btn = QPushButton("Start", self)
        self._start_btn.clicked.connect(self._on_start)
        self._cancel_btn = QPushButton("Cancel", self)
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._cancel_btn.setEnabled(False)
        controls.addWidget(self._start_btn)
        controls.addWidget(self._cancel_btn)
        controls.addStretch(1)
        layout.addLayout(controls)

        self._progress = QProgressBar(self)
        self._progress.setMaximum(1)
        self._progress.setValue(0)
        layout.addWidget(self._progress)

        self._log = QTextEdit(self)
        self._log.setReadOnly(True)
        layout.addWidget(self._log, 1)

        self._exiftool_exe = detect_exiftool()
        if self._exiftool_exe is None:
            self.statusBar().showMessage(
                "Ready. ExifTool NOT found - writing is disabled (dry-run only)."
            )
            self._append_log(
                "WARNING: ExifTool not found. You can preview (dry-run) but "
                "not write tags. Install ExifTool or it must be on PATH / "
                "at C:\\ExifTool\\."
            )
        else:
            self.statusBar().showMessage(f"Ready. ExifTool: {self._exiftool_exe}")
        self._worker: Optional[AttitudeWorker] = None

    @Slot()
    def _on_browse(self) -> None:
        start = self._folder_edit.text().strip() or os.path.expanduser("~")
        d = QFileDialog.getExistingDirectory(self, "Choose folder", start)
        if d:
            self._folder_edit.setText(d)

    @Slot()
    def _on_browse_csv(self) -> None:
        start = self._csv_edit.text().strip() or self._folder_edit.text().strip() or os.path.expanduser("~")
        f, _ = QFileDialog.getSaveFileName(self, "Sidecar CSV", start, "CSV (*.csv)")
        if f:
            self._csv_edit.setText(f)

    @Slot()
    def _on_start(self) -> None:
        raw = self._folder_edit.text().strip()
        if not raw:
            QMessageBox.warning(self, "No folder", "Choose a folder first.")
            return
        root = Path(raw).expanduser()
        if not root.exists() or not root.is_dir():
            QMessageBox.warning(self, "Bad folder", f"Not a folder:\n  {root}")
            return

        write = not self._dryrun_cb.isChecked()
        if write and self._exiftool_exe is None:
            QMessageBox.warning(
                self,
                "ExifTool required",
                "ExifTool was not found, so tags cannot be written.\n"
                "Re-run as dry-run, or install ExifTool.",
            )
            return

        csv_path: Optional[Path] = None
        csv_raw = self._csv_edit.text().strip()
        if csv_raw:
            csv_path = Path(csv_raw).expanduser()

        self._log.clear()
        self._progress.setMaximum(1)
        self._progress.setValue(0)
        self._set_inputs_enabled(False)

        self._worker = AttitudeWorker(
            root,
            recursive=self._recursive_cb.isChecked(),
            write=write,
            skip_no_heading=self._skip_cb.isChecked(),
            csv_path=csv_path,
            exiftool_exe=self._exiftool_exe,
            parent=self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.log_line.connect(self._append_log)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.start()
        self.statusBar().showMessage("Working...")

    @Slot()
    def _on_cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.statusBar().showMessage("Cancelling...")

    @Slot(int, int, str)
    def _on_progress(self, done: int, total: int, name: str) -> None:
        self._progress.setMaximum(max(total, 1))
        self._progress.setValue(min(done, total))
        if name:
            self.statusBar().showMessage(f"{done}/{total}: {name}")

    @Slot(str)
    def _on_failed(self, msg: str) -> None:
        self._append_log(f"ERROR: {msg}")
        QMessageBox.critical(self, "Tagging failed", msg)

    @Slot(object, bool)
    def _on_finished(self, stats: RunStats, write_mode: bool) -> None:
        self._set_inputs_enabled(True)
        self._worker = None
        mode = "Wrote" if write_mode else "Would write"
        summary = (
            f"{mode} attitude for {stats.with_heading + stats.without_heading} "
            f"frame(s): {stats.with_heading} with heading, "
            f"{stats.without_heading} without. "
            f"parsed={stats.parsed} unparsed={stats.unparsed} "
            f"gps_missing={stats.no_gps} unknown_serial={stats.unknown_serial}"
        )
        if write_mode:
            summary += f" | written={stats.written} failed={stats.failed}"
        self._append_log(summary)
        for path, why in stats.failures[:20]:
            self._append_log(f"  FAILED {path}: {why}")
        self.statusBar().showMessage(summary)

    def _set_inputs_enabled(self, enabled: bool) -> None:
        self._start_btn.setEnabled(enabled)
        self._cancel_btn.setEnabled(not enabled)
        self._folder_edit.setEnabled(enabled)
        self._browse_btn.setEnabled(enabled)
        self._csv_edit.setEnabled(enabled)
        self._csv_btn.setEnabled(enabled)
        self._recursive_cb.setEnabled(enabled)
        self._dryrun_cb.setEnabled(enabled)
        self._skip_cb.setEnabled(enabled)

    def _append_log(self, text: str) -> None:
        self._log.append(text)

    def set_folder(self, value: str) -> None:
        self._folder_edit.setText(value)


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO)
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    win = AttitudeWindow()
    if len(argv) > 1 and argv[1]:
        win.set_folder(argv[1])
    win.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
