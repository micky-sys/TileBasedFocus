"""Batch reader: convert ``.bay14line`` files from up to 3 camera dirs to TIFF/JPEG.

Examples::

    python tools/bay14_reader_gui.py
    python tools/bay14_reader_gui.py E:\\run\\camA E:\\run\\camB E:\\run\\camC \\
        --out F:\\export_tifs

Uses :mod:`bay14_export_core` for mmap decode (same as ``bay14_to_tif``) and
optional linear 14→16 scaling before encode.
"""

from __future__ import annotations

import argparse
import logging
import re
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
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from bay14_export_core import (  # noqa: E402
    FILTER_REGISTRY,
    FrameFilter,
    count_line_frames,
    discover_lines,
    encode_jpeg,
    encode_tiff_rgb16,
    iter_line_frames,
    peek_line_meta,
    write_tiff_bayer_u16,
)

log = logging.getLogger(__name__)


def build_date() -> str:
    """Return a human-readable build/last-modified date (YYYY-MM-DD).

    When frozen by PyInstaller we use the executable's mtime (the moment
    the EXE was produced); running from source we fall back to this
    module's own mtime so the GUI always shows a meaningful date.
    """
    try:
        if getattr(sys, "frozen", False):
            target = Path(sys.executable)
        else:
            target = Path(__file__).resolve()
        return datetime.fromtimestamp(target.stat().st_mtime).strftime("%Y-%m-%d")
    except OSError:
        return "unknown"


def normalize_fs_path(raw: str) -> Path:
    """Strip quotes/whitespace and resolve *raw* for stable ``exists()`` / ``glob()``."""
    s = raw.strip().strip('"').strip("'").strip()
    p = Path(s).expanduser()
    try:
        return p.resolve(strict=False)
    except OSError:
        return p


def resolve_cam_label(line_path: Path, fallback_dir: Path) -> str:
    """Prefer ``camera_serial`` from the line header; else *fallback_dir* name."""
    try:
        meta = peek_line_meta(line_path)
        s = meta.get("camera_serial") or meta.get("serial")
        if s:
            return str(s)
    except Exception:
        log.debug("peek_line_meta failed for %s", line_path, exc_info=True)
    return fallback_dir.name


# Line-file stem layout produced by the capture writer, e.g.
#   Cam2439007_20260525_ParallaxTestRishon_L13_144545_p0
# Exports rewrite this into
#   Cam2439007_20260525_ParallaxTestRishon_144545_L13
# i.e. swap line/time and drop the per-part ``_p{N}`` suffix that only
# matters at the container level.
_LINE_STEM_RE = re.compile(
    r"^(?P<cam>Cam\d+)_(?P<date>\d{8})_(?P<project>.+?)"
    r"_L(?P<line>\d+)_(?P<hhmmss>\d{6})(?:_p\d+)?$"
)


def rewrite_export_stem(line_stem: str) -> str:
    """Rewrite a ``.bay14line`` stem into the export naming scheme.

    Returns ``{cam}_{date}_{project}_{hhmmss}_L{line}`` on a match, or the
    original *line_stem* unchanged if it does not parse (so weird inputs
    still round-trip through the exporter instead of being dropped).
    """
    m = _LINE_STEM_RE.match(line_stem)
    if not m:
        return line_stem
    return (
        f"{m.group('cam')}_{m.group('date')}_{m.group('project')}"
        f"_{m.group('hhmmss')}_L{m.group('line')}"
    )


def build_export_path(
    out_dir: Path,
    cam_label: str,
    line_path: Path,
    merged_meta: dict,
    extension: str,
) -> Path:
    stem = rewrite_export_stem(line_path.stem)
    sweep = merged_meta["sweep"]
    fin = merged_meta["frame_in_sweep"]
    safe_label = cam_label.replace("/", "_").replace("\\", "_")
    name = f"{stem}_S{sweep}_F{fin}.{extension}"
    return out_dir / safe_label / name


