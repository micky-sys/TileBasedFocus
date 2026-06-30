"""Tiny PySide6 GUI for the export renamer.

Wraps :mod:`rename_exports_to_new_scheme` with a single folder picker, a
"recursive" / "dry-run" checkbox pair, an optional extension filter, and a
log pane that streams each rename. The actual work happens in a QThread so
the UI stays responsive on slow filesystems (e.g. Google Drive).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
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

from rename_exports_to_new_scheme import (  # noqa: E402
    iter_candidates,
    rename_one,
)

log = logging.getLogger(__name__)


class RenameWorker(QThread):
    """Background thread that walks the folder and renames files."""

    progress = Signal(int, int, str)  # done, total, message
    log_line = Signal(str)
    failed = Signal(str)
    finished_ok = Signal(int, int, int, bool)  # renamed, skipped, unchanged, cancelled

    def __init__(
        self,
        root: Path,
        *,
        recursive: bool,
        dry_run: bool,
        extensions: Optional[set[str]],
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._root = Path(root)
        self._recursive = recursive
        self._dry_run = dry_run
        self._extensions = extensions
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        renamed = 0
        skipped = 0
        unchanged = 0
        cancelled = False
        try:
            self.log_line.emit(f"Scanning {self._root} ...")
            candidates = iter_candidates(
                [self._root],
                recursive=self._recursive,
                extensions=self._extensions,
            )
            total = len(candidates)
            self.log_line.emit(f"Found {total} file(s) matching the old scheme.")
            if total == 0:
                self.finished_ok.emit(0, 0, 0, False)
                return

            for i, src in enumerate(candidates, start=1):
                if self._cancel.is_set():
                    cancelled = True
                    break
                status, dest = rename_one(src, dry_run=self._dry_run)
                if status in ("renamed", "would_rename"):
                    renamed += 1
                    if dest is not None:
                        verb = "Would rename" if self._dry_run else "Renamed"
                        self.log_line.emit(f"{verb}: {src.name} -> {dest.name}")
                elif status == "skipped_exists":
                    skipped += 1
                    dest_name = dest.name if dest is not None else "?"
                    self.log_line.emit(
                        f"SKIP (target already exists): {src.name} -> {dest_name}"
                    )
                else:
                    unchanged += 1
                self.progress.emit(i, total, src.name)

            self.finished_ok.emit(renamed, skipped, unchanged, cancelled)
        except Exception as exc:
            log.exception("rename worker failed")
            self.failed.emit(str(exc))
            self.finished_ok.emit(renamed, skipped, unchanged, cancelled)


class RenamerWindow(QMainWindow):
    """Single-window UI: folder picker + options + log pane."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Parallax export renamer")
        self.resize(820, 540)

        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        intro = QLabel(
            "Renames frame exports from the old layout\n"
            "    Cam{ID}_{date}_{project}_L{line}_{hhmmss}_p{N}_S{sweep}_F{frame}.{ext}\n"
            "into the new layout\n"
            "    Cam{ID}_{date}_{project}_{hhmmss}_L{line}_S{sweep}_F{frame}.{ext}\n"
            "(_p{N} is dropped, line/time swap). Files already in the new layout are left alone.",
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        folder_group = QGroupBox("Folder to rename under", self)
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
        opts_row.addWidget(self._recursive_cb)
        opts_row.addWidget(self._dryrun_cb)
        opts_row.addSpacing(20)
        opts_row.addWidget(QLabel("Extensions (optional, space-separated):", opts_group))
        self._ext_edit = QLineEdit(opts_group)
        self._ext_edit.setPlaceholderText("e.g. tif jpg")
        self._ext_edit.setFixedWidth(220)
        opts_row.addWidget(self._ext_edit)
        opts_row.addStretch(1)
        layout.addWidget(opts_group)

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

        self.statusBar().showMessage("Ready.")
        self._worker: Optional[RenameWorker] = None

    @Slot()
    def _on_browse(self) -> None:
        start = self._folder_edit.text().strip() or os.path.expanduser("~")
        d = QFileDialog.getExistingDirectory(self, "Choose folder", start)
        if d:
            self._folder_edit.setText(d)

    def _parse_extensions(self) -> Optional[set[str]]:
        raw = self._ext_edit.text().strip()
        if not raw:
            return None
        return {e.lower().lstrip(".") for e in raw.split() if e}

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

        self._log.clear()
        self._progress.setMaximum(1)
        self._progress.setValue(0)
        self._start_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._folder_edit.setEnabled(False)
        self._browse_btn.setEnabled(False)
        self._recursive_cb.setEnabled(False)
        self._dryrun_cb.setEnabled(False)
        self._ext_edit.setEnabled(False)

        self._worker = RenameWorker(
            root,
            recursive=self._recursive_cb.isChecked(),
            dry_run=self._dryrun_cb.isChecked(),
            extensions=self._parse_extensions(),
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
        QMessageBox.critical(self, "Rename failed", msg)

    @Slot(int, int, int, bool)
    def _on_finished(
        self, renamed: int, skipped: int, unchanged: int, cancelled: bool,
    ) -> None:
        self._start_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._folder_edit.setEnabled(True)
        self._browse_btn.setEnabled(True)
        self._recursive_cb.setEnabled(True)
        self._dryrun_cb.setEnabled(True)
        self._ext_edit.setEnabled(True)
        self._worker = None
        dry = self._dryrun_cb.isChecked()
        verb = "Would rename" if dry else "Renamed"
        suffix = " (cancelled)" if cancelled else ""
        summary = (
            f"{verb} {renamed} file(s); skipped {skipped} (target exists); "
            f"{unchanged} already in new format.{suffix}"
        )
        self._append_log(summary)
        self.statusBar().showMessage(summary)

    def _append_log(self, text: str) -> None:
        self._log.append(text)

    def set_folder(self, value: str) -> None:
        self._folder_edit.setText(value)


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO)
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    win = RenamerWindow()
    if len(argv) > 1 and argv[1]:
        win.set_folder(argv[1])
    win.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
