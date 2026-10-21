"""Hardware-free check of the live w10 scorer (host/capture/w10_live.py).

A fake slot pool serves one synthetic full-res frame. Asserts: the live path's score equals array_edges on
the same frame; an offer while a score is in flight is skipped; an offer under pool pressure is skipped;
the result arrives through the Qt signal with the offer's metadata and copy/score times; a recording
drop (note_drop) stands the scorer down for that line and a new line re-arms it.

    py -3 tools/test_w10_live.py
"""
import os, queue, sys, time
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "host"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication  # noqa: E402
from capture.psf_w10 import array_edges  # noqa: E402
from capture.w10_live import W10LiveScorer, format_score  # noqa: E402

H, W = 9528, 13400


def synthetic_frame(seed=0):
    """Blocky scene with blurred step edges (sigma 1.2) and noise, uint16 at 16-bit scale."""
    import cv2
    rng = np.random.default_rng(seed)
    small = rng.integers(8000, 40000, size=(H // 64, W // 64)).astype(np.float32)
    img = cv2.resize(small, (W, H), interpolation=cv2.INTER_NEAREST)
    img = cv2.GaussianBlur(img, (0, 0), 1.2)
    img += rng.normal(0, 150, size=img.shape).astype(np.float32)
    return np.clip(img, 0, 65535).astype(np.uint16)


class FakePool:
    def __init__(self, frame, free):
        self.frame = frame; self.free_q = queue.Queue()
        for i in range(free):
            self.free_q.put(i)

    def slot_view(self, idx):
        return self.frame


def main():
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)
    frame = synthetic_frame()
    ref = array_edges(frame, {"encoder_count": 1234})
    assert np.isfinite(ref["w10"]), ref

    got = []
    sc = W10LiveScorer(frame_in_sweep=7)
    sc.scored.connect(got.append)      # delivered queued to this (the scorer's) thread, as in the app

    # pressure: too few free slots -> skipped, nothing copied
    assert sc.offer(FakePool(frame, free=2), 0, dict(cam="camX", camera=9, sweep=1, frame=7)) is False
    assert sc.skipped_pressure == 1

    pool = FakePool(frame, free=8)
    meta = dict(cam="cam9[test]", camera=9, line=1, sweep=51, frame=7, ts="t", focus={"encoder_count": 1234})
    assert sc.offer(pool, 0, meta) is True
    # second offer while the first is in flight -> skipped
    assert sc.offer(pool, 0, meta) is False and sc.skipped_busy == 1

    t0 = time.time()
    while not got and time.time() - t0 < 10:
        app.processEvents(); time.sleep(0.01)
    assert got, "no score arrived"
    s = got[0]
    assert s["w10"] == ref["w10"] and s["n_edges"] == ref["n_edges"], (s["w10"], ref["w10"])
    assert s["sweep"] == 51 and s["frame"] == 7 and s["camera"] == 9 and s["encoder"] == 1234
    line = format_score(s)
    assert "S51 F7" in line and "w10" in line and "enc 1234" in line and "copy" in line and "score" in line, line
    assert 0 < s["copy_ms"] < 500 and 0 < s["score_ms"] < 5000, (s["copy_ms"], s["score_ms"])
    print(line)

    # fail-safe: a recording drop on the current line stands the scorer down until the line changes
    notes = []
    sc.notice.connect(notes.append)
    sc.note_drop("cam9[test]")                     # line defaults to the last offered line (1)
    sc.note_drop("cam9[test]", 1)                  # same line again: no second notice
    assert sc.disabled_line == 1 and sc.disabled_count == 1
    assert sc.offer(pool, 0, meta) is False and sc.skipped_disabled == 1
    assert sc.offer(pool, 0, dict(meta, line=2)) is True and sc.disabled_line is None   # new line re-arms
    t0 = time.time()
    while len(got) < 2 and time.time() - t0 < 10:
        app.processEvents(); time.sleep(0.01)
    assert len(got) == 2 and got[1]["line"] == 2
    assert len(notes) == 2 and "off for the rest" in notes[0] and "back on" in notes[1], notes

    # after the previous finished, a new offer is accepted again
    assert sc.offer(pool, 0, meta) is True
    sc.shutdown()                      # waits for the in-flight score; the signal is still queued
    app.processEvents()
    assert len(got) == 3 and sc.offer(pool, 0, meta) is False   # closed: nothing accepted after shutdown
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