class ExportWorker(QThread):
    """Walk line files and encode each frame.

    Each job is a ``(fallback_dir, line_path, flip_lr)`` triple. When
    ``flip_lr`` is True every frame produced from that line file is
    horizontally mirrored before encode (Bayer-pattern label is
    re-derived in :func:`bay14_export_core.apply_fliplr_to_frame`).
    """

    progress = Signal(int, int, str)  # done, total, message
    log_line = Signal(str)
    failed = Signal(str)
    finished_ok = Signal(int, int, bool)  # done, total, cancelled

    def __init__(
        self,
        jobs: list[tuple[Path, Path, bool]],
        out_dir: Path,
        export_kind: str,
        jpeg_quality: int,
        filter_chain: list[FrameFilter],
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._jobs = jobs
        self._out_dir = Path(out_dir)
        self._export_kind = export_kind
        self._jpeg_quality = int(jpeg_quality)
        self._filter_chain = list(filter_chain)
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def _recovery_notice(self, msg: str) -> None:
        self.log_line.emit(msg)

    def run(self) -> None:
        cancelled = False
        done = 0
        total = 0
        try:
            total = sum(count_line_frames(lp) for _fd, lp, _flip in self._jobs)
            if total <= 0:
                names = [lp.name for _fd, lp, _flip in self._jobs]
                preview = ", ".join(names[:12])
                if len(names) > 12:
                    preview += ", …"
                self.failed.emit(
                    "No frames to export: every matched .bay14line file reports "
                    f"0 frames (stub/empty line files or unreadable index). "
                    f"Files: {preview or '(none)'}"
                )
                self.finished_ok.emit(0, 0, False)
                return

            for fallback_dir, line_path, flip_lr in self._jobs:
                cam_label = resolve_cam_label(line_path, fallback_dir)
                for merged_meta, arr in iter_line_frames(
                    line_path,
                    recovery_notice=self._recovery_notice,
                ):
                    if self._cancel.is_set():
                        cancelled = True
                        break
                    for filt in self._filter_chain:
                        arr = filt.apply(arr, merged_meta)

                    if self._export_kind == "jpeg":
                        out_path = build_export_path(
                            self._out_dir, cam_label, line_path, merged_meta, "jpg",
                        )
                        encode_jpeg(
                            out_path, arr, merged_meta,
                            quality=self._jpeg_quality, flip_lr=flip_lr,
                        )
                    elif self._export_kind == "tiff_rgb":
                        out_path = build_export_path(
                            self._out_dir, cam_label, line_path, merged_meta, "tif",
                        )
                        encode_tiff_rgb16(
                            out_path, arr, merged_meta, flip_lr=flip_lr,
                        )
                    else:
                        out_path = build_export_path(
                            self._out_dir, cam_label, line_path, merged_meta, "tif",
                        )
                        write_tiff_bayer_u16(
                            out_path, arr, merged_meta,
                            scale_14_to_16=True, flip_lr=flip_lr,
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


class ReaderWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._build_date = build_date()
        self.setWindowTitle(f"Bay14 line batch reader  (build {self._build_date})")
        self.resize(920, 640)

        self._cam_edits: list[QLineEdit] = []
        self._cam_browses: list[QPushButton] = []
        self._cam_flips: list[QCheckBox] = []
        self._summary_labels: list[QLabel] = []

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # --- Inputs ---
        in_group = QGroupBox("Camera directories (each scanned for .bay14line files)")
        in_layout = QVBoxLayout(in_group)
        for i in range(3):
            row = QHBoxLayout()
            lab = QLabel(f"Cam {i + 1}:")
            lab.setMinimumWidth(52)
            edit = QLineEdit()
            edit.setPlaceholderText(f"Optional path to camera {i + 1} folder…")
            btn = QPushButton("Browse…")
            flip_cb = QCheckBox("flip")
            flip_cb.setToolTip(
                "Mirror every frame from this camera horizontally before "
                "encode. For Bayer-raw exports the CFA pattern label "
                "(bayer_pattern / genicam_pixel_format) is updated to "
                "match (RG<->GR, BG<->GB) so the downstream demosaicer "
                "still aligns. Use it when this camera is mounted "
                "reversed relative to the others."
            )
            idx = i

            def _browse_slot(_checked: bool = False, j: int = idx) -> None:
                self._browse_cam(j)

            btn.clicked.connect(_browse_slot)
            edit.textChanged.connect(self._refresh_discovery)
            row.addWidget(lab)
            row.addWidget(edit, stretch=1)
            row.addWidget(btn)
            row.addWidget(flip_cb)
            in_layout.addLayout(row)
            self._cam_edits.append(edit)
            self._cam_browses.append(btn)
            self._cam_flips.append(flip_cb)

        pat_row = QHBoxLayout()
        pat_row.addWidget(QLabel("Glob pattern:"))
        self._pattern_edit = QLineEdit("*.bay14line")
        self._pattern_edit.setToolTip(
            "Examples: *.bay14line (files in this folder only), "
            "**/*.bay14line (all subfolders — same as Include subfolders).",
        )
        self._pattern_edit.textChanged.connect(self._refresh_discovery)
        pat_row.addWidget(self._pattern_edit, stretch=1)
        self._recursive_cb = QCheckBox("Include subfolders")
        self._recursive_cb.setToolTip(
            "Also search nested directories for line files "
            "(uses **/your-pattern).",
        )
        self._recursive_cb.toggled.connect(self._refresh_discovery)
        pat_row.addWidget(self._recursive_cb)
        in_layout.addLayout(pat_row)

        sum_grid = QGridLayout()
        for i in range(3):
            sl = QLabel("—")
            sl.setWordWrap(True)
            sum_grid.addWidget(QLabel(f"Summary {i + 1}:"), i, 0)
            sum_grid.addWidget(sl, i, 1)
            self._summary_labels.append(sl)
        in_layout.addLayout(sum_grid)

        root.addWidget(in_group)

        # --- Output ---
        out_group = QGroupBox("Output")
        out_form = QFormLayout(out_group)
        out_row = QHBoxLayout()
        self._out_edit = QLineEdit()
        self._out_edit.setPlaceholderText("Destination folder…")
        out_btn = QPushButton("Browse…")
        out_btn.clicked.connect(self._browse_out)
        out_row.addWidget(self._out_edit, stretch=1)
        out_row.addWidget(out_btn)
        out_form.addRow("Folder:", out_row)

        fmt_row = QHBoxLayout()
        self._fmt_tiff = QRadioButton("TIFF")
        self._fmt_jpeg = QRadioButton("JPEG")
        self._fmt_tiff.setChecked(True)
        # Same parent widget → Qt would treat all radios as one exclusive set,
        # clearing TIFF mode when picking Format=TIFF. Use explicit groups.
        for rb in (self._fmt_tiff, self._fmt_jpeg):
            rb.setAutoExclusive(False)
        fmt_row.addWidget(self._fmt_tiff)
        fmt_row.addWidget(self._fmt_jpeg)
        fmt_row.addStretch()
        out_form.addRow("Format:", fmt_row)

        self._format_group = QButtonGroup(self)
        self._format_group.setExclusive(True)
        self._format_group.addButton(self._fmt_tiff)
        self._format_group.addButton(self._fmt_jpeg)

        self._tiff_raw = QRadioButton("Raw Bayer uint16 (14→16 shift)")
        self._tiff_rgb = QRadioButton("Demosaiced RGB uint16 (classic TIFF)")
        self._tiff_raw.setChecked(True)
        for rb in (self._tiff_raw, self._tiff_rgb):
            rb.setAutoExclusive(False)
        tiff_box = QVBoxLayout()
        tiff_box.addWidget(self._tiff_raw)
        tiff_box.addWidget(self._tiff_rgb)
        out_form.addRow("TIFF mode:", tiff_box)

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
        out_form.addRow("", jq_row)

        root.addWidget(out_group)

        self._format_group.buttonClicked.connect(self._update_format_controls)
        self._update_format_controls()

        # --- Filters (placeholder) ---
        filt_group = QGroupBox("Filters (future)")
        filt_layout = QVBoxLayout(filt_group)
        self._filter_list = QListWidget()
        self._filter_list.setMinimumHeight(72)
        self._filter_list.setEnabled(False)
        filt_layout.addWidget(self._filter_list)
        add_row = QHBoxLayout()
        self._filter_combo = QComboBox()
        for name in sorted(FILTER_REGISTRY.keys()):
            self._filter_combo.addItem(name)
        self._filter_combo.setEnabled(False)
        self._filter_combo.setToolTip(
            "Filter chain UI is reserved — register filters in FILTER_REGISTRY "
            "and wire Add-filter in a later revision.",
        )
        add_btn = QPushButton("Add filter…")
        add_btn.setEnabled(False)
        add_btn.setToolTip(self._filter_combo.toolTip())
        add_row.addWidget(self._filter_combo)
        add_row.addWidget(add_btn)
        add_row.addStretch()
        filt_layout.addLayout(add_row)
        root.addWidget(filt_group)

        # --- Run ---
        run_row = QHBoxLayout()
        self._start_btn = QPushButton("Start export")
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setEnabled(False)
        self._start_btn.clicked.connect(self._on_start)
        self._cancel_btn.clicked.connect(self._on_cancel)
        run_row.addWidget(self._start_btn)
        run_row.addWidget(self._cancel_btn)
        run_row.addStretch()
        root.addLayout(run_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        root.addWidget(self._progress)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMinimumHeight(160)
        root.addWidget(self._log, stretch=1)

        self._worker: Optional[ExportWorker] = None

        build_lab = QLabel(f"Build: {self._build_date}")
        build_lab.setStyleSheet("color: gray;")
        self.statusBar().addPermanentWidget(build_lab)

    def set_cam_dir(self, index: int, path: str | Path) -> None:
        if 0 <= index < len(self._cam_edits):
            self._cam_edits[index].setText(str(path))

    def set_out_dir(self, path: str | Path) -> None:
        self._out_edit.setText(str(path))

    def _browse_cam(self, index: int) -> None:
        d = QFileDialog.getExistingDirectory(self, f"Cam {index + 1} folder")
        if d:
            self._cam_edits[index].setText(d)

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

    def _pattern(self) -> str:
        p = self._pattern_edit.text().strip()
        return p if p else "*.bay14line"

    def _recursive(self) -> bool:
        return self._recursive_cb.isChecked()

    def _discover_lines(self, cam_dir: Path) -> list[Path]:
        return discover_lines(cam_dir, self._pattern(), recursive=self._recursive())

    def _diagnose_no_matches(self) -> str:
        """Human-readable reasons no jobs were built (for message boxes)."""
        bits: list[str] = []
        pattern = self._pattern()
        rec = self._recursive()
        any_path = False
        for i, edit in enumerate(self._cam_edits):
            raw = edit.text().strip()
            if not raw:
                continue
            any_path = True
            p = normalize_fs_path(raw)
            if not p.exists():
                bits.append(
                    f"Cam {i + 1}: path does not exist.\n"
                    f"  You entered: {raw!r}\n"
                    f"  Resolved to: {p}",
                )
                continue
            if not p.is_dir():
                bits.append(
                    f"Cam {i + 1}: not a folder (maybe a file).\n  {p}",
                )
                continue
            found = self._discover_lines(p)
            tip = ""
            if not found:
                tip = (
                    "\n  • Turn on 'Include subfolders' if files are in a "
                    "nested folder.\n"
                    "  • This tool only reads `.bay14line` containers (not "
                    "single-frame `.bay14` or `.bay14sweep`)."
                )
            bits.append(
                f"Cam {i + 1}: {p}\n"
                f"  Matched {len(found)} file(s) with pattern {pattern!r} "
                f"({'recursive' if rec else 'this folder only'}).{tip}",
            )
        if not any_path:
            return "Enter at least one camera folder in Cam 1–3 (Browse or paste)."
        return "\n\n".join(bits)

    def _collect_jobs(self) -> list[tuple[Path, Path, bool]]:
        """Returns ``(fallback_dir, line_path, flip_lr)`` triples in the
        same iteration order as the camera rows, so per-row flip flags
        attach to the right folder's frames.
        """
        jobs: list[tuple[Path, Path, bool]] = []
        for edit, flip_cb in zip(self._cam_edits, self._cam_flips):
            raw = edit.text().strip()
            if not raw:
                continue
            p = normalize_fs_path(raw)
            if not p.is_dir():
                continue
            flip = flip_cb.isChecked()
            for line_path in self._discover_lines(p):
                jobs.append((p, line_path, flip))
        return jobs

    @Slot()
    def _refresh_discovery(self) -> None:
        pattern = self._pattern()
        rec = self._recursive()
        for i, edit in enumerate(self._cam_edits):
            raw = edit.text().strip()
            if not raw:
                self._summary_labels[i].setText("—")
                continue
            p = normalize_fs_path(raw)
            if not p.exists():
                self._summary_labels[i].setText("(path does not exist)")
                continue
            if not p.is_dir():
                self._summary_labels[i].setText("(not a directory)")
                continue
            lines = self._discover_lines(p)
            try:
                n_frames = sum(count_line_frames(lp) for lp in lines)
            except Exception as exc:
                self._summary_labels[i].setText(f"Error counting frames: {exc}")
                continue
            codecs = set()
            for lp in lines:
                try:
                    pm = peek_line_meta(lp)
                    codecs.add(str(pm.get("compression", "?")))
                except Exception:
                    codecs.add("?")
            cc = ", ".join(sorted(codecs))
            self._summary_labels[i].setText(
                f"{len(lines)} line file(s), {n_frames} frame(s); codec(s): {cc} "
                f"({'recursive' if rec else 'flat'})",
            )

    def _export_kind(self) -> str:
        if self._fmt_jpeg.isChecked():
            return "jpeg"
        if self._tiff_rgb.isChecked():
            return "tiff_rgb"
        return "tiff_raw"

    def _filter_instances(self) -> list[FrameFilter]:
        """Reserved for future QListWidget population."""
        return []

    @Slot()
    def _on_start(self) -> None:
        jobs = self._collect_jobs()
        out_raw = self._out_edit.text().strip()
        if not jobs:
            QMessageBox.warning(
                self,
                "Nothing to do",
                "No `.bay14line` files matched.\n\n"
                + self._diagnose_no_matches(),
            )
            return
        if not out_raw:
            QMessageBox.warning(self, "No output folder", "Choose a destination directory.")
            return
        out_dir = Path(out_raw)
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "Output folder", str(exc))
            return

        self._log.clear()
        self._append_log(f"Starting export: {len(jobs)} line file(s), kind={self._export_kind()}")

        self._start_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        for edit in self._cam_edits:
            edit.setEnabled(False)
        for cb in self._cam_flips:
            cb.setEnabled(False)
        self._pattern_edit.setEnabled(False)
        self._out_edit.setEnabled(False)

        worker = ExportWorker(
            jobs=jobs,
            out_dir=out_dir,
            export_kind=self._export_kind(),
            jpeg_quality=self._quality_slider.value(),
            filter_chain=self._filter_instances(),
            parent=self,
        )
        worker.progress.connect(self._on_progress)
        worker.log_line.connect(self._append_log)
        worker.failed.connect(self._on_failed)
        worker.finished_ok.connect(self._on_finished)
        worker.start()
        self._worker = worker

    @Slot()
    def _on_cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    @Slot(int, int, str)
    def _on_progress(self, done: int, total: int, msg: str) -> None:
        self._progress.setMaximum(max(total, 1))
        self._progress.setValue(min(done, total))
        if msg:
            self.statusBar().showMessage(msg)

    @Slot(str)
    def _on_failed(self, msg: str) -> None:
        self._append_log(f"ERROR: {msg}")
        QMessageBox.critical(self, "Export failed", msg)

    @Slot(int, int, bool)
    def _on_finished(self, done: int, total: int, cancelled: bool) -> None:
        self._worker = None
        suffix = " (cancelled)" if cancelled else ""
        self._append_log(f"Finished{suffix}: {done}/{total} frame(s).")
        self.statusBar().showMessage(f"Done{suffix}: {done}/{total}")
        self._reenable_inputs()

    def _reenable_inputs(self) -> None:
        self._start_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        for edit in self._cam_edits:
            edit.setEnabled(True)
        for cb in self._cam_flips:
            cb.setEnabled(True)
        self._pattern_edit.setEnabled(True)
        self._out_edit.setEnabled(True)

    def _append_log(self, text: str) -> None:
        self._log.append(text)


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "cam_dirs",
        nargs="*",
        help="Up to three camera directories (optional pre-fill).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional output directory pre-fill.",
    )
    args = parser.parse_args(argv)

    app = QApplication(sys.argv)
    win = ReaderWindow()
    dirs = list(args.cam_dirs)
    for i in range(min(3, len(dirs))):
        win.set_cam_dir(i, dirs[i])
    if args.out is not None:
        win.set_out_dir(args.out)
    win.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
