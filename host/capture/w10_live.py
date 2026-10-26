"""Live w10 of one frame per sweep while recording (M0/M1/M3), without touching the recording path.

The acquisition thread owns the full-res slot for a moment before it hands it to the writer; at that
moment, for frame ``frame_in_sweep`` of each sweep, it offers the slot here. ``offer`` copies only the
five 1028x1028 ROIs (~10 MB, a few ms) and returns; one background thread scores them (~150 ms,
sec.54.2) and ``scored`` carries the result to the GUI for the preview overlay and the log.

Recording comes first, so this drops rather than waits:
* at most one score in flight across all cameras (a second offer while busy is skipped);
* nothing is copied when the slot pool is short of free slots (the writer is behind);
* a failure inside is logged and swallowed; the submit path never sees it;
* fail-safe: the pipeline calls ``note_drop`` whenever a recording frame is lost to backpressure
  (pool empty, work_q full, SDK callback queue full). The scorer then steps aside for the rest of
  that line -- whether or not the drop was its doing -- and says so in the log. It re-arms on the
  next line. Each score line also carries the copy and score times, so a ground run gives numbers.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from PySide6.QtCore import QObject, Signal

from .psf_w10 import crop_rois, frame_ok, score_rois_raw14

log = logging.getLogger(__name__)


class W10LiveScorer(QObject):
    scored = Signal(dict)   # stats of array_edges + the offer's meta (cam, line, sweep, frame, ts, focus)
    notice = Signal(str)    # fail-safe on/off messages for the GUI log

    def __init__(self, frame_in_sweep: int = 7, min_free_slots: int = 3, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.frame_in_sweep = int(frame_in_sweep)
        self.min_free_slots = int(min_free_slots)
        self._busy = threading.Lock()          # held from offer() until the score is emitted
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="w10-live")
        self.skipped_busy = 0
        self.skipped_pressure = 0
        self.skipped_disabled = 0
        self.scored_count = 0
        self.disabled_count = 0                # times the fail-safe fired
        self.disabled_line: Optional[int] = None   # line we are standing down on, None = armed
        self._last_line: Optional[int] = None  # line of the most recent offer (any outcome)
        self._closed = False

    # -- producer side (acquisition thread) --------------------------------------------------------
    def offer(self, slot_pool, slot_idx: int, meta: dict) -> bool:
        """Called by the acquisition thread while it still owns ``slot_idx``. Returns True if taken."""
        if self._closed:
            return False
        line = meta.get("line")
        self._last_line = line
        if self.disabled_line is not None:
            if line == self.disabled_line:
                self.skipped_disabled += 1
                return False
            self.disabled_line = None          # new line: re-arm
            self.notice.emit(f"live scoring back on (line {line})")
        try:
            free = slot_pool.free_q.qsize()
        except Exception:  # noqa: BLE001 -- qsize is not implemented everywhere
            free = self.min_free_slots
        if free < self.min_free_slots:
            self.skipped_pressure += 1
            return False
        if not self._busy.acquire(blocking=False):
            self.skipped_busy += 1
            return False
        try:
            t0 = time.perf_counter()
            rois = crop_rois(slot_pool.slot_view(int(slot_idx)), copy=True)
            meta = dict(meta, copy_ms=(time.perf_counter() - t0) * 1e3)
            self._pool.submit(self._run, rois, meta)
            return True
        except Exception:  # noqa: BLE001 -- never let the scorer hurt the submit path
            self._busy.release()
            log.debug("w10 live: offer failed", exc_info=True)
            return False

    def note_drop(self, cam: str, line: Optional[int] = None) -> None:
        """Pipeline: a recording frame was lost to backpressure. Stand down for the rest of the line.

        Safe from any thread, including the SDK callback: a comparison in the common case, one log
        line and one queued signal the first time per line. Ignored until the scorer has been offered
        at least one frame (a drop before that cannot be ours, and we would not know the line).
        """
        try:
            if line is None:
                line = self._last_line
            if self._closed or line is None or self.disabled_line == line:
                return
            self.disabled_line = line
            self.disabled_count += 1
            msg = (f"{cam} dropped a recording frame on line {line}; live scoring off for the rest of this "
                   f"line (scored {self.scored_count}, in flight: {self._busy.locked()})")
            log.warning("w10 live: %s", msg)
            self.notice.emit(msg)
        except Exception:  # noqa: BLE001
            log.debug("w10 live: note_drop failed", exc_info=True)

    # -- worker side ---------------------------------------------------------------------------------
    def _run(self, rois, meta: dict) -> None:
        try:
            t0 = time.perf_counter()
            stats = score_rois_raw14(rois, meta.get("focus"))
            stats["ok"] = frame_ok(stats)
            stats.update(meta)
            stats["score_ms"] = (time.perf_counter() - t0) * 1e3
            self.scored_count += 1
            self.scored.emit(stats)
        except Exception:  # noqa: BLE001
            log.warning("w10 live: scoring failed", exc_info=True)
        finally:
            self._busy.release()

    def shutdown(self) -> None:
        self._closed = True
        self._pool.shutdown(wait=True)


def format_score(s: dict) -> str:
    """One line for the log / overlay:
    ``cam2[2439008] L1 S51 F7  w10 5.31 px  (1832 edges, 5 ROI, enc 128612)  copy 3 ms, score 142 ms``."""
    enc = s.get("encoder")
    enc_s = f", enc {enc:.0f}" if isinstance(enc, (int, float)) and enc == enc else ""
    w = s.get("w10")
    w_s = f"w10 {w:.2f} px" if isinstance(w, (int, float)) and w == w else "w10 --"
    flag = "" if s.get("ok") else "  [gated: " + ("cloud" if (s.get("med_mean") or 0) > 22000 else "few edges") + "]"
    t_s = ""
    if "copy_ms" in s and "score_ms" in s:
        t_s = f"  copy {s['copy_ms']:.0f} ms, score {s['score_ms']:.0f} ms"
    return (f"{s.get('cam', '?')} L{s.get('line', '?')} S{s.get('sweep', '?')} F{s.get('frame', '?')}  {w_s}  "
            f"({s.get('n_edges', 0)} edges, {s.get('n_roi', 0)} ROI{enc_s}){flag}{t_s}")
