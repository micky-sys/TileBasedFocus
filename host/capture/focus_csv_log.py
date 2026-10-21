"""CSV log of manual-backlash focus telemetry for the Iron 661 capture app.

One row per locked-tile focus frame while the operator creeps focus: the
``timestamp, frame, encoder, focus_score`` needed to plot the score-vs-encoder
curve offline (its forward/back hysteresis is the gear backlash). Files land in
the same ``Session logs`` folder as :class:`session_log.SessionLogger`, named
``Iron661_focus_<YYYYMMDD_HHMMSS>.csv``.

Never fatal: I/O errors are swallowed and reported once to stderr -- a logging
hiccup must not take down capture.
"""

from __future__ import annotations

import csv
import datetime as _dt
import os
import sys
import threading
from typing import Optional

from .session_log import default_log_dir

CSV_HEADER = ["timestamp", "frame", "encoder", "focus_score"]


class FocusCsvLogger:
    """Append-only, flush-per-row CSV of focus telemetry. One instance per
    logging session; construction opens the file and writes the header, and
    :meth:`write_row` appends until :meth:`close`."""

    def __init__(self, log_dir: Optional[str] = None, header=CSV_HEADER, prefix: str = "Iron661_focus") -> None:
        """``header`` / ``prefix`` let another telemetry stream (the w10 autofocus run) reuse the
        same folder, naming and never-fatal I/O; rows then go through :meth:`write`."""
        self._lock = threading.Lock()
        self._fh = None
        self._writer = None
        self.path: Optional[str] = None
        self.start_time = _dt.datetime.now()

        directory = log_dir or default_log_dir()
        stamp = self.start_time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(directory, f"{prefix}_{stamp}.csv")
        try:
            os.makedirs(directory, exist_ok=True)
            self._fh = open(path, "w", encoding="utf-8", newline="")
            self._writer = csv.writer(self._fh)
            self._writer.writerow(list(header))
            self._fh.flush()
            self.path = path
        except Exception as exc:  # pragma: no cover - I/O environment dependent
            print(f"FocusCsvLogger: could not open {path!r}: {exc!r}", file=sys.stderr)
            self._fh = None
            self._writer = None

    def write_row(self, timestamp: str, frame: int, encoder, focus_score: float) -> None:
        """Append one telemetry row and flush (so a crash keeps the tail)."""
        self.write([timestamp, int(frame), encoder, float(focus_score)])

    def write(self, row) -> None:
        """Append one row of the instance's ``header`` and flush."""
        with self._lock:
            w = self._writer
            if w is None:
                return
            try:
                w.writerow(list(row))
                self._fh.flush()
            except Exception as exc:  # pragma: no cover - I/O environment dependent
                print(f"FocusCsvLogger: write failed: {exc!r}", file=sys.stderr)

    def close(self) -> None:
        with self._lock:
            if self._fh is None:
                return
            try:
                self._fh.close()
            except Exception:
                pass
            finally:
                self._fh = None
                self._writer = None
