"""Offline viewer + batch TIFF exporter for captured .bay14 files.

Two things in one app:

1. **Viewer** -- pick a project folder, browse every ``.bay14`` file
   inside it (grouped by camera serial), open one, see the embedded
   header metadata + a rendered preview. Toggle between monochrome
   (raw Bayer luminance) and debayered colour. Brightness scaling so
   underexposed frames are still inspectable.

2. **Batch export** -- one click converts every ``.bay14`` under the
   currently-loaded project folder (all three cameras) to classic
   uncompressed uint16 TIFFs. Reuses the production unpacker from
   ``capture.raw_bay_writer`` so the bytes match exactly what
   ``tools/bay14_to_tif.py`` would produce on a single file.

The viewer subsamples 4x for fast interactive rendering. A 13400x9528
14-bit packed file unpacks in ~150-300 ms; rendering at native res
takes another ~300 ms for the imagecodecs / numpy debayer, so an
operator clicking through dozens of frames would otherwise stall.
``--full-res`` on the CLI disables subsampling.

Run it::

    py -3 tools/bay14_viewer.py
    py -3 tools/bay14_viewer.py D:\\my_project
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import (
    QObject,
    QSize,
    Qt,
    QThread,
    Signal,
    Slot,
)
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStatusBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

# Add ``host`` to sys.path so we can pull in the production unpacker
# + header parser. Keeping a single source of truth for the .bay14
# format means this viewer can never silently disagree with the
# writer about layout.
_HERE = Path(__file__).resolve().parent
_HOST = _HERE.parent / "host"
if str(_HOST) not in sys.path:
    sys.path.insert(0, str(_HOST))

from capture.bayer_demosaic import opencv_bayer_rgb_demosaic_code  # noqa: E402
from capture.raw_bay_writer import (  # noqa: E402
    COMPRESSION_NONE,
    SWEEP_HEADER_MAGIC,
    SWEEP_INDEX_ENTRY_SIZE,
    decompress_packed,
    parse_frame_chunk_header,
    parse_header,
    parse_sweep_header,
    parse_sweep_index,
    unpack_u14,
)

log = logging.getLogger("bay14_viewer")


# ---------------------------------------------------------------------------


# Default decimation for the interactive preview. ``cv2.cvtColor`` for a
# 13400x9528 frame takes ~300 ms; running that on every brightness
# slider tick would lock the GUI. 4x decimation drops it to ~20 ms,
# which feels instant.
PREVIEW_DECIMATE = 4

# Cap the "fit-to-window" pixmap size so resizing the window doesn't
# trigger a huge cv2.resize. We render at this max edge then let
# QLabel.setScaledContents handle the final stretch.
PREVIEW_MAX_EDGE_PX = 2000


@dataclass
class FrameRecord:
    """One frame entry surfaced in the file tree.

    For legacy ``.bay14`` per-frame files there's a 1:1 mapping
    between :attr:`path` and the displayed entry. For
    ``.bay14sweep`` batched files, one on-disk file shows up as N
    records (one per frame inside it); :attr:`frame_index_in_file`
    is the chunk index inside that sweep file (0-based) used by
    :func:`load_bay14` to find the right chunk to decode.
    """

    path: Path
    camera_serial: str
    sweep: Optional[int]
    frame: Optional[int]
    line: Optional[int]
    project: str
    date_str: str
    # 0-based index into the sweep-file's chunk list when this
    # record represents a frame inside a .bay14sweep. None for
    # plain .bay14 files (one frame per file -> no index needed).
    frame_index_in_file: Optional[int] = None

    def display(self) -> str:
        bits = [self.path.name]
        if self.sweep is not None and self.frame is not None:
            bits.append(f"(S{self.sweep} F{self.frame})")
        return "  ".join(bits)


# ---------------------------------------------------------------------------


def _parse_filename(path: Path) -> dict[str, Optional[str | int]]:
    """Best-effort extraction of fields from a filename.

    Recognised schemes (in priority order):

    * Sweep batch: ``Cam{SN}_{YYYYMMDD}_{project}_L{line}_S{sweep}.bay14sweep``
      (no per-frame suffix; one file holds K frames internally).
    * Per-frame: ``Cam{SN}_{YYYYMMDD}_{project}_L{line}_S{sweep}_{frame}.bay14``
    * Legacy per-frame: ``Cam{SN}_{project}_{YYYYMMDD}_L{line}_{frame}.bay14``
    """
    stem = path.stem  # filename without ``.bay14`` / ``.bay14sweep``
    parts = stem.split("_")
    out: dict[str, Optional[str | int]] = {
        "camera_serial": "",
        "project": "",
        "date": "",
        "line": None,
        "sweep": None,
        "frame": None,
    }
    if not parts or not parts[0].lower().startswith("cam"):
        return out
    out["camera_serial"] = parts[0][3:]

    # Sweep-file tail: ..._L{line}_S{sweep}  (no frame suffix)
    tail_sweep = parts[-2:] if len(parts) >= 2 else []
    is_sweep = (
        path.suffix.lower() == ".bay14sweep"
        and len(tail_sweep) == 2
        and tail_sweep[0].startswith("L")
        and tail_sweep[1].startswith("S")
    )
    if is_sweep:
        try:
            out["line"] = int(tail_sweep[0][1:])
            out["sweep"] = int(tail_sweep[1][1:])
        except ValueError:
            pass
        head = parts[1:-2]
        if len(head) >= 2 and head[0].isdigit() and len(head[0]) == 8:
            out["date"] = head[0]
            out["project"] = "_".join(head[1:])
        else:
            out["project"] = "_".join(head)
        return out

    # New scheme tail: ..._L{line}_S{sweep}_{frame}
    tail_new = parts[-3:] if len(parts) >= 3 else []
    is_new = (
        len(tail_new) == 3
        and tail_new[0].startswith("L")
        and tail_new[1].startswith("S")
        and tail_new[2].isdigit()
    )
    if is_new:
        try:
            out["line"] = int(tail_new[0][1:])
            out["sweep"] = int(tail_new[1][1:])
            out["frame"] = int(tail_new[2])
        except ValueError:
            pass
        head = parts[1:-3]
        if len(head) >= 2 and head[0].isdigit() and len(head[0]) == 8:
            out["date"] = head[0]
            out["project"] = "_".join(head[1:])
        else:
            out["project"] = "_".join(head)
        return out

    # Legacy scheme tail: ..._L{line}_{frame}
    tail_old = parts[-2:] if len(parts) >= 2 else []
    is_old = (
        len(tail_old) == 2
        and tail_old[0].startswith("L")
        and tail_old[1].isdigit()
    )
    if is_old:
        try:
            out["line"] = int(tail_old[0][1:])
            out["frame"] = int(tail_old[1])
        except ValueError:
            pass
        head = parts[1:-2]
        if len(head) >= 2 and head[-1].isdigit() and len(head[-1]) == 8:
            out["project"] = "_".join(head[:-1])
            out["date"] = head[-1]
        else:
            out["project"] = "_".join(head)
    return out


def scan_project_folder(folder: Path) -> list[FrameRecord]:
    """Recursively list every ``.bay14`` and ``.bay14sweep`` under *folder*.

    Per-frame ``.bay14`` files surface as one :class:`FrameRecord`
    each. Sweep ``.bay14sweep`` files surface as N records (one per
    frame inside the sweep) with ``frame_index_in_file`` populated
    so :func:`load_bay14` can extract the right chunk.

    A malformed sweep file (truncated header / index) is skipped
    with no records emitted; the operator sees the rest of the
    folder rather than crashing the viewer on one bad file.
    """
    records: list[FrameRecord] = []
    if not folder.is_dir():
        return records

    for path in sorted(folder.rglob("*.bay14")):
        info = _parse_filename(path)
        rec = FrameRecord(
            path=path,
            camera_serial=str(info.get("camera_serial") or ""),
            sweep=info.get("sweep") if isinstance(info.get("sweep"), int) else None,
            frame=info.get("frame") if isinstance(info.get("frame"), int) else None,
            line=info.get("line") if isinstance(info.get("line"), int) else None,
            project=str(info.get("project") or ""),
            date_str=str(info.get("date") or ""),
        )
        records.append(rec)

    for path in sorted(folder.rglob("*.bay14sweep")):
        info = _parse_filename(path)
        try:
            with open(path, "rb") as f:
                head = f.read(8192)
            sweep_meta, header_len, n = parse_sweep_header(head)
            with open(path, "rb") as f:
                f.seek(header_len)
                index_blob = f.read(n * SWEEP_INDEX_ENTRY_SIZE)
            index = parse_sweep_index(index_blob, n)
        except Exception:
            log.exception("scan: failed to read sweep file %s; skipping", path)
            continue
        # Carry the sweep-level filename meta down; per-frame frame
        # number comes from the chunk index entry.
        for chunk_pos, (f_in, _off, _payload, _unc) in enumerate(index):
            records.append(FrameRecord(
                path=path,
                camera_serial=str(info.get("camera_serial") or ""),
                sweep=info.get("sweep") if isinstance(info.get("sweep"), int) else None,
                frame=int(f_in),
                line=info.get("line") if isinstance(info.get("line"), int) else None,
                project=str(info.get("project") or ""),
                date_str=str(info.get("date") or ""),
                frame_index_in_file=chunk_pos,
            ))
    return records


# ---------------------------------------------------------------------------


def load_bay14(
    path: Path,
    *,
    frame_index_in_file: Optional[int] = None,
) -> tuple[dict, np.ndarray]:
    """Read a single frame off disk and return ``(metadata, frame_u16)``.

    Three on-disk layouts are accepted:

    * ``.bay14`` with embedded ``BAY14HDR`` (current per-frame).
    * ``.bay14`` legacy sidecar (``.bay14.json`` next to the file).
    * ``.bay14sweep`` -- batched, contains multiple frames; pass
      *frame_index_in_file* to pick which one (0-based chunk index
      from :func:`scan_project_folder`).

    The returned metadata for sweep files is a merge of sweep-level
    fields (camera, project, geometry, codec, ...) with the per-
    frame chunk fields (frame_in_sweep, capture_kind, gps, ...) so
    downstream display code doesn't have to know which layout the
    source file used.
    """
    raw = path.read_bytes()
    if raw[:8] == SWEEP_HEADER_MAGIC:
        if frame_index_in_file is None:
            frame_index_in_file = 0
        sweep_meta, header_len, n_frames = parse_sweep_header(
            raw[:max(64, len(raw))]
        )
        if not 0 <= frame_index_in_file < n_frames:
            raise IndexError(
                f"{path.name}: requested frame_index_in_file="
                f"{frame_index_in_file} but file only holds "
                f"{n_frames} frames"
            )
        index_blob = raw[
            header_len:header_len + n_frames * SWEEP_INDEX_ENTRY_SIZE
        ]
        index = parse_sweep_index(index_blob, n_frames)
        f_in, offset, payload_len, uncompressed = index[frame_index_in_file]
        # Per-chunk header gives us the per-frame meta we want to
        # surface alongside the sweep-level meta.
        chunk_blob = raw[offset:offset + 4096]
        chunk_meta, c_hdr_len, c_payload_len = parse_frame_chunk_header(
            chunk_blob
        )
        if c_payload_len != payload_len:
            raise ValueError(
                f"{path.name}: chunk @offset {offset} payload_len "
                f"mismatch (chunk header {c_payload_len} vs index "
                f"{payload_len})"
            )
        payload_start = offset + c_hdr_len
        payload = bytes(raw[payload_start:payload_start + payload_len])
        codec = str(sweep_meta.get("compression", COMPRESSION_NONE))
        packed = decompress_packed(
            payload, codec=codec, expected_size=uncompressed,
        )
        meta = dict(sweep_meta)
        meta.update(chunk_meta)
        meta["frame_in_sweep"] = int(f_in)
    elif raw[:8] == b"BAY14HDR":
        meta, header_len, data_len = parse_header(raw[:max(64, len(raw))])
        on_disk = bytes(raw[header_len:header_len + data_len])
        if len(on_disk) != data_len:
            raise ValueError(
                f"{path.name}: header says {data_len} on-disk bytes "
                f"but file has {len(on_disk)}"
            )
        codec = str(meta.get("compression", COMPRESSION_NONE))
        h = int(meta["height"])
        w = int(meta["width"])
        expected_pack = h * w * 7 // 4
        uncompressed = int(meta.get("uncompressed_size", expected_pack))
        packed = decompress_packed(on_disk, codec=codec,
                                   expected_size=uncompressed)
    else:
        sidecar = path.with_suffix(path.suffix + ".json")
        if not sidecar.exists():
            raise FileNotFoundError(
                f"{path.name}: no embedded header AND no sidecar at "
                f"{sidecar.name}"
            )
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
        packed = np.frombuffer(raw, dtype=np.uint8)

    h = int(meta["height"])
    w = int(meta["width"])
    n_pixels = h * w
    expected = n_pixels * 7 // 4
    if packed.size != expected:
        raise ValueError(
            f"{path.name}: packed size {packed.size} != expected {expected} "
            f"(geometry {h}x{w})"
        )
    arr = unpack_u14(packed, n_pixels).reshape(h, w)
    return meta, arr


# ---------------------------------------------------------------------------


def _maybe_decimate(arr_u16: np.ndarray, decimate: int) -> np.ndarray:
    """Bayer-aligned decimation. ``decimate`` of 1 is a no-op."""
    if decimate <= 1:
        return arr_u16
    # Always step by an even number so we keep one full Bayer quad per
    # output pixel block. A 14-bit Iron 661 uses pattern "RG"; if the
    # caller will debayer, the alignment matters.
    step = max(2, decimate) & ~1
    return arr_u16[::step, ::step]


def _bayer_to_rgb_u8(
    arr_u16: np.ndarray,
    brightness: float,
    *,
    bayer_meta: Optional[dict] = None,
) -> np.ndarray:
    """Debayer a Bayer uint16 array and scale to uint8 RGB.

    Uses cv2 if available, falls back to a tiny half-res naive
    debayer if cv2 is missing. ``brightness`` is a multiplicative
    gain applied after right-shifting to 8 bits.

    *bayer_meta* is embedded header keys (e.g. ``genicam_pixel_format``)
    passed through from :func:`load_bay14`.
    """
    try:
        import cv2

        code = opencv_bayer_rgb_demosaic_code(bayer_meta or {})
        rgb_u16 = cv2.cvtColor(arr_u16, code)
    except Exception:
        # Naive 2x2 quad -> RGB pixel. Halves resolution but is
        # dependency-free.
        h, w = arr_u16.shape
        h2 = h - (h % 2)
        w2 = w - (w % 2)
        a = arr_u16[:h2:2, :w2:2]   # R
        b = arr_u16[:h2:2, 1:w2:2]  # G
        c = arr_u16[1:h2:2, :w2:2]  # G
        d = arr_u16[1:h2:2, 1:w2:2]  # B
        rgb_u16 = np.empty((a.shape[0], a.shape[1], 3), dtype=np.uint16)
        rgb_u16[..., 0] = a
        rgb_u16[..., 1] = (b.astype(np.uint32) + c.astype(np.uint32)) // 2
        rgb_u16[..., 2] = d

    # 14-bit values live in the low bits. Shift to 8-bit then apply
    # brightness multiplier with saturation.
    base = (rgb_u16 >> 6).astype(np.float32)
    out = np.clip(base * brightness, 0, 255).astype(np.uint8)
    return out


def _bayer_to_mono_u8(arr_u16: np.ndarray, brightness: float) -> np.ndarray:
    """Render the raw Bayer mosaic directly as luminance (no debayer).

    This is the same path the live preview uses -- treats every pixel
    as a sample regardless of colour, which is what the operator
    actually needs to confirm exposure / focus / framing without
    paying for a debayer.
    """
    base = (arr_u16 >> 6).astype(np.float32)
    out = np.clip(base * brightness, 0, 255).astype(np.uint8)
    return out


def render_to_qimage(
    arr_u16: np.ndarray,
    *,
    color: bool,
    brightness: float,
    decimate: int,
    bayer_meta: Optional[dict] = None,
) -> QImage:
    """Build a QImage suitable for display from a unpacked uint16 frame."""
    sub = _maybe_decimate(arr_u16, decimate)

    if color:
        rgb = _bayer_to_rgb_u8(sub, brightness, bayer_meta=bayer_meta)
        rgb = np.ascontiguousarray(rgb)
        h, w, _ = rgb.shape
        return QImage(
            rgb.data, w, h, w * 3, QImage.Format.Format_RGB888,
        ).copy()
    gray = _bayer_to_mono_u8(sub, brightness)
    gray = np.ascontiguousarray(gray)
    h, w = gray.shape
    return QImage(
        gray.data, w, h, w, QImage.Format.Format_Grayscale8,
    ).copy()


# ---------------------------------------------------------------------------


class _RenderWorker(QThread):
    """Latest-wins worker that loads a .bay14 file and renders a QImage.

    Single-frame in flight at a time so the GUI never queues up a
    backlog when the user scrolls fast through the file list. Loading
    + unpacking dominates render time, so we cache the most recently
    loaded raw uint16 array; subsequent re-renders (brightness slider,
    mono/color toggle) skip the load step.
    """

    frame_ready = Signal(QImage, dict)
    failed = Signal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._pending: Optional[dict] = None
        # Cache key is (path, frame_index_in_file) so two records
        # backed by the same .bay14sweep file but pointing at
        # different frames don't collide.
        self._cache_key: Optional[tuple[Path, Optional[int]]] = None
        self._cache_arr: Optional[np.ndarray] = None
        self._cache_meta: Optional[dict] = None
        self._running = True

    def submit(
        self,
        *,
        path: Path,
        color: bool,
        brightness: float,
        decimate: int,
        frame_index_in_file: Optional[int] = None,
    ) -> None:
        with self._cond:
            self._pending = {
                "path": path,
                "color": color,
                "brightness": brightness,
                "decimate": decimate,
                "frame_index_in_file": frame_index_in_file,
            }
            self._cond.notify()

    def stop_gracefully(self) -> None:
        with self._cond:
            self._running = False
            self._cond.notify_all()

    def run(self) -> None:
        while True:
            with self._cond:
                while self._running and self._pending is None:
                    self._cond.wait(timeout=0.5)
                if not self._running:
                    return
                req = self._pending
                self._pending = None

            if req is None:
                continue
            try:
                self._handle(req)
            except Exception as exc:
                log.exception("render failed: %s", exc)
                self.failed.emit(f"{req['path'].name}: {exc}")

    def _handle(self, req: dict) -> None:
        path: Path = req["path"]
        frame_idx: Optional[int] = req.get("frame_index_in_file")
        key = (path, frame_idx)
        if key != self._cache_key:
            t0 = time.perf_counter()
            meta, arr = load_bay14(path, frame_index_in_file=frame_idx)
            self._cache_key = key
            self._cache_arr = arr
            self._cache_meta = meta
            log.info(
                "loaded %s%s in %.0f ms (%dx%d uint16)",
                path.name,
                f" [frame#{frame_idx}]" if frame_idx is not None else "",
                (time.perf_counter() - t0) * 1000.0,
                arr.shape[0],
                arr.shape[1],
            )
        assert self._cache_arr is not None
        assert self._cache_meta is not None

        t1 = time.perf_counter()
        qimg = render_to_qimage(
            self._cache_arr,
            color=req["color"],
            brightness=req["brightness"],
            decimate=req["decimate"],
            bayer_meta=self._cache_meta,
        )
        log.debug(
            "rendered %s in %.0f ms (color=%s, decimate=%d)",
            path.name,
            (time.perf_counter() - t1) * 1000.0,
            req["color"],
            req["decimate"],
        )
        self.frame_ready.emit(qimg, dict(self._cache_meta))


# ---------------------------------------------------------------------------


class _ExportWorker(QThread):
    """Walks a list of frame records, writes each as classic uint16 TIFF.

    Accepts both per-frame .bay14 records and per-frame-inside-sweep
    records (where multiple records share the same source path and
    differ only by ``frame_index_in_file``). The output filename
    pattern includes the per-sweep frame number suffix in the latter
    case so two records from the same sweep file land in different
    .tif files instead of overwriting each other.
    """

    progress = Signal(int, int, str)  # done, total, current_basename
    failed = Signal(str)
    finished_ok = Signal(int, int)  # ok_count, total

    def __init__(
        self,
        records: list["FrameRecord"],
        out_dir: Optional[Path],
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._records = list(records)
        self._out_dir = out_dir
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            import tifffile
        except ImportError as exc:
            self.failed.emit(
                f"tifffile not installed: {exc}. Install with `pip install tifffile`."
            )
            return

        total = len(self._records)
        ok = 0
        for i, rec in enumerate(self._records):
            if self._cancel:
                break
            p = rec.path
            self.progress.emit(i, total, p.name)
            try:
                meta, arr = load_bay14(
                    p, frame_index_in_file=rec.frame_index_in_file,
                )
                # For sweep-file records, append ``_F{frame}`` so two
                # frames from the same .bay14sweep don't clobber the
                # same output .tif. For plain .bay14 the suffix is
                # empty and naming is unchanged from before.
                if rec.frame_index_in_file is not None and rec.frame is not None:
                    out_stem = f"{p.stem}_F{rec.frame}"
                else:
                    out_stem = p.stem
                if self._out_dir is None:
                    out_path = p.with_name(out_stem + ".tif")
                else:
                    self._out_dir.mkdir(parents=True, exist_ok=True)
                    out_path = self._out_dir / (out_stem + ".tif")
                description = json.dumps(meta, separators=(",", ":"))
                tifffile.imwrite(
                    str(out_path),
                    arr,
                    photometric="minisblack",
                    compression=None,
                    bigtiff=False,
                    description=description,
                )
                ok += 1
            except Exception as exc:
                log.exception("export failed for %s: %s", p, exc)
        self.progress.emit(total, total, "")
        self.finished_ok.emit(ok, total)


# ---------------------------------------------------------------------------


class ViewerWindow(QMainWindow):
    """Top-level offline viewer + exporter window."""

    def __init__(self, initial_folder: Optional[Path] = None) -> None:
        super().__init__()
        self.setWindowTitle("Bay14 Viewer")
        self.resize(1400, 900)

        self._folder: Optional[Path] = None
        self._records: list[FrameRecord] = []
        self._current_record: Optional[FrameRecord] = None
        self._brightness = 1.0
        self._decimate = PREVIEW_DECIMATE
        self._color = False
        self._export_worker: Optional[_ExportWorker] = None

        self._render = _RenderWorker(self)
        self._render.frame_ready.connect(self._on_frame_ready)
        self._render.failed.connect(self._on_render_failed)
        self._render.start()

        self._build_ui()
        self.setStatusBar(QStatusBar(self))
        self.statusBar().showMessage("Pick a project folder to start.")

        if initial_folder is not None:
            self._load_folder(initial_folder)

    # ── UI construction ─────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        outer.addLayout(self._build_top_bar())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_left_pane())
        splitter.addWidget(self._build_right_pane())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 1080])
        outer.addWidget(splitter, 1)

    def _build_top_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._folder_label = QLabel("(no folder)")
        self._folder_label.setStyleSheet("font-family: Consolas, monospace;")
        self._folder_label.setMinimumWidth(420)

        pick_btn = QPushButton("Open project folder…")
        pick_btn.clicked.connect(self._on_pick_folder_clicked)

        export_btn = QPushButton("Export all to TIFF…")
        export_btn.setToolTip(
            "Convert every .bay14 in the current project folder to "
            "uncompressed uint16 TIFF (across all cameras)."
        )
        export_btn.clicked.connect(self._on_export_clicked)
        self._export_btn = export_btn

        row.addWidget(QLabel("Folder:"))
        row.addWidget(self._folder_label, 1)
        row.addWidget(pick_btn)
        row.addWidget(export_btn)
        return row

    def _build_left_pane(self) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["File"])
        self._tree.setRootIsDecorated(True)
        self._tree.itemSelectionChanged.connect(self._on_tree_selection)
        layout.addWidget(self._tree, 1)

        self._counts_label = QLabel("0 files")
        self._counts_label.setStyleSheet(
            "color:#888; font-family: Consolas, monospace;"
        )
        layout.addWidget(self._counts_label)
        return wrap

    def _build_right_pane(self) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        layout.addLayout(self._build_render_controls())

        self._image_label = QLabel("Open a project folder, then pick a file.")
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setStyleSheet(
            "background:#101010; color:#888; font-family:Consolas, monospace;"
        )
        self._image_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._image_label.setMinimumSize(QSize(640, 480))
        layout.addWidget(self._image_label, 1)

        self._meta_label = QLabel("")
        self._meta_label.setStyleSheet(
            "font-family: Consolas, monospace; color:#aaa;"
        )
        self._meta_label.setWordWrap(True)
        self._meta_label.setMinimumHeight(60)
        layout.addWidget(self._meta_label)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        return wrap

    def _build_render_controls(self) -> QHBoxLayout:
        row = QHBoxLayout()

        # Mono / colour toggle.
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._mono_btn = QRadioButton("Mono (raw Bayer)")
        self._mono_btn.setChecked(True)
        self._mono_btn.toggled.connect(
            lambda on: on and self._set_color(False)
        )
        self._color_btn = QRadioButton("Colour (debayer RGB)")
        self._color_btn.toggled.connect(
            lambda on: on and self._set_color(True)
        )
        self._mode_group.addButton(self._mono_btn)
        self._mode_group.addButton(self._color_btn)
        row.addWidget(self._mono_btn)
        row.addWidget(self._color_btn)
        row.addSpacing(12)

        # Brightness multiplier 0.25 .. 16.0 (log slider scale).
        row.addWidget(QLabel("Brightness:"))
        self._brightness_slider = QSlider(Qt.Orientation.Horizontal)
        self._brightness_slider.setMinimum(-16)
        self._brightness_slider.setMaximum(16)
        self._brightness_slider.setValue(0)
        self._brightness_slider.setMaximumWidth(220)
        self._brightness_slider.setToolTip(
            "Linear brightness gain applied after right-shifting from "
            "14 to 8 bits. 0 = 1.0x, +n = ~1.18^n, -n = ~1/1.18^n."
        )
        self._brightness_slider.valueChanged.connect(self._on_brightness_changed)
        self._brightness_label = QLabel("1.00x")
        self._brightness_label.setMinimumWidth(54)
        row.addWidget(self._brightness_slider)
        row.addWidget(self._brightness_label)

        row.addSpacing(12)
        # Full-resolution toggle.
        self._fullres_check = QCheckBox("Full-res render (slower)")
        self._fullres_check.setToolTip(
            "Disable 4x decimation. Each render takes 200-500 ms vs ~20 ms "
            "decimated. Useful for inspecting fine focus / pixel-level noise."
        )
        self._fullres_check.toggled.connect(self._on_fullres_toggled)
        row.addWidget(self._fullres_check)

        row.addStretch(1)
        return row

    # ── Folder + tree ───────────────────────────────────────────────────

    def _on_pick_folder_clicked(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Pick project folder",
            str(self._folder) if self._folder else "",
        )
        if path:
            self._load_folder(Path(path))

    def _load_folder(self, folder: Path) -> None:
        self.statusBar().showMessage(f"Scanning {folder}…")
        QApplication.processEvents()
        records = scan_project_folder(folder)
        self._folder = folder
        self._records = records
        self._folder_label.setText(str(folder))
        self._populate_tree(records)
        self._counts_label.setText(
            f"{len(records)} files across "
            f"{len({r.camera_serial for r in records if r.camera_serial})} "
            "cameras"
        )
        self.statusBar().showMessage(
            f"{len(records)} .bay14 files in {folder}", 4000
        )

    def _populate_tree(self, records: list[FrameRecord]) -> None:
        """Group records by camera serial, then by line."""
        self._tree.clear()
        # Group by serial (camera) -> by line -> ordered files.
        by_cam: dict[str, dict[Optional[int], list[FrameRecord]]] = {}
        for rec in records:
            cam = rec.camera_serial or "(unknown camera)"
            by_cam.setdefault(cam, {}).setdefault(rec.line, []).append(rec)

        for cam in sorted(by_cam):
            cam_node = QTreeWidgetItem([f"Cam {cam} ({sum(len(v) for v in by_cam[cam].values())} files)"])
            cam_node.setData(0, Qt.ItemDataRole.UserRole, None)
            self._tree.addTopLevelItem(cam_node)
            for line in sorted(by_cam[cam], key=lambda x: (x is None, x or 0)):
                line_label = f"L{line}" if line is not None else "(no line)"
                line_node = QTreeWidgetItem([
                    f"{line_label} ({len(by_cam[cam][line])} files)"
                ])
                line_node.setData(0, Qt.ItemDataRole.UserRole, None)
                cam_node.addChild(line_node)
                # Sort within a line by sweep then frame so the rows
                # browse in capture order.
                ordered = sorted(
                    by_cam[cam][line],
                    key=lambda r: (
                        r.sweep if r.sweep is not None else 1_000_000,
                        r.frame if r.frame is not None else 1_000_000,
                        r.path.name,
                    ),
                )
                for rec in ordered:
                    item = QTreeWidgetItem([rec.display()])
                    item.setData(0, Qt.ItemDataRole.UserRole, rec)
                    line_node.addChild(item)
            cam_node.setExpanded(True)

    def _on_tree_selection(self) -> None:
        items = self._tree.selectedItems()
        if not items:
            return
        rec = items[0].data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(rec, FrameRecord):
            return
        self._current_record = rec
        self._submit_render()

    def _submit_render(self) -> None:
        rec = self._current_record
        if rec is None:
            return
        self._render.submit(
            path=rec.path,
            color=self._color,
            brightness=self._brightness,
            decimate=1 if self._fullres_check.isChecked() else self._decimate,
            frame_index_in_file=rec.frame_index_in_file,
        )
        # Show ``filename [Fk]`` for frames inside a sweep file so
        # the user knows which chunk of a .bay14sweep is loading.
        suffix = (f" [F{rec.frame}]"
                  if rec.frame_index_in_file is not None else "")
        self.statusBar().showMessage(f"Loading {rec.path.name}{suffix}…")

    # ── Render-control slots ────────────────────────────────────────────

    def _set_color(self, on: bool) -> None:
        self._color = bool(on)
        self._submit_render()

    def _on_brightness_changed(self, slider_val: int) -> None:
        # Slider runs -16 .. +16. Map to a multiplicative gain in
        # roughly 0.07 .. 14.0 with 1.0 at zero.
        gain = float(1.18 ** slider_val)
        self._brightness = gain
        self._brightness_label.setText(f"{gain:.2f}x")
        self._submit_render()

    def _on_fullres_toggled(self, _on: bool) -> None:
        self._submit_render()

    # ── Render slots ────────────────────────────────────────────────────

    @Slot(QImage, dict)
    def _on_frame_ready(self, qimg: QImage, meta: dict) -> None:
        if qimg.isNull():
            return
        # Cap the rendered max edge so massive full-res frames don't
        # produce a >4k pixmap and stall the QLabel scaler.
        target_w = min(qimg.width(), PREVIEW_MAX_EDGE_PX)
        target_h = min(qimg.height(), PREVIEW_MAX_EDGE_PX)
        if (target_w, target_h) != (qimg.width(), qimg.height()):
            qimg = qimg.scaled(
                target_w, target_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        pix = QPixmap.fromImage(qimg).scaled(
            self._image_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image_label.setPixmap(pix)
        self._meta_label.setText(self._format_meta(meta))
        rec = self._current_record
        if rec is not None:
            self.statusBar().showMessage(rec.path.name, 4000)

    def _on_render_failed(self, msg: str) -> None:
        self._image_label.setText(f"Render failed: {msg}")
        self.statusBar().showMessage(f"Failed: {msg}")

    def _format_meta(self, meta: dict) -> str:
        """One-line + multi-line view of the embedded JSON metadata."""
        lines: list[str] = []
        sweep = meta.get("sweep")
        frame = meta.get("frame_in_sweep")
        line = meta.get("line")
        cam = meta.get("camera_serial")
        proj = meta.get("project")
        ts = meta.get("acquire_timestamp")
        head_bits = []
        if cam:
            head_bits.append(f"Cam {cam}")
        if proj:
            head_bits.append(f"project={proj}")
        if line is not None:
            head_bits.append(f"L{line}")
        if sweep is not None and frame is not None:
            head_bits.append(f"S{sweep} F{frame}")
        if meta.get("width") and meta.get("height"):
            head_bits.append(f"{meta['width']}x{meta['height']}")
        if meta.get("bit_depth"):
            head_bits.append(f"{meta['bit_depth']}-bit")
        if ts:
            head_bits.append(f"@{ts}")
        lines.append("  ".join(head_bits))

        gps = meta.get("gps")
        if isinstance(gps, dict) and any(
            gps.get(k) is not None for k in ("latitude_deg", "longitude_deg")
        ):
            lat = gps.get("latitude_deg")
            lon = gps.get("longitude_deg")
            alt = gps.get("altitude_m")
            lines.append(
                f"GPS: lat={lat}  lon={lon}  alt={alt}"
            )
        return "\n".join(lines)

    # ── Export ──────────────────────────────────────────────────────────

    def _on_export_clicked(self) -> None:
        if not self._records:
            QMessageBox.information(
                self, "Nothing to export",
                "Open a project folder containing .bay14 files first.",
            )
            return
        out_dir_str = QFileDialog.getExistingDirectory(
            self, "Pick output folder for TIFFs",
            str(self._folder) if self._folder else "",
        )
        if not out_dir_str:
            return
        out_dir = Path(out_dir_str)
        if self._export_worker is not None and self._export_worker.isRunning():
            QMessageBox.information(
                self, "Already exporting",
                "An export is already running.",
            )
            return
        worker = _ExportWorker(list(self._records), out_dir, self)
        worker.progress.connect(self._on_export_progress)
        worker.failed.connect(self._on_export_failed)
        worker.finished_ok.connect(self._on_export_done)
        worker.finished.connect(worker.deleteLater)
        self._export_worker = worker
        self._progress.setRange(0, len(paths))
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._export_btn.setEnabled(False)
        worker.start()

    @Slot(int, int, str)
    def _on_export_progress(self, done: int, total: int, basename: str) -> None:
        self._progress.setValue(done)
        if basename:
            self.statusBar().showMessage(f"Exporting {done}/{total}: {basename}")

    @Slot(int, int)
    def _on_export_done(self, ok: int, total: int) -> None:
        self._progress.setVisible(False)
        self._export_btn.setEnabled(True)
        self.statusBar().showMessage(
            f"Exported {ok}/{total} TIFFs.", 8000
        )
        QMessageBox.information(
            self, "Export complete",
            f"Wrote {ok} of {total} TIFF files.",
        )

    @Slot(str)
    def _on_export_failed(self, msg: str) -> None:
        self._progress.setVisible(False)
        self._export_btn.setEnabled(True)
        QMessageBox.critical(self, "Export failed", msg)

    # ── Lifecycle ───────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # noqa: N802 -- Qt naming
        try:
            self._render.stop_gracefully()
            self._render.wait(2000)
        except Exception:
            pass
        if self._export_worker is not None:
            try:
                self._export_worker.cancel()
                self._export_worker.wait(2000)
            except Exception:
                pass
        super().closeEvent(event)


# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "folder", nargs="?", default=None,
        help="Optional project folder to open at startup.",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        help="Python logging level (DEBUG/INFO/WARNING)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    app = QApplication(sys.argv)
    win = ViewerWindow(initial_folder=Path(args.folder) if args.folder else None)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
