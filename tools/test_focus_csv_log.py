"""Self-checks for the manual-backlash focus telemetry (no hardware, no Qt).

Covers the two host-side pieces the feature adds:

* :class:`capture.focus_csv_log.FocusCsvLogger` -- writes the
  ``timestamp, frame, encoder, focus_score`` CSV (header + one row per sample)
  into a given directory; read it back and assert the header + values.
* :class:`capture.focus_panel._FocusScatterGraph` ring buffer -- trims to its
  cap so a long creep session can't grow the point list without bound.

Run: ``py -3 tools/test_focus_csv_log.py``
"""

from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOST = REPO / "host"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HOST))

from capture.focus_csv_log import CSV_HEADER, FocusCsvLogger  # noqa: E402


def test_logger_writes_header_and_rows() -> None:
    with tempfile.TemporaryDirectory() as d:
        logger = FocusCsvLogger(log_dir=d)
        assert logger.path is not None, "logger failed to open a file"
        assert Path(logger.path).parent == Path(d), "wrote outside the given dir"
        rows_in = [
            ("2026-07-27T11:00:00.100", 1, 4096, 12.5),
            ("2026-07-27T11:00:00.200", 2, 4102, 18.0),
            ("2026-07-27T11:00:00.300", 3, 4090, 9.25),
        ]
        for ts, frame, enc, score in rows_in:
            logger.write_row(ts, frame, enc, score)
        logger.close()

        with open(logger.path, newline="", encoding="utf-8") as fh:
            got = list(csv.reader(fh))

    assert got[0] == CSV_HEADER, f"bad header: {got[0]}"
    assert len(got) == 1 + len(rows_in), f"row count: {len(got)}"
    # Values survive the round trip (as strings out of the CSV).
    assert got[1] == ["2026-07-27T11:00:00.100", "1", "4096", "12.5"], got[1]
    assert got[3] == ["2026-07-27T11:00:00.300", "3", "4090", "9.25"], got[3]


def test_logger_never_fatal_on_bad_dir() -> None:
    # A directory path that can't be created (a file where a dir should be)
    # must leave the logger usable-but-inert, not raise.
    with tempfile.TemporaryDirectory() as d:
        blocker = Path(d) / "not_a_dir"
        blocker.write_text("x", encoding="utf-8")
        logger = FocusCsvLogger(log_dir=str(blocker / "sub"))
        assert logger.path is None, "expected open to fail gracefully"
        logger.write_row("t", 1, 0, 0.0)  # must not raise
        logger.close()


def test_scatter_ring_buffer_trims_to_cap() -> None:
    # Constructing a QWidget without a QApplication is a FATAL C++ abort (not a
    # catchable Python error), so set up a headless app first; skip the check if
    # PySide6 or the offscreen platform isn't available.
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication

        from capture.focus_panel import _FocusScatterGraph
    except Exception as exc:  # pragma: no cover - PySide6 optional in CI
        print(f"  skip: scatter ring buffer (PySide6 unavailable: {exc})")
        return
    app = QApplication.instance() or QApplication([])
    g = _FocusScatterGraph()
    cap = g._MAX
    for i in range(cap + 250):
        g.add_point(i, float(i % 7))
    assert len(g._enc) == cap, f"enc not trimmed: {len(g._enc)}"
    assert len(g._score) == cap, f"score not trimmed: {len(g._score)}"
    # Trimming keeps the NEWEST points (a ring buffer, oldest dropped).
    assert g._enc[-1] == float(cap + 249), g._enc[-1]
    g.clear()
    assert not g._enc and not g._score, "clear() left data"


def main() -> int:
    for t in (
        test_logger_writes_header_and_rows,
        test_logger_never_fatal_on_bad_dir,
        test_scatter_ring_buffer_trims_to_cap,
    ):
        t()
        print(f"  ok: {t.__name__}")
    print("focus-csv-log tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
