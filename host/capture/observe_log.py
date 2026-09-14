"""CSV sink for OBSERVE runs: one row per tracked patch per frame.

OBSERVE watches several patches at a fixed lens position to answer a question a normal
AF run cannot: when two patches are measured on the SAME frame at the SAME focus, why
does the newer one score ~1.6x the older one? Age and frame position are perfectly
confounded on a forward-scrolling scene -- a patch travels at a constant rate, so its
age IS its height -- so the rows carry both, and the scene is run in each direction to
tell them apart.

Streams rather than accumulating: a 90 s observation at 3 fps with 3 patches is ~800
rows, which is small, but the writes happen on the frame path and buffering them until
the end would lose everything if the run is killed.
"""
from __future__ import annotations

import csv
import datetime as _dt
import logging
import os
from typing import Optional

from .session_log import default_log_dir

log = logging.getLogger(__name__)

COLUMNS = [
    "frame", "t_s", "pos_counts", "raw_counts",
    "oid", "born_frame", "age_frames",
    "x0", "y0", "x1", "y1", "y_centre",
    "score", "track_response",
]


class ObserveLog:
    """Append-only CSV for one observation run.

    Disables itself on the first OSError instead of raising. Learned from the OrthoSim
    ground-truth logger (HANDOFF error 6): it wrote to a Google Drive mount, the flush
    raised Errno 22, the exception escaped into the repaint loop, and the display froze
    for 12 hours while the simulation kept running underneath. A diagnostic that can
    take down the thing it is measuring is worse than no diagnostic.
    """

    def __init__(self, csv_dir: Optional[str] = None) -> None:
        self._dir = csv_dir or default_log_dir()
        self._fh = None
        self._w = None
        self.path: Optional[str] = None
        self.rows = 0

    def open(self, meta: Optional[dict] = None) -> Optional[str]:
        stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(self._dir, f"Iron661_observe_{stamp}.csv")
        try:
            os.makedirs(self._dir, exist_ok=True)
            self._fh = open(path, "w", newline="", encoding="utf-8")
            self._w = csv.writer(self._fh)
            for k, v in sorted((meta or {}).items()):
                self._w.writerow([f"# {k}", v])
            self._w.writerow(COLUMNS)
            self._fh.flush()
        except OSError as exc:
            log.warning("observe log: cannot open %s (%s) -- not recording", path, exc)
            self._fh = self._w = None
            return None
        self.path = path
        return path

    def write(self, row: dict) -> None:
        if self._w is None:
            return
        x0, y0, x1, y1 = row["roi"]
        try:
            self._w.writerow([
                row["frame"], f"{row['t']:.3f}",
                f"{row['pos_counts']:.1f}", f"{row['raw_counts']:.1f}",
                row["oid"], row["born_frame"], row["age_frames"],
                x0, y0, x1, y1, f"{row['y_centre']:.1f}",
                f"{row['score']:.6g}", f"{row['track_response']:.4f}",
            ])
            self.rows += 1
            if self.rows % 50 == 0:
                self._fh.flush()
        except OSError as exc:
            log.warning("observe log: write failed (%s) -- disabling after %d rows",
                        exc, self.rows)
            self.close()

    def close(self) -> None:
        fh, self._fh, self._w = self._fh, None, None
        if fh is None:
            return
        try:
            fh.close()
        except OSError:
            pass
